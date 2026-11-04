"""Unpost and repost of posted documents, and Recompute of posted records on request, on real SQLite and PostgreSQL."""

import pytest

from evlet import (Application, Boolean, Child, Currency, Database, Decimal, Effect, Journal, Link, LocalAuth, Model, Move, Negate, Policy, Reference,
                   Registry, Rule, Sequence, State, Swap, Table, Text, effects, journal)
from evlet.auth import AccessError, User
from evlet.calc import F
from evlet.identity import RecordId, encode
from evlet.postgres import PostgreSQLDatabase
from finite_fixtures import FiniteMeta
from test_business import client, http
from test_effects import etag, move, posting_models, new_bill, open_bills
from test_postgres import pg_dsn  # noqa: F401 - fixture
from test_rules_types import ALL, add_user, start


class Account(Model):
    permissions = ALL
    title = Text(default='')


class Ledger(Model):
    permissions = {'admin': Rule(('read',)), 'editor': Rule(('read',)), 'viewer': Rule(())}
    account = Link(Account, required=True)
    debit = Currency(default=0)
    credit = Currency(default=0)
    memo = Text(default='')
    source = Reference()
    reversal = Boolean(default=False)


class Line(Child):
    item = Text(default='')
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))


class InvoiceMeta(FiniteMeta):
    journal = Journal(tamper_evident=True)
    effects = (Effect(Ledger, rows='ledger_rows', on='issued', reverse=Swap('debit', 'credit'), balanced=('debit', 'credit')),)


class Invoice(Model):
    Meta = InvoiceMeta
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void')), 'editor': Rule(('read', 'create', 'update', 'void')),
                   'viewer': Rule(('read',))}
    title = Text(default='')
    account = Link(Account)
    number = Sequence('INV-{n:04}', assign_on='issued')
    # Takes this much off the credit row, so a test can make a repost fail its balance check.
    skew = Currency(default=0)
    lines = Table(Line)
    total = Currency(compute=lines.sum('amount'))
    status = State(('draft', 'issued', 'paid', 'void'), moves=(Move('draft', 'issued', 'Issue'), Move('issued', 'paid', 'Mark paid')),
                   locked=('issued', 'paid', 'void'), final=('paid', 'void'), voidable=('issued',))

    def ledger_rows(self):
        rows = [{'account': self.account, 'debit': line.amount, 'memo': line.item} for line in self.lines]
        return rows + [{'account': self.account, 'credit': self.total - self.skew, 'memo': 'revenue'}]


class Payment(Model):
    permissions = ALL
    invoice = Link(Invoice)
    amount = Currency(default=0)
    status = State(('draft', 'done', 'void'), moves=(Move('draft', 'done', 'Confirm'),), locked=('done', 'void'), final=('void',),
                   voidable=('done',))


class Allocation(Model):
    """Matches a ledger entry an invoice posted, or (through a Reference) any record."""
    permissions = ALL
    entry = Link(Ledger)
    about = Reference()
    status = State(('draft', 'done', 'void'), moves=(Move('draft', 'done', 'Confirm'),), locked=('done', 'void'), final=('void',),
                   voidable=('done',))


class Tally(Model):
    permissions = {'admin': Rule(('read',)), 'editor': Rule(('read',))}
    amount = Currency(default=0)
    source = Reference()
    reversal = Boolean(default=False)


class Memo(Model):
    """Posted by a move only admin may use."""
    Meta = type('Meta', (), {'effects': (Effect(Tally, rows='rows', on='sent', reverse=Negate('amount')),)})
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update', 'unpost', 'repost'))}
    status = State(('draft', 'sent'), moves=(Move('draft', 'sent', 'Send', roles=('admin',)),), locked=('sent',))

    def rows(self):
        return [{'amount': '1'}]


class Order(Model):
    """Its posting move asks for a value it requires."""
    Meta = type('Meta', (), {'effects': (Effect(Tally, rows='rows', on='sent', reverse=Negate('amount')),)})
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    ref = Text(required_when=F('status') == 'sent')
    status = State(('draft', 'sent'), moves=(Move('draft', 'sent', 'Send'),), locked=('sent',))

    def rows(self):
        return [{'amount': '2'}]


MODELS = (Account, Ledger, Invoice, Payment, Allocation, Tally, Memo, Order)


