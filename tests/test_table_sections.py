"""Section rows in Tables (Table(..., sections=True)): real SQLite.

A section row is a heading with an id, a label and a place among the rows. It is stored apart from the rows, so
validation, derive(), sums, postings and row queries see only real lines; `self.lines.sections()` gives the grouping.
HTTP writes create, rename, move and delete section rows with the same checks as rows."""

from decimal import Decimal as D

import pytest

from evlet import (Application, Child, Database, Decimal, Integer, Model, PrintDesign, Registry, Rule, Section, State, Move,
                   Table, Text, ValidationError, action)
from evlet.auth import LocalAuth
from evlet.printing import Column, Table as PrintTable, document_context
from evlet.sections import STORE
from test_business import PASSWORD, client, http

FULL = {'admin': Rule(('read', 'create', 'update', 'delete')),
        'editor': Rule(('read', 'create', 'update', 'delete')),
        'viewer': Rule(('read',))}
# What derive() and actions saw, for the assertions.
SEEN = []


class Line(Child):
    item = Text(max_length=40, required=True)
    qty = Decimal(scale=2, default=1, minimum=0)
    amount = Decimal(scale=2, default=0)
    warehouse = Text(max_length=40)

    class Meta:
        layout = (Section('Item', ('item',), description='What is sold.'),
                  Section('Stock', ('qty', 'amount', 'warehouse'), collapsed=True))


class Quote(Model):
    permissions = FULL

    class Meta:
        calculation_language = 'finite-v1'
        derives = ('line_count',)

    title_text = Text(max_length=40, default='quote')
    status = State(('draft', 'sent'), default='draft', locked=('sent',), moves=(Move('draft', 'sent', 'Send'),))
    lines = Table(Line, sections=True)
    line_count = Integer()
    total_qty = Decimal(scale=2, compute=lines.sum('qty'))
    total_amount = Decimal(scale=2, compute=lines.sum('amount'))

    def derive(self):
        SEEN.append(('derive', [line.item for line in self.lines], [(label, [line.item for line in rows]) for label, rows in self.lines.sections()]))
        self.line_count = len(self.lines)

    @action('Regroup')
    def regroup(self):
        # Assigning rows without sections keeps them; naming sections replaces them.
        first, *rest = list(self.lines)
        self.lines = [{'section': 'Main'}, first, {'section': 'Rest'}, *rest]

    @action('Drop empty lines')
    def drop_empty(self):
        self.lines = [line for line in self.lines if line.qty]


class Order(Model):
    """A document without calculations, so trusted session writes settle it (derive() runs)."""
    permissions = FULL

    class Meta:
        derives = ('line_count',)

    class Row(Child):
        item = Text(max_length=40, required=True)

    lines = Table(Row, sections=True)
    line_count = Integer()

    def derive(self):
        SEEN.append(('order', [line.item for line in self.lines], [(label, [line.item for line in rows]) for label, rows in self.lines.sections()]))
        self.line_count = len(self.lines)


class Contract(Model):
    """Revisable: a revise copies the document, its rows and its section rows."""
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise'))}

    class Meta:
        revisable = True

    class Row(Child):
        item = Text(max_length=40)

    status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued', 'void'),
                   final=('void',), voidable=('issued',))
    lines = Table(Row, sections=True)


class Plain(Model):
    permissions = FULL

    class Row(Child):
        note = Text(max_length=40)

    rows = Table(Row)


class QuotePrint(PrintDesign):
    model = Quote
    title = 'Quote'
    main = (PrintTable('lines', columns=(Column('item'), Column('qty'), Column('amount'))),)


@pytest.fixture
def app(tmp_path):
    SEEN.clear()
    database = Database(tmp_path / 'sections.sqlite3', Registry(Quote, Order, Contract, Plain))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


def add_user(app, role):
    from evlet.auth import password_hash
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (role, password_hash(PASSWORD), role))
    return client(app, role)


def patch(call, url, body):
    return call('PATCH', url, body, headers={b'if-match': call('GET', url)[2][b'etag']})


def shape(lines):
    return [('§ ' + line['section']) if 'section' in line else line['item'] for line in lines]


