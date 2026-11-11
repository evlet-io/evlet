"""derive() with Meta.derives, and before_close(self, to): real SQLite, through the ASGI routes and Context.

derive() builds stored values formulas cannot express (one row per distinct value of another Table, rows
built from a linked record with a remainder row). It runs before calculations on every write and preview;
what it sets is cleaned, calculated, validated and returned, and callers cannot write it.

before_close() refuses a move into a final state, a void or a delete while other records depend on the
record, on every surface, before anything is written."""

from decimal import Decimal as D

import pytest

from evlet import (Application, Boolean, Child, Database, Decimal, Integer, Link, Model, Move, Registry, Rule, State, Table,
                   Text, ValidationError)
from evlet.auth import AccessError, KeyAccess, LocalAuth, User
from evlet.connector import ConnectorError
from evlet.tools import ApprovalNeeded
from test_business import PASSWORD, client, http

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}
ADMIN = User(1, 'admin', 'admin')
# What derive() saw of its context, and whether a test asks it to misbehave.
SEEN = []
MISBEHAVE = []


class Plan(Model):
    """A linked record derive() reads through the read-only session: how many parts to split into."""
    permissions = FULL
    label = Text(max_length=40, default='plan')
    parts = Integer(default=2)


class Trip(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void'))}

    class Meta:
        calculation_language = 'finite-v1'
        derives = ('places', 'place_count', 'longest', 'shares')

    class Stop(Child):
        place = Text(max_length=40, required=True)
        minutes = Integer(default=0)

    class PlaceTotal(Child):
        place = Text(max_length=40)
        stops = Integer(default=0)
        minutes = Decimal(scale=2)
        note = Text(max_length=40, default='derived')

    class Share(Child):
        amount = Decimal(scale=2)
        remainder = Boolean(default=False)

    title_text = Text(max_length=40, default='trip')
    remarks = Text(max_length=40, default='', editable_when_locked=True)
    plan = Link(Plan)
    amount = Decimal(scale=2, default=0)
    stops = Table(Stop)
    places = Table(PlaceTotal)
    place_count = Integer()
    longest = Decimal(scale=2)
    shares = Table(Share)
    # A calculation over rows derive() built: it runs after derive().
    total_minutes = Decimal(scale=2, compute=places.sum('minutes'))
    status = State(('open', 'closed', 'cancelled', 'void'), default='open',
                   moves=(Move('open', 'closed', 'Close'), Move('open', 'cancelled', 'Cancel'), Move('closed', 'cancelled', 'Cancel')),
                   locked=('closed', 'cancelled', 'void'), final=('cancelled', 'void'), voidable=('closed',))

    def derive(self):
        session = self.context.session
        SEEN.append({'create': hasattr(session, 'create'), 'connection': hasattr(session, 'connection'),
                     'readonly': self.context.readonly})
        if 'field' in MISBEHAVE:
            self.amount = 1
        if 'refuse' in MISBEHAVE:
            raise ValidationError({'amount': 'Refused by derive'})
        totals = {}
        for stop in self.stops:
            count, minutes = totals.get(stop.place, (0, 0))
            totals[stop.place] = (count + 1, minutes + (stop.minutes or 0))
        # Strings on purpose: what derive() sets is cleaned like caller input.
        self.places = [{'place': place, 'stops': count, 'minutes': f'{minutes}.00'} for place, (count, minutes) in sorted(totals.items())]
        self.place_count = len(totals)
        self.longest = f'{max((stop.minutes or 0 for stop in self.stops), default=0)}.5'
        if 'precise' in MISBEHAVE:
            # More decimal places than the fields allow, and a fraction for an Integer: refused like caller input.
            self.longest = D(1) / D(3)
            self.place_count = D('2.5')
            self.places = [{'place': place, 'stops': count, 'minutes': D('1.005')} for place, (count, _) in sorted(totals.items())]
        shares = []
        if self.plan is not None:
            plan = session.get(Plan, self.plan)
            part = (self.amount / plan.parts).quantize(D('0.01'), rounding='ROUND_DOWN')
            shares = [{'amount': part} for _ in range(plan.parts - 1)]
            shares.append({'amount': self.amount - part * (plan.parts - 1), 'remainder': True})
        self.shares = shares

    def before_close(self, to):
        SEEN.append({'close': to, 'readonly': self.context.readonly})
        if 'write' in MISBEHAVE:
            self.context.create(Plan, parts=9)
        legs = self.context.session.list(Leg, filters={'trip': self.id, 'status__in': ['open']}, order_by=('id',))
        if legs:
            raise ValidationError({'_form': 'Cancel the later documents first: ' + ', '.join(leg.label for leg in legs)})


class Leg(Model):
    """A record that depends on a trip while it is open."""
    permissions = FULL
    label = Text(max_length=40, required=True)
    trip = Link(Trip)
    status = State(('open', 'cancelled'), default='open', moves=(Move('open', 'cancelled', 'Cancel'),), final=('cancelled',))


class Factor(Model):
    """A factor table keyed on a pair of values, which derive() reads through the read-only session."""
    permissions = FULL
    origin = Text(max_length=10, required=True)
    target = Text(max_length=10, required=True)
    factor = Decimal(scale=4, required=True)


class Sheet(Model):
    """People edit the lines; derive() sets each line's net and converted values, which formulas cannot express."""
    permissions = FULL

    class Meta:
        calculation_language = 'finite-v1'
        derives = ('lines.net', 'lines.converted')

    class Line(Child):
        qty = Decimal(scale=2, default=1)
        price = Decimal(scale=2, default=0)
        discount = Decimal(scale=4, default=0)
        unit = Text(max_length=10, default='a')
        net = Decimal(scale=4)
        converted = Decimal(scale=4)

    decimals = Integer(default=2, minimum=0, maximum=4)
    target = Text(max_length=10, default='b')
    lines = Table(Line)
    # Reads the derived row field, so derive() ran first.
    total = Decimal(scale=4, compute=lines.sum('net'))

    def derive(self):
        if 'rows' in MISBEHAVE:
            self.lines = []
        if 'append' in MISBEHAVE:
            self.lines.append(self.lines[0])
        step = D(10) ** -self.decimals
        for line in self.lines:
            net = ((line.qty or 0) * (line.price or 0) * (1 - (line.discount or 0))).quantize(step)
            # A string on purpose: derived row values are cleaned too.
            line.net = str(net)
            found = self.context.session.list(Factor, filters={'origin': line.unit, 'target': self.target}, limit=1)
            line.converted = net * found[0].factor if found else None


@pytest.fixture
def app(tmp_path):
    SEEN.clear()
    MISBEHAVE.clear()
    database = Database(tmp_path / 'derive.sqlite3', Registry(Plan, Trip, Leg, Factor, Sheet))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app, client(app)


STOPS = [{'place': 'north', 'minutes': 10}, {'place': 'south', 'minutes': 5}, {'place': 'north', 'minutes': 20}]


def create(admin, **values):
    status, body, _ = admin('POST', '/api/trip', {'children': {'stops': STOPS}, **values})
    assert status == 201, body
    return body


def patch(admin, record_id, body):
    url = f'/api/trip/{record_id}'
    return admin('PATCH', url, body, headers={b'if-match': admin('GET', url)[2][b'etag']})


def move(admin, model, record_id, to):
    url = f'/api/{model}/{record_id}'
    return admin('POST', f'{url}/moves/{to}', {}, headers={b'if-match': admin('GET', url)[2][b'etag']})


def places(body):
    return [(row['place'], row['stops'], row['minutes'], row['note']) for row in body['children']['places']]


def journal_count(app):
    with app.database.transaction() as tx:
        return tx.connection.execute('SELECT count(*) FROM evlet_journal').fetchone()[0]


# derive(): order, cleaning and the save response

def test_derived_rows_and_fields_are_cleaned_calculated_and_returned_on_save(app):
    app, admin = app
    body = create(admin)
    # One row per distinct place, built from the other Table, with its declared default and cleaned Decimals.
    assert places(body) == [('north', 2, '30.00', 'derived'), ('south', 1, '5.00', 'derived')]
    assert body['place_count'] == 2 and body['longest'] == '20.50'
    # The calculation reads the derived rows, so derive() ran first.
    assert body['total_minutes'] == '35.00'
    with app.database.transaction() as tx:
        saved = tx.get(Trip, body['id'])
        assert saved.longest == D('20.50') and saved.total_minutes == D('35.00')
    # derive() saw a read-only context and a session with no write methods.
    assert SEEN[0] == {'create': False, 'connection': False, 'readonly': True}


def test_rows_built_from_a_linked_record_with_a_remainder_row(app):
    app, admin = app
    status, plan, _ = admin('POST', '/api/plan', {'parts': 3})
    assert status == 201, plan
    body = create(admin, plan=plan['id'], amount='100.00')
    assert [(row['amount'], row['remainder']) for row in body['children']['shares']] == [
        ('33.33', False), ('33.33', False), ('33.34', True)]
    # The rows follow the linked record on the next save.
    assert admin('PATCH', f"/api/plan/{plan['id']}", {'parts': 2},
                 headers={b'if-match': admin('GET', f"/api/plan/{plan['id']}")[2][b'etag']})[0] == 200
    status, body, _ = patch(admin, body['id'], {'amount': '10.01'})
    assert status == 200, body
    assert [(row['amount'], row['remainder']) for row in body['children']['shares']] == [('5.00', False), ('5.01', True)]


def test_derived_rows_keep_their_ids_when_they_come_out_the_same(app):
    app, admin = app
    body = create(admin)
    ids = [row['id'] for row in body['children']['places']]
    stop = body['children']['stops'][1]['id']
    status, after, _ = patch(admin, body['id'], {'children': {'stops': {'update': [{'id': stop, 'minutes': 7}]}}})
    assert status == 200, after
    assert [row['id'] for row in after['children']['places']] == ids
    assert places(after)[1] == ('south', 1, '7.00', 'derived') and after['total_minutes'] == '37.00'
    # A new place adds a row; the others keep theirs.
    status, after, _ = patch(admin, body['id'], {'children': {'stops': {'create': [{'_key': 'n1', 'place': 'east', 'minutes': 1}]}}})
    assert status == 200, after
    assert [row['place'] for row in after['children']['places']] == ['east', 'north', 'south']
    assert len({row['id'] for row in after['children']['places']} & set(ids)) == 2


def test_a_preview_runs_derive_before_its_calculations(app):
    app, admin = app
    status, body, _ = admin('POST', '/api/_draft/trip', {'values': {'children': {'stops': STOPS}}})
    assert status == 200, body
    values = body['values']
    assert [(row['place'], row['minutes']) for row in values['children']['places']] == [('north', '30.00'), ('south', '5.00')]
    assert values['place_count'] == 2 and values['longest'] == '20.50' and values['total_minutes'] == '35.00'
    assert body['errors'] == {}
    with app.database.transaction() as tx:
        assert tx.count(Trip) == 0


def test_derived_values_with_more_decimal_places_than_the_field_allows_are_field_errors(app):
    app, admin = app
    saved = create(admin)
    row = saved['children']['places'][0]['id']
    MISBEHAVE.append('precise')
    expected = {'longest': 'Use at most 2 decimal places', 'place_count': 'Expected an integer'}
    status, body, _ = admin('POST', '/api/trip', {'children': {'stops': STOPS}})
    assert status == 422 and {key: body['fields'][key] for key in expected} == expected
    assert any(key.startswith('places.') and key.endswith('.minutes') for key in body['fields'])
    # A preview reports the same errors and shows the refused values as empty, never a 500.
    status, body, _ = admin('POST', '/api/_draft/trip', {'values': {'children': {'stops': STOPS}}})
    assert status == 200, body
    assert {key: body['errors'][key] for key in expected} == expected
    assert body['values']['longest'] is None and body['values']['place_count'] is None
    # On a saved record the error on a rebuilt row is keyed by the saved row the preview shows.
    status, body, _ = admin('POST', '/api/_draft/trip', {'id': saved['id'], 'values': {}})
    assert status == 200, body
    assert body['errors'][f'places.{row}.minutes'] == 'Use at most 2 decimal places'
    assert body['values']['children']['places'][0]['id'] == row and body['values']['children']['places'][0]['minutes'] is None
    status, body, _ = patch(admin, saved['id'], {'title_text': 'changed'})
    assert status == 422 and body['fields'][f'places.{row}.minutes'] == 'Use at most 2 decimal places'
    assert admin('GET', f"/api/trip/{saved['id']}")[1]['longest'] == '20.50'


def test_a_locked_record_keeps_its_derived_values(app):
    app, admin = app
    body = create(admin)
    assert move(admin, 'trip', body['id'], 'closed')[0] == 200
    calls = len(SEEN)
    # An ordinary write on a locked record keeps its saved results and its derived values: derive() does not run.
    status, after, _ = patch(admin, body['id'], {'remarks': 'noted'})
    assert status == 200 and len(SEEN) == calls
    assert after['place_count'] == 2 and places(after) == places(body)


def test_a_validation_error_from_derive_refuses_the_save_and_writes_nothing(app):
    app, admin = app
    body = create(admin)
    MISBEHAVE.append('refuse')
    status, refused, _ = patch(admin, body['id'], {'title_text': 'changed'})
    assert status == 422 and refused['fields'] == {'amount': 'Refused by derive'}
    status, preview, _ = admin('POST', '/api/_draft/trip', {'id': body['id'], 'values': {}})
    assert status == 200 and preview['errors'] == {'amount': 'Refused by derive'}
    assert admin('GET', f"/api/trip/{body['id']}")[1]['title_text'] == 'trip'


def test_derive_sets_only_what_meta_derives_names(app):
    app, _ = app
    MISBEHAVE.append('field')
    with app.business.context(ADMIN) as context, pytest.raises(AttributeError, match='Meta.derives'):
        context.create(Trip)
    with app.database.transaction() as tx:
        assert tx.count(Trip) == 0


# Callers cannot write what derive() sets

def test_clients_and_python_callers_cannot_write_derived_fields_or_tables(app):
    app, admin = app
    status, body, _ = admin('POST', '/api/trip', {'place_count': 5})
    assert status == 422 and body['fields'] == {'place_count': 'DERIVED_FIELD_WRITE: this value is set by the record'}
    status, body, _ = admin('POST', '/api/trip', {'children': {'places': [{'place': 'x'}]}})
    assert status == 422 and set(body['fields']) == {'places'}
    saved = create(admin)
    row = saved['children']['places'][0]['id']
    status, body, _ = patch(admin, saved['id'], {'children': {'places': {'update': [{'id': row, 'stops': 9}]}}})
    assert status == 422 and set(body['fields']) == {'places'}
    with app.business.context(ADMIN) as context, pytest.raises(ValidationError) as caught:
        context.update(Trip, saved['id'], longest='1.00')
    assert caught.value.errors == {'longest': 'DERIVED_FIELD_WRITE: this value is set by the record'}


def test_the_schema_marks_derived_fields_and_tables_read_only(app):
    app, admin = app
    schema = admin('GET', '/api/schema')[1]
    trip = next(model for model in schema['models'] if model['name'] == 'trip')
    children = {child['name']: child for child in trip['children']}
    assert children['places'].get('read_only') is True and children['shares'].get('read_only') is True
    assert 'read_only' not in children['stops']
    fields = {field['name']: field for field in trip['fields']}
    assert fields['place_count']['read_only'] is True and fields['longest']['read_only'] is True
    assert fields['amount']['read_only'] is False
    # The form previews these on the server when the person pauses, so they show before the save.
    assert trip['derives'] == ['places', 'place_count', 'longest', 'shares']
    assert all('derives' not in model for model in schema['models'] if model['name'] in ('stop',))


def test_declarations_are_checked_when_the_model_is_defined():
    with pytest.raises(ValueError, match='needs a derive'):
        class NoMethod(Model):
            class Meta:
                derives = ('count',)
            count = Integer()
    with pytest.raises(ValueError, match='name the fields and Tables'):
        class NoNames(Model):
            count = Integer()
            def derive(self): pass
    with pytest.raises(ValueError, match='not a field or Table'):
        class Unknown(Model):
            class Meta:
                derives = ('missing',)
            def derive(self): pass
    with pytest.raises(ValueError, match='calculated, State, Sequence'):
        class Calculated(Model):
            class Meta:
                calculation_language = 'finite-v1'
                derives = ('total',)
            amount = Decimal(scale=2)
            total = Decimal(scale=2, compute=amount * 2)
            def derive(self): pass
    with pytest.raises(ValueError, match='takes self and to'):
        class WrongClose(Model):
            name_text = Text()
            def before_close(self): pass
    with pytest.raises(ValueError, match='a Table row cannot declare it'):
        class RowHook(Child):
            def before_close(self, to): pass


# before_close(): moves into a final state, void and delete

def leg(admin, trip_id, label='L-2'):
    status, body, _ = admin('POST', '/api/leg', {'label': label, 'trip': trip_id})
    assert status == 201, body
    return body['id']


def test_a_move_into_a_final_state_is_refused_while_records_depend_on_it(app):
    app, admin = app
    trip = create(admin)['id']
    first = leg(admin, trip)
    entries = journal_count(app)
    status, body, _ = move(admin, 'trip', trip, 'cancelled')
    assert status == 422 and body['fields'] == {'_form': 'Cancel the later documents first: L-2'}
    assert admin('GET', f'/api/trip/{trip}')[1]['status'] == 'open' and journal_count(app) == entries
    assert SEEN[-1] == {'close': 'cancelled', 'readonly': True}
    # A move into a state that is not final does not ask.
    calls = [item for item in SEEN if 'close' in item]
    assert move(admin, 'trip', trip, 'closed')[0] == 200
    assert [item for item in SEEN if 'close' in item] == calls
    # Once the dependent record is cancelled, the trip may close for good.
    assert move(admin, 'leg', first, 'cancelled')[0] == 200
    status, body, _ = move(admin, 'trip', trip, 'cancelled')
    assert status == 200 and body['status'] == 'cancelled'


def test_void_and_delete_ask_too(app):
    app, admin = app
    trip = create(admin)['id']
    leg(admin, trip)
    assert move(admin, 'trip', trip, 'closed')[0] == 200
    url = f'/api/trip/{trip}'
    status, body, _ = admin('POST', f'{url}/void', {'reason': 'mistake'}, headers={b'if-match': admin('GET', url)[2][b'etag']})
    assert status == 422 and body['fields'] == {'_form': 'Cancel the later documents first: L-2'}
    assert admin('GET', url)[1]['status'] == 'closed' and SEEN[-1]['close'] == 'void'
    other = create(admin)['id']
    leg(admin, other, 'L-3')
    status, body, _ = admin('DELETE', f'/api/trip/{other}', None, headers={b'if-match': admin('GET', f'/api/trip/{other}')[2][b'etag']})
    assert status == 422 and body['fields'] == {'_form': 'Cancel the later documents first: L-3'}
    assert SEEN[-1]['close'] is None
    with app.database.transaction() as tx:
        assert tx.count(Trip) == 2


def test_python_and_tool_callers_are_refused_the_same_way(app):
    app, admin = app
    trip = create(admin)['id']
    leg(admin, trip)
    with app.business.context(ADMIN) as context:
        with pytest.raises(ValidationError, match='later documents first'):
            context.move(Trip, trip, 'cancelled')
        with pytest.raises(ValidationError, match='later documents first'):
            context.delete(Trip, trip)
        # The context carries on: nothing was written by the refused changes.
        assert context.get(Trip, trip).status == 'open'
    assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'mcp.allow_writes', 'value': True}]})[0] == 200
    tools = app.business.tools
    mcp = User(1, 'admin', 'admin', access=KeyAccess(True, None, None, 7))
    with pytest.raises(ConnectorError) as caught:
        try:
            tools.execute(mcp, 'delete_record', {'model': 'trip', 'id': trip})
        except ApprovalNeeded as exc:
            tools.decide(ADMIN, exc.approval['id'], True)
            tools.execute(mcp, 'delete_record', {'model': 'trip', 'id': trip})
    assert caught.value.status == 422 and caught.value.fields == {'_form': 'Cancel the later documents first: L-2'}
    with app.database.transaction() as tx:
        assert tx.get(Trip, trip).status == 'open'


