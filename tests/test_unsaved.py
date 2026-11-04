"""Unsaved changes kept on leave: own entries only, writable fields only, the record is never written."""

import json
import sqlite3
import time

import pytest

from evlet import Model, Registry, Text
from evlet.framework import upgrade
from evlet.unsaved import DAY
from examples.playground.models import Customer, Invoice, Project, ProjectTask
from test_api_keys import create as create_key, with_key
from test_business import PASSWORD, client, hashed, http, secured  # noqa: F401

REVISION = '"' + 'a' * 64 + '"'


def project(app, **values):
    with app.database.transaction() as tx:
        record = tx.create(Project, title='Plan', **values)
        task = tx.create(ProjectTask, project=record.id, title='First')
    return record, task


def path(record, model='project'):
    return f'/api/_unsaved/{model}/{record.id}'


def opened(c, record, model='project'):
    status, _, headers = c('GET', f'/api/{model}/{record.id}')
    assert status == 200
    return headers[b'etag'].decode()


def rows(app):
    with app.database._connection() as connection:
        return [dict(row) for row in connection.execute('SELECT user_id, model, record_id, base_revision, saved_at, size FROM evlet_unsaved ORDER BY user_id')]


def counts(app):
    with app.database._connection() as connection:
        return {table: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                for table in ('evlet_events', 'evlet_audit', 'evlet_jobs')}


def test_leaving_keeps_writable_edits_apart_from_the_record(secured):
    record, task = project(secured)
    editor = client(secured, 'editor')
    revision = opened(editor, record)
    before = counts(secured)
    changes = {'values': {'title': 'Renamed', 'notes': 'Draft notes', 'status': 'done'}, 'base': {'title': 'Plan', 'status': 'planned'},
               'children': {'tasks': {'added': [{'key': 'n1', 'values': {'title': 'Second'}}],
                                      'changed': {str(task.id): {'values': {'done': True}, 'base': {'done': False}}}, 'removed': []}}}
    status, saved, _ = editor('PUT', path(record), {'changes': changes, 'base_revision': revision})
    assert status == 200, saved
    # status is read only, so it is dropped and listed; everything else is kept as sent.
    assert saved['dropped'] == ['status'] and saved['size'] > 0
    # The record, its rows, events, history and jobs are untouched.
    assert secured.database.get(Project, record.id).title == 'Plan'
    assert secured.database.get(ProjectTask, task.id).done is False
    assert secured.database.count(ProjectTask, filters={'project': record.id}) == 1
    assert counts(secured) == before
    assert opened(editor, record) == revision
    # Idempotent: the same body again leaves one row.
    assert editor('PUT', path(record), {'changes': changes, 'base_revision': revision})[0] == 200
    assert len(rows(secured)) == 1
    status, read, _ = editor('GET', path(record))
    assert status == 200 and read['dropped'] == []
    entry = read['entry']
    assert entry['stale'] is False and entry['base_revision'] == revision
    assert entry['changes'] == {'values': {'title': 'Renamed', 'notes': 'Draft notes'}, 'base': {'title': 'Plan'},
                                'children': {'tasks': {'added': [{'key': 'n1', 'values': {'title': 'Second'}}],
                                                       'changed': {str(task.id): {'values': {'done': True}, 'base': {'done': False}}}}}}
    # Discard.
    assert editor('DELETE', path(record))[0] == 204
    assert editor('GET', path(record))[1] == {'entry': None, 'dropped': []}
    assert editor('DELETE', path(record))[0] == 204


def test_only_the_owner_reads_an_entry(secured):
    record, _ = project(secured)
    admin, editor = client(secured), client(secured, 'editor')
    revision = opened(editor, record)
    assert editor('PUT', path(record), {'changes': {'values': {'title': 'Mine'}}, 'base_revision': revision})[0] == 200
    assert admin('GET', path(record))[1] == {'entry': None, 'dropped': []}
    assert admin('DELETE', path(record))[0] == 204
    assert editor('GET', path(record))[1]['entry']['changes'] == {'values': {'title': 'Mine'}}
    # Signed out, and with a CSRF token missing, nothing is read or written.
    assert http(secured, 'GET', path(record))[0] == 401
    assert http(secured, 'PUT', path(record), {'changes': {}, 'base_revision': revision}, cookie=editor.cookie)[0] == 403


def test_people_who_cannot_update_the_record_keep_nothing(secured):
    record, _ = project(secured)
    viewer = client(secured, 'viewer')
    revision = opened(viewer, record)
    status, payload, _ = viewer('PUT', path(record), {'changes': {'values': {'title': 'No'}}, 'base_revision': revision})
    assert status == 403, payload
    assert rows(secured) == []
    assert client(secured, 'editor')('PUT', '/api/_unsaved/project/0000000000zzz', {'changes': {'values': {'title': 'x'}}, 'base_revision': revision})[0] == 404


