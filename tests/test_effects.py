"""Phase 6: effects posted on a move, reversed on void, read-only target rows and the check command, on real SQLite."""

from decimal import Decimal as D

import pytest

from evlet import (Application, Boolean, Child, Count, Currency, Database, Decimal, Effect, Integer, Link, Model, Move, Negate, Reference, Registry,
                   Rule, Sequence, State, Sum, Swap, Table, Text, ValidationError, action, effects)
from evlet.auth import AccessError, LocalAuth
from evlet.calc import F
from evlet.identity import RecordId
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import ALL, add_user, start


class Account(Model):
    permissions = ALL
    title = Text(default='')


class LedgerEntry(Model):
    # Editors read entries without amounts; viewers cannot read them at all.
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read',), read_fields=('account', 'memo', 'source', 'reversal')), 'viewer': Rule(())}
    account = Link(Account, required=True)
    debit = Currency(default=0)
    credit = Currency(default=0)
    memo = Text(default='')
    source = Reference()
    reversal = Boolean(default=False)


class StockMove(Model):
    """A plain model: validated by its fields and validate()."""
    item = Text(required=True)
    qty = Decimal()
    source = Reference()
    reversal = Boolean(default=False)

    def validate(self):
        if self.item == 'forbidden':
            raise ValidationError({'item': 'This item cannot move'})


class InvoiceLine(Child):
    item = Text(default='')
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))


class InvoiceMeta(FiniteMeta):
    revisable = True
    effects = (
        Effect(LedgerEntry, rows='ledger_rows', on='issued', reverse=Swap('debit', 'credit'), balanced=('debit', 'credit')),
        Effect('stockmove', rows='stock_rows', on='issued', reverse=Negate('qty')),
    )


class Invoice(Model):
    Meta = InvoiceMeta
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise')),
                   'editor': Rule(('read', 'create', 'update', 'void')), 'viewer': Rule(('read',))}
    title = Text(default='')
    account = Link(Account)
    # Takes this much off the credit row, so a test can post an unbalanced batch.
    skew = Currency(default=0)
    lines = Table(InvoiceLine)
    total = Currency(compute=lines.sum('amount'))
    status = State(('draft', 'issued', 'paid', 'void'), moves=(Move('draft', 'issued', 'Issue'), Move('issued', 'paid', 'Mark paid')),
                   locked=('issued', 'paid', 'void'), final=('paid', 'void'), voidable=('issued',))

    def ledger_rows(self):
        rows = [{'account': self.account, 'debit': line.amount, 'memo': line.item} for line in self.lines]
        return rows + [{'account': self.account, 'credit': self.total - self.skew, 'memo': 'revenue'}]

    def stock_rows(self):
        return [{'item': line.item, 'qty': -line.qty} for line in self.lines]


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


def move(c, url, to):
    return c('POST', f'{url}/moves/{to}', {}, headers=etag(c, url))


def entries(app, table='ledgerentry'):
    with app.database.transaction() as tx:
        return [dict(row) for row in tx.connection.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()]


# Declarations

def target(**fields):
    return type('Target', (Model,), {'source': Reference(), 'reversal': Boolean(default=False), 'amount': Currency(default=0), **fields})


def source(effect, state=None, **methods):
    meta = type('Meta', (), {'effects': (effect,)})
    state = state or State(('draft', 'done', 'void'), moves=(Move('draft', 'done', 'Done'),), locked=('done', 'void'),
                           final=('void',), voidable=('done',))
    return type('Source', (Model,), {'Meta': meta, 'status': state, 'rows': lambda self: [], **methods})


@pytest.mark.parametrize('make, message', [
    (lambda T: source(Effect(T, rows='rows', on='nowhere', reverse=Negate('amount'))), 'not a state'),
    (lambda T: source(Effect(T, rows='rows', on='draft', reverse=Negate('amount'))), 'must be entered by a declared move'),
    (lambda T: source(Effect(T, rows='missing', on='done', reverse=Negate('amount'))), 'not a method'),
    (lambda T: source(Effect(T, rows='rows', on='done', reverse=Negate('amount')), rows=lambda self, tx: []), 'takes only self'),
    (lambda T: source(Effect(T, rows='rows', on='done')), 'needs reverse'),
    (lambda T: source(Effect(T, rows='rows', on='done', reverse=Negate('nothing'))), 'Negate names'),
    (lambda T: source(Effect(T, rows='rows', on='done', reverse=Swap('amount', 'reversal'))), 'Swap names'),
    (lambda T: source(Effect(T, rows='rows', on='done', reverse=Negate('amount'), balanced=('amount', 'label'))), 'balanced names'),
    (lambda T: source(Effect(T, rows='rows', on='done', reverse=Negate('amount')),
                      state=State(('draft', 'done'), moves=(Move('draft', 'done', 'Done'), Move('done', 'draft', 'Back'))),), 'enter'),
    (lambda T: source(Effect(T, rows='rows', on='done'), state=State(('draft', 'done'), moves=(Move('draft', 'done', 'Done'),))),
     'must be locked'),
])
def test_effect_declarations_are_checked(make, message):
    model = target(label=Text())
    with pytest.raises(ValueError, match=message):
        Registry(make(model), model)


def test_effect_targets_are_checked():
    plain = type('Plain', (Model,), {'amount': Currency(default=0)})
    no_flag = type('NoFlag', (Model,), {'source': Reference(), 'amount': Currency(default=0)})
    for model, message in ((plain, 'source = Reference'), (no_flag, 'reversal = Boolean')):
        with pytest.raises(ValueError, match=message):
            Registry(source(Effect(model, rows='rows', on='done', reverse=Negate('amount'))), model)
    bounded = target(amount=Decimal(minimum='0'))
    with pytest.raises(ValueError, match='symmetric'):
        Registry(source(Effect(bounded, rows='rows', on='done', reverse=Negate('amount'))), bounded)
    mixed = target(debit=Currency(), credit=Decimal())
    with pytest.raises(ValueError, match='same type'):
        Registry(source(Effect(mixed, rows='rows', on='done', reverse=Swap('debit', 'credit'))), mixed)
    stateful = target(status=State(('a', 'b')))
    with pytest.raises(ValueError, match='no State'):
        Registry(source(Effect(stateful, rows='rows', on='done', reverse=Negate('amount'))), stateful)
    same = target()
    with pytest.raises(ValueError, match='at most one effect per target'):
        meta = type('Meta', (), {'effects': (Effect(same, rows='rows', on='done', reverse=Negate('amount')),
                                             Effect(same, rows='rows', on='done', reverse=Negate('amount')))})
        Registry(type('Twice', (Model,), {'Meta': meta, 'status': State(('draft', 'done'), moves=(Move('draft', 'done'),), locked=('done',)),
                                           'rows': lambda self: []}), same)
    with pytest.raises(ValueError):
        Effect(same, rows='rows', on='done', reverse='swap')
    with pytest.raises(ValueError):
        Swap('debit', 'debit')
    # Without voidable states after `on`, reverse is optional.
    Registry(source(Effect(same, rows='rows', on='done'), state=State(('draft', 'done'), moves=(Move('draft', 'done'),), locked=('done',))), same)


# Posting

