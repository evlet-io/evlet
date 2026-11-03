"""Phase 7: Sequence fields, counters, period reset, scopes and concurrency, on real SQLite."""

from concurrent.futures import ThreadPoolExecutor
import re
import sqlite3

import pytest

from evlet import (AddField, ConflictError, Database, Date, Link, Migration, Model, Move, Registry, RenameField, Rule, SchemaError,
                   Sequence, State, Text, ValidationError, action)
from evlet.auth import User
from evlet.calc import F
from evlet.identity import RecordId
from evlet.sequences import backfill, clear_for_copy, parse_format
from test_business import client
from test_rules_types import ALL, add_user, start

ADMIN = User(1, 'admin', 'admin')


class Company(Model):
    permissions = ALL
    name = Text(default='')


class Invoice(Model):
    class Meta:
        # A revision is a new draft without a number; it gets its own when it is issued.
        revisable = True
    permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('name', 'number', 'status'))}
    company = Link(Company, required=True)
    posting_date = Date(required=True)
    name = Text(default='')
    number = Sequence('INV-{year}-{n:05}', scope=('company',), assign_on='issued', date='posting_date')
    status = State(('draft', 'issued', 'void'), moves=(
        Move('draft', 'issued', 'Issue', run='on_issue'),
        Move('issued', 'draft', 'Reopen'),
    ), locked=('issued', 'void'), final=('void',), voidable=('issued',))

    def on_issue(self):
        if self.name == 'fail':
            raise ValidationError({'name': 'Run failed'})

    def changed(self, change):
        # Runs after the number is assigned and the record is written, in the same transaction.
        if change.operation == 'move' and self.name == 'boom':
            raise ValidationError({'name': 'Rejected after numbering'})

    @action('Rename', inputs={'value': Text(required=True)})
    def rename(self, value):
        self.name = value

    @action('Renumber')
    def renumber(self):
        self.number = 'INV-HAND'


class Receipt(Model):
    permissions = ALL
    received_on = Date(required=True)
    number = Sequence('R{yy}{month}-{n:03}', date='received_on')


class Ticket(Model):
    class Meta:
        title_field = 'code'
    permissions = ALL
    code = Sequence('T{n}')
    title = Text(default='')


class Pass(Model):
    permissions = ALL
    holder = Text(required=True, unique=True)
    code = Sequence('P{n}')


class Visit(Model):
    # The clerk may edit the visit but cannot read its number.
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), read_fields=('day', 'note'), write_fields=('day', 'note'))}
    day = Date(required=True)
    note = Text(default='')
    code = Sequence('V{year}-{n}', date='day')


class Slip(Model):
    # Numbered on create and revisable, so a revision is numbered when the engine creates it.
    class Meta:
        revisable = True
    permissions = ALL
    note = Text(default='')
    code = Sequence('S{n}')
    status = State(('open', 'closed', 'void'), moves=(Move('open', 'closed', 'Close'),),
                   locked=('closed', 'void'), final=('void',), voidable=('closed',))

    def changed(self, change):
        if change.operation == 'revise' and self.note == 'boom':
            raise ValidationError({'note': 'Rejected after numbering'})


class Permit(Model):
    # Held permits can be voided before they are granted, so before they have a number.
    permissions = ALL
    code = Sequence('PM{n}', assign_on='granted')
    status = State(('draft', 'held', 'granted', 'void'), moves=(Move('draft', 'held', 'Hold'), Move('held', 'granted', 'Grant')),
                   locked=('held', 'granted', 'void'), final=('void',), voidable=('held', 'granted'))


MODELS = (Company, Invoice, Receipt, Ticket, Pass, Visit, Slip, Permit)


@pytest.fixture
def numbered(tmp_path):
    return seed(start(Database(tmp_path / 'db.sqlite3', Registry(*MODELS))))


def seed(app):
    c = client(app)
    one = c('POST', '/api/company', {'name': 'One'})[1]['id']
    two = c('POST', '/api/company', {'name': 'Two'})[1]['id']
    return app, c, one, two


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def move(c, url, to):
    return c('POST', f'{url}/moves/{to}', {}, headers=etag(c, url))


