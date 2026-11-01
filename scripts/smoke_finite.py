"""Installed-wheel smoke: a finite-v1 nested document over real HTTP, no Node on PATH."""
import http.client, json, re, shutil, socket, sys, tempfile, threading, time
from pathlib import Path
import evlet, uvicorn
from evlet import Application, Database, LocalAuth, Registry, Model, Child, Table, Decimal, Currency, Rule
from evlet.calc import round, parent

node = shutil.which('node')  # Report it: an installed wheel must not need Node.
assert 'site-packages' in evlet.__file__, evlet.__file__

class Task(Child):
    hours = Decimal(default=0)
    amount = Currency(compute=round(hours * parent.hourly_rate, 2))

class Stage(Child):
    hourly_rate = Currency(compute=parent.hourly_rate, stored=False)
    tasks = Table(Task)
    total = Currency(compute=tasks.sum('amount'))

class Project(Model):
    class Meta:
        calculation_language = 'finite-v1'
        numeric_profile = 'finite40-hu18-v1'
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    hourly_rate = Currency(default=100)
    stages = Table(Stage)
    total = Currency(compute=stages.sum('total'))

sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
origin = f'http://127.0.0.1:{port}'
db = Database(Path(tempfile.mkdtemp()) / 'db.sqlite3', Registry(Project))
app = Application(db, auth=LocalAuth(db, origins=(origin,), secure_cookies=False, allow_setup=True))
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
threading.Thread(target=server.run, daemon=True).start()
while not server.started: time.sleep(0.05)
state = {'cookie': '', 'csrf': ''}

def call(method, path, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port)
    hdrs = {'host': f'127.0.0.1:{port}', 'origin': origin, 'content-type': 'application/json', 'x-evlet-request': '1', 'sec-fetch-site': 'same-origin'}
    if state['cookie']: hdrs['cookie'] = state['cookie']
    if state['csrf']: hdrs['x-csrf-token'] = state['csrf']
    hdrs.update(headers or {})
    connection.request(method, path, None if body is None else json.dumps(body), hdrs)
    response = connection.getresponse()
    data = response.read()
    return response.status, dict(response.getheaders()), data

status, _, data = call('POST', '/api/auth/setup', {'username': 'admin', 'password': 'correct horse battery staple'})
assert status == 201, data
status, headers, data = call('POST', '/api/auth/login', {'username': 'admin', 'password': 'correct horse battery staple'})
assert status == 200, data
state['cookie'] = headers['set-cookie'].split(';')[0]; state['csrf'] = json.loads(data)['csrf']
schema = json.loads(call('GET', '/api/schema')[2])
plan_hash = next(m for m in schema['models'] if m['name'] == 'project')['calculation']['model_schema_hash']
body = {'_calculation': {'model_schema_hash': plan_hash}, 'children': {'stages': [
    {'_key': 'A', 'children': {'tasks': [{'_key': 'a1', 'hours': '2'}, {'_key': 'a2', 'hours': '3'}]}},
    {'_key': 'B', 'children': {'tasks': [{'_key': 'b1', 'hours': '4'}]}}]}}
status, headers, data = call('POST', '/api/project', body)
assert status == 201, data
created = json.loads(data)
assert created['total'] == '900.00', created
status, headers, data = call('GET', f"/api/project/{created['id']}")
loaded = json.loads(data)
status, _, data = call('PATCH', f"/api/project/{created['id']}", {'hourly_rate': '120'}, {'if-match': headers['etag']})
assert status == 200 and json.loads(data)['total'] == '1080.00', data
status, _, page = call('GET', '/app/project')
assert status == 200, status
assets = re.findall(r'(?:src|href)="(/[^"]+\.(?:js|css))"', page.decode())
assert assets and all(call('GET', asset)[0] == 200 for asset in assets), assets
assert not re.search(r'https?://(?!127\.0\.0\.1)', page.decode()), 'external URL in shell page'
server.should_exit = True
print('finite wheel smoke passed:', evlet.__file__, 'assets', len(assets), 'node on PATH:', bool(node))
