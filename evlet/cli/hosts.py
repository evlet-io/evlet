"""evlet host add, remove and list, and the host evlet install offers for a frontend's suggested_host.

Each command edits instances/<name>/routes.py: it creates the file, or changes its hosts = (...) tuple when
that tuple holds only Host(...) entries. Anything it can't change safely is left to its owner with a message.
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


def check_host(root, name, host, frontend):
    """Refuse a host or root that the instance can't serve before anything is written."""
    from ..routing import HOSTNAME
    if not HOSTNAME.fullmatch(host) or '..' in host or '.' not in host or host.endswith('.localhost'):
        raise ValueError(f'{host} is not a public lowercase hostname such as www.example.com')
    app = _runtime(root, name)
    if not app.routes.primary:
        raise ValueError(f'Instance {name} has no domain yet. Run evlet setup --host first, or set domain in instances/{name}/config.py')
    if host == app.routes.primary:
        raise ValueError(f'{host} is the primary host of {name}')
    if frontend is not None:
        served = app.routes.frontend(frontend)
        if served is None:
            known = ', '.join(identity for identity, item in app.routes.frontends() if item.kind != 'evlet') or 'none'
            raise ValueError(f'{frontend} is not a frontend of an installed app. Static and server frontends: {known}')
        if served.kind == 'evlet':
            raise ValueError(f'{frontend} is an evlet frontend, which stays on the primary host. Choose a static or server frontend')
    return app


def _port(root, name, port):
    """The port of the installed unit in deploy/, so a host change keeps it; else port or 8000."""
    if port is not None:
        return port
    try:
        words = (root / 'deploy' / f'evlet-{name}.service').read_text(encoding='utf-8').split()
        return int(words[words.index('--port') + 1])
    except (OSError, ValueError, IndexError):
        return 8000


def proxy_files(root, name, app, port, user=None):
    """Write deploy/evlet-<name>.caddy, the service unit and the global options, as evlet setup --host does."""
    import getpass
    from . import proxy, site
    trusted = app.business.auth.trusted_proxies
    (root / 'deploy').mkdir(mode=0o755, exist_ok=True)
    written = [proxy.write(root / 'deploy' / f'evlet-{name}.caddy', proxy.caddy(app.routes, port, name, trusted, global_options=False)),
               proxy.write(root / 'deploy' / f'evlet-{name}.service', proxy.systemd_unit(root, name, port, user or getpass.getuser())),
               proxy.write(root / 'deploy' / site.GLOBAL_FILE, proxy.GLOBAL_OPTIONS)]
    for path in written:
        print(f'Wrote {path}')


def _finish(root, name, port, apply, run, user, app):
    from . import site
    proxy_files(root, name, app, port, user)
    # The instance reads routes.py when it starts, so the service restarts after Caddy has the new site.
    commands = site.privileged_steps(root, name, restart=True)
    if not apply:
        print('Review the files, then run these steps with sudo:')
        print('\n'.join('    ' + site.describe(command) for command in commands))
        print('Or run the same command again with --apply to run them. Each one is shown first and sudo asks for your password.')
        return
    with site.step('install the Caddy site and restart the service'):
        site.run_privileged(commands, run=run)


def add(root, name, host, frontend, *, apply=False, ips=None, port=None, routes_only=False, run=None, user=None):
    """evlet host add: routes.py, the DNS check, the Caddy file, then with apply the sudo steps."""
    from . import proxy, site
    check_host(root, name, host, frontend)
    path = child(root, 'instances', name)
    if not routes_only:
        saved = None if ips else site.saved_addresses(root)
        report, ok = proxy.check(None, ips or saved, hosts=(host,))
        print(report)
        if not ok:
            raise ValueError(f'{host} has no usable A or AAAA record yet. Point it at this server, or pass --ip with the public address. Nothing was changed')
    changed, app = _checked(root, name, lambda: add_host_line(path, host, frontend))
    print(f'Added {host} to instances/{name}/routes.py, served by {frontend}' if changed else f'instances/{name}/routes.py already serves {host} with {frontend}')
    if routes_only:
        print(f'Restart the instance to serve it, such as: sudo systemctl restart evlet-{name}. '
              'Caddy with on-demand TLS gets its certificate on the first visit.')
        return
    _finish(root, name, _port(root, name, port), apply, run, user, app)
    if apply:
        print(site.site_answers(host))


def remove(root, name, host, *, apply=False, port=None, routes_only=False, run=None, user=None):
    """evlet host remove: take the host out of routes.py and the Caddy file."""
    path = child(root, 'instances', name)
    changed, app = _checked(root, name, lambda: remove_host_line(path, host))
    if not changed:
        raise ValueError(f'instances/{name}/routes.py does not list {host}')
    print(f'Removed {host} from instances/{name}/routes.py')
    if routes_only:
        print(f'Restart the instance to stop serving it, such as: sudo systemctl restart evlet-{name}')
        return
    _finish(root, name, _port(root, name, port), apply, run, user, app)


def listing(root, name):
    """One line per host: the primary host, then each sub-host with its root frontend and realm."""
    app = _runtime(root, name)
    routes = app.routes
    if not routes.primary:
        return f'Instance {name} has no domain; it serves localhost only'
    rows = [('HOST', 'ROOT', 'SIGN-IN'), (routes.primary, 'evlet.desk', 'desk')]
    rows += [(host, routes.root(host), routes.realm(host)) for host in routes.sub_hosts()]
    widths = [max(len(row[index]) for row in rows) + 3 for index in range(2)]
    return '\n'.join(row[0].ljust(widths[0]) + row[1].ljust(widths[1]) + row[2] for row in rows)


def suggest(root, name, apps, *, yes=False, skip=False, ask=None):
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
            print(f'{identity} suggests the host {offer}. Serve it with: evlet host add {name} {offer} --root {identity}')
            continue
        else:
            answer = ask(f'Serve {identity} on its own host? Press Enter for {offer}, type your own domain, or type no: ').strip().lower()
            if answer in ('n', 'no'):
                continue
            host = answer or offer
        check_host(root, name, host, identity)
        path = child(root, 'instances', name)
        _checked(root, name, lambda: add_host_line(path, host, identity))
        print(f'Added {host} to instances/{name}/routes.py, served by {identity}')
        print(f'Next: evlet host add {name} {host} --root {identity} --apply checks DNS, updates Caddy and restarts the service')
        added.append(host)
    return added
