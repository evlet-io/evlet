"""The lifecycle core: what the evlet lifecycle commands do, without prompts or printing (docs/cli.md#lifecycle-core).

Each function returns an Outcome and never reads stdin or writes stdout. A question goes to confirm(question),
which returns True to go on; a command given neither yes=True nor confirm refuses, as the CLI does without a
terminal. Each output line goes to out(line) as it happens, when out is given, and into Outcome.messages. An error
that stops a command is raised, as the CLI raises it (ValueError, OSError, sqlite3.Error and the others
evlet.cli.main reports); a command that ran its checks but did not go ahead, such as a failed restart check or a
declined upgrade, returns Outcome(ok=False). The evlet commands are these functions with out=print and their
prompts at the CLI edge.
"""

from dataclasses import dataclass
from pathlib import Path
import time

from .project import child


@dataclass(frozen=True)
class Outcome:
    """What a lifecycle function did.

    steps are the stages it completed in order: shell commands it ran (without the '$ '), or names such as
    'backup', 'migrations', 'framework' and 'host <name>'. messages are the lines the CLI prints. backup is the main
    file of the backup it wrote. before and after are the instance's apps.lock state ({'apps': ..., 'evlet': ...})
    around the change, or None when there is no instance or lock. value is the function's own result."""
    ok: bool
    steps: tuple = ()
    messages: tuple = ()
    backup: str | None = None
    before: dict | None = None
    after: dict | None = None
    value: object = None


class _Log:
    """Collects the lines and steps of one call and passes each line on to out."""

    def __init__(self, out):
        self.out, self.messages, self.steps = out, [], []

    def __call__(self, line):
        line = str(line)
        self.messages.append(line)
        if line.startswith('$ '):
            self.steps.append(line[2:])
        if self.out is not None:
            self.out(line)

    def step(self, name):
        self.steps.append(name)

    def outcome(self, ok=True, **values):
        return Outcome(ok, tuple(self.steps), tuple(self.messages), **values)


def lock_state(root, instance):
    """The apps.lock of an instance, or None when the instance or its lock cannot be read."""
    from ..instances import read_lock
    try:
        return read_lock(child(Path(root), 'instances', instance))
    except (ValueError, OSError):
        return None


def _agreed(yes, confirm, question):
    return yes or (confirm is not None and confirm(question))


# Install and migrate


def install_app(root, instance, app, *, trust=False, confirm=None, hosts='offer', ask=None, out=None):
    """evlet install APP --instance INSTANCE: install a trusted app and its app dependencies, then offer hosts.

    confirm(question) is asked to trust changed app source unless trust. hosts is 'offer' (ask(question) -> answer
    for each new frontend with a suggested host; without ask only the evlet host add command is shown), 'take'
    (--yes) or 'skip' (--no-host). value is the installer's result with 'hosts', the hosts added to routes.py."""
    if hosts not in ('offer', 'take', 'skip'):
        raise ValueError("hosts is 'offer', 'take' or 'skip'")
    return _install(root, instance, app, trust=trust, confirm=confirm, hosts=hosts, ask=ask, out=out)


def migrate(root, instance, *, trust=False, confirm=None, recompute_posted=False, allow_destructive=False,
            allow_maintenance=False, out=None):
    """evlet migrate INSTANCE: back up, migrate the installed apps and the framework, or recover an interrupted install."""
    return _install(root, instance, None, trust=trust, confirm=confirm, recompute_posted=recompute_posted,
                    allow_destructive=allow_destructive, allow_maintenance=allow_maintenance, out=out)


