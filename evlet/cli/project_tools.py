"""The project tools: what the portal's buttons and MCP clients call on the project endpoint (docs/project-endpoint.md).

Each tool is an @api(tool=True) service of the endpoint Application, named by its function, with the permission
(Project, 'read') that only the owner holds, and a tier (TIERS[name], or function.__tier__):

- operate: running the project, in development and production.
- develop: changing it (files, apps, migrations, builds, tests, instances, Controls overrides, settings, site files),
  in development only. The tier rule is read from project.json on every call, never from the client: in production
  a develop tool is not listed and a call gets 403 "Not available in production". Only the server shell changes the
  mode (evlet project mode), so no tool can unlock its own tier.

There is no shell tool: the tools run Evlet's own commands and touch project files, never arbitrary processes. They
never read an instance's records (the instance's own MCP endpoint does that); in development that is a boundary of the
tools, not a security one, since app code they write runs in the instance.

Approvals go through the Tool Engine (Settings, AI). In production app_install, app_update, evlet_upgrade,
backup_restore and instance_delete always ask; backup_restore and instance_delete always ask in development too. Other
tools that change something ask unless endpoint.json lists them under never_ask.

A long tool starts an operation and returns {'operation': id}: one operation runs at a time, in a thread of the
endpoint, appended to the operation log as it starts and ends; operation_status(id) shows its live output. A short
change (a file, a host, an override) runs in the request and is logged as one operation with its result. An operation
that needs the instance's exclusive lock (install, migrate, restore, app update, Evlet upgrade) stops the instance's
child process first and starts it again afterwards. Running instances are reached only through the management
channel (evlet/manage.py, evlet/cli/channel.py).
"""

from contextlib import nullcontext
import os
from pathlib import Path
import threading

from ..models import ValidationError
from ..services import Endpoint, api
from .channel import ChannelError
from .oplog import CURRENT, Project, Running, begin, end, now, operations

TIERS = {}
# Tools that always ask for the owner's approval in production; instance_delete and backup_restore in both modes.
ALWAYS_ASK = frozenset(('app_install', 'app_update', 'evlet_upgrade', 'backup_restore', 'instance_delete'))
ASK_IN_DEVELOPMENT = frozenset(('backup_restore', 'instance_delete'))
PRODUCTION_REFUSAL = 'Not available in production'
DELETE_WARNING = ('Deleting {instance} stops it and moves its folder, with its database, files and secret.key, to '
                  '.evlet/deleted/ on the server. It is no longer served or listed.')
RESTORE_WARNING = ('Restoring {backup} replaces every database file of {instance}. Every change made in {instance} since '
                   'that backup was written is lost. A safety copy of the replaced files is kept.')


def tool(tier, method='POST'):
    """A project tool: an owner-only service offered to MCP clients, with its tier."""
    def decorate(function):
        function.__tier__ = tier
        TIERS[function.__name__] = tier
        return api(method=method, permission=(Project, 'read'), tool=True)(function)
    return decorate


