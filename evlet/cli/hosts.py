"""evlet host add, remove, list, check, enable and disable, and the host evlet install offers for a frontend's suggested_host.

Without --live, add and remove edit instances/<name>/routes.py: they create the file, or change its hosts = (...)
tuple when that tuple holds only Host(...) entries. Anything they can't change safely is left to its owner with a
message, and the instance reads the change when it restarts. With --live a host is a row of evlet_hosts instead
(evlet.hosts): the running instance serves it within a second once its DNS check passes, without a restart.
"""

import ast
import os
from pathlib import Path
import tempfile

from .project import child

IMPORT = 'from evlet import Host\n'


def _refuse(where, host, root=None):
    line = f'Host("{host}", root="{root}")' if root else f'Host("{host}", ...)'
    action = f'add {line} to its hosts tuple' if root else f'remove {line} from its hosts tuple'
    return ValueError(f'{where} sets hosts in a way evlet cannot change safely. Edit it yourself: {action}, then restart the instance')


def _entries(text, where, host, root):
    """The hosts assignment and its (host name, root, source) entries, or (None, []) when routes.py has none."""
    tree = ast.parse(text, where)
    found = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == 'hosts' and isinstance(node.ctx, ast.Store)]
    if not found:
        return None, []
    assigns = [node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'hosts' for target in node.targets)]
    if len(found) != 1 or len(assigns) != 1 or len(assigns[0].targets) != 1 or not isinstance(assigns[0].value, ast.Tuple):
        raise _refuse(where, host, root)
    node = assigns[0]
    lines = text.splitlines(keepends=True)
    segment = ast.get_source_segment(text, node)
    # The tuple is rewritten whole, so it must stand on lines of its own and hold no comments to lose.
    if lines[node.end_lineno - 1][node.end_col_offset:].strip() or '#' in segment:
        raise _refuse(where, host, root)
    entries = []
    for element in node.value.elts:
        if not (isinstance(element, ast.Call) and isinstance(element.func, ast.Name) and element.func.id == 'Host' and element.args
                and isinstance(element.args[0], ast.Constant) and isinstance(element.args[0].value, str)):
            raise _refuse(where, host, root)
        served = next((keyword.value for keyword in element.keywords if keyword.arg == 'root'), element.args[1] if len(element.args) > 1 else None)
        name = served.value if isinstance(served, ast.Constant) else served.id if isinstance(served, ast.Name) else None
        entries.append((element.args[0].value, name, ast.get_source_segment(text, element)))
    return node, entries


def _imports_host(text):
    tree = ast.parse(text)
    return any(isinstance(node, ast.ImportFrom) and node.module == 'evlet' and any((alias.asname or alias.name) == 'Host' for alias in node.names)
               for node in tree.body)


def _with_import(text):
    """text with from evlet import Host after its last top-level import (or its docstring), unless it has it."""
    if _imports_host(text):
        return text
    tree = ast.parse(text)
    after = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or (node is tree.body[0] and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
            after = node.end_lineno
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:after]) + IMPORT + ('\n' if not after else '') + ''.join(lines[after:])


def _replace(text, node, sources):
    block = 'hosts = (\n' + ''.join(f'    {source},\n' for source in sources) + ')\n' if sources else 'hosts = ()\n'
    if node is None:
        return text + ('\n' if text and not text.endswith('\n') else '') + ('\n' if text.strip() else '') + block
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + block + ''.join(lines[node.end_lineno:])


def _write(target, text):
    compile(text, str(target), 'exec')
    with tempfile.NamedTemporaryFile('w', dir=target.parent, prefix='.routes.py.', delete=False, encoding='utf-8') as handle:
        handle.write(text)
    os.chmod(handle.name, 0o644)
    os.replace(handle.name, target)


def _routes_file(path):
    target = path / 'routes.py'
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError(f'Instance routes must be a regular file: {path.name}/routes.py')
    return target


def add_host_line(path, host, root):
    """Add Host(host, root="app.frontend") to routes.py. Returns False when it is already there with that root."""
    from ..routing import Host
    Host(host, root)
    target, where = _routes_file(path), f'instances/{path.name}/routes.py'
    text = target.read_text(encoding='utf-8') if target.exists() else ''
    node, entries = _entries(text, where, host, root)
    for name, served, _ in entries:
        if name == host:
            if served == root:
                return False
            raise ValueError(f'{where} already serves {host} with root {served or "another frontend"}. Run evlet host remove first')
    new = _with_import(_replace(text, node, [source for _, _, source in entries] + [f'Host("{host}", root="{root}")']))
    _write(target, new)
    return True