def test_a_move_posts_balanced_rows_atomically(books):
    app, c, account = books
    url = invoice(c, account)
    assert entries(app) == [] and entries(app, 'stockmove') == []
    status, moved, _ = move(c, url, 'issued')
    assert status == 200, moved
    ledger = entries(app)
    # 0.10 + 0.20 debits balance the 0.30 credit exactly; amounts are stored as scaled integers.
    assert [(row['debit'], row['credit'], row['memo'], row['source_model'], row['source_id'], row['reversal']) for row in ledger] == [
        (10, 0, 'a', 'invoice', RecordId(moved['id']), 0), (20, 0, 'b', 'invoice', RecordId(moved['id']), 0),
        (0, 30, 'revenue', 'invoice', RecordId(moved['id']), 0)]
    assert [(row['item'], row['qty']) for row in entries(app, 'stockmove')] == [('a', -10000), ('b', -20000)]
    assert moved['_effects'] == [{'target': 'ledgerentry', 'rows': 3, 'reversals': 0, 'balanced': True},
                                 {'target': 'stockmove', 'rows': 2, 'reversals': 0}]
    # Moving on from the state does not post again.
    assert move(c, url, 'paid')[0] == 200 and len(entries(app)) == 3
    assert effects.check(app) == []


def test_context_move_posts_and_failures_roll_back_the_transaction(books):
    from evlet.auth import User
    app, c, account = books
    good, bad = invoice(c, account), invoice(c, account, skew='0.05')
    admin = User(1, 'admin', 'admin')
    with app.business.context(admin) as context:
        assert context.move(Invoice, RecordId(good.rsplit('/', 1)[1]), 'issued')['status'] == 'issued'
    assert len(entries(app)) == 3
    with pytest.raises(ValidationError, match='not balanced'):
        with app.business.context(admin) as context:
            context.move(Invoice, RecordId(bad.rsplit('/', 1)[1]), 'issued')
    assert len(entries(app)) == 3 and c('GET', bad)[1]['status'] == 'draft'


def test_unbalanced_rows_roll_the_move_back(books):
    app, c, account = books
    url = invoice(c, account, skew='0.01')
    status, body, _ = move(c, url, 'issued')
    assert status == 422
    assert body['fields'] == {'_effects': 'LedgerEntry rows are not balanced: debit totals 0.30 and credit totals 0.29'}
    assert c('GET', url)[1]['status'] == 'draft' and entries(app) == [] and entries(app, 'stockmove') == []


def test_an_invalid_row_rolls_the_move_back(books):
    app, c, account = books
    url = invoice(c, account, lines=(('a', '1', '1'), ('forbidden', '1', '1')))
    status, body, _ = move(c, url, 'issued')
    assert status == 422 and body['fields'] == {'_effects': 'StockMove row 2 is not valid: item: This item cannot move'}
    assert c('GET', url)[1]['status'] == 'draft' and entries(app) == [] and entries(app, 'stockmove') == []
    # A required value left empty fails like a normal record, and so does a value the field rejects.
    url = invoice(c, account, lines=(('', '1', '1'),))
    assert move(c, url, 'issued')[1]['fields'] == {'_effects': 'StockMove row 1 is not valid: item: This field must not be blank'}
    # Without an account the ledger rows miss a required Link.
    url = invoice(c, None)
    assert move(c, url, 'issued')[1]['fields'] == {'_effects': 'LedgerEntry row 1 is not valid: account: This field is required'}
    assert entries(app) == [] and entries(app, 'stockmove') == []


def test_rows_may_not_set_engine_or_unknown_fields(tmp_path):
    class Rogue(Model):
        Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(LedgerEntry, rows='rows', on='issued', reverse=Swap('debit', 'credit')),)})
        permissions = ALL
        account = Link(Account)
        status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued', 'void'),
                       final=('void',), voidable=('issued',))
        bad = Text(default='source')

        def rows(self):
            return [{'account': self.account, self.bad: {'model': 'account', 'id': 1}}]
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Account, LedgerEntry, Rogue)))
    c = client(app)
    account = c('POST', '/api/account', {'title': 'A'})[1]['id']
    for bad in ('source', 'reversal', 'unknown'):
        url = f"/api/rogue/{c('POST', '/api/rogue', {'account': account, 'bad': bad})[1]['id']}"
        assert move(c, url, 'issued')[1]['fields'] == {'_effects': f'LedgerEntry row 1 is not valid: {bad}: Not a field the rows may set'}
    assert entries(app) == []


def test_rows_see_the_saved_document_read_only(tmp_path):
    seen = []

    class Probe(Model):
        Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(StockMove, rows='rows', on='issued'),)})
        permissions = ALL
        title = Text(default='')
        lines = Table(InvoiceLine)
        total = Currency(compute=lines.sum('amount'))
        status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued',))

        def rows(self):
            seen.append((self.status, str(self.total), [line.item for line in self.lines]))
            self.title = 'changed'
    app = start(Database(tmp_path / 'db.sqlite3', Registry(StockMove, Probe)))
    c = client(app)
    url = f"/api/probe/{c('POST', '/api/probe', {'children': {'lines': [{'item': 'x', 'qty': '2', 'rate': '3'}]}})[1]['id']}"
    # Changing the record from its rows method fails the move; the exception is not shown.
    status, body, _ = move(c, url, 'issued')
    assert status == 422 and body['fields'] == {'_effects': 'Probe.rows could not produce the StockMove rows'}
    assert seen == [('issued', '6.00', ['x'])] and c('GET', url)[1]['status'] == 'draft'


# Read only

def test_effect_rows_are_read_only_on_every_write_path(books):
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    row = RecordId(entries(app)[0]['id'])
    stock = RecordId(entries(app, 'stockmove')[0]['id'])
    headers = etag(c, f'/api/ledgerentry/{row}')
    assert c('POST', '/api/ledgerentry', {'account': account, 'debit': '1'})[0] == 403
    assert c('PATCH', f'/api/ledgerentry/{row}', {'memo': 'x'}, headers=headers)[0] == 403
    assert c('DELETE', f'/api/ledgerentry/{row}', headers=headers)[0] == 403
    assert c('POST', '/api/stockmove', {'item': 'x', 'qty': '1'})[0] == 403
    assert c('PATCH', f'/api/stockmove/{stock}', {'item': 'y'}, headers=etag(c, f'/api/stockmove/{stock}'))[0] == 403
    assert c('DELETE', f'/api/stockmove/{stock}', headers=etag(c, f'/api/stockmove/{stock}'))[0] == 403
    schema = {model['name']: model for model in c('GET', '/api/schema')[1]['models']}
    assert schema['ledgerentry']['permissions'] == {'create': False, 'update': False, 'delete': False}
    assert schema['ledgerentry']['effect_target'] is True and 'effect_target' not in schema['invoice']
    from evlet.auth import User
    user = User(1, 'admin', 'admin')
    with app.business.context(user) as context:
        # Context writes, and so dialogs and services that write through it.
        for write in (lambda: context.create(LedgerEntry, account=account), lambda: context.update(LedgerEntry, row, memo='x'),
                      lambda: context.delete(LedgerEntry, row)):
            with pytest.raises(AccessError) as caught:
                write()
            assert caught.value.status == 403
    # Raw sessions and the Database helpers, including legacy Action handlers.
    for write in (lambda tx: tx.create(LedgerEntry, account=account), lambda tx: tx.update(LedgerEntry, row, memo='x'),
                  lambda tx: tx.delete(LedgerEntry, row), lambda tx: tx.create(StockMove, item='x'),
                  lambda tx: tx.update(StockMove, stock, item='y')):
        with pytest.raises(ValueError, match='read only'):
            with app.database.transaction() as tx:
                write(tx)
    with pytest.raises(ValueError, match='read only'):
        app.database.update(StockMove, stock, item='y')
    assert entries(app)[0]['memo'] == 'a' and entries(app, 'stockmove')[0]['item'] == 'a'
    # An audited repair may change them.
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(StockMove, stock, item='repaired')
    assert entries(app, 'stockmove')[0]['item'] == 'repaired'