def _install(root, instance, app, *, trust, confirm, recompute_posted=False, allow_destructive=False,
             allow_maintenance=False, hosts='skip', ask=None, out):
    from .install import install, migration_label
    log, root = _Log(out), Path(root)
    before = lock_state(root, instance)
    known = set(before['apps']) if before else set()
    migrating = app is None
    result = install(root, instance, app_name=app, trust=trust, confirm=confirm, recompute_posted=recompute_posted,
                     allow_destructive=allow_destructive, allow_maintenance=allow_maintenance)
    if result['backup']:
        log.step('backup')
    if result['migrations']:
        log.step('migrations')
    if result.get('framework'):
        log.step('framework')
    log(f'Instance {instance}: ' + ('apps ' if migrating else 'installed ') + ', '.join(result['apps']))
    added = [name for name in result['apps'] if name not in known]
    if added:
        # The same rule as a first install: a new app's tables come from its models; later changes need a migration.
        log('Tables created from the models of ' + ', '.join(added) +
            f'. After this, every model change needs a migration: evlet makemigration APP --instance {instance}')
    log('Applied migrations: ' + (', '.join(map(migration_label, result['migrations'])) or 'none; schema is current'))
    if result.get('framework') and result['backup']:
        log('Framework migrations: ' + ', '.join(result['framework']))
    elif result.get('framework'):
        log(f'Framework tables: created ({len(result["framework"])} framework migrations recorded)')
    if recompute_posted:
        log(f'Posted records recalculated: {result.get("posted", 0)}')
    if result['backup']:
        log('Backup: ' + result['backup'])
    served = []
    if not migrating:
        from .hosts import suggest
        served = suggest(root, instance, set(result['apps']) - known, yes=hosts == 'take', skip=hosts == 'skip', ask=ask, out=log)
        for host in served:
            log.step('host ' + host)
    return log.outcome(backup=result['backup'], before=before, after=lock_state(root, instance),
                       value={**result, 'hosts': served})


def migration_plan(root, instance, *, trust=False, confirm=None, out=None):
    """evlet migrate INSTANCE --plan: the pending migrations and suggested ones, worked out on a copy; writes nothing.

    value is (MigrationPlan, {app: (migration name, Python or '')}, packages, pending framework steps)."""
    from .install import plan
    from ..framework import version
    log = _Log(out)
    result, suggestions, packages, steps = plan(Path(root), instance, trust=trust, confirm=confirm)
    log(f'Instance {instance}: plan only; nothing was written')
    log(f'Planned with Evlet {version()}')
    log(result.text(suggestion=False))
    if steps:
        log('Pending framework migrations (run after a backup):')
        log('\n'.join(f'  {step.name}\n    {step.summary}' for step in steps))
    for package in packages:
        migration, code = suggestions[package.name]
        if not code:
            continue
        where = f'apps/{package.path.name}/migrations/m{migration}.py' if package.layout == 'flat' \
            else f'the migrations tuple in apps/{package.path.name}/{package.name}/migrations/__init__.py'
        log(f'Suggested migration for {package.name} (save as {where}; review it and fill in any ...):')
        log('\n'.join(('    ' + line).rstrip() for line in code.splitlines()))
        if package.layout == 'flat':
            log(f'Or write it with: evlet makemigration {package.name} --instance {instance}')
    state = lock_state(root, instance)
    return log.outcome(before=state, after=state, value=(result, suggestions, packages, steps))


def make_migration(root, app, instance, *, name='changes', trust=False, confirm=None, out=None):
    """evlet makemigration APP --instance INSTANCE: write the suggested migration. value is (file, needs editing)."""
    from .install import makemigration
    log = _Log(out)
    target, review = makemigration(Path(root), app, instance, name=name, trust=trust, confirm=confirm)
    log.step('write ' + str(target))
    log(f'Wrote {target}')
    log('Edit the lines marked with ... or # before running evlet migrate.' if review else
        f'Review it, then run evlet migrate {instance}.')
    return log.outcome(value=(target, review))


# Backups


