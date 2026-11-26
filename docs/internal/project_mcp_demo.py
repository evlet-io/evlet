"""Record a session of an MCP client against a real project endpoint (docs/internal/project-mcp-demo.md).

    .venv/bin/python docs/internal/project_mcp_demo.py [OUTPUT.md]

It makes a project in a temporary folder, runs `evlet endpoint` as a real process (which runs the instance as a real
child process), connects as an MCP client with OAuth (dynamic registration, PKCE, the owner's consent) and speaks
JSON-RPC over HTTP to /_/mcp. Every MCP request and response is written to the transcript, trimmed.

Development: create an app, write a model, create an instance, install the app, make and run a migration, run the
tests. Then, on the shell, `evlet project mode production` and a restart of the endpoint; the client connects again and
sees the develop tools gone (tools/list, list_services, and a 403 call), and app_update (approved by the owner through
the Tool Engine's approval route) and instance_restart work.

Shortcuts, shell side only: the project's .venv reads this checkout's packages through a .pth file instead of a fresh install, the
"public host" is project.example.com reached on 127.0.0.1 with the headers Caddy would add, and the app's Git origin
is a bare repository served over plain HTTP on 127.0.0.1.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[2]
PASSWORD = 'a long owner password for the demo'
CALLBACK = 'https://claude.ai/api/mcp/auth_callback'
HOST = 'project.example.com'
LIMIT = 1600
ENV = {**os.environ, 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost', 'PYTHONPATH': str(REPO)}

transcript = []


def note(text):
    print(text, flush=True)
    transcript.append(text)


def block(kind, value):
    text = value if isinstance(value, str) else json.dumps(value, indent=1, ensure_ascii=False)
    if len(text) > LIMIT:
        text = text[:LIMIT] + f'\n... [{len(text) - LIMIT} more characters trimmed]'
    transcript.append(f'```{kind}\n{text}\n```')


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def shell(root, *arguments, check=True):
    """An evlet command on the server's shell, recorded."""
    note(f'**Shell:** `evlet {" ".join(arguments)}`')
    done = subprocess.run([str(root / '.venv/bin/python'), '-m', 'evlet', '--project', str(root), *arguments], capture_output=True, text=True,
                          env=ENV, stdin=subprocess.DEVNULL, timeout=600)
    block('text', (done.stdout + done.stderr).strip() or '(no output)')
    if check and done.returncode:
        raise SystemExit(f'evlet {" ".join(arguments)} failed')
    return done


def git(path, *arguments):
    return subprocess.run(['git', '-c', 'user.name=Demo', '-c', 'user.email=demo@example.com', '-c', 'init.defaultBranch=main',
                           '-C', str(path), *arguments], check=True, capture_output=True, text=True).stdout.strip()


