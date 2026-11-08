"""Reverse proxy configuration from an instance's route table. It prints or writes a file; it never reloads a service."""

import ipaddress
import os
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


def caddy(routes, port=8000, instance='<name>', trusted=()):
    sites = []
    for host in _hosts(routes):
        # /_/ holds framework internals, such as the on-demand TLS check, for local callers only.
        lines = [f'{host} {{', '\t@internal path /_/*', '\trespond @internal 404']
        if host in routes.sub_hosts():
            lines += [f'\t@primary_only path_regexp (?i)^/({"|".join(PRIMARY_ONLY)})(/|$)', '\trespond @primary_only 404']
        lines += [f'\treverse_proxy 127.0.0.1:{port}', '}']
        sites.append('\n'.join(lines))
    return _note(instance, trusted) + '\n\n'.join(sites) + '\n'


def nginx(routes, port=8000, instance='<name>', trusted=()):
    hosts = _hosts(routes)
    blocks = [f'''server {{
    listen 80;
    listen [::]:80;
    server_name {' '.join(hosts)};
    return 301 https://$host$request_uri;
}}''']
    for host in hosts:
        block = f'''server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {host};
    ssl_certificate /etc/letsencrypt/live/{host}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{host}/privkey.pem;
    location ^~ /_/ {{ return 404; }}
'''
        if host in routes.sub_hosts():
            block += f'    location ~* ^/({"|".join(PRIMARY_ONLY)})(/|$) {{ return 404; }}\n'
        block += f'''    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_read_timeout 3600s;
        client_max_body_size 2m;
    }}
}}'''
        blocks.append(block)
    return _note(instance, trusted) + '\n\n'.join(blocks) + '\n'


def server_addresses():
    """This server's outgoing addresses, found from the routing table; no packet is sent."""
    found = set()
    for family, probe in ((socket.AF_INET, '192.0.2.1'), (socket.AF_INET6, '2001:db8::1')):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe_socket:
                probe_socket.connect((probe, 9))
                found.add(probe_socket.getsockname()[0])
        except OSError:
            pass
    return found


def check(routes, addresses=None, resolve=socket.getaddrinfo):
    """One line per host saying whether its A/AAAA records point at this server, and whether all do."""
    addresses = {str(ipaddress.ip_address(item)) for item in (addresses or server_addresses())}
    lines, ok = [], True
    if any(ipaddress.ip_address(item).is_private for item in addresses):
        lines.append('This server has a private address. Pass --ip with its public address.')
    width = max(len(host) for host in _hosts(routes)) + 3
    for host in routes.hosts():
        try:
            records = sorted({str(ipaddress.ip_address(info[4][0].split('%')[0])) for info in resolve(host, None, proto=socket.IPPROTO_TCP)})
        except (OSError, UnicodeError):
            records = []
        if not records:
            status = 'no A or AAAA record'
        elif set(records) <= addresses:
            status = 'points here'
        else:
            status = 'points elsewhere'
        ok = ok and status == 'points here'
        lines.append(host.ljust(width) + (', '.join(records) or '-').ljust(32) + status)
    return '\n'.join(lines), ok


def write(path, text):
    """Write the configuration atomically to an explicit path."""
    target = Path(path).absolute()
    with tempfile.NamedTemporaryFile('w', dir=target.parent, prefix='.' + target.name, delete=False) as handle:
        handle.write(text)
    os.chmod(handle.name, 0o644)
    os.replace(handle.name, target)
    return target


def systemd_unit(root, instance, port=8000, user='evlet'):
    """A systemd unit that runs evlet serve on loopback, so the proxy is the only public listener. Review before installing it."""
    import re
    from .setup import python_in
    root = Path(root)
    # systemd expands % and $ and splits on spaces; keep the paths literal.
    if not re.fullmatch(r'[A-Za-z0-9._/@+-]+', str(root)) or not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        raise ValueError('The project path and user may only use letters, digits and . _ / @ + -; move the project or choose another user')
    return f'''[Unit]
Description=Evlet instance {instance}
After=network.target

[Service]
User={user}
WorkingDirectory={root}
ExecStart={python_in(root)} -P -m evlet serve {instance} --jobs --project {root} --host 127.0.0.1 --port {port}
Restart=on-failure
UMask=0077

[Install]
WantedBy=multi-user.target
'''