# Void and revise

def test_void_writes_mirror_rows_and_keeps_originals(books):
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    before = entries(app)
    status, voided, _ = c('POST', f'{url}/void', {'reason': 'Wrong customer'}, headers=etag(c, url))
    assert status == 200 and voided['status'] == 'void', voided
    ledger = entries(app)
    assert ledger[:3] == before
    # Swap exchanges debit and credit; the mirrors keep the source and are marked as reversals.
    assert [(row['debit'], row['credit'], row['memo'], row['source_id'], row['reversal']) for row in ledger[3:]] == [
        (0, 10, 'a', RecordId(voided['id']), 1), (0, 20, 'b', RecordId(voided['id']), 1), (30, 0, 'revenue', RecordId(voided['id']), 1)]
    # Negate flips the quantity.
    assert [(row['qty'], row['reversal']) for row in entries(app, 'stockmove')] == [(-10000, 0), (-20000, 0), (10000, 1), (20000, 1)]
    assert c('GET', url)[1]['_effects'][0] == {'target': 'ledgerentry', 'rows': 3, 'reversals': 3, 'balanced': True}
    assert effects.check(app) == []


def test_a_void_that_cannot_reverse_rolls_back(books, monkeypatch):
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    # A target validator that rejects the mirror rows blocks the void.
    monkeypatch.setattr(StockMove, 'validate', lambda self: (_ for _ in ()).throw(ValidationError({'qty': 'No returns'}))
                        if self.qty is not None and self.qty > 0 else None)
    status, body, _ = c('POST', f'{url}/void', {'reason': 'x'}, headers=etag(c, url))
    assert status == 422 and body['fields'] == {'_effects': 'The reversal of StockMove row 1 is not valid: qty: No returns'}
    assert c('GET', url)[1]['status'] == 'issued' and len(entries(app)) == 3 and len(entries(app, 'stockmove')) == 2


def test_revise_reverses_the_original_and_the_copy_posts_its_own(books):
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    status, copy, _ = c('POST', f'{url}/revise', {'reason': 'New prices'}, headers=etag(c, url))
    assert status == 201, copy
    original = c('GET', url)[1]
    assert original['status'] == 'void' and original['_effects'][0]['reversals'] == 3
    copy_url = f"/api/invoice/{copy['id']}"
    assert copy['_effects'] == [{'target': 'ledgerentry', 'rows': 0, 'reversals': 0, 'balanced': True},
                                {'target': 'stockmove', 'rows': 0, 'reversals': 0}]
    assert move(c, copy_url, 'issued')[0] == 200
    sources = [(str(RecordId(row['source_id'])), row['reversal']) for row in entries(app)]
    assert sources.count((original['id'], 0)) == sources.count((original['id'], 1)) == 3
    assert sources.count((copy['id'], 0)) == 3 and (copy['id'], 1) not in sources
    assert effects.check(app) == []


# Permissions

def test_roles_that_cannot_read_a_target_see_nothing_about_it(books):
    app, c, account = books
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')
    url = invoice(editor, account)
    status, moved, _ = move(editor, url, 'issued')
    # The editor reads entries but not their amounts, so it sees no balance. Stock moves are ungoverned.
    assert status == 200 and moved['_effects'] == [{'target': 'ledgerentry', 'rows': 3, 'reversals': 0},
                                                   {'target': 'stockmove', 'rows': 2, 'reversals': 0}]
    # The viewer cannot read ledger entries, so it sees nothing about them.
    assert viewer('GET', url)[1]['_effects'] == [{'target': 'stockmove', 'rows': 2, 'reversals': 0}]
    schema = {model['name']: model for model in viewer('GET', '/api/schema')[1]['models']}
    assert [item['target'] for item in schema['invoice']['effects']] == ['stockmove'] and 'ledgerentry' not in schema
    editor_schema = {model['name']: model for model in editor('GET', '/api/schema')[1]['models']}
    assert editor_schema['invoice']['effects'][0] == {'target': 'ledgerentry', 'on': 'issued', 'balanced': None, 'reverse': None}
    admin_schema = {model['name']: model for model in c('GET', '/api/schema')[1]['models']}
    assert admin_schema['invoice']['effects'] == [
        {'target': 'ledgerentry', 'on': 'issued', 'balanced': ['debit', 'credit'], 'reverse': {'swap': ['debit', 'credit']}},
        {'target': 'stockmove', 'on': 'issued', 'balanced': None, 'reverse': {'negate': ['qty']}}]
    assert viewer('GET', '/api/ledgerentry')[0] == 403
    # The editor cannot read the amounts, so the balance error does not show the totals.
    url = invoice(editor, account, skew='1')
    assert move(editor, url, 'issued')[1]['fields'] == {'_effects': 'LedgerEntry rows are not balanced'}


def test_failures_are_generic_for_roles_that_cannot_read_the_target(tmp_path):
    class Hidden(Model):
        permissions = {'admin': Rule(('read',)), 'editor': Rule(()), 'viewer': Rule(())}
        item = Text(required=True)
        qty = Decimal()
        source = Reference()
        reversal = Boolean(default=False)

    class Job(Model):
        Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(Hidden, rows='rows', on='done'),)})
        permissions = ALL
        item = Text(default='')
        status = State(('open', 'done'), moves=(Move('open', 'done', 'Done'),), locked=('done',))

        def rows(self):
            return [{'item': self.item, 'qty': '1'}]
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Hidden, Job)))
    editor = add_user(app, 'editor')
    url = f"/api/job/{editor('POST', '/api/job', {})[1]['id']}"
    assert move(editor, url, 'done')[1]['fields'] == {'_effects': 'The rows this record posts to other records are not valid'}
    url = f"/api/job/{editor('POST', '/api/job', {'item': 'x'})[1]['id']}"
    moved = move(editor, url, 'done')[1]
    assert moved['status'] == 'done' and '_effects' not in moved
    assert len(entries(app, 'hidden')) == 1


# Check

