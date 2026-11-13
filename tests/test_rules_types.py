"""Phase 2: initial suggestions, field rules, Recompute and the new field types on real SQLite."""

from datetime import time

import pytest

from evlet import (AddField, AddIndex, Application, RenameField, Boolean, Child, Currency, Database, Decimal, Duration, Integer, Link, LocalAuth,
                   Migration, Model, Percent, Phone, Recompute, Ref, Reference, Registry, Rule, SchemaError, Select, Table,
                   Tags, Text, Time, User, ValidationError, action)
from evlet.calc import CalcError, D, Document, F, parent, when
from evlet.identity import RecordId, encode
from test_business import http, client, PASSWORD
from finite_fixtures import FiniteMeta

ALL = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}


def no_bad(value):
    if value == 'bad':
        raise ValueError('Not allowed')


class Client(Model):
    permissions = {**ALL, 'editor': Rule(('read',), read_fields=('title', 'rate', 'credit_days')),
                   'viewer': Rule(('read',), read_fields=('title',))}
    title = Text(required=True)
    rate = Currency(default=0)
    credit_days = Integer(default=30)
    secret_limit = Currency(default=0)


class Product(Model):
    permissions = ALL
    title = Text(default='')
    price = Currency(default=0)


class DealLine(Child):
    product = Link(Product)
    qty = Decimal(default=1)
    rate = Currency(initial=F('product.price'))
    amount = Currency(compute=F('qty') * F('rate'))


class Deal(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='Deal')
    client = Link(Client)
    stage = Select(('open', 'won', 'lost'), default='open')
    terms = Integer(initial=F('client.credit_days'))
    discount = Currency(initial=F('client.rate'), locked_when=F('stage') == 'won')
    reason = Text(applies_when=F('stage') == 'lost', required_when=F('stage') == 'lost', validator=no_bad)
    note = Text(required_when=F('client.credit_days') > 60)
    approved = Boolean(default=False, locked_when=F('client.secret_limit') > 100)
    lines = Table(DealLine)
    total = Currency(compute=lines.sum('amount'))

    @action
    def win(self):
        self.stage = 'won'

    @action
    def bump(self):
        self.discount = (self.discount or 0) + 1


def application(tmp_path, *models, migrations=None):
    return start(Database(tmp_path / 'db.sqlite3', Registry(*models), migrations=migrations))


def start(db):
    """An initialized application with an admin account."""
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    return app


def add_user(app, role):
    assert client(app)('POST', '/api/auth/users', {'username': role, 'password': PASSWORD, 'role': role})[0] == 201
    return client(app, role)


def patch(c, url, body):
    _, _, headers = c('GET', url)
    return c('PATCH', url, body, headers={b'if-match': headers[b'etag']})


@pytest.fixture
def deals(tmp_path):
    return seed_deals(application(tmp_path, Client, Product, Deal))


def seed_deals(app):
    c = client(app)
    one = c('POST', '/api/client', {'title': 'One', 'rate': '2.50', 'credit_days': 30})[1]['id']
    two = c('POST', '/api/client', {'title': 'Two', 'rate': '4.00', 'credit_days': 10})[1]['id']
    pen = c('POST', '/api/product', {'title': 'Pen', 'price': '1.20'})[1]['id']
    ink = c('POST', '/api/product', {'title': 'Ink', 'price': '7.00'})[1]['id']
    return app, c, {'one': one, 'two': two, 'pen': pen, 'ink': ink}


# Definitions and the dependency graph

def test_option_combinations_are_rejected_when_declared():
    for options in ({'initial': F('a'), 'default': 1}, {'initial': F('a'), 'compute': F('a')}, {'initial': F('a'), 'fetch': 'a.b'},
                    {'compute': F('a'), 'locked_when': F('b')}, {'applies_when': F('b'), 'required': True},
                    {'required_when': F('b'), 'required_if': 'b'}, {'locked_when': F('b'), 'read_only_if': 'b'},
                    {'initial': 'a * 2'}, {'applies_when': lambda record: True}):
        with pytest.raises(ValueError):
            Integer(**options)
    with pytest.raises(ValueError, match='currency'):
        Currency(currency='dirham!')

    class Wrong(Model):
        amount = Currency(currency='code')
    with pytest.raises(ValueError, match='currency'):
        Registry(Wrong)


def test_initial_and_rules_join_the_dependency_order_and_cycles_fail():
    plan = Registry(Deal, Client, Product).plans[Deal]
    order = [field['target'] for field in plan['fields']]
    assert order.index('lines[*].rate:initial') < order.index('lines[*].amount') < order.index('total')
    assert order.index('discount:locked_when') < order.index('discount:initial')
    assert {field['kind'] for field in plan['fields'] if field.get('kind')} == {'initial', 'applies_when', 'required_when', 'locked_when'}

    def build(**fields):
        return Registry(type('Cyclic', (Model,), {'Meta': FiniteMeta, **fields}))
    with pytest.raises(CalcError, match='MODEL_DEPENDENCY_CYCLE: a -> b:initial -> a'):
        build(b=Decimal(initial=F('a') + 1), a=Decimal(compute=F('b') * 2))
    with pytest.raises(CalcError, match='MODEL_DEPENDENCY_CYCLE'):
        build(a=Decimal(initial=F('a') + 1))
    with pytest.raises(CalcError, match='MODEL_DEPENDENCY_CYCLE'):
        build(a=Text(applies_when=F('a') == 'x'))
    with pytest.raises(CalcError, match='COMPUTE_TYPE_ERROR'):
        build(a=Decimal(), b=Text(required_when=F('a') + 1))
    with pytest.raises(CalcError, match='COMPUTE_TYPE_ERROR'):
        build(a=Decimal(), b=Text(initial=F('a')))
    for opaque in (Tags(), Time(), Reference()):
        with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
            build(a=opaque, b=Boolean(compute=F('a') == None))  # noqa: E711
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        build(a=Tags(initial=F('b')), b=Text())

    class Shift(Model):
        starts = Time()
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        Registry(Shift, type('Roster', (Model,), {'Meta': FiniteMeta, 'shift': Link(Shift),
                                                  'flag': Boolean(compute=F('shift.starts') == None)}))  # noqa: E711


