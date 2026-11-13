"""Meta.api: read-only models that only trusted Python writes, and models callers never reach, on real SQLite."""

import pytest

from evlet import (Action, Application, Column, Count, Currency, Database, Link, Report, Model, Policy, Reference,
                   Registry, Rule, State, Text, ValidationError, action)
from evlet.auth import LocalAuth, User
from evlet.permissions import calling
from finite_fixtures import FiniteMeta
from test_business import PASSWORD, client, http
from test_rules_types import ALL

FULL = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'))}


class Entry(Model):
    """A ledger-style model: everyone reads it, only the posting action writes it."""
    class Meta:
        api = 'read'
    permissions = FULL
    memo = Text(default='')
    amount = Currency(default=0)


class Book(Model):
    """A read-only document: states, moves, void and actions exist, and still no caller may use them."""
    Meta = type('Meta', (FiniteMeta,), {'api': 'read'})
    permissions = {**FULL, 'admin': Rule(('read', 'create', 'update', 'delete'))}
    memo = Text(default='')
    status = State(
        State.Step('draft'),
        State.Step('open', lock=True),
        State.Step('void', lock=True, final=True),
        rules=(
            State.Rule('draft', 'open', 'Open', key='open'),
            State.Rule('open', 'void', 'Void', key='void', by=('admin',), remark='required'),
        ))

    @action
    def rename(self):
        self.memo = 'renamed'


class Secret(Model):
    """No surface at all: only Python reaches it."""
    class Meta:
        api = None
    permissions = FULL
    code = Text(default='')


class Sale(Model):
    permissions = FULL
    memo = Text(default='')
    # Links into a model with no surface must be hidden.
    secret = Link(Secret, hidden=True)
    other = Reference()


class Order(Model):
    Meta = FiniteMeta
    permissions = FULL
    memo = Text(default='')

    @action
    def post(self):
        # Trusted code: an action handler writes the read-only document through its context.
        self.context.create(Book, memo=self.memo)


def post_sale(tx, user, sale):
    # Trusted code: a legacy action handler writes the read-only model through its session.
    tx.create(Entry, memo=sale.memo, amount='12.50')


MODELS = (Entry, Book, Secret, Sale, Order)


class Codes(Report):
    """A report over a model with no surface: trusted code reads it, and only its columns reach the roles it names."""
    title = 'Codes'
    roles = ('admin',)
    model = Secret
    columns = (Column('id', type='link', link='secret'), Column('code'), Column('count', type='integer', total=True, unit='codes', source='secret.code'))

    def run(self):
        page = self.context.list(Secret, order_by=('id',), limit=self.context.limit, offset=self.context.offset)
        [everything] = self.context.aggregate(Secret, measures={'count': Count()})
        return {'rows': [{'id': row['id'], 'code': row['code'], 'count': 1} for row in page['rows']], 'total': page['total'],
                'totals': {'count': everything['count']}}


