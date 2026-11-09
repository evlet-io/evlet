"""derive() on trusted Session writes: real SQLite.

App code (actions, move runs, jobs, scripts) writes through the transaction session. For a model that declares
derive(), a trusted create, update or row write settles the saved document like any other write: derive, clean,
calculate, validate, store. A failure rolls the write back even when trusted code catches it. Models without
derive() keep the raw session write, and maintenance() stays a raw repair."""

from decimal import Decimal as D

import pytest

from evlet import Application, Child, Database, Decimal, Integer, Model, Registry, Rule, Table, Text, ValidationError, action
from evlet.auth import LocalAuth, User
from evlet.calc import F
from test_business import PASSWORD, http

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}
ADMIN = User(1, 'admin', 'admin')
# The model each derive() call named through self.model, for the document and for its rows.
SEEN = []


class Order(Model):
    """People and app code edit the lines; derive() sets each line's net, a summary per unit and the header count."""
    permissions = FULL

    class Meta:
        derives = ('line_count', 'total', 'units', 'lines.net')

    class Line(Child):
        unit = Text(max_length=10, default='box')
        qty = Decimal(scale=2, default=1)
        price = Decimal(scale=2, default=0)
        net = Decimal(scale=2)

    class Unit(Child):
        unit = Text(max_length=10)
        qty = Decimal(scale=2)

    label = Text(max_length=40, default='order')
    discount = Decimal(scale=2, default=0, minimum=0, maximum=1)
    lines = Table(Line)
    units = Table(Unit)
    line_count = Integer()
    # Set by derive() from the derived row field. A document with calculations is a finite-v1 document, which the
    # session refuses to write: app code writes it through Context.create/update.
    total = Decimal(scale=2)

    def derive(self):
        SEEN.append((self.model, [line.model for line in self.lines]))
        totals = {}
        for line in self.lines:
            # A string on purpose: what derive() sets is cleaned like caller input.
            line.net = str(((line.qty or 0) * (line.price or 0) * (1 - (self.discount or 0))).quantize(D('0.01')))
            totals[line.unit] = totals.get(line.unit, 0) + (line.qty or 0)
        self.units = [{'unit': unit, 'qty': qty} for unit, qty in sorted(totals.items())]
        self.line_count = len(self.lines)
        self.total = sum((D(line.net) for line in self.lines), D(0))

    def validate(self):
        if self.line_count is not None and self.line_count > 3:
            raise ValidationError({'lines': 'Use at most three lines'})


class Batch(Model):
    """A document that needs at least one line: trusted code creates it with its rows in one call."""
    permissions = FULL

    class Meta:
        derives = ('line_count', 'lines.net')

    class Line(Child):
        qty = Decimal(scale=2, default=1)
        price = Decimal(scale=2, default=0)
        net = Decimal(scale=2)

    label = Text(max_length=40, default='batch')
    lines = Table(Line, min_rows=1)
    line_count = Integer()

    def derive(self):
        SEEN.append((self.model, [line.model for line in self.lines]))
        for line in self.lines:
            line.net = (line.qty or 0) * (line.price or 0)
        self.line_count = len(self.lines)


class Note(Model):
    """A declarative model without derive(): trusted session writes stay raw writes."""
    permissions = FULL
    text = Text(max_length=40, default='')
    copies = Integer(default=1)


class Desk(Model):
    """App code that writes orders through its action's transaction session."""
    permissions = FULL
    label = Text(max_length=40, default='desk')

    @action('Open order')
    def open_order(self):
        session = self.context.session
        order = session.create(Order, label='from desk', discount='0.5')
        Line = line_model(session)
        session.create(Line, evlet_owner=order.id, qty='2', price='3')
        return {'order': str(order.id)}


def line_model(session, name='lines', parent=Order):
    return next(child.model for child in session.registry.children if child.parent is parent and child.name == name)


def rows(session, order_id, name='lines', parent=Order):
    model = line_model(session, name, parent)
    return session.list(model, filters={'evlet_owner': order_id}, order_by=('evlet_position', 'id'))


@pytest.fixture
def app(tmp_path):
    SEEN.clear()
    database = Database(tmp_path / 'derive-session.sqlite3', Registry(Order, Batch, Note, Desk))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