def test_document_sessions_report_rule_states():
    plan = Registry(Deal, Client, Product).plans[Deal]
    document = Document(plan, {'stage': 'open', 'reason': 'dropped'})
    root = document.root.handle
    assert document.snapshot()['reason'] is None  # A value that does not apply is cleared.
    assert document.rule_states(root)['reason'] == {'applies': False, 'required': False, 'locked': False}
    # Undecided rules fail closed: no client means credit_days is unknown, so the note is required.
    assert document.rule_states(root)['note']['required'] is True
    document.set(root, 'stage', 'lost')
    assert document.rule_states(root)['reason'] == {'applies': True, 'required': True, 'locked': False}
    document.set(root, 'stage', 'won')
    # A new document has no saved state, so nothing on it is locked.
    assert document.rule_states(root)['discount']['locked'] is False
    saved = Document(plan, {'id': 4, 'stage': 'won'})
    assert saved.rule_states(saved.root.handle)['discount']['locked'] is True


# initial= over HTTP

def test_initial_suggestions_follow_their_inputs_and_keep_user_edits(deals):
    app, c, ids = deals
    status, deal, _ = c('POST', '/api/deal', {'client': ids['one'], 'children': {'lines': [
        {'_key': 'a', 'product': ids['pen'], 'qty': '2'}, {'_key': 'b', 'product': ids['ink'], 'rate': '6.50'}]}})
    assert status == 201, deal
    url = f"/api/deal/{deal['id']}"
    assert (deal['terms'], deal['discount'], deal['total']) == (30, '2.50', '8.90')
    assert [line['rate'] for line in deal['children']['lines']] == ['1.20', '6.50']
    # A later edit is kept while the inputs stay the same.
    assert patch(c, url, {'discount': '7.00'})[1]['discount'] == '7.00'
    kept = patch(c, url, {'title': 'Renamed'})[1]
    assert (kept['discount'], kept['terms']) == ('7.00', 30)
    # A changed input suggests again; a value supplied in the same write wins.
    moved = patch(c, url, {'client': ids['two']})[1]
    assert (moved['discount'], moved['terms']) == ('4.00', 10)
    both = patch(c, url, {'client': ids['one'], 'discount': '9.00'})[1]
    assert (both['discount'], both['terms']) == ('9.00', 30)
    # An unchanged input does not suggest again, even when the linked record changed meanwhile.
    patch(c, f"/api/client/{ids['one']}", {'rate': '3.00'})
    assert patch(c, url, {'title': 'Again'})[1]['discount'] == '9.00'
    lines = both['children']['lines']
    swapped = patch(c, url, {'children': {'lines': [{'id': lines[0]['id'], 'product': ids['ink']}, {'id': lines[1]['id']},
                                                    {'_key': 'n', 'product': ids['pen']}]}})[1]
    assert [line['rate'] for line in swapped['children']['lines']] == ['7.00', '6.50', '1.20']
    assert swapped['total'] == '21.70'
    cleared = patch(c, url, {'client': None, 'note': 'Walk-in'})[1]
    assert (cleared['discount'], cleared['terms']) == (None, None)


def test_preview_shows_initial_suggestions(deals):
    app, c, ids = deals
    status, result, _ = c('POST', '/api/_draft/deal', {'values': {'client': ids['two'], 'children': {'lines': [{'_key': 'a', 'product': ids['ink']}]}}})
    assert status == 200, result
    assert (result['values']['discount'], result['values']['children']['lines'][0]['rate']) == ('4.00', '7.00')


# applies_when and required_when

def test_applies_when_clears_and_skips_validation_and_required_when_is_enforced(deals):
    app, c, ids = deals
    status, deal, _ = c('POST', '/api/deal', {'client': ids['one'], 'reason': 'bad'})
    assert status == 201 and deal['reason'] is None  # Not applying: cleared, and its validator does not run.
    url = f"/api/deal/{deal['id']}"
    status, result, _ = patch(c, url, {'stage': 'lost'})
    assert status == 422 and result['fields'] == {'reason': 'This field is required'}
    assert patch(c, url, {'stage': 'lost', 'reason': 'bad'})[1]['fields'] == {'reason': 'Not allowed'}
    lost = patch(c, url, {'stage': 'lost', 'reason': 'Price'})[1]
    assert lost['reason'] == 'Price'
    reopened = patch(c, url, {'stage': 'open'})[1]
    assert reopened['reason'] is None
    # An undecided rule counts as required: without a client the credit days are unknown.
    status, result, _ = c('POST', '/api/deal', {'title': 'No client'})
    assert status == 422 and result['fields'] == {'note': 'This field is required'}
    assert c('POST', '/api/deal', {'title': 'No client', 'note': 'Walk-in'})[0] == 201
    long = c('POST', '/api/client', {'title': 'Slow', 'credit_days': 90})[1]['id']
    assert c('POST', '/api/deal', {'client': long})[1]['fields'] == {'note': 'This field is required'}


