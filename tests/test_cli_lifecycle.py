"""Real package/instance lifecycle tests with disposable SQLite databases."""

from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from evlet import User
from evlet.cli.app import create_app as scaffold_app, fetch_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.cli.project import project_root, instance_guard
from evlet.cli.setup import setup
from evlet.instances import load_runtime, read_lock, database_state
from evlet.packages import read_package, resolve_packages, validate_url


def create_app(root, name):
    # Keep this suite as the unchanged legacy upgrade/compatibility fixture.
    return scaffold_app(root, name, legacy=True)


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
    yield root
    for name in list(sys.modules):
        if name in ("notes", "crm") or name.startswith(("notes.", "crm.")):
            del sys.modules[name]


def runtime(root, name='acme'):
    app = load_runtime(root, name)
    app.initialize()
    return app


def change_model(root, *, migration=True, fail=False):
    package = root / 'apps/notes/notes'
    model_file = package / 'models/__init__.py'
    original = model_file.read_text()
    model_file.write_text(original.replace('Model, Text', 'Model, Text, Boolean').replace('    class Meta:', '    done = Boolean(default=False)\n\n    class Meta:'))
    if migration:
        frozen = original.split('models = ')[0]
        (package / 'migrations/__init__.py').write_text('from evlet import AddField, Boolean, CreateModel, Migration\n' + frozen + '\nmigrations = (Migration("0001_initial", CreateModel(NotesItem)), Migration("0002_done", AddField("notesitem", "' + ('unknown' if fail else 'done') + '", Boolean(default=False))))\n')


def test_setup_containers_environment_and_never_overwrites(tmp_path):
    root = setup(tmp_path / 'p')
    assert sorted(path.name for path in root.iterdir()) == ['.venv', 'apps', 'instances']
    with pytest.raises(FileExistsError):
        setup(root)
    assert not list(root.rglob('*.sqlite3'))


def test_discovery_from_child_and_explicit_override(project, monkeypatch):
    monkeypatch.chdir(project / 'apps/notes/notes/models')
    assert project_root() == project
    assert project_root(project) == project
    with pytest.raises(ValueError, match='No Evlet project'):
        project_root(project / 'apps')


@pytest.mark.parametrize('name', ['../outside', 'A', 'class', 'a-b', '', '_app', 'evlet', 'json'])
def test_invalid_app_names_leave_no_app(project, name):
    before = set((project / 'apps').iterdir())
    with pytest.raises(ValueError):
        create_app(project, name)
    assert set((project / 'apps').iterdir()) == before


def test_instances_start_without_database(project):
    path = project / 'instances/acme'
    assert {item.name for item in path.iterdir()} == {'config.py', 'apps.lock'}
    assert read_lock(path) == {'apps': {}}


def test_new_config_omits_reserved_fields_and_old_config_still_loads(project):
    from evlet.instances import read_instance
    path = project / 'instances/acme'
    text = (path / 'config.py').read_text()
    assert 'database=' not in text and 'storage=' not in text
    config = read_instance(path)
    assert (config.database, config.storage) == ('sqlite', 'local')
    (path / 'config.py').write_text('from evlet import Instance\n\ninstance = Instance(name="acme", database="sqlite", storage="local", domain=None)\n')
    assert read_instance(path).name == 'acme'
    (path / 'config.py').write_text('from evlet import Instance\n\ninstance = Instance(name="acme", database="postgresql")\n')
    with pytest.raises(ValueError, match='SQLite'):
        read_instance(path)


def test_untrusted_install_does_not_execute_source(project, tmp_path):
    marker = tmp_path / 'executed'
    hooks = project / 'apps/notes/notes/hooks.py'
    hooks.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n' + hooks.read_text())
    with pytest.raises(ValueError, match='explicit trust'):
        install(project, 'acme', app_name='notes')
    assert not marker.exists()
    assert not (project / 'instances/acme/data').exists()


def test_static_metadata_never_imports_app(project, tmp_path):
    marker = tmp_path / 'executed'
    hooks = project / 'apps/notes/notes/hooks.py'
    hooks.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError("do not import")\n')
    package = read_package(project / 'apps/notes')
    assert package.name == 'notes'
    assert resolve_packages(project, ['notes'])[0].name == 'notes'
    assert not marker.exists()