def build(database):
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      actions=(Action(Sale, 'post', 'Post', post_sale), Action(Entry, 'touch', 'Touch', post_sale)), attachments=(Entry, Book), reports=(Codes,))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    assert admin('POST', '/api/auth/users', {'username': 'editor', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    return app, admin


@pytest.fixture
def shop(tmp_path):
    registry = Registry(*MODELS)
    database = Database(tmp_path / 'shop.sqlite3', registry)
    return build(database)


ADMIN = User(1, 'admin', 'admin')


def seeded(app):
    """One entry, one open book and one secret, written by trusted Python."""
    with app.database.transaction() as tx:
        entry = tx.create(Entry, memo='seed', amount='1.00').id
        secret = tx.create(Secret, code='s-1').id
    with app.business.context(ADMIN) as context:
        book = context.create(Book, memo='seed')['id']
        context.move(Book, book, 'open')
    return entry, book, secret


# Registration

def test_meta_api_values_are_checked():
    bad = type('Bad', (Model,), {'Meta': type('Meta', (), {'api': 'write'}), 'title': Text()})
    with pytest.raises(ValueError, match='Meta.api is "full", "read" or None'):
        Registry(bad)


def test_a_visible_link_to_a_model_without_surface_is_refused_at_startup():
    hidden = type('Hidden', (Model,), {'Meta': type('Meta', (), {'api': None}), 'code': Text()})
    leaky = type('Leaky', (Model,), {'hidden': Link(hidden)})
    with pytest.raises(ValueError, match=r'Leaky.hidden links to Hidden, which has Meta.api = None. Declare the Link hidden=True'):
        Registry(hidden, leaky)
    Registry(hidden, type('Quiet', (Model,), {'hidden': Link(hidden, hidden=True)}))
    # Between two models without a surface nothing reaches a caller, so the Link may show.
    Registry(hidden, type('Inner', (Model,), {'Meta': type('Meta', (), {'api': None}), 'hidden': Link(hidden)}))


def test_effect_rows_of_a_document_without_surface_hide_their_source():
    from evlet import Boolean, Effect, Negate
    def models(hidden):
        target = type('Tally', (Model,), {'source': Reference(hidden=hidden), 'reversal': Boolean(default=False), 'amount': Currency(default=0)})
        meta = type('Meta', (), {'api': None, 'effects': (Effect(target, rows='rows', on='done', reverse=Negate('amount')),)})
        state = State(State.Step('draft'), State.Step('done', lock=True, release=True), State.Step('void', lock=True, final=True),
                      rules=(State.Rule('draft', 'done', 'Done'), State.Rule('done', 'void', 'Void', remark='required')))
        return type('Source', (Model,), {'Meta': meta, 'status': state, 'rows': lambda self: []}), target
    with pytest.raises(ValueError, match=r'Tally.source names Source records, which has Meta.api = None'):
        Registry(*models(False))
    Registry(*models(True))


# Meta.api = "read"

WRITES = [
    ('POST', '/api/entry', {'memo': 'x'}),
    ('PATCH', '/api/entry/{entry}', {'memo': 'x'}),
    ('DELETE', '/api/entry/{entry}', None),
    ('POST', '/api/entry/{entry}/actions/touch', {}),
    ('POST', '/api/entry/{entry}/attachments', {'name': 'a.txt', 'content_type': 'text/plain', 'data': 'aGk='}),
    ('POST', '/api/entry/{entry}/journal', {'kind': 'note', 'body': 'hello'}),
    ('POST', '/api/book', {'memo': 'x'}),
    ('PATCH', '/api/book/{book}', {'memo': 'x'}),
    ('DELETE', '/api/book/{book}', None),
    ('POST', '/api/_draft/book', {'values': {'memo': 'x'}}),
    ('POST', '/api/book/{book}/actions/rename', {}),
    ('POST', '/api/book/{book}/moves/open', {}),
    ('POST', '/api/book/{book}/moves/void', {'remark': 'Wrong'}),
    ('POST', '/api/book/{book}/attachments', {'name': 'a.txt', 'content_type': 'text/plain', 'data': 'aGk='}),
    ('POST', '/api/book/{book}/prints/invoice/file', {}),
]


def test_every_write_route_family_is_refused_for_every_role(shop):
    app, admin = shop
    entry, book, _ = seeded(app)
    editor = client(app, 'editor')
    for caller in (admin, editor):
        for method, path, body in WRITES:
            status, payload, _ = caller(method, path.format(entry=entry, book=book), body)
            assert status == 403, (method, path, status, payload)
            assert 'read only here' in payload['error']
    # Reads still work, subject to each role's permissions.
    assert admin('GET', f'/api/entry/{entry}')[1]['memo'] == 'seed'
    assert [item['memo'] for item in editor('GET', '/api/entry')[1]['items']] == ['seed']
    assert admin('GET', f'/api/book/{book}')[1]['status'] == 'open'
    with app.database.transaction() as tx:
        assert [record.memo for record in tx.list(Entry)] == ['seed'] and [record.memo for record in tx.list(Book)] == ['seed']


def test_trusted_python_writes_a_read_model(shop):
    app, admin = shop
    sale = admin('POST', '/api/sale', {'memo': 'counter'})
    # A legacy action handler (tx) and a declared action (context) are trusted, even inside a request.
    assert admin('POST', f"/api/sale/{sale[1]['id']}/actions/post", {}, headers={b'if-match': sale[2][b'etag']})[0] == 200
    order = admin('POST', '/api/order', {'memo': 'from order'})
    status, done, _ = admin('POST', f"/api/order/{order[1]['id']}/actions/post", {}, headers={b'if-match': order[2][b'etag']})
    assert status == 200, done
    with app.database.transaction() as tx:
        assert [(record.memo, str(record.amount)) for record in tx.list(Entry)] == [('counter', '12.50')]
        assert [record.memo for record in tx.list(Book)] == ['from order']
    # Python sessions and contexts outside a request write as before.
    with app.business.context(ADMIN) as context:
        created = context.create(Book, memo='script')
        context.update(Book, created['id'], memo='script 2')
        context.delete(Book, created['id'])


def test_the_schema_offers_no_writes_and_states_why(shop):
    app, admin = shop
    models = {entry['name']: entry for entry in admin('GET', '/api/schema')[1]['models']}
    for name in ('entry', 'book'):
        assert models[name]['api'] == 'read'
        assert {'create', 'update', 'delete'} <= set(models[name]['permissions']) and not any(models[name]['permissions'].values())
        assert models[name]['actions'] == []
    assert 'api' not in models['sale']
    described = admin('GET', '/api/_discovery/entry')[1]
    assert described['operations'] == ['read'] and set(described['schemas']) == {'read'}


def test_controls_overrides_cannot_grant_writes_back(shop):
    app, admin = shop
    entry, _, _ = seeded(app)
    rule = {'ops': ['read', 'create', 'update', 'delete'], 'read': None, 'write': None, 'scope': None}
    status, payload, _ = admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'perm', 'key': 'editor|Entry', 'value': rule}]})
    assert status == 200, payload
    editor = client(app, 'editor')
    assert editor('POST', '/api/entry', {'memo': 'granted?'})[0] == 403
    assert editor('PATCH', f'/api/entry/{entry}', {'memo': 'granted?'})[0] == 403
    # The rule itself refuses before any Rule, also for a Policy built with an override in Python.
    policy = Policy(app.registry, overrides={('editor', Entry): Rule(('read', 'create', 'update', 'delete'))})
    with calling():
        assert not policy.allows(User(2, 'editor', 'editor'), Entry, 'create') and policy.allows(User(2, 'editor', 'editor'), Entry, 'read')
    assert policy.allows(User(2, 'editor', 'editor'), Entry, 'create')