# locked_when

def test_locked_fields_reject_changes_over_http_actions_and_context(deals):
    app, c, ids = deals
    deal = c('POST', '/api/deal', {'client': ids['one']})[1]
    url = f"/api/deal/{deal['id']}"
    _, _, headers = c('GET', url)
    won = c('POST', url + '/actions/win', {}, headers={b'if-match': headers[b'etag']})
    assert won[0] == 200 and won[1]['stage'] == 'won'
    status, result, _ = patch(c, url, {'discount': '1.00'})
    assert status == 422 and result['fields'] == {'discount': 'This field is locked and cannot be changed now'}
    assert patch(c, url, {'discount': '2.50', 'title': 'Same value'})[0] == 200
    _, _, headers = c('GET', url)
    status, result, _ = c('POST', url + '/actions/bump', {}, headers={b'if-match': headers[b'etag']})
    assert status == 422 and 'locked' in result['fields']['discount']
    with pytest.raises(ValidationError, match='locked'):
        with app.business.context(User(1, 'admin', 'admin')) as context:
            context.update(Deal, deal['id'], discount='5.00')
    # The engine does not overwrite a locked field; other suggestions still follow their inputs.
    moved = patch(c, url, {'client': ids['two']})[1]
    assert (moved['discount'], moved['terms']) == ('2.50', 10)
    # Nothing is locked on create: the record has no saved values to keep.
    status, created, _ = c('POST', '/api/deal', {'stage': 'won', 'discount': '1.00', 'note': 'x'})
    assert status == 201 and created['discount'] == '1.00'
    created = c('POST', '/api/deal', {'stage': 'won', 'client': ids['one']})[1]
    rate = c('GET', f"/api/client/{ids['one']}")[1]['rate']
    assert created['discount'] == rate and created['terms'] == 30
    assert c('GET', url)[1]['discount'] == '2.50'


def test_rules_over_unreadable_inputs_stay_enforced_without_revealing_them(deals):
    app, c, ids = deals
    secret = c('POST', '/api/client', {'title': 'Guarded', 'secret_limit': '500'})[1]['id']
    deal = c('POST', '/api/deal', {'client': secret})[1]
    url = f"/api/deal/{deal['id']}"
    editor = add_user(app, 'editor')
    status, result, _ = patch(editor, url, {'approved': True})
    assert status == 422 and result['fields'] == {'approved': 'This field is locked and cannot be changed now'}
    # The editor's schema omits the rule that reads the hidden limit; the admin's shows it.
    fields = {entry['name']: entry for model in editor('GET', '/api/schema')[1]['models'] if model['name'] == 'deal' for entry in model['fields']}
    assert fields['approved']['locked_when'] is None and fields['reason']['applies_when'] is not None
    assert fields['discount']['initial'] == {'$expr': 'hop', 'field': 'client', 'steps': [['client', 'rate', 'decimal']]}
    assert fields['total']['compute']['$expr'] == 'aggregate' and fields['total']['stored'] is True
    admin = {entry['name']: entry for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'deal' for entry in model['fields']}
    assert admin['approved']['locked_when']['$expr'] == 'gt'
    calculation = next(model for model in editor('GET', '/api/schema')[1]['models'] if model['name'] == 'deal')['calculation']
    targets = [field['target'] for field in calculation['plan']['fields']]
    assert 'approved:locked_when' not in targets and 'discount:initial' in targets
    # A pending lock is explained only to a role that can read what the rule reads.
    unknown = c('POST', '/api/deal', {'note': 'x'})[1]
    status, result, _ = patch(c, f"/api/deal/{unknown['id']}", {'approved': True})
    assert result['fields'] == {'approved': 'This field is locked until the values its lock rule reads are filled in'}
    status, result, _ = patch(editor, f"/api/deal/{unknown['id']}", {'approved': True})
    assert result['fields'] == {'approved': 'This field is locked and cannot be changed now'}


def test_initial_over_unreadable_inputs_is_withheld_like_a_calculation(tmp_path):
    class Vendor(Model):
        permissions = {**ALL, 'editor': Rule(('read',), read_fields=('title',))}
        title = Text(default='')
        cost = Currency(default=0)

    class Order(Model):
        Meta = FiniteMeta
        permissions = ALL
        vendor = Link(Vendor)
        price = Currency(initial=F('vendor.cost') * 2)
        memo = Text(default='')

    app = application(tmp_path, Vendor, Order)
    c = client(app)
    vendor = c('POST', '/api/vendor', {'cost': '4.00'})[1]['id']
    editor = add_user(app, 'editor')
    status, order, _ = editor('POST', '/api/order', {'vendor': vendor})
    assert status == 201 and 'price' not in order
    assert c('GET', f"/api/order/{order['id']}")[1]['price'] == '8.00'
    assert editor('POST', '/api/order', {'vendor': vendor, 'price': '1.00'})[0] == 403


# New field types

