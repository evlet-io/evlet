"""Controls system pages: installed apps, routing, jobs, the attention list and the audit log."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as Late
import inspect
import json
import re
import socket
import ssl
from threading import Lock
import time

from .auth import AccessError, User
from .models import ValidationError

PAGE = 50
PROXY_LATER = 'Proxy config and host checks arrive with sub-hosts. Until then, use evlet routes on the server.'
LOOKUP_SECONDS = 5
CHECK_SECONDS = 10
DNS = ('points here', 'points elsewhere', 'no A or AAAA record')


def bounded(resolve):
    """A DNS lookup that gives up after LOOKUP_SECONDS, so a slow resolver can't hold a request."""
    def lookup(host, port, **options):
        pool = ThreadPoolExecutor(1)
        try:
            return pool.submit(resolve, host, port, **options).result(LOOKUP_SECONDS)
        except Late:
            raise OSError(f'{host} did not resolve within {LOOKUP_SECONDS} seconds') from None
        finally:
            pool.shutdown(wait=False)
    return lookup


def certificate(host, timeout=5):
    """Whether the host serves a valid certificate on 443, and until when."""
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw, \
                ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
            return {'certificate': 'valid', 'expires': int(ssl.cert_time_to_seconds(tls.getpeercert()['notAfter']))}
    except (OSError, ValueError, KeyError):
        return {'certificate': 'missing', 'expires': None}


