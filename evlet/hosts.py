"""Hosts from data: sub-hosts kept in evlet_hosts, served without a restart.

routes.py stays the code: its primary host and sub-hosts are read once at start, and a clash there fails
the boot. A row in evlet_hosts adds one sub-host on top of that table (evlet.routing.with_hosts). Each
process checks a version row at most once a second and swaps in a rebuilt RouteTable when it moved, so a
host written by the CLI, a job or another process is served within about a second.

A host starts pending. It becomes active once its DNS points here, by the same check as
`evlet proxy --check` (dns_status), which the evlet_host_check job runs on pending hosts every minute.
Only active hosts are in the route table, so only they get 200 from /_/tls-allowed. A row the table
can't serve (an app was removed, routes.py took the host) is skipped and listed under Needs attention.

A target with no dot names an instance site (evlet.sites); its folder is the site's current release, read
with the hosts whenever the version row moves, so a publish or rollback is served the same way.

The functions below are the API the CLI (evlet host ... --live), Controls and the MCP host tools call.
Each change is audited (area Hosts) and bumps the version row.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as Late
import ipaddress
import json
import logging
from pathlib import Path
import socket
from threading import Lock
import time

from . import qb
from .adapter import Column, OperationalError, TableDefinition

logger = logging.getLogger(__name__)

STATUSES = ('pending', 'active', 'disabled')
HOSTS = TableDefinition('evlet_hosts', (
    Column('host', 'TEXT', primary=True),
    # A frontend id ("app.frontend") or, once sites exist, a site name (evlet.routing.target_error).
    Column('target', 'TEXT', required=True),
    Column('status', 'TEXT', required=True, default='pending'),
    Column('records', 'TEXT', required=True, default=''),
    Column('checked_at', 'INTEGER'),
    Column('error', 'TEXT'),
    Column('created_at', 'INTEGER', required=True),
    Column('created_by', 'TEXT', required=True),
    Column('changed_at', 'INTEGER', required=True),
), checks=(qb.in_('status', STATUSES),))
VERSION = TableDefinition('evlet_hosts_version', (
    Column('id', 'INTEGER', primary=True),
    Column('version', 'INTEGER', required=True),
))
CHECK_TASK = 'evlet_host_check'
CHECK_EVERY = 60
# How often a process looks at the version row.
REFRESH_SECONDS = 1.0
LOOKUP_SECONDS = 5
SERVER_FILE = 'server.json'
DNS = ('points here', 'may point here', 'points elsewhere', 'no A or AAAA record')


def ensure(connection):
    """The framework step 0019_hosts: the hosts table and its version row."""
    connection.ensure_table(HOSTS)
    connection.ensure_table(VERSION)
    if connection.run(qb.select('evlet_hosts_version', 'version').where(id=1)).one() is None:
        connection.run(qb.insert('evlet_hosts_version', id=1, version=0))


def _bump(connection):
    connection.run(qb.update('evlet_hosts_version', version=qb.col('version') + 1).where(id=1))


# ---------- the DNS check (evlet proxy --check) ----------

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


def bounded(resolve):
    """A DNS lookup that gives up after LOOKUP_SECONDS, so a slow resolver can't hold a request or a job."""
    def lookup(host, port, **options):
        pool = ThreadPoolExecutor(1)
        try:
            return pool.submit(resolve, host, port, **options).result(LOOKUP_SECONDS)
        except Late:
            raise OSError(f'{host} did not resolve within {LOOKUP_SECONDS} seconds') from None
        finally:
            pool.shutdown(wait=False)
    return lookup


def dns_status(host, addresses, resolve=socket.getaddrinfo):
    """(records, status, passed, warning) for one host: where its A/AAAA records point compared with addresses.

    status is one of DNS. It fails only with no record, or with only private records while this server has a
    public address. Behind NAT (most cloud servers) the server sees only private addresses, so a public record
    can't be matched: that passes as "may point here" with a warning."""
    addresses = {str(ipaddress.ip_address(item)) for item in addresses}
    public_here = any(ipaddress.ip_address(item).is_global for item in addresses)
    try:
        records = sorted({str(ipaddress.ip_address(info[4][0].split('%')[0])) for info in resolve(host, None, proto=socket.IPPROTO_TCP)})
    except (OSError, UnicodeError, ValueError):
        records = []
    public = any(ipaddress.ip_address(item).is_global for item in records)
    if not records:
        return records, DNS[3], False, None
    if set(records) & addresses:
        return records, DNS[0], True, None
    if public or not public_here:
        return records, DNS[1], True, (f'{host} points at {", ".join(records)}. If that is not this server, fix its A record. '
                                       'Ports 80 and 443 must be open.')
    return records, DNS[2], False, None


def saved_addresses(project):
    """The public addresses a passing DNS check stored in deploy/server.json of the project, or None."""
    if project is None:
        return None
    try:
        value = json.loads((project / 'deploy' / SERVER_FILE).read_text(encoding='utf-8')).get('ip')
        if not isinstance(value, list) or not value:
            return None
        return [str(ipaddress.ip_address(item)) for item in value]
    except (OSError, ValueError, TypeError, AttributeError):
        return None