class Contact(Model):
    title = Text(default='')
    phone = Phone()
    share = Percent()
    opens = Time()
    spent = Duration()
    labels = Tags()
    about = Reference()
    price = Currency(currency='AED')
    code = Text(default='USD')
    local = Currency(currency='code')


def test_time_and_datetime_refuse_hour_24_on_every_python():
    from evlet import DateTime
    assert Time().validate('00:00') == time(0, 0) and Time().validate('23:59:59') == time(23, 59, 59)
    for bad in ('24:00', '24:00:00', '9:30', '12:60', '12:00:60', '12:00:00.5', '1200'):
        with pytest.raises(ValueError):
            Time().validate(bad)
    assert DateTime().validate('2026-01-01T23:59:59Z').hour == 23
    for bad in ('2026-01-01T24:00:00Z', '2026-01-01 24:00+04:00', '2026-01-01T9:00:00Z', '2026-01-01T10:00:00'):
        with pytest.raises(ValueError):
            DateTime().validate(bad)


def test_new_types_validate_and_serialize():
    assert Phone().validate(' +971 (50) 123-4567 ') == '+971501234567'
    assert Phone().validate('050 123 4567') == '050 123 4567'
    for bad in ('12a', '+1+2', '12', '+' + '1' * 16):
        with pytest.raises(ValueError):
            Phone().validate(bad)
    assert str(Percent().validate('12.5')) == '12.50' and Percent(maximum='200').validate('150') is not None
    for bad in ('-1', '100.01', '1.234'):
        with pytest.raises(ValueError):
            Percent().validate(bad)
    assert Time().validate('09:30') == time(9, 30) and Time().serialize(time(9, 30)) == '09:30:00'
    for bad in ('9:30', '24:00', time(1, 2, 3, 4)):
        with pytest.raises(ValueError):
            Time().validate(bad)
    assert Duration().validate('PT1H30M') == 5400 and Duration().validate('P1DT2S') == 86402 and Duration().validate(60) == 60
    assert [Duration().serialize(value) for value in (0, 5400, 86461)] == ['PT0S', 'PT1H30M', 'P1DT1M1S']
    for bad in ('PT', 'P', '1H', 'PT1.5H', -1, 1.5):
        with pytest.raises(ValueError):
            Duration().validate(bad)
    assert Tags().validate([' vip ', 'Gold']) == ('vip', 'Gold') and Tags().validate('vip, north,') == ('vip', 'north')
    for bad in (['a', 'A'], ['a,b'], ['"q"'], [''], ['x' * 41], [str(i) for i in range(21)], 'a, A', [1], 7):
        with pytest.raises(ValueError):
            Tags().validate(bad)
    assert Reference().validate({'model': 'product', 'id': 3}) == ('product', 3)
    for bad in ({'model': 'Product', 'id': 3}, {'model': 'product', 'id': 0}, {'model': 'product'}, 'product:3'):
        with pytest.raises(ValueError):
            Reference().validate(bad)
    # index=True indexes both columns together; a Reference is never unique.
    assert Reference(index=True).index
    with pytest.raises(ValueError, match="cannot be unique"):
        Reference(unique=True)
    with pytest.raises(ValueError, match='collide'):
        type('Clash', (Model,), {'about': Reference(), 'about_id': Integer()})
    assert Ref('parent.warehouse').path == 'parent.warehouse'
    with pytest.raises(TypeError):
        Ref()
    fields = {field['name']: field for field in Registry(Contact).ui_schema()['models'][0]['fields']}
    assert (fields['price']['currency'], fields['local']['currency_source'], fields['labels']['type'], fields['about']['type']) == ('AED', {'field': 'code'}, 'tags', 'reference')
    assert 'currency' not in fields['local'] and 'currency_source' not in fields['price']


def check_types_storage_and_filters(database):
    other = database.create(Contact, title='Target')
    first = database.create(Contact, title='A', phone='+44 20 7946 0000', share='12.5', opens='09:00', spent='PT2H',
                            labels=['vip', 'north'], about={'model': 'contact', 'id': other.id}, price='10.00')
    database.create(Contact, title='B', share='50', opens='14:30:00', spent=600, labels=['north'])
    loaded = database.get(Contact, first.id).to_dict()
    assert loaded | {'id': 0} == {'id': 0, 'name': None, 'title': 'A', 'phone': '+442079460000', 'share': '12.50', 'opens': '09:00:00', 'spent': 'PT2H',
                                  'labels': ['vip', 'north'], 'about': {'model': 'contact', 'id': other.id}, 'price': '10.00',
                                  'code': 'USD', 'local': None}

    def names(**filters):
        return sorted(record.title for record in database.list(Contact, filters=filters))
    assert names(labels__contains='north') == ['A', 'B'] and names(labels__contains='vip') == ['A'] and names(labels__contains='nort') == []
    assert names(about={'model': 'contact', 'id': other.id}) == ['A'] and names(about__isnull=True) == ['B', 'Target']
    assert names(spent__gte='PT1H') == ['A'] and names(spent__lt=3600) == ['B']
    assert names(opens__lt='12:00') == ['A'] and names(share__gt='20') == ['B'] and names(phone__startswith='+44') == ['A']
    with pytest.raises(ValidationError):
        database.list(Contact, filters={'about__contains': 'x'})
    with pytest.raises(ValidationError):
        database.list(Contact, order_by=('about',))
    with pytest.raises(ValidationError, match='does not exist'):
        database.create(Contact, about={'model': 'contact', 'id': 999})
    with pytest.raises(ValidationError, match='registered model'):
        database.update(Contact, first.id, about={'model': 'nothing', 'id': 1})
    assert database.update(Contact, first.id, about=None, labels=[]).to_dict()['labels'] == []