def test_check_reports_changed_missing_extra_and_unbalanced_rows(books):
    app, c, account = books
    first = invoice(c, account)
    second = invoice(c, account, lines=(('c', '1', '5'),))
    move(c, first, 'issued')
    move(c, second, 'issued')
    assert effects.check(app) == []
    rows, stock = entries(app), entries(app, 'stockmove')
    first_id, second_id = rows[0]['source_id'], rows[-1]['source_id']
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(LedgerEntry, rows[0]['id'], debit='0.11')
        tx.delete(StockMove, stock[1]['id'])
        # Every row of the second invoice's ledger is gone; issued always means posted, so it is missing.
        for row in rows[3:]:
            tx.delete(LedgerEntry, row['id'])
        tx.create(StockMove, item='z', qty='1', source={'model': 'invoice', 'id': second_id})
    found = {(item['record'], item['target'], item['kind'], item['reversal']) for item in effects.check(app)}
    assert found == {(first_id, 'ledgerentry', 'changed', False), (first_id, 'ledgerentry', 'unbalanced', False),
                     (first_id, 'stockmove', 'missing', False), (second_id, 'ledgerentry', 'missing', False),
                     (second_id, 'stockmove', 'extra', False)}
    changed = next(item for item in effects.check(app) if item['kind'] == 'changed')
    assert changed['expected']['debit'] == '0.10' and changed['actual']['debit'] == '0.11' and changed['row'] == rows[0]['id']
    assert 'changed; row' in effects.describe(changed)


def test_check_reports_rows_of_records_that_have_not_posted(books):
    app, c, account = books
    url = invoice(c, account)
    record_id = RecordId(url.rsplit('/', 1)[1])
    with app.database.transaction() as tx, tx.maintenance():
        tx.create(StockMove, item='a', qty='-1', source={'model': 'invoice', 'id': record_id})
        # A row whose source was removed: References have no SQL foreign key.
        tx.connection.execute("INSERT INTO stockmove(item, qty, source_model, source_id, reversal) VALUES ('b', -1, 'invoice', 999, 0)")
    assert [(item['record'], item['kind'], item['message']) for item in effects.check(app)] == [
        (record_id, 'extra', 'The record has not posted this effect'), (999, 'extra', 'The source record does not exist')]


def test_check_command_exit_codes(books, tmp_path, monkeypatch, capsys):
    from evlet.cli import effects as command
    from evlet.cli.main import main
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    root = tmp_path / 'project'
    for name in ('apps', 'instances/acme'):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(command, 'load_runtime', lambda project, name: app if (project, name) == (root, 'acme') else None)
    main(['effects', 'check', 'acme', '--project', str(root)])
    assert capsys.readouterr().out == 'No differences\n'
    first = entries(app, 'stockmove')[0]['id']
    with app.database.transaction() as tx, tx.maintenance():
        tx.delete(StockMove, first)
    with pytest.raises(SystemExit) as stopped:
        main(['effects', 'check', 'acme', '--project', str(root)])
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert output.out.startswith(f"invoice {url.rsplit('/', 1)[1]} -> stockmove: missing; expected ") and output.err == '1 difference\n'
    # Nothing was written by the check.
    assert len(entries(app, 'stockmove')) == 1 and len(entries(app)) == 3


# Review findings

def main_branch(user):
    return {'title': 'main'}


class Branch(Model):
    # The admin role sees only the main branch; editors see every branch.
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'), scope=main_branch),
                   'editor': Rule(('read', 'create', 'update', 'delete'))}
    title = Text(default='')
    open = Boolean(default=True)


class BranchEntry(Model):
    """Declarative target whose Link has a filter and links to a model admin can only partly read."""
    permissions = {'admin': Rule(('read',)), 'editor': Rule(('read',)), 'viewer': Rule(())}
    branch = Link(Branch, required=True, filters={'open': True})
    amount = Currency(default=0)
    source = Reference()
    reversal = Boolean(default=False)


class PlainEntry(Model):
    """Plain target: a Link to a missing record must be a 422, not a foreign key conflict."""
    branch = Link(Branch)
    qty = Decimal()
    source = Reference()
    reversal = Boolean(default=False)


class BranchOrder(Model):
    Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(BranchEntry, rows='entries', on='done'),
                                                    Effect(PlainEntry, rows='plain', on='done'))})
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update', 'delete'))}
    branch = Link(Branch)
    missing = Integer(default=0)
    plain_missing = Integer(default=0)
    status = State(('draft', 'done'), moves=(Move('draft', 'done', 'Done'),), locked=('done',), final=('done',))

    def entries(self):
        return [{'branch': self.missing or self.branch, 'amount': '5'}]

    def plain(self):
        return [{'branch': self.plain_missing or self.branch, 'qty': '1'}]


@pytest.fixture
def branches(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Branch, BranchEntry, PlainEntry, BranchOrder)))
    return app, add_user(app, 'editor')


def test_rows_are_validated_without_any_role(branches):
    app, editor = branches
    branch = editor('POST', '/api/branch', {'title': 'dubai'})[1]['id']
    url = f"/api/branchorder/{editor('POST', '/api/branchorder', {'branch': branch})[1]['id']}"
    # Admin cannot read the dubai branch, yet the engine posts rows linking to it.
    status, body, _ = move(editor, url, 'done')
    assert status == 200, body
    assert [row['branch'] for row in entries(app, 'branchentry')] == [RecordId(branch)] and len(entries(app, 'plainentry')) == 1
    # Link filters are decided on the stored record.
    closed = editor('POST', '/api/branch', {'title': 'closed', 'open': False})[1]['id']
    url = f"/api/branchorder/{editor('POST', '/api/branchorder', {'branch': closed})[1]['id']}"
    assert move(editor, url, 'done')[1]['fields'] == {
        '_effects': "BranchEntry row 1 is not valid: branch: This Branch doesn't fit the other values on this form."}


def test_links_to_missing_records_are_field_errors(branches):
    app, editor = branches
    branch = editor('POST', '/api/branch', {'title': 'main'})[1]['id']
    url = f"/api/branchorder/{editor('POST', '/api/branchorder', {'branch': branch, 'missing': 999})[1]['id']}"
    assert move(editor, url, 'done')[1]['fields'] == {'_effects': 'BranchEntry row 1 is not valid: branch: The linked record does not exist'}
    # A plain target gives the same 422, not a 409 from the foreign key.
    url = f"/api/branchorder/{editor('POST', '/api/branchorder', {'branch': branch, 'plain_missing': 999})[1]['id']}"
    status, body, _ = move(editor, url, 'done')
    assert status == 422 and body['fields'] == {'_effects': 'PlainEntry row 1 is not valid: branch: The linked record does not exist'}
    assert entries(app, 'branchentry') == entries(app, 'plainentry') == [] and editor('GET', url)[1]['status'] == 'draft'


def test_a_reverse_must_keep_a_balanced_pair_balanced():
    for reverse in (Negate('debit'), Swap('debit', 'other')):
        model = target(debit=Currency(), credit=Currency(), other=Currency())
        with pytest.raises(ValueError, match='would not balance'):
            Registry(source(Effect(model, rows='rows', on='done', reverse=reverse, balanced=('debit', 'credit'))), model)
    for reverse in (Negate('debit', 'credit'), Swap('credit', 'debit'), Negate('other')):
        model = target(debit=Currency(), credit=Currency(), other=Currency())
        Registry(source(Effect(model, rows='rows', on='done', reverse=reverse, balanced=('debit', 'credit'))), model)