def backup_create(root, instance, *, to=None, with_files=False, out=None):
    """evlet backup --instance INSTANCE: the four database files as one set. value is the written paths."""
    from .backup import backup
    log, root = _Log(out), Path(root)
    written = backup(root, instance, to, with_files=with_files)
    log.step('backup')
    log('Backup: ' + str(written[0]))
    log('With: ' + ', '.join(item.name for item in written[1:]))
    instance_path = root / 'instances' / instance
    if not with_files:
        log(f'Not included: {instance_path / "files"} (uploaded attachments); add --with-files to archive it.')
    log(f'Not included: {instance_path / "secret.key"}. Without it a restored database cannot open saved connector, '
        'mailbox and two-step secrets. Keep a copy stored apart from these backups.')
    state = lock_state(root, instance)
    return log.outcome(backup=str(written[0]), before=state, after=state, value=written)


def backup_list(root, instance, *, out=None):
    """The main files of the backup sets in the instance's data/backups, newest first; value is their paths.

    Only names are read. Each main file is what backup_restore takes; its side files sit next to it."""
    from ..writes import STORES
    log = _Log(out)
    folder = child(Path(root), 'instances', instance) / 'data' / 'backups'
    if not (folder.parent.parent / 'config.py').is_file():
        raise ValueError(f'Instance does not exist: {instance}')
    sides = tuple(f'-{store}.sqlite3' for store in STORES)
    found = sorted((item for item in folder.glob('*.sqlite3') if item.is_file() and not item.is_symlink()
                    and not item.name.endswith(sides)), key=lambda item: item.name, reverse=True) if folder.is_dir() else []
    for item in found:
        log(str(item))
    return log.outcome(value=tuple(found))


def backup_restore(root, instance, source, *, yes=False, confirm=None, out=None):
    """evlet restore SOURCE --instance INSTANCE: replace the database files with a backup set. Needs the server stopped.

    Without yes, confirm(question) must return True: the CLI asks for the instance name to be typed.
    value is (safety copy of the replaced files or None, whether evlet migrate must move the side tables)."""
    from .backup import restore
    log, root = _Log(out), Path(root)
    if not _agreed(yes, confirm, f'This replaces every database file of {instance}. Type the instance name to restore: '):
        raise ValueError('Restore cancelled; nothing was changed')
    before = lock_state(root, instance)
    safety, single = restore(root, instance, source)
    if safety:
        log.step('safety copy')
    log.step('restore')
    log(f'Instance {instance}: restored from {source}')
    if safety:
        log(f'The files it replaced are kept in {safety}')
    if single:
        log(f'This backup is from before sessions, logs and Eva had their own files. Run evlet migrate {instance} to move them before serving.')
    return log.outcome(backup=str(safety) if safety else None, before=before, after=lock_state(root, instance),
                       value=(safety, single))


# Services: restart and upgrade


def restart_instance(root, instance, *, service=None, check_only=False, yes=False, confirm=None, timeout=30.0,
                     capture=True, out=None, unit_dir=None, run=None, which=None, fetch=None, sleep=time.sleep,
                     clock=time.monotonic):
    """evlet restart INSTANCE: run the read-only checks, then sudo systemctl restart and wait for /health.

    ok is False when a check failed, nobody confirmed, or the service did not come back. capture=False lets
    systemctl write to the terminal, as the CLI does. unit_dir, run, which, fetch, sleep and clock replace the
    system for tests."""
    from .restart import restart_service
    if not 1 <= timeout <= 3600:
        raise ValueError('Use a --timeout between 1 and 3600 seconds')
    log = _Log(out)
    state = lock_state(root, instance)
    ok = restart_service(Path(root), instance, service=service, check_only=check_only, yes=yes, confirm=confirm,
                         timeout=timeout, unit_dir=unit_dir, run=run, which=which, fetch=fetch, sleep=sleep, clock=clock,
                         out=log, capture=capture)
    return log.outcome(ok, before=state, after=state)