def test_new_types_store_and_filter_on_sqlite(tmp_path):
    database = Database(tmp_path / 'types.sqlite3', Registry(Contact))
    database.initialize()
    check_types_storage_and_filters(database)
    with database._connection() as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(contact)')}
    assert {'about_model', 'about_id'} <= columns and 'about' not in columns


def add_types(make):
    """Add each new type to an existing table with AddField and check the stored rows."""
    class Contact(Model):
        title = Text(default='')
    database = make(Registry(Contact), [])
    database.create(Contact, title='Existing')

    class Contact(Model):  # noqa: F811 - the upgraded definition
        title = Text(default='')
        phone = Phone()
        share = Percent(default='5')
        opens = Time()
        spent = Duration(default=0)
        labels = Tags()
        about = Reference()
    migration = Migration('0001_types', AddField('contact', 'phone', Phone()), AddField('contact', 'share', Percent(default='5')),
                          AddField('contact', 'opens', Time()), AddField('contact', 'spent', Duration(default=0)),
                          AddField('contact', 'labels', Tags()), AddField('contact', 'about', Reference()))
    upgraded = make(Registry(Contact), [migration], migrate=True)
    with pytest.raises(SchemaError, match='null backfill'):
        AddField('contact', 'other', Reference(), backfill={'model': 'contact', 'id': 1}).apply(None)
    record = upgraded.list(Contact)[0]
    assert record.to_dict() | {'id': 0} == {'id': 0, 'name': None, 'title': 'Existing', 'phone': None, 'share': '5.00', 'opens': None,
                                            'spent': 'PT0S', 'labels': None, 'about': None}
    updated = upgraded.update(Contact, record.id, about={'model': 'contact', 'id': record.id}, labels=['a'])
    assert updated.to_dict()['about'] == {'model': 'contact', 'id': record.id}
    return upgraded


