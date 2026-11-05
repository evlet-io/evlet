"""Phase 5: void and revise, their permissions, details, schema and migration, on real SQLite."""

import hashlib
import json

import pytest

from evlet import (AddField, Boolean, Child, Currency, Database, Decimal, Integer, Link, Migration, Model, Move, Registry,
                   Rule, SchemaError, State, Table, Text, ValidationError)
from evlet import corrections
from evlet.auth import AccessError, User
from evlet.calc import F
from evlet.identity import RecordId
from evlet.permissions import Policy
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import ALL, add_user, start

CHANGES = []


class Account(Model):
    permissions = ALL
    title = Text(default='')
    limit = Currency(default=0)


class Note(Child):
    text = Text(default='')


class QuoteLine(Child):
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))
    memo = Text(default='', editable_when_locked=True)
    notes = Table(Note)


class RevisableMeta(FiniteMeta):
    revisable = True


def lifecycle():
    return State(('draft', 'issued', 'paid', 'void'), moves=(
        Move('draft', 'issued', 'Issue', when=F('total') > 0),
        Move('issued', 'paid', 'Mark paid'),
    ), locked=('issued', 'paid', 'void'), final=('paid', 'void'), voidable=('issued',))


class Quote(Model):
    Meta = RevisableMeta
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise')),
                   'editor': Rule(('read', 'create', 'update', 'void', 'revise'), scope=lambda user: {'owner': user.id}),
                   'viewer': Rule(('read',), read_fields=('title', 'total'))}
    title = Text(default='')
    # Unique values are cleared on a revision.
    code = Text(unique=True)
    owner = Integer(default=0)
    account = Link(Account)
    # Suggested from the account; a revision keeps the value the original had.
    heading = Text(initial=F('account.title'))
    # A stored result over a linked record: a locked or voided quote keeps it, a revision recalculates it.
    cap = Currency(compute=F('account.limit'))
    lines = Table(QuoteLine)
    total = Currency(compute=lines.sum('amount'))
    status = lifecycle()

    def changed(self, change):
        CHANGES.append((change.operation, self.id, self.status))


class Order(Model):
    """Voidable but not revisable; no Rule names void, so only admin may void it."""
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='')
    status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),),
                   locked=('issued', 'void'), final=('void',), voidable=('issued',))


class Ticket(Model):
    """Ungoverned (no permissions, tables or sources): every role keeps its default operations."""
    title = Text(default='')
    status = State(('open', 'closed', 'void'), moves=(Move('open', 'closed', 'Close'),),
                   locked=('closed', 'void'), final=('void',), voidable=('closed',))


MODELS = (Account, Quote, Order, Ticket)


@pytest.fixture
def quotes(tmp_path):
    return seed(start(Database(tmp_path / 'db.sqlite3', Registry(*MODELS))))


def seed(app):
    c = client(app)
    account = c('POST', '/api/account', {'title': 'Acme', 'limit': '50'})[1]['id']
    return app, c, account


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def post(c, url, operation, body, headers=None):
    return c('POST', f'{url}/{operation}', body, headers=etag(c, url) if headers is None else headers)


def new_quote(c, account, **values):
    body = {'title': 'Quote', 'account': account, 'children': {'lines': [
        {'_key': 'a', 'qty': '2', 'rate': '5', 'memo': 'first', 'children': {'notes': [{'_key': 'n1', 'text': 'x'}, {'_key': 'n2', 'text': 'y'}]}},
        {'_key': 'b', 'qty': '1', 'rate': '3'}]}, **values}
    status, record, _ = c('POST', '/api/quote', body)
    assert status == 201, record
    return record, f"/api/quote/{record['id']}"


def issued_quote(c, account, **values):
    record, url = new_quote(c, account, **values)
    status, moved, _ = c('POST', f'{url}/moves/issued', {}, headers=etag(c, url))
    assert status == 200 and moved['status'] == 'issued', moved
    return moved, url


def rows(app, model):
    with app.database.transaction() as tx:
        return tx.connection.execute(f'SELECT * FROM {model} ORDER BY id').fetchall()


# Declarations