@pytest.fixture(params=['sqlite', 'postgresql'])
def books(request, tmp_path):
    registry = Registry(*MODELS)
    if request.param == 'sqlite':
        database = Database(tmp_path / 'books.sqlite3', registry)
    else:
        database = PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry)
    app = start(database)
    c = client(app)
    account = c('POST', '/api/account', {'title': 'Sales'})[1]['id']
    return app, c, account


@pytest.fixture
def sqlite_books(tmp_path):
    app = start(Database(tmp_path / 'books.sqlite3', Registry(*MODELS)))
    c = client(app)
    return app, c, c('POST', '/api/account', {'title': 'Sales'})[1]['id']


def new_invoice(c, account, lines=(('a', '1', '0.10'), ('b', '2', '0.10'))):
    body = {'title': 'Invoice', 'account': account, 'children': {'lines': [{'item': item, 'qty': qty, 'rate': rate} for item, qty, rate in lines]}}
    status, record, _ = c('POST', '/api/invoice', body)
    assert status == 201, record
    return f"/api/invoice/{record['id']}"


def issued_invoice(c, account, **kwargs):
    url = new_invoice(c, account, **kwargs)
    status, body, _ = move(c, url, 'issued')
    assert status == 200, body
    return url


def correct(c, url, operation, body=None, **kwargs):
    return c('POST', f'{url}/{operation}', {'reason': 'Wrong customer'} if body is None else body, headers=etag(c, url), **kwargs)


def ledger(app):
    with app.database.transaction() as tx:
        return [(row['debit'], row['credit'], bool(row['reversal'])) for row in
                tx.connection.execute('SELECT debit, credit, reversal FROM ledger ORDER BY id').fetchall()]


def audit_rows(app):
    with app.database.transaction() as tx:
        return [(row['actor'], row['area'], row['target'], row['detail']) for row in
                tx.connection.execute("SELECT actor, area, target, detail FROM evlet_audit WHERE area='Records' ORDER BY id").fetchall()]


def kinds(c, url):
    return [item['kind'] for item in c('GET', f'{url}/journal')[1]['items']]


# Unpost and repost

def test_unpost_cancels_postings_keeps_the_number_and_repost_posts_again(books):
    app, c, account = books
    url = issued_invoice(c, account)
    issued = c('GET', url)[1]
    assert issued['number'] == 'INV-0001' and issued['_unpost'] == {'allowed': True} and '_repost' not in issued
    assert ledger(app) == [(10, 0, False), (20, 0, False), (0, 30, False)]

    status, body, _ = correct(c, url, 'unpost', {'reason': '  Wrong customer  '})
    assert status == 200, body
    assert (body['status'], body['number'], body['total'], body['id']) == ('draft', 'INV-0001', '0.30', issued['id'])
    assert body['_effects'] == [{'target': 'ledger', 'rows': 3, 'reversals': 3, 'balanced': True}]
    assert body['_repost'] == {'allowed': True} and '_unpost' not in body and not body['_locked']
    # Nothing is deleted: each row gets a mirror row that cancels it.
    assert ledger(app) == [(10, 0, False), (20, 0, False), (0, 30, False), (0, 10, True), (0, 20, True), (30, 0, True)]
    items = c('GET', f'{url}/journal')[1]['items']
    assert [item['kind'] for item in items[:2]] == ['effects', 'unposted']
    assert items[1]['data'] == {'from': 'issued', 'to': 'draft', 'reason': 'Wrong customer'} and items[1]['actor'] == 1
    assert items[0]['data'] == {'targets': [{'target': 'ledger', 'rows': 3, 'reversal': True, 'balanced': True}]}
    assert audit_rows(app) == [('admin', 'Records', f"invoice {issued['id']}", 'Unposted from issued to draft · Wrong customer')]
    assert effects.check(app) == []

    # The record is editable again and keeps its number.
    line = body['children']['lines'][0]
    status, body, _ = c('PATCH', url, {'children': {'lines': {'update': [{'id': line['id'], 'qty': '3'}]}}}, headers=etag(c, url))
    assert status == 200 and body['total'] == '0.50' and body['number'] == 'INV-0001', body

    status, body, _ = correct(c, url, 'repost', {})
    assert status == 200, body
    assert (body['status'], body['number'], body['total']) == ('issued', 'INV-0001', '0.50')
    assert body['_effects'] == [{'target': 'ledger', 'rows': 6, 'reversals': 3, 'balanced': True}]
    assert body['_unpost'] == {'allowed': True} and '_repost' not in body
    assert ledger(app)[6:] == [(30, 0, False), (20, 0, False), (0, 50, False)]
    assert kinds(c, url)[:3] == ['reposted', 'effects', 'moved']
    assert c('GET', f'{url}/journal')[1]['items'][0]['data'] == {'to': 'issued', 'move': 'Issue'}
    assert audit_rows(app)[-1] == ('admin', 'Records', f"invoice {issued['id']}", 'Reposted by Issue')
    assert effects.check(app) == []
    # Numbers stay gapless: the repost took none, so the next invoice gets the next one.
    assert c('GET', issued_invoice(c, account))[1]['number'] == 'INV-0002'

    # A void now reverses only the live rows; earlier postings were cancelled by the unpost.
    assert correct(c, url, 'void')[0] == 200
    assert ledger(app)[-3:] == [(0, 30, True), (0, 20, True), (50, 0, True)] and len(ledger(app)) == 15
    assert effects.check(app) == []
    entries, breaks = journal.verify(app.database)
    assert entries > 0 and breaks == []