def test_same_app_two_instances_have_separate_records_and_files(project):
    create_instance(project, 'beta')
    for name in ('acme', 'beta'):
        install(project, name, app_name='notes', trust=True)
    first, second = runtime(project), runtime(project, 'beta')
    model = first.registry.models['notesitem']
    record = first.database.create(model, title='Only Acme')
    assert second.database.count(second.registry.models['notesitem']) == 0
    with first.database.transaction() as tx:
        file_id = first.business.storage.add(tx, 'notesitem', record.id, 'private.txt', b'private bytes')
        row = tx.connection.execute('SELECT * FROM evlet_attachments WHERE id=?', (file_id,)).fetchone()
        assert first.business.storage.read(row) == b'private bytes'
        with pytest.raises(Exception):
            second.business.storage.read(row)
    assert first.business.storage.root != second.business.storage.root
    assert first.business.auth.database.path != second.business.auth.database.path
    assert first.business.jobs.tenant != second.business.jobs.tenant


def test_install_second_app_preserves_records_and_history(project):
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    app.database.create(app.registry.models['notesitem'], title='Keep me')
    create_app(project, 'crm')
    result = install(project, 'acme', app_name='crm', trust=True)
    app = runtime(project)
    assert set(app.registry.models) == {'notesitem', 'crmitem'}
    assert app.database.list(app.registry.models['notesitem'])[0].title == 'Keep me'
    assert Path(result['backup']).is_file()
    assert set(read_lock(project / 'instances/acme')['apps']) == {'notes', 'crm'}


def test_missing_dependency_fails_before_import_or_data(project):
    path = project / 'apps/notes/pyproject.toml'
    path.write_text(path.read_text().replace('"evlet>=0.1.2,<0.2"', '"evlet>=0.1.2,<0.2", "missing_evlet_test_dependency>=1"'))
    with pytest.raises(ValueError, match='Missing dependency'):
        install(project, 'acme', app_name='notes', trust=True)
    assert not (project / 'instances/acme/data').exists()


def test_local_dependency_installed_first(project):
    create_app(project, 'crm')
    path = project / 'apps/notes/pyproject.toml'
    path.write_text(path.read_text().replace('"evlet>=0.1.2,<0.2"', '"evlet>=0.1.2,<0.2", "crm>=0.1"'))
    result = install(project, 'acme', app_name='notes', trust=True)
    assert result['apps'] == ['crm', 'notes']
    assert set(runtime(project).registry.models) == {'crmitem', 'notesitem'}


def test_circular_dependencies_rejected(project):
    create_app(project, 'crm')
    for name, dependency in (('notes', 'crm'), ('crm', 'notes')):
        path = project / f'apps/{name}/pyproject.toml'
        path.write_text(path.read_text().replace('"evlet>=0.1.2,<0.2"', f'"evlet>=0.1.2,<0.2", "{dependency}>=0.1"'))
    with pytest.raises(ValueError, match='Circular'):
        install(project, 'acme', app_name='notes', trust=True)


def test_model_change_needs_explicit_migration_and_preserves_data(project):
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    app.database.create(app.registry.models['notesitem'], title='Original')
    change_model(project)
    with pytest.raises(ValueError, match='differs from apps.lock'):
        runtime(project)
    result = install(project, 'acme', trust=True)
    assert len(result['migrations']) == 1
    app = runtime(project)
    record = app.database.list(app.registry.models['notesitem'])[0]
    assert record.title == 'Original' and record.done is False


def test_failed_migration_preserves_database_lock_and_files(project):
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    item = app.database.create(app.registry.models['notesitem'], title='Do not lose me')
    path = project / 'instances/acme'
    before = (path / 'apps.lock').read_bytes()
    change_model(project, fail=True)
    with pytest.raises(ValueError, match='Migrations do not match'):
        install(project, 'acme', trust=True)
    assert (path / 'apps.lock').read_bytes() == before
    with sqlite3.connect(path / 'data/database.sqlite3') as connection:
        assert connection.execute('SELECT title FROM notesitem WHERE id=?', (item.id,)).fetchone()[0] == 'Do not lose me'
        assert 'done' not in [row[1] for row in connection.execute('PRAGMA table_info(notesitem)')]
    assert not (path / '.install-journal.json').exists()