# ---------- the live route table ----------

class LiveRoutes:
    """The route table an Application serves: the code table from routes.py plus the active rows of evlet_hosts.

    refresh() reads the version row at most once a second and, when it moved, rebuilds the table and swaps
    it into the application, its Business, Controls and LocalAuth (origins, Host check and realms)."""

    def __init__(self, application):
        self.application = application
        self.code = application.routes
        auth = application.business.auth
        self._origins, self._realms = frozenset(auth.origins), dict(auth.realms)
        # Sites ({name: (Frontend, folder of the current release or None)}) that a row's target may name, read
        # from evlet_sites with the hosts (evlet.sites); sites_folder holds their files (build_instance sets it).
        self.sites = {}
        # {model API name: (site, ...)}: the models the sites' current releases render collections from (evlet.sites.watching).
        self.site_watches = {}
        path = getattr(application.database, 'path', None)
        self.sites_folder = None if path is None else Path(path).parent / 'sites'
        # The project folder, for the addresses deploy/server.json saved (build_instance sets it).
        self.project = None
        self.problems = ()
        self._version, self._checked, self._lock, self._swapped = None, float('-inf'), Lock(), False

    @property
    def database(self):
        return self.application.database

    def due(self):
        return time.monotonic() - self._checked >= REFRESH_SECONDS

    def refresh(self, force=False):
        """Swap in a new table when the version row moved since the last look. Never raises: a database it
        can't read leaves the current table in place. Returns whether the table changed."""
        if not force and not self.due():
            return False
        self._checked = time.monotonic()
        with self._lock:
            try:
                path = getattr(self.database, 'path', None)
                if path is not None and not path.exists():
                    return False
                with self.database._connection() as connection:
                    # One statement each second; before the 0019_hosts step the table is missing, which is no error.
                    try:
                        version = connection.run(qb.select('evlet_hosts_version', 'version').where(id=1)).value()
                    except OperationalError:
                        return False
                    if version == self._version and not force:
                        return False
                    rows = connection.run(qb.select('evlet_hosts', 'host', 'target').where(status='active').order_by('host')).all()
                    from .sites import serving, watching
                    sites = serving(connection, self.sites_folder)
                    watches = watching(sites)
            except Exception:
                logger.exception('Could not read evlet_hosts; the current route table stays')
                return False
            self._version = version
            self.sites, self.site_watches = sites, watches
            self.swap([(row['host'], row['target']) for row in rows])
            return True

    def swap(self, entries):
        from .routing import authority, with_hosts
        if not entries and not self._swapped:
            # Nothing from data, as before: the code table and the auth stay exactly as they were built.
            self.problems = ()
            return
        self._swapped = bool(entries)
        table, problems = with_hosts(self.code, entries, self.sites)
        for host, reason in problems:
            logger.warning('Host %s from evlet_hosts is not served: %s', host, reason)
        self.problems = tuple(problems)
        application = self.application
        business = application.business
        origins = {('https://' + host) if table.primary else f'http://{host}:{table.port}' for host in table.data_hosts()}
        realms = dict(self._realms)
        for origin in origins:
            realm = table.realm(origin.split('//', 1)[1])
            if realm != 'desk':
                realms[authority(origin).encode()] = realm
        business.auth.reroute(self._origins | origins, realms)
        application.routes = business.routes = table
        if business.controls is not None:
            business.controls.routes = table

    def attention(self):
        """Needs attention items for rows the table skipped."""
        return [{'tone': 'amber', 'icon': 'globe', 'page': 'routing', 'title': f'{host} is not served', 'text': reason
                 + '. Fix or remove it with evlet host remove, or in Controls, Routing.'} for host, reason in self.problems]


# ---------- the API ----------

def _live(business):
    live = getattr(business, 'live_routes', None)
    if live is None:
        raise ValueError('Hosts from data need an application with sign-in')
    return live


def _public(row, live):
    item = dict(row)
    item['served'] = item['host'] in live.application.routes.data_hosts()
    item['problem'] = next((reason for host, reason in live.problems if host == item['host']), None)
    return item


def list_hosts(business):
    """Every evlet_hosts row, by host: host, target, status, records, checked_at, error, created_at, created_by,
    changed_at, whether it is served now and why not when the table skipped it."""
    live = _live(business)
    live.refresh(force=True)
    with business.database._connection() as connection:
        if not connection.table_exists('evlet_hosts'):
            return []
        rows = connection.run(qb.select('evlet_hosts').order_by('host')).all()
    return [_public(row, live) for row in rows]


def _row(connection, host):
    return connection.run(qb.select('evlet_hosts').where(host=host)).one()