def test_void_checks_the_balance_of_the_mirror_rows(books, monkeypatch):
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    original = effects.mirror_values

    def skewed(bound, values):
        result = original(bound, values)
        if bound.balanced and result.get('debit'):
            result['debit'] += D('1')
        return result
    monkeypatch.setattr(effects, 'mirror_values', skewed)
    status, body, _ = c('POST', f'{url}/void', {'reason': 'x'}, headers=etag(c, url))
    assert status == 422 and body['fields']['_effects'].startswith('The reversal of the LedgerEntry rows are not balanced: debit totals')
    assert c('GET', url)[1]['status'] == 'issued' and len(entries(app)) == 3


def test_a_failing_rows_method_is_a_field_error_and_an_invalid_check(books, monkeypatch, caplog):
    app, c, account = books
    url = invoice(c, account)
    move(c, url, 'issued')
    monkeypatch.setattr(Invoice, 'stock_rows', lambda self: 1 / 0)
    found = effects.check(app)
    assert [(item['target'], item['kind']) for item in found] == [('stockmove', 'invalid')]
    assert 'ZeroDivisionError' in found[0]['message']
    other = invoice(c, account)
    status, body, _ = move(c, other, 'issued')
    assert status == 422 and body['fields'] == {'_effects': 'Invoice.stock_rows could not produce the StockMove rows'}
    assert 'division' not in str(body) and 'ZeroDivisionError' in caplog.text
    assert c('GET', other)[1]['status'] == 'draft' and len(entries(app)) == 3


def test_reading_a_virtual_result_is_an_error(tmp_path):
    class Virtual(Model):
        Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(StockMove, rows='rows', on='done'),)})
        permissions = ALL
        n = Decimal(default=2)
        double = Decimal(stored=False, compute=F('n') * 2)
        status = State(('draft', 'done'), moves=(Move('draft', 'done', 'Done'),), locked=('done',))

        def rows(self):
            return [{'item': 'x', 'qty': self.double}]
    app = start(Database(tmp_path / 'db.sqlite3', Registry(StockMove, Virtual)))
    c = client(app)
    url = f"/api/virtual/{c('POST', '/api/virtual', {})[1]['id']}"
    status, body, _ = move(c, url, 'done')
    assert status == 422 and 'Virtual.double is a virtual result (stored=False)' in body['fields']['_effects']
    assert entries(app, 'stockmove') == []


def test_an_unreachable_state_is_rejected():
    model = target()
    state = State(('draft', 'limbo', 'done', 'void'), moves=(Move('limbo', 'done', 'Done'),), locked=('done', 'void', 'limbo'),
                  final=('void',), voidable=('done',))
    with pytest.raises(ValueError, match='no sequence of declared moves'):
        Registry(source(Effect(model, rows='rows', on='done', reverse=Negate('amount')), state=state), model)


def test_check_reads_a_snapshot_without_the_write_lock(books):
    import sqlite3
    app, c, account = books
    move(c, invoice(c, account), 'issued')
    writer = sqlite3.connect(app.database.path, timeout=0)
    try:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('UPDATE stockmove SET qty = 0')
        # The writer holds the write lock and has not committed: the check neither waits nor sees its change.
        assert effects.check(app) == []
    finally:
        writer.rollback()
        writer.close()


def test_source_lookups_use_the_source_index(books):
    import sqlite3
    app, c, account = books
    assert LedgerEntry.__declared_fields__['source'].index and Reference(index=True).index
    with pytest.raises(ValueError, match='cannot be unique'):
        Reference(unique=True)
    connection = sqlite3.connect(app.database.path)
    indexes = [row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='ledgerentry'")]
    plan = connection.execute('EXPLAIN QUERY PLAN SELECT * FROM "ledgerentry" WHERE "source_model"=? AND "source_id" IN (?) ORDER BY id',
                              ('invoice', 1)).fetchall()
    connection.close()
    assert any('"source_model", "source_id"' in (sql or '') for sql in indexes), indexes
    assert any('INDEX' in row[3] and 'source' in row[3] for row in plan), plan


def test_an_existing_target_table_adds_the_source_index_by_migration(tmp_path):
    from evlet import AddIndex, CreateModel, Migration, SchemaError

    def entry_model(indexed):
        return type('Entry', (Model,), {'item': Text(default=''), 'source': Reference(index=indexed), 'reversal': Boolean(default=False)})
    old, new = entry_model(False), entry_model(True)
    path = tmp_path / 'db.sqlite3'
    first = (Migration('0001', CreateModel(old)),)
    Database(path, Registry(old), migrations=first).migrate(first)
    with pytest.raises(SchemaError):
        Database(path, Registry(new), migrations=first).initialize()
    second = first + (Migration('0002_source_index', AddIndex('entry', 'source')),)
    Database(path, Registry(new), migrations=second).migrate(second)
    Database(path, Registry(new), migrations=second).initialize()


def test_a_renamed_indexed_reference_keeps_its_index(tmp_path):
    from evlet import CreateModel, Migration, RenameField
    before = type('Note', (Model,), {'about': Reference(index=True)})
    after = type('Note', (Model,), {'subject': Reference(index=True)})
    path = tmp_path / 'db.sqlite3'
    first = (Migration('0001', CreateModel(before)),)
    Database(path, Registry(before), migrations=first).migrate(first)
    second = first + (Migration('0002', RenameField('note', 'about', 'subject')),)
    Database(path, Registry(after), migrations=second).migrate(second)
    Database(path, Registry(after), migrations=second).initialize()


def test_posting_many_rows_costs_one_statement_per_row(tmp_path):
    from contextlib import contextmanager
    log = []

    class Counting(Database):
        @contextmanager
        def _connection(self):
            with super()._connection() as connection:
                connection.set_trace_callback(log.append)
                yield connection

    class Big(Model):
        Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(LedgerEntry, rows='rows', on='done', reverse=Swap('debit', 'credit'),
                                                               balanced=('debit', 'credit')),)})
        permissions = ALL
        account = Link(Account)
        status = State(('open', 'done', 'void'), moves=(Move('open', 'done', 'Done'),), locked=('done', 'void'), final=('void',),
                       voidable=('done',))

        def rows(self):
            return [{'account': self.account, 'debit': '1'}, {'account': self.account, 'credit': '1'}] * 250
    app = start(Counting(tmp_path / 'db.sqlite3', Registry(Account, LedgerEntry, Big)))
    c = client(app)
    account = c('POST', '/api/account', {'title': 'A'})[1]['id']
    url = f"/api/big/{c('POST', '/api/big', {'account': account})[1]['id']}"
    headers = etag(c, url)
    log.clear()
    assert c('POST', f'{url}/moves/done', {}, headers=headers)[0] == 200
    inserts = [sql for sql in log if sql.startswith('INSERT INTO "ledgerentry"')]
    assert len(inserts) == 500 and len(log) - 500 < 60, (len(log), [sql[:80] for sql in log if not sql.startswith('INSERT')])
    log.clear()
    assert c('POST', f'{url}/void', {'reason': 'x'}, headers=etag(c, url))[0] == 200
    assert len(log) - 500 < 60, len(log)


# Effects beside the phases stacked under them: savepoints and numbers (7), measures (9) and relations (10)