def test_unpost_is_refused_while_a_live_record_depends_on_it(books):
    app, c, account = books
    url = issued_invoice(c, account)
    invoice_id = url.rsplit('/', 1)[1]
    status, payment, _ = c('POST', '/api/payment', {'invoice': invoice_id, 'amount': '0.30'})
    assert status == 201
    payment_url = f"/api/payment/{payment['id']}"
    assert move(c, payment_url, 'done')[0] == 200
    before = (ledger(app), kinds(c, url))
    status, body, _ = correct(c, url, 'unpost')
    assert (status, body) == (409, {'error': f"This record cannot be unposted while Payment {payment['id']} depends on it. "
                                             'Void or unpost it first.'})
    assert (ledger(app), kinds(c, url)) == before and c('GET', url)[1]['status'] == 'issued'
    # A void payment no longer holds on to the invoice.
    assert correct(c, payment_url, 'void')[0] == 200
    status, body, _ = correct(c, url, 'unpost')
    assert status == 200 and body['status'] == 'draft', body


def test_dependents_are_named_only_when_the_caller_can_open_them(sqlite_books):
    app, c, account = sqlite_books
    url = issued_invoice(c, account)
    invoice_id = url.rsplit('/', 1)[1]
    payments = []
    for _ in range(2):
        payment = c('POST', '/api/payment', {'invoice': invoice_id})[1]
        payments.append(payment['id'])
        assert move(c, f"/api/payment/{payment['id']}", 'done')[0] == 200
    assert correct(c, url, 'unpost')[1]['error'] == \
        f'This record cannot be unposted while Payment {payments[0]} and Payment {payments[1]} depend on it. Void or unpost them first.'
    # An editor given unpost who cannot read payments is told how many records depend on it, not which.
    from evlet.documents import Context
    rules = {('editor', Invoice): Rule(('read', 'create', 'update', 'unpost')), ('editor', Payment): Rule(())}
    app.business.policy = Policy(app.business.registry, rules=rules)
    with app.database.transaction() as tx:
        context = Context(app.business, tx, User(2, 'editor', 'editor'))
        with pytest.raises(AccessError) as caught:
            context.unpost(Invoice, RecordId(invoice_id), 'Wrong')
    assert caught.value.message == 'This record cannot be unposted while 2 records you cannot open depend on it. Void or unpost them first.'