def test_unknown_names_bad_bodies_and_other_routes_are_refused(secured):
    record, task = project(secured)
    editor = client(secured, 'editor')
    revision = opened(editor, record)
    put = lambda changes, **extra: editor('PUT', path(record), {'changes': changes, 'base_revision': revision, **extra})
    assert put({'values': {'nope': 1}})[0] == 422
    assert put({'children': {'lines': {'added': []}}})[0] == 422
    assert put({'children': {'tasks': {'changed': {'12': {'values': {'done': True}}}}}})[0] == 422
    assert put({'children': {'tasks': {'added': [{'values': {'ghost': 1}}]}}})[0] == 422
    assert put({'other': {}})[0] == 422
    assert put({'values': {'title': 'x'}}, extra=1)[0] == 422
    assert editor('PUT', path(record), {'changes': {'values': {'title': 'x'}}, 'base_revision': 'v1'})[0] == 422
    assert editor('POST', path(record), {})[0] == 404
    assert editor('PUT', f'/api/_unsaved/project_task/{task.id}', {'changes': {}, 'base_revision': revision})[0] == 404
    assert editor('PUT', f'/api/_unsaved/nothing/{task.id}', {'changes': {}, 'base_revision': revision})[0] == 404
    assert editor('PUT', f'/api/_unsaved/project/{int(record.id)}', {'changes': {}, 'base_revision': revision})[0] == 404
    # An empty change set removes the entry instead of keeping an empty one.
    assert put({'values': {'title': 'x'}})[0] == 200 and len(rows(secured)) == 1
    assert put({'values': {}})[1]['saved_at'] is None and rows(secured) == []
    assert rows(secured) == []


def test_api_keys_and_assistants_cannot_use_the_store(secured):
    record, _ = project(secured)
    admin = client(secured)
    token = create_key(admin, scope={'access': 'write', 'models': None})[1]['token']
    revision = opened(admin, record)
    status, payload, _ = with_key(secured, token, 'PUT', path(record), {'changes': {'values': {'title': 'x'}}, 'base_revision': revision})
    assert status == 403 and 'API keys' in payload['error']
    assert with_key(secured, token, 'GET', path(record))[0] == 403
    assert rows(secured) == []


def test_oversize_entries_are_refused_with_a_clear_message(secured):
    record, _ = project(secured)
    editor = client(secured, 'editor')
    revision = opened(editor, record)
    status, payload, _ = editor('PUT', path(record), {'changes': {'values': {'notes': 'x' * (257 * 1024)}}, 'base_revision': revision})
    assert status == 413 and 'larger than 256 KB' in payload['error'] and 'stay in this tab' in payload['error']
    assert rows(secured) == []
    assert editor('PUT', path(record), {'changes': {'values': {'notes': 'x' * (200 * 1024)}}, 'base_revision': revision})[0] == 200
    # The transport limit leaves room for the largest allowed setting, and no more.
    assert editor('PUT', path(record), {'changes': {'values': {'notes': 'x' * (1100 * 1024)}}, 'base_revision': revision})[0] == 413


def test_a_moved_record_is_stale_and_lost_access_drops_the_entry(secured):
    record, _ = project(secured)
    admin, editor = client(secured), client(secured, 'editor')
    revision = opened(editor, record)
    assert editor('PUT', path(record), {'changes': {'values': {'title': 'Mine', 'notes': 'n'}, 'base': {'title': 'Plan', 'notes': None}},
                                        'base_revision': revision})[0] == 200
    current = opened(admin, record)
    assert admin('PATCH', f'/api/project/{record.id}', {'title': 'Theirs'}, headers={b'if-match': current.encode()})[0] == 200
    entry = editor('GET', path(record))[1]['entry']
    assert entry['stale'] is True and entry['changes']['base'] == {'title': 'Plan', 'notes': None}
    # Notes stop being writable for editors: the field is dropped and named.
    from evlet import Rule
    from evlet.permissions import Policy
    policy = secured.business.policy
    narrowed = Policy(secured.registry, rules={**policy.declared, ('editor', Project): Rule(('read', 'create', 'update', 'delete'), write_fields=('title', 'customer', 'budget', 'progress', 'starts_on'))})
    secured.business.policy = narrowed
    read = editor('GET', path(record))[1]
    assert read['dropped'] == ['notes'] and read['entry']['changes']['values'] == {'title': 'Mine'}
    # Read but no longer update: the whole entry goes, and the reply says so.
    secured.business.policy = Policy(secured.registry, rules={**policy.declared, ('editor', Project): Rule(('read',))})
    assert editor('GET', path(record))[1] == {'entry': None, 'dropped': 'record'}
    assert rows(secured) == []
    secured.business.policy = policy