def test_void_and_revise_declarations_are_checked():
    for options in ({'voidable': ('b',), 'locked': ('b',)},
                    {'voidable': ('b',), 'locked': ('b', 'void')},
                    {'voidable': ('b',), 'locked': ('b',), 'final': ('void',)}):
        with pytest.raises(ValueError, match='"void" state'):
            State(('a', 'b', 'void'), **options)
    State(('a', 'b', 'void'), locked=('b', 'void'), final=('void',), voidable=('b',))
    State(('a', 'b'), locked=('b',))  # without voidable states, no void state is needed

    def model(class_name, meta=RevisableMeta, **namespace):
        return type(class_name, (Model,), {'Meta': meta, **namespace})
    with pytest.raises(ValueError, match='voidable states'):
        model('NoState', title=Text())
    with pytest.raises(ValueError, match='voidable states'):
        model('NoVoid', status=State(('a', 'b'), locked=('b',)))
    for reserved in ('revises', 'revised_by'):
        with pytest.raises(ValueError, match='reserved'):
            model('Taken', status=lifecycle(), **{reserved: Text()})
    with pytest.raises(ValueError, match='True or False'):
        model('Odd', meta=type('Meta', (), {'revisable': 'yes'}), status=lifecycle())
    # With voidable states, "void" is reached only through void(), never through a declared move.
    with pytest.raises(ValueError, match='goes to "void"'):
        State(('a', 'b', 'void'), moves=(Move('b', 'void', 'Kill'),), locked=('b', 'void'), final=('void',), voidable=('b',))
    State(('a', 'void'), moves=(Move('a', 'void'),), locked=('void',), final=('void',))  # without voidable states it is a move
    # A revision clears unique values, so a revisable document cannot require one, on the record or its rows.
    with pytest.raises(ValueError, match='Taken.code is unique and required'):
        model('Taken', status=lifecycle(), code=Text(unique=True, required=True))
    row = type('Row', (Child,), {'code': Text(unique=True, required=True)})
    with pytest.raises(ValueError, match='Row.code is unique and required'):
        model('Rows', status=lifecycle(), rows=Table(row))
    field = Quote.__declared_fields__['revises']
    assert isinstance(field, Link) and field.target is Quote and field.index and field.read_only and field.label == 'Revises'
    assert Quote.__revisable__ and not Order.__revisable__ and 'revises' not in Order.__declared_fields__
    # A subclass gets its own field, linking to its own table.
    later = type('Later', (Quote,), {'storage_name': 'later'})
    assert later.__declared_fields__['revises'] is not field and later.__declared_fields__['revises'].target is later
    registry = Registry(*MODELS)
    with pytest.raises(ValueError, match="'revise' without 'create'"):
        Policy(registry, rules={('editor', Quote): Rule(('read', 'update', 'revise'))})
    with pytest.raises(ValueError, match='cannot be voided'):
        Policy(registry, rules={('admin', Account): Rule(('read', 'void'))})
    with pytest.raises(ValueError, match='cannot be revised'):
        Policy(registry, rules={('admin', Order): Rule(('read', 'create', 'revise'))})
    with pytest.raises(ValueError, match='read access'):
        Policy(registry, rules={('admin', Order): Rule(('void',))})


