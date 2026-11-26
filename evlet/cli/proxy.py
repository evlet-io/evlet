"""Reverse proxy configuration from an instance's route table. It prints or writes a file; it never reloads a service."""

import ipaddress
import os
import re
from pathlib import Path
import socket
import tempfile

# Framework pages that live on the primary host only; the proxy refuses them on sub-hosts too.
PRIMARY_ONLY = ('controls', 'docs')


def _hosts(routes):
    if not routes.primary:
        raise ValueError('Set a primary host in routes.py or Instance.domain before writing a proxy configuration')
    return routes.hosts()


# The proxy runs on this server and connects to Evlet on the loopback address.
LOCAL = ('127.0.0.1', '::1')


def trust_line(trusted=()):
    """The trusted_proxies argument to add to Instance(...), or None when the local proxy is already trusted."""
    from ..auth import _trusted, trusted_proxies_line
    networks = [ipaddress.ip_network(value, strict=False) for value in trusted]
    if all(_trusted(networks, address) for address in LOCAL):
        return None
    return trusted_proxies_line(trusted, LOCAL)


def _note(instance, trusted=()):
    line = trust_line(trusted)
    if line is None:
        return '# Instance(trusted_proxies=...) already trusts this proxy, so Evlet reads client addresses from X-Forwarded-For.\n\n'
    return (f'# Evlet reads client addresses from X-Forwarded-For only from trusted proxies. Add this line to Instance(...)\n'
            f'# in instances/{instance}/config.py, then restart Evlet:\n#     {line},\n\n')


# Caddy's global options: refuse a request whose Host differs from the TLS server name (SNI), so a visitor
# can't reach one host's site through another host's certificate. They must open the Caddyfile, which is why
# evlet setup --apply installs them as their own file and imports it on the Caddyfile's first line.
GLOBAL_OPTIONS = '''{
\tservers :443 {
\t\tstrict_sni_host on
\t}
}
'''


def caddy(routes, port=8000, instance='<name>', trusted=(), global_options=True):
    """A Caddyfile for the instance's hosts. global_options=False leaves out the global block, for a file
    another Caddyfile imports (evlet setup --apply installs GLOBAL_OPTIONS on its own)."""
    from ..app import PUBLIC_PATHS
    sites = []
    for host in _hosts(routes):
        # /_/ holds framework routes. The MCP endpoint, its OAuth sign-in and the mailbox sign-in return
        # are public; the rest, such as the on-demand TLS check, are for local callers only.
        lines = [f'{host} {{', '\t@internal {', '\t\tpath /_/*', '\t\tnot path ' + ' '.join(PUBLIC_PATHS), '\t}', '\trespond @internal 404']
        if host in routes.sub_hosts() and not routes.public(host):
            lines += [f'\t@primary_only path_regexp (?i)^/({"|".join(PRIMARY_ONLY)})(/|$)', '\trespond @primary_only 404']
        # Browsers that saw HTTPS once never try plain HTTP again, so a first visit can't be downgraded later.
        lines += ['\theader Strict-Transport-Security "max-age=31536000"', f'\treverse_proxy 127.0.0.1:{port}', '}']
        sites.append('\n'.join(lines))
    return _note(instance, trusted) + (GLOBAL_OPTIONS + '\n' if global_options else '') + '\n\n'.join(sites) + '\n'


def _nginx_proxy(port, body):
    return f'''proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_read_timeout 3600s;
        client_max_body_size {body};'''


def nginx(routes, port=8000, instance='<name>', trusted=()):
    from ..app import PUBLIC_PATHS, request_body_limit
    from ..controls import IMAGE_LIMITS
    hosts = _hosts(routes)
    blocks = [f'''server {{
    listen 80;
    listen [::]:80;
    server_name {' '.join(hosts)};
    return 301 https://$host$request_uri;
}}''']
    # Exact locations win over the /_/ prefix, so only the public framework routes pass. Evlet reads at most 64 KiB there.
    public = ''
    for path in PUBLIC_PATHS:
        private_callback = '        access_log off;\n' if path in ('/_/oidc/callback', '/_/mail/callback') else ''
        public += f'    location = {path} {{\n{private_callback}        {_nginx_proxy(port, "128k")}\n    }}\n'
    # Match the application's encoded-body ceilings. Use the supported image maximum so a later
    # Controls adjustment never requires a proxy reload; Evlet still applies the actual per-image limit.
    image_bytes = IMAGE_LIMITS['files.image_max_kb'][1] * 1024
    upload_routes = (
        ('= /api/_settings/me/photo', '/api/_settings/me/photo'),
        ('= /api/_eva/files', '/api/_eva/files'),
        ('~ ^/api/.*/(attachments|_images)$', '/api/model/_images'),
        ('~ ^/api/_controls/prints', '/api/_controls/prints'),
        ('~ ^/api/_unsaved/', '/api/_unsaved/item'),
    )
    uploads = ''.join(f'    location {match} {{\n        {_nginx_proxy(port, request_body_limit(path, image_bytes))}\n    }}\n'
                      for match, path in upload_routes)
    for host in hosts:
        block = f'''server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {host};
    ssl_certificate /etc/letsencrypt/live/{host}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{host}/privkey.pem;
    if ($ssl_server_name != $host) {{ return 421; }}
    add_header Strict-Transport-Security "max-age=31536000" always;
{public}    location ^~ /_/ {{ return 404; }}
'''
        if host in routes.sub_hosts() and not routes.public(host):
            block += f'    location ~* ^/({"|".join(PRIMARY_ONLY)})(/|$) {{ return 404; }}\n'
        block += uploads + f'''    location / {{
        {_nginx_proxy(port, request_body_limit('/api/model', image_bytes))}
    }}
}}'''
        blocks.append(block)
    return _note(instance, trusted) + '\n\n'.join(blocks) + '\n'


