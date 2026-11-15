"""Framework step 0016_activity and the old Journal names: a database written before the rename keeps its rows and
its hash chains, and each old name fails with a pointer to its replacement."""

from pathlib import Path
import json
import shutil
import sqlite3

import pytest

from evlet import Application, Database, LocalAuth, Model, Policy, Registry, Rule, Text, activity
from evlet.cli.main import main
from evlet.framework import STEPS, upgrade
import history_fixture as fixture
from test_business import PASSWORD, client, http

FIXTURE = Path(__file__).parent / 'fixtures' / 'sqlite-before-activity'
HASHED = 9  # Entries of the tamper-evident Order and Tally in the fixture.


def restore(folder):
    """The fixture's files, restored from their SQL dumps."""
    for name in fixture.FILES:
        with sqlite3.connect(folder / (name + '.sqlite3')) as connection:
            connection.executescript((FIXTURE / (name + '.sql')).read_text())
    return folder / 'database.sqlite3'


def schema(path):
    """(type, name, table, sql) of every schema object of a file, sorted."""
    with sqlite3.connect(path) as connection:
        return sorted(connection.execute('SELECT type, name, tbl_name, sql FROM sqlite_master'), key=repr)


def rows(path, table):
    with sqlite3.connect(path) as connection:
        return connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def application(path):
    database = Database(path, fixture.registry())
    return Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))


def instance(tmp_path, database):
    """A project with one instance, acme, whose database is a copy of `database`."""
    root = tmp_path / 'project'
    (root / 'apps').mkdir(parents=True)
    folder = root / 'instances' / 'acme'
    (folder / 'data').mkdir(parents=True)
    (folder / 'config.py').write_text("from evlet import Instance\n\ninstance = Instance(name='acme', domain=None)\n")
    shutil.copy(database, folder / 'data' / 'database.sqlite3')
    return root, folder / 'data' / 'database.sqlite3'


def test_the_step_renames_the_tables_in_place_and_keeps_every_row(tmp_path):
    path = restore(tmp_path)
    entries, hashed = rows(path, 'evlet_journal'), rows(path, 'evlet_journal_hashed')
    assert len(entries) == 11 and hashed == [('order',), ('tally',)]
    database = Database(path, fixture.registry())
    assert upgrade(database) == ['0016_activity'] and STEPS[-1].name == '0016_activity'
    database.close()
    # Same ids, hashes and chains under the new names; nothing is left under the old ones.
    assert rows(path, 'evlet_activity') == entries and rows(path, 'evlet_activity_hashed') == hashed
    objects = schema(path)
    assert not [item for item in objects if 'journal' in (item[1] + item[2] + (item[3] or '')).lower()]
    names = {item[1] for item in objects if item[2] == 'evlet_activity'}
    assert {'evlet_activity_record', 'evlet_activity_no_update', 'evlet_activity_no_delete', 'evlet_activity_no_replace'} <= names
    assert ('evlet_activity', 11) in rows(path, 'sqlite_sequence')
    # The Controls override of the custom role names the new operation.
    stored = dict((row[1], json.loads(row[2])) for row in rows(path, 'evlet_overrides') if row[0] == 'perm')
    assert stored['clerk|Order'] == {'ops': ['read', 'activity'], 'read': ['title', 'status'], 'scope': None, 'write': None}
    # Its index, triggers and columns are those a new database gets.
    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    made = application(fresh / 'database.sqlite3')
    made.initialize()
    made.database.close()
    created = schema(fresh / 'database.sqlite3')
    pick = lambda items: sorted(item for item in items if item[2].startswith('evlet_activity') and item[0] in ('index', 'trigger'))
    assert pick(objects) == pick(created) and len(pick(objects)) == 5
    columns = lambda file, table: [row[1:] for row in sqlite3.connect(file).execute(f'PRAGMA table_info("{table}")')]
    for table in ('evlet_activity', 'evlet_activity_hashed'):
        assert columns(path, table) == columns(fresh / 'database.sqlite3', table)


def test_chains_written_before_the_rename_verify_and_tampering_still_shows(tmp_path, capsys):
    path = restore(tmp_path)
    app = application(path)
    app.initialize()  # Runs the pending framework step, as an application outside an instance does.
    assert activity.verify(app.database) == (HASHED, [])
    # The CLI reads the same chains.
    app.database.close()
    root, copy = instance(tmp_path, path)
    main(['activity', 'verify', 'acme', '--project', str(root)])
    assert capsys.readouterr().out == f'Activity verified: {HASHED} hashed entries, no breaks\n'
    # A new entry extends the record's chain from the hash stored before the rename.
    app = application(path)
    app.initialize()
    admin = client(app)
    order = next(row for row in rows(path, 'order') if row[1] == 'Spices and tea')
    last = rows(path, 'evlet_activity')[7]
    assert (last[0], last[2]) == (8, order[0])
    from evlet.identity import text
    status, note, _ = admin('POST', f'/api/order/{text(order[0])}/activity', {'kind': 'note', 'text': 'After the rename'})
    assert status == 201, note
    assert rows(path, 'evlet_activity')[-1][7] == last[8]
    assert activity.verify(app.database) == (HASHED + 1, [])
    # The custom role keeps its migrated override: it adds notes but no longer reads the amount.
    assert admin('POST', '/api/auth/users', {'username': 'casey', 'password': PASSWORD, 'role': 'clerk'})[0] == 201
    casey = client(app, 'casey')
    assert casey('POST', f'/api/order/{text(order[0])}/activity', {'kind': 'note', 'text': 'From the clerk'})[0] == 201
    assert 'amount' not in casey('GET', f'/api/order/{text(order[0])}')[1]
    # Still append only, under the new trigger names.
    app.database.close()
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError, match='Activity is append only'):
        connection.execute("UPDATE evlet_activity SET data='{}' WHERE id=2")
    # An entry from before the rename that is changed behind the engine's back is a break.
    with sqlite3.connect(copy) as connection:
        connection.execute('DROP TRIGGER evlet_activity_no_update')
        connection.execute('''UPDATE evlet_activity SET data='{"text":"Seen by nobody"}' WHERE id=8''')
    with pytest.raises(SystemExit) as exit:
        main(['activity', 'verify', 'acme', '--project', str(root)])
    assert exit.value.code == 1
    output = capsys.readouterr()
    assert output.out == f'Entry 8 (order {text(order[0])}): the hash does not match the entry\n'
    assert output.err == f'1 break in {HASHED} hashed entries\n'


