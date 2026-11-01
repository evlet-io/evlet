"""Phase 6: effects posted on a move, reversed on void, read-only target rows and the check command, on real SQLite."""

from decimal import Decimal as D

import pytest

from evlet import (Boolean, Child, Currency, Database, Decimal, Effect, Integer, Link, Model, Move, Negate, Reference, Registry,
                   Rule, State, Swap, Table, Text, ValidationError, effects)
from evlet.auth import AccessError
from evlet.calc import F
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import ALL, add_user, start


class Account(Model):
    permissions = ALL
    name = Text(default='')


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
    name = Text(default='')
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
    account = c('POST', '/api/account', {'name': 'Sales'})[1]['id']
    return app, c, account


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def invoice(c, account, lines=(('a', '1', '0.10'), ('b', '2', '0.10')), **values):
    body = {'name': 'Invoice', 'account': account, **values,
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
        (10, 0, 'a', 'invoice', moved['id'], 0), (20, 0, 'b', 'invoice', moved['id'], 0), (0, 30, 'revenue', 'invoice', moved['id'], 0)]
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
        assert context.move(Invoice, int(good.rsplit('/', 1)[1]), 'issued')['status'] == 'issued'
    assert len(entries(app)) == 3
    with pytest.raises(ValidationError, match='not balanced'):
        with app.business.context(admin) as context:
            context.move(Invoice, int(bad.rsplit('/', 1)[1]), 'issued')
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
    account = c('POST', '/api/account', {'name': 'A'})[1]['id']
    for bad in ('source', 'reversal', 'unknown'):
        url = f"/api/rogue/{c('POST', '/api/rogue', {'account': account, 'bad': bad})[1]['id']}"
        assert move(c, url, 'issued')[1]['fields'] == {'_effects': f'LedgerEntry row 1 is not valid: {bad}: Not a field the rows may set'}
    assert entries(app) == []


def test_rows_see_the_saved_document_read_only(tmp_path):
    seen = []

    class Probe(Model):
        Meta = type('Meta', (FiniteMeta,), {'effects': (Effect(StockMove, rows='rows', on='issued'),)})
        permissions = ALL
        name = Text(default='')
        lines = Table(InvoiceLine)
        total = Currency(compute=lines.sum('amount'))
        status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued',))

        def rows(self):
            seen.append((self.status, str(self.total), [line.item for line in self.lines]))
            self.name = 'changed'
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
    row = entries(app)[0]['id']
    stock = entries(app, 'stockmove')[0]['id']
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
        (0, 10, 'a', voided['id'], 1), (0, 20, 'b', voided['id'], 1), (30, 0, 'revenue', voided['id'], 1)]
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
    sources = [(row['source_id'], row['reversal']) for row in entries(app)]
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
    record_id = int(url.rsplit('/', 1)[1])
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
    assert output.out.startswith('invoice 1 -> stockmove: missing; expected ') and output.err == '1 difference\n'
    # Nothing was written by the check.
    assert len(entries(app, 'stockmove')) == 1 and len(entries(app)) == 3


# Review findings

def main_branch(user):
    return {'name': 'main'}


class Branch(Model):
    # The admin role sees only the main branch; editors see every branch.
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'), scope=main_branch),
                   'editor': Rule(('read', 'create', 'update', 'delete'))}
    name = Text(default='')
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
    branch = editor('POST', '/api/branch', {'name': 'dubai'})[1]['id']
    url = f"/api/branchorder/{editor('POST', '/api/branchorder', {'branch': branch})[1]['id']}"
    # Admin cannot read the dubai branch, yet the engine posts rows linking to it.
    status, body, _ = move(editor, url, 'done')
    assert status == 200, body
    assert [row['branch'] for row in entries(app, 'branchentry')] == [branch] and len(entries(app, 'plainentry')) == 1
    # Link filters are decided on the stored record.
    closed = editor('POST', '/api/branch', {'name': 'closed', 'open': False})[1]['id']
    url = f"/api/branchorder/{editor('POST', '/api/branchorder', {'branch': closed})[1]['id']}"
    assert move(editor, url, 'done')[1]['fields'] == {
        '_effects': 'BranchEntry row 1 is not valid: branch: The selected record does not match the current filters'}


def test_links_to_missing_records_are_field_errors(branches):
    app, editor = branches
    branch = editor('POST', '/api/branch', {'name': 'main'})[1]['id']
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
    account = c('POST', '/api/account', {'name': 'A'})[1]['id']
    url = f"/api/big/{c('POST', '/api/big', {'account': account})[1]['id']}"
    headers = etag(c, url)
    log.clear()
    assert c('POST', f'{url}/moves/done', {}, headers=headers)[0] == 200
    inserts = [sql for sql in log if sql.startswith('INSERT INTO "ledgerentry"')]
    assert len(inserts) == 500 and len(log) - 500 < 60, (len(log), [sql[:80] for sql in log if not sql.startswith('INSERT')])
    log.clear()
    assert c('POST', f'{url}/void', {'reason': 'x'}, headers=etag(c, url))[0] == 200
    assert len(log) - 500 < 60, len(log)