def _caddy_path(pattern, path):
    """Caddy's path matcher: exact, or * as a wildcard; case-insensitive."""
    return re.fullmatch('.*'.join(re.escape(part) for part in pattern.lower().split('*')), path.lower()) is not None


def caddy_refuses(text, paths):
    """The paths a Caddy file answers with 404 before Evlet sees them, on any of its sites.

    It reads the matchers evlet proxy writes now and wrote before (path, not path and path_regexp,
    on one line or in a block) used by respond @name 404, so start-up can warn about an outdated file.
    """
    matchers, refusing, block = {}, set(), None
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if block is not None:
            if line == '}':
                block = None
            elif line:
                matchers[block].append(line.split())
            continue
        words = line.split()
        if len(words) >= 2 and words[0].startswith('@'):
            if words[1:] == ['{']:
                block = words[0]
                matchers[block] = []
            else:
                matchers[words[0]] = [words[1:]]
        elif len(words) >= 3 and words[0] == 'respond' and words[1].startswith('@') and words[2] == '404':
            refusing.add(words[1])

    def matches(conditions, path):
        for condition in conditions:
            negate = condition[0] == 'not'
            kind, *values = condition[1:] if negate else condition
            if kind == 'path':
                hit = any(_caddy_path(value, path) for value in values)
            elif kind == 'path_regexp' and values:
                hit = re.search(values[-1], path) is not None
            else:
                return False  # a matcher this reader does not know never counts as refusing
            if hit == negate:
                return False
        return bool(conditions)
    return [path for path in paths if any(matches(matchers.get(name, ()), path) for name in refusing)]


def server_addresses():
    """This server's outgoing addresses, found from the routing table; no packet is sent."""
    from ..hosts import server_addresses
    return server_addresses()


def check(routes, addresses=None, resolve=socket.getaddrinfo, hosts=None):
    """One line per host saying where its A/AAAA records point, then any warnings, and whether setup may go on.

    It checks the route table's hosts, or only hosts when given (evlet host add checks a host before adding it).
    It stops (False) only for a host with no record, or with only private records while this server has a public address.
    Each host is checked by evlet.hosts.dns_status, which the host check job uses too."""
    from ..hosts import dns_status
    hosts = tuple(hosts) if hosts is not None else _hosts(routes)
    addresses = addresses or server_addresses()
    lines, warnings, ok = [], [], True
    width = max(len(host) for host in hosts) + 3
    for host in hosts:
        records, status, passed, warning = dns_status(host, addresses, resolve)
        ok = ok and passed
        if warning:
            warnings.append(warning)
        lines.append(host.ljust(width) + (', '.join(records) or '-').ljust(32) + status)
    return '\n'.join(lines + warnings), ok


def write(path, text):
    """Write the configuration atomically to an explicit path."""
    target = Path(path).absolute()
    with tempfile.NamedTemporaryFile('w', dir=target.parent, prefix='.' + target.name, delete=False) as handle:
        handle.write(text)
    os.chmod(handle.name, 0o644)
    os.replace(handle.name, target)
    return target


def systemd_unit(root, instance, port=8000, user='evlet'):
    """A systemd unit that runs evlet serve on loopback, so the proxy is the only public listener. Review before installing it.

    It runs the web requests and the job queue in one process (--jobs), so job steps take their turn in the same
    write queue as people's saves. Secrets go in /etc/evlet/<instance>.env, which the unit reads when it exists."""
    import re
    from .setup import python_in
    root = Path(root)
    # systemd expands % and $ and splits on spaces; keep the paths literal.
    if not re.fullmatch(r'[A-Za-z0-9._/@+-]+', str(root)) or not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        raise ValueError('The project path and user may only use letters, digits and . _ / @ + -; move the project or choose another user')
    # ProtectHome would hide a project kept in a home directory, so it is set only for projects outside one.
    home = any(root.absolute().is_relative_to(base) for base in ('/home', '/root', '/run/user'))
    protect_home = '' if home else 'ProtectHome=read-only\n'
    return f'''[Unit]
Description=Evlet instance {instance}
After=network.target

[Service]
User={user}
WorkingDirectory={root}
ExecStart={python_in(root)} -P -m evlet serve {instance} --jobs --project {root} --host 127.0.0.1 --port {port}
Restart=on-failure
UMask=0077
# Optional secrets such as EVLET_SECRET_KEY: a root-owned 0600 file, never Environment= in this world-readable unit.
EnvironmentFile=-/etc/evlet/{instance}.env
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
{protect_home}ReadWritePaths={root}/instances/{instance}

[Install]
WantedBy=multi-user.target
'''