class Runtime:
    """What the tools reach: the project folder, the supervisor and the one operation that may run."""

    def __init__(self, root, supervisor):
        self.root, self.supervisor = Path(root), supervisor
        self.app = None
        self.lock = threading.Lock()
        self.active = None
        self.thread = None
        self.restart_endpoint = False

    # ---------- operations ----------

    @staticmethod
    def origin(context):
        if getattr(context, 'origin', None):
            return context.origin  # the CLI through the endpoint's control route (endpoint.Endpoint.control)
        return 'mcp' if getattr(context.user, 'access', None) is not None else 'portal'

    def call(self, name, arguments, origin='cli'):
        """Run a tool for the server shell (evlet restart and evlet upgrade while the endpoint runs): as the owner,
        outside any request, logged with origin."""
        from types import SimpleNamespace
        endpoint = next(item for item in self.app.business.apis if item.name == name)
        return endpoint.function(SimpleNamespace(origin=origin, user=None, readonly=True, _tx=None), **arguments)

    def launch(self, context, kind, instance, arguments, work, *, pause=()):
        """Log the start of an operation and run work(out) in a thread; work returns an Outcome. Returns its id."""
        with self.lock:
            if self.active is not None:
                raise ValidationError({'operation': f'Operation {self.active.id} ({self.active.kind}) is running. '
                                                    'Wait for it to finish, then try again.'})
            running = Running(origin=self.origin(context))
            running.kind, running.started = kind, now()
            # A POST service holds a write transaction on project.db for its request: the start row goes through it.
            running.id = begin(self.root, kind, instance, arguments, running.origin, running.started,
                               tx=None if context.readonly else context._tx)
            self.active = running
        self.thread = threading.Thread(target=self.run, args=(running, instance, work, pause), name=f'evlet-{kind}', daemon=True)
        self.thread.start()
        return {'operation': running.id, 'result': 'started'}

    def record(self, context, kind, instance, arguments, work):
        """Run a short change in this request and log it as one operation: work(out) returns the result. A failure is
        logged and answered as {'result': 'failed', 'error': ...}, so its log rows are kept."""
        running = Running(origin=self.origin(context))
        running.kind, running.started = kind, now()
        running.id = begin(self.root, kind, instance, arguments, running.origin, running.started, tx=context._tx)
        token = CURRENT.set(running)
        try:
            value, ok, error = work(running), True, ''
        except (ValueError, OSError, ChannelError) as exc:
            value, ok, error = None, False, getattr(exc, 'message', None) or str(exc) or type(exc).__name__
            if isinstance(exc, ValidationError):
                error = '; '.join(f'{key}: {message}' for key, message in exc.errors.items())
            running(f'Failed: {error}')
        finally:
            CURRENT.reset(token)
        end(self.root, running.id, kind=kind, instance=instance, origin=running.origin, started=running.started, ok=ok,
            output=running.lines, error=error, tx=context._tx)
        return {'operation': running.id, 'result': 'ok', **(value or {})} if ok else \
            {'operation': running.id, 'result': 'failed', 'error': error}

    def manage(self, instance, method, route, body=None, query=None):
        """A call on the running instance's management channel."""
        from . import channel
        child = self.child(instance)
        if child.state != 'running':
            raise ChannelError(409, f'{instance} is {child.state}; start it with instance_start')
        return channel.call(self.root, instance, child.port, method, route, body, query=query)

    def running(self, instance):
        child = self.supervisor.children.get(instance)
        return child is not None and child.state == 'running'

    def run(self, running, instance, work, pause):
        token = CURRENT.set(running)
        ok, before, error = False, None, ''
        try:
            with self.supervisor.paused(pause) if pause else nullcontext():
                outcome = work(running)
            ok, before = outcome.ok, outcome.before
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            running(f'Failed: {error}')
        finally:
            CURRENT.reset(token)
            try:
                end(self.root, running.id, kind=running.kind, instance=instance, origin=running.origin, started=running.started,
                    ok=ok, output=running.lines, before=before, error=error)
            finally:
                with self.lock:
                    self.active = None
        if self.restart_endpoint:
            self.reload()

    def reload(self):
        """After evlet_upgrade: stop the children and replace this process with the same command on the new Evlet."""
        import sys
        self.supervisor.stop_all()
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])

    def wait(self, timeout=None):
        """Wait for the operation running in a thread (tests and shutdown)."""
        if self.thread is not None:
            self.thread.join(timeout)

    # ---------- instances ----------

    def instance(self, name):
        from .endpoint import instance_names
        if name not in instance_names(self.root):
            raise ValidationError({'instance': f'There is no instance named {name}'})
        return name

    def child(self, name):
        """The supervised child of an instance, added when the instance was created after the endpoint started."""
        from .endpoint import instance_ports
        self.instance(name)
        if name not in self.supervisor.children:
            self.supervisor.add(name, instance_ports(self.root)[name])
        return self.supervisor.children[name]

    def healthy(self, name, timeout=60.0):
        from ..instances import read_instance
        from .restart import wait_for
        domain = read_instance(self.root / 'instances' / name).domain
        return wait_for(self.child(name).port, domain, timeout)

    def backup_file(self, instance, name):
        from . import core
        for path in core.backup_list(self.root, instance).value:
            if path.name == name:
                return path
        raise ValidationError({'backup': f'{instance} has no backup named {name}. backup_list names them'})