def test_admin_keeps_corrections_and_other_permissions_are_unchanged():
    from examples.playground.models import registry as playground
    import test_states

    def table(registry, skip=()):
        policy, result = Policy(registry), {}
        for name, model in sorted(registry.models.items()):
            if name in skip:
                continue
            for role in ('admin', 'editor', 'viewer'):
                user = User(1, 'x', role)
                result[f'{role}:{name}'] = [[op for op in ('read', 'create', 'update', 'delete') if policy.allows(user, model, op)],
                                            sorted(policy.fields(user, model)), sorted(policy.fields(user, model, write=True)),
                                            policy.rule(user, model).scope is not None]
        return result
    # Recorded before void and revise existed: every create, read, update, delete and field permission is the same.
    # Re-recorded when the Playground added Customer.currency and Payment.currency (0004_currencies); without
    # those two fields the table hashes to the earlier 58ecf53f... value. Re-recorded again when Invoice.number
    # (0006_invoice_number) was added: readable by every role, writable by none; without it the table hashes to
    # the earlier cf1f61c2... value.
    # The Playground's Item and its tables (0005_items), Employee (0007_employees) and LedgerEntry (0009_ledger) came later with permissions of their own; left out, the
    # models above keep exactly the recorded table.
    # Re-recorded for the standard record name and title (0008_record_names): every record model gains a
    # readable `name` and `title`; Customer.name became customer_name, Project.name the declared title,
    # Invoice.number moved into the named (and so writable) standard name, and test_states renamed its
    # own name fields to title. Before this the table hashed to b1bb34ff....
    # Re-recorded when Invoice.project (0010_invoice_project) was added: an ordinary Link every role reads
    # and admin and editor write, like Invoice.customer. Without it the table hashes to 19b87059....
    data = {'playground': table(playground, ('item', 'item_prices', 'item_barcodes', 'employee', 'ledgerentry')), 'states': table(Registry(*test_states.MODELS))}
    assert hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() == \
        '6db2817902ab2156d697698970e4073d8771e5a1044a91527612ba46447fae88'
    policy = Policy(Registry(*MODELS))
    assert dict(policy.corrections) == {(Quote, 'void'): {'admin', 'editor'}, (Quote, 'revise'): {'admin', 'editor'},
                                        (Order, 'void'): {'admin'}, (Ticket, 'void'): {'admin'}}
    assert not Policy(Registry(*test_states.MODELS)).corrections
    # A Rule naming void for another role adds that role; admin keeps it.
    only_editor = Policy(Registry(*MODELS), rules={('editor', Order): Rule(('read', 'update', 'void'))})
    assert only_editor.corrections[Order, 'void'] == {'admin', 'editor'}
    # Admin keeps them only while it may read and update the model (and create, for revise).
    assert not Policy(Registry(*MODELS), rules={('admin', Order): Rule(())}).corrections[Order, 'void']
    assert not Policy(Registry(*MODELS), rules={('admin', Order): Rule(('read',))}).corrections[Order, 'void']
    limited = Policy(Registry(*MODELS), rules={('admin', Quote): Rule(('read', 'update'))})
    assert limited.corrections[Quote, 'void'] == {'admin', 'editor'} and limited.corrections[Quote, 'revise'] == {'editor'}


# Void

def check_void(app):
    app, c, account = app
    record, url = new_quote(c, account)
    assert c('POST', f'{url}/void', {'reason': 'x'})[0] == 428
    assert post(c, url, 'void', {'reason': 'Wrong'}, headers={b'if-match': b'"stale"'})[0] == 409
    assert post(c, url, 'void', {'reason': 'Wrong'})[1] == {'error': 'This record is draft, which cannot be voided'}
    issued, url = issued_quote(c, account)
    for body, message in (({}, 'Give a reason'), ({'reason': '  '}, 'Give a reason'), ({'reason': 5}, 'Give a reason'),
                          ({'reason': 'x' * 1001}, 'Use at most 1000 characters')):
        assert post(c, url, 'void', body)[1]['fields'] == {'reason': message}
    assert post(c, url, 'void', {'reason': 'x', 'status': 'draft'})[1]['fields'] == {'_form': 'Send only a reason'}
    # A linked value changes after the quote was locked: the saved result stays, through the void too.
    c('PATCH', f'/api/account/{account}', {'limit': '75'}, headers=etag(c, f'/api/account/{account}'))
    before = c('GET', url)
    with app.database.transaction() as tx:
        version = tx.connection.execute('SELECT plan_hash FROM evlet_calculation_versions WHERE record_id=? AND resource=?',
                                        (RecordId(issued['id']), 'quote')).fetchone()[0]
    status, voided, headers = post(c, url, 'void', {'reason': '  Duplicate  '})
    assert status == 200 and voided['status'] == 'void' and headers[b'etag'] != before[2][b'etag']
    assert {key: value for key, value in voided.items() if not key.startswith('_') and key != 'status'} == \
        {key: value for key, value in before[1].items() if not key.startswith('_') and key != 'status'}
    assert voided['cap'] == '50.00' and voided['total'] == '13.00' and voided['revised_by'] is None
    assert voided['_moves'] == [] and voided['_locked']
    assert voided['_void'] == {'allowed': False, 'reason': 'This record cannot be voided in its current state'}
    # A void record without a revision may still be revised.
    assert voided['_revise'] == {'allowed': True}
    assert c('GET', url)[2][b'etag'] == headers[b'etag']
    with app.database.transaction() as tx:
        assert tx.connection.execute('SELECT plan_hash FROM evlet_calculation_versions WHERE record_id=? AND resource=?',
                                     (RecordId(issued['id']), 'quote')).fetchone()[0] == version
    assert post(c, url, 'void', {'reason': 'Again'})[1] == {'error': 'This record is void, which cannot be voided'}
    # A voided record is locked and final: no ordinary write, delete or move.
    assert c('PATCH', url, {'title': 'x'}, headers=etag(c, url))[1]['fields'] == {'title': 'This record is void, so it is locked'}
    assert c('DELETE', url, headers=etag(c, url))[0] == 409
    assert c('POST', f'{url}/moves/paid', {}, headers=etag(c, url))[0] == 409


