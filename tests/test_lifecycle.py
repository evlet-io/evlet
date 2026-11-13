"""Lifecycle tests use real SQLite transactions, HTTP authorization and persisted child rows."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from evlet import (Application, Child, Currency, Database, Integer, LocalAuth, Model, Registry,
                   Rule, Table, Text, User, ValidationError, action, validate)
from evlet.auth import AccessError
from test_business import client, http, PASSWORD


class Posting(Model):
    permissions = {"admin": Rule(("read", "create", "update", "delete")),
                   "editor": Rule(("read", "create", "update"))}
    execution = Text(required=True, unique=True)
    revision_number = Integer()
    amount = Currency()


class Invoice(Model):
    confirmable = True
    track_changes = True
    lifecycle_labels = {'confirm': 'Post', 'confirmed': 'Posted', 'cancel': 'Reverse'}
    number = Text(required=True, unique=True)
    customer = Text(required=True)
    amount = Currency(default='10')
    remarks = Text(editable_when_locked=True)
    secret = Text(tracking='mask')
    credential = Text(tracking='exclude')
    gate = Text(default='ok')
    permissions = {
        'admin': Rule(('read', 'create', 'update', 'delete')),
        'editor': Rule(('read', 'create', 'update', 'confirm')),
        'viewer': Rule(('read',), read_fields=('number', 'customer', 'amount')),
    }

    class Line(Child):
        title = Text(required=True)
        qty = Integer(default=1)
        secret = Text(tracking='exclude')
    lines = Table(Line)

    def check_lifecycle(self, operation):
        if operation == 'reopen' and self.gate == 'legal':
            raise ValidationError({'_form': 'Use a credit note'})
        if operation == 'confirm' and self.gate == 'approval':
            entries = self.context.history(Invoice, self.id)['items']
            if not any(item['kind'] == 'approved' for item in entries):
                raise ValidationError({'_form': 'Approval is required'})

    @validate.confirm
    def positive(self):
        if self.amount <= 0:
            raise ValidationError({'amount': 'Must be positive'})

    @validate.cancel
    def cancellable(self):
        if self.gate == 'block_cancel':
            raise ValidationError({'_form': 'Dependent document exists'})

    @action.confirm(priority=-10)
    def change_before_posting(self):
        if self.gate == 'mutate':
            self.remarks = 'posted'

    @action.confirm
    def post_ledger(self):
        execution = self.context.lifecycle_execution
        record = self.context.create(Posting, execution=execution.id, revision_number=execution.revision, amount=self.amount)
        self.context.reference_effect(Posting, record['id'])
        if self.gate == 'fail_confirm':
            raise ValueError('posting failed')

    @action.cancel
    def reverse_ledger(self):
        execution = self.context.lifecycle_execution
        record = self.context.create(Posting, execution=execution.id, revision_number=execution.revision, amount=-self.amount)
        self.context.reference_effect(Posting, record['id'])
        if self.gate == 'fail_cancel':
            raise ValueError('reversal failed')


@pytest.fixture
def app(tmp_path):
    registry = Registry(Invoice, Posting)
    database = Database(tmp_path / 'lifecycle.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


ADMIN = User(1, 'admin', 'admin')


def create(context, **values):
    return context.create(Invoice, **{'number': 'INV-001', 'customer': 'A', 'secret': 'never-store', 'credential': 'never-store', **values})['id']


def test_identity_revisions_snapshots_and_history(app):
    with app.business.context(ADMIN, source='Integration', correlation_id='request-1') as context:
        document = context.document(Invoice, number='INV-001', customer='A')
        document.save()
        original = document.id
        assert document.revision == 0
        document.confirm()
        assert document.lifecycle_state == 'confirmed'
        first = document.snapshot(revision=1)
        document.cancel(reason='Incorrect customer').reopen()
        assert document.revision == 1
        document.customer = 'B'
        document.save().confirm()
        assert document.id == original and document.number == 'INV-001' and document.revision == 2
        assert document.snapshot(revision=1)['document']['customer'] == 'A'
        assert document.diff(1, 2)['changes']['fields']['customer'] == {'old': 'A', 'new': 'B'}
        assert document.revisions()['total'] == 2
        events = document.history()['items']
        assert [event['kind'] for event in events] == ['reconfirmed', 'changed', 'reopened', 'cancelled', 'confirmed', 'created']
        assert all(event['data']['source'] == 'Integration' and event['data']['correlation_id'] == 'request-1' for event in events)
        assert first['execution_id'] != document.snapshot(revision=2)['execution_id']
        cancelled = next(event for event in events if event['kind'] == 'cancelled')
        assert cancelled['data']['revision'] == 1 and len(cancelled['data']['effects']) == 2
    assert app.database.count(Posting) == 3


@pytest.mark.parametrize('gate,operation', [('fail_confirm', 'confirm'), ('fail_cancel', 'cancel')])
def test_handler_failure_rollback_even_when_caught(app, gate, operation):
    with app.business.context(ADMIN) as context:
        record_id = create(context, gate=gate)
        if operation == 'cancel': context.confirm(Invoice, record_id)
        prior = context.history(Invoice, record_id)['total']
        with pytest.raises(ValueError, match='failed'):
            getattr(context, operation)(Invoice, record_id, **({'reason': 'wrong'} if operation == 'cancel' else {}))
        assert context.revisions(Invoice, record_id)['total'] == (operation == 'cancel')
        assert context.history(Invoice, record_id)['total'] == prior
        assert context.get(Invoice, record_id)._lifecycle.state == ('confirmed' if operation == 'cancel' else 'draft')
    assert app.database.count(Posting) == (operation == 'cancel')


@pytest.mark.parametrize('operation', ['confirm', 'cancel', 'reopen'])
def test_invalid_transitions(app, operation):
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        if operation == 'confirm': context.confirm(Invoice, record_id)
        with pytest.raises(AccessError) as error:
            getattr(context, operation)(Invoice, record_id, **({'reason': 'wrong'} if operation == 'cancel' else {}))
        assert error.value.status == 409


def test_locking_delete_and_raw_write_guards(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        context.confirm(Invoice, record_id)
        with pytest.raises(AccessError): context.update(Invoice, record_id, amount='99')
        with pytest.raises(AccessError): context.update(Invoice, record_id, children={'lines': [{'title': 'x'}]})
        with pytest.raises(AccessError): context.delete(Invoice, record_id)
        context.update(Invoice, record_id, remarks='Allowed')
        assert context.snapshot(Invoice, record_id, revision=1)['document']['remarks'] is None
        context.cancel(Invoice, record_id, reason='wrong')
        with pytest.raises(AccessError): context.delete(Invoice, record_id)
        context.reopen(Invoice, record_id)
        with pytest.raises(AccessError): context.delete(Invoice, record_id)
    with pytest.raises(ValueError, match='Context'): app.database.update(Invoice, record_id, amount='99')
    with pytest.raises(ValueError, match='Context'): app.database.delete(Invoice, record_id)
    with pytest.raises(ValueError, match='Context'): app.database.create(Invoice, number='raw', customer='X')


def test_never_confirmed_draft_can_be_deleted(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        context.delete(Invoice, record_id)
    assert app.database.count(Invoice) == 0
    with app.database.transaction() as tx:
        assert tx.connection.execute("SELECT count(*) FROM evlet_journal WHERE kind='deleted'").fetchone()[0] == 1


def test_application_guards_and_approval_separation(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context, gate='approval')
        with pytest.raises(ValidationError, match='Approval'): context.confirm(Invoice, record_id)
        context.record_approval(Invoice, record_id, approved=True, reason='Checked')
        assert context.get(Invoice, record_id)._lifecycle.state == 'draft'
        context.confirm(Invoice, record_id)
        assert context.snapshot(Invoice, record_id, revision=1)['approval']['approved'] is True
        context.cancel(Invoice, record_id, reason='Correction')
        context.reopen(Invoice, record_id)
        context.update(Invoice, record_id, gate='legal')
        context.confirm(Invoice, record_id)
        context.cancel(Invoice, record_id, reason='Correction')
        with pytest.raises(ValidationError, match='credit note'): context.reopen(Invoice, record_id)


def test_snapshot_and_history_sensitive_values_excluded_at_rest(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context, children={'lines': [{'title': 'A', 'secret': 'child-secret'}]})
        context.confirm(Invoice, record_id)
        snap = context.snapshot(Invoice, record_id, revision=1)
        assert snap['document']['secret'] == '[masked]'
        assert 'credential' not in snap['document']
        for table in ('evlet_journal', 'evlet_document_revisions'):
            rows = context._tx.connection.execute(f'SELECT data FROM {table}').fetchall()
            assert all('never-store' not in row[0] and 'child-secret' not in row[0] for row in rows)
        for sql in ("UPDATE evlet_document_revisions SET data='{}'", 'DELETE FROM evlet_document_revisions',
                    'INSERT OR REPLACE INTO evlet_document_revisions SELECT * FROM evlet_document_revisions'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'): context._tx.connection.execute(sql)
        with pytest.raises(sqlite3.IntegrityError, match='append only'):
            context._tx.connection.execute("UPDATE evlet_journal SET data='{}'")


def test_stable_child_ids_and_diff(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context, children={'lines': [{'title': 'A'}, {'title': 'B'}, {'title': 'C'}]})
        context.confirm(Invoice, record_id)
        first = context.snapshot(Invoice, record_id, revision=1)['document']['children']['lines']
        context.cancel(Invoice, record_id, reason='Rows').get('id')
        context.reopen(Invoice, record_id)
        context.update(Invoice, record_id, children={'lines': [{'id': first[1]['id'], 'title': 'B changed'}, {'id': first[0]['id'], 'title': 'A'}, {'title': 'D'}]})
        context.confirm(Invoice, record_id)
        changes = context.diff(Invoice, record_id, 1, 2)['changes']['rows']['lines']
        assert changes['updated'][0]['id'] == first[1]['id']
        assert changes['removed'][0]['id'] == first[2]['id']
        assert changes['added'][0]['title'] == 'D'
        assert len(changes['positions']) == 2
        update = next(event for event in context.history(Invoice, record_id)['items'] if event['kind'] == 'changed')
        assert update['data']['rows']['lines']['reordered'] is True


def test_concurrent_confirmation_executes_once(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context)
    def confirm():
        try:
            with app.business.context(ADMIN) as context: context.confirm(Invoice, record_id)
            return 200
        except AccessError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: confirm(), range(2))) == [200, 409]
    assert app.database.count(Posting) == 1


def test_http_permissions_etags_and_read_only_history(app):
    c = client(app)
    status, doc, headers = c('POST', '/api/invoice', {'number': 'INV-001', 'customer': 'A'})
    assert status == 201, doc
    path = f"/api/invoice/{doc['id']}"
    assert c('POST', path + '/confirm', {})[0] == 428
    status, confirmed, posted = c('POST', path + '/confirm', {}, headers={b'if-match': headers[b'etag']})
    assert status == 200, confirmed
    assert confirmed['_lifecycle']['revision'] == 1
    assert c('POST', path + '/cancel', {'reason': 'Wrong'}, headers={b'if-match': headers[b'etag']})[0] == 409
    assert c('GET', path + '/snapshot?revision=1')[1]['document']['customer'] == 'A'
    assert c('GET', path + '/revisions')[1]['total'] == 1
    assert c('GET', path + '/diff?from=1&to=1')[1]['changes'] == {}
    assert c('GET', path + '/history')[1]['total'] == 2
    assert c('POST', path + '/snapshot', {})[0] == 405
    assert c('GET', path + '/snapshot?revision=0')[0] == 422
    assert c('GET', path + '/snapshot?revision=1&revision=2')[0] == 422
    assert c('GET', path + '/snapshot?revision=99999999999999999999999999')[0] == 422
    app.business.auth.create_user(username='viewer', password=PASSWORD, role='viewer')
    viewer = client(app, 'viewer')
    assert viewer('POST', path + '/cancel', {'reason': 'Wrong'}, headers={b'if-match': posted[b'etag']})[0] == 403
    shown = viewer('GET', path + '/snapshot?revision=1')[1]
    assert set(shown['document']) == {'id', 'customer', 'number', 'amount', 'children'}
    assert shown['effects'] == [] or 'model' in shown['effects'][0]
    assert 'secret' not in json.dumps(viewer('GET', path + '/history')[1])


def test_editor_explicit_operation_permissions(app):
    with app.business.context(ADMIN) as context: record_id = create(context)
    with app.business.context(User(2, 'editor', 'editor')) as context:
        context.confirm(Invoice, record_id)
        with pytest.raises(AccessError) as error: context.cancel(Invoice, record_id, reason='No')
        assert error.value.status == 403


def test_non_confirmable_tracking_model(tmp_path):
    class Customer(Model):
        track_changes = True
        title = Text()
    database = Database(tmp_path / 'plain.sqlite3', Registry(Customer))
    app = Application(database, auth=LocalAuth(database, origins=("http://127.0.0.1:8000",)))
    app.initialize()
    with app.business.context(User(0, 'system', 'admin')) as context:
        record = context.document(Customer, title='A').save()
        record.title = 'B'
        record.save()
        assert record.history()['total'] == 2
        with pytest.raises(AccessError): record.confirm()
        record.delete()


def test_handler_priority_inheritance_and_standard_validation(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context, gate='mutate')
        context.confirm(Invoice, record_id)
        assert context.snapshot(Invoice, record_id, revision=1)['document']['remarks'] == 'posted'
        second = create(context, number='INV-002', amount='0')
        with pytest.raises(ValidationError): context.confirm(Invoice, second)
        assert context.revisions(Invoice, second)['total'] == 0


def test_shared_browser_contract():
    from scripts.generate_lifecycle_fixtures import DESTINATION, normalize, record
    assert normalize(record()) == json.loads(DESTINATION.read_text())


def test_nested_rows_are_preserved_and_compared(tmp_path):
    class Notes(Child):
        text = Text()
    class Lines(Child):
        title = Text()
        notes = Table(Notes)
    class Nested(Model):
        permissions = {"admin": Rule(("read", "create", "update", "delete"))}
        confirmable = True
        title = Text()
        lines = Table(Lines)
        class Meta:
            calculation_language = 'finite-v1'
    database = Database(tmp_path / 'nested.sqlite3', Registry(Nested))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    with app.business.context(ADMIN) as context:
        record = context.create(Nested, title='Nested', children={'lines': [{'title': 'Line', 'children': {'notes': [{'text': 'A'}]}}]})
        context.confirm(Nested, record['id'])
        first = context.snapshot(Nested, record['id'], revision=1)['document']['children']['lines'][0]
        context.cancel(Nested, record['id'], reason='Notes')
        context.reopen(Nested, record['id'])
        context.update(Nested, record['id'], children={'lines': [{'id': first['id'], 'children': {'notes': [{'id': first['children']['notes'][0]['id'], 'text': 'B'}]}}]})
        context.confirm(Nested, record['id'])
        delta = context.diff(Nested, record['id'], 1, 2)['changes']['rows']['lines']['updated'][0]['rows']['notes']['updated'][0]
        assert delta['fields']['text'] == {'old': 'A', 'new': 'B'}


def test_outbox_is_atomic_and_uses_execution_key(tmp_path):
    from evlet import JobQueue, Task
    class Queued(Model):
        confirmable = True
        fail = Integer(default=0)
        @action.confirm
        def send(self):
            execution = self.context.lifecycle_execution
            self.context.enqueue('send', {'revision': execution.revision}, key=execution.id)
            if self.fail: raise ValueError('rollback queued effect')
    database = Database(tmp_path / 'queue.sqlite3', Registry(Queued))
    jobs = JobQueue(database, [Task('send', lambda tx, job, payload: None)])
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)), jobs=jobs)
    app.initialize()
    with app.business.context(User(0, 'engine', 'admin')) as context:
        record_id = context.create(Queued, fail=1)['id']
        with pytest.raises(ValueError): context.confirm(Queued, record_id)
        assert context._tx.connection.execute('SELECT count(*) FROM evlet_jobs').fetchone()[0] == 0
        context.update(Queued, record_id, fail=0)
        context.confirm(Queued, record_id)
        assert context._tx.connection.execute('SELECT count(*) FROM evlet_jobs').fetchone()[0] == 1
    assert jobs.run_one() and not jobs.run_one()


def test_validators_are_readonly_and_handlers_cannot_assign_engine_state(tmp_path):
    class Protected(Model):
        confirmable = True
        value = Text()
        @validate.confirm
        def invalid(self):
            self.value = 'validator mutation'
    database = Database(tmp_path / 'protected.sqlite3', Registry(Protected))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    with app.business.context(ADMIN) as context:
        record_id = context.create(Protected, value='A')['id']
        with pytest.raises(AttributeError, match='read only'): context.confirm(Protected, record_id)
        assert context.get(Protected, record_id).value == 'A'
        assert context.revisions(Protected, record_id)['total'] == 0


def test_decorator_order_and_overrides():
    calls = []
    class Base(Model):
        confirmable = True
        @action.confirm(priority=5)
        def zebra(self): calls.append('base')
        @action.confirm(priority=-1)
        def early(self): calls.append('early')
        @validate.cancel
        def cancel_rule(self): pass
    class Derived(Base):
        @action.confirm(priority=5)
        def zebra(self): calls.append('derived')
        @action.confirm(priority=5)
        def alpha(self): calls.append('alpha')
    for kind, operation, priority, name in Derived.__lifecycle_handlers__:
        if kind == 'action' and operation == 'confirm': getattr(Derived, name)(None)
    assert calls == ['early', 'alpha', 'derived']


def test_child_snapshot_obeys_current_row_scope_and_masks(app):
    from evlet import Policy
    with app.business.context(ADMIN) as context:
        record_id = create(context, children={'lines': [{'title': 'Visible'}, {'title': 'Hidden'}]})
        context.confirm(Invoice, record_id)
    child = app.registry.children[0].model
    app.business.policy = Policy(app.registry, rules={('editor', child): Rule(('read',), scope=lambda user: {'title': 'Visible'})})
    with app.business.context(User(2, 'editor', 'editor'), readonly=True) as context:
        shown = context.snapshot(Invoice, record_id, revision=1)
        assert [row['title'] for row in shown['document']['children']['lines']] == ['Visible']
        assert 'Hidden' not in json.dumps(context.history(Invoice, record_id))


def test_document_number_is_permanent_after_confirmation(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        context.confirm(Invoice, record_id)
        context.cancel(Invoice, record_id, reason='Change')
        context.reopen(Invoice, record_id)
        with pytest.raises(AccessError, match='permanent'): context.update(Invoice, record_id, number='INV-002')
        assert context.get(Invoice, record_id).number == 'INV-001'


def test_application_value_error_validator_is_a_validation_failure(tmp_path):
    class Checked(Model):
        confirmable = True
        title = Text()
        @validate.confirm
        def check(self): raise ValueError('Invalid document')
    database = Database(tmp_path / 'checked.sqlite3', Registry(Checked))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    with app.business.context(ADMIN) as context:
        record_id = context.create(Checked, title='A')['id']
        with pytest.raises(ValidationError, match='Invalid document'): context.confirm(Checked, record_id)
        assert context.revisions(Checked, record_id)['total'] == 0


def test_history_effect_references_obey_target_permissions(app):
    from evlet import Policy
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        context.confirm(Invoice, record_id)
    app.business.policy = Policy(app.registry, rules={('editor', Posting): Rule(())})
    with app.business.context(User(2, 'editor', 'editor'), readonly=True) as context:
        event = context.history(Invoice, record_id)['items'][0]
        assert event['data']['effects'] == []
        assert context.snapshot(Invoice, record_id, revision=1)['effects'] == []


def test_excluded_only_changes_still_record_an_operation(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        context.update(Invoice, record_id, credential='changed-secret')
        event = context.history(Invoice, record_id)['items'][0]
        assert event['kind'] == 'changed' and event['changes'] == {}
        assert 'changed-secret' not in json.dumps(event)


def test_snapshot_hash_detects_privileged_database_tampering(app):
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        context.confirm(Invoice, record_id)
        context._tx.connection.execute('DROP TRIGGER evlet_revision_no_update')
        context._tx.connection.execute("UPDATE evlet_document_revisions SET data='{}'")
        with pytest.raises(AccessError, match='integrity'): context.snapshot(Invoice, record_id, revision=1)


def test_lifecycle_availability_requires_update_access(app):
    from evlet import Policy
    with app.business.context(ADMIN) as context: record_id = create(context)
    app.business.policy = Policy(app.registry, rules={('editor', Invoice): Rule(('read', 'confirm'))})
    user = User(2, 'editor', 'editor')
    with app.business.context(user, readonly=True) as context:
        record = context._tx.get(Invoice, record_id)
        assert app.business.availability(context, Invoice, record)['_lifecycle_actions'] == []
    entry = next(model for model in app.business.schema(user)['models'] if model['name'] == 'invoice')
    assert entry['lifecycle']['permissions']['confirm'] is False


@pytest.mark.parametrize('failure', ['snapshot', 'cancel_history'])
def test_late_failure_rolls_back_postings_state_and_history(app, failure):
    from evlet import ConflictError
    with app.business.context(ADMIN) as context:
        record_id = create(context)
        if failure == 'cancel_history': context.confirm(Invoice, record_id)
        table = 'evlet_document_revisions' if failure == 'snapshot' else 'evlet_journal'
        condition = '' if failure == 'snapshot' else "WHEN NEW.kind='cancelled' "
        context._tx.connection.execute(f"CREATE TRIGGER fail_late BEFORE INSERT ON {table} {condition}BEGIN SELECT RAISE(ABORT, 'late failure'); END")
        prior_history = context.history(Invoice, record_id)['total']
        with pytest.raises(ConflictError):
            if failure == 'snapshot': context.confirm(Invoice, record_id)
            else: context.cancel(Invoice, record_id, reason='Test rollback')
        assert context.history(Invoice, record_id)['total'] == prior_history
        assert context.revisions(Invoice, record_id)['total'] == (failure == 'cancel_history')
        assert context.get(Invoice, record_id)._lifecycle.state == ('draft' if failure == 'snapshot' else 'confirmed')
        assert context._tx.count(Posting) == (failure == 'cancel_history')
        context._tx.connection.execute('DROP TRIGGER fail_late')
        if failure == 'snapshot':
            context.confirm(Invoice, record_id)
            assert context.snapshot(Invoice, record_id, revision=1)['revision'] == 1


def test_snapshots_and_history_hide_links_to_unreadable_models(tmp_path):
    from evlet import Link
    class Target(Model):
        permissions = {'admin': Rule(('read', 'create', 'update'))}
        title = Text()
    class Linked(Model):
        confirmable = True
        target = Link(Target)
        permissions = {'admin': Rule(('read', 'create', 'update')),
                       'viewer': Rule(('read',))}
    database = Database(tmp_path / 'links.sqlite3', Registry(Target, Linked))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    with app.business.context(ADMIN) as context:
        target = context.create(Target, title='Private target')['id']
        record_id = context.create(Linked, target=target)['id']
        context.confirm(Linked, record_id)
    with app.business.context(User(2, 'viewer', 'viewer'), readonly=True) as context:
        with pytest.raises(AttributeError):
            context.get(Linked, record_id).target
        assert 'target' not in context.snapshot(Linked, record_id, revision=1)['document']
        created = next(item for item in context.history(Linked, record_id)['items'] if item['kind'] == 'created')
        assert 'target' not in created['data'].get('fields', {})


def test_confirmed_action_keeps_calculations_after_formula_version_change(tmp_path):
    from types import MappingProxyType
    class Calculated(Model):
        confirmable = True
        permissions = {'admin': Rule(('read', 'create', 'update'))}
        qty = Integer(default=2)
        rate = Currency(default='10')
        total = Currency(compute=qty * rate)
        remarks = Text(editable_when_locked=True)
        class Meta:
            calculation_language = 'finite-v1'
        @action
        def note(self): self.remarks = 'Checked'
    database = Database(tmp_path / 'kept.sqlite3', Registry(Calculated))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    with app.business.context(ADMIN) as context:
        record_id = context.create(Calculated)['id']
        context.confirm(Calculated, record_id)
    # An application upgrade changes the active formula version; confirmed results stay fixed.
    app.registry.formula_hashes = MappingProxyType({Calculated: 'new-formula-version'})
    with app.business.context(ADMIN) as context:
        result = context.action(Calculated, record_id, 'note')
        assert result['remarks'] == 'Checked' and result['total'] == '20.00'
        assert context.snapshot(Calculated, record_id, revision=1)['document']['total'] == '20.00'


def test_recompute_audit_applies_tracking_policies_before_storage(tmp_path):
    from evlet import CreateModel, Migration, Recompute
    def definition(multiplier):
        return type('Migrating', (Model,), {
            'track_changes': True,
            'value': Integer(default=17),
            'masked': Integer(compute=f'value * {multiplier}', tracking='mask'),
            'excluded': Integer(compute=f'value * {multiplier + 1}', tracking='exclude'),
        })
    Old = definition(2)
    base = Migration('0000_initial', CreateModel(Old))
    path = tmp_path / 'migration-tracking.sqlite3'
    database = Database(path, Registry(Old), migrations=(base,))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    with app.business.context(ADMIN) as context: created = context.create(Old)['id']
    New = definition(4)
    upgraded = Database(path, Registry(New))
    upgraded.migrate((base, Migration('0001_recompute', Recompute('migrating', ('masked', 'excluded')))))
    with upgraded.transaction() as tx:
        event = json.loads(tx.connection.execute("SELECT data FROM evlet_journal WHERE kind='recomputed'").fetchone()[0])
        assert event['fields']['masked'] == {'old': '[masked]', 'new': '[masked]'}
        assert 'excluded' not in event['fields']
        assert event['source'] == 'System' and event['revision'] == 0
    assert upgraded.get(New, created).masked == 68


def test_journal_can_filter_native_lifecycle_events(app):
    c = client(app)
    _, record, headers = c('POST', '/api/invoice', {'number': 'INV-001', 'customer': 'A'})
    path = f"/api/invoice/{record['id']}"
    assert c('POST', path + '/confirm', {}, headers={b'if-match': headers[b'etag']})[0] == 200
    status, page, _ = c('GET', path + '/journal?kind=confirmed')
    assert status == 200, page
    assert page['total'] == 1 and page['items'][0]['kind'] == 'confirmed'
    assert c('GET', path + '/journal?kind=unknown')[0] == 422


def test_confirmed_document_shows_kept_results_in_preview_detail_and_save(tmp_path):
    from evlet import Link
    from evlet.calc import F
    everything = Rule(('read', 'create', 'update', 'confirm', 'cancel', 'reopen'))

    class Product(Model):
        permissions = {'admin': Rule(('read', 'create', 'update'))}
        price = Currency(default='0')

    class Quote(Model):
        confirmable = True
        permissions = {'admin': everything}
        product = Link(Product)
        price = Currency(compute=F('product.price'))
        doubled = Currency(compute=F('price') * 2, stored=False)
        remarks = Text(editable_when_locked=True)
        class Meta:
            calculation_language = 'finite-v1'

    database = Database(tmp_path / 'kept.sqlite3', Registry(Product, Quote))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    with app.business.context(ADMIN) as context:
        product = context.create(Product, price='10')['id']
        quote = context.create(Quote, product=product)['id']
        context.confirm(Quote, quote)
        # The linked price changes after the quote was confirmed.
        context.update(Product, product, price='99')
    c = client(app)
    url = f'/api/quote/{quote}'
    # The draft preview, the record detail and the save all keep the confirmed price.
    preview = c('POST', '/api/_draft/quote', {'id': quote, 'values': {'remarks': 'Called'}})[1]['values']
    assert (preview['price'], preview['doubled']) == ('10.00', '20.00')
    detail = c('GET', url)[1]
    assert (detail['price'], detail['doubled']) == ('10.00', '20.00')
    saved = c('PATCH', url, {'remarks': 'Called'}, headers={b'if-match': c('GET', url)[2][b'etag']})[1]
    assert (saved['remarks'], saved['price'], saved['doubled']) == ('Called', '10.00', '20.00')
