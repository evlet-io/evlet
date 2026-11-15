"""Models and data for tests/fixtures/sqlite-before-activity: a database written by the Evlet before framework step
0016_activity, whose record history was still called the Journal (evlet_journal, Meta.journal, the `journal` operation).

tests/test_activity_rename.py restores it, runs the framework steps and checks that the hash chains written before the
rename still verify. Keep the dumps as they are. To write them again, run make_dumps(folder) with that earlier Evlet
on the path (its tests/ directory too, for the helpers); the models below then declare Meta.journal = Journal(...).
"""

from evlet import Currency, Integer, Model, Registry, Rule, State, Text

try:
    from evlet import Activity as History
    OPTION = 'activity'
except ImportError:  # make_dumps, with the Evlet before the rename
    from evlet import Journal as History
    OPTION = 'journal'

# The operation that lets a role add notes and follow ups, under the name of the Evlet that runs this.
NOTES = OPTION
ROLES = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
         'viewer': Rule(('read', NOTES))}


class Order(Model):
    """Tamper evident, with a State: engine entries with the state manager's columns, and people's notes."""
    Meta = type('Meta', (), {OPTION: History(tamper_evident=True)})
    permissions = ROLES
    title = Text(default='')
    amount = Currency(default=0)
    docstate = State(
        State.Step('draft'),
        State.Step('placed', lock=True),
        rules=(State.Rule('draft', 'placed', 'Place', key='place', remark='optional'),))


class Memo(Model):
    """An Activity without hashes."""
    Meta = type('Meta', (), {OPTION: History()})
    permissions = ROLES
    title = Text(default='')


class Tally(Model):
    """A tamper-evident ledger: trusted code writes its rows, each with an entry."""
    Meta = type('Meta', (), {'ledger': True, OPTION: History(tamper_evident=True)})
    amount = Integer()


MODELS = (Order, Memo, Tally)
FILES = ('database', 'sessions', 'logs', 'eva')


def registry():
    return Registry(*MODELS)


def make_dumps(folder):
    """Write the fixture with the Evlet on the path: four SQL dumps, one per file of the database."""
    import sqlite3
    import tempfile
    from pathlib import Path
    from evlet import Database
    from evlet.auth import User
    from evlet.framework import upgrade
    from test_business import client
    from test_rules_types import add_user, start
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'database.sqlite3'
        app = start(Database(path, registry()))
        add_user(app, 'editor')
        add_user(app, 'viewer')
        admin = client(app)
        # A custom role whose stored Controls override (stricter than the file: no amount) names the operation; step
        # 0016 renames it there.
        assert admin('POST', '/api/_controls/roles', {'id': 'clerk', 'base': 'viewer'})[0] == 201
        change = {'kind': 'perm', 'key': 'clerk|Order', 'value': {'ops': ['read', NOTES], 'read': ['title', 'docstate'],
                                                                 'write': None, 'scope': None}}
        assert admin('POST', '/api/_controls/overrides', {'changes': [change]})[0] == 200
        people = {name: User(index, name, name) for index, name in enumerate(('admin', 'editor', 'viewer'), 1)}
        from importlib import import_module
        history = import_module('evlet.' + OPTION)
        with app.business.context(people['editor']) as context:
            first = context.create(Order, title='Spices', amount='120.50')['id']
            context.update(Order, first, title='Spices and tea', amount='130.25')
            second = context.create(Order, title='Dates')['id']
            memo = context.create(Memo, title='Plain')['id']
            history.post(context, Memo, memo, 'note', text='Not hashed')
        with app.business.context(people['admin']) as context:
            context.move(Order, first, 'place', remark='Customer confirmed')
            history.post(context, Order, first, 'follow_up', text='Call about delivery', due='2026-11-30')
        with app.business.context(people['viewer']) as context:
            history.post(context, Order, first, 'note', text='Seen by the viewer')
            history.post(context, Order, second, 'call', text='Asked for a quote', party='Gulf Spices', minutes=5)
        with app.database.transaction() as tx:
            row = tx.create(Tally, amount=1).id
            tx.create(Tally, amount=2)
        upgrade(app.database)
        app.database.close()
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            file = path.with_name(name + '.sqlite3')
            if file.exists():
                with sqlite3.connect(file) as connection:
                    (target / (name + '.sql')).write_text('\n'.join(connection.iterdump()) + '\n')
    return row