def void(c, url, reason='Wrong'):
    return c('POST', f'{url}/void', {'reason': reason}, headers=etag(c, url))


def revise(c, url, reason='Wrong'):
    return c('POST', f'{url}/revise', {'reason': reason}, headers=etag(c, url))


def invoice(c, company, day='2026-03-10', **values):
    status, record, _ = c('POST', '/api/invoice', {'company': company, 'posting_date': day, **values})
    assert status == 201, record
    return record, f"/api/invoice/{record['id']}"


def issue(c, company, day='2026-03-10', **values):
    record, url = invoice(c, company, day, **values)
    status, body, _ = move(c, url, 'issued')
    assert status == 200, body
    return body['number']


# Declarations

def test_formats_are_validated_strictly():
    assert parse_format('INV-{year}-{n:05}') == ('INV-', ('year', 0), '-', ('n', 5))
    assert parse_format('{n}') == (('n', 0),)
    for text in ('', 'INV', 'INV-{year}', '{n}{n}', '{n:5}', '{n:00}', '{n:019}', '{N}', '{day}-{n}', '{ n }', '{year}{yy}{n}',
                 '{month}-{n}', 'A{n', 'A}{n}', '{{n}}', 'A{n}}', '{n}\n', 'x' * 121 + '{n}', 42):
        with pytest.raises(ValueError):
            parse_format(text)
    for options in ({'scope': 'company'}, {'scope': ('a', 'a')}, {'scope': ('bad name',)}, {'assign_on': ''},
                    {'date': 'posting_date'}, {'date': 1}, {'required': True}, {'default': 'x'}, {'unique': True},
                    {'compute': F('x')}):
        with pytest.raises(ValueError):
            Sequence('N{n}', **options)
    assert Sequence('N{year}{n}', date='day').period == 'year' and Sequence('{yy}{month}{n}').period == 'month'


def test_declarations_are_checked_against_the_model():
    def register(**namespace):
        namespace.setdefault('permissions', ALL)
        return Registry(Company, type('Doc', (Model,), namespace))
    with pytest.raises(ValueError, match='scope field'):
        register(code=Sequence('N{n}', scope=('missing',)))
    with pytest.raises(ValueError, match='required'):
        register(company=Link(Company), code=Sequence('N{n}', scope=('company',)))
    with pytest.raises(ValueError, match='scope field'):
        register(other=Text(default=''), note=Text(compute=F('other')), code=Sequence('N{n}', scope=('note',)))
    with pytest.raises(ValueError, match='Date field'):
        register(day=Text(required=True), code=Sequence('N{year}{n}', date='day'))
    with pytest.raises(ValueError, match='assign_on'):
        register(code=Sequence('N{n}', assign_on='issued'))
    with pytest.raises(ValueError, match='assign_on'):
        register(status=State(('draft', 'sent')), code=Sequence('N{n}', assign_on='issued'))
    with pytest.raises(ValueError, match='not its State'):
        register(status=State(('draft', 'sent')), code=Sequence('N{n}', scope=('status',)))
    with pytest.raises(ValueError, match='cannot read a Sequence'):
        register(code=Sequence('N{n}'), copy=Text(compute=F('code')))
    from evlet import Child, Table
    with pytest.raises(ValueError, match='root document'):
        register(rows=Table(type('Row', (Child,), {'code': Sequence('N{n}')})))
    # A model whose only special field is a Sequence is a document, so the engine assigns it.
    assert Ticket.__declaration_mode__ and register(code=Sequence('N{n}')).sequences


# Assignment

def check_numbers_follow_moves_scopes_and_periods(numbered):
    app, c, one, two = numbered
    record, url = invoice(c, one)
    assert record['number'] is None
    issued = move(c, url, 'issued')[1]
    assert issued['number'] == 'INV-2026-00001'
    # Each company has its own counter, and each posting year restarts it.
    assert issue(c, one) == 'INV-2026-00002'
    assert issue(c, two) == 'INV-2026-00001'
    assert issue(c, one, '2027-01-02') == 'INV-2027-00001'
    assert issue(c, one, '2026-12-31') == 'INV-2026-00003'
    # Reopening and issuing again keeps the number; a voided record keeps it too.
    reopened = move(c, url, 'draft')[1]
    assert reopened['number'] == 'INV-2026-00001' and move(c, url, 'issued')[1]['number'] == 'INV-2026-00001'
    assert void(c, url)[1]['number'] == 'INV-2026-00001'
    # The month resets monthly; the date field decides the period.
    receipts = [c('POST', '/api/receipt', {'received_on': day})[1]['number']
                for day in ('2026-01-31', '2026-02-01', '2026-02-28', '2027-02-01')]
    assert receipts == ['R2601-001', 'R2602-001', 'R2602-002', 'R2702-001']
    # assign_on="create" without a period numbers every new record.
    assert [c('POST', '/api/ticket', {'title': str(i)})[1]['code'] for i in range(3)] == ['T1', 'T2', 'T3']