def test_recover_commit_between_database_and_lock(project, monkeypatch):
    import importlib
    installer = importlib.import_module('evlet.cli.install')
    original = installer.atomic_json
    def fail_lock(path, value):
        if path.name == 'apps.lock':
            raise OSError('simulated disk failure')
        original(path, value)
    monkeypatch.setattr(installer, 'atomic_json', fail_lock)
    with pytest.raises(OSError, match='disk failure'):
        install(project, 'acme', app_name='notes', trust=True)
    with pytest.raises(ValueError, match='Interrupted'):
        runtime(project)
    monkeypatch.setattr(installer, 'atomic_json', original)
    result = install(project, 'acme', trust=True)
    assert result['apps'] == ['notes']
    assert read_lock(project / 'instances/acme') == database_state(project / 'instances/acme/data/database.sqlite3')
    assert runtime(project).registry.models['notesitem']


def test_active_server_guard_prevents_install(project):
    with instance_guard(project / 'instances/acme', serving=True):
        with pytest.raises(ValueError, match='in use'):
            install(project, 'acme', app_name='notes', trust=True)
    assert not (project / 'instances/acme/data').exists()


def test_lock_database_mismatch_fails_closed(project):
    install(project, 'acme', app_name='notes', trust=True)
    path = project / 'instances/acme/apps.lock'
    value = json.loads(path.read_text()); value['apps']['notes']['version'] = '99.0'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='differs from apps.lock'):
        runtime(project)


def test_view_action_and_permission_registration(project):
    package = project / 'apps/notes/notes'
    (package / 'hooks.py').write_text('''from evlet import AppDefinition, Action, Rule
from evlet.ui import PageView, Column, Text
from .models import models, NotesItem
from .migrations import migrations

def rename(tx, user, record):
    tx.update(NotesItem, record.id, title='Changed by action')

app = AppDefinition(models=models, migrations=migrations,
    actions=(Action(NotesItem, 'rename', 'Rename', rename),),
    permissions={('viewer', NotesItem): Rule(operations=())},
    views=(PageView('notes_home', route='/notes', title='Notes', layout=Column(children=[Text('Notes page')])),))
''')
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    model = app.registry.models['notesitem']
    assert not app.business.policy.allows(User(1, 'read', 'viewer'), model, 'read')
    assert app.ui.resolve('/notes', app.registry.ui_schema(), User(1, 'owner', 'admin'))['view']['title'] == 'Notes'
    item = app.database.create(model, title='Before')
    with app.database.transaction() as tx:
        app.business.actions[model, 'rename'].handler(tx, User(1, 'owner', 'admin'), item)
    assert app.database.get(model, item.id).title == 'Changed by action'


def test_schedule_registered_without_running_jobs(project):
    path = project / 'apps/notes/notes/hooks.py'
    path.write_text(path.read_text().replace('from evlet import AppDefinition', 'from evlet import AppDefinition, Task, Schedule').replace('permissions=rules, migrations=migrations)', 'permissions=rules, migrations=migrations, tasks=(Task("notes_task", lambda tx, context, payload: None),), schedules=(Schedule("notes_hourly", "notes_task", 3600),))'))
    install(project, 'acme', app_name='notes', trust=True)
    install(project, 'acme', trust=True)
    app = runtime(project)
    with app.database.transaction() as tx:
        assert tx.connection.execute('SELECT COUNT(*) FROM evlet_schedules').fetchone()[0] == 1
        assert tx.connection.execute('SELECT COUNT(*) FROM evlet_jobs').fetchone()[0] == 0