class System:
    def __init__(self, controls):
        self.controls = controls
        self.path, self.started = None, {}
        self.checked, self.check_lock = {}, Lock()

    def load(self, path):
        """The instance folder (instances/<name>) and the apps.lock this process started with."""
        if path is None:
            return
        from .instances import read_lock
        self.path, self.started = path, read_lock(path)['apps']

    def instance_name(self):
        return None if self.path is None else self.path.name

    def write_audit(self, actor, area, target, detail, before=None, after=None):
        with self.controls.database.transaction() as tx:
            self.controls.audit(tx.connection, actor, area, target, detail, before, after)

    # ---------- apps ----------

    def updates(self):
        """Per app: a newer source waiting to be migrated, and whether the server still runs an older one."""
        if self.path is None:
            return {}
        from .instances import read_lock
        from .packages import portable_lock_entry, read_package
        try:
            locked = read_lock(self.path)['apps']
        except ValueError:
            return {}
        result = {}
        for name, entry in locked.items():
            entry = portable_lock_entry(name, entry)
            try:
                package = read_package(self.path.parent.parent / 'apps' / name)
                update = None if package.locked() == entry else {'from': entry['version'], 'to': package.version, 'error': None}
            except (OSError, ValueError) as exc:
                update = {'from': entry['version'], 'to': None, 'error': str(exc)}
            started = self.started.get(name)
            result[name] = {'update': update, 'restart': started is not None and portable_lock_entry(name, started) != entry}
        return result

    def apps(self):
        rows = self.controls.routes.rows() if self.controls.routes else ()
        updates = self.updates()
        result = []
        for app in self.controls.apps:
            source = self.started.get(app['id'], {}).get('source') or f"apps/{app['id']}"
            frontends = [{'name': row.serves, 'path': row.path, 'host': row.host, 'realm': row.realm}
                         for row in rows if row.serves.startswith(app['id'] + '.')]
            result.append({key: app[key] for key in ('id', 'title', 'version', 'models', 'connectors', 'permissions', 'schedules')}
                          | {'source': source, 'frontends': frontends} | updates.get(app['id'], {'update': None, 'restart': False}))
        return result

    # ---------- routing ----------

    @staticmethod
    def proxy():
        """evlet proxy, when this release has it (it arrives with sub-hosts)."""
        try:
            from .cli import proxy
        except ImportError:
            return None
        return proxy

    def routing(self):
        from .routing import PLANNED, RESERVED
        routes, proxy = self.controls.routes, self.proxy()
        subs = routes.sub_hosts()
        result = {
            'rows': [{'host': row.host, 'path': row.path, 'serves': row.serves, 'realm': row.realm, 'source': row.source, 'locked': row.locked}
                     for row in routes.rows()],
            'planned': ['/' + name for name, status in RESERVED.items() if status == PLANNED],
            'hosts': [{'host': host, 'primary': host == routes.primary, 'sub': host in subs, 'realm': routes.realm(host)} for host in routes.hosts()],
            'primary': routes.primary, 'proxy': None, 'proxy_error': None if proxy else PROXY_LATER,
        }
        if proxy is not None:
            name = self.instance_name() or '<name>'
            try:
                result['proxy'] = {'caddy': proxy.caddy(routes, routes.port, name), 'nginx': proxy.nginx(routes, routes.port, name)}
            except ValueError as exc:
                result['proxy_error'] = str(exc)
        return result

    def check_hosts(self, actor):
        """The evlet proxy --check DNS report per host, and the certificate of each host that points here."""
        proxy, routes = self.proxy(), self.controls.routes
        if proxy is None:
            raise AccessError(409, PROXY_LATER)
        with self.check_lock:
            now = time.monotonic()
            if now - self.checked.get(actor.id, -CHECK_SECONDS) < CHECK_SECONDS:
                raise AccessError(429, 'Hosts were just checked. Try again in a few seconds.')
            self.checked[actor.id] = now
        options = {'resolve': bounded(socket.getaddrinfo)} if 'resolve' in inspect.signature(proxy.check).parameters else {}
        try:
            report, ok = proxy.check(routes, **options)
        except ValueError as exc:
            raise AccessError(409, str(exc)) from None
        lines, hosts = report.splitlines(), []
        for host in routes.hosts():
            line = next((item for item in lines if item.startswith(host + ' ')), '')
            status = next((item for item in DNS if line.endswith(item)), None)
            records = line[len(host):len(line) - len(status)].strip() if status else ''
            found = {'host': host, 'records': '' if records == '-' else records, 'dns': status}
            hosts.append(found | (certificate(host) if status == DNS[0] else {'certificate': None, 'expires': None}))
        notes = [item for item in lines if not any(item.startswith(host + ' ') for host in routes.hosts())]
        return {'hosts': hosts, 'notes': notes, 'ok': ok}

    # ---------- jobs ----------

    def queue(self):
        jobs = self.controls.business.jobs
        if jobs is None:
            raise AccessError(404, 'This instance has no background jobs.')
        return jobs

    def schedule_names(self):
        if self.controls.business.jobs is None:
            return []
        with self.controls.database._connection() as connection:
            if not self.controls.database.columns(connection, 'evlet_schedules'):
                return []
            return [row[0] for row in connection.execute('SELECT name FROM evlet_schedules ORDER BY name')]

    def apply_schedule(self, name, active):
        """Pause or resume a schedule after its override is saved. A schedule that is gone has nothing to apply."""
        try:
            self.queue().set_schedule_active(name, active)
        except (AccessError, ValueError):
            pass

    def failed_jobs(self):
        if self.controls.business.jobs is None:
            return 0
        with self.controls.database._connection() as connection:
            return connection.execute("SELECT count(*) FROM evlet_jobs WHERE state='failed'").fetchone()[0]

    def jobs(self):
        jobs = self.controls.business.jobs
        if jobs is None:
            return {'available': False, 'schedules': [], 'jobs': []}
        owner = {name: app['id'] for app in self.controls.apps for name in app['schedules']}
        with self.controls.database._connection() as connection:
            rows = connection.execute('SELECT s.name, s.task, s.interval, s.next_run, s.enabled, c.expression, c.timezone FROM evlet_schedules s '
                                      'LEFT JOIN evlet_calendar_schedules c ON c.name = s.name WHERE c.retired IS NULL OR c.retired = 0 ORDER BY s.name').fetchall()
        settings = self.controls.settings
        schedules = [{'name': row['name'], 'task': row['task'], 'every': None if row['expression'] else row['interval'],
                      'expression': row['expression'], 'timezone': row['timezone'], 'next_run': row['next_run'], 'active': bool(row['enabled']),
                      'paused': settings.get(f"schedule.{row['name']}.active") is False, 'app': owner.get(row['name'])} for row in rows]
        labels = {self.controls.registry.name_for(model): name for name, model in self.controls.models().items()}
        recent = [{**row, 'model': labels.get(row['model'], row['model'])} for row in jobs.recent()[:PAGE]]
        return {'available': True, 'schedules': schedules, 'jobs': recent}

    def set_schedule(self, actor, name, body):
        """Pause or resume: saved at once as the setting override schedule.<name>.active."""
        if not isinstance(body, dict) or set(body) != {'active'} or type(body['active']) is not bool:
            raise ValidationError({'active': 'Use true or false'})
        if name not in self.schedule_names():
            raise AccessError(404, 'Schedule not found')
        self.controls.save(actor, {'changes': [{'kind': 'setting', 'key': f'schedule.{name}.active', 'value': None if body['active'] else False}]})
        return self.jobs()

    def run_schedule(self, actor, name):
        """Run now: one job for the schedule's task, as the schedule would enqueue it."""
        jobs = self.queue()
        with self.controls.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_schedules WHERE name=?', (name,)).fetchone()
            if row is None:
                raise AccessError(404, 'Schedule not found')
            if not row['enabled']:
                raise AccessError(409, f'{name} is paused. Resume it first.')
            model = self.controls.registry.models.get(row['model']) if row['model'] else None
            try:
                receipt = jobs.enqueue(tx, row['task'], json.loads(row['payload']), user=None if row['actor'] is None else User(row['actor'], '', ''),
                                       target=None if model is None else (model, row['record_id']))
            except ValueError as exc:
                raise AccessError(409, str(exc)) from None
            self.controls.audit(tx.connection, actor.username, 'Jobs', name, f'Run now · job #{receipt.id}')
        return {'job': receipt.id, **self.jobs()}

    def retry(self, actor, job_id):
        """Retry a failed job. It keeps its idempotency key."""
        try:
            self.queue().retry(job_id)
        except ValueError:
            raise AccessError(409, 'Only failed jobs can be retried.') from None
        self.write_audit(actor.username, 'Jobs', f'Job #{job_id}', 'Retried')
        return self.jobs()

    # ---------- attention ----------

    def extension_problems(self):
        """Screen extensions that fail on this instance's form layouts, such as one that targets a removed section."""
        business = self.controls.business
        ui = getattr(business, 'ui', None)
        if ui is None or not ui.extensions or not self.controls.custom.layouts:
            return []
        user = User(0, 'schema', 'admin')
        try:
            ui.compile(business.schema(user), user)
        except ValueError as exc:
            return [str(exc)]
        return []

    def attention(self, apps, failed):
        from .controls import SCHEDULE
        items = []
        add = lambda icon, page, title, text: items.append({'tone': 'amber', 'icon': icon, 'page': page, 'title': title, 'text': text})
        for app in apps:
            if app['update'] and app['update']['error']:
                add('package', 'apps', f"The {app['title']} app can't be read", app['update']['error'])
            elif app['update']:
                add('package', 'apps', f"{app['title']} {app['update']['to']} is waiting to be migrated",
                    f'Stop the server, then run evlet migrate {self.instance_name()} on the server. It takes a backup first.')
            elif app['restart']:
                add('package', 'apps', f"Restart the server to load the new {app['title']}",
                    f"The database is migrated. The server still runs {app['version']}, the version it started with.")
        for problem in self.controls.problems:
            kind, key, error = problem['kind'], problem['key'], problem['error']
            schedule = SCHEDULE.fullmatch(key) if kind == 'setting' else None
            if kind == 'perm' and key == 'all':
                add('lock', 'permissions', 'Every role but admin is denied', error)
            elif kind == 'perm':
                add('lock', 'permissions', f"No access for {key.replace('|', ' on ')} until this is fixed", error)
            elif kind == 'field':
                add('lock', 'models', f'{key} is read only for everyone until this is fixed', error)
            elif schedule:
                add('clock', 'overrides', f'The pause of {schedule[1]} no longer applies', error)
            elif kind == 'setting':
                add('lock', 'security', f'{key} uses its strictest value until this is fixed', error)
            elif kind == 'naming' and error.startswith('Series '):
                add('hash', 'naming', f'A {key} naming series is skipped', error)
            else:
                add('layers', 'naming' if kind == 'naming' else 'models', f'The {key} {kind} override no longer applies. The app file is used.', error)
        for error in self.extension_problems():
            add('layers', 'models', 'A screen extension no longer fits the form layout', error)
        if failed:
            add('clock', 'jobs', f"{failed} job{'s' if failed != 1 else ''} failed", 'Retry them from Jobs once the cause is fixed.')
        return items

    # ---------- audit ----------

    def audit(self, query):
        """A page of the audit log, newest first: area, a search over who, what and the detail, and ids before a cursor."""
        area, text, before = query.get('area', ''), query.get('q', '').strip().lower(), query.get('before')
        if len(area) > 40:
            raise ValidationError({'area': 'Choose an area from the list.'})
        if len(text) > 100:
            raise ValidationError({'q': 'Search with at most 100 characters.'})
        clauses, values = [], []
        if area:
            clauses.append('area = ?')
            values.append(area)
        if text:
            like = '%' + re.sub(r'([\\%_])', r'\\\1', text) + '%'
            clauses.append("(lower(actor) LIKE ? ESCAPE '\\' OR lower(target) LIKE ? ESCAPE '\\' OR lower(detail) LIKE ? ESCAPE '\\')")
            values += [like] * 3
        if before is not None:
            if not re.fullmatch(r'[1-9][0-9]{0,18}', before) or int(before) > 2**63 - 1:
                raise ValidationError({'before': 'Use the next value from the previous page.'})
            clauses.append('id < ?')
            values.append(int(before))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.controls.database._connection() as connection:
            rows = [dict(row) for row in connection.execute(f'SELECT id, at, actor, area, target, detail FROM evlet_audit{where} ORDER BY id DESC LIMIT ?',
                                                            (*values, PAGE + 1))]
            areas = [row[0] for row in connection.execute('SELECT DISTINCT area FROM evlet_audit ORDER BY area')]
        return {'items': rows[:PAGE], 'next': rows[PAGE - 1]['id'] if len(rows) > PAGE else None, 'areas': areas}