def test_void_checks_state_reason_revision_and_keeps_saved_values(quotes):
    check_void(quotes)


def test_void_and_revise_routes_and_non_correctable_models(quotes):
    app, c, account = quotes
    _, url = issued_quote(c, account)
    assert c('GET', f'{url}/void')[0] == 405
    assert c('POST', '/api/quote/99/void', {'reason': 'x'}, headers={b'if-match': b'"x"'})[0] == 404
    assert c('POST', '/api/quote/0/revise', {'reason': 'x'})[0] == 404
    # Missing records and models that cannot be corrected answer 404 before the body is checked.
    assert c('POST', '/api/quote/99/void', {'reason': 'x', 'extra': 1}, headers={b'if-match': b'"x"'})[0] == 404
    assert c('POST', f'/api/account/{account}/void', {'extra': 1}, headers={b'if-match': b'"x"'})[0] == 404
    assert c('POST', f'/api/account/{account}/void', {'reason': 'x'}, headers=etag(c, f'/api/account/{account}'))[1] == \
        {'error': 'This record cannot be voided'}
    order = c('POST', '/api/order', {'title': 'O'})[1]
    order_url = f"/api/order/{order['id']}"
    assert c('POST', f'{order_url}/moves/issued', {}, headers=etag(c, order_url))[0] == 200
    assert post(c, order_url, 'revise', {'reason': 'x'})[1] == {'error': 'This record cannot be revised'}
    assert c('GET', order_url)[1]['status'] == 'issued' and '_revise' not in c('GET', order_url)[1]
    assert post(c, order_url, 'void', {'reason': 'x'})[1]['status'] == 'void'


# Revise