def test_numbers_follow_moves_scopes_and_periods(numbered):
    check_numbers_follow_moves_scopes_and_periods(numbered)


def check_assigned_numbers_fix_their_scope_and_period(numbered):
    app, c, one, two = numbered
    record, url = invoice(c, one)
    move(c, url, 'issued')
    move(c, url, 'draft')
    edit = lambda body: c('PATCH', url, body, headers=etag(c, url))
    assert edit({'company': two})[1]['fields'] == {'company': 'This is fixed once the number is assigned'}
    assert edit({'posting_date': '2027-01-01'})[1]['fields'] == {'posting_date': 'This stays in the period of the assigned number'}
    assert edit({'posting_date': '2026-11-30'})[1]['posting_date'] == '2026-11-30'
    receipt = c('POST', '/api/receipt', {'received_on': '2026-05-05'})[1]
    status, body, _ = c('PATCH', f"/api/receipt/{receipt['id']}", {'received_on': '2026-06-01'}, headers=etag(c, f"/api/receipt/{receipt['id']}"))
    assert status == 422 and body['fields'] == {'received_on': 'This stays in the period of the assigned number'}


def test_assigned_numbers_fix_their_scope_and_period(numbered):
    check_assigned_numbers_fix_their_scope_and_period(numbered)


def check_failures_do_not_consume_numbers(numbered):
    app, c, one, two = numbered
    # A run method that fails before assignment, and a reaction that fails after the number is written.
    for name in ('fail', 'boom'):
        record, url = invoice(c, one, name=name)
        status, body, _ = move(c, url, 'issued')
        assert status == 422, body
        assert c('GET', url)[1]['number'] is None
    with pytest.raises(RuntimeError):
        with app.business.context(ADMIN) as context:
            assert context.create(Ticket, title='rolled back')['code'] == 'T1'
            raise RuntimeError('rollback')
    assert issue(c, one) == 'INV-2026-00001'
    assert c('POST', '/api/ticket', {})[1]['code'] == 'T1'
    # A deleted record's number is never reused.
    ticket = c('POST', '/api/ticket', {})[1]
    assert c('DELETE', f"/api/ticket/{ticket['id']}", headers=etag(c, f"/api/ticket/{ticket['id']}"))[0] == 204
    assert c('POST', '/api/ticket', {})[1]['code'] == 'T3'


def test_failures_do_not_consume_numbers(numbered):
    check_failures_do_not_consume_numbers(numbered)


# Void and revise