def add_host(business, host, target, *, actor):
    """Add host, pending, serving target (a frontend id such as "site.www"). It is served once a DNS check
    passes: check_hosts now, or the evlet_host_check job within a minute. Returns the row."""
    from .routing import target_error
    live = _live(business)
    error = target_error(live.code, host, target, live.sites)
    if error:
        raise ValueError(error)
    now = int(time.time())
    with business.database.transaction() as tx:
        if _row(tx.connection, host) is not None:
            raise ValueError(f'{host} is already a host of this instance. Remove it first to change its target')
        tx.connection.run(qb.insert('evlet_hosts', host=host, target=target, status='pending', records='', created_at=now,
                                    created_by=actor, changed_at=now))
        _bump(tx.connection)
        business.controls.audit(tx.connection, actor, 'Hosts', host, f'Added, serving {target}', None,
                                {'target': target, 'status': 'pending'})
    schedule_checks(business)
    live.refresh(force=True)
    return next(item for item in list_hosts(business) if item['host'] == host)


def remove_host(business, host, *, actor):
    """Remove a host added from data; it stops being served within a second. A host in routes.py is not
    removed here (evlet host remove edits routes.py)."""
    live = _live(business)
    with business.database.transaction() as tx:
        row = _row(tx.connection, host)
        if row is None:
            raise ValueError(f'{host} is not a host added from data')
        tx.connection.run(qb.delete('evlet_hosts').where(host=host))
        _bump(tx.connection)
        business.controls.audit(tx.connection, actor, 'Hosts', host, 'Removed', {'target': row['target'], 'status': row['status']}, None)
    live.refresh(force=True)


def set_host_status(business, host, status, *, actor):
    """Disable a host (it stops being served and checked) or set it back to pending, so the next check can
    make it active again. Returns the row."""
    if status not in ('pending', 'disabled'):
        raise ValueError('Set a host to pending or disabled; a passing DNS check makes it active')
    live = _live(business)
    with business.database.transaction() as tx:
        row = _row(tx.connection, host)
        if row is None:
            raise ValueError(f'{host} is not a host added from data')
        if row['status'] != status:
            tx.connection.run(qb.update('evlet_hosts', status=status, changed_at=int(time.time())).where(host=host))
            _bump(tx.connection)
            business.controls.audit(tx.connection, actor, 'Hosts', host, 'Disabled' if status == 'disabled' else 'Set to pending',
                                    {'status': row['status']}, {'status': status})
    if status == 'pending':
        schedule_checks(business)
    live.refresh(force=True)
    return next(item for item in list_hosts(business) if item['host'] == host)


def check_hosts(business, hosts=None, *, actor, addresses=None, resolve=None, pending_only=False):
    """Run the DNS check on hosts (default: every host that is not disabled) and record it. A passing check
    makes a pending host active. A failing check keeps an active host active, with the error recorded, so a
    resolver hiccup never takes a site down; disable it to stop serving it. Returns the checked rows."""
    live = _live(business)
    addresses = addresses or saved_addresses(live.project) or server_addresses()
    resolve = resolve or bounded(socket.getaddrinfo)
    with business.database._connection() as connection:
        if not connection.table_exists('evlet_hosts'):
            return []
        rows = {row['host']: row for row in connection.run(qb.select('evlet_hosts').order_by('host')).all()}
    if hosts is not None:
        unknown = [host for host in hosts if host not in rows]
        if unknown:
            raise ValueError(f'{", ".join(unknown)} is not a host added from data')
        chosen = [rows[host] for host in hosts]
    else:
        chosen = [row for row in rows.values() if row['status'] != 'disabled' and (row['status'] == 'pending' or not pending_only)]
    checked = []
    for row in chosen:
        records, status, passed, warning = dns_status(row['host'], addresses, resolve)
        error = None if passed else f'DNS: {status}'
        with business.database.transaction() as tx:
            current = _row(tx.connection, row['host'])
            if current is None or current['status'] == 'disabled':
                continue
            becomes = 'active' if passed else current['status']
            tx.connection.run(qb.update('evlet_hosts', status=becomes, records=', '.join(records), checked_at=int(time.time()),
                                        error=error or warning, changed_at=int(time.time()) if becomes != current['status'] else current['changed_at'])
                              .where(host=row['host']))
            if becomes != current['status']:
                _bump(tx.connection)
                business.controls.audit(tx.connection, actor, 'Hosts', row['host'], f'Active: DNS {status}',
                                        {'status': current['status']}, {'status': becomes})
        checked.append(row['host'])
    live.refresh(force=True)
    return [item for item in list_hosts(business) if item['host'] in checked]


# ---------- the check job ----------

def check_job(business):
    """The evlet_host_check task: check every pending host. An external task, so each host commits on its own."""
    def run(context, payload):
        return {'checked': len(check_hosts(business, actor='system', pending_only=True))}
    return run


def register_task(business):
    """Register the check task in this process's queue, so the runner can run its schedule."""
    if business.jobs is None:
        return
    from .jobs import Task
    business.jobs.tasks.setdefault(CHECK_TASK, Task(CHECK_TASK, check_job(business), max_attempts=1, external=True))


def schedule_checks(business):
    """Schedule the check every minute, once the instance has a host from data (none is made before)."""
    if business.jobs is None:
        return
    register_task(business)
    with business.database.transaction() as tx:
        exists = tx.connection.run(qb.select('evlet_schedules', 'name').where(name=CHECK_TASK)).one()
    if not exists:
        business.jobs.schedule(CHECK_TASK, CHECK_TASK, {}, every=CHECK_EVERY)