def test_save_and_delete_remove_entries_in_their_transaction(secured):
    record, _ = project(secured)
    admin, editor = client(secured), client(secured, 'editor')
    for c in (admin, editor):
        assert c('PUT', path(record), {'changes': {'values': {'title': 'Kept'}}, 'base_revision': opened(c, record)})[0] == 200
    assert len(rows(secured)) == 2
    # A save removes the saver's own entry only.
    assert editor('PATCH', f'/api/project/{record.id}', {'title': 'Saved'}, headers={b'if-match': opened(editor, record).encode()})[0] == 200
    assert [row['user_id'] for row in rows(secured)] == [1]
    # Deleting the record removes everyone's.
    assert editor('PUT', path(record), {'changes': {'values': {'title': 'Again'}}, 'base_revision': opened(editor, record)})[0] == 200
    assert admin('DELETE', f'/api/project/{record.id}', headers={b'if-match': opened(admin, record).encode()})[0] == 204
    assert rows(secured) == []


def test_documents_keep_edits_without_numbers_validators_or_history(secured):
    with secured.database.transaction() as tx:
        customer = tx.create(Customer, customer_name='Acme', email='a@example.com')
    admin = client(secured)
    status, invoice, _ = admin('POST', '/api/invoice', {'customer': str(customer.id), 'reference': 'PO-1', 'total': '10.00'})
    assert status == 201, invoice
    record = type('Opened', (), {'id': invoice['id']})
    etag = opened(admin, record, 'invoice')
    with secured.database._connection() as connection:
        counters = connection.execute('SELECT * FROM evlet_sequence').fetchall()
        before = [tuple(row) for row in counters], counts(secured)
    # A total under the minimum would fail validation on Save; leaving keeps it anyway.
    status, saved, _ = admin('PUT', f'/api/_unsaved/invoice/{invoice["id"]}', {'changes': {'values': {'total': '0.00', 'reference': ''}},
                                                                             'base_revision': etag})
    assert status == 200, saved
    with secured.database._connection() as connection:
        assert ([tuple(row) for row in connection.execute('SELECT * FROM evlet_sequence')], counts(secured)) == before
    assert admin('GET', f'/api/invoice/{invoice["id"]}')[1]['total'] == '10.00'
    # A document save goes through the document pipeline and removes the entry.
    assert opened(admin, record, 'invoice') == etag
    assert admin('PATCH', f'/api/invoice/{invoice["id"]}', {'reference': 'PO-2'}, headers={b'if-match': etag.encode()})[0] == 200
    assert rows(secured) == []


def test_a_model_can_opt_out_and_a_person_can_turn_it_off(secured):
    with secured.database.transaction() as tx:
        customer = tx.create(Customer, customer_name='Acme', email='a@example.com')
    admin, editor = client(secured), client(secured, 'editor')
    schema = {model['name']: model for model in editor('GET', '/api/schema')[1]['models']}
    assert schema['project']['autosave'] is True and schema['payment']['autosave'] is False
    status, payload, _ = admin('PUT', f'/api/_unsaved/payment/{customer.id}', {'changes': {}, 'base_revision': REVISION})
    assert status == 404 and "doesn't keep unsaved changes" in payload['error']
    record, _ = project(secured)
    assert editor('PUT', path(record), {'changes': {'values': {'title': 'x'}}, 'base_revision': opened(editor, record)})[0] == 200
    assert editor('GET', '/api/_unsaved')[1] == {'count': 1, 'enabled': True, 'limits': {
        'server_days': 30, 'server_entries': 200, 'browser_days': 7, 'browser_entries': 20, 'entry_kb': 256}}
    # Turning the setting off forgets what was kept and refuses new entries.
    assert editor('PATCH', '/api/_settings/me/prefs', {'keep_unsaved': False})[0] == 200
    assert rows(secured) == [] and editor('GET', '/api/_unsaved')[1]['enabled'] is False
    status, payload, _ = editor('PUT', path(record), {'changes': {'values': {'title': 'x'}}, 'base_revision': opened(editor, record)})
    assert status == 403 and 'turned off' in payload['error']
    assert editor('PATCH', '/api/_settings/me/prefs', {'keep_unsaved': 'no'})[0] == 422
    assert editor('GET', '/api/_settings/me')[1]['prefs']['keep_unsaved'] is False


def test_meta_autosave_is_validated_at_registration():
    class Note(Model):
        class Meta:
            autosave = 'yes'
        body = Text()
    with pytest.raises(ValueError, match='Meta.autosave is True or False'):
        Registry(Note)

    class Quiet(Model):
        class Meta:
            autosave = False
        body = Text()
    assert next(model for model in Registry(Quiet).ui_schema()['models'])['autosave'] is False