def test_unpost_and_repost_check_the_record_and_the_request(sqlite_books):
    app, c, account = sqlite_books
    draft = new_invoice(c, account)
    assert c('POST', f'{draft}/unpost', {'reason': 'x'})[0] == 428
    assert correct(c, draft, 'unpost')[1] == {'error': 'This record has nothing posted to cancel'}
    assert correct(c, draft, 'repost')[1] == {'error': 'This record was never posted. Use its moves to post it'}
    url = issued_invoice(c, account)
    for body, message in (({}, 'Give a reason'), ({'reason': '  '}, 'Give a reason'), ({'reason': 'x' * 1001}, 'Use at most 1000 characters')):
        assert correct(c, url, 'unpost', body)[1]['fields'] == {'reason': message}
    assert correct(c, url, 'unpost', {'reason': 'x', 'status': 'draft'})[1]['fields'] == {'_form': 'Send only a reason'}
    assert correct(c, url, 'repost')[1] == {'error': 'This record is posted already'}
    assert c('POST', f'{url}/unpost', {'reason': 'x'}, headers={b'if-match': b'"stale"'})[0] == 409
    assert c('GET', f'{url}/unpost')[0] == 405
    # Models without effects have no unpost.
    payment = c('POST', '/api/payment', {})[1]['id']
    assert correct(c, f'/api/payment/{payment}', 'unpost')[:2] == (404, {'error': 'This record cannot be unposted'})
    # A void record has nothing live to unpost and cannot be reposted.
    assert correct(c, url, 'void')[0] == 200
    assert correct(c, url, 'unpost')[1] == {'error': 'This record is void, so it cannot be unposted'}
    assert correct(c, url, 'repost')[1] == {'error': 'This record is void, so it cannot be reposted'}
    detail = c('GET', url)[1]
    assert '_unpost' not in detail and '_repost' not in detail
    # A paid invoice (final) can be unposted too; a repost reason is optional but recorded when given.
    paid = issued_invoice(c, account)
    assert move(c, paid, 'paid')[0] == 200
    assert correct(c, paid, 'unpost')[1]['status'] == 'draft'
    assert correct(c, paid, 'repost', {'reason': 'Customer fixed'})[1]['status'] == 'issued'
    assert c('GET', f'{paid}/journal')[1]['items'][0]['data'] == {'to': 'issued', 'move': 'Issue', 'reason': 'Customer fixed'}
    assert effects.check(app) == []


def test_only_admin_and_roles_a_rule_names_may_unpost_or_repost(sqlite_books):
    from test_api_keys import create, with_key
    app, c, account = sqlite_books
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')
    url = issued_invoice(c, account)
    for caller in (editor, viewer):
        detail = caller('GET', url)[1]
        assert '_unpost' not in detail and '_repost' not in detail
        assert correct(caller, url, 'unpost')[0] == 403
    # The schema tells each role what it may do.
    schema = {role: next(model for model in caller('GET', '/api/schema')[1]['models'] if model['name'] == 'invoice')
              for role, caller in (('admin', c), ('editor', editor))}
    assert schema['admin']['unpostable'] and schema['admin']['permissions']['unpost'] and schema['admin']['permissions']['repost']
    assert not schema['editor']['permissions']['unpost'] and not schema['editor']['permissions']['repost']
    # API keys: a read key of the admin and the editor's write key are refused; the admin's write key works.
    read = create(c, 'Read', {'access': 'read', 'models': None})[1]['token']
    write = create(c, 'Write', {'access': 'write', 'models': None})[1]['token']
    editor_key = create(c, 'Editor', {'access': 'write', 'models': None}, user='editor')[1]['token']
    tag = c('GET', url)[2][b'etag']
    def keyed(token, operation):
        return http(app, 'POST', f'{url}/{operation}', {'reason': 'Script'},
                    headers={b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b'', b'if-match': tag})[0]
    assert keyed(read, 'unpost') == 403 and keyed(editor_key, 'unpost') == 403
    assert keyed(write, 'unpost') == 200
    tag = c('GET', url)[2][b'etag']
    assert keyed(editor_key, 'repost') == 403 and keyed(write, 'repost') == 200
    # Trusted Python code gets the same checks.
    from evlet.documents import Context
    with app.database.transaction() as tx:
        with pytest.raises(AccessError) as caught:
            Context(app.business, tx, User(2, 'editor', 'editor')).unpost(Invoice, RecordId(url.rsplit('/', 1)[1]), 'x')
    assert caught.value.status == 403


def test_policy_keeps_unpost_and_repost_for_admin_and_adds_named_roles():
    registry = Registry(*MODELS)
    policy = Policy(registry)
    assert policy.corrections[Invoice, 'unpost'] == {'admin'} and policy.corrections[Invoice, 'repost'] == {'admin'}
    assert (Payment, 'unpost') not in policy.corrections
    named = Policy(registry, rules={('editor', Invoice): Rule(('read', 'update', 'unpost'))})
    assert named.corrections[Invoice, 'unpost'] == {'admin', 'editor'} and named.corrections[Invoice, 'repost'] == {'admin'}
    # Admin keeps them only while its Rule reads and updates the model, like void.
    assert not Policy(registry, rules={('admin', Invoice): Rule(('read',))}).corrections[Invoice, 'unpost']
    with pytest.raises(ValueError, match="Rule names 'unpost', but the model cannot be unposted"):
        Policy(registry, rules={('editor', Payment): Rule(('read', 'update', 'unpost'))})
    # Controls lists them as operations an override may keep or take away.
    from evlet.controls import OPERATIONS
    assert {'unpost', 'repost'} <= set(OPERATIONS)