def test_new_types_can_be_added_by_migration_on_sqlite(tmp_path):
    def make(registry, migrations, migrate=False):
        database = Database(tmp_path / 'add.sqlite3', registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    add_types(make)


class Secret(Model):
    permissions = {**ALL, 'viewer': Rule(())}
    title = Text(default='')


class Memo(Model):
    permissions = ALL
    title = Text(default='')
    about = Reference()


def test_references_check_existence_and_hide_unreadable_targets_over_http(tmp_path):
    app = application(tmp_path, Secret, Memo)
    c = client(app)
    secret = c('POST', '/api/secret', {'title': 'x'})[1]['id']
    status, memo, _ = c('POST', '/api/memo', {'about': {'model': 'secret', 'id': secret}})
    assert status == 201 and memo['about'] == {'model': 'secret', 'id': secret}
    assert c('POST', '/api/memo', {'about': {'model': 'secret', 'id': encode(RecordId(secret) + 50)}})[0] == 404
    assert c('POST', '/api/memo', {'about': {'model': 'nothing', 'id': 1}})[0] == 422
    viewer = add_user(app, 'viewer')
    assert viewer('GET', f"/api/memo/{memo['id']}")[1]['about'] is None
    assert viewer('GET', '/api/memo')[1]['items'][0]['about'] is None
    query = '/api/memo?filters=%7B%22about%22%3A%7B%22model%22%3A%22secret%22%2C%22id%22%3A%22' + secret + '%22%7D%7D'
    assert viewer('GET', query)[0] == 403 and c('GET', query)[1]['total'] == 1
    assert viewer('GET', '/api/memo?filters=%7B%22about__isnull%22%3Afalse%7D')[0] == 403
    assert viewer('GET', query.replace('%22about%22', '%22about__eq%22'))[0] == 403 and c('GET', query.replace('%22about%22', '%22about__eq%22'))[1]['total'] == 1
    editor = add_user(app, 'editor')
    assert editor('POST', '/api/memo', {'about': {'model': 'memo', 'id': memo['id']}})[0] == 201


# Recompute

def bill_models(factor, *, divisor=None, with_memo=False):
    class BillLine(Child):
        qty = Decimal(default=1)
        rate = Currency(default=0)
        amount = Currency(compute=F('qty') * F('rate') * D(factor))

    class Bill(Model):
        Meta = FiniteMeta
        storage_name = 'bill'
        permissions = ALL
        title = Text(default='', index=True)
        zero = Decimal(default=0)
        lines = Table(BillLine)
        total = Currency(compute=lines.sum('amount') if divisor is None else lines.sum('amount') / F(divisor))
        if with_memo:
            memo = Text(default='')
    return Bill


BASE = Migration('0000_base', AddIndex('bill', 'title'))


def test_recompute_updates_stored_results_and_rolls_back_on_failure(tmp_path):
    check_recompute(application(tmp_path, bill_models('1'), migrations=[BASE]),
                    lambda registry, migrations: Database(tmp_path / 'db.sqlite3', registry, migrations=migrations))


def check_recompute(app, reopen):
    """app starts with bill_models('1') and BASE; reopen(registry, migrations) opens the same database."""
    c = client(app)
    bill = c('POST', '/api/bill', {'children': {'lines': [{'_key': 'a', 'qty': '0.3333', 'rate': '1.00'}, {'_key': 'b', 'qty': '2', 'rate': '1.25'}]}})[1]
    assert bill['total'] == '2.83'
    steps = [BASE, Migration('0001_recompute', Recompute('bill_lines', ('amount',)), Recompute('bill', ('total',)))]
    database = reopen(Registry(bill_models('1.5')), steps)
    assert database.migrate(steps) == ['0001_recompute']
    upgraded = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    upgraded.initialize()
    c = client(upgraded)
    url = f"/api/bill/{bill['id']}"
    loaded = c('GET', url)[1]
    # 0.3333 * 1.00 * 1.5 = 0.49995 rounds half up to 0.50; 2 * 1.25 * 1.5 = 3.75.
    assert [line['amount'] for line in loaded['children']['lines']] == ['0.50', '3.75'] and loaded['total'] == '4.25'
    # Every stored result was recalculated, so the document saves under the new formulas without a 409.
    assert patch(c, url, {'title': 'after'})[0] == 200

    broken = steps + [Migration('0002_fail', AddField('bill', 'memo', Text(default='')), Recompute('bill', ('total',)))]
    with pytest.raises(SchemaError, match='COMPUTE_DIVISION_BY_ZERO'):
        reopen(Registry(bill_models('1.5', divisor='zero', with_memo=True)), broken).migrate(broken)
    with database._connection() as connection:
        assert 'memo' not in connection.columns('bill')
        assert [row[0] for row in connection.execute('SELECT name FROM evlet_migrations ORDER BY position')] == ['0000_base', '0001_recompute']
    assert c('GET', url)[1]['total'] == '4.25'


def test_recompute_checksums_validation_and_string_formulas(tmp_path):
    assert Migration('m', Recompute('bill', ('total',))).checksum() != Migration('m', Recompute('bill', ('total', 'other'))).checksum()
    for fields in ((), 'total', ('a', 'a'), (1,)):
        with pytest.raises(ValueError):
            Recompute('bill', fields)

    class Sale(Model):
        storage_name = 'sale'
        label = Text(default='', index=True)
        qty = Decimal(default=1)
        rate = Currency(default=0)
        amount = Currency(compute='qty * rate')
    base = Migration('0000', AddIndex('sale', 'label'))
    database = Database(tmp_path / 'legacy.sqlite3', Registry(Sale), migrations=[base])
    database.initialize()
    with database.transaction() as tx:
        tx.connection.execute('INSERT INTO sale (qty, rate, amount) VALUES (30000, 2500000, 0)')  # rate 2.50 in millionths

    class Sale(Model):  # noqa: F811
        storage_name = 'sale'
        label = Text(default='', index=True)
        qty = Decimal(default=1)
        rate = Currency(default=0)
        amount = Currency(compute='qty * rate * 2')
    steps = [base, Migration('0001', Recompute('sale', ('amount',)))]
    Database(tmp_path / 'legacy.sqlite3', Registry(Sale), migrations=steps).migrate(steps)
    assert Database(tmp_path / 'legacy.sqlite3', Registry(Sale)).list(Sale)[0].to_dict()['amount'] == '15.00'
    with pytest.raises(SchemaError, match='stored calculated'):
        bad = steps + [Migration('0002', Recompute('sale', ('rate',)))]
        Database(tmp_path / 'legacy.sqlite3', Registry(Sale), migrations=bad).migrate(bad)


def test_formula_hash_ignores_suggestions_and_required_or_locked_rules():
    def build(**options):
        model = type('Hashed', (Model,), {'Meta': FiniteMeta, 'a': Decimal(), 'b': Text(**options), 'c': Decimal(compute=F('a') * 2)})
        registry = Registry(model)
        return registry.formula_hashes[model], registry.plans[model]['model_schema_hash']
    plain = build()
    for options in ({'initial': F('a') == 1}, {'required_when': F('a') > 1}, {'locked_when': F('a') > 1}):
        options = {'initial': when(F('a') > 1, 'big', 'small')} if 'initial' in options else options
        hashed, fingerprint = build(**options)
        assert hashed == plain[0] and fingerprint != plain[1], options
    assert build(applies_when=F('a') > 1)[0] != plain[0]


def test_durations_and_percents_calculate_as_numbers_over_http(tmp_path):
    class Visit(Model):
        Meta = FiniteMeta
        permissions = ALL
        minutes = Integer(default=0)
        length = Duration(compute=F('minutes') * 60)
        share = Percent(default='12.5')
        portion = Currency(compute=F('length') * F('share') / 100)

    app = application(tmp_path, Visit)
    c = client(app)
    status, visit, _ = c('POST', '/api/visit', {'minutes': 90})
    assert status == 201, visit
    assert (visit['length'], visit['share'], visit['portion']) == ('PT1H30M', '12.50', '675.00')


# Review fixes

def test_a_lock_is_decided_on_the_document_as_loaded(deals):
    app, c, ids = deals
    deal = c('POST', '/api/deal', {'client': ids['one']})[1]
    url = f"/api/deal/{deal['id']}"
    assert patch(c, url, {'stage': 'won'})[0] == 200
    # Unlocking and changing in one write is still a change to a locked field.
    status, result, _ = patch(c, url, {'stage': 'open', 'discount': '9.00'})
    assert status == 422 and 'locked' in result['fields']['discount']
    assert patch(c, url, {'stage': 'open'})[0] == 200
    assert patch(c, url, {'discount': '9.00'})[1]['discount'] == '9.00'


def test_a_self_referencing_lock_uses_the_saved_value(tmp_path):
    class Offer(Model):
        Meta = FiniteMeta
        permissions = ALL
        price = Currency(default=0, locked_when=F('price') > 100)

    app = application(tmp_path, Offer)
    c = client(app)
    # On create nothing is locked: there is no saved price yet, so 500 is accepted and then kept.
    high = c('POST', '/api/offer', {'price': '500'})[1]
    assert high['price'] == '500.00'
    status, result, _ = patch(c, f"/api/offer/{high['id']}", {'price': '50'})
    assert status == 422 and 'locked' in result['fields']['price']
    offer = c('POST', '/api/offer', {'price': '50'})[1]
    url = f"/api/offer/{offer['id']}"
    assert patch(c, url, {'price': '500'})[1]['price'] == '500.00'
    status, result, _ = patch(c, url, {'price': '50'})
    assert status == 422 and 'locked' in result['fields']['price']


def test_sessions_keep_the_loaded_lock_until_rebased():
    plan = Registry(Deal, Client, Product).plans[Deal]
    document = Document(plan, {'id': 3, 'stage': 'won', 'discount': '2.50'})
    root = document.root.handle
    assert document.rule_states(root)['discount']['locked'] is True
    document.set(root, 'stage', 'open')
    assert document.rule_states(root)['discount']['locked'] is True
    document.rebase()
    assert document.rule_states(root)['discount']['locked'] is False
    # A new document has no saved state: its rules follow the current values.
    fresh = Document(plan, {'stage': 'won'})
    fresh.set(fresh.root.handle, 'stage', 'open')
    assert fresh.rule_states(fresh.root.handle)['discount']['locked'] is False


def test_resending_an_unchanged_input_does_not_suggest_again(deals):
    app, c, ids = deals
    deal = c('POST', '/api/deal', {'client': ids['one']})[1]
    url = f"/api/deal/{deal['id']}"
    assert patch(c, url, {'terms': 45})[1]['terms'] == 45
    assert patch(c, url, {'client': ids['one']})[1]['terms'] == 45
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.update(Deal, deal['id'], client=ids['one'])['terms'] == 45
    plan = Registry(Deal, Client, Product).plans[Deal]
    document = Document(plan, {'id': 1, 'client': 7, 'terms': 45})
    document.set_lookups('client', 7, {'credit_days': 30, 'rate': '1', 'secret_limit': '0'})
    document.set(document.root.handle, 'client', 7)
    assert document.snapshot()['terms'] == 45 and document.trace['evaluated'] == 0


def test_legacy_child_tables_cannot_hold_finite_rows(tmp_path):
    from evlet.business import ChildTable

    class Parent(Model):
        permissions = ALL
        title = Text(default='')

    class Row(Model):
        permissions = ALL
        owner = Link(Parent, required=True)
        qty = Integer(default=0, locked_when=F('qty') > 5)
    db = Database(tmp_path / 'db.sqlite3', Registry(Parent, Row))
    with pytest.raises(ValueError, match=r'Table\(Child\)'):
        Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False),
                    children=(ChildTable(Parent, 'rows', Row, 'owner', 'Rows'),))