def check_revise(app):
    app, c, account = app
    issued, url = issued_quote(c, account, owner=0, code='Q-1')
    c('PATCH', url, {'children': {'lines': {'update': [{'id': issued['children']['lines'][0]['id'], 'memo': 'kept while locked'}]}}},
      headers=etag(c, url))
    original = c('GET', url)[1]
    # The account changes after the quote was issued.
    c('PATCH', f'/api/account/{account}', {'title': 'Renamed', 'limit': '80'}, headers=etag(c, f'/api/account/{account}'))
    assert post(c, url, 'revise', {'reason': ''})[1]['fields'] == {'reason': 'Give a reason'}
    status, copy, headers = post(c, url, 'revise', {'reason': 'Price change'})
    assert status == 201, copy
    assert copy['_original'] == issued['id'] and headers[b'location'] == f"/api/quote/{copy['id']}".encode()
    assert copy['id'] != issued['id'] and copy['status'] == 'draft' and copy['revises'] == issued['id'] and copy['code'] is None
    assert copy['revised_by'] is None and not copy['_locked'] and copy['_void']['allowed'] is False
    # Source values are copied; the initial suggestion keeps the original's value; results are recalculated.
    assert (copy['title'], copy['heading'], copy['account'], copy['total'], copy['cap']) == ('Quote', 'Acme', account, '13.00', '80.00')

    def shape(lines):
        return [(row['qty'], row['rate'], row['amount'], row['memo'], [note['text'] for note in row['children']['notes']]) for row in lines]
    assert shape(copy['children']['lines']) == shape(original['children']['lines'])
    def ids(lines):
        return {('line', row['id']) for row in lines} | {('note', note['id']) for row in lines for note in row['children']['notes']}
    old_ids, new_ids = ids(original['children']['lines']), ids(copy['children']['lines'])
    assert not old_ids & new_ids and len(new_ids) == len(old_ids) == 4
    with app.database.transaction() as tx:
        positions = {owner: [row[0] for row in tx.connection.execute(
            'SELECT evlet_position FROM quote_lines WHERE evlet_owner=? ORDER BY evlet_position, id', (owner,)).fetchall()]
            for owner in (RecordId(issued['id']), RecordId(copy['id']))}
    assert positions[RecordId(issued['id'])] == positions[RecordId(copy['id'])] and positions[RecordId(copy['id'])]
    # The original is void and links to its revision; it is otherwise unchanged.
    voided = c('GET', url)[1]
    assert voided['status'] == 'void' and voided['revised_by'] == copy['id'] and voided['code'] == 'Q-1'
    assert (voided['cap'], voided['heading'], voided['children']) == (original['cap'], original['heading'], original['children'])
    assert post(c, url, 'revise', {'reason': 'Again'})[1] == {'error': 'This record already has a revision'}
    assert voided['_revise'] == {'allowed': False, 'reason': 'This record already has a revision'}
    # The copy is an ordinary draft: editable, and it can be issued and revised in turn.
    copy_url = f"/api/quote/{copy['id']}"
    edited = c('PATCH', copy_url, {'title': 'Quote v2', 'children': {'lines': {'delete': [copy['children']['lines'][1]['id']]}}},
               headers=etag(c, copy_url))[1]
    assert edited['title'] == 'Quote v2' and edited['total'] == '10.00'
    assert c('PATCH', copy_url, {'revises': None}, headers=etag(c, copy_url))[0] == 403
    assert c('POST', f'{copy_url}/moves/issued', {}, headers=etag(c, copy_url))[1]['status'] == 'issued'
    third = post(c, copy_url, 'revise', {'reason': 'Second change'})[1]
    assert third['revises'] == copy['id'] and c('GET', copy_url)[1]['revised_by'] == third['id']
    # Ordinary creates cannot set the link.
    assert c('POST', '/api/quote', {'title': 'x', 'revises': issued['id']})[0] == 403
    # When the revision draft is deleted, the void original may be revised again; it stays void.
    third_url = f"/api/quote/{third['id']}"
    assert c('DELETE', third_url, headers=etag(c, third_url))[0] == 204
    again = c('GET', copy_url)[1]
    assert again['status'] == 'void' and again['revised_by'] is None and again['_revise'] == {'allowed': True}
    fourth = post(c, copy_url, 'revise', {'reason': 'Draft was deleted'})
    assert fourth[0] == 201 and fourth[1]['revises'] == copy['id'] and c('GET', copy_url)[1]['status'] == 'void'
    # A record voided without a revision may be revised later too.
    _, plain = issued_quote(c, account)
    post(c, plain, 'void', {'reason': 'Stopped'})
    assert post(c, plain, 'revise', {'reason': 'Restarted'})[0] == 201
    # revised_by is found through the index on revises.
    if type(app.database) is Database:  # PostgreSQL checks its plan in test_postgres.py
        with app.database.transaction() as tx:
            plan = ' '.join(str(tuple(row)) for row in tx.connection.execute(
                'EXPLAIN QUERY PLAN SELECT id FROM "quote" WHERE "revises"=? ORDER BY id LIMIT 1', (1,)).fetchall())
        assert 'USING INDEX' in plan or 'USING COVERING INDEX' in plan, plan


def test_revise_copies_values_and_rows_and_links_both_records(quotes):
    check_revise(quotes)


def test_revise_keeps_order_of_saved_rows(quotes):
    app, c, account = quotes
    record, url = new_quote(c, account)
    lines = [row['id'] for row in record['children']['lines']]
    c('PATCH', url, {'children': {'lines': {'create': [{'_key': 'z', 'qty': '1', 'rate': '1'}], 'order': ['z', *reversed(lines)]}}},
      headers=etag(c, url))
    assert c('POST', f'{url}/moves/issued', {}, headers=etag(c, url))[0] == 200
    before = [row['rate'] for row in c('GET', url)[1]['children']['lines']]
    copy = post(c, url, 'revise', {'reason': 'Reorder kept'})[1]
    assert [row['rate'] for row in copy['children']['lines']] == before == ['1.00', '3.00', '5.00']