def test_before_close_cannot_write(app):
    app, admin = app
    trip = create(admin)['id']
    MISBEHAVE.append('write')
    with app.business.context(ADMIN) as context, pytest.raises(AccessError) as caught:
        context.move(Trip, trip, 'cancelled')
    assert caught.value.status == 403
    with app.database.transaction() as tx:
        assert tx.count(Plan) == 0 and tx.get(Trip, trip).status == 'open'


# Derived fields on the rows of a Table callers edit ("table.field" in Meta.derives)

LINES = [{'_key': 'a', 'qty': '3', 'price': '10.00', 'discount': '0.1', 'unit': 'a'},
         {'_key': 'b', 'qty': '1', 'price': '2.50', 'unit': 'c'}]


def sheet(app, admin, **values):
    with app.database.transaction() as tx:
        tx.create(Factor, origin='a', target='b', factor=D('1.5'))
    status, body, _ = admin('POST', '/api/sheet', {'children': {'lines': LINES}, **values})
    assert status == 201, body
    return body


def nets(body):
    return [(row['net'], row['converted']) for row in body['children']['lines']]


def test_derived_row_fields_are_set_cleaned_calculated_and_returned(app):
    app, admin = app
    body = sheet(app, admin, decimals=1)
    # net = qty x price x (1 - discount), rounded to the record's decimals; converted through the factor table.
    assert nets(body) == [('27.0000', '40.5000'), ('2.5000', None)]
    assert body['total'] == '29.5000'
    # Editing a line recalculates its derived values; the rows stay the caller's.
    line = body['children']['lines'][1]['id']
    url = f"/api/sheet/{body['id']}"
    status, after, _ = admin('PATCH', url, {'decimals': 2, 'children': {'lines': {'update': [{'id': line, 'price': '2.55', 'unit': 'a'}]}}},
                             headers={b'if-match': admin('GET', url)[2][b'etag']})
    assert status == 200, after
    assert nets(after) == [('27.0000', '40.5000'), ('2.5500', '3.8250')] and after['total'] == '29.5500'
    assert [row['id'] for row in after['children']['lines']] == [row['id'] for row in body['children']['lines']]
    with app.database.transaction() as tx:
        stored = tx.connection.execute('SELECT net FROM sheet_lines ORDER BY "evlet_position"').fetchall()
        # Stored exactly, as scaled integers (scale 4).
        assert [row[0] for row in stored] == [270000, 25500]