class Office(Model):
    permissions = ALL
    title = Text(default='')

    class Meta:
        # Per record: the postings of an office, read with the caller's row scope on Posting.
        measures = {'posted': Sum('amount', model='posting', link='office'), 'postings': Count('posting', link='office')}


class Posting(Model):
    # Editors read only their own postings; viewers read none.
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read',), scope=lambda user: {'owner': user.username}), 'viewer': Rule(())}
    office = Link(Office)
    owner = Text(default='')
    memo = Text(default='')
    amount = Currency(default=0)
    source = Reference(index=True)
    reversal = Boolean(default=False)

    class Meta:
        measures = {'total': Sum('amount'), 'rows': Count(), 'reversals': Count(where=F('reversal') == True)}  # noqa: E712


class Audit(Model):
    permissions = ALL
    memo = Text(default='')
    amount = Currency(default=0)
    source = Reference()
    reversal = Boolean(default=False)

    def validate(self):
        if self.amount is not None and self.amount > 1000:
            raise ValidationError({'amount': 'Too large to audit'})


class Bill(Model):
    Meta = type('Meta', (FiniteMeta,), {'revisable': True, 'effects': (
        Effect(Posting, rows='postings', on='issued', reverse=Negate('amount')),
        Effect(Audit, rows='audits', on='issued', reverse=Negate('amount')))})
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise')),
                   'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
    office = Link(Office)
    owner = Text(default='')
    amount = Currency(default=0)
    # Numbered by the same move that posts the effects; the rows can quote the number.
    number = Sequence('B-{n:03}', assign_on='issued')
    status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),),
                   locked=('issued', 'void'), final=('void',), voidable=('issued',))

    def postings(self):
        return [{'office': self.office, 'owner': self.owner, 'memo': self.number, 'amount': self.amount}]

    def audits(self):
        return [{'memo': self.number, 'amount': self.amount}]


STACKED = (Office, Posting, Audit, Bill)


def stacked_books(database):
    app = start(database)
    c = client(app)
    return app, c, c('POST', '/api/office', {'title': 'North'})[1]['id']


def bill(c, office, amount='10.00', owner='editor'):
    status, record, _ = c('POST', '/api/bill', {'office': office, 'amount': amount, 'owner': owner})
    assert status == 201, record
    return record['id']


def check_a_caught_move_failure_leaves_no_rows_or_number(books):
    from evlet.auth import User
    app, c, office = books
    good, bad, after = bill(c, office), bill(c, office, '5000.00'), bill(c, office)
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.move(Bill, good, 'issued')['number'] == 'B-001'
        # The Posting effect writes its row before the Audit effect refuses the amount; trusted code catches the
        # failure and commits, so the operation's savepoint must take the posting and the number back.
        with pytest.raises(ValidationError, match='Too large to audit'):
            context.move(Bill, bad, 'issued')
        assert context.move(Bill, after, 'issued')['number'] == 'B-002'
    assert [(row['memo'], row['source_id']) for row in entries(app, 'posting')] == [('B-001', RecordId(good)), ('B-002', RecordId(after))]
    assert [row['source_id'] for row in entries(app, 'audit')] == [RecordId(good), RecordId(after)]
    assert c('GET', f'/api/bill/{bad}')[1]['status'] == 'draft' and effects.check(app) == []


def check_numbers_and_rows_on_one_move_and_on_revise(books):
    app, c, office = books
    url = f'/api/bill/{bill(c, office)}'
    status, moved, _ = move(c, url, 'issued')
    # The number is assigned when the move settles, before the record is written; the effects post after the
    # write, so their rows already see it.
    assert status == 200 and moved['number'] == 'B-001', moved
    assert [row['memo'] for row in entries(app, 'posting')] == ['B-001']
    status, copy, _ = c('POST', f'{url}/revise', {'reason': 'New amount'}, headers=etag(c, url))
    assert status == 201 and copy['number'] is None, copy
    original = c('GET', url)[1]
    assert (original['status'], original['number']) == ('void', 'B-001')
    copy_url = f"/api/bill/{copy['id']}"
    assert move(c, copy_url, 'issued')[1]['number'] == 'B-002'
    # The reversal keeps the original's number and source; the copy posts once more under its own number.
    assert [(row['memo'], row['amount'], row['source_id'], row['reversal']) for row in entries(app, 'posting')] == [
        ('B-001', 1000, RecordId(original['id']), 0), ('B-001', -1000, RecordId(original['id']), 1), ('B-002', 1000, RecordId(copy['id']), 0)]
    assert effects.check(app) == []


def check_measures_over_a_target_follow_row_scope(books):
    app, c, office = books
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')
    ids = [bill(c, office, amount, owner) for amount, owner in (('10.00', 'editor'), ('20.00', 'admin'), ('5.00', 'editor'))]
    for record_id in ids:
        assert move(c, f'/api/bill/{record_id}', 'issued')[0] == 200
    voided = ids[0]
    assert c('POST', f'/api/bill/{voided}/void', {'reason': 'x'}, headers=etag(c, f'/api/bill/{voided}'))[0] == 200
    # Admin: all rows, the reversal included, so the void nets to zero.
    assert c('GET', '/api/posting/measures')[1] == {'measures': {'total': '25.00', 'rows': 4, 'reversals': 1}}
    assert c('GET', f'/api/office/{office}/measures')[1] == {'measures': {'posted': '25.00', 'postings': 4}}
    # The editor's row scope keeps the admin's posting out of every total.
    assert editor('GET', '/api/posting/measures')[1] == {'measures': {'total': '5.00', 'rows': 3, 'reversals': 1}}
    assert editor('GET', f'/api/office/{office}/measures')[1] == {'measures': {'posted': '5.00', 'postings': 3}}
    grouped = editor('GET', '/api/posting/aggregate?measures=t:sum:amount&group_by=reversal')[1]['items']
    assert grouped == [{'reversal': False, 't': '15.00'}, {'reversal': True, 't': '-10.00'}]
    assert viewer('GET', '/api/posting/measures')[0] == 403
    # Measures read; they never open a write path to read-only rows.
    assert c('POST', '/api/posting/measures', {})[0] in (404, 405) and c('POST', '/api/posting', {'amount': '1'})[0] == 403
    # Trusted code reads every row; originals alone are the gross amount posted.
    assert app.database.measures(Posting, filters={'reversal': False}) == {'total': D('35.00'), 'rows': 3, 'reversals': 0}


def check_an_effect_source_is_not_a_relation(books):
    app, c, office = books
    models = {model['name']: model for model in c('GET', '/api/schema')[1]['models']}
    # Relations come from Links only. The Reference back to the posting document is not one: a bill lists its
    # rows through `_effects` counts, while the office Link on Posting is an ordinary relation of Office.
    assert [item['name'] for item in models['bill']['relations']] == ['bill.revises']
    assert [item['name'] for item in models['office']['relations']] == ['bill.office', 'posting.office']
    url = f'/api/bill/{bill(c, office)}'
    move(c, url, 'issued')
    assert c('GET', f'{url}/related/posting.source')[0] == 404
    related = c('GET', f'/api/office/{office}/related/posting.office')[1]
    assert related['total'] == 1 and related['items'][0]['memo'] == 'B-001'