def check_void_and_revise_keep_and_renew_numbers(numbered):
    app, c, one, two = numbered
    record, url = invoice(c, one)
    assert move(c, url, 'issued')[1]['number'] == 'INV-2026-00001'
    # The revision starts as a draft without a number; the original is void and keeps its own.
    status, copy, _ = revise(c, url)
    assert status == 201 and copy['number'] is None and copy['status'] == 'draft' and copy['revises'] == record['id']
    original = c('GET', url)[1]
    assert (original['status'], original['number'], original['revised_by']) == ('void', 'INV-2026-00001', copy['id'])
    # The copy is a new record, so its scope is free until it is numbered; issuing it takes the next number.
    copy_url = f"/api/invoice/{copy['id']}"
    assert c('PATCH', copy_url, {'company': two}, headers=etag(c, copy_url))[1]['company'] == two
    assert move(c, copy_url, 'issued')[1]['number'] == 'INV-2026-00001'
    assert issue(c, one) == 'INV-2026-00002'
    # Voiding before the number is due assigns nothing; voiding after it keeps the number.
    permits = []
    for _ in range(2):
        permit = c('POST', '/api/permit', {})[1]
        permits.append(f"/api/permit/{permit['id']}")
        move(c, permits[-1], 'held')
    assert move(c, permits[1], 'granted')[1]['code'] == 'PM1'
    assert [(body['status'], body['code']) for body in (void(c, url)[1] for url in permits)] == [('void', None), ('void', 'PM1')]
    third = f"/api/permit/{c('POST', '/api/permit', {})[1]['id']}"
    move(c, third, 'held')
    assert move(c, third, 'granted')[1]['code'] == 'PM2'
    # A number due on create is assigned to the copy when the engine creates it.
    slip = c('POST', '/api/slip', {})[1]
    slip_url = f"/api/slip/{slip['id']}"
    move(c, slip_url, 'closed')
    status, slip_copy, _ = revise(c, slip_url)
    assert status == 201 and (slip['code'], slip_copy['code'], slip_copy['status']) == ('S1', 'S2', 'open')
    assert c('GET', slip_url)[1]['code'] == 'S1'


def test_void_and_revise_keep_and_renew_numbers(numbered):
    check_void_and_revise_keep_and_renew_numbers(numbered)


def check_a_caught_revise_failure_leaves_no_number(numbered):
    app, c, one, two = numbered
    with app.business.context(ADMIN) as context:
        failing = context.create(Slip, note='boom')
        context.move(Slip, failing['id'], 'closed')
        kept = context.create(Slip)
        context.move(Slip, kept['id'], 'closed')
    assert (failing['code'], kept['code']) == ('S1', 'S2')
    with app.business.context(ADMIN) as context:
        # The copy took S3 before its reaction failed; the savepoint gives the number back.
        with pytest.raises(ValidationError):
            context.revise(Slip, failing['id'], 'Wrong')
        assert context.create(Slip)['code'] == 'S3'
        assert context.revise(Slip, kept['id'], 'Wrong')['code'] == 'S4'
    assert c('GET', f"/api/slip/{failing['id']}")[1]['status'] == 'closed'
    assert c('GET', f"/api/slip/{kept['id']}")[1]['status'] == 'void'
    with app.database.transaction() as tx:
        codes = [row[0] for row in tx.connection.execute('SELECT code FROM slip ORDER BY id').fetchall()]
        assert tx.connection.execute('SELECT count(*) FROM slip WHERE revises=?', (failing['id'],)).fetchone()[0] == 0
    assert codes == ['S1', 'S2', 'S3', 'S4']


def test_a_caught_revise_failure_leaves_no_number(numbered):
    check_a_caught_revise_failure_leaves_no_number(numbered)


def check_only_the_engine_writes_a_sequence(numbered):
    app, c, one, two = numbered
    status, body, _ = c('POST', '/api/invoice', {'company': one, 'posting_date': '2026-03-10', 'number': 'INV-HAND'})
    assert status == 422 and body['fields'] == {'number': 'This number is assigned by the system'}
    record, url = invoice(c, one, number=None)
    move(c, url, 'issued')
    saved = move(c, url, 'draft')[1]['number']
    # Sending the saved value back is not a change; anything else is refused.
    assert c('PATCH', url, {'number': saved, 'name': 'Kept'}, headers=etag(c, url))[1]['name'] == 'Kept'
    assert c('PATCH', url, {'number': 'INV-HAND'}, headers=etag(c, url))[1]['fields'] == {'number': 'This number is assigned by the system'}
    assert c('POST', f'{url}/actions/renumber', {}, headers=etag(c, url))[1]['fields'] == {'number': 'This number is assigned by the system'}
    editor = add_user(app, 'editor')
    assert editor('PATCH', url, {'number': 'INV-HAND'}, headers=etag(editor, url))[0] == 422
    with app.business.context(ADMIN) as context:
        with pytest.raises(ValidationError):
            context.update(Invoice, record['id'], number='INV-HAND')
        with pytest.raises(ValidationError):
            context.create(Ticket, code='T99')
    database, ticket = app.database, c('POST', '/api/ticket', {})[1]
    with pytest.raises(ValueError, match='assigned by the engine'):
        with database.transaction() as tx:
            tx.update(Ticket, ticket['id'], code='T99')
    with database.transaction() as tx:
        # Sending the saved value back is allowed; maintenance() repairs are trusted.
        tx.update(Ticket, ticket['id'], code=ticket['code'], title='raw')
        with tx.maintenance():
            assert tx.update(Ticket, ticket['id'], code='T99').code == 'T99'
    with pytest.raises(ValueError, match='assigned by the engine'):
        with database.transaction() as tx:
            tx.create(Ticket, code='T99')
    assert c('GET', url)[1]['number'] == saved
    schema = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'invoice')
    field = next(field for field in schema['fields'] if field['name'] == 'number')
    assert (field['type'], field['read_only'], field['required'], field['sequence']) == ('sequence', True, False, {
        'format': 'INV-{year}-{n:05}', 'scope': ['company'], 'assign_on': 'issued', 'date': 'posting_date'})
    ticket_schema = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'ticket')
    assert ticket_schema['title_field'] == 'code'
    viewer = add_user(app, 'viewer')
    assert viewer('GET', url)[1]['number'] == saved