def instance_status(runtime, name):
    """One instance in project_status: its id, name, host, Evlet version, apps, process state and health."""
    import sqlite3
    from ..instances import instance_identity, read_instance
    from .core import lock_state
    from .restart import health
    path = runtime.root / 'instances' / name
    result = {'name': name, 'id': None, 'host': None, 'version': None, 'apps': {}, 'status': 'stopped', 'health': False,
              'port': None, 'restarts': 0, 'error': None}
    try:
        identity = instance_identity(path)
        result['id'] = identity['id'] if identity else None
        domain = read_instance(path).domain
    except (OSError, ValueError, SyntaxError, sqlite3.Error) as exc:
        result['error'], domain = str(exc), None
    lock = lock_state(runtime.root, name)
    if lock:
        result['apps'] = {app: entry['version'] for app, entry in lock['apps'].items()}
        result['version'] = (lock.get('evlet') or {}).get('version')
    child = runtime.supervisor.children.get(name)
    if child is not None:
        result.update(status=child.state, port=child.port, restarts=child.restarts)
        result['host'] = domain or f'http://127.0.0.1:{child.port}'
        result['health'] = child.state == 'running' and health(child.port, domain)
    return result


def endpoints(runtime):
    """The project tools of runtime as Endpoint objects for Application(apis=...)."""
    from . import core

    @tool('operate', 'GET')
    def project_status(context):
        """The project at a glance: its id, mode and endpoint, the MCP address, and each instance with its id, host,
        Evlet version, installed apps, process status and health. Also the operation running now, if any."""
        from ..instances import read_project
        from .endpoint import instance_names, two_step_ready, url
        record = read_project(runtime.root) or {}
        active = runtime.active
        return {'id': record.get('id'), 'created': record.get('created'), 'mode': record.get('mode'), 'endpoint': url(runtime.root),
                'mcp': url(runtime.root) + '/_/mcp', 'two_step': two_step_ready(runtime.root),
                'instances': [instance_status(runtime, name) for name in instance_names(runtime.root)],
                'running': {'operation': active.id, 'kind': active.kind} if active else None,
                'tools': {name: tier for name, tier in TIERS.items() if tier == 'operate' or record.get('mode') != 'production'}}

    @tool('operate', 'GET')
    def operation_log(context, limit: int = 50, before: int = 0):
        """The newest operations first: kind, instance, origin (portal, mcp or cli), result, start and end times and
        arguments. Pass before (an operation id) for older ones. Read one operation's output with operation_status."""
        if not 1 <= limit <= 200:
            raise ValidationError({'limit': 'Use 1 to 200'})
        rows = operations(runtime.root, limit=limit, before=before)
        for row in rows:
            row['lines'] = len(row.pop('output'))
            row.pop('before')
            if runtime.active is not None and row['id'] == runtime.active.id:
                row['result'], row['lines'] = 'running', len(runtime.active.lines)
        return {'operations': rows}

    @tool('operate', 'GET')
    def operation_status(context, operation: int):
        """One operation with its output lines: live while it runs (result 'running'), then as logged. result is
        'ok', 'failed', or 'started' for one run by another process (the CLI) that has not ended or was interrupted."""
        from .oplog import operation as read
        found = read(runtime.root, operation)
        if found is None:
            raise ValidationError({'operation': f'No operation {operation}'})
        active = runtime.active
        if active is not None and active.id == operation:
            found.update(result='running', output=list(active.lines))
        return found

    @tool('operate', 'GET')
    def instance_logs(context, instance: str, lines: int = 200):
        """The last lines an instance's process printed, kept by the endpoint since it started (2,000 at most). They
        can hold values from records and settings that tracebacks print."""
        if not 1 <= lines <= 2000:
            raise ValidationError({'lines': 'Use 1 to 2000'})
        return {'instance': instance, 'lines': runtime.supervisor.logs(runtime.child(instance).name, lines)}

    def supervised(kind, action):
        def work(instance):
            def run(out):
                getattr(runtime.supervisor, action)(instance)
                child = runtime.supervisor.children[instance]
                out(f'{instance}: {child.state} on port {child.port}')
                ok = action == 'stop' or runtime.healthy(instance)
                if not ok:
                    out(f'{instance} did not answer /health within 60 seconds; read instance_logs.')
                return core.Outcome(ok, (action,), tuple(out.lines), before=core.lock_state(runtime.root, instance),
                                    after=core.lock_state(runtime.root, instance))
            return run
        return lambda context, instance: runtime.launch(context, kind, runtime.child(instance).name, {'instance': instance},
                                                        work(instance))

    @tool('operate')
    def instance_restart(context, instance: str):
        """Stop the instance's process and start it again, then wait until it answers /health. Needs no sudo.
        Returns an operation to follow."""
        return supervised('instance_restart', 'restart')(context, instance)

    @tool('operate')
    def instance_stop(context, instance: str):
        """Stop the instance's process. It stays stopped, also after a crash, until instance_start. Returns an operation."""
        return supervised('instance_stop', 'stop')(context, instance)

    @tool('operate')
    def instance_start(context, instance: str):
        """Start the instance's process and wait until it answers /health. Returns an operation to follow."""
        return supervised('instance_start', 'start')(context, instance)

    @tool('operate')
    def backup_create(context, instance: str, with_files: bool = False):
        """Back up the instance's database files as one set, while it keeps serving. with_files adds the uploaded files."""
        runtime.instance(instance)
        return runtime.launch(context, 'backup_create', instance, {'instance': instance, 'with_files': with_files},
                              lambda out: core.backup_create(runtime.root, instance, with_files=with_files, out=out))

    @tool('operate', 'GET')
    def backup_list(context, instance: str):
        """The instance's backup sets, newest first, by name: what backup_restore takes."""
        runtime.instance(instance)
        found = core.backup_list(runtime.root, instance).value
        return {'instance': instance, 'backups': [{'name': path.name, 'bytes': path.stat().st_size} for path in found]}

    @tool('operate')
    def backup_restore(context, instance: str, backup: str, confirm: bool = False):
        """Destructive: replace every database file of the instance with a backup set from backup_list. Every change
        made in the instance since that backup is lost. It always asks: without confirm=true it only answers with what
        will be lost. The instance is stopped for the restore and started again."""
        runtime.instance(instance)
        source = runtime.backup_file(instance, backup)
        if not confirm:
            raise ValidationError({'confirm': RESTORE_WARNING.format(backup=backup, instance=instance) +
                                              ' Call backup_restore again with confirm true to restore.'})
        return runtime.launch(context, 'backup_restore', instance, {'instance': instance, 'backup': backup},
                              lambda out: core.backup_restore(runtime.root, instance, str(source), yes=True, out=out),
                              pause=(instance,))

    @tool('operate', 'GET')
    def app_update_plan(context, instance: str, app: str, ref: str):
        """What updating an installed app to ref (a tag, branch or commit of its Git source) would do: the new commits
        and the migration plan. Writes nothing but the app's fetched Git objects. Returns an operation to follow."""
        runtime.instance(instance)
        return runtime.launch(context, 'app_update_plan', instance, {'instance': instance, 'app': app, 'ref': ref},
                              lambda out: core.app_update_plan(runtime.root, instance, app, ref, trust=True, out=out))

    @tool('operate')
    def app_update(context, instance: str, app: str, ref: str):
        """Update an installed app to ref from its recorded Git source: migration plan, backup, stop, migrate, start and
        a health check. If a step fails, the previous version and the backup come back. Returns an operation to follow."""
        runtime.instance(instance)
        child = runtime.child(instance)
        was_running = child.wanted

        def work(out):
            return core.app_update(runtime.root, instance, app, ref, trust=True, out=out,
                                   stop=lambda: runtime.supervisor.stop(instance),
                                   start=(lambda: runtime.supervisor.start(instance)) if was_running else None,
                                   healthy=(lambda: runtime.healthy(instance)) if was_running else None)
        return runtime.launch(context, 'app_update', instance, {'instance': instance, 'app': app, 'ref': ref}, work)

    @tool('operate')
    def app_install(context, instance: str, source: str):
        """Install an app in the instance: source is a Git URL (fetched like evlet app --get) or, in development, the
        name of an app in apps/. In production only URLs that start with one of the sources in project.json are taken.
        The instance is stopped for the install and started again. Returns an operation to follow."""
        runtime.instance(instance)
        allowed(runtime.root, source)

        def work(out):
            name = source
            if '/' in source or ':' in source:
                from .site import fetched_from
                name = fetched_from(runtime.root, source) or core.app_fetch(runtime.root, source, trust=True, out=out).value.name
            return core.install_app(runtime.root, instance, name, trust=True, hosts='skip', out=out)
        return runtime.launch(context, 'app_install', instance, {'instance': instance, 'source': source}, work, pause=(instance,))

    def upgrade(plan, to, out):
        from .endpoint import instance_names
        names = [name for name in instance_names(runtime.root) if (runtime.root / 'instances' / name / 'data/database.sqlite3').is_file()]
        if not names:
            raise ValidationError({'instance': 'No instance has a database yet; there is nothing to upgrade'})
        return core.upgrade_evlet(runtime.root, names[0], to=to or None, yes=not plan, confirm=(lambda question: False) if plan else None,
                                  all_instances=True, supervised=True, out=out)

    @tool('operate', 'GET')
    def evlet_upgrade_plan(context, to: str = ''):
        """Stage a newer Evlet (to: a version; default the newest of this minor) and show each instance's migration
        plan for it. Nothing is changed. Returns an operation to follow."""
        return runtime.launch(context, 'evlet_upgrade_plan', '', {'to': to}, lambda out: upgrade(True, to, out))

    @tool('operate')
    def evlet_upgrade(context, to: str = ''):
        """Upgrade the project's Evlet: stage it, plan, stop every instance, back them up, migrate and start them again.
        The endpoint restarts itself on the new version afterwards. Returns an operation to follow."""
        def work(out):
            outcome = upgrade(False, to, out)
            if outcome.ok:
                out('The endpoint restarts on the new Evlet now.')
                runtime.restart_endpoint = True
            return outcome
        return runtime.launch(context, 'evlet_upgrade', '', {'to': to}, work, pause=tuple(runtime.supervisor.children))

    # ---------- operate: hosts from data (Part 4), through the running instance or the hosts API ----------

    @tool('operate')
    def host_add(context, instance: str, host: str, target: str):
        """Add a host from data to the instance, served by target (APP.FRONTEND, or a site's name) once its DNS points
        here; it is checked at once. Needs no restart. Through the running instance, else straight to its database."""
        runtime.instance(instance)
        arguments = {'instance': instance, 'host': host, 'target': target}
        if runtime.running(instance):
            return runtime.record(context, 'host_add', instance, arguments,
                                  lambda out: runtime.manage(instance, 'POST', 'hosts/add', {'host': host, 'target': target}))
        return runtime.record(context, 'host_add', instance, arguments,
                              lambda out: {'output': list(core.host_add(runtime.root, instance, host, target, live=True, out=out).messages)})

    @tool('operate')
    def host_remove(context, instance: str, host: str):
        """Remove a host from data from the instance; it stops being served within a second."""
        runtime.instance(instance)
        arguments = {'instance': instance, 'host': host}
        if runtime.running(instance):
            return runtime.record(context, 'host_remove', instance, arguments,
                                  lambda out: runtime.manage(instance, 'POST', 'hosts/remove', {'host': host}))

        def work(out):
            from . import hosts
            hosts.remove_live(runtime.root, instance, host, out=out)
            return {'removed': host}
        return runtime.record(context, 'host_remove', instance, arguments, work)

    @tool('operate')
    def host_check(context, instance: str, host: str = ''):
        """Check the DNS of the instance's hosts from data (host, or all but disabled ones); a pending host whose A or
        AAAA record points here becomes active and is served. Lists each host with its status."""
        runtime.instance(instance)
        arguments = {'instance': instance, 'host': host}
        if runtime.running(instance):
            return runtime.record(context, 'host_check', instance, arguments,
                                  lambda out: runtime.manage(instance, 'POST', 'hosts/check', {'hosts': [host] if host else None}))

        def work(out):
            from . import hosts
            hosts.check(runtime.root, instance, [host] if host else (), out=out)
            return {'output': list(out.lines)}
        return runtime.record(context, 'host_check', instance, arguments, work)

    # ---------- develop: project files ----------

    @tool('develop', 'GET')
    def files_list(context, path: str = 'apps'):
        """The files and folders under path: apps/ or a folder inside it, or 'instances' for the config.py and routes.py
        of each instance. Nothing else of the project is reachable (.venv, .evlet, data/, keys, dot folders, links)."""
        from . import project_files
        try:
            return project_files.listing(runtime.root, path)
        except ValueError as exc:
            raise ValidationError({'path': str(exc)}) from None

    @tool('develop', 'GET')
    def file_read(context, path: str):
        """Read a UTF-8 text file in apps/, or an instance's config.py or routes.py (up to 1 MiB)."""
        from . import project_files
        try:
            return project_files.read(runtime.root, path)
        except ValueError as exc:
            raise ValidationError({'path': str(exc)}) from None

    def checked(path):
        from . import project_files
        try:
            project_files.inside(runtime.root, path)
        except ValueError as exc:
            raise ValidationError({'path': str(exc)}) from None

    @tool('develop')
    def file_write(context, path: str, text: str):
        """Write a UTF-8 text file (up to 64 KiB per call) in apps/, making its folders, or replace an instance's
        config.py or routes.py. An app's code changes reload the instance in development."""
        from . import project_files
        checked(path)
        return runtime.record(context, 'file_write', '', {'path': path, 'bytes': len(text.encode())},
                              lambda out: project_files.write(runtime.root, path, text))

    @tool('develop')
    def file_delete(context, path: str):
        """Delete a file in apps/, or an instance's routes.py."""
        from . import project_files
        checked(path)
        return runtime.record(context, 'file_delete', '', {'path': path}, lambda out: project_files.delete(runtime.root, path))

    # ---------- develop: apps, migrations, builds, tests ----------

    @tool('develop')
    def app_create(context, name: str):
        """Scaffold a new app in apps/NAME (evlet app NAME). Returns an operation to follow."""
        return runtime.launch(context, 'app_create', '', {'name': name}, lambda out: core.app_create(runtime.root, name, out=out))

    @tool('develop')
    def app_fetch(context, url: str):
        """Fetch an app from a Git URL into apps/ (evlet app --get URL), then prepare and build it. Returns an operation."""
        from ..packages import validate_url
        try:
            validate_url(url)
        except ValueError as exc:
            raise ValidationError({'url': str(exc)}) from None
        return runtime.launch(context, 'app_fetch', '', {'url': url}, lambda out: core.app_fetch(runtime.root, url, trust=True, out=out))

    @tool('develop')
    def makemigration(context, app: str, instance: str, name: str = 'changes'):
        """Write the app's next migration from its models, compared with what the instance has (evlet makemigration).
        Returns an operation to follow; its output names the file."""
        runtime.instance(instance)
        return runtime.launch(context, 'makemigration', instance, {'app': app, 'instance': instance, 'name': name},
                              lambda out: core.make_migration(runtime.root, app, instance, name=name, trust=True, out=out))

    @tool('develop')
    def migrate(context, instance: str):
        """Apply the pending migrations of the instance's apps and Evlet (evlet migrate), after a backup. The instance
        stops for it and starts again. A destructive step is refused here. Returns an operation to follow."""
        runtime.instance(instance)
        return runtime.launch(context, 'migrate', instance, {'instance': instance},
                              lambda out: core.migrate(runtime.root, instance, trust=True, out=out), pause=(instance,))

    @tool('develop')
    def build(context):
        """Build a versioned generation of the project's apps (evlet build), without activating it. Returns an operation."""
        return runtime.launch(context, 'build', '', {}, lambda out: core.build(runtime.root, trust=True, out=out))

    @tool('develop')
    def tests_run(context, target: str = '', seconds: int = core.TESTS_SECONDS):
        """Run pytest in the project's .venv on apps/, or on target (an app, or a test path inside apps/), for at most
        seconds (up to 900). Returns an operation; its output is pytest's last 400 lines and its result whether it passed."""
        from . import project_files
        if not 1 <= seconds <= 900:
            raise ValidationError({'seconds': 'Use 1 to 900'})
        try:
            project_files.inside(runtime.root, 'apps/' + target if target else 'apps', roots=('apps',))
        except ValueError as exc:
            raise ValidationError({'target': str(exc)}) from None
        return runtime.launch(context, 'tests_run', '', {'target': target, 'seconds': seconds},
                              lambda out: core.tests_run(runtime.root, target, seconds=seconds, out=out))

    # ---------- develop: instances ----------

    @tool('develop')
    def instance_create(context, name: str):
        """Make a new empty instance (evlet instance NAME) with its own port. Install an app in it with app_install, then
        start it with instance_start. Returns an operation to follow."""
        def work(out):
            from .endpoint import instance_ports
            outcome = core.instance_create(runtime.root, name, out=out)
            port = instance_ports(runtime.root)[name]
            runtime.supervisor.add(name, port)
            out(f'{name} will serve on 127.0.0.1:{port}')
            return outcome
        return runtime.launch(context, 'instance_create', name, {'name': name}, work)

    @tool('develop')
    def instance_delete(context, instance: str):
        """Destructive: stop the instance and delete it. Its folder, with its database, files and secret.key, moves to
        .evlet/deleted/ on the server. It always asks for the owner's approval. Returns an operation to follow."""
        runtime.instance(instance)

        def work(out):
            from .endpoint import read_settings, save_settings
            if instance in runtime.supervisor.children:
                runtime.supervisor.stop(instance)
            outcome = core.instance_delete(runtime.root, instance, yes=True, out=out)
            runtime.supervisor.children.pop(instance, None)
            settings = read_settings(runtime.root)
            settings['instances'].pop(instance, None)
            save_settings(runtime.root, settings)
            return outcome
        return runtime.launch(context, 'instance_delete', instance, {'instance': instance}, work)

    # ---------- develop: Controls overrides, app settings and site files, through the running instance ----------

    def json_argument(name, value, kinds):
        import json
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ValidationError({name: 'Send JSON'}) from None
        if not isinstance(parsed, kinds):
            raise ValidationError({name: 'Send ' + ' or '.join({dict: 'a JSON object', list: 'a JSON list'}.get(kind, 'JSON') for kind in kinds)})
        return parsed

    @tool('develop')
    def controls_override_set(context, instance: str, kind: str, key: str, value: str):
        """Set one Controls override of the running instance, as an admin would in Controls: kind ('setting', 'perm',
        ...), key and value as JSON ('null' resets it to the file value). Audited on the instance as project."""
        runtime.instance(instance)
        parsed = json_argument('value', value, (dict, list, str, int, float, bool, type(None)))
        return runtime.record(context, 'controls_override_set', instance, {'instance': instance, 'kind': kind, 'key': key},
                              lambda out: runtime.manage(instance, 'POST', 'overrides', {'changes': [{'kind': kind, 'key': key, 'value': parsed}]}) and {})

    @tool('develop')
    def settings_set(context, instance: str, app: str, values: str):
        """Change an app's settings in the running instance: values is a JSON object of setting names and values
        (null resets one to its default). Audited on the instance as project."""
        runtime.instance(instance)
        parsed = json_argument('values', values, (dict,))
        return runtime.record(context, 'settings_set', instance, {'instance': instance, 'app': app, 'keys': sorted(parsed)},
                              lambda out: {'saved': runtime.manage(instance, 'POST', 'settings', {'app': app, 'values': parsed}).get('saved')})

    @tool('develop')
    def site_files_write(context, instance: str, site: str, files: str = '{}', delete: str = '[]'):
        """Write files to the draft of one of the running instance's sites: files is a JSON object of path and UTF-8
        text; delete a JSON list of paths to remove. The site serves them once published (site_publish)."""
        import base64
        runtime.instance(instance)
        written, removed = json_argument('files', files, (dict,)), json_argument('delete', delete, (list,))
        if not all(isinstance(value, str) for value in written.values()):
            raise ValidationError({'files': 'Each file is text'})
        body = {'site': site, 'files': {path: base64.b64encode(text.encode()).decode() for path, text in written.items()}, 'delete': removed}
        return runtime.record(context, 'site_files_write', instance, {'instance': instance, 'site': site, 'files': sorted(written), 'delete': removed},
                              lambda out: runtime.manage(instance, 'POST', 'sites/files', body))

    @tool('develop')
    def site_publish(context, instance: str, site: str, note: str = ''):
        """Publish the draft of a site of the running instance as a new release, served within a second."""
        runtime.instance(instance)
        return runtime.record(context, 'site_publish', instance, {'instance': instance, 'site': site, 'note': note},
                              lambda out: runtime.manage(instance, 'POST', 'sites/publish', {'site': site, 'note': note}))

    @tool('develop')
    def site_rollback(context, instance: str, site: str, release: str = ''):
        """Serve an earlier release of a site again: release, or the one before the current one."""
        runtime.instance(instance)
        return runtime.record(context, 'site_rollback', instance, {'instance': instance, 'site': site, 'release': release},
                              lambda out: runtime.manage(instance, 'POST', 'sites/rollback', {'site': site, 'release': release}))

    functions = (project_status, operation_log, operation_status, instance_logs, instance_restart, instance_stop,
                 instance_start, backup_create, backup_list, backup_restore, app_update_plan, app_update, app_install,
                 evlet_upgrade_plan, evlet_upgrade, host_add, host_remove, host_check,
                 files_list, file_read, file_write, file_delete, app_create, app_fetch, makemigration, migrate, build,
                 tests_run, instance_create, instance_delete, controls_override_set, settings_set, site_files_write,
                 site_publish, site_rollback)
    return tuple(Endpoint(function.__name__, function, function.__api__['method'], function.__api__['permission'], tool=True,
                          available=(lambda: develop_refusal(runtime.root)) if function.__tier__ == 'develop' else None,
                          ask=approval(runtime.root, function.__name__))
                 for function in functions)


