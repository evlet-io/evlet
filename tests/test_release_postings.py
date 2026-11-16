"""Postings in release hooks: @release posts rows to other models through self.context.session in the move's
transaction and @release.exit reverses them; a released record keeps its totals. On real SQLite through ASGI."""

import pytest

from evlet import (Application, Boolean, Child, Currency, Database, Decimal, Link, Model, Reference, Registry, Rule, State,
                   Table, Text, ValidationError, action, release, validate)
from evlet.auth import LocalAuth
from evlet.calc import F
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import ALL, start


class Account(Model):
    permissions = ALL
    title = Text(default='')


class LedgerEntry(Model):
    """An ordinary model: people read it, the invoice's hooks write it."""
    class Meta:
        api = 'read'
    permissions = {'admin': Rule(('read',)), 'editor': Rule(('read',)), 'viewer': Rule(())}
    account = Link(Account, required=True)
    debit = Currency(default=0)
    credit = Currency(default=0)
    memo = Text(default='')
    source = Reference(index=True)
    reversal = Boolean(default=False)


class StockMove(Model):
    """A plain model with its own checks, which a session write runs too."""
    item = Text(required=True)
    qty = Decimal()
    source = Reference(index=True)
    reversal = Boolean(default=False)

    @validate
    def movable(self):
        if self.item == 'forbidden':
            raise ValidationError({'item': 'This item cannot move'})


class InvoiceLine(Child):
    item = Text(default='')
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))


class Invoice(Model):
    Meta = FiniteMeta
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
    title = Text(default='')
    account = Link(Account)
    lines = Table(InvoiceLine)
    total = Currency(compute=lines.sum('amount'))
    note = Text(default='', versioned=False)
    docstate = State(
        State.Step('draft'),
        State.Step('issued', release=True),
        State.Step('overdue', release=True),
        State.Step('paid', final=True),
        State.Step('void', final=True),
        rules=(
            State.Rule('draft', 'issued', 'Issue', key='issued'),
            State.Rule('issued', 'overdue', 'Overdue', key='overdue', ask=('note',)),
            State.Rule(('issued', 'overdue'), 'paid', 'Mark paid', key='paid'),
            State.Rule(('issued', 'overdue'), 'draft', 'Reopen', key='reopen', by=('admin',), remark='required'),
            State.Rule(('issued', 'overdue'), 'void', 'Void', key='void', by=('admin', 'editor'), remark='required'),
        ))

    def ledger_rows(self):
        rows = [{'account': self.account, 'debit': line.amount, 'memo': line.item} for line in self.lines]
        return [{**row, 'source': ('invoice', self.id)} for row in [*rows, {'account': self.account, 'credit': self.total, 'memo': 'revenue'}]]

    @release
    def post(self):
        tx = self.context.session
        for row in self.ledger_rows():
            tx.create(LedgerEntry, **row)
        for line in self.lines:
            tx.create(StockMove, item=line.item, qty=-line.qty, source=('invoice', self.id))

    @release.exit
    def unpost(self):
        tx = self.context.session
        for row in self.ledger_rows():
            tx.create(LedgerEntry, **{**row, 'debit': row['credit'] if 'credit' in row else 0, 'credit': row.get('debit', 0), 'reversal': True})
        for line in self.lines:
            tx.create(StockMove, item=line.item, qty=line.qty, source=('invoice', self.id), reversal=True)


MODELS = (Account, LedgerEntry, StockMove, Invoice)


@pytest.fixture
def books(tmp_path):
    return seed(start(Database(tmp_path / 'db.sqlite3', Registry(*MODELS))))


def seed(app):
    c = client(app)
    account = c('POST', '/api/account', {'title': 'Sales'})[1]['id']
    return app, c, account


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def invoice(c, account, lines=(('a', '1', '0.10'), ('b', '2', '0.10')), **values):
    body = {'title': 'Invoice', 'account': account, **values,
            'children': {'lines': [{'item': item, 'qty': qty, 'rate': rate} for item, qty, rate in lines]}}
    status, record, _ = c('POST', '/api/invoice', body)
    assert status == 201, record
    return f"/api/invoice/{record['id']}"


def move(c, url, to, body=None):
    return c('POST', f'{url}/moves/{to}', body or {}, headers=etag(c, url))


def entries(app, table='ledgerentry'):
    with app.database.transaction() as tx:
        return [dict(row) for row in tx.connection.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()]


