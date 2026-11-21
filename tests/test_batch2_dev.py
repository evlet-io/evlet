"""evlet dev keeps serving after a failed start, and app errors name the author's own files (DX-04, DX-05, DX-10, DX-12,
DX-18, DX-24, DX-25, SECH-09, SECH-15). Real projects, real SQLite."""

import asyncio
import json
import stat
import sys
import tarfile

import pytest

from evlet import Model, State, Text
from evlet.builds import prepare, unfinished_migrations
from evlet.cli.app import create_app
from evlet.cli.backup import backup
from evlet.cli.install import install, makemigration
from evlet.cli.instance import create_instance
from evlet.cli.main import check_framework_source, main
from evlet.cli.project import instance_guard
from evlet.cli.serve import LOOPBACK, ManagedRuntime, data_state, plain_http_warning
from evlet.cli.setup import setup
from evlet.instances import load_runtime
from evlet.migrations import UNFINISHED, field_code
from evlet.packages import read_package, resolve_packages


@pytest.fixture(autouse=True)
def environment_stub(monkeypatch):
    import importlib
    module = importlib.import_module('evlet.cli.setup')
    monkeypatch.setattr(module, 'create_environment', lambda root, framework: (root / '.venv').mkdir())


@pytest.fixture
def project(tmp_path):
    root = setup(tmp_path / 'project').resolve()
    create_app(root, 'notes')
    create_instance(root, 'acme')
    install(root, 'acme', app_name='notes', trust=True)
    yield root
    for name in list(sys.modules):
        if name == 'notes' or name.startswith('notes.'):
            del sys.modules[name]


def add_field(root):
    model = root / 'apps/notes/models/item.py'
    model.write_text(model.read_text().replace("    title = Text(required=True, max_length=160)\n",
                                               "    title = Text(required=True, max_length=160)\n    phone = Text(default='')\n"))


async def lifespan(runtime):
    """Start the runtime through ASGI lifespan; returns (startup message, stop coroutine)."""
    inbox, outbox = asyncio.Queue(), asyncio.Queue()
    await inbox.put({'type': 'lifespan.startup'})
    task = asyncio.create_task(runtime({'type': 'lifespan'}, inbox.get, outbox.put))
    started = await outbox.get()

    async def stop():
        await inbox.put({'type': 'lifespan.shutdown'})
        message = await outbox.get()
        await task
        return message
    return started, stop


async def get(runtime, path, accept='text/html'):
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        sent.append(message)
    scope = {'type': 'http', 'method': 'GET', 'path': path, 'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
             'scheme': 'http', 'http_version': '1.1', 'server': ('127.0.0.1', 8000), 'client': ('127.0.0.1', 5000),
             'headers': [(b'host', b'127.0.0.1:8000'), (b'accept', accept.encode())]}
    await runtime(scope, receive, send)
    start = next(message for message in sent if message['type'] == 'http.response.start')
    body = b''.join(message.get('body', b'') for message in sent if message['type'] == 'http.response.body')
    return start['status'], body.decode()


def test_dev_start_failure_serves_an_error_page_releases_the_lock_and_recovers_after_migrate(project, capsys):
    from evlet.devlink import DevSignIn
    path = project / 'instances/acme'
    add_field(project)
    managed = ManagedRuntime(lambda: load_runtime(project, 'acme', development=True), lambda: instance_guard(path, serving=True),
                             development=True, retry_key=lambda: data_state(path))
    runtime = DevSignIn(managed, announce=True)

    async def scenario():
        started, stop = await lifespan(runtime)
        assert started['type'] == 'lifespan.startup.complete'  # the port answers instead of hanging
        status, page = await get(runtime, '/')
        assert status == 503
        assert 'phone: field is in the models but not in the database' in page
        assert 'evlet makemigration APP --instance INSTANCE' in page and 'http-equiv="refresh"' in page
        status, body = await get(runtime, '/api/session', accept='application/json')
        assert status == 503 and 'Model definitions changed' in json.loads(body)['error']
        # The failed start holds no lock, so evlet migrate can run beside evlet dev.
        await asyncio.to_thread(makemigration, project, 'notes', 'acme', name='phone', trust=True)
        await asyncio.to_thread(install, project, 'acme', trust=True)
        status, _ = await get(runtime, '/health', accept='application/json')
        assert status == 200 and managed.app is not None and managed.failure is None
        assert '/_dev/signin?token=' in capsys.readouterr().out  # the startup link waited for the instance to start
        assert (await stop())['type'] == 'lifespan.shutdown.complete'
    asyncio.run(scenario())


def test_serve_still_fails_its_start_outside_development(project):
    add_field(project)
    runtime = ManagedRuntime(lambda: load_runtime(project, 'acme'))

    async def scenario():
        inbox, outbox = asyncio.Queue(), asyncio.Queue()
        await inbox.put({'type': 'lifespan.startup'})
        await runtime({'type': 'lifespan'}, inbox.get, outbox.put)
        message = await outbox.get()
        assert message['type'] == 'lifespan.startup.failed'
        assert 'evlet migrate acme' in message['message']
    asyncio.run(scenario())


