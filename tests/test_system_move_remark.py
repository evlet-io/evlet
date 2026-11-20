"""A remark on a system move (a rule with by=()): application code passes context.move(..., remark=...), and the
activity's `moved` entry keeps it as it keeps a person's. finance_v1's delivery undo wants its reason on the move."""

import pytest

from evlet import Database, Model, Registry, Rule, State, Text, User, ValidationError
from evlet.activity import entries
from test_rules_types import start

ADMIN = User(1, 'admin', 'admin')
ALL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Delivery(Model):
    permissions = ALL
    title = Text(default='Delivery')
    docstate = State(State.Step('dispatched'), State.Step('posted'), State.Step('back'), rules=(
        State.Rule('dispatched', 'posted', key='to_all', by=()),
        State.Rule('posted', 'dispatched', key='undo', by=(), remark='required'),
        State.Rule('posted', 'back', key='turn_back', by=(), remark='optional')))


@pytest.fixture
def app(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Delivery)))
    yield app
    app.database.close()


def remarks(app, record_id):
    with app.business.context(ADMIN) as context:
        return [(entry['rule'], entry.get('remark')) for entry in entries(context, Delivery, record_id)['items'] if entry['kind'] == 'moved'][::-1]


def test_code_gives_a_system_move_a_remark_and_the_activity_keeps_it(app):
    with app.business.context(ADMIN) as context:
        record = context.create(Delivery)
        context.move(Delivery, record.id, 'to_all', remark='All 5 arrived')
    with pytest.raises(ValidationError, match='needs a remark'):
        with app.business.context(ADMIN) as context:
            context.move(Delivery, record.id, 'undo')
    with app.business.context(ADMIN) as context:
        context.move(Delivery, record.id, 'undo', remark='Receipt entered twice')
        context.move(Delivery, record.id, 'to_all')
        context.move(Delivery, record.id, 'turn_back', remark='Refused at the door')
    assert remarks(app, record.id) == [('to_all', 'All 5 arrived'), ('undo', 'Receipt entered twice'), ('to_all', None),
                                       ('turn_back', 'Refused at the door')]