def test_a_preview_sets_derived_row_fields(app):
    app, admin = app
    with app.database.transaction() as tx:
        tx.create(Factor, origin='a', target='b', factor=D('2'))
    status, body, _ = admin('POST', '/api/_draft/sheet', {'values': {'children': {'lines': LINES}}})
    assert status == 200 and body['errors'] == {}, body
    lines = body['values']['children']['lines']
    assert [(row['net'], row['converted']) for row in lines] == [('27.0000', '54.0000'), ('2.5000', None)]
    assert body['values']['total'] == '29.5000'


def test_callers_cannot_write_derived_row_fields(app):
    app, admin = app
    status, body, _ = admin('POST', '/api/sheet', {'children': {'lines': [{'_key': 'a', 'qty': '1', 'net': '5'}]}})
    assert status == 422 and body['fields'] == {'lines.net': 'DERIVED_FIELD_WRITE: this value is set by the record'}
    saved = sheet(app, admin)
    line = saved['children']['lines'][0]['id']
    url = f"/api/sheet/{saved['id']}"
    status, body, _ = admin('PATCH', url, {'children': {'lines': {'update': [{'id': line, 'converted': '1'}]}}},
                            headers={b'if-match': admin('GET', url)[2][b'etag']})
    assert status == 422 and body['fields'] == {'lines.converted': 'DERIVED_FIELD_WRITE: this value is set by the record'}
    with app.business.context(ADMIN) as context, pytest.raises(ValidationError) as caught:
        context.update(Sheet, saved['id'], children={'lines': {'create': [{'_key': 'n', 'net': '1'}]}})
    assert caught.value.errors == {'lines.net': 'DERIVED_FIELD_WRITE: this value is set by the record'}
    # The schema marks them read only on the row; the Table itself stays editable.
    schema = admin('GET', '/api/schema')[1]
    entry = next(model for model in schema['models'] if model['name'] == 'sheet')
    child = entry['children'][0]
    assert 'read_only' not in child
    row = next(model for model in schema['models'] if model['name'] == child['model'])
    flags = {field['name']: field['read_only'] for field in row['fields'] if field['name'] in ('qty', 'net', 'converted')}
    assert flags == {'qty': False, 'net': True, 'converted': True}


@pytest.mark.parametrize('misbehave', ['rows', 'append'])
def test_derive_keeps_the_rows_of_a_table_callers_edit(app, misbehave):
    app, admin = app
    MISBEHAVE.append(misbehave)
    with app.business.context(ADMIN) as context, pytest.raises(AttributeError):
        context.create(Sheet, children={'lines': [{'qty': '1'}]})
    with app.database.transaction() as tx:
        assert tx.count(Sheet) == 0


def test_row_field_declarations_are_checked():
    with pytest.raises(ValueError, match='not a Table'):
        class NoTable(Model):
            class Meta:
                derives = ('lines.net',)
            def derive(self): pass
    with pytest.raises(ValueError, match='not a field of'):
        class NoField(Model):
            class Meta:
                derives = ('lines.missing',)
            class Line(Child):
                qty = Integer()
            lines = Table(Line)
            def derive(self): pass
    with pytest.raises(ValueError, match='sets its rows whole'):
        class Both(Model):
            class Meta:
                derives = ('lines', 'lines.qty')
            class Line(Child):
                qty = Integer()
            lines = Table(Line)
            def derive(self): pass
