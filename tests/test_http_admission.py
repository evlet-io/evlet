"""Real SQLite requests under contention, cancellation, slow bodies and shutdown."""
import asyncio
import importlib
import json
import threading

import pytest

from tests.support import client

from evlet import Application, Database, Model, Registry, Text


class Item(Model):
    title = Text(required=True)


@pytest.fixture
def bounded(tmp_path, monkeypatch):
    module = importlib.import_module('evlet.runtime')
    runtime = module.HTTPRuntime({name: 1 for name in module.HTTPRuntime.DEFAULTS})
    runtime.queue_seconds = .05  # Overload tests reach the 503 quickly.
    monkeypatch.setattr(module, '_runtime', runtime)
    app = Application(Database(tmp_path / 'data.sqlite3', Registry(Item)))
    app.initialize()
    yield app, runtime
    runtime.close()
    app.database.close()


async def request(app, method='GET', path='/health', body=None, receive=None, send=None, headers=()):
    sent = []
    async def incoming():
        return {'type': 'http.request', 'body': json.dumps(body or {}).encode()}
    async def outgoing(event):
        sent.append(event)
        if send is not None:
            await send(event)
    await app({'type': 'http', 'method': method, 'path': path,
               'headers': [(b'host', b'127.0.0.1:8000'), (b'content-type', b'application/json'), *headers]},
              receive or incoming, outgoing)
    return sent


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


def test_cancelled_writer_keeps_admission_and_reads_keep_capacity(bounded):
    app, runtime = bounded
    held, release = threading.Event(), threading.Event()
    def hold():
        with app.database.transaction():
            held.set()
            assert release.wait(5)
    blocker = threading.Thread(target=hold)
    blocker.start()
    assert held.wait(2)
    async def run():
        writer = asyncio.create_task(request(app, 'POST', '/api/item', {'title': 'One'}))
        await until(lambda: bool(app._work.futures))
        writer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writer
        assert (await request(app, 'POST', '/api/item', {'title': 'Too many'}))[0]['status'] == 503
        assert (await asyncio.wait_for(request(app, path='/api/item'), 1))[0]['status'] == 200
        assert (await asyncio.wait_for(request(app), 1))[0]['status'] == 200
        assert len(app._work.futures) == 1
        assert runtime.lanes['write'].executor._work_queue.qsize() <= 1
        release.set()
        await until(lambda: not app._work.futures)
        assert (await request(app, 'POST', '/api/item', {'title': 'Two'}))[0]['status'] == 201
        assert sorted(item.title for item in app.database.list(Item)) == ['One', 'Two']
    try:
        asyncio.run(run())
    finally:
        release.set()
        blocker.join(3)


def test_stalled_body_holds_no_write_slot_and_times_out_when_idle(bounded):
    app, runtime = bounded
    runtime.body_idle_seconds = .05
    async def run():
        reading = asyncio.Event()
        async def stalled():
            reading.set()
            await asyncio.Future()
        first = asyncio.create_task(request(app, 'POST', '/api/item', receive=stalled))
        await reading.wait()
        # The only write slot stays free while the first body has not arrived.
        assert runtime.lanes['write'].active() == 0
        assert (await asyncio.wait_for(request(app, 'POST', '/api/item', {'title': 'Other'}), 1))[0]['status'] == 201
        assert (await asyncio.wait_for(first, 1))[0]['status'] == 408
        assert (await request(app, 'POST', '/api/item', {'title': 'Recovered'}))[0]['status'] == 201
    asyncio.run(run())


def test_slow_steady_body_is_accepted_beyond_the_idle_limit(bounded):
    app, runtime = bounded
    runtime.body_idle_seconds = .1
    payload = json.dumps({'title': 'Slow but steady'}).encode()
    chunks = [payload[index:index + 3] for index in range(0, len(payload), 3)]
    async def run():
        sent = iter(range(len(chunks)))
        async def dribble():
            index = next(sent)
            await asyncio.sleep(.03)  # Total time well beyond the idle limit, each gap within it.
            return {'type': 'http.request', 'body': chunks[index], 'more_body': index < len(chunks) - 1}
        started = asyncio.get_running_loop().time()
        response = await request(app, 'POST', '/api/item', receive=dribble)
        assert asyncio.get_running_loop().time() - started > 2 * runtime.body_idle_seconds
        assert response[0]['status'] == 201
    asyncio.run(run())
    assert [item.title for item in app.database.list(Item)] == ['Slow but steady']