def create(call):
    status, data, _ = call('POST', '/api/quote', {'children': {'lines': [
        {'section': 'Goods'}, {'item': 'Shelf', 'qty': '4', 'amount': '8913.04'},
        {'section': 'Services'}, {'item': 'Install', 'qty': '1', 'amount': '4861'}, {'item': 'Deliver', 'qty': '1', 'amount': '2430.80'},
        {'section': 'Later'}]}})
    assert status == 201, data
    return data


def test_schema_marks_the_table_and_names_the_summed_fields(app):
    call = client(app)
    models = {model['name']: model for model in call('GET', '/api/schema')[1]['models']}
    lines = models['quote']['children'][0]
    assert lines['sections'] is True and lines['section_sums'] == ['qty', 'amount']
    assert 'sections' not in models['plain']['children'][0]
    # A row model declares its own form layout with Section blocks (titles and descriptions) for the row editor.
    layout = models['quote_lines']['layout']
    assert [(part['label'], part['fields'], part.get('description')) for part in layout] == [
        ('Item', ['item'], 'What is sold.'), ('Stock', ['qty', 'amount', 'warehouse'], None)]


def test_payload_places_section_rows_and_python_sees_only_real_lines(app):
    call = client(app)
    data = create(call)
    assert shape(data['children']['lines']) == ['§ Goods', 'Shelf', '§ Services', 'Install', 'Deliver', '§ Later']
    heading = data['children']['lines'][0]
    assert set(heading) == {'id', 'section'} and isinstance(heading['id'], str) and len(heading['id']) == 13
    # Sums and derive() count real lines only.
    assert data['line_count'] == 3 and data['total_qty'] == '6.00' and data['total_amount'] == '16204.84'
    assert SEEN[-1] == ('derive', ['Shelf', 'Install', 'Deliver'],
                        [('Goods', ['Shelf']), ('Services', ['Install', 'Deliver']), ('Later', [])])
    # Row queries never see section rows.
    with app.database.transaction() as tx:
        Rows = next(child.model for child in tx.registry.children if child.parent is Quote)
        assert [row.item for row in tx.list(Rows, filters={'evlet_owner': data['id']}, order_by=('evlet_position',))] == ['Shelf', 'Install', 'Deliver']
        assert tx.connection.execute(f'SELECT count(*) FROM {STORE}').fetchone()[0] == 3


def test_validation_and_defaults_skip_section_rows(app):
    call = client(app)
    # `item` is required on rows; a section row has no item and still saves.
    data = create(call)
    assert data['children']['lines'][0] == {'id': data['children']['lines'][0]['id'], 'section': 'Goods'}
    # A real row still needs its item.
    status, errors, _ = patch(call, f"/api/quote/{data['id']}", {'children': {'lines': {'create': [{'qty': '1'}]}}})
    assert status == 422
    # A section row takes no row fields, and a label is 1 to 200 characters.
    for bad in ({'section': 'X', 'qty': '1'}, {'section': ''}, {'section': 'x' * 201}, {'section': 5}):
        status, errors, _ = patch(call, f"/api/quote/{data['id']}", {'children': {'lines': {'create': [bad]}}})
        assert status == 422, bad


def test_change_sets_create_rename_move_and_delete_sections(app):
    call = client(app)
    data = create(call)
    url = f"/api/quote/{data['id']}"
    lines = data['children']['lines']
    goods, services, later = (line['id'] for line in lines if 'section' in line)
    status, data, _ = patch(call, url, {'children': {'lines': {
        'update': [{'id': goods, 'section': 'Products'}], 'delete': [later],
        'create': [{'section': 'Extras', '_key': 'extras'}, {'item': 'Bolt', 'qty': '2'}]}}})
    assert status == 200, data
    assert shape(data['children']['lines']) == ['§ Products', 'Shelf', '§ Services', 'Install', 'Deliver', '§ Extras', 'Bolt']
    assert data['_row_ids']['extras'] == data['children']['lines'][5]['id']
    # order moves rows and section rows together.
    ids = {line.get('section') or line['item']: line['id'] for line in data['children']['lines']}
    order = [ids[name] for name in ('Services', 'Install', 'Products', 'Shelf', 'Deliver', 'Extras', 'Bolt')]
    status, data, _ = patch(call, url, {'children': {'lines': {'order': order}}})
    assert shape(data['children']['lines']) == ['§ Services', 'Install', '§ Products', 'Shelf', 'Deliver', '§ Extras', 'Bolt']
    # A row never becomes a section row, and an id must belong to this Table.
    status, errors, _ = patch(call, url, {'children': {'lines': {'update': [{'id': ids['Shelf'], 'section': 'Nope'}]}}})
    assert status == 422 and 'cannot become a section' in errors['fields']['lines']
    status, _, _ = patch(call, url, {'children': {'lines': {'update': [{'id': '01hc1sd9vc1ye', 'section': 'Nope'}]}}})
    assert status == 422


