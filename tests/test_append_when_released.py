"""A Table declared append_when_released=True: trusted code appends rows to it on a released (locked) record, while
every request (browser, API, MCP) is still refused, and the saved rows stay as they are. finance_v1's arrivals log on
a sent Stock Transfer had to be a separate model shown as a Related block before."""

import pytest

from evlet import Child, Database, Decimal, Model, Registry, Rule, State, Table, Text, User, ValidationError, action, release
from support import client
from test_rules_types import start

ADMIN = User(1, 'admin', 'admin')
ALL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Line(Child):
    qty = Decimal(scale=2)


class Arrival(Child):
    qty = Decimal(scale=2)
    note = Text(default='')


class Transfer(Model):
    permissions = ALL
    title = Text(default='Transfer')
    lines = Table(Line)
    arrivals = Table(Arrival, append_when_released=True)
    docstate = State(State.Step('draft'), State.Step('sent', release=True), State.Step('void', final=True),
                     rules=(State.Rule('draft', 'sent', 'Send', key='send'), State.Rule('sent', 'void', 'Void', key='void')))

    @release
    def posted(self):
        pass

    @release.exit
    def unposted(self):
        pass

    @action
    def receive(self):
        """Code that logs an arrival on the sent transfer, as finance_v1's receipt does."""
        self.arrivals = [*({'id': row.id, 'qty': row.qty, 'note': row.note} for row in self.arrivals), {'qty': '2', 'note': 'dock 3'}]


@pytest.fixture
def sent(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Transfer)))
    with app.business.context(ADMIN) as context:
        record = context.create(Transfer, children={'lines': [{'qty': '5'}], 'arrivals': [{'qty': '1'}]})
        context.move(Transfer, record.id, 'send')
    yield app, record.id
    app.database.close()


def rows(app, record_id):
    with app.business.context(ADMIN) as context:
        record = context.get(Transfer, record_id)
        return [(str(row.qty), row.note) for row in record.arrivals], record


def test_code_appends_rows_to_the_opted_in_table_of_a_released_record(sent):
    app, record_id = sent
    first = rows(app, record_id)[1].arrivals[0].id
    with app.business.context(ADMIN) as context:
        context.update(Transfer, record_id, children={'arrivals': [{'id': first}, {'qty': '3', 'note': 'late'}]})
    with app.business.context(ADMIN) as context:
        context.action(Transfer, record_id, 'receive')
    found, record = rows(app, record_id)
    assert found == [('1.00', ''), ('3.00', 'late'), ('2.00', 'dock 3')]
    assert record.docstate == 'sent'


def test_saved_rows_and_other_tables_stay_locked_even_for_code(sent):
    app, record_id = sent
    _, record = rows(app, record_id)
    first, line = record.arrivals[0].id, record.lines[0].id
    for children in ({'arrivals': [{'id': first, 'qty': '9'}]},              # a saved row changed
                     {'arrivals': [{'qty': '4'}, {'id': first}]},             # a new row before a saved one
                     {'arrivals': []},                                        # a saved row removed
                     {'lines': [{'id': line}, {'qty': '1'}]}):                # a table without the option
        with pytest.raises(ValidationError, match='so it is locked'):
            with app.business.context(ADMIN) as context:
                context.update(Transfer, record_id, children=children)
    assert rows(app, record_id)[0] == [('1.00', '')]


def test_a_request_cannot_append_to_it(sent):
    app, record_id = sent
    c = client(app)
    url = f'/api/transfer/{record_id}'
    status, body, _ = c('PATCH', url, {'children': {'arrivals': {'create': [{'qty': '3'}]}}}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert status == 422 and 'arrivals' in body['fields'], body
    assert rows(app, record_id)[0] == [('1.00', '')]