def test_state_fields_get_a_complete_frozen_copy(project):
    class Invoice(Model):
        docstate = State(State.Step('draft'), State.Step('issued', lock=True), State.Step('paid', final=True),
                         rules=(State.Rule('draft', 'issued'), State.Rule('issued', 'paid')))
        number = Text(default='')

    code, names = field_code(Invoice.__fields__['docstate'])
    assert '...' not in code and UNFINISHED not in code and names == {'State'}
    copy = eval(code, {'State': State})
    assert tuple(copy.choices) == ('draft', 'issued', 'paid') and copy.final == ('paid',) and copy.default == 'draft'


def test_makemigration_for_a_state_model_migrates_without_editing(project):
    (project / 'apps/notes/models/invoice.py').write_text(
        "from evlet import Model, Rule, State, Text\n\n\n"
        "class Invoice(Model):\n"
        "    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}\n"
        "    reference = Text(default='')\n"
        "    docstate = State(State.Step('draft'), State.Step('issued', final=True), rules=(State.Rule('draft', 'issued'),))\n")
    target, needs_edit = makemigration(project, 'notes', 'acme', name='invoice', trust=True)
    assert 'State(State.Step(' in target.read_text() and not needs_edit
    install(project, 'acme', trust=True)


def test_an_unfinished_migration_is_refused_naming_the_file_and_line(project):
    folder = project / 'apps/notes/migrations'
    folder.mkdir(exist_ok=True)
    (folder / '__init__.py').write_text('')
    (folder / 'm0002_x.py').write_text(f"from evlet import AddField, Migration, Text\n\nmigration = Migration('0002_x', AddField('notesitem', 'x', Text(...)))  {UNFINISHED}\n")
    with pytest.raises(ValueError, match=r'apps/notes/migrations/m0002_x\.py:3: finish this line'):
        unfinished_migrations(read_package(project / 'apps/notes'))


def test_build_errors_name_the_app_file_not_the_build_copy(project):
    model = project / 'apps/notes/models/item.py'
    model.write_text(model.read_text() + '\nfrom evlet import Nope\n')
    with pytest.raises(ValueError) as caught:
        prepare(project, resolve_packages(project, ['notes']))
    message = str(caught.value)
    assert f'{project}/apps/notes/models/item.py:' in message and 'ImportError' in message
    assert '.build-' not in message and '<frozen' not in message


def test_meta_errors_name_the_model_and_option():
    from evlet import Registry, Link

    class Customer(Model):
        label_text = Text(default='')

    class Invoice(Model):
        customer = Link(Customer)

        class Meta:
            title_field = 'customer'
    with pytest.raises(ValueError, match=r"Invoice\.Meta\.title_field = 'customer'"):
        Registry(Customer, Invoice)


def test_setup_from_an_unbuilt_source_checkout_says_what_to_run(tmp_path):
    (tmp_path / 'frontend').mkdir()
    (tmp_path / 'frontend/package.json').write_text('{}')
    (tmp_path / 'pyproject.toml').write_text('')
    with pytest.raises(ValueError, match='no built browser UI.*npm --prefix'):
        check_framework_source(str(tmp_path))
    (tmp_path / 'evlet/ui/static').mkdir(parents=True)
    (tmp_path / 'evlet/ui/static/index.html').write_text('')
    check_framework_source(str(tmp_path))
    check_framework_source('evlet==0.4.0')


def test_public_plain_http_is_warned_and_ipv6_loopback_is_loopback(project, monkeypatch, capsys):
    from evlet.cli import serve as serving
    ran = []
    monkeypatch.setattr(serving, 'run_server', lambda application, **options: ran.append(options))
    assert '::1' in LOOPBACK
    serving.serve(project, 'acme', host='::1', port=8123)
    assert 'plain HTTP' not in capsys.readouterr().err
    with pytest.raises(ValueError, match='both --ssl-certfile and --ssl-keyfile'):
        serving.serve(project, 'acme', host='::1', ssl_certfile='cert.pem')
    assert 'over plain HTTP' in plain_http_warning('0.0.0.0', 8000, 'erp.example.com')


def test_backup_with_files_and_the_secret_key_reminder(project, capsys):
    from evlet.instances import load_runtime as load
    app = load(project, 'acme')
    app.initialize()
    app.database.close()
    private = project / 'instances/acme/files/private'
    private.mkdir(parents=True, exist_ok=True)
    (private / ('a' * 64)).write_bytes(b'attachment')
    written = backup(project, 'acme', with_files=True)
    archive = written[-1]
    assert archive.name.endswith('-files.tar.gz') and stat.S_IMODE(archive.stat().st_mode) == 0o600
    with tarfile.open(archive) as tar:
        assert f'files/private/{"a" * 64}' in tar.getnames()
        assert not any(name.endswith('secret.key') for name in tar.getnames())
    main(['backup', '--instance', 'acme', '--project', str(project)])
    out = capsys.readouterr().out
    assert out.startswith('Backup: ') and 'secret.key' in out and '--with-files' in out