def test_an_upgraded_database_opens_unchanged(tmp_path):
    path = restore(tmp_path)
    database = Database(path, fixture.registry())
    upgrade(database)
    database.close()
    contents = lambda: {name: (objects, {item[1]: rows(file, item[1]) for item in objects if item[0] == 'table'})
                        for name in fixture.FILES for file in (path.with_name(name + '.sqlite3'),) for objects in (schema(file),)}
    before = contents()
    app = application(path)
    app.initialize()
    assert upgrade(app.database) == [] and app.database.migrate(()) == []
    app.database.close()
    assert contents() == before


def test_verify_refuses_an_instance_whose_step_is_pending(tmp_path, capsys):
    root, _ = instance(tmp_path, restore(tmp_path))
    with pytest.raises(SystemExit):
        main(['activity', 'verify', 'acme', '--project', str(root)])
    assert 'framework migrations pending; run evlet migrate acme first' in capsys.readouterr().err


def test_an_app_migration_before_the_step_writes_to_the_renamed_table(tmp_path):
    """evlet migrate runs app migrations before the framework steps; their entries must join the old chains."""
    path = restore(tmp_path)
    database = Database(path, fixture.registry())
    entries = rows(path, 'evlet_journal')
    database.migrate(())
    assert rows(path, 'evlet_activity') == entries
    with database.transaction() as tx:
        tx.create(fixture.Tally, amount=3)
    assert upgrade(database) == ['0016_activity']
    assert activity.verify(database) == (HASHED + 1, [])
    database.close()


# The old names


def test_meta_journal_names_its_replacement():
    with pytest.raises(ValueError, match=r'Meta.journal was renamed: use Meta.activity = Activity\(...\)'):
        type('Old', (Model,), {'Meta': type('Meta', (), {'journal': None}), 'title': Text(default='')})


def test_importing_journal_names_its_replacement():
    with pytest.raises(ImportError, match='evlet.Journal was renamed: use evlet.Activity'):
        from evlet import Journal  # noqa: F401
    with pytest.raises(ImportError, match='evlet.journal was renamed: use evlet.activity'):
        from evlet import journal  # noqa: F401
    import evlet
    assert not hasattr(evlet, 'Activities') and 'Journal' not in evlet.__all__


def test_the_journal_operation_names_its_replacement(tmp_path):
    registry = fixture.registry()
    with pytest.raises(ValueError, match="the Rule operation 'journal' was renamed; use 'activity'"):
        Policy(registry, rules={('viewer', fixture.Order): Rule(('read', 'journal'))})
    app = application(restore(tmp_path))
    app.initialize()
    http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    admin = client(app)
    change = {'kind': 'perm', 'key': 'clerk|Order', 'value': {'ops': ['read', 'journal'], 'read': None, 'write': None, 'scope': None}}
    status, payload, _ = admin('POST', '/api/_controls/overrides', {'changes': [change]})
    assert status == 422 and 'The operation journal was renamed; use activity.' in json.dumps(payload)
    app.database.close()


def test_the_journal_route_names_its_replacement(tmp_path):
    app = application(restore(tmp_path))
    app.initialize()
    admin = client(app)
    from evlet.identity import text
    order = text(rows(app.database.path, 'order')[0][0])
    for method, body in (('GET', None), ('POST', {'kind': 'note', 'text': 'Hi'})):
        status, payload, _ = admin(method, f'/api/order/{order}/journal', body)
        assert (status, payload) == (410, {'error': '/api/order/{id}/journal was renamed: use /api/order/{id}/activity'})
    assert admin('GET', f'/api/order/{order}/activity')[0] == 200
    app.database.close()


@pytest.mark.parametrize('argv', [['journal', 'verify', 'acme'], ['--project', '/nowhere', 'journal', 'verify', 'acme']])
def test_the_journal_command_names_its_replacement(argv, capsys):
    with pytest.raises(SystemExit) as exit:
        main(argv)
    assert exit.value.code == 2
    assert "'evlet journal' was renamed: use 'evlet activity verify INSTANCE'" in capsys.readouterr().err


def test_old_modules_meta_inheritance_and_operation_name_the_replacement():
    import importlib
    import pytest as _pytest
    from evlet import Model, Policy, Registry, Text, User
    from evlet.permissions import Rule
    for name, replacement in (('evlet.journal', 'evlet.activity'), ('evlet.cli.journal', 'evlet.cli.activity')):
        with _pytest.raises(ImportError, match=f'{name} was renamed: use {replacement}'):
            importlib.import_module(name)
    class Base:
        journal = None
    with _pytest.raises(ValueError, match='Meta.journal was renamed'):
        class Inherited(Model):
            title = Text()
            Meta = type('Meta', (Base,), {})
    class Plain(Model):
        permissions = {'admin': Rule(('read',))}
        title = Text()
    policy = Policy(Registry(Plain))
    with _pytest.raises(ValueError, match="'journal' operation was renamed: use 'activity'"):
        policy.allows(User(1, 'admin', 'admin'), Plain, 'journal')