def test_shipped_plans_list_only_readable_lookups(deals):
    app, c, ids = deals
    editor = add_user(app, 'editor')
    plan = next(model for model in editor('GET', '/api/schema')[1]['models'] if model['name'] == 'deal')['calculation']['plan']
    lookups = [entry for table in plan['tables'].values() for entry in table.get('lookups', [])]
    assert lookups == [{'model': 'client', 'fields': ['credit_days', 'rate']}] or all('secret_limit' not in entry['fields'] for entry in lookups)
    assert 'secret_limit' not in str(plan)
    admin = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'deal')['calculation']
    assert 'secret_limit' in str(admin.get('plan', admin))


def test_no_field_is_locked_on_create_or_on_new_rows(deals):
    app, c, ids = deals
    # A field that locks once another is set may be entered after it, in the same create.
    status, result, _ = c('POST', '/api/deal', {'stage': 'won', 'client': ids['one'], 'discount': '0.01'})
    assert status == 201 and result['discount'] == '0.01', result
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.create(Deal, stage='won', discount='3.00', note='x')['discount'] == '3.00'
    # The draft preview of a new record reports nothing locked either.
    preview = c('POST', '/api/_draft/deal', {'values': {'stage': 'won', 'discount': '0.02', 'note': 'x'}})[1]
    assert 'discount' not in preview['errors'] and preview['states'].get('discount') != 'locked'
    # Once saved, the lock is decided on the saved record.
    status, result, _ = patch(c, f"/api/deal/{result['id']}", {'discount': '0.02'})
    assert status == 422 and 'locked' in result['fields']['discount']