# Permissions

def check_permissions(app):
    app, c, account = app
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')
    editor_id = 2
    _, mine = issued_quote(c, account, owner=editor_id)
    _, other = issued_quote(c, account, owner=1)
    # Row scope applies: a record outside the editor's scope is not found.
    assert post(editor, other, 'void', {'reason': 'x'}, headers={b'if-match': b'"x"'})[0] == 404
    assert viewer('POST', f'{mine}/void', {'reason': 'x'}, headers=etag(viewer, mine))[0] == 403
    detail = editor('GET', mine)[1]
    assert detail['_void'] == detail['_revise'] == {'allowed': True}
    viewer_detail = viewer('GET', mine)[1]
    assert '_void' not in viewer_detail and '_revise' not in viewer_detail and 'revised_by' not in viewer_detail
    copy = post(editor, mine, 'revise', {'reason': 'Mine'})
    assert copy[0] == 201 and copy[1]['owner'] == editor_id
    # Order: no Rule names void, so only admin may void it.
    order = c('POST', '/api/order', {'title': 'O'})[1]
    order_url = f"/api/order/{order['id']}"
    c('POST', f'{order_url}/moves/issued', {}, headers=etag(c, order_url))
    assert post(editor, order_url, 'void', {'reason': 'x'})[1] == {'error': 'You do not have permission for this operation'}
    assert editor('GET', order_url)[1]['_void'] == {'allowed': False, 'reason': 'Your role cannot void this record'}
    assert c('GET', order_url)[1]['_void'] == {'allowed': True}
    # Ungoverned models: editors keep create, update and delete; void is still admin only.
    ticket = editor('POST', '/api/ticket', {'title': 'T'})[1]
    ticket_url = f"/api/ticket/{ticket['id']}"
    editor('POST', f'{ticket_url}/moves/closed', {}, headers=etag(editor, ticket_url))
    assert post(editor, ticket_url, 'void', {'reason': 'x'})[0] == 403
    assert post(c, ticket_url, 'void', {'reason': 'x'})[1]['status'] == 'void'
    # A role that may void but cannot read the State gets messages without state names, and no availability.
    business = app.business
    business.policy = Policy(business.registry, rules={('editor', Order): Rule(('read', 'update', 'void'), read_fields=('title',))})
    draft = c('POST', '/api/order', {'title': 'D'})[1]
    draft_url = f"/api/order/{draft['id']}"
    assert post(editor, draft_url, 'void', {'reason': 'x'})[1] == {'error': 'This record cannot be voided now'}
    assert '_void' not in editor('GET', draft_url)[1]
    assert post(editor, order_url, 'void', {'reason': 'x'})[0] == 200
    second = c('POST', '/api/order', {'title': 'S'})[1]
    second_url = f"/api/order/{second['id']}"
    c('POST', f'{second_url}/moves/issued', {}, headers=etag(c, second_url))
    assert post(c, second_url, 'void', {'reason': 'x'})[0] == 200  # admin keeps void


def test_void_and_revise_permissions_row_scope_and_messages(quotes):
    check_permissions(quotes)


# Schema and Python API

def test_schema_reports_voidable_revisable_and_the_revises_field(quotes):
    app, c, account = quotes
    viewer = add_user(app, 'viewer')
    models = {model['name']: model for model in c('GET', '/api/schema')[1]['models']}
    quote, order, account_entry = models['quote'], models['order'], models['account']
    assert (quote['voidable'], quote['revisable'], order['voidable'], order['revisable']) == (True, True, True, False)
    assert 'voidable' not in account_entry and 'revisable' not in account_entry
    assert quote['permissions'] == {'create': True, 'update': True, 'delete': True, 'void': True, 'revise': True}
    assert order['permissions'] == {'create': True, 'update': True, 'delete': True, 'void': True}
    assert account_entry['permissions'] == {'create': True, 'update': True, 'delete': True}
    revises = next(field for field in quote['fields'] if field['name'] == 'revises')
    assert revises['type'] == 'link' and revises['target'] == 'quote' and revises['read_only'] and revises['index']
    viewer_quote = next(model for model in viewer('GET', '/api/schema')[1]['models'] if model['name'] == 'quote')
    # The viewer cannot read the State, so it sees no correction metadata at all.
    assert 'voidable' not in viewer_quote and 'revisable' not in viewer_quote and 'void' not in viewer_quote['permissions']
    assert 'revises' not in [field['name'] for field in viewer_quote['fields']]