def test_a_failed_unpost_or_repost_leaves_nothing_behind(books, monkeypatch):
    from evlet import corrections
    app, c, account = books
    url = issued_invoice(c, account)
    record_id = RecordId(url.rsplit('/', 1)[1])
    before = (ledger(app), kinds(c, url), audit_rows(app))

    def broken(*args, **kwargs):
        raise RuntimeError('journal is down')
    monkeypatch.setattr(corrections, 'unposted', broken)
    from evlet.documents import Context
    # Trusted code that catches the failure and commits still leaves no half-done unpost.
    with app.database.transaction() as tx:
        context = Context(app.business, tx, User(1, 'admin', 'admin'))
        with pytest.raises(RuntimeError):
            context.unpost(Invoice, record_id, 'Wrong')
    assert (ledger(app), kinds(c, url), audit_rows(app)) == before and c('GET', url)[1]['status'] == 'issued'
    monkeypatch.undo()

    # A repost that fails its checks rolls back the move, its number and its rows.
    assert correct(c, url, 'unpost')[0] == 200
    assert c('PATCH', url, {'skew': '0.01'}, headers=etag(c, url))[0] == 200
    before = (ledger(app), kinds(c, url), audit_rows(app))
    status, body, _ = correct(c, url, 'repost', {})
    assert status == 422 and 'not balanced' in body['fields']['_effects'], body
    assert (ledger(app), kinds(c, url), audit_rows(app)) == before
    detail = c('GET', url)[1]
    assert (detail['status'], detail['number']) == ('draft', 'INV-0001')
    assert effects.check(app) == []


def test_unpost_returns_to_the_state_the_record_posted_from(tmp_path):
    # Bill posts on "issued", entered from "draft" or "approved": the Journal tells which.
    app = start(Database(tmp_path / 'db.sqlite3', Registry(*posting_models(1))))
    c = client(app)
    customer = c('POST', '/api/customer', {})[1]['id']
    direct, approved = new_bill(c, customer), new_bill(c, customer)
    assert move(c, approved, 'approved')[0] == 200
    for url in (direct, approved):
        assert move(c, url, 'issued')[0] == 200
    assert correct(c, direct, 'unpost')[1]['status'] == 'draft'
    assert correct(c, approved, 'unpost')[1]['status'] == 'approved'
    # From "approved" one move posts; from "draft" two lead on, but only Issue posts.
    assert correct(c, approved, 'repost')[1]['status'] == 'issued'
    assert correct(c, direct, 'repost')[1]['status'] == 'issued'
    assert effects.check(app) == []


# Recompute