def insert(app, user_id, record_id, saved_at, model='project'):
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_unsaved(user_id,model,record_id,changes,base_revision,saved_at,size) VALUES (?,?,?,?,?,?,?)',
                              (user_id, model, record_id, '{"values":{"title":"x"}}', REVISION, saved_at, 24))


def test_expiry_and_caps_purge_in_batches(secured):
    now = int(time.time())
    unsaved = secured.business.unsaved
    for index in range(12):
        insert(secured, 1, 1000 + index, now - 31 * DAY - index)
    insert(secured, 1, 2000, now - 29 * DAY)
    assert unsaved.purge() == 12
    assert [row['record_id'] for row in rows(secured)] == [2000]
    secured.business.controls.settings['unsaved.server_entries'] = 10
    for index in range(14):
        insert(secured, 2, 3000 + index, now - index)
    assert unsaved.purge() == 4
    kept = sorted(row['record_id'] for row in rows(secured) if row['user_id'] == 2)
    assert kept == list(range(3000, 3010))
    # Each PUT applies the cap to the person straight away, dropping the oldest.
    record, _ = project(secured)
    editor = client(secured, 'editor')
    assert editor('PUT', path(record), {'changes': {'values': {'title': 'New'}}, 'base_revision': opened(editor, record)})[0] == 200
    kept = sorted(row['record_id'] for row in rows(secured) if row['user_id'] == 2)
    assert len(kept) == 10 and record.id in kept and 3009 not in kept
    # An expired entry is not offered, and is removed when read.
    old, _ = project(secured)
    insert(secured, 2, old.id, now - 40 * DAY)
    assert editor('GET', path(old))[1] == {'entry': None, 'dropped': []}
    assert old.id not in [row['record_id'] for row in rows(secured)]
    # The daily clean-up is a framework schedule when jobs are on.
    if secured.business.jobs is not None:
        with secured.database._connection() as connection:
            assert connection.execute("SELECT interval FROM evlet_schedules WHERE name='evlet_unsaved_purge'").fetchone()[0] == DAY


def test_controls_shows_counts_never_contents_and_clears_old_entries(secured):
    now = int(time.time())
    insert(secured, 2, 1, now - 10 * DAY)
    insert(secured, 2, 2, now - DAY)
    insert(secured, 3, 3, now)
    admin = client(secured)
    status, summary, _ = admin('GET', '/api/_controls/unsaved')
    assert status == 200
    assert summary['entries'] == 3 and summary['bytes'] == 72 and summary['oldest'] == now - 10 * DAY
    assert [(person['username'], person['entries'], person['bytes']) for person in summary['people']] == [('editor', 2, 48), ('viewer', 1, 24)]
    assert 'title' not in json.dumps(summary) and 'changes' not in json.dumps(summary)
    assert client(secured, 'editor')('GET', '/api/_controls/unsaved')[0] == 404
    assert admin('POST', '/api/_controls/unsaved/clear', {'days': -1})[0] == 422
    status, cleared, _ = admin('POST', '/api/_controls/unsaved/clear', {'days': 5})
    assert status == 200 and cleared['cleared'] == 1 and cleared['entries'] == 2
    with secured.database._connection() as connection:
        row = connection.execute("SELECT actor, area, detail FROM evlet_audit WHERE target='Unsaved changes'").fetchone()
    assert tuple(row) == ('admin', 'Records', 'Cleared 1 unsaved entry older than 5 days')


def test_limits_are_instance_settings_with_safe_maximums(secured):
    admin = client(secured)
    def save(key, value):
        return admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': key, 'value': value}]})
    assert save('unsaved.server_days', 91)[0] == 422
    assert save('unsaved.entry_kb', 2048)[0] == 422
    assert save('unsaved.browser_entries', True)[0] == 422
    assert save('unsaved.server_days', 60)[0] == 200
    assert save('unsaved.entry_kb', 16)[0] == 200
    assert admin('GET', '/api/_unsaved')[1]['limits']['server_days'] == 60
    record, _ = project(secured)
    status, payload, _ = admin('PUT', path(record), {'changes': {'values': {'notes': 'x' * 17000}}, 'base_revision': opened(admin, record)})
    assert status == 413 and '16 KB' in payload['error']
    with secured.database._connection() as connection:
        assert connection.execute("SELECT area FROM evlet_audit WHERE target='unsaved.server_days'").fetchone()[0] == 'Records'


def test_the_framework_step_creates_the_table(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE evlet_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
    from evlet import Database
    class Memo(Model):
        body = Text()
    database = Database(path, Registry(Memo))
    database.initialize()
    assert '0004_unsaved_changes' in upgrade(database)
    with sqlite3.connect(path) as connection:
        assert [row[1] for row in connection.execute('PRAGMA table_info(evlet_unsaved)')] == [
            'user_id', 'model', 'record_id', 'changes', 'base_revision', 'saved_at', 'size']