def test_trusted_create_stores_derived_header_values(app):
    with app.database.transaction() as tx:
        order = tx.create(Order, label='empty')
        # The returned record is the settled one.
        assert order.line_count == 0 and order.total == D('0')
    with app.database.transaction() as tx:
        assert tx.get(Order, order.id).line_count == 0
    assert SEEN[0] == (Order, [])


def test_documents_with_calculations_stay_context_only(app):
    class Priced(Model):
        permissions = FULL

        class Meta:
            derives = ('base',)
        price = Decimal(scale=2, default=0)
        base = Decimal(scale=2)
        doubled = Decimal(scale=2, compute=F('base') * 2)

        def derive(self):
            self.base = self.price

    database = Database(app.database.path.parent / 'priced.sqlite3', Registry(Priced))
    priced = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    priced.initialize()
    with database.transaction() as tx, pytest.raises(ValueError, match='finite-v1 document'):
        tx.create(Priced, price='2')


def test_trusted_row_writes_and_header_update_settle_the_document(app):
    with app.database.transaction() as tx:
        order = tx.create(Order)
        Line = line_model(tx)
        first = tx.create(Line, evlet_owner=order.id, qty='2', price='10', evlet_position=1)
        tx.create(Line, evlet_owner=order.id, unit='bag', qty='1', price='4', evlet_position=2)
        # Row create: the row's derived field, the derived Table and the header follow.
        assert [line.net for line in rows(tx, order.id)] == [D('20.00'), D('4.00')]
        assert [(unit.unit, unit.qty) for unit in rows(tx, order.id, 'units')] == [('bag', D('1')), ('box', D('2'))]
        saved = tx.get(Order, order.id)
        assert saved.line_count == 2 and saved.total == D('24.00')
        # Row update.
        tx.update(Line, first.id, qty='3')
        assert [line.net for line in rows(tx, order.id)] == [D('30.00'), D('4.00')]
        assert tx.get(Order, order.id).total == D('34.00')
        # Header update: the 'table.field' rows are derived again from the new header value.
        updated = tx.update(Order, order.id, discount='0.5')
        assert updated.total == D('17.00')
        assert [line.net for line in rows(tx, order.id)] == [D('15.00'), D('2.00')]
        # Row delete.
        tx.delete(Line, first.id)
        assert tx.get(Order, order.id).line_count == 1
        assert [(unit.unit, unit.qty) for unit in rows(tx, order.id, 'units')] == [('bag', D('1'))]
    # derive() named its model, and its rows' model, through self.model.
    assert SEEN[-1] == (Order, [Line])


def test_derived_rows_keep_their_ids_across_trusted_writes(app):
    with app.database.transaction() as tx:
        order = tx.create(Order)
        Line = line_model(tx)
        line = tx.create(Line, evlet_owner=order.id, qty='1', price='1')
        before = [unit.id for unit in rows(tx, order.id, 'units')]
        tx.update(Line, line.id, price='2')
        assert [unit.id for unit in rows(tx, order.id, 'units')] == before


def test_a_failed_settlement_rolls_back_the_write_even_when_caught(app):
    with app.database.transaction() as tx:
        order = tx.create(Order)
        Line = line_model(tx)
        for _ in range(3):
            tx.create(Line, evlet_owner=order.id, qty='1', price='1')
        with pytest.raises(ValidationError) as caught:
            tx.create(Line, evlet_owner=order.id, qty='1', price='1')
        assert caught.value.errors == {'lines': 'Use at most three lines'}
        with pytest.raises(ValidationError):
            tx.update(Order, order.id, discount='2')
        # Trusted code caught both failures; the transaction commits without them.
    with app.database.transaction() as tx:
        assert len(rows(tx, order.id)) == 3
        saved = tx.get(Order, order.id)
        assert saved.discount == D('0') and saved.line_count == 3 and saved.total == D('3.00')


def test_an_action_writing_through_its_session_stores_derived_values(app):
    with app.business.context(ADMIN) as context:
        desk = context.create(Desk)
        result = context.action(Desk, desk['id'], 'open_order')
    with app.database.transaction() as tx:
        order = tx.get(Order, result['_outcome']['order'])
        assert order.label == 'from desk' and order.line_count == 1 and order.total == D('3.00')
        assert [line.net for line in rows(tx, order.id)] == [D('3.00')]