def production(root):
    """Whether project.json says production, read on every call."""
    from ..instances import read_project
    return (read_project(root) or {}).get('mode') == 'production'


def develop_refusal(root):
    """Why a develop tool may not run now: in production it may not."""
    return PRODUCTION_REFUSAL if production(root) else None


def approval(root, name):
    """The Tool Engine's ask hook of a tool (evlet.services.Endpoint.ask): always asks for ALWAYS_ASK in production and
    ASK_IN_DEVELOPMENT in both modes, with what is lost; never asks for the tools endpoint.json lists under never_ask;
    else the usual approval for a change."""
    def ask(arguments):
        instance = arguments.get('instance', '') if isinstance(arguments, dict) else ''
        if name in ASK_IN_DEVELOPMENT or name in ALWAYS_ASK and production(root):
            warning = {'backup_restore': RESTORE_WARNING.format(backup=arguments.get('backup', ''), instance=instance),
                       'instance_delete': DELETE_WARNING.format(instance=instance)}.get(name, 'Production: this tool always asks.')
            return {'warning': warning}
        from .endpoint import read_settings
        return False if name in read_settings(root)['never_ask'] else None
    return ask



def allowed(root, source):
    """Refuse an app source the project's mode does not allow: in production a Git URL that starts with one of
    project.json's sources, in development also an app already in apps/."""
    from ..instances import read_project
    from ..packages import validate_url
    from .project import valid_name
    record = read_project(root) or {}
    is_url = '/' in source or ':' in source
    if is_url:
        try:
            validate_url(source)
        except ValueError as exc:
            raise ValidationError({'source': str(exc)}) from None
    if record.get('mode') != 'production':
        if not is_url:
            try:
                valid_name(source)
            except ValueError as exc:
                raise ValidationError({'source': str(exc)}) from None
            if not (Path(root) / 'apps' / source).is_dir():
                raise ValidationError({'source': f'There is no app named {source} in apps/'})
        return
    sources = record.get('sources') or []
    if not isinstance(sources, list) or not all(isinstance(item, str) and item for item in sources):
        raise ValidationError({'source': 'project.json sources must be a list of URL prefixes'})
    if not is_url or not any(source.startswith(prefix) for prefix in sources):
        raise ValidationError({'source': 'In production apps are installed only from the sources in project.json: '
                                         + (', '.join(sources) or 'none are listed') + '. Edit project.json on the server to add one.'})