@pytest.mark.parametrize('check', [check_a_caught_move_failure_leaves_no_rows_or_number, check_numbers_and_rows_on_one_move_and_on_revise,
                                   check_measures_over_a_target_follow_row_scope, check_an_effect_source_is_not_a_relation])
def test_effects_beside_numbers_measures_and_relations(tmp_path, check):
    check(stacked_books(Database(tmp_path / 'stacked.sqlite3', Registry(*STACKED))))


# The Journal (Phase 8): `effects` entries when a move posts rows and when a void writes their mirrors

def history(c, url):
    status, body, _ = c('GET', f'{url}/journal')
    assert status == 200, body
    return body['items']


def check_the_journal_records_postings_and_reversals(books):
    app, c, account = books
    url = invoice(c, account)
    assert move(c, url, 'issued')[0] == 200
    # Newest first: the move, then the rows it posted in the same transaction.
    posted, moved = history(c, url)[:2]
    assert moved['kind'] == 'moved' and moved['data']['to'] == 'issued'
    assert posted['kind'] == 'effects' and 'partial' not in posted
    assert posted['data'] == {'targets': [{'target': 'ledgerentry', 'rows': 3, 'reversal': False, 'balanced': True},
                                          {'target': 'stockmove', 'rows': 2, 'reversal': False}]}
    # Moving on posts nothing, so no entry is written.
    assert move(c, url, 'paid')[0] == 200 and [item['kind'] for item in history(c, url)][:2] == ['moved', 'effects']
    url = invoice(c, account)
    move(c, url, 'issued')
    assert c('POST', f'{url}/void', {'reason': 'Wrong customer'}, headers=etag(c, url))[0] == 200
    reversed_rows, voided = history(c, url)[:2]
    assert voided['kind'] == 'voided' and voided['data']['reason'] == 'Wrong customer'
    assert reversed_rows['kind'] == 'effects' and reversed_rows['data'] == {
        'targets': [{'target': 'ledgerentry', 'rows': 3, 'reversal': True, 'balanced': True},
                    {'target': 'stockmove', 'rows': 2, 'reversal': True}]}
    assert c('GET', url)[1]['_journal']['count'] == len(history(c, url))
    from evlet import journal
    assert journal.verify(app.database)[1] == []


def test_the_journal_records_postings_and_reversals(books):
    check_the_journal_records_postings_and_reversals(books)


def test_journal_effects_entries_hide_targets_the_reader_cannot_read(books, tmp_path):
    app, c, account = books
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')
    url = invoice(c, account)
    move(c, url, 'issued')
    # The editor reads ledger entries but not their amounts, so the balance is removed.
    entry = history(editor, url)[0]
    assert entry['kind'] == 'effects' and entry['partial'] is True
    assert entry['data'] == {'targets': [{'target': 'ledgerentry', 'rows': 3, 'reversal': False},
                                         {'target': 'stockmove', 'rows': 2, 'reversal': False}]}
    # The viewer cannot read ledger entries at all.
    entry = history(viewer, url)[0]
    assert entry['partial'] is True and entry['data'] == {'targets': [{'target': 'stockmove', 'rows': 2, 'reversal': False}]}

    class Hidden(Model):
        permissions = {'admin': Rule(('read',)), 'editor': Rule(()), 'viewer': Rule(())}
        item = Text(default='')
        source = Reference()
        reversal = Boolean(default=False)

    class Job(Model):
        Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(Hidden, rows='rows', on='done'),)})
        permissions = ALL
        status = State(('open', 'done'), moves=(Move('open', 'done', 'Done'),), locked=('done',))

        def rows(self):
            return [{'item': 'x'}]
    other = start(Database(tmp_path / 'hidden.sqlite3', Registry(Hidden, Job)))
    admin, editor = client(other), add_user(other, 'editor')
    url = f"/api/job/{editor('POST', '/api/job', {})[1]['id']}"
    assert move(editor, url, 'done')[0] == 200
    # A reader of no target sees a `changed` entry with nothing in it, like a move whose State it cannot read.
    assert history(editor, url)[0] == {**history(editor, url)[0], 'kind': 'changed', 'data': {}, 'partial': True}
    assert history(admin, url)[0]['data'] == {'targets': [{'target': 'hidden', 'rows': 1, 'reversal': False}]}


def check_a_rolled_back_move_leaves_no_effects_entry(books, monkeypatch):
    from evlet import journal
    from evlet.auth import User
    app, c, office = books
    record_id = bill(c, office)
    # The number entry is written after the effects entry; failing there rolls the savepoint back.
    def fail(*args):
        raise RuntimeError('number entry failed')
    with app.business.context(User(1, 'admin', 'admin')) as context:
        monkeypatch.setattr(journal, 'number', fail)
        with pytest.raises(RuntimeError):
            context.move(Bill, record_id, 'issued')
        monkeypatch.undo()
    assert [item['kind'] for item in history(c, f'/api/bill/{record_id}')] == ['created']
    assert entries(app, 'posting') == [] and entries(app, 'audit') == []
    assert move(c, f'/api/bill/{record_id}', 'issued')[0] == 200
    assert [item['kind'] for item in history(c, f'/api/bill/{record_id}')] == ['number', 'effects', 'moved', 'created']


def test_a_rolled_back_move_leaves_no_effects_entry(tmp_path, monkeypatch):
    check_a_rolled_back_move_leaves_no_effects_entry(stacked_books(Database(tmp_path / 'stacked.sqlite3', Registry(*STACKED))), monkeypatch)


def test_models_with_effects_journal_by_default(tmp_path):
    from evlet import Journal
    from evlet.journal import compile_journals
    assert Registry(*MODELS).journals[Invoice] == Journal()
    # Effects need a State, which journals already; the rule names Effects too, so a model is journaled by either.
    plain = type('Loose', (Model,), {'name': Text(default='')})
    assert compile_journals((plain,), (), {plain: ()}) == {plain: Journal()} and compile_journals((plain,), ()) == {}
    # Meta.journal = None is the one way out: rows are posted, no entry is written.
    class Quiet(Model):
        Meta = type('Meta', (FiniteMeta,), {'journal': None, 'effects': (Effect(StockMove, rows='rows', on='done'),)})
        permissions = ALL
        status = State(('open', 'done'), moves=(Move('open', 'done', 'Done'),), locked=('done',))

        def rows(self):
            return [{'item': 'x', 'qty': '1'}]
    app = start(Database(tmp_path / 'quiet.sqlite3', Registry(StockMove, Quiet)))
    c = client(app)
    url = f"/api/quiet/{c('POST', '/api/quiet', {})[1]['id']}"
    assert Quiet not in app.business.journals and move(c, url, 'done')[0] == 200
    assert len(entries(app, 'stockmove')) == 1 and c('GET', f'{url}/journal')[0] == 404


# Posted records keep their calculated values