def test_full_lists_and_rows_left_out(app):
    call = client(app)
    data = create(call)
    url = f"/api/quote/{data['id']}"
    rows = [line for line in data['children']['lines'] if 'item' in line]
    # A full list states the sections too; section rows left out are removed.
    status, data, _ = patch(call, url, {'children': {'lines': [{'section': 'All'}, *({'id': row['id']} for row in rows)]}})
    assert status == 200, data
    assert shape(data['children']['lines']) == ['§ All', 'Shelf', 'Install', 'Deliver']
    with app.database.transaction() as tx:
        assert tx.connection.execute(f'SELECT count(*) FROM {STORE}').fetchone()[0] == 1
    # A change set that leaves sections out keeps them in place.
    status, data, _ = patch(call, url, {'children': {'lines': {'create': [{'item': 'Bolt'}]}}})
    assert shape(data['children']['lines']) == ['§ All', 'Shelf', 'Install', 'Deliver', 'Bolt']


def test_tables_without_sections_refuse_section_rows(app):
    call = client(app)
    status, errors, _ = call('POST', '/api/plain', {'children': {'rows': [{'section': 'Goods'}]}})
    assert status == 422 and 'no section rows' in errors['fields']['rows']
    with pytest.raises(ValueError, match='field named section'):
        class Bad(Child):
            section = Text()
        Table(Bad, sections=True)
    with pytest.raises(ValueError, match='True or False'):
        Table(Line, sections='yes')


def test_forms_cannot_hold_sections():
    from evlet import Dialog
    with pytest.raises(ValueError, match='Tables of saved records'):
        class Ask(Dialog):
            class Row(Child):
                note = Text()
            rows = Table(Row, sections=True)


def test_derived_tables_cannot_hold_sections():
    with pytest.raises(ValueError, match='derive\\(\\) cannot build it'):
        class Built(Model):
            class Meta:
                derives = ('lines',)

            class Row(Child):
                note = Text()
            lines = Table(Row, sections=True)

            def derive(self):
                self.lines = []


def test_http_permission_checks_apply_to_section_rows(app):
    admin = client(app)
    data = create(admin)
    url = f"/api/quote/{data['id']}"
    viewer = add_user(app, 'viewer')
    # A role that cannot update the record cannot add, rename or remove its sections.
    status, _, _ = viewer('PATCH', url, {'children': {'lines': {'create': [{'section': 'Mine'}]}}},
                          headers={b'if-match': viewer('GET', url)[2][b'etag']})
    assert status == 403
    # A viewer reads the section rows with the record.
    assert shape(viewer('GET', url)[1]['children']['lines'])[0] == '§ Goods'


def test_row_permissions_gate_section_writes(tmp_path):
    class Limited(Model):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}

        class Meta:
            pass

        class Row(Child):
            permissions = {'admin': Rule(('read', 'update'))}
            note = Text(max_length=40)
        rows = Table(Row, sections=True)

    database = Database(tmp_path / 'limited.sqlite3', Registry(Limited))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    admin = client(app)
    status, data, _ = admin('POST', '/api/limited', {})
    assert status == 201, data
    # The row model has no create permission, so no section row can be added either.
    status, _, _ = patch(admin, f"/api/limited/{data['id']}", {'children': {'rows': {'create': [{'section': 'Goods'}]}}})
    assert status == 403