def test_only_the_engine_writes_a_sequence(numbered):
    check_only_the_engine_writes_a_sequence(numbered)


def check_assigned_numbers_are_unique_per_scope(numbered):
    app, c, one, two = numbered
    first, second = issue(c, one), issue(c, one)
    assert (first, second) == ('INV-2026-00001', 'INV-2026-00002')
    other_id, draft_id = invoice(c, two)[0]['id'], invoice(c, one)[0]['id']
    with app.database.transaction() as tx:
        rows = tx.list(Invoice, filters={'company': one}, order_by=('id',))
    with pytest.raises(ConflictError):
        with app.database.transaction() as tx, tx.maintenance():
            tx.update(Invoice, rows[1].id, number=first)
    # The same number may appear in another scope; null numbers never collide.
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Invoice, other_id, number=first)
    assert c('GET', f'/api/invoice/{other_id}')[1]['number'] == first
    assert c('GET', f'/api/invoice/{draft_id}')[1]['number'] is None


def test_assigned_numbers_are_unique_per_scope(numbered):
    check_assigned_numbers_are_unique_per_scope(numbered)


def check_parallel_writers_get_distinct_gapless_numbers(app, writers=8, each=6):
    def work(worker):
        made = []
        for step in range(each):
            try:
                with app.business.context(ADMIN) as context:
                    made.append(context.create(Ticket, title=f'{worker}-{step}')['code'])
                    if step % 3 == 2:
                        raise RuntimeError('rolled back')
            except RuntimeError:
                made.pop()
        return made
    with ThreadPoolExecutor(max_workers=writers) as pool:
        numbers = [number for made in pool.map(work, range(writers)) for number in made]
    committed = writers * (each - each // 3)
    assert sorted(numbers, key=lambda value: int(value[1:])) == [f'T{n}' for n in range(1, committed + 1)]
    with app.database.transaction() as tx:
        assert sorted(record.code for record in tx.list(Ticket, limit=100)) == sorted(numbers)


def test_parallel_writers_get_distinct_gapless_numbers(tmp_path):
    app = start(Database(tmp_path / 'parallel.sqlite3', Registry(*MODELS)))
    check_parallel_writers_get_distinct_gapless_numbers(app)


def test_counters_live_in_the_framework_table(numbered):
    app, c, one, two = numbered
    issue(c, one)
    issue(c, two, '2027-04-01')
    with app.database.transaction() as tx:
        rows = tx.connection.execute('SELECT model, field, scope_key, period, "last" FROM evlet_sequence ORDER BY scope_key').fetchall()
    assert [tuple(row) for row in rows] == [('invoice', 'number', f'[{int(RecordId(one))}]', '2026', 1), ('invoice', 'number', f'[{int(RecordId(two))}]', '2027', 1)]


def test_clear_for_copy_gives_a_copy_a_fresh_number():
    values = {'company': 1, 'number': 'INV-2026-00001', 'name': 'Copy'}
    assert clear_for_copy(Invoice, values) == {'company': 1, 'number': None, 'name': 'Copy'}
    assert values['number'] == 'INV-2026-00001' and clear_for_copy(Company, {'name': 'x'}) == {'name': 'x'}


# Migrations

def check_a_sequence_can_be_added_to_an_existing_table(make):
    class Ticket(Model):
        permissions = ALL
        title = Text(default='')
    database = make(Registry(Ticket), [])
    made = [database.create(Ticket, title=title).id for title in ('a', 'b', 'c')]
    with pytest.raises(SchemaError, match='without a backfill'):
        AddField('ticket', 'code', Sequence('T{n}'), backfill='T1').signature()

    class Ticket(Model):  # noqa: F811 - the upgraded definition
        permissions = ALL
        title = Text(default='')
        code = Sequence('T{n}')
    migration = Migration('0001_code', AddField('ticket', 'code', Sequence('T{n}')))
    upgraded = make(Registry(Ticket), [migration], migrate=True)
    assert [record.code for record in upgraded.list(Ticket)] == [None, None, None]
    app = start(upgraded)
    c = client(app)
    assert c('POST', '/api/ticket', {'title': 'new'})[1]['code'] == 'T1'
    with upgraded.transaction() as tx:
        assert backfill(tx, Ticket, 'code', filters={'title__in': ['a', 'b']}) == 2
    assert [record.code for record in upgraded.list(Ticket, order_by=('id',))] == ['T2', 'T3', None, 'T1']
    with pytest.raises(ConflictError):
        with upgraded.transaction() as tx, tx.maintenance():
            tx.update(Ticket, made[2], code='T1')
    return upgraded


def test_a_sequence_can_be_added_to_an_existing_table(tmp_path):
    def make(registry, migrations, migrate=False):
        database = Database(tmp_path / 'add.sqlite3', registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_a_sequence_can_be_added_to_an_existing_table(make)


def test_backfill_checks_its_arguments(tmp_path):
    database = Database(tmp_path / 'backfill.sqlite3', Registry(*MODELS))
    database.initialize()
    with database.transaction() as tx:
        with pytest.raises(ValueError, match='not a Sequence'):
            backfill(tx, Ticket, 'title')
        assert backfill(tx, Ticket, 'code') == 0
    with pytest.raises(ValueError, match='today'):
        backfill(None, type('Dated', (Model,), {'code': Sequence('{year}{n}')}), 'code')


def test_a_missing_sequence_index_is_reported(tmp_path):
    path = tmp_path / 'index.sqlite3'
    Database(path, Registry(*MODELS)).initialize()
    connection = sqlite3.connect(path)
    name = connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'evlet_ticket_code_sequence%'").fetchone()[0]
    sql = connection.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
    assert re.search(r'WHERE "code" IS NOT NULL$', sql)
    connection.execute(f'DROP INDEX "{name}"')
    connection.commit()
    connection.close()
    with pytest.raises(SchemaError, match='missing its unique index'):
        Database(path, Registry(*MODELS), migrations=[]).migrate([])


# Review findings

def check_a_caught_failure_leaves_no_trace(app):
    c = client(app)
    one = c('POST', '/api/company', {'name': 'One'})[1]['id']
    with app.business.context(ADMIN) as context:
        assert context.create(Pass, holder='Ann')['code'] == 'P1'
        # The unique holder fails after the number is taken; trusted code catches it and commits.
        with pytest.raises(ConflictError):
            context.create(Pass, holder='Ann')
        assert context.create(Pass, holder='Bob')['code'] == 'P2'
    draft = c('POST', '/api/invoice', {'company': one, 'posting_date': '2026-03-10'})[1]
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Invoice, draft['id'], name='boom')
    with app.business.context(ADMIN) as context:
        with pytest.raises(ValidationError):
            context.move(Invoice, draft['id'], 'issued')
        context.create(Ticket, title='after')
    saved = c('GET', f"/api/invoice/{draft['id']}")[1]
    assert (saved['status'], saved['number']) == ('draft', None)
    assert issue(c, one) == 'INV-2026-00001'
    assert sorted((record['code'], record['holder']) for record in c('GET', '/api/pass')[1]['items']) == [('P1', 'Ann'), ('P2', 'Bob')]


def test_a_caught_failure_leaves_no_trace(tmp_path):
    check_a_caught_failure_leaves_no_trace(start(Database(tmp_path / 'caught.sqlite3', Registry(*MODELS))))


def check_a_planted_number_is_skipped(numbered):
    app, c, one, two = numbered
    ids = [invoice(c, one)[0]['id'] for _ in range(3)]
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Invoice, ids[0], number='INV-2026-00001')
        tx.update(Invoice, ids[1], number='INV-2026-00002')
    # Another scope is not affected; this one moves past the numbers already held.
    assert issue(c, two) == 'INV-2026-00001'
    url = f'/api/invoice/{ids[2]}'
    assert move(c, url, 'issued')[1]['number'] == 'INV-2026-00003'
    assert issue(c, one) == 'INV-2026-00004'