def test_full_lane_queues_then_refuses_with_retry_after(bounded):
    app, runtime = bounded
    held, release = threading.Event(), threading.Event()
    original = app._dispatch
    def gated(scope, body):
        if body and body.get('title') == 'Hold':
            held.set()
            assert release.wait(5)
        return original(scope, body)
    app._dispatch = gated
    async def run():
        first = asyncio.create_task(request(app, 'POST', '/api/item', {'title': 'Hold'}))
        await until(held.is_set)
        rejected = await request(app, 'POST', '/api/item', {'title': 'Refused'})
        assert rejected[0]['status'] == 503 and (b'retry-after', b'1') in rejected[0]['headers']
        runtime.queue_seconds = 3
        queued = asyncio.create_task(request(app, 'POST', '/api/item', {'title': 'Queued'}))
        await until(lambda: runtime.lanes['write'].waiting() == 1)
        release.set()
        assert (await first)[0]['status'] == 201
        assert (await asyncio.wait_for(queued, 2))[0]['status'] == 201
        assert runtime.lanes['write'].active() == 0 and runtime.lanes['write'].waiting() == 0
    try:
        asyncio.run(run())
    finally:
        release.set()
    assert sorted(item.title for item in app.database.list(Item)) == ['Hold', 'Queued']


def test_slow_response_retains_admission(bounded):
    app, _ = bounded
    async def run():
        sending, release = asyncio.Event(), asyncio.Event()
        async def stalled(event):
            if event['type'] == 'http.response.body':
                sending.set()
                await release.wait()
        first = asyncio.create_task(request(app, path='/api/item', send=stalled))
        await sending.wait()
        assert (await request(app, path='/api/item'))[0]['status'] == 503
        assert (await request(app))[0]['status'] == 200
        release.set()
        await first
    asyncio.run(run())


def test_shutdown_waits_for_cancelled_sqlite_work(bounded, monkeypatch):
    app, _ = bounded
    held, release = threading.Event(), threading.Event()
    def hold():
        with app.database.transaction():
            held.set()
            assert release.wait(5)
    blocker = threading.Thread(target=hold)
    blocker.start()
    assert held.wait(2)
    closed = threading.Event()
    original = app.database.close
    def close():
        assert not app._work.futures
        original()
        closed.set()
    monkeypatch.setattr(app.database, 'close', close)
    async def run():
        writer = asyncio.create_task(request(app, 'POST', '/api/item', {'title': 'Commit before close'}))
        await until(lambda: bool(app._work.futures))
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        async def receive(): return {'type': 'lifespan.shutdown'}
        sent = []
        async def send(event): sent.append(event)
        shutdown = asyncio.create_task(app({'type': 'lifespan'}, receive, send))
        await asyncio.sleep(.03)
        assert not closed.is_set() and not shutdown.done()
        assert (await request(app))[0]['status'] == 503
        release.set()
        await asyncio.wait_for(shutdown, 2)
        assert closed.is_set() and sent == [{'type': 'lifespan.shutdown.complete'}]
    try:
        asyncio.run(run())
    finally:
        release.set()
        blocker.join(3)


def test_correlation_is_bounded_and_logs_no_raw_path_or_query(bounded, caplog):
    import logging
    app, runtime = bounded
    runtime.log_requests = True
    with caplog.at_level(logging.INFO, logger='evlet.http'):
        sent = asyncio.run(request(app, path='/api/secret-user-value'))
    request_id = dict(sent[0]['headers'])[b'x-request-id'].decode()
    assert len(request_id) == 32 and int(request_id, 16) >= 0
    record = next(row for row in caplog.records if row.name == 'evlet.http')
    assert record.request_id == request_id and record.http_route == '/api/{unknown}'
    assert record.http_status == 404 and record.duration_ms >= 0
    assert 'secret-user-value' not in str(record.__dict__)


def test_streams_are_admitted_but_do_not_park_read_workers(bounded, secured):
    _, runtime = bounded
    user = client(secured, 'editor')
    headers = [(b'cookie', user.cookie.encode())]
    async def run():
        first_page, disconnect = asyncio.Event(), asyncio.Event()
        async def receive():
            await disconnect.wait()
            return {'type': 'http.disconnect'}
        async def send(event):
            if event['type'] == 'http.response.body':
                first_page.set()
        stream = asyncio.create_task(request(secured, path='/api/_events/stream', receive=receive, send=send, headers=headers))
        try:
            await asyncio.wait_for(first_page.wait(), 2)
            assert runtime.lanes['stream'].active() == 1
            assert not secured._work.futures
            assert (await request(secured, path='/api/_events/stream', headers=headers))[0]['status'] == 503
            assert (await request(secured, path='/api/customer', headers=headers))[0]['status'] == 200
            assert (await request(secured))[0]['status'] == 200
        finally:
            disconnect.set()
            await asyncio.wait_for(stream, 2)
        assert runtime.lanes['stream'].active() == 0
    asyncio.run(run())