def upgrade_evlet(root, instance, *, to=None, service=None, yes=False, confirm=None, all_instances=False, capture=True,
                  out=None, unit_dir=None, run=None, which=None):
    """evlet upgrade INSTANCE: stage a newer Evlet, plan, stop, back up, migrate and restart the instances.

    confirm is asked once, after the staged plans and before anything stops; a confirm that answers False is a plan
    only, with the plans in messages and nothing changed. backup is the instance's backup and value every backup
    written, by instance. capture=False lets the commands write to the terminal, as the CLI does."""
    from .upgrade import upgrade_project
    log, root = _Log(out), Path(root)
    before = lock_state(root, instance)
    backups = {}
    ok = upgrade_project(root, instance, to=to, service=service, yes=yes, confirm=confirm, all_instances=all_instances,
                         unit_dir=unit_dir, run=run, which=which, out=log, capture=capture, backups=backups)
    return log.outcome(ok, backup=backups.get(instance), before=before, after=lock_state(root, instance),
                       value={item: path for item, path in backups.items() if path})


# Hosts


def host_add(root, instance, host, frontend, *, apply=False, ips=None, port=None, routes_only=False, out=None, run=None,
             user=None):
    """evlet host add INSTANCE HOST --root APP.FRONTEND: routes.py, the DNS check, the Caddy files, with apply the sudo steps."""
    from . import hosts
    log = _Log(out)
    hosts.add(Path(root), instance, host, frontend, apply=apply, ips=ips, port=port, routes_only=routes_only, run=run,
              user=user, out=log)
    log.step('host ' + host)
    return log.outcome()


def host_remove(root, instance, host, *, apply=False, port=None, routes_only=False, out=None, run=None, user=None):
    """evlet host remove INSTANCE HOST: take the host out of routes.py and the Caddy files."""
    from . import hosts
    log = _Log(out)
    hosts.remove(Path(root), instance, host, apply=apply, port=port, routes_only=routes_only, run=run, user=user, out=log)
    log.step('host ' + host)
    return log.outcome()


def host_list(root, instance, *, out=None):
    """evlet host list INSTANCE: the primary host and each sub-host with its root frontend."""
    from . import hosts
    log = _Log(out)
    log(hosts.listing(Path(root), instance))
    return log.outcome()


def host_check(root, instance, host, frontend=None, *, ips=None, out=None):
    """Whether the instance can serve host (and frontend at its root) and the host resolves here; writes nothing."""
    from . import hosts, proxy, site
    log, root = _Log(out), Path(root)
    hosts.check_host(root, instance, host, frontend)
    report, ok = proxy.check(None, ips or site.saved_addresses(root), hosts=(host,))
    log(report)
    return log.outcome(ok)


# Apps, builds and instances


def app_create(root, name, *, legacy=False, out=None):
    """evlet app NAME: scaffold an app. value is its folder."""
    from .app import create_app
    log = _Log(out)
    target = create_app(Path(root), name, legacy=legacy)
    log(f'App created: {target}')
    return log.outcome(value=target)


def app_fetch(root, url, *, trust=False, confirm=None, out=None):
    """evlet app --get URL: fetch statically, then prepare and build after trust. value is the Package."""
    from .app import get_app
    log = _Log(out)
    package = get_app(Path(root), url, trust=trust, confirm=confirm, out=log)
    return log.outcome(value=package)


def build(root, *, trust=False, confirm=None, out=None):
    """evlet build: a versioned generation of the project's apps, without activating or migrating it. value is its manifest."""
    from .app import prepare_dependencies
    from ..builds import prepare
    log, root = _Log(out), Path(root)
    names = [path.name for path in sorted((root / 'apps').iterdir()) if path.is_dir() and not path.name.startswith('.')]
    if not _agreed(trust, confirm, 'Trust and build these local applications: ' + ', '.join(names) + '?'):
        raise ValueError('Build requires explicit trust; review source, then use --trust')
    packages = prepare_dependencies(root, names)
    _, manifest = prepare(root, packages)
    log('Built generation: ' + manifest['fingerprint'])
    return log.outcome(value=manifest)


def instance_create(root, name, *, out=None):
    """evlet instance NAME: an empty instance folder. value is its path."""
    from .instance import create_instance
    log = _Log(out)
    target = create_instance(Path(root), name)
    log(f'Instance created: {target}')
    return log.outcome(after=lock_state(root, name), value=target)