def test_a_new_row_has_no_locks_but_a_saved_row_keeps_them(tmp_path):
    class Lot(Child):
        sealed = Boolean(default=False)
        code = Text(default='', locked_when=F('sealed') == True)  # noqa: E712

    class Shipment(Model):
        Meta = FiniteMeta
        permissions = ALL
        lots = Table(Lot)

    app = application(tmp_path, Shipment)
    c = client(app)
    # A sealed row with a code: on a new row both are accepted together.
    shipment = c('POST', '/api/shipment', {'children': {'lots': [{'_key': 'a', 'sealed': True, 'code': 'A1'}]}})
    assert shipment[0] == 201, shipment
    url, row = f"/api/shipment/{shipment[1]['id']}", shipment[1]['children']['lots'][0]['id']
    added = patch(c, url, {'children': {'lots': {'create': [{'_key': 'b', 'sealed': True, 'code': 'B1'}]}}})
    assert added[0] == 200 and [lot['code'] for lot in added[1]['children']['lots']] == ['A1', 'B1']
    status, result, _ = patch(c, url, {'children': {'lots': {'update': [{'id': row, 'code': 'A2'}]}}})
    assert status == 422 and 'locked' in str(result['fields'])


def test_recompute_stores_values_that_no_longer_apply_as_empty(tmp_path):
    check_recompute_clears(lambda registry, migrations: Database(tmp_path / 'clear.sqlite3', registry, migrations=migrations))


def check_recompute_clears(make):
    class Ticket(Model):
        storage_name = 'ticket'
        label = Text(default='', index=True)
        stage = Text(default='open')
        reason = Text()
        size = Decimal(default=0)
        total = Decimal(compute=F('size') * 2)
    base = Migration('0000', AddIndex('ticket', 'label'))
    database = make(Registry(Ticket), [base])
    database.initialize()
    with database.transaction() as tx:
        with tx.maintenance():
            created = tx.create(Ticket, stage='open', reason='kept?', size='3', total='6')

    class Ticket(Model):  # noqa: F811 - reason now applies only to lost tickets
        storage_name = 'ticket'
        label = Text(default='', index=True)
        stage = Text(default='open')
        reason = Text(applies_when=F('stage') == 'lost')
        size = Decimal(default=0)
        total = Decimal(compute=F('size') * 2)
    steps = [base, Migration('0001', Recompute('ticket', ('total',)))]
    upgraded = make(Registry(Ticket), steps)
    upgraded.migrate(steps)
    with upgraded.transaction() as tx:
        record = tx.get(Ticket, created.id)
    assert (record.reason, str(record.total)) == (None, '6.0000')


def test_renaming_a_reference_renames_both_columns(tmp_path):
    check_reference_rename(lambda registry, migrations: Database(tmp_path / 'rename.sqlite3', registry, migrations=migrations))


def check_reference_rename(make):
    class Card(Model):
        storage_name = 'card'
        label = Text(default='', index=True)
        about = Reference()
    base = Migration('0000', AddIndex('card', 'label'))
    database = make(Registry(Card), [base])
    database.initialize()
    first = database.create(Card, label='a')
    database.update(Card, first.id, about={'model': 'card', 'id': first.id})

    class Card(Model):  # noqa: F811
        storage_name = 'card'
        label = Text(default='', index=True)
        target = Reference()
    steps = [base, Migration('0001', RenameField('card', 'about', 'target'))]
    renamed = make(Registry(Card), steps)
    renamed.migrate(steps)
    assert renamed.get(Card, first.id).to_dict()['target'] == {'model': 'card', 'id': first.id}


def test_a_locked_field_is_not_cleared_by_its_applies_when(tmp_path):
    class Form(Model):
        Meta = FiniteMeta
        permissions = ALL
        stage = Select(('open', 'won'), default='open')
        kind = Select(('a', 'b'), default='a')
        gated = Text(applies_when=F('kind') == 'a', locked_when=F('stage') == 'won')

    app = application(tmp_path, Form)
    c = client(app)
    form = c('POST', '/api/form', {'gated': 'keep'})[1]
    url = f"/api/form/{form['id']}"
    assert patch(c, url, {'stage': 'won'})[0] == 200
    status, result, _ = patch(c, url, {'kind': 'b'})
    assert status == 422 and result['fields'] == {'gated': 'This field is locked, so a change that would clear it is not allowed'}
    assert c('GET', url)[1]['gated'] == 'keep'
    # The session keeps a saved locked value too, so a browser shows what the server keeps.
    document = Document(app.registry.plans[Form], {'id': 1, 'stage': 'won', 'kind': 'a', 'gated': 'keep'})
    document.set(document.root.handle, 'kind', 'b')
    assert document.snapshot()['gated'] == 'keep'
    assert patch(c, url, {'stage': 'open'})[0] == 200 and patch(c, url, {'kind': 'b'})[1]['gated'] is None


def test_shipped_plans_and_order_leave_out_hidden_tables_and_fields(tmp_path):
    class Hidden(Child):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
        v = Integer(default=0)

    class Item(Child):
        permissions = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'), read_fields=('qty',))}
        qty = Decimal(default=1)
        cost = Decimal(default=0)

    class Sheet(Model):
        Meta = FiniteMeta
        permissions = ALL
        title = Text(default='')
        items = Table(Item)
        secretrows = Table(Hidden)
        count = Integer(compute=items.count())

    app = application(tmp_path, Sheet)
    editor = add_user(app, 'editor')
    entry = next(model for model in editor('GET', '/api/schema')[1]['models'] if model['name'] == 'sheet')
    text = str(entry.get('calculation')) + str(entry.get('order'))
    assert 'secretrows' not in text and 'cost' not in text
    assert 'plan' in entry['calculation'] and entry['calculation']['plan']['tables']['$root']['tables'] == ['items']
    assert 'items.qty' in entry['order']