def test_locked_records_refuse_section_changes(app):
    call = client(app)
    data = create(call)
    url = f"/api/quote/{data['id']}"
    assert call('POST', f'{url}/moves/sent', {}, headers={b'if-match': call('GET', url)[2][b'etag']})[0] == 200
    goods = data['children']['lines'][0]['id']
    for change in ({'create': [{'section': 'New'}]}, {'update': [{'id': goods, 'section': 'Renamed'}]}, {'delete': [goods]}):
        status, errors, _ = patch(call, url, {'children': {'lines': change}})
        assert status == 422 and 'locked' in errors['fields']['lines'], change


def test_actions_assign_rows_with_and_without_sections(app):
    call = client(app)
    data = create(call)
    url = f"/api/quote/{data['id']}"
    status, data, _ = call('POST', f'{url}/actions/drop_empty', {}, headers={b'if-match': call('GET', url)[2][b'etag']})
    assert status == 200, data
    # Rows assigned without naming sections keep the sections in place.
    assert shape(data['children']['lines']) == ['§ Goods', 'Shelf', '§ Services', 'Install', 'Deliver', '§ Later']
    status, data, _ = call('POST', f'{url}/actions/regroup', {}, headers={b'if-match': call('GET', url)[2][b'etag']})
    assert status == 200, data
    assert shape(data['children']['lines']) == ['§ Main', 'Shelf', '§ Rest', 'Install', 'Deliver']


def test_trusted_session_writes_take_section_rows(app):
    with app.database.transaction() as tx:
        quote = tx.create(Order, children={'lines': [{'section': 'Goods'}, {'item': 'Shelf'}]})
        assert quote.line_count == 1
    assert SEEN[-1][2] == [('Goods', ['Shelf'])]
    with app.database.transaction() as tx:
        tx.update(Order, quote.id, children={'lines': {'create': [{'section': 'More'}, {'item': 'Bolt'}]}})
    assert SEEN[-1][2] == [('Goods', ['Shelf']), ('More', ['Bolt'])]
    call = client(app)
    assert shape(call('GET', f'/api/order/{quote.id}')[1]['children']['lines']) == ['§ Goods', 'Shelf', '§ More', 'Bolt']
    # Deleting the record removes its section rows.
    url = f'/api/order/{quote.id}'
    assert call('DELETE', url, headers={b'if-match': call('GET', url)[2][b'etag']})[0] in (200, 204)
    with app.database.transaction() as tx:
        assert tx.connection.execute(f'SELECT count(*) FROM {STORE}').fetchone()[0] == 0


def test_journal_records_section_changes(app):
    call = client(app)
    data = create(call)
    url = f"/api/quote/{data['id']}"
    goods = data['children']['lines'][0]['id']
    patch(call, url, {'children': {'lines': {'update': [{'id': goods, 'section': 'Products'}], 'create': [{'section': 'Extra'}]}}})
    status, history, _ = call('GET', f'{url}/history')
    assert status == 200, history
    entries = history['items'] if isinstance(history, dict) else history
    changed = [entry for entry in entries if entry.get('kind') == 'changed']
    sections = changed[0]['data']['rows']['lines']['sections']
    assert sections['renamed'] == [{'id': goods, 'old': 'Goods', 'new': 'Products'}]
    assert [item['label'] for item in sections['added']] == ['Extra']
    created = [entry for entry in entries if entry.get('kind') == 'created'][0]
    assert [item['label'] for item in created['data']['rows']['lines']['sections']['added']] == ['Goods', 'Services', 'Later']


def test_paged_lines_carry_their_section_rows(app):
    call = client(app)
    data = create(call)
    url = f"/api/quote/{data['id']}/lines/lines"
    first = call('GET', f'{url}?limit=1&offset=0')[1]
    second = call('GET', f'{url}?limit=1&offset=1')[1]
    last = call('GET', f'{url}?limit=1&offset=2')[1]
    assert first['total'] == 3
    assert shape(first['items']) == ['§ Goods', 'Shelf']
    assert shape(second['items']) == ['§ Services', 'Install']
    assert shape(last['items']) == ['Deliver', '§ Later']