@pytest.mark.parametrize('cancel_queued', [False, True])
def test_read_burst_has_a_bounded_queue_and_independent_probe(bounded, http_capacity, monkeypatch, cancel_queued):
    app, _ = bounded
    # Eight read threads whatever this host's CPU count (the default scales with it).
    monkeypatch.setenv('EVLET_HTTP_READ_WORKERS', '8')
    http_capacity(read=32)
    module = importlib.import_module('evlet.runtime')
    runtime = module.runtime()
    assert runtime.lanes['read'].workers == 8
    release = threading.Event()
    entered = []
    lock = threading.Lock()
    original = app._dispatch
    def gated(scope, body):
        if scope['path'] == '/api/item':
            with lock:
                entered.append(1)
            assert release.wait(5)
        return original(scope, body)  # Every admitted request still reads real SQLite.
    monkeypatch.setattr(app, '_dispatch', gated)
    async def run():
        readers = [asyncio.create_task(request(app, path='/api/item')) for _ in range(32)]
        try:
            await until(lambda: len(app._work.futures) == 32 and len(entered) == 8)
            runtime.queue_seconds = .05
            assert runtime.lanes['read'].active() == 32
            assert runtime.lanes['read'].executor._work_queue.qsize() == 24
            assert (await request(app, path='/api/item'))[0]['status'] == 503
            assert (await asyncio.wait_for(request(app), 1))[0]['status'] == 200
            if cancel_queued:
                readers[-1].cancel()
                await asyncio.gather(readers[-1], return_exceptions=True)
                assert runtime.lanes['read'].active() == 32
                assert (await request(app, path='/api/item'))[0]['status'] == 503
            release.set()
            results = await asyncio.gather(*readers, return_exceptions=True)
            assert sum(isinstance(row, list) and row[0]['status'] == 200 for row in results) == 32 - int(cancel_queued)
            await until(lambda: not app._work.futures)
            assert len(entered) == 32 and runtime.lanes['read'].active() == 0
            assert (await request(app, path='/api/item'))[0]['status'] == 200
        finally:
            release.set()
            await asyncio.gather(*readers, return_exceptions=True)
    asyncio.run(run())


def test_restarted_application_refuses_body_from_previous_generation(bounded):
    app, _ = bounded
    async def run():
        receiving, release = asyncio.Event(), asyncio.Event()
        async def old_body():
            receiving.set()
            await release.wait()
            return {'type': 'http.request', 'body': b'{"title":"Old request"}'}
        old = asyncio.create_task(request(app, 'POST', '/api/item', receive=old_body))
        await receiving.wait()
        app.stop()
        await app._work.drain()
        app._work.start()
        release.set()
        assert (await old)[0]['status'] == 503
        assert app.database.count(Item) == 0
        assert (await request(app, 'POST', '/api/item', {'title': 'New request'}))[0]['status'] == 201
    asyncio.run(run())


def test_disabled_stream_shapes_use_normal_dispatch(bounded, secured):
    app, _ = bounded
    for path in ('/api/_events/stream', '/api/_eva/runs/1/stream'):
        assert asyncio.run(request(app, path=path))[0]['status'] == 404
    # An authenticated app still enforces normal authentication on unavailable Eva routes.
    assert asyncio.run(request(secured, path='/api/_eva/runs/1/stream'))[0]['status'] == 401


def test_invalid_health_method_cannot_reserve_probe_while_waiting_for_body(bounded):
    app, runtime = bounded
    async def run():
        read_body = False
        async def stalled():
            nonlocal read_body
            read_body = True
            await asyncio.Future()
        invalid = asyncio.create_task(request(app, 'POST', '/health', receive=stalled))
        try:
            await asyncio.sleep(.01)
            assert (await asyncio.wait_for(request(app), 1))[0]['status'] == 200
            assert (await asyncio.wait_for(invalid, 1))[0]['status'] == 405
            assert not read_body and runtime.lanes['probe'].active() == 0
        finally:
            invalid.cancel()
            await asyncio.gather(invalid, return_exceptions=True)
    asyncio.run(run())


def test_stalled_bodies_do_not_block_writes_to_another_application(tmp_path, http_capacity):
    http_capacity()  # Default write lane: 4 slots shared by every application in the process.
    apps = [Application(Database(tmp_path / f'{name}.sqlite3', Registry(Item))) for name in ('slow', 'other')]
    for app in apps:
        app.initialize()
    slow, other = apps
    async def run():
        stalled = asyncio.Event()
        async def never():
            await stalled.wait()
            return {'type': 'http.disconnect'}
        # Four clients that started a sign-in or create and stopped sending its body.
        held = [asyncio.create_task(request(slow, 'POST', path, receive=never))
                for path in ('/api/auth/login', '/api/item', '/api/item', '/api/item')]
        await asyncio.sleep(.05)
        try:
            assert (await asyncio.wait_for(request(other, 'POST', '/api/item', {'title': 'Other app'}), 1))[0]['status'] == 201
            assert (await asyncio.wait_for(request(slow, 'POST', '/api/item', {'title': 'Same app'}), 1))[0]['status'] == 201
        finally:
            stalled.set()
            await asyncio.gather(*held, return_exceptions=True)
    try:
        asyncio.run(run())
    finally:
        for app in apps:
            app.database.close()