def test_mcp_and_eva_tools_cannot_write_a_read_model(shop):
    app, admin = shop
    entry, _, _ = seeded(app)
    for tool, values in (('create_record', {'model': 'entry', 'values': {'memo': 'tool'}}),
                         ('update_record', {'model': 'entry', 'id': str(entry), 'values': {'memo': 'tool'}}),
                         ('delete_record', {'model': 'entry', 'id': str(entry)})):
        status, payload, _ = admin('POST', '/api/_tools/execute', {'tool': tool, 'arguments': values})
        assert status == 403, (tool, payload)
    assert admin('POST', '/api/_tools/execute', {'tool': 'get_record', 'arguments': {'model': 'entry', 'id': str(entry)}})[0] == 200
    with app.database.transaction() as tx:
        assert [record.memo for record in tx.list(Entry)] == ['seed']


# Meta.api = None

def test_a_model_without_surface_answers_404_everywhere(shop):
    app, admin = shop
    _, _, secret = seeded(app)
    for method, path, body in (('GET', '/api/secret', None), ('POST', '/api/secret', {'code': 'x'}), ('GET', f'/api/secret/{secret}', None),
                               ('PATCH', f'/api/secret/{secret}', {'code': 'x'}), ('DELETE', f'/api/secret/{secret}', None),
                               ('GET', '/api/secret/aggregate?measures=count:count', None), ('GET', f'/api/secret/{secret}/attachments', None),
                               ('POST', f'/api/secret/{secret}/journal', {'kind': 'note', 'body': 'x'}), ('GET', '/api/_discovery/secret', None),
                               ('GET', '/app/secret', None)):
        assert admin(method, path, body)[0] == 404, (method, path)
    assert 'secret' not in {entry['name'] for entry in admin('GET', '/api/schema')[1]['models']}
    assert 'secret' not in {entry['name'] for entry in admin('GET', '/api/_discovery')[1]['models']}
    assert 'Secret' not in {model['name'] for model in admin('GET', '/api/_controls/state')[1]['models']}
    assert not any(item['key'].endswith('|Secret') for item in admin('GET', '/api/_controls/overrides')[1]['items'])
    status, payload, _ = admin('POST', '/api/_tools/execute', {'tool': 'list_records', 'arguments': {'model': 'secret'}})
    assert status == 404, payload
    # A caller cannot point a Reference at it, and the hidden Link never shows.
    status, payload, _ = admin('POST', '/api/sale', {'memo': 'x', 'other': {'model': 'secret', 'id': str(secret)}})
    assert status in (403, 422), payload
    assert [record.memo for record in app.database.list(Sale)] == []
    with app.database.transaction() as tx:
        sale = tx.create(Sale, memo='linked', secret=secret).id
    assert 'secret' not in admin('GET', f'/api/sale/{sale}')[1]


def test_a_model_without_surface_stays_a_full_python_model(shop):
    app, _ = shop
    _, _, secret = seeded(app)
    with app.database.transaction() as tx:
        tx.update(Secret, secret, code='s-2')
        assert tx.get(Secret, secret).code == 's-2' and tx.count(Secret) == 1
    with pytest.raises(ValidationError):
        with app.database.transaction() as tx:
            tx.create(Secret, code=7)
    assert app.database.list(Secret)[0].code == 's-2'


def test_a_report_reads_a_model_without_surface_for_the_roles_it_names(shop):
    app, admin = shop
    _, _, secret = seeded(app)
    [entry] = [item for item in admin('GET', '/api/_reports')[1]['reports'] if item['name'] == 'Codes']
    # The report is listed; it never names the hidden model, as its model or as a link target.
    assert entry['model'] is None and {column['name']: column.get('link') for column in entry['columns']} == {'id': None, 'code': None, 'count': None}
    status, result, _ = admin('POST', '/api/_reports/Codes', {'filters': {}})
    assert status == 200, result
    assert [(row['id'], row['code']) for row in result['rows']] == [(str(secret), 's-1')] and result['totals']['values'] == {'count': '1'}
    # A role the report does not name gets neither the listing nor the rows.
    editor = client(app, 'editor')
    assert 'Codes' not in {item['name'] for item in editor('GET', '/api/_reports')[1]['reports']}
    assert editor('POST', '/api/_reports/Codes', {'filters': {}})[0] == 403
    # The model itself still has no surface.
    assert admin('GET', '/api/secret')[0] == 404 and admin('GET', f'/api/secret/{secret}')[0] == 404
    assert 'secret' not in {model['name'] for model in admin('GET', '/api/schema')[1]['models']}
    assert admin('POST', '/api/_tools/execute', {'tool': 'list_records', 'arguments': {'model': 'secret'}})[0] == 404