def remove_host_line(path, host):
    """Remove the Host(host, ...) entry from routes.py. Returns False when routes.py does not list it."""
    target, where = _routes_file(path), f'instances/{path.name}/routes.py'
    if not target.exists():
        return False
    text = target.read_text(encoding='utf-8')
    node, entries = _entries(text, where, host, None)
    if host not in [name for name, _, _ in entries]:
        return False
    _write(target, _replace(text, node, [source for name, _, source in entries if name != host]))
    return True


def _runtime(root, name):
    from ..instances import load_runtime
    app = load_runtime(root, name)
    app.database.close()
    return app


def _checked(root, name, change):
    """Run change() on routes.py, then load the instance; put the old file back if it no longer starts."""
    target = child(root, 'instances', name) / 'routes.py'
    before = target.read_bytes() if target.exists() else None
    changed = change()
    if changed:
        try:
            return changed, _runtime(root, name)
        except Exception:
            if before is None:
                target.unlink()
            else:
                _write(target, before.decode('utf-8'))
            raise
    return changed, _runtime(root, name)


def check_host(root, name, host, frontend, *, site=False):
    """Refuse a host or root that the instance can't serve before anything is written. site=True (--live) also
    accepts a site name (evlet sites), which evlet.hosts.add_host checks against the instance's sites."""
    from ..routing import HOSTNAME
    if not HOSTNAME.fullmatch(host) or '..' in host or '.' not in host or host.endswith('.localhost'):
        raise ValueError(f'{host} is not a public lowercase hostname such as www.example.com')
    app = _runtime(root, name)
    if not app.routes.primary:
        raise ValueError(f'Instance {name} has no domain yet. Run evlet setup --host first, or set domain in instances/{name}/config.py')
    if host == app.routes.primary:
        raise ValueError(f'{host} is the primary host of {name}')
    if frontend is not None and not (site and '.' not in frontend):
        served = app.routes.frontend(frontend)
        if served is None:
            known = ', '.join(identity for identity, item in app.routes.frontends() if item.kind != 'evlet') or 'none'
            raise ValueError(f'{frontend} is not a frontend of an installed app. Static and server frontends: {known}')
        if served.kind == 'evlet':
            raise ValueError(f'{frontend} is an evlet frontend, which stays on the primary host. Choose a static or server frontend')
    return app


def _port(root, name, port):
    """The port of the installed unit, so a host change keeps it; else the one in deploy/, else port or 8000."""
    from . import site
    if port is not None:
        return port
    # The installed unit belongs to root; the copy in deploy/ is only a fallback before the first --apply.
    for unit in (site.installed(name)[0], root / 'deploy' / f'evlet-{name}.service'):
        try:
            words = unit.read_text(encoding='utf-8').split()
            return int(words[words.index('--port') + 1])
        except (OSError, ValueError, IndexError):
            continue
    return 8000


def proxy_files(root, name, app, port, user=None, out=print):
    """Write deploy/evlet-<name>.caddy, the service unit and the global options, as evlet setup --host does.

    Returns the rendered texts, which --apply installs (never the copies in deploy/)."""
    from . import proxy, site
    trusted = app.business.auth.trusted_proxies
    account, chosen = site.service_account(name, user)
    texts = {'unit': proxy.systemd_unit(root, name, port, account),
             'site': proxy.caddy(app.routes, port, name, trusted, global_options=False), 'options': proxy.GLOBAL_OPTIONS}
    (root / 'deploy').mkdir(mode=0o755, exist_ok=True)
    written = [proxy.write(root / 'deploy' / f'evlet-{name}.caddy', texts['site']),
               proxy.write(root / 'deploy' / f'evlet-{name}.service', texts['unit']),
               proxy.write(root / 'deploy' / site.GLOBAL_FILE, texts['options'])]
    for path in written:
        out(f'Wrote {path}')
    note = site.account_note(account, chosen)
    if note:
        out(note)
    return texts


def _finish(root, name, port, apply, run, user, app, out=print, restart=True):
    from . import site
    texts = proxy_files(root, name, app, port, user, out)
    # The instance reads routes.py when it starts, so the service restarts after Caddy has the new site.
    # A host from data needs no restart: the running instance picks it up by itself.
    commands = site.privileged_steps(root, name, texts, restart=restart)
    if not apply:
        out('Review the files, then run these steps with sudo:')
        out('\n'.join('    ' + site.describe(command) for command in commands))
        out('Or run the same command again with --apply to run them. Each one is shown first and sudo asks for your password.')
        return
    with site.step('install the Caddy site and restart the service'):
        site.run_privileged(commands, run=run, out=out)