def test_a_planted_number_is_skipped(numbered):
    check_a_planted_number_is_skipped(numbered)


def check_roles_that_cannot_read_the_number_learn_nothing(numbered):
    app, c, one, two = numbered
    visit = c('POST', '/api/visit', {'day': '2026-05-05'})[1]
    url = f"/api/visit/{visit['id']}"
    assert visit['code'] == 'V2026-1'
    editor = add_user(app, 'editor')
    right = editor('PATCH', url, {'code': 'V2026-1'}, headers=etag(editor, url))
    wrong = editor('PATCH', url, {'code': 'V2026-9'}, headers=etag(editor, url))
    assert right[:2] == wrong[:2] and right[0] == 403
    status, body, _ = editor('PATCH', url, {'day': '2027-01-01'}, headers=etag(editor, url))
    assert status == 422 and body['fields'] == {'day': 'This cannot change now'}
    assert c('PATCH', url, {'day': '2027-01-01'}, headers=etag(c, url))[1]['fields'] == {'day': 'This stays in the period of the assigned number'}


def test_roles_that_cannot_read_the_number_learn_nothing(numbered):
    check_roles_that_cannot_read_the_number_learn_nothing(numbered)


def check_raw_session_writes_number_and_keep_scope(numbered):
    app, c, one, two = numbered
    issue(c, one)
    record = c('GET', '/api/invoice')[1]['items'][0]
    with pytest.raises(ValidationError, match='fixed once the number is assigned'):
        with app.database.transaction() as tx:
            tx.update(Invoice, record['id'], company=two)
    with pytest.raises(ValidationError, match='period'):
        with app.database.transaction() as tx:
            tx.update(Invoice, record['id'], posting_date='2027-01-01')
    with app.database.transaction() as tx:
        assert tx.update(Invoice, record['id'], posting_date='2026-12-01').posting_date.month == 12
        # Raw creates number a Sequence that is due on create.
        assert tx.create(Ticket, title='raw').code == 'T1'
        assert tx.create(Invoice, company=one, posting_date='2026-01-01').number is None
        with tx.maintenance():
            assert tx.update(Invoice, record['id'], company=two).company == RecordId(two)
    assert c('POST', '/api/ticket', {})[1]['code'] == 'T2'


def test_raw_session_writes_number_and_keep_scope(numbered):
    check_raw_session_writes_number_and_keep_scope(numbered)


def check_a_scope_field_can_be_renamed(make):
    class Bill(Model):
        permissions = ALL
        branch = Text(required=True)
        code = Sequence('B{n}', scope=('branch',))
    database = make(Registry(Bill), [])
    app = start(database)
    assert client(app)('POST', '/api/bill', {'branch': 'North'})[1]['code'] == 'B1'

    class Bill(Model):  # noqa: F811 - the upgraded definition
        permissions = ALL
        site = Text(required=True)
        code = Sequence('B{n}', scope=('site',))
    migration = Migration('0001_site', RenameField('bill', 'branch', 'site'))
    upgraded = make(Registry(Bill), [migration], migrate=True)
    c = client(start(upgraded))
    assert c('POST', '/api/bill', {'site': 'North'})[1]['code'] == 'B2'
    assert c('POST', '/api/bill', {'site': 'South'})[1]['code'] == 'B1'


def test_a_scope_field_can_be_renamed(tmp_path):
    def make(registry, migrations, migrate=False):
        database = Database(tmp_path / 'rename.sqlite3', registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_a_scope_field_can_be_renamed(make)