def test_recompute_includes_posted_records_only_when_asked(tmp_path, caplog):
    from evlet import AddIndex, Migration, Recompute
    path, base = tmp_path / 'db.sqlite3', Migration('0000_base', AddIndex('bill', 'memo'))
    app = start(Database(path, Registry(*posting_models(1)), migrations=[base]))
    c = client(app)
    customer = c('POST', '/api/customer', {})[1]['id']
    issued, unposted, voided = new_bill(c, customer), new_bill(c, customer), new_bill(c, customer)
    for url in (issued, unposted, voided):
        assert move(c, url, 'issued')[0] == 200
    assert correct(c, unposted, 'unpost')[0] == 200
    assert correct(c, voided, 'void')[0] == 200
    app.database.close()
    first = Migration('0001_recompute', Recompute('bill_lines', ('amount',)), Recompute('bill', ('total',)))
    # Off by default: the posted bill keeps its results; the unposted one has nothing live and is recalculated.
    db = Database(path, Registry(*posting_models(2)), migrations=[base, first])
    applied = db.migrate(db.migrations)
    assert (applied.skipped, applied.posted) == (2, 0)
    db.close()
    app, c = open_bills(path, 2)
    assert [c('GET', url)[1]['total'] for url in (issued, unposted)] == ['3.00', '6.00']
    assert 'recomputed' not in kinds(c, issued) and kinds(c, unposted)[0] == 'recomputed'
    app.database.close()
    # An admin's explicit choice: the posted bill is recalculated and its entry says so; so is the audit log.
    second = Migration('0002_recompute', Recompute('bill_lines', ('amount',)), Recompute('bill', ('total',)))
    db = Database(path, Registry(*posting_models(3)), migrations=[base, first, second])
    with caplog.at_level('WARNING', logger='evlet.migrations'):
        applied = db.migrate(db.migrations, recompute_posted=True)
    # A void record keeps its results even then: its rows and their reversal must keep matching it.
    assert (applied.skipped, applied.posted) == (1, 1)
    assert 'changed the saved results of 1 posted records' in caplog.text
    db.close()
    app, c = open_bills(path, 3)
    assert [c('GET', url)[1]['total'] for url in (issued, unposted, voided)] == ['9.00', '9.00', '3.00']
    assert 'recomputed' not in kinds(c, voided)
    entry = c('GET', f'{issued}/journal')[1]['items'][0]
    assert entry['kind'] == 'recomputed' and entry['actor'] is None and entry['data']['posted'] is True
    assert entry['data']['fields'] == {'total': {'old': '3.00', 'new': '9.00'}}
    assert 'posted' not in c('GET', f'{unposted}/journal')[1]['items'][0]['data']
    assert audit_rows(app) == [('admin', 'Records', f"bill {unposted.rsplit('/', 1)[1]}", 'Unposted from issued to draft · Wrong customer'),
                               ('cli', 'Records', 'bill', 'Recompute changed 1 posted record, as asked')]
    # Its saved rows no longer match: the check says so, and an unpost and repost brings them in line.
    assert [item['kind'] for item in effects.check(app)] == ['changed']
    assert correct(c, issued, 'unpost')[0] == 200
    # Unposted, it is an ordinary draft: it recalculates under the active formulas before it posts again.
    plan = app.business.registry.plan_for(app.business.registry.models['bill'])
    recalculate = {'_calculation': {'model_schema_hash': plan['model_schema_hash'], 'recalculate': True}}
    assert correct(c, issued, 'repost')[1]['error'].startswith('SCHEMA_VERSION_MISMATCH')
    assert c('PATCH', issued, {'memo': 'checked', **recalculate}, headers=etag(c, issued))[0] == 200
    status, body, _ = correct(c, issued, 'repost')
    assert status == 200 and body['total'] == '9.00', body
    assert effects.check(app) == []


def test_recompute_posted_on_postgresql(pg_dsn):
    from evlet import AddIndex, Migration, Recompute
    base = Migration('0000_base', AddIndex('bill', 'memo'))
    database = PostgreSQLDatabase(pg_dsn, Registry(*posting_models(1)), migrations=[base])
    app = start(database)
    c = client(app)
    customer = c('POST', '/api/customer', {})[1]['id']
    url = new_bill(c, customer)
    assert move(c, url, 'issued')[0] == 200
    step = Migration('0001_recompute', Recompute('bill_lines', ('amount',)), Recompute('bill', ('total',)))
    database.close()
    database = PostgreSQLDatabase(pg_dsn, Registry(*posting_models(2)), migrations=[base, step])
    applied = database.migrate(database.migrations, recompute_posted=True)
    assert (applied.skipped, applied.posted) == (0, 1)
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    app.initialize()
    c = client(app)
    assert c('GET', url)[1]['total'] == '6.00'
    assert c('GET', f'{url}/journal')[1]['items'][0]['data']['posted'] is True


def test_migrate_command_takes_the_recompute_posted_flag(monkeypatch, capsys, tmp_path):
    import importlib
    installer, cli = importlib.import_module('evlet.cli.install'), importlib.import_module('evlet.cli.main')
    seen = []

    def install(root, name, **kwargs):
        seen.append((name, kwargs['recompute_posted']))
        return {'apps': ['books'], 'migrations': ['0002_recompute'], 'backup': None, 'posted': 3}
    monkeypatch.setattr(installer, 'install', install)
    monkeypatch.setattr(cli, 'project_root', lambda project: tmp_path)
    cli.main(['migrate', 'acme', '--trust'])
    assert 'Posted records' not in capsys.readouterr().out
    cli.main(['migrate', 'acme', '--trust', '--recompute-posted'])
    assert 'Posted records recalculated: 3' in capsys.readouterr().out
    assert seen == [('acme', False), ('acme', True)]


# Review findings