class Http:
    """Requests to the endpoint on 127.0.0.1; in production with the Host and forwarding headers Caddy sends."""

    def __init__(self, port):
        self.port, self.cookie, self.csrf, self.public = port, '', '', False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, method, path, body=None, *, form=None, token=None, extra=None):
        headers = {'Accept': 'application/json, text/event-stream'}
        data = None
        if form is not None:
            data, headers['Content-Type'] = urllib.parse.urlencode(form).encode(), 'application/x-www-form-urlencoded'
        elif body is not None:
            data, headers['Content-Type'] = json.dumps(body).encode(), 'application/json'
        if token:
            headers['Authorization'] = 'Bearer ' + token
        else:
            headers['X-Evlet-Request'] = '1'
            if self.cookie:
                headers.update({'Cookie': self.cookie, 'X-CSRF-Token': self.csrf})
        if self.public:
            headers.update({'Host': HOST, 'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '127.0.0.1'})
        headers.update(extra or {})
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=60) as response:
                status, raw, received = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, raw, received = error.code, error.read(), error.headers
        cookie = received.get('Set-Cookie')
        if cookie and 'evlet_session=' in cookie:
            self.cookie = cookie.split(';')[0]
        try:
            return status, json.loads(raw or b'null')
        except ValueError:
            return status, raw.decode('utf-8', 'replace')

    def sign_in(self, totp_secret=None):
        status, data = self('POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
        if status == 200 and data.get('two_step') == 'code':
            from evlet.settings import totp
            status, data = self('POST', '/api/auth/login', {'ticket': data['ticket'], 'code': code(totp_secret)})
        assert status == 200 and 'csrf' in data, data
        self.csrf = data['csrf']


def code(secret, wait_for_new=False):
    from evlet.settings import totp
    raw = base64.b32decode(secret + '=' * (-len(secret) % 8))
    window = int(time.time() // 30)
    if wait_for_new:
        time.sleep((window + 1) * 30 - time.time() + 1)
        window += 1
    return totp(raw, window)


class Client:
    """An MCP client: OAuth with PKCE, then JSON-RPC over HTTP. Every call is recorded."""

    def __init__(self, http, origin):
        self.http, self.origin, self.token, self.id = http, origin, None, 0

    def connect(self, owner):
        status, registered = self.http('POST', '/_/oauth/register', {'client_name': 'Demo MCP client', 'redirect_uris': [CALLBACK]})
        assert status == 201, registered
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        query = urllib.parse.urlencode({'response_type': 'code', 'client_id': registered['client_id'], 'redirect_uri': CALLBACK,
                                        'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': 'demo',
                                        'resource': self.origin + '/_/mcp'})
        status, info = owner('GET', '/api/_oauth/authorize?' + query)
        assert status == 200, info
        status, approved = owner('POST', '/api/_oauth/authorize', {'query': query, 'approve': True, 'allow_changes': True})
        assert status == 200, approved
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(approved['redirect']).query))
        status, tokens = self.http('POST', '/_/oauth/token', form={'grant_type': 'authorization_code', 'code': params['code'],
                                                                   'redirect_uri': CALLBACK, 'client_id': registered['client_id'],
                                                                   'code_verifier': verifier})
        assert status == 200, tokens
        self.token = tokens['access_token']
        note(f'The client registered, the owner approved it on the consent page, and the code was exchanged: a Bearer '
             f'token for {self.origin}/_/mcp.')
        self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'demo', 'version': '1'}})
        self.rpc('notifications/initialized', notify=True)

    def rpc(self, method, params=None, *, notify=False, show=True):
        message = {'jsonrpc': '2.0', 'method': method, **({} if notify else {'id': self.next()}), **({'params': params} if params is not None else {})}
        status, answer = self.http('POST', '/_/mcp', message, token=self.token)
        if show:
            block('json', {'request': message})
            block('json', {'status': status, 'response': answer})
        return answer

    def next(self):
        self.id += 1
        return self.id

    def call(self, service, show=True, **arguments):
        answer = self.rpc('tools/call', {'name': 'call_service', 'arguments': {'service': service, 'arguments': arguments}}, show=show)
        result = answer['result']
        return json.loads(result['content'][0]['text']), result.get('isError', False)

    def follow(self, started, seconds=600):
        """Poll operation_status until the operation ends; the last answer is recorded."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            found, _ = self.call('operation_status', show=False, operation=started['operation'])
            if found['result'] not in ('running', 'started'):
                self.call('operation_status', operation=started['operation'])
                return found
            time.sleep(1)
        raise SystemExit(f'Operation {started} did not end')

    def run(self, service, **arguments):
        """Call a tool; follow its operation if it started one."""
        result, error = self.call(service, **arguments)
        if error:
            raise SystemExit(f'{service} failed: {result}')
        if result.get('result') == 'started':
            result = self.follow(result)
        assert result.get('result', 'ok') == 'ok', result
        return result


def wait(check, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.5)
    raise SystemExit('Timed out')


def start_endpoint(root, port):
    process = subprocess.Popen([str(root / '.venv/bin/python'), '-m', 'evlet', '--project', str(root), 'endpoint', '--no-link'], env=ENV,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    wait(lambda: Http(port)('GET', '/health', extra={'Host': '127.0.0.1'})[0] == 200 or Http(port)('GET', '/health', extra={'Host': HOST})[0] == 200)
    return process


def stop_endpoint(process):
    process.send_signal(signal.SIGINT)
    try:
        process.wait(60)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)


BODY = "    title = Text(required=True, max_length=160)\n"


def with_field(source, line):
    assert BODY in source, source
    return source.replace(BODY, BODY + line)


TEST = '''from evlet.testing import TestApp
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def test_a_note_keeps_its_body(tmp_path):
    with TestApp(APP, directory=tmp_path) as app:
        editor = app.client(role='editor')
        created = editor.post('/api/notesitem', {'title': 'Groceries', 'body': 'Milk, eggs', 'tag': 'home'})
        assert created.status == 201, created.data
        assert editor.get('/api/notesitem/' + created.data['id']).data['body'] == 'Milk, eggs'
'''


def environment(target, framework):
    """The project's own .venv, which sees this checkout's packages (Evlet and its locked dependencies) through a .pth
    file instead of installing them again; what the project installs (its apps) goes into it, not into the checkout."""
    import site
    import venv
    venv.EnvBuilder(with_pip=True).create(target / '.venv')
    packages = next((target / '.venv' / 'lib').glob('python3*/site-packages'))
    (packages / 'evlet-checkout.pth').write_text(f'import site; site.addsitedir({site.getsitepackages()[0]!r})\n')


def main(output):
    from evlet.cli import endpoint
    from evlet.cli.setup import setup
    import evlet.cli.setup as setup_module
    work = Path(tempfile.mkdtemp(prefix='evlet-demo-'))
    root = work / 'demo'
    setup_module.create_environment = environment
    setup(root)
    port, instance_port, git_port = free_port(), free_port(), free_port()
    note('# Project MCP: a recorded session\n')
    note(f'Recorded {time.strftime("%Y-%m-%d")} by `docs/internal/project_mcp_demo.py` against a real `evlet endpoint` '
         'process and its instance child process. Requests and responses are trimmed to 1,600 characters each.\n')
    note('## Development\n')
    endpoint.create(root)  # what `evlet setup --endpoint` does before it serves the endpoint
    endpoint.set_owner_password(root, PASSWORD)
    note('**Shell:** `evlet setup --endpoint` made `.evlet/project.sqlite3` with the owner account (it then serves the '
         'endpoint; here `evlet endpoint --no-link` serves it below).\n')
    settings = endpoint.read_settings(root)
    never = ['app_create', 'file_write', 'instance_create', 'app_install', 'instance_start', 'makemigration', 'migrate',
             'tests_run', 'instance_restart', 'app_update']
    endpoint.save_settings(root, {**settings, 'port': port, 'instances': {'acme': instance_port}, 'never_ask': never})
    note(f'On the server, the owner set a password and listed tools that need no approval in `.evlet/endpoint.json` '
         f'(`never_ask`: {", ".join(never)}). app_update is listed too: production ignores that for it.\n')
    process = start_endpoint(root, port)
    try:
        owner = Http(port)
        owner.sign_in()
        client = Client(Http(port), f'http://127.0.0.1:{port}')
        client.connect(owner)
        note('### The tools\n')
        client.rpc('tools/list')
        listed = client.rpc('tools/call', {'name': 'list_services', 'arguments': {}})
        names = [item['name'] for item in listed['result']['structuredContent']['services']]
        note(f'list_services offers {len(names)} project tools in development: {", ".join(names)}.\n')
        note('### Create an app and write a model\n')
        client.run('app_create', name='notes')
        client.run('files_list', path='apps/notes')
        source = client.run('file_read', path='apps/notes/models/item.py')['text']
        model = with_field(source, "    body = Text(max_length=4000, default='')\n")
        client.run('file_write', path='apps/notes/models/item.py', text=model)
        note('### An instance with the app\n')
        client.run('instance_create', name='acme')
        client.run('app_install', instance='acme', source='notes')
        client.run('instance_start', instance='acme')
        note('### Change the model, make and run the migration\n')
        client.run('file_write', path='apps/notes/models/item.py', text=with_field(model, "    tag = Text(max_length=40, default='')\n"))
        client.run('makemigration', app='notes', instance='acme', name='tag')
        client.run('migrate', instance='acme')
        note('### Run the tests\n')
        client.run('file_write', path='apps/notes/tests/test_body.py', text=TEST)
        client.run('tests_run', target='notes')
        # Two-step sign-in for the owner, which production requires; set up in Settings.
        status, started = owner('POST', '/api/_settings/me/two-step', {'password': PASSWORD})
        assert status == 200, started
        status, confirmed = owner('POST', '/api/_settings/me/two-step/confirm', {'code': code(started['secret'])})
        assert status == 200, confirmed
        note('The owner set up two-step sign-in in Settings (production refuses without it).\n')
    finally:
        stop_endpoint(process)

    note('## Release v1 and v2 of the app from Git (the developer, outside Evlet)\n')
    app = root / 'apps/notes'
    git(app, 'init', '--quiet')
    git(app, 'add', '-A')
    git(app, 'commit', '--quiet', '-m', 'Notes v1')
    git(app, 'tag', 'v1')
    served = work / 'git'
    served.mkdir()
    subprocess.run(['git', 'clone', '--quiet', '--bare', str(app), str(served / 'notes.git')], check=True)
    git(served / 'notes.git', 'update-server-info')
    origin = f'http://127.0.0.1:{git_port}/notes.git'
    git(app, 'remote', 'add', 'origin', origin)
    clone = work / 'clone'
    subprocess.run(['git', 'clone', '--quiet', str(served / 'notes.git'), str(clone)], check=True)
    readme = clone / 'README.md'
    readme.write_text(readme.read_text() + '\nVersion two: notes have tags.\n')
    git(clone, 'commit', '--quiet', '-am', 'Notes v2')
    git(clone, 'tag', 'v2')
    git(clone, 'push', '--quiet', 'origin', 'HEAD:main', 'v2')
    git(served / 'notes.git', 'update-server-info')
    files = subprocess.Popen([sys.executable, '-m', 'http.server', str(git_port), '--bind', '127.0.0.1', '--directory', str(served)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    note(f'apps/notes is a Git checkout at v1 whose origin ({origin}) has a tag v2. apps.lock records the commit once '
         'the instance is migrated, as production checks it:\n')
    shell(root, 'migrate', 'acme', '--trust')

    note('## Production\n')
    try:
        endpoint.create(root, host=HOST)
        note(f'On the server: the endpoint gets its public host {HOST} (`evlet setup --endpoint --endpoint-host {HOST}`; '
             'the Caddy and systemd steps are left out here).\n')
        shell(root, 'project', 'mode', 'production')
        process = start_endpoint(root, port)
        try:
            owner = Http(port)
            owner.public = True
            secret = started['secret']
            time.sleep(max(0, (int(time.time() // 30) + 1) * 30 - time.time() + 1))  # a new code, not the one used above
            owner.sign_in(secret)
            client = Client(Http(port), f'https://{HOST}')
            client.http.public = True
            client.connect(owner)
            note('### The develop tools are gone\n')
            client.rpc('tools/list')
            listed = client.rpc('tools/call', {'name': 'list_services', 'arguments': {}})
            names = [item['name'] for item in listed['result']['structuredContent']['services']]
            note(f'list_services offers {len(names)} tools in production: {", ".join(names)}.\n')
            refused, error = client.call('file_write', path='apps/notes/models/item.py', text='')
            assert error and 'Not available in production' in json.dumps(refused), refused
            status, data = owner('POST', '/api/_services/migrate', {'instance': 'acme'})
            note(f'The portal route refuses too: POST /api/_services/migrate answers {status} {json.dumps(data)}.\n')
            assert status == 403
            note('### app_update asks, and the owner approves it\n')
            wait(lambda: client.call('project_status', show=False)[0]['instances'][0]['health'], seconds=120)
            waiting, error = client.call('app_update', instance='acme', app='notes', ref='v2')
            assert error, waiting
            status, approvals = owner('GET', '/api/_tools/approvals')
            pending = [item for item in approvals['items'] if item['status'] == 'pending']
            note('The owner reads the waiting approval (GET /api/_tools/approvals) and approves it '
                 f'(POST /api/_tools/approvals/{pending[0]["id"]}):')
            block('json', pending[0])
            status, decided = owner('POST', f'/api/_tools/approvals/{pending[0]["id"]}', {'approve': True})
            assert status == 200, decided
            client.run('app_update', instance='acme', app='notes', ref='v2')
            note('### instance_restart\n')
            client.run('instance_restart', instance='acme')
            client.call('operation_log', limit=12)
        finally:
            stop_endpoint(process)
    finally:
        files.terminate()
    Path(output).write_text('\n\n'.join(transcript) + '\n', encoding='utf-8')
    print(f'Transcript: {output}')
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name('project-mcp-demo.md')))