def add(root, name, host, frontend, *, apply=False, ips=None, port=None, routes_only=False, run=None, user=None, live=False,
        out=print):
    """evlet host add: routes.py, the DNS check, the Caddy file, then with apply the sudo steps. live adds a host from data."""
    from . import proxy, site
    if live:
        return add_live(root, name, host, frontend, apply=apply, ips=ips, port=port, run=run, user=user, out=out)
    check_host(root, name, host, frontend)
    if host in _data_rows(root, name):
        raise ValueError(f'{host} is a host from data. Remove it with evlet host remove {name} {host} first, or keep it there')
    path = child(root, 'instances', name)
    if not routes_only:
        saved = None if ips else site.saved_addresses(root)
        report, ok = proxy.check(None, ips or saved, hosts=(host,))
        out(report)
        if not ok:
            raise ValueError(f'{host} has no usable A or AAAA record yet. Point it at this server, or pass --ip with the public address. Nothing was changed')
    changed, app = _checked(root, name, lambda: add_host_line(path, host, frontend))
    out(f'Added {host} to instances/{name}/routes.py, served by {frontend}' if changed else f'instances/{name}/routes.py already serves {host} with {frontend}')
    if routes_only:
        out(f'Restart the instance to serve it: evlet restart {name}. '
              'Caddy with on-demand TLS gets its certificate on the first visit.')
        return
    _finish(root, name, _port(root, name, port), apply, run, user, app, out)
    if apply:
        out(site.site_answers(host))


def remove(root, name, host, *, apply=False, port=None, routes_only=False, run=None, user=None, out=print):
    """evlet host remove: take the host out of routes.py and the Caddy file, or remove a host from data."""
    path = child(root, 'instances', name)
    changed, app = _checked(root, name, lambda: remove_host_line(path, host))
    if not changed and host in _data_rows(root, name):
        return remove_live(root, name, host, apply=apply, port=port, run=run, user=user, out=out)
    if not changed:
        raise ValueError(f'instances/{name}/routes.py does not list {host}')
    out(f'Removed {host} from instances/{name}/routes.py')
    if routes_only:
        out(f'Restart the instance to stop serving it: evlet restart {name}')
        return
    _finish(root, name, _port(root, name, port), apply, run, user, app, out)


def _table(rows):
    widths = [max(len(row[index]) for row in rows) + 3 for index in range(len(rows[0]) - 1)]
    return '\n'.join(''.join(value.ljust(width) for value, width in zip(row, widths)) + row[-1] for row in rows)


def _open(root, name):
    from ..instances import load_runtime
    return load_runtime(root, name)


def _data_rows(root, name):
    """{host: row} of the instance's hosts from data."""
    from ..hosts import list_hosts
    app = _open(root, name)
    try:
        return {row['host']: row for row in list_hosts(app.business)}
    finally:
        app.database.close()


def _data_table(rows):
    lines = [('HOST', 'ROOT', 'STATUS', 'DNS')]
    for row in rows:
        status = row['status'] if row['served'] or row['status'] != 'active' else 'active, not served'
        dns = row['problem'] or row['error'] or (row['records'] and 'points here: ' + row['records']) or ('-' if row['checked_at'] is None else '')
        lines.append((row['host'], row['target'], status, dns))
    return _table(lines)


def listing(root, name):
    """One line per host: the primary host, then each sub-host of routes.py with its root frontend and realm,
    then the hosts from data with their status."""
    from ..hosts import list_hosts
    app = _open(root, name)
    try:
        routes = app.routes
        data = list_hosts(app.business)
    finally:
        app.database.close()
    if not routes.primary:
        return f'Instance {name} has no domain; it serves localhost only'
    rows = [('HOST', 'ROOT', 'SIGN-IN'), (routes.primary, 'evlet.desk', 'desk')]
    rows += [(host, routes.root(host), routes.realm(host)) for host in routes.sub_hosts() if host not in routes.data_hosts()]
    text = _table(rows)
    if data:
        text += '\n\nHosts from data, served without a restart once their DNS check passes:\n' + _data_table(data)
    return text