@pytest.mark.parametrize('url', ['file:///tmp/app', '/tmp/app', 'http://example.com/app.git', 'https://user:secret@example.com/app.git', 'ext::sh anything', '--help'])
def test_git_url_validation(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_fetch_uses_staging_and_does_not_run_python(project, tmp_path, monkeypatch):
    import evlet.cli.app as command
    source = project / 'apps/notes'
    marker = tmp_path / 'executed'
    (source / 'notes/__init__.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
    for args in (['init'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'Initial app']):
        subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True)
    destination = setup(tmp_path / 'fetched')
    actual_run = subprocess.run
    def local_transport(args, **options):
        if 'clone' in args:
            assert options['env']['GIT_CONFIG_GLOBAL'] == os.devnull
            assert '--no-recurse-submodules' in args
            args = ['git', '-c', 'core.hooksPath=' + os.devnull, 'clone', '--', str(source), args[-1]]
        return actual_run(args, **options)
    monkeypatch.setattr(command.subprocess, 'run', local_transport)
    report = fetch_app(destination, 'https://example.test/notes.git')
    assert 'Static validation passed' in report
    assert read_package(destination / 'apps/notes').locked()['commit']
    assert not marker.exists()
    with pytest.raises(ValueError, match='already exists'):
        fetch_app(destination, 'https://example.test/notes.git')
    assert not list((destination / 'apps').glob('.fetch-*'))


def test_cli_lists_and_backward_compatible_starter(project, capsys, tmp_path):
    main(['--project', str(project), 'app', '--list'])
    assert 'notes\t0.1.0' in capsys.readouterr().out
    main(['instance', '--list', '--project', str(project)])
    assert 'acme\tno installed apps' in capsys.readouterr().out
    main(['new', str(tmp_path / 'old_starter')])
    assert (tmp_path / 'old_starter/app.py').is_file()


def test_registration_ignores_stale_same_timestamp_bytecode(project):
    import py_compile
    file = project / 'apps/notes/notes/models/__init__.py'
    py_compile.compile(str(file), doraise=True)
    before = file.stat()
    file.write_text(file.read_text().replace("label = 'Item'", "label = 'Note'"))
    os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
    install(project, 'acme', app_name='notes', trust=True)
    assert runtime(project).registry.ui_schema()['models'][0]['label'] == 'Note'


def test_two_apps_can_append_migrations_in_interleaved_order(project):
    install(project, 'acme', app_name='notes', trust=True)
    create_app(project, 'crm')
    install(project, 'acme', app_name='crm', trust=True)
    change_model(project)
    install(project, 'acme', trust=True)
    app = runtime(project)
    assert 'done' in app.registry.models['notesitem'].__fields__
    with app.database.transaction() as tx:
        names = [row[0] for row in tx.connection.execute('SELECT name FROM evlet_migrations ORDER BY position')]
    assert names == ['app5_notes__0001_initial', 'app3_crm__0001_initial', 'app5_notes__0002_done']


def test_failed_first_install_leaves_no_published_database(project):
    path = project / 'apps/notes/notes/hooks.py'
    path.write_text('raise ValueError("invalid app registration")\n')
    with pytest.raises(ValueError, match='invalid app registration'):
        install(project, 'acme', app_name='notes', trust=True)
    assert not (project / 'instances/acme/data/database.sqlite3').exists()
    assert not (project / 'instances/acme/files').exists()
    assert read_lock(project / 'instances/acme') == {'apps': {}}


def test_interrupted_before_database_publish_recovers_old_state(project, monkeypatch):
    import importlib
    installer = importlib.import_module('evlet.cli.install')
    original = installer.os.replace
    def fail_database(source, target):
        if Path(target).name == 'database.sqlite3':
            raise OSError('simulated failure before publish')
        return original(source, target)
    monkeypatch.setattr(installer.os, 'replace', fail_database)
    with pytest.raises(OSError, match='before publish'):
        install(project, 'acme', app_name='notes', trust=True)
    monkeypatch.setattr(installer.os, 'replace', original)
    install(project, 'acme', app_name='notes', trust=True)
    assert runtime(project).registry.models['notesitem']


def test_instance_symlink_cannot_redirect_install(project, tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir()
    (project / 'instances/acme/data').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic link'):
        install(project, 'acme', app_name='notes', trust=True)
    assert not list(outside.iterdir())


def test_playground_ignores_customer_database_environment(tmp_path, monkeypatch):
    from evlet.cli.playground import playground_app
    customer = tmp_path / 'customer.sqlite3'
    customer.write_bytes(b'preserve existing database')
    monkeypatch.setenv('EVLET_DATABASE', str(customer))
    monkeypatch.setenv('EVLET_POSTGRES_DSN', 'never connect to this server')
    app = playground_app(tmp_path / 'demo')
    assert app.database.path != customer
    assert customer.read_bytes() == b'preserve existing database'
    assert app.database.count(app.registry.models['customer']) == 1


def test_docs_are_available_without_project_or_database(tmp_path, monkeypatch):
    from evlet.cli.docs import documentation, render_markdown
    monkeypatch.chdir(tmp_path)
    assert 'evlet setup myproject' in documentation()['cli.md']
    assert not list(tmp_path.iterdir())
    html = render_markdown('<script>alert(1)</script>\n[x](javascript:alert)\n```python\nx = 1\n```')
    assert '<script>' not in html and 'href="javascript:' not in html
    assert '<pre><code>x = 1' in html


def test_bound_actions_use_the_selected_instance_queue(project):
    path = project / 'apps/notes/notes/hooks.py'
    path.write_text('''from evlet import AppDefinition, Action, Task
from .models import models, NotesItem
from .migrations import migrations

def rename(tx, context, payload):
    tx.update(NotesItem, payload['id'], title='Completed job')

def actions(services):
    def queue(tx, user, record):
        return services.jobs.enqueue(tx, 'notes_rename', {'id': record.id}, target=(NotesItem, record.id))
    return (Action(NotesItem, 'queue', 'Queue rename', queue),)

app = AppDefinition(models=models, migrations=migrations, actions=actions,
                    tasks=(Task('notes_rename', rename),))
''')
    install(project, 'acme', app_name='notes', trust=True)
    create_instance(project, 'beta')
    install(project, 'beta', app_name='notes', trust=True)
    first, second = runtime(project), runtime(project, 'beta')
    model = first.registry.models['notesitem']
    record = first.database.create(model, title='Before queue')
    with first.database.transaction() as tx:
        receipt = first.business.actions[model, 'queue'].handler(tx, None, record)
    assert receipt.state == 'queued'
    assert first.business.jobs.run_one()
    assert first.database.get(model, record.id).title == 'Completed job'
    assert not second.business.jobs.run_one()


def test_install_and_backup_close_connections_without_garbage_collection(project):
    import gc
    gc.collect()
    gc.disable()
    try:
        install(project, 'acme', app_name='notes', trust=True)
        app = runtime(project)
        record = app.database.create(app.registry.models['notesitem'], title='No implicit cleanup')
        create_app(project, 'crm')
        install(project, 'acme', app_name='crm', trust=True)
        change_model(project)
        install(project, 'acme', trust=True)
        app = runtime(project)
        assert app.database.get(app.registry.models['notesitem'], record.id).title == 'No implicit cleanup'
    finally:
        gc.enable()
        gc.collect()


@pytest.mark.parametrize('consumer', ['action', 'job'])
@pytest.mark.parametrize('helper_kind', ['stale_cache', 'sourceless'])
@pytest.mark.parametrize('layout', ['module', 'package', 'namespace'])
def test_lazy_app_imports_keep_source_policy(project, consumer, helper_kind, layout):
    import py_compile

    package = project / 'apps/notes/notes'
    folder = package if layout == 'module' else package / 'nested'
    folder.mkdir(exist_ok=True)
    if layout == 'package':
        (folder / '__init__.py').write_text('')
    helper = folder / 'helper.py'
    module = 'helper' if layout == 'module' else 'nested.helper'
    (package / 'hooks.py').write_text('''from evlet import AppDefinition, Action, Task
from .models import models, NotesItem
from .migrations import migrations

def update(tx, user, record):
    from .helper import title
    tx.update(NotesItem, record.id, title=title())

def work(tx, context, payload):
    from .helper import title
    tx.update(NotesItem, payload['id'], title=title())

app = AppDefinition(models=models, migrations=migrations,
    actions=(Action(NotesItem, 'update_title', 'Update title', update),),
    tasks=(Task('notes_title', work, max_attempts=1),))
'''.replace('.helper import', '.' + module + ' import'))
    if helper_kind == 'stale_cache':
        helper.write_text("def title():\n    return 'Before'\n")
        py_compile.compile(str(helper), doraise=True)
        before = helper.stat()
        helper.write_text("def title():\n    return 'After!'\n")
        os.utime(helper, ns=(before.st_atime_ns, before.st_mtime_ns))
    install(project, 'acme', app_name='notes', trust=True)
    locked = read_package(project / 'apps/notes').locked()
    app = runtime(project)
    model = app.registry.models['notesitem']
    record = app.database.create(model, title='Original')
    if helper_kind == 'sourceless':
        helper.write_text("def title():\n    return 'Untracked bytecode'\n")
        py_compile.compile(str(helper), cfile=str(folder / 'helper.pyc'), doraise=True)
        helper.unlink()
        assert read_package(project / 'apps/notes').locked() == locked
    assert 'notes.' + module not in sys.modules
    if consumer == 'action':
        def invoke():
            with app.database.transaction() as tx:
                app.business.actions[model, 'update_title'].handler(tx, User(1, 'owner', 'admin'), record)
        if helper_kind == 'sourceless':
            with pytest.raises(ImportError, match='source files'):
                invoke()
        else:
            invoke()
    else:
        with app.database.transaction() as tx:
            receipt = app.business.jobs.enqueue(tx, 'notes_title', {'id': record.id}, target=(model, record.id))
        assert app.business.jobs.run_one()
        with app.database.transaction() as tx:
            state = tx.connection.execute('SELECT state,error FROM evlet_jobs WHERE id=?', (receipt.id,)).fetchone()
        assert state['state'] == ('failed' if helper_kind == 'sourceless' else 'succeeded')
        if helper_kind == 'sourceless':
            assert state['error'] == 'ModuleNotFoundError'
    assert app.database.get(model, record.id).title == ('Original' if helper_kind == 'sourceless' else 'After!')


def test_runtime_source_policy_leaves_unrelated_packages_alone(project, tmp_path, monkeypatch):
    import importlib
    import py_compile

    install(project, 'acme', app_name='notes', trust=True)
    runtime(project)
    package = tmp_path / 'unmanaged_package'
    package.mkdir()
    (package / '__init__.py').write_text('')
    helper = package / 'helper.py'
    helper.write_text("value = 'ordinary dependency'\n")
    py_compile.compile(str(helper), cfile=str(package / 'helper.pyc'), doraise=True)
    helper.unlink()
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        module = importlib.import_module('unmanaged_package.helper')
        assert module.value == 'ordinary dependency'
    finally:
        sys.modules.pop('unmanaged_package.helper', None)
        sys.modules.pop('unmanaged_package', None)


def test_local_lock_is_portable_and_moving_project_keeps_data(project, tmp_path, monkeypatch):
    import shutil
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    app.database.create(app.registry.models['notesitem'], title='Portable')
    app.database.close()
    assert read_lock(project/'instances/acme')['apps']['notes']['source'] == 'apps/notes'
    moved = tmp_path/'moved'
    shutil.copytree(project, moved)
    for name in list(sys.modules):
        if name == 'notes' or name.startswith('notes.'):
            monkeypatch.delitem(sys.modules, name)
    other = runtime(moved)
    assert other.database.list(other.registry.models['notesitem'])[0].title == 'Portable'


def test_legacy_absolute_local_lock_remains_readable(project, monkeypatch):
    from evlet.cli.project import atomic_json, json_text
    install(project, 'acme', app_name='notes', trust=True)
    path = project/'instances/acme'
    old = read_lock(path)
    old['apps']['notes']['source'] = '/previous/machine/project/apps/notes'
    atomic_json(path/'apps.lock', old)
    with sqlite3.connect(path/'data/database.sqlite3') as connection:
        connection.execute('UPDATE evlet_installed_apps SET manifest=? WHERE id=1', (json_text(old),))
    assert runtime(project).registry.models['notesitem']
    install(project, 'acme', confirm=lambda message: pytest.fail('Relocation alone must not request trust'))
    assert read_lock(path)['apps']['notes']['source'] == 'apps/notes'


def test_app_root_helpers_are_not_exposed_during_registration(project):
    helper = project/'apps/notes/outside_lock_helper.py'
    helper.write_text('value = 1\n')
    hooks = project/'apps/notes/notes/hooks.py'
    hooks.write_text('import outside_lock_helper\n' + hooks.read_text())
    with pytest.raises(ValueError, match='outside_lock_helper'):
        install(project, 'acme', app_name='notes', trust=True)
    assert str(helper.parent) not in sys.path


@pytest.mark.parametrize('name', ['uvicorn', 'packaging', 'boto3'])
def test_app_names_do_not_shadow_runtime_libraries(project, name):
    with pytest.raises(ValueError, match='conflicts'):
        create_app(project, name)
    assert not (project/'apps'/name).exists()


def test_scaffold_preserves_underscores_and_uses_supported_evlet_version(project):
    for name in ('shop_x', 'shopx'):
        create_app(project, name)
        install(project, 'acme', app_name=name, trust=True)
    assert {'shop_xitem', 'shopxitem'} <= runtime(project).registry.models.keys()
    metadata = (project/'apps/shop_x/pyproject.toml').read_text()
    assert 'evlet>=0.1.2,<0.2' in metadata and 'title = "Shop X"' in metadata
    for name in list(sys.modules):
        if name.split('.')[0] in ('shop_x', 'shopx'):
            del sys.modules[name]


def test_unchanged_migrate_does_not_create_backup(project):
    install(project, 'acme', app_name='notes', trust=True)
    path = project/'instances/acme/data/backups'
    before = list(path.glob('*'))
    result = install(project, 'acme')
    assert result['backup'] is None and result['migrations'] == []
    assert list(path.glob('*')) == before


def test_instance_session_uses_its_name_and_unique_preference_identity(project):
    from test_business import http
    install(project, 'acme', app_name='notes', trust=True)
    create_instance(project, 'beta')
    install(project, 'beta', app_name='notes', trust=True)
    first, second = runtime(project), runtime(project, 'beta')
    a = http(first, 'GET', '/api/auth/session')[1]['application']
    b = http(second, 'GET', '/api/auth/session')[1]['application']
    assert a['name'] == 'Acme' and b['name'] == 'Beta'
    assert not a['playground'] and not b['playground']
    assert a['id'] != b['id']
    assert http(runtime(project), 'GET', '/api/auth/session')[1]['application']['id'] == a['id']


def test_offline_docs_exclude_internal_handoff():
    from evlet.cli.docs import documentation
    assert 'handoff.md' not in documentation()


def test_config_security_sets_the_starting_sign_in_rules(project):
    from evlet import Security
    install(project, 'acme', app_name='notes', trust=True)
    config = project / 'instances/acme/config.py'
    original = config.read_text()
    config.write_text(original + '\nfrom evlet import Security\nsecurity = Security(session_hours=8, lockout=4, password_min=20)\n')
    app = runtime(project)
    try:
        auth, controls = app.business.auth, app.business.controls
        assert (auth.session_hours, auth.lockout, auth.password_min) == (8, 4, 20)
        settings = controls.state(User(1, 'admin', 'admin'), {'headers': [(b'host', b'127.0.0.1:8000')], 'client': ('127.0.0.1', 1)})['settings']
        assert settings['security.session_hours'] == {'file': 8, 'value': 8} and settings['security.lockout'] == {'file': 4, 'value': 4}
        # Controls can only make them stricter than the config value.
        for key, value in (('security.session_hours', 9), ('security.lockout', 5), ('security.password_min', 19)):
            with pytest.raises(ValueError):
                controls.check_setting(key, value)
        assert controls.check_setting('security.session_hours', 2) == 2 and controls.check_change('setting', 'security.lockout', 4) is None
    finally:
        app.database.close()
    for bad in ('Security(session_hours=0)', 'Security(session_hours=169)', 'Security(lockout=2)', 'Security(lockout=51)',
                'Security(password_min=14)', 'Security(lockout=True)', '{"lockout": 4}'):
        config.write_text(original + f'\nfrom evlet import Security\nsecurity = {bad}\n')
        with pytest.raises(ValueError):
            load_runtime(project, 'acme')
    assert Security() == Security(session_hours=12, lockout=5, password_min=15)