def posting_models(factor):
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
        note = Text(default='')

    class BillMeta(FiniteMeta):
        effects = (Effect('posting', rows='posting_rows', on='issued', reverse=Negate('amount')),)

    class Bill(Model):
        __resource__ = 'bill'
        Meta = BillMeta
        permissions = {'admin': Rule(('read', 'create', 'update', 'void'))}
        customer = Link(Customer)
        lines = Table(BillLine)
        fee = Currency(compute=F('customer.fee'))
        total = Currency(compute=lines.sum('amount') + F('fee'))
        memo = Text(default='', editable_when_locked=True, index=True)
        charge = Currency(applies_when=F('status') == 'paid')
        charged = Currency(compute=F('charge'))
        status = State(('draft', 'approved', 'issued', 'paid', 'void'),
                       moves=(Move('draft', 'approved', 'Approve'), Move(('draft', 'approved'), 'issued', 'Issue'), Move('issued', 'paid', 'Pay')),
                       locked=('approved', 'issued', 'paid', 'void'), final=('paid', 'void'), voidable=('issued',))

        def posting_rows(self):
            return [{'amount': self.total}]

        @action('Note', inputs={'memo': Text(required=True)})
        def note(self, memo):
            self.memo = memo

        @action('Bump')
        def bump(self):
            self.lines[0].qty = self.lines[0].qty + 10

        @action('Label line')
        def label_line(self):
            self.lines[0].note = 'checked'

        @action('Add line')
        def add_line(self):
            self.lines = [*self.lines, {'qty': '1', 'rate': '2'}]

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


def test_posted_records_keep_their_calculated_values_on_moves_and_actions(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(*posting_models(1))))
    c = client(app)
    customer = c('POST', '/api/customer', {'fee': '1'})[1]['id']
    url = new_bill(c, customer)
    assert move(c, url, 'issued')[1]['total'] == '4.00'
    assert c('PATCH', f'/api/customer/{customer}', {'fee': '10'}, headers=etag(c, f'/api/customer/{customer}'))[0] == 200
    status, body, _ = c('POST', url + '/actions/note', {'memo': 'called'}, headers=etag(c, url))
    assert status == 200 and (body['memo'], body['total'], body['fee']) == ('called', '4.00', '1.00'), body
    assert effects.check(app) == []
    # A deploy that changes a formula: moves and actions still work and keep the saved values.
    app.database.close()
    app, c = open_bills(tmp_path / 'db.sqlite3', 2)
    plan = app.business.registry.plan_for(app.business.registry.models['bill'])
    recalculate = {'_calculation': {'model_schema_hash': plan['model_schema_hash'], 'recalculate': True}}
    for status, body, _ in (c('POST', url + '/actions/note', {'memo': 'again', **recalculate}, headers=etag(c, url)),
                            c('PATCH', url, {'memo': 'again', **recalculate}, headers=etag(c, url))):
        assert status == 409 and body['error'] == 'This record has postings. To change its totals, void or revise it.'
    status, body, _ = c('POST', url + '/actions/note', {'memo': 'later'}, headers=etag(c, url))
    assert status == 200 and body['total'] == '4.00', body
    status, body, _ = move(c, url, 'paid')
    assert status == 200 and (body['status'], body['total'], body['children']['lines'][0]['amount']) == ('paid', '4.00', '3.00'), body
    assert effects.check(app) == []


def test_recompute_skips_posted_records_and_journals_the_rest(tmp_path, caplog):
    from evlet import AddIndex, Migration, Recompute
    path, base = tmp_path / 'db.sqlite3', Migration('0000_base', AddIndex('bill', 'memo'))
    app = start(Database(path, Registry(*posting_models(1)), migrations=[base]))
    c = client(app)
    customer = c('POST', '/api/customer', {})[1]['id']
    draft, approved, issued, voided = (new_bill(c, customer) for _ in range(4))
    assert move(c, approved, 'approved')[0] == 200
    for url in (issued, voided):
        assert move(c, url, 'issued')[0] == 200
    assert c('POST', voided + '/void', {'reason': 'wrong'}, headers=etag(c, voided))[0] == 200
    app.database.close()
    steps = (base, Migration('0001_recompute', Recompute('bill_lines', ('amount',)), Recompute('bill', ('fee', 'total', 'charged'))),)
    with caplog.at_level('INFO', logger='evlet.migrations'):
        db = Database(path, Registry(*posting_models(2)), migrations=list(steps))
        applied = db.migrate(db.migrations)
    assert applied == ['0001_recompute'] and applied.skipped == 2
    assert 'kept the saved results of 2 posted records' in caplog.text
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    app.initialize()
    c = client(app)
    # A locked record that never posted is recalculated; posted ones, voided or not, keep their results.
    assert [c('GET', url)[1]['total'] for url in (draft, approved, issued, voided)] == ['6.00', '6.00', '3.00', '3.00']
    assert effects.check(app) == []
    for url in (draft, approved):
        latest = c('GET', url + '/journal')[1]['items'][0]
        assert latest['kind'] == 'recomputed' and latest['actor'] is None
        assert latest['data'] == {'fields': {'total': {'old': '3.00', 'new': '6.00'}},
                                  'rows': {'lines': {'updated': [{'id': latest['data']['rows']['lines']['updated'][0]['id'],
                                                                  'fields': {'amount': {'old': '3.00', 'new': '6.00'}}}]}}}
    assert [item['kind'] for item in c('GET', issued + '/journal')[1]['items']].count('recomputed') == 0
    # Posted records stay on their old formula version; recalculated ones move to the active one.
    for url in (draft, approved):
        status, body, _ = move(c, url, 'issued')
        assert status == 200 and body['total'] == '6.00', body
    assert effects.check(app) == []


def test_posted_records_refuse_changes_to_what_their_results_read(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(*posting_models(1))))
    c = client(app)
    customer = c('POST', '/api/customer', {})[1]['id']
    url = new_bill(c, customer)
    # Before posting, actions may change inputs and add rows; results follow.
    status, body, _ = c('POST', url + '/actions/add_line', {}, headers=etag(c, url))
    assert status == 200 and body['total'] == '5.00' and len(body['children']['lines']) == 2, body
    status, body, _ = c('POST', url + '/actions/bump', {}, headers=etag(c, url))
    assert status == 200 and body['total'] == '20.00', body
    assert move(c, url, 'issued')[0] == 200
    refused = {'bump': 'Quantity feeds posted totals. To change it, void or revise the record.',
               'add_line': 'Lines feed posted totals. To change them, void or revise the record.'}
    for name, message in refused.items():
        status, body, _ = c('POST', f'{url}/actions/{name}', {}, headers=etag(c, url))
        assert (status, body['error']) == (409, message), body
    # Fields no stored result reads may still change.
    status, body, _ = c('POST', url + '/actions/label_line', {}, headers=etag(c, url))
    assert status == 200 and body['children']['lines'][0]['note'] == 'checked', body
    status, body, _ = c('POST', f'{url}/moves/paid', {'charge': '5'}, headers=etag(c, url))
    assert (status, body['error']) == (409, 'Charge feeds posted totals. To change it, void or revise the record.'), body
    status, body, _ = move(c, url, 'paid')
    assert status == 200 and (body['status'], body['total']) == ('paid', '20.00'), body
    assert effects.check(app) == []


def test_draft_preview_of_a_locked_or_posted_record_shows_the_kept_results(tmp_path):
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
    status, body, _ = c('PATCH', approved, {'memo': 'x'}, headers=etag(c, approved))
    assert status == 200 and (body['fee'], body['total']) == previews[approved], body