def _after_live(root, name, app, active, apply, port, run, user, out=print):
    """What a host from data needs from Caddy: nothing with on-demand TLS; with a Caddy site per host, the site again."""
    if apply:
        _finish(root, name, _port(root, name, port), True, run, user, app, out, restart=False)
    elif active:
        out(f'With Caddy on-demand TLS nothing else is needed. With a Caddy site per host, run evlet host check {name} --apply '
              'to write it again and reload Caddy; the service keeps running.')


def add_live(root, name, host, frontend, *, apply=False, ips=None, port=None, run=None, user=None, out=print):
    """evlet host add --live: a pending host in evlet_hosts, checked at once; served when the check passes."""
    from ..hosts import add_host, check_hosts
    check_host(root, name, host, frontend, site=True)
    app = _open(root, name)
    try:
        add_host(app.business, host, frontend, actor='cli')
        out(f'Added {host} to the hosts of {name}, served by {frontend}. It is pending until its DNS points here.')
        row = check_hosts(app.business, [host], actor='cli', addresses=ips)[0]
        out(_data_table([row]))
        active = row['status'] == 'active'
        out('Active: the running instance serves it within a second, without a restart.' if active else
              'The instance checks pending hosts every minute and serves this one once its A or AAAA record points here.')
        _after_live(root, name, app, active, apply, port, run, user, out)
    finally:
        app.database.close()


def remove_live(root, name, host, *, apply=False, port=None, run=None, user=None, out=print):
    from ..hosts import remove_host
    app = _open(root, name)
    try:
        remove_host(app.business, host, actor='cli')
        out(f'Removed {host} from the hosts of {name}. The running instance stops serving it within a second.')
        if apply:
            _finish(root, name, _port(root, name, port), True, run, user, app, out, restart=False)
    finally:
        app.database.close()


def check(root, name, hosts=(), *, ips=None, apply=False, port=None, run=None, user=None, out=print):
    """evlet host check: the DNS check of hosts from data (default: all but disabled); a pass makes a pending host active."""
    from ..hosts import check_hosts
    app = _open(root, name)
    try:
        before = set(app.routes.data_hosts())
        rows = check_hosts(app.business, list(hosts) or None, actor='cli', addresses=ips)
        if not rows:
            out(f'Instance {name} has no hosts from data to check. Add one with evlet host add {name} HOST --root APP.FRONTEND --live')
            return
        out(_data_table(rows))
        _after_live(root, name, app, set(app.routes.data_hosts()) != before, apply, port, run, user, out)
    finally:
        app.database.close()


def set_status(root, name, host, status, out=print):
    """evlet host disable / enable: stop serving a host from data, or set it back to pending for the next check."""
    from ..hosts import set_host_status
    app = _open(root, name)
    try:
        set_host_status(app.business, host, status, actor='cli')
    finally:
        app.database.close()
    out(f'Disabled {host}; the running instance stops serving it within a second' if status == 'disabled' else
          f'{host} is pending again; it is served once a DNS check passes (evlet host check {name} {host})')


def suggest(root, name, apps, *, yes=False, skip=False, ask=None, out=print):
    """After evlet install adds apps: offer <label>.<instance domain> for each frontend with suggested_host.

    yes takes each suggestion, skip offers nothing. Without a terminal (and without yes) it only prints the
    command. It writes routes.py only; the printed evlet host add command checks DNS and updates Caddy."""
    if skip or not apps:
        return []
    app = _runtime(root, name)
    domain = app.routes.primary
    if not domain:
        return []
    served = {app.routes.root(host) for host in app.routes.sub_hosts()}
    added = []
    for identity, frontend in app.routes.frontends():
        if frontend.suggested_host is None or identity.split('.')[0] not in apps or identity in served:
            continue
        offer = f'{frontend.suggested_host}.{domain}'
        if yes:
            host = offer
        elif ask is None:
            out(f'{identity} suggests the host {offer}. Serve it with: evlet host add {name} {offer} --root {identity}')
            continue
        else:
            answer = ask(f'Serve {identity} on its own host? Press Enter for {offer}, type your own domain, or type no: ').strip().lower()
            if answer in ('n', 'no'):
                continue
            host = answer or offer
        check_host(root, name, host, identity)
        path = child(root, 'instances', name)
        _checked(root, name, lambda: add_host_line(path, host, identity))
        out(f'Added {host} to instances/{name}/routes.py, served by {identity}')
        out(f'Next: evlet host add {name} {host} --root {identity} --apply checks DNS, updates Caddy and restarts the service')
        added.append(host)
    return added