def test_a_record_that_ever_posted_is_never_deleted(books):
    from test_api_keys import create
    app, c, account = books
    draft = new_invoice(c, account)
    assert '_deletable' not in c('GET', draft)[1]
    url = issued_invoice(c, account)
    assert c('GET', url)[1]['_deletable'] is False
    assert correct(c, url, 'unpost')[1]['_deletable'] is False
    before = ledger(app)
    status, body, _ = c('DELETE', url, headers=etag(c, url))
    assert (status, body) == (409, {'error': 'This record has posted before, so it cannot be deleted. Repost it and void it instead.'})
    # An API key and trusted Python code are refused the same way.
    write = create(c, 'Write', {'access': 'write', 'models': None})[1]['token']
    keyed = http(app, 'DELETE', url, None, headers={b'authorization': f'Bearer {write}'.encode(), b'x-evlet-request': b'',
                                                     b'if-match': c('GET', url)[2][b'etag']})
    assert keyed[0] == 409
    from evlet.documents import Context
    with app.database.transaction() as tx:
        with pytest.raises(AccessError) as caught:
            Context(app.business, tx, User(1, 'admin', 'admin')).delete(Invoice, RecordId(url.rsplit('/', 1)[1]))
    assert caught.value.status == 409
    detail = c('GET', url)[1]
    assert (detail['status'], detail['number'], ledger(app)) == ('draft', 'INV-0001', before)
    # A record that never posted is deleted as before.
    assert c('DELETE', draft, headers=etag(c, draft))[0] == 204


def test_records_linking_to_posted_entries_or_rows_block_an_unpost(books):
    app, c, account = books
    url = issued_invoice(c, account)
    invoice = c('GET', url)[1]
    with app.database.transaction() as tx:
        entry = tx.connection.execute('SELECT id FROM ledger WHERE source_id=? ORDER BY id LIMIT 1', (RecordId(invoice['id']),)).fetchone()[0]
    allocation = c('POST', '/api/allocation', {'entry': encode(entry)})[1]['id']
    assert move(c, f'/api/allocation/{allocation}', 'done')[0] == 200
    message = f'This record cannot be unposted while Allocation {allocation} depends on it. Void or unpost it first.'
    assert correct(c, url, 'unpost')[1] == {'error': message}
    assert correct(c, f'/api/allocation/{allocation}', 'void')[0] == 200
    # A Reference to one of its Table rows holds on to it too.
    line = invoice['children']['lines'][0]['id']
    row_model = app.business.registry.name_for(next(child.model for child in app.business.registry.children if child.parent is Invoice))
    other = c('POST', '/api/allocation', {'about': {'model': row_model, 'id': line}})[1]['id']
    assert move(c, f'/api/allocation/{other}', 'done')[0] == 200
    assert correct(c, url, 'unpost')[1]['error'].startswith(f'This record cannot be unposted while Allocation {other} depends')
    assert correct(c, f'/api/allocation/{other}', 'void')[0] == 200
    assert correct(c, url, 'unpost')[0] == 200


def test_repost_is_offered_only_when_the_caller_could_run_the_posting_move(sqlite_books):
    app, c, _ = sqlite_books
    editor = add_user(app, 'editor')
    memo = f"/api/memo/{c('POST', '/api/memo', {})[1]['id']}"
    assert move(c, memo, 'sent')[0] == 200
    # The editor may unpost (a Rule names it), but Send is admin's, so it is not offered repost.
    assert correct(editor, memo, 'unpost')[0] == 200
    assert '_repost' not in editor('GET', memo)[1] and c('GET', memo)[1]['_repost'] == {'allowed': True}
    assert correct(editor, memo, 'repost', {})[0] == 403
    assert correct(c, memo, 'repost', {})[0] == 200
    # A posting move that asks for a required value is the way to post again; repost refuses and is not offered.
    order = f"/api/order/{c('POST', '/api/order', {})[1]['id']}"
    assert c('POST', f'{order}/moves/sent', {'ref': 'PO-1'}, headers=etag(c, order))[0] == 200
    assert correct(c, order, 'unpost')[0] == 200
    assert '_repost' not in c('GET', order)[1]
    assert correct(c, order, 'repost', {})[1] == {'error': 'Send asks for values, so post the record with Send itself'}
    # A Rule may not give repost without update.
    with pytest.raises(ValueError, match="'repost' without 'update'"):
        Policy(app.business.registry, rules={('editor', Memo): Rule(('read', 'repost'))})