def test_models_without_derive_keep_the_raw_session_write(app, tmp_path):
    with app.database.transaction() as tx:
        note = tx.update(Note, tx.create(Note, text='a').id, copies=3)
        assert note.text == 'a' and note.copies == 3
    # A bare Database has no application: plain models still write, and a derive() model is refused.
    bare = Database(tmp_path / 'bare.sqlite3', Registry(Order, Note, Desk))
    bare.initialize()
    with bare.transaction() as tx:
        assert tx.create(Note, text='b').text == 'b'
        with pytest.raises(ValueError, match='declares derive'):
            tx.create(Order)
        # maintenance() is a raw repair: derive() does not run.
        with tx.maintenance():
            assert tx.create(Order).line_count is None
    assert SEEN == []


def test_the_draft_names_its_model_and_a_field_named_model_wins():
    class Car(Model):
        permissions = FULL

        class Meta:
            derives = ('code',)
        model = Text(max_length=20, default='')
        code = Text(max_length=20)

        def derive(self):
            self.code = self.model.upper()

    from evlet.documents import Draft
    assert Draft(Order, {}).model is Order
    assert Draft(Car, {'model': 'tiny'}).model == 'tiny'


def test_ledger_models_cannot_declare_derive():
    with pytest.raises(ValueError, match='Meta.derives is not available'):
        class Entry(Model):
            class Meta:
                ledger = True
                derives = ('double',)
            amount = Decimal(scale=2)
            double = Decimal(scale=2)

            def derive(self):
                self.double = self.amount * 2


def test_a_trusted_create_takes_its_rows_and_settles_once(app):
    with app.database.transaction() as tx:
        batch = tx.create(Batch, label='two', children={'lines': [{'qty': '2', 'price': '3'}, {'qty': '1', 'price': '5'}]})
        assert batch.line_count == 2
        assert [line.net for line in rows(tx, batch.id, parent=Batch)] == [D('6.00'), D('5.00')]
    # One derive() for the document and both rows, not one per row.
    assert len(SEEN) == 1
    # Without its rows the document is refused, as on any other write.
    with app.database.transaction() as tx, pytest.raises(ValidationError) as caught:
        tx.create(Batch, label='none')
    assert 'lines' in caught.value.errors
    with app.database.transaction() as tx:
        assert tx.count(Batch) == 1


def test_a_trusted_update_replaces_or_changes_rows_in_one_call(app):
    with app.database.transaction() as tx:
        batch = tx.create(Batch, children={'lines': [{'qty': '1', 'price': '1'}, {'qty': '1', 'price': '2'}]})
        first, second = rows(tx, batch.id, parent=Batch)
        # A full list: rows with an id are kept and changed, the others are removed, new ones added.
        tx.update(Batch, batch.id, label='changed', children={'lines': [{'id': str(second.id), 'qty': '3'}, {'qty': '2', 'price': '4'}]})
        saved = rows(tx, batch.id, parent=Batch)
        assert [(line.id == second.id, line.net) for line in saved] == [(True, D('6.00')), (False, D('8.00'))]
        assert tx.get(Batch, batch.id).label == 'changed' and tx.get(Batch, batch.id).line_count == 2
        # A change set.
        tx.update(Batch, batch.id, children={'lines': {'delete': [str(saved[1].id)]}})
        assert tx.get(Batch, batch.id).line_count == 1


def test_rows_sent_with_a_trusted_write_are_checked_and_rolled_back(app):
    with app.database.transaction() as tx:
        batch = tx.create(Batch, children={'lines': [{'qty': '1', 'price': '1'}]})
        # Removing every row breaks min_rows: the header change and the rows are undone, though the error is caught.
        with pytest.raises(ValidationError):
            tx.update(Batch, batch.id, label='gone', children={'lines': []})
        # A derived row field is the record's to set.
        with pytest.raises(ValidationError) as caught:
            tx.create(Batch, children={'lines': [{'qty': '1', 'net': '9'}]})
        assert caught.value.errors == {'lines.net': 'DERIVED_FIELD_WRITE: this value is set by the record'}
    with app.database.transaction() as tx:
        assert tx.count(Batch) == 1
        assert tx.get(Batch, batch.id).label == 'batch' and len(rows(tx, batch.id, parent=Batch)) == 1
        # children= is only for a document the session settles.
        with pytest.raises(ValueError, match='children= is for a document that declares derive'):
            tx.create(Note, children={'lines': []})