def ledger(app):
    """(debit, credit, reversal) per ledger row, in hundredths."""
    return [(row['debit'] // 10000, row['credit'] // 10000, row['reversal']) for row in entries(app)]


def test_entering_the_released_set_posts_in_the_move_transaction(books):
    app, c, account = books
    url = invoice(c, account)
    status, body, _ = move(c, url, 'issued')
    assert status == 200 and body['docstate'] == 'issued', body
    assert ledger(app) == [(10, 0, 0), (20, 0, 0), (0, 30, 0)]
    assert [(row['item'], row['qty'] // 10000) for row in entries(app, 'stockmove')] == [('a', -1), ('b', -2)]
    # The activity has the move only; the app's postings are ordinary rows.
    assert [item['kind'] for item in c('GET', url + '/activity')[1]['items']][:1] == ['moved']
    # Posted rows are ordinary records: Meta.api = "read" keeps callers out, the source index finds them.
    row = entries(app)[0]
    assert c('PATCH', f"/api/ledgerentry/{row['id']}", {'memo': 'x'})[0] in (403, 405)
    assert c('GET', '/api/ledgerentry?filters=' + '{"source":["invoice","%s"]}' % url.rsplit('/', 1)[1])[0] == 200


def test_a_failing_posting_rolls_the_whole_move_back(books):
    app, c, account = books
    url = invoice(c, account, lines=(('forbidden', '1', '1.00'),))
    status, body, _ = move(c, url, 'issued')
    assert status == 422 and c('GET', url)[1]['docstate'] == 'draft', body
    assert entries(app) == [] and entries(app, 'stockmove') == []


def test_exits_reverse_and_an_unchanged_reentry_posts_again(books):
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    # A move within the released set posts nothing and may change only versioned=False fields.
    assert move(c, url, 'overdue', {'note': 'Reminded'})[0] == 200 and len(entries(app)) == 3
    assert move(c, url, 'reopen', {'remark': 'Check'})[0] == 200
    assert ledger(app)[3:] == [(0, 10, 1), (0, 20, 1), (30, 0, 1)]
    # Nothing changed, but entering the set again posts again, so the books match the record.
    status, body, _ = move(c, url, 'issued')
    assert status == 200 and body['_version']['number'] == 0 and len(entries(app)) == 9
    assert move(c, url, 'void', {'remark': 'Wrong'})[0] == 200
    assert sum(row['debit'] - row['credit'] for row in entries(app)) == 0
    assert sum(row['qty'] for row in entries(app, 'stockmove')) == 0
    # A released record is never deleted.
    assert c('DELETE', url, headers=etag(c, url))[0] == 409


def test_meta_effects_is_removed():
    with pytest.raises(ValueError, match='Meta.effects was removed: post rows in a @release hook'):
        Registry(type('Old', (Model,), {'Meta': type('Meta', (), {'effects': ()}), 'title': Text()}))


# Released records keep their totals

def posting_models(factor):
    """A customer whose fee feeds a bill's total, and a bill that posts its total when issued."""
    class Customer(Model):
        __resource__ = 'customer'
        permissions = ALL
        fee = Currency(default=0)

    class Posting(Model):
        __resource__ = 'posting'
        permissions = {'admin': Rule(('read',))}
        amount = Currency(default=0)
        source = Reference(index=True)
        reversal = Boolean(default=False)

    class BillLine(Child):
        __resource__ = 'bill_lines'
        qty = Decimal(default=1, label='Quantity')
        rate = Currency(default=0)
        amount = Currency(compute=F('qty') * F('rate') * factor)
        note = Text(default='', versioned=False)

    class Bill(Model):
        __resource__ = 'bill'
        Meta = FiniteMeta
        permissions = {'admin': Rule(('read', 'create', 'update'))}
        customer = Link(Customer)
        lines = Table(BillLine)
        fee = Currency(compute=F('customer.fee'))
        total = Currency(compute=lines.sum('amount') + F('fee'))
        memo = Text(default='', versioned=False, index=True)
        charge = Currency(applies_when=F('docstate') == 'paid')
        charged = Currency(compute=F('charge'))
        docstate = State(
            State.Step('draft'),
            State.Step('approved', lock=True),
            State.Step('issued', release=True),
            State.Step('paid', final=True),
            State.Step('void', final=True),
            rules=(
                State.Rule('draft', 'approved', 'Approve', key='approved'),
                State.Rule(('draft', 'approved'), 'issued', 'Issue', key='issued'),
                State.Rule('issued', 'paid', 'Pay', key='paid'),
                State.Rule('issued', 'void', 'Void', key='void', by=('admin',), remark='required'),
            ))

        @release
        def post(self):
            self.context.session.create(Posting, amount=self.total, source=('bill', self.id))

        @release.exit
        def unpost(self):
            self.context.session.create(Posting, amount=-self.total, source=('bill', self.id), reversal=True)

        @action('Note', inputs={'memo': Text(required=True)})
        def note(self, memo):
            self.memo = memo

        @action('Bump')
        def bump(self):
            self.lines[0].qty = self.lines[0].qty + 10

        @action('Label line')
        def label_line(self):
            self.lines[0].note = 'checked'

    return Customer, Posting, Bill


def open_bills(path, factor):
    db = Database(path, Registry(*posting_models(factor)))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    return app, client(app)


def new_bill(c, customer):
    status, record, _ = c('POST', '/api/bill', {'customer': customer, 'children': {'lines': [{'qty': '2', 'rate': '1.50'}]}})
    assert status == 201, record
    return f"/api/bill/{record['id']}"


def posted(app):
    with app.database.transaction() as tx:
        return [row[0] // 10000 for row in tx.connection.execute('SELECT amount FROM posting ORDER BY id')]


def test_released_records_keep_their_totals_on_moves_and_actions(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(*posting_models(1))))
    c = client(app)
    customer = c('POST', '/api/customer', {'fee': '1'})[1]['id']
    url = new_bill(c, customer)
    assert move(c, url, 'issued')[1]['total'] == '4.00' and posted(app) == [400]
    assert c('PATCH', f'/api/customer/{customer}', {'fee': '10'}, headers=etag(c, f'/api/customer/{customer}'))[0] == 200
    status, body, _ = c('POST', url + '/actions/note', {'memo': 'called'}, headers=etag(c, url))
    assert status == 200 and (body['memo'], body['total'], body['fee']) == ('called', '4.00', '1.00'), body
    # A deploy that changes a formula: actions still work and keep the saved values; recalculating is refused.
    app.database.close()
    app, c = open_bills(tmp_path / 'db.sqlite3', 2)
    plan = app.business.registry.plan_for(app.business.registry.models['bill'])
    recalculate = {'_calculation': {'model_schema_hash': plan['model_schema_hash'], 'recalculate': True}}
    for status, body, _ in (c('POST', url + '/actions/note', {'memo': 'again', **recalculate}, headers=etag(c, url)),
                            c('PATCH', url, {'memo': 'again', **recalculate}, headers=etag(c, url))):
        assert status == 409 and body['error'].startswith('This record is released, so it keeps its saved results'), body
    # Actions inside the released set change only versioned=False fields.
    assert c('POST', url + '/actions/bump', {}, headers=etag(c, url))[1] == {'error': 'lines is part of the issued document; reopen it to change it'}
    status, body, _ = c('POST', url + '/actions/label_line', {}, headers=etag(c, url))
    assert status == 200 and body['children']['lines'][0]['note'] == 'checked', body
    # Leaving the set keeps the totals too: the exit may not change what they read, and reverses the same amount.
    status, body, _ = c('POST', f'{url}/moves/paid', {'charge': '5'}, headers=etag(c, url))
    assert (status, body['error']) == (409, 'Charge feeds released totals. To change it, move the record out of the released set first.'), body
    status, body, _ = move(c, url, 'paid')
    assert status == 200 and (body['docstate'], body['total']) == ('paid', '4.00') and posted(app) == [400, -400], body


def test_draft_preview_of_a_locked_or_released_record_shows_the_kept_results(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(*posting_models(1))))
    c = client(app)
    customer = c('POST', '/api/customer', {'fee': '1'})[1]['id']
    approved, issued, draft = new_bill(c, customer), new_bill(c, customer), new_bill(c, customer)
    assert move(c, approved, 'approved')[0] == 200 and move(c, issued, 'issued')[0] == 200
    assert c('PATCH', f'/api/customer/{customer}', {'fee': '10'}, headers=etag(c, f'/api/customer/{customer}'))[0] == 200
    previews = {}
    for url in (approved, issued, draft):
        status, body, _ = c('POST', '/api/_draft/bill', {'id': url.rsplit('/', 1)[1], 'values': {'memo': 'x'}})
        assert status == 200, body
        previews[url] = (body['values']['fee'], body['values']['total'])
    assert previews == {approved: ('1.00', '4.00'), issued: ('1.00', '4.00'), draft: ('10.00', '13.00')}
