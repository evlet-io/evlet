"""Draft CAS fencing across aborted workers, concurrent tabs, Save and cleanup; real SQLite/ASGI."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from evlet import qb
from evlet.auth import User
from examples.playground.models import Customer, Project
from test_business import client, hashed, secured  # noqa: F401
from test_unsaved import REVISION, opened, path, project


def body(title, revision):
    return {'changes': {'values': {'title': title}}, 'base_revision': revision}


def match(etag):
    return {b'if-match': etag}


def test_draft_cas_preserves_newer_copy_and_legacy_callers_remain_supported(secured):
    record, _ = project(secured)
    person = client(secured, 'editor')
    revision, route = opened(person, record), path(record)
    empty = person('GET', route)[2][b'etag']
    first = person('PUT', route, body('First', revision), headers=match(empty))
    assert first[0] == 200 and first[2][b'etag'] != empty
    assert person('PUT', route, body('Stale tab', revision), headers=match(empty))[0] == 412
    second = person('PUT', route, body('Second', revision), headers=match(first[2][b'etag']))
    assert second[0] == 200 and second[2][b'etag'] != first[2][b'etag']
    # A late normal-save compensation may delete only the copy it actually wrote.
    assert person('DELETE', route, headers=match(first[2][b'etag']))[0] == 412
    assert person('GET', route)[1]['entry']['changes']['values']['title'] == 'Second'
    deleted = person('DELETE', route, headers=match(second[2][b'etag']))
    assert deleted[0] == 204 and deleted[2][b'etag'] not in (empty, second[2][b'etag'])
    assert person('PUT', route, body('Old empty tab', revision), headers=match(empty))[0] == 412
    # Existing clients retain their unconditional contract and unchanged JSON body shape.
    assert person('PUT', route, body('Legacy caller', revision))[0] == 200
    assert person('GET', route)[1]['entry']['changes']['values']['title'] == 'Legacy caller'
    assert person('DELETE', route)[0] == 204


def test_browser_draft_cas_fixture_matches_real_authenticated_responses(secured):
    fixture = json.loads((Path(__file__).parents[1] / 'frontend/src/evlet/tests/unsaved-cas-fixture.json').read_text())
    record, _ = project(secured)
    person = client(secured, 'editor')
    revision, route = opened(person, record), path(record)
    captured = {}
    def capture(name, method, values=None, headers=None):
        status, result, response_headers = person(method, route, values, headers=headers or {})
        captured[name] = {'status': status, 'body': result.decode() if isinstance(result, bytes) else result,
                          'etag': response_headers.get(b'etag', b'').decode() or None}
        return response_headers.get(b'etag')
    empty = capture('empty', 'GET')
    kept = capture('kept', 'PUT', body('Kept draft', revision), match(empty))
    capture('read_kept', 'GET')
    capture('stale_put', 'PUT', body('Old draft', revision), match(empty))
    capture('stale_delete', 'DELETE', headers=match(empty))
    capture('deleted', 'DELETE', headers=match(kept))
    capture('read_deleted', 'GET')
    # Only wall time and this newly created record's base revision differ from the recorded fixture.
    fixture['kept']['body']['saved_at'] = captured['kept']['body']['saved_at']
    fixture['read_kept']['body']['entry']['saved_at'] = captured['kept']['body']['saved_at']
    fixture['read_kept']['body']['entry']['base_revision'] = revision
    assert captured == fixture


@pytest.mark.parametrize('operation', ['discard', 'save', 'preference'])
def test_empty_draft_version_changes_when_pending_work_must_be_fenced(secured, operation):
    record, _ = project(secured)
    person = client(secured, 'editor')
    revision, route = opened(person, record), path(record)
    before = person('GET', route)[2][b'etag']
    if operation == 'discard':
        assert person('DELETE', route, headers=match(before))[0] == 204
    elif operation == 'save':
        assert person('PATCH', f'/api/project/{record.id}', {'title': 'Saved record'})[0] == 200
    else:
        assert person('PATCH', '/api/_settings/me/prefs', {'keep_unsaved': False})[0] == 200
        assert person('PATCH', '/api/_settings/me/prefs', {'keep_unsaved': True})[0] == 200
    after = person('GET', route)
    assert after[1]['entry'] is None and after[2][b'etag'] != before
    assert person('PUT', route, body('Old in-flight copy', revision), headers=match(before))[0] == 412


@pytest.mark.parametrize('kind', ['document', 'plain'])
@pytest.mark.parametrize('copy', ['matching', 'newer', 'unacknowledged'])
def test_older_record_save_clears_only_its_acknowledged_draft(secured, monkeypatch, kind, copy):
    if kind == 'document':
        record, _ = project(secured)
        model, field = 'project', 'title'
    else:
        with secured.database.transaction() as tx:
            record = tx.create(Customer, customer_name='Original')
        model, field = 'customer', 'customer_name'
    person = client(secured, 'editor')
    revision, route = opened(person, record, model), path(record, model)
    change = lambda value: {'changes': {'values': {field: value}}, 'base_revision': revision}
    first = person('PUT', route, change('Draft A'))
    expected = first[2][b'etag'] if copy != 'unacknowledged' else b'preserve'
    entered, release = threading.Event(), threading.Event()
    original = secured.business.dispatch
    def delayed(scope, values):
        if scope['method'] == 'PATCH' and values.get(field) == 'Saved B':
            entered.set()
            assert release.wait(10)
        return original(scope, values)
    monkeypatch.setattr(secured.business, 'dispatch', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        saved = pool.submit(person, 'PATCH', f'/api/{model}/{record.id}', {field: 'Saved B'},
                            headers={b'if-match': revision.encode(), b'x-evlet-draft-revision': expected})
        try:
            assert entered.wait(5)
            if copy != 'matching':
                assert person('PUT', route, change('Newer draft C'), headers=match(first[2][b'etag']))[0] == 200
        finally:
            release.set()
        assert saved.result(timeout=5)[0] == 200
    read = person('GET', route)[1]['entry']
    if copy == 'matching':
        assert read is None
    else:
        assert read['changes']['values'][field] == 'Newer draft C' and read['stale'] is True
    assert person('GET', f'/api/{model}/{record.id}')[1][field] == 'Saved B'
    # A draft-cleanup token never substitutes for the record's own optimistic concurrency guard.
    assert person('PATCH', f'/api/{model}/{record.id}', {field: 'Wrong revision'},
                  headers={b'if-match': revision.encode(), b'x-evlet-draft-revision': b'preserve'})[0] == 409


def test_preserving_record_save_still_invalidates_an_empty_draft_request(secured):
    record, _ = project(secured)
    person = client(secured, 'editor')
    revision, route = opened(person, record), path(record)
    expected = person('GET', route)[2][b'etag']
    assert person('PATCH', f'/api/project/{record.id}', {'title': 'Saved'},
                  headers={b'if-match': revision.encode(), b'x-evlet-draft-revision': b'preserve'})[0] == 200
    assert person('PUT', route, body('Late draft', revision), headers=match(expected))[0] == 412


def test_aborted_old_put_cannot_overwrite_later_confirmed_draft(secured, monkeypatch):
    record, _ = project(secured)
    person = client(secured, 'editor')
    revision, route = opened(person, record), path(record)
    expected = person('GET', route)[2][b'etag']
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original, rejected = secured.business.unsaved.put, []

    def delayed(user, model, name, record_id, values, **options):
        if values['changes']['values']['title'] == 'Older draft':
            entered.set()
            assert release.wait(10)
            try:
                return original(user, model, name, record_id, values, **options)
            except Exception as exc:
                rejected.append(getattr(exc, 'status', None))
                raise
            finally:
                finished.set()
        return original(user, model, name, record_id, values, **options)

    monkeypatch.setattr(secured.business.unsaved, 'put', delayed)

    async def request(method, payload=None):
        sent, events = [], [{'type': 'http.request', 'body': json.dumps(payload or {}).encode()}]
        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}
        async def send(message):
            sent.append(message)
        scope = {'type': 'http', 'method': method, 'path': route, 'query_string': b'',
                 'headers': [(b'host', b'127.0.0.1:8000'), (b'content-type', b'application/json'),
                             (b'x-evlet-request', b'1'), (b'cookie', person.cookie.encode()),
                             (b'x-csrf-token', person.csrf.encode()), (b'if-match', expected)],
                 'client': ('127.0.0.1', 1234)}
        await secured(scope, receive, send)
        return sent[0]['status'], json.loads(sent[1]['body'])

    async def check():
        old = asyncio.create_task(request('PUT', body('Older draft', revision)))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            old.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old
            assert (await request('PUT', body('Newest confirmed draft', revision)))[0] == 200
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
            assert rejected == [412]
            status, current = await request('GET')
            assert status == 200 and current['entry']['changes']['values']['title'] == 'Newest confirmed draft'
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 5)
    asyncio.run(check())


def test_expiry_read_cleanup_cannot_remove_a_newer_entry(secured, monkeypatch):
    record, _ = project(secured)
    person = client(secured, 'editor')
    revision, route = opened(person, record), path(record)
    first = person('PUT', route, body('Expired', revision))
    with secured.database.transaction() as tx:
        tx.connection.run(qb.update('evlet_unsaved', saved_at=0).where(model='project', record_id=record.id))
    entered, release = threading.Event(), threading.Event()
    original = secured.business.unsaved.read_entry
    def delayed(*args):
        result = original(*args)
        if result[1]:
            entered.set()
            assert release.wait(10)
        return result
    monkeypatch.setattr(secured.business.unsaved, 'read_entry', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(person, 'GET', route)
        try:
            assert entered.wait(5)
            assert person('PUT', route, body('New copy', revision), headers=match(first[2][b'etag']))[0] == 200
        finally:
            release.set()
        assert old.result(timeout=5)[0] == 200
    assert person('GET', route)[1]['entry']['changes']['values']['title'] == 'New copy'


def test_existing_draft_schema_is_upgraded_and_unrelated_entries_keep_their_tokens(secured):
    record, _ = project(secured)
    other, _ = project(secured)
    person = client(secured, 'editor')
    revision, route = opened(person, record), path(record)
    assert person('PUT', route, body('Retained', revision))[0] == 200
    # Reconstruct the old layout. Startup must preserve its content and add only internal metadata.
    with secured.database.schema_transaction() as connection:
        connection.execute('ALTER TABLE evlet_unsaved DROP COLUMN version')
        connection.execute('DROP TABLE evlet_unsaved_versions')
    secured.business.unsaved.initialize()
    before = person('GET', route)
    assert before[1]['entry']['changes']['values']['title'] == 'Retained'
    assert person('PUT', path(other), body('Other record', opened(person, other)))[0] == 200
    assert person('GET', route)[2][b'etag'] == before[2][b'etag']
    assert person('PUT', route, body('Updated', revision), headers=match(before[2][b'etag']))[0] == 200


def test_same_second_cap_keeps_the_just_confirmed_copy(secured, monkeypatch):
    person = client(secured, 'editor')
    secured.business.controls.settings['unsaved.server_entries'] = 10
    records = [project(secured)[0] for _ in range(11)]
    import evlet.unsaved as module
    now = int(module.time.time())
    monkeypatch.setattr(module.time, 'time', lambda: now)
    for record in records:
        response = person('PUT', path(record), body(str(record.id), opened(person, record)))
        assert response[0] == 200
        read = person('GET', path(record))
        assert read[1]['entry'] is not None and read[2][b'etag'] == response[2][b'etag']
    assert person('GET', path(records[0]))[1]['entry'] is None


@pytest.mark.parametrize('clock_backwards', [False, True])
def test_delayed_writer_keeps_the_copy_it_confirms(secured, monkeypatch, clock_backwards):
    engine = secured.business.unsaved
    secured.business.controls.settings['unsaved.server_entries'] = 10
    records = [project(secured)[0] for _ in range(11)]
    with secured.database._connection() as connection:
        user_id = connection.run(qb.select('evlet_users', 'id').where(username='editor')).value()
    user = User(user_id, 'editor', 'editor')
    original = secured.database.transaction
    entered, release = threading.Event(), threading.Event()
    import evlet.unsaved as module
    now = [int(module.time.time())]
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: now[0]))

    @contextmanager
    def delayed(*args, **kwargs):
        if threading.current_thread().name.startswith('old-put') and not entered.is_set():
            entered.set()
            assert release.wait(10)
        with original(*args, **kwargs) as tx:
            yield tx

    monkeypatch.setattr(secured.database, 'transaction', delayed)
    def put(record, title):
        return engine.put(user, Project, 'project', record.id, body(title, REVISION))
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='old-put') as pool:
        old = pool.submit(put, records[0], 'Confirmed after wait')
        try:
            assert entered.wait(3)
            now[0] += 2
            for record in records[1:]:
                assert put(record, 'Other copy')[0] == 200
            if clock_backwards:
                now[0] -= 60
        finally:
            release.set()
        result = old.result(timeout=5)
    with secured.database._connection() as connection:
        saved = connection.run(qb.select('evlet_unsaved', 'changes', 'version', 'saved_at')
                               .where(user_id=user_id, model='project', record_id=records[0].id)).one()
    assert result[0] == 200 and saved is not None
    assert dict(result[2])[b'etag'] == f'"unsaved-{saved["version"]}"'.encode()
    assert saved['saved_at'] == now[0]
    assert json.loads(saved['changes'])['values']['title'] == 'Confirmed after wait'
