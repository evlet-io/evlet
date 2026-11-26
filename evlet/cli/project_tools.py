"""The project tools: what the portal's buttons and MCP clients call on the project endpoint (docs/project-endpoint.md).

Each tool is an @api(tool=True) service of the endpoint Application, named by its function, with the permission
(Project, 'read') that only the owner holds, and a tier: 'operate' for every tool here. Phase 5 of the endpoint plan
adds the 'develop' tier and decides by the project's mode which tiers MCP clients see; a tool's tier is
TIERS[name], or function.__tier__.

A tool that changes something starts an operation and returns {'operation': id}: one operation runs at a time, in
a thread of the endpoint, appended to the operation log as it starts and ends. operation_status(id) shows its live
output while it runs. An operation that needs the instance's exclusive lock (install, migrate, restore, app update,
Evlet upgrade) stops the instance's child process first and starts it again afterwards.
"""

from contextlib import nullcontext
import os
from pathlib import Path
import threading

from ..models import ValidationError
from ..services import Endpoint, api
from .oplog import CURRENT, Project, Running, begin, end, now, operations

TIERS = {}
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
        self.caddy = None  # the CaddySync of an endpoint that pushes routes to Caddy (evlet/cli/caddy.py)

    # ---------- operations ----------

    @staticmethod
    def origin(context):
        return 'mcp' if getattr(context.user, 'access', None) is not None else 'portal'

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
        Evlet version, installed apps, process status and health. Also the operation running now, if any, and caddy:
        whether the endpoint's last push of routes to Caddy's admin API worked (null without one)."""
        from ..instances import read_project
        from .endpoint import instance_names, two_step_ready, url
        record = read_project(runtime.root) or {}
        active = runtime.active
        return {'id': record.get('id'), 'created': record.get('created'), 'mode': record.get('mode'), 'endpoint': url(runtime.root),
                'mcp': url(runtime.root) + '/_/mcp', 'two_step': two_step_ready(runtime.root),
                'instances': [instance_status(runtime, name) for name in instance_names(runtime.root)],
                'running': {'operation': active.id, 'kind': active.kind} if active else None,
                'caddy': runtime.caddy.status() if runtime.caddy is not None else None}

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

    functions = (project_status, operation_log, operation_status, instance_logs, instance_restart, instance_stop,
                 instance_start, backup_create, backup_list, backup_restore, app_update_plan, app_update, app_install,
                 evlet_upgrade_plan, evlet_upgrade)
    return tuple(Endpoint(function.__name__, function, function.__api__['method'], function.__api__['permission'], tool=True)
                 for function in functions)


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