def test_print_groups_rows_under_their_sections_with_sums(app):
    call = client(app)
    data = create(call)
    business = app.business
    from test_printing import user
    admin = user(app)
    with business.database.transaction() as tx:
        record = business.policy.record(tx, admin, Quote, data['id'])
        context = document_context(business, tx, admin, Quote, record)
    # doc.lines holds real rows only, so templates that loop over lines are unchanged.
    assert [row['item'] for row in context['doc']['lines']] == ['Shelf', 'Install', 'Deliver']
    groups = context['sections']['lines']
    assert [(group['label'], [row['item'] for row in group['rows']], group['sums']) for group in groups] == [
        ('Goods', ['Shelf'], {'qty': D('4.00'), 'amount': D('8913.04')}),
        ('Services', ['Install', 'Deliver'], {'qty': D('2.00'), 'amount': D('7291.80')}),
        ('Later', [], {'qty': D('0'), 'amount': D('0')})]


def test_revise_copies_section_rows(app):
    call = client(app)
    status, data, _ = call('POST', '/api/contract', {'children': {'lines': [{'section': 'A'}, {'item': 'one'}, {'section': 'B'}, {'item': 'two'}]}})
    assert status == 201, data
    url = f"/api/contract/{data['id']}"
    assert call('POST', f'{url}/moves/issued', {}, headers={b'if-match': call('GET', url)[2][b'etag']})[0] == 200
    status, copy, _ = call('POST', f'{url}/revise', {'reason': 'price change'}, headers={b'if-match': call('GET', url)[2][b'etag']})
    assert status in (200, 201) and copy['revises'] == data['id'], copy
    assert [line.get('section') or line['item'] for line in copy['children']['lines']] == ['A', 'one', 'B', 'two']
    # New section rows: the original keeps its own.
    original = {line['id'] for line in call('GET', url)[1]['children']['lines'] if 'section' in line}
    assert original.isdisjoint(line['id'] for line in copy['children']['lines'] if 'section' in line)


def test_postings_read_sections(app):
    from evlet.effects import load_document
    call = client(app)
    data = create(call)
    with app.database.transaction() as tx:
        document = load_document(app.business, tx, Quote, data['id'])
        assert [(label, [row.item for row in rows]) for label, rows in document.lines.sections()] == [
            ('Goods', ['Shelf']), ('Services', ['Install', 'Deliver']), ('Later', [])]
        assert [row.item for row in document.lines] == ['Shelf', 'Install', 'Deliver']


def test_turning_sections_on_and_off_needs_no_migration(tmp_path):
    path = tmp_path / 'toggle.sqlite3'

    def build(sections):
        class Order(Model):
            permissions = FULL

            class Row(Child):
                note = Text(max_length=40)
            rows = Table(Row, sections=sections)
        database = Database(path, Registry(Order))
        app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
        app.initialize()
        return app

    app = build(False)
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    call = client(app)
    status, data, _ = call('POST', '/api/order', {'children': {'rows': [{'note': 'a'}, {'note': 'b'}]}})
    assert status == 201
    # The same database starts with sections=True: no schema change, saved rows unchanged.
    app = build(True)
    call = client(app)
    url = f"/api/order/{data['id']}"
    status, data, _ = patch(call, url, {'children': {'rows': {'create': [{'section': 'Top'}], 'order': []}}})
    assert status == 200, data
    assert [row.get('section') or row['note'] for row in data['children']['rows']] == ['a', 'b', 'Top']
    # Off again: the rows are as they were and the stored headings are not shown (they come back if it is turned on).
    app = build(False)
    call = client(app)
    assert [row['note'] for row in call('GET', url)[1]['children']['rows']] == ['a', 'b']


def test_print_html_shows_section_heading_rows(app):
    pytest.importorskip('jinja2')
    import re
    from evlet.printing import Compiler
    from test_printing import user
    data = create(client(app))
    admin = user(app)
    with app.database.transaction() as tx:
        context = document_context(app.business, tx, admin, Quote, app.business.policy.record(tx, admin, Quote, data['id']))
    html = Compiler(QuotePrint, context, app.business).html()
    body = re.search(r'<tbody>(.*?)</tbody>', html, re.S).group(1)
    headings = re.findall(r'<tr class="evlet-section">(.*?)</tr>', body, re.S)
    cells = [[re.sub('<[^>]+>', '', cell) for cell in re.findall(r'<td[^>]*>(.*?)</td>', row)] for row in headings]
    assert [row[0] for row in cells] == ['Goods', 'Services', 'Later']
    assert cells[1][1] == '2' and cells[1][2] == '7,291.80'
    assert body.count('<tr>') == 3