def test_python_context_void_and_revise(quotes):
    app, c, account = quotes
    issued, url = issued_quote(c, account)
    with pytest.raises(ValidationError) as error:
        with app.business.context(User(1, 'admin', 'admin')) as context:
            context.void(Quote, issued['id'], None)
    assert error.value.errors == {'reason': 'Give a reason'}
    with pytest.raises(AccessError):
        with app.business.context(User(1, 'admin', 'admin')) as context:
            context.create(Quote, title='x', revises=issued['id'])
    with pytest.raises(AccessError, match='read only'):
        with app.business.context(User(1, 'admin', 'admin'), readonly=True) as context:
            context.void(Quote, issued['id'], 'x')
    CHANGES.clear()
    with app.business.context(User(1, 'admin', 'admin')) as context:
        copy = context.revise('quote', issued['id'], 'From Python')
        assert context.get(Quote, issued['id']).revised_by == copy['id']
    assert CHANGES == [('void', RecordId(issued['id']), 'void'), ('revise', copy['id'], 'draft')]
    assert copy['revises'] == RecordId(issued['id']) and len(copy['children']['lines']) == 2
    _, second = issued_quote(c, account)
    with app.business.context(User(1, 'admin', 'admin')) as context:
        voided = context.void(Quote, RecordId(second.rsplit('/', 1)[1]), 'From Python')
    assert voided['status'] == 'void' and voided['revised_by'] is None


# Rollback

def check_rollback(app, monkeypatch):
    app, c, account = app
    issued, url = issued_quote(c, account)
    before = c('GET', url)
    counts = {table: len(rows(app, table)) for table in ('quote', 'quote_lines', 'quote_lines_notes')}

    def fail(*args):
        raise ValidationError({'_form': 'Hook failed'})
    for operation, hook in (('void', 'reverse_effects'), ('void', 'voided'), ('revise', 'reverse_effects'),
                            ('revise', 'prepare_copy'), ('revise', 'voided'), ('revise', 'revised')):
        with monkeypatch.context() as patch:
            patch.setattr(corrections, hook, fail)
            assert post(c, url, operation, {'reason': 'x'})[1] == {'error': 'Validation failed', 'fields': {'_form': 'Hook failed'}}
        assert c('GET', url)[2][b'etag'] == before[2][b'etag']
        assert {table: len(rows(app, table)) for table in counts} == counts
    # A copy that fails validation rolls the void back too.
    with monkeypatch.context() as patch:
        patch.setattr(Quote, 'validate', lambda self: (_ for _ in ()).throw(ValidationError({'title': 'Rejected'})), raising=False)
        assert post(c, url, 'revise', {'reason': 'x'})[1] == {'error': 'Validation failed', 'fields': {'title': 'Rejected'}}
    assert c('GET', url)[1]['status'] == 'issued' and {table: len(rows(app, table)) for table in counts} == counts


def test_any_failure_inside_void_or_revise_rolls_everything_back(quotes, monkeypatch):
    check_rollback(quotes, monkeypatch)


# Migration

def check_revises_migration(make):
    """Turning on Meta.revisable adds a column, so an existing table needs AddField like any field."""
    def memo(revisable):
        meta = type('Meta', (), {'revisable': revisable})
        return type('Memo', (Model,), {'Meta': meta, '__module__': __name__, 'name': Text(default=''),
                                       'status': State(('draft', 'sent', 'void'), moves=(Move('draft', 'sent', 'Send'),),
                                                       locked=('sent', 'void'), final=('void',), voidable=('sent',))})
    old = memo(False)
    database = make(Registry(old), [])
    database.create(old, name='Existing')
    new = memo(True)
    with pytest.raises(SchemaError):
        make(Registry(new), [])
    with pytest.raises(SchemaError, match='do not match'):
        make(Registry(new), [Migration('0001_other', AddField('memo', 'other', Text(default='')))], migrate=True)
    migration = Migration('0001_revises', AddField('memo', 'revises', Link(new, index=True)))
    upgraded = make(Registry(new), [migration], migrate=True)
    record = upgraded.list(new)[0]
    assert record.revises is None and record.name == 'Existing'
    return upgraded


def test_revisable_needs_an_add_field_migration(tmp_path):
    def make(registry, migrations, migrate=False):
        database = Database(tmp_path / 'memo.sqlite3', registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_revises_migration(make)


# Revise is refused up front when the caller's copy could not be created

FULL = ('read', 'create', 'update', 'delete')


class Vendor(Model):
    permissions = {'admin': Rule(FULL), 'editor': Rule(('read',), scope=lambda user: {'public': True}), 'viewer': Rule(('read',))}
    title = Text(default='')
    public = Boolean(default=True)


class Part(Child):
    permissions = {'admin': Rule(FULL), 'editor': Rule(FULL, scope=lambda user: {'region': 'east'}), 'viewer': Rule(('read',))}
    region = Text(default='east')


class Bill(Model):
    class Meta:
        revisable = True
    permissions = {'admin': Rule((*FULL, 'void', 'revise')), 'editor': Rule(('read', 'create', 'update', 'revise')),
                   'viewer': Rule(('read',))}
    title = Text(default='')
    vendor = Link(Vendor)
    parts = Table(Part)
    status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),),
                   locked=('issued', 'void'), final=('void',), voidable=('issued',))


def check_revise_is_predicted(app):
    app, c = app
    editor = add_user(app, 'editor')
    public = c('POST', '/api/vendor', {'title': 'Open'})[1]['id']
    private = c('POST', '/api/vendor', {'title': 'Closed', 'public': False})[1]['id']

    def bill(vendor, regions):
        record = c('POST', '/api/bill', {'title': 'B', 'vendor': vendor, 'children': {'parts': [{'region': region} for region in regions]}})[1]
        url = f"/api/bill/{record['id']}"
        assert c('POST', f'{url}/moves/issued', {}, headers=etag(c, url))[0] == 200
        return url
    hidden_rows = 'You cannot see every row of this record, so you cannot revise it'
    hidden_link = 'This record links to a record you cannot read, so you cannot revise it'
    for url, reason in ((bill(public, ('east', 'west')), hidden_rows), (bill(private, ('east',)), hidden_link)):
        before = editor('GET', url)
        assert before[1]['_revise'] == {'allowed': False, 'reason': reason}
        assert post(editor, url, 'revise', {'reason': 'x'})[:2] == (403, {'error': reason})
        assert editor('GET', url)[2][b'etag'] == before[2][b'etag']
        # Admin sees every row and vendor, so it may revise the same record.
        assert c('GET', url)[1]['_revise'] == {'allowed': True}
    url = bill(public, ('east', 'east'))
    assert editor('GET', url)[1]['_revise'] == {'allowed': True}
    business = app.business
    rows = business.registry.models['bill_parts']
    business.policy = Policy(business.registry, rules={('editor', rows): Rule(('read', 'update'))})
    no_rows = 'Your role cannot add the rows of this record, so you cannot revise it'
    assert editor('GET', url)[1]['_revise'] == {'allowed': False, 'reason': no_rows}
    assert post(editor, url, 'revise', {'reason': 'x'})[1] == {'error': no_rows}
    business.policy = Policy(business.registry)
    status, copy, _ = post(editor, url, 'revise', {'reason': 'x'})
    assert status == 201 and [row['region'] for row in copy['children']['parts']] == ['east', 'east']


def test_revise_is_refused_up_front_when_the_copy_would_leave_the_callers_scope(tmp_path):
    app = start(Database(tmp_path / 'bill.sqlite3', Registry(Vendor, Bill)))
    check_revise_is_predicted((app, client(app)))
