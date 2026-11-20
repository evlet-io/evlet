"""Sign-in under load and attack: hashing slots that queue briefly, account-wide slow-down, finished two-step
sign-ins, MCP registration and consent, and bounded HTTP lanes. Real SQLite throughout."""

import asyncio
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

import evlet.auth as auth_module
from evlet import qb
from evlet.auth import AccessError, password_hash
from evlet.settings import totp
from test_business import PASSWORD, client, hashed, http, secured  # noqa: F401
from test_assistants import CALLBACK, allow, authorize_query, connect, pkce, register
from test_settings_security import enrol, settled  # noqa: F401


def attempts(app, key):
    with app.database._connection() as connection:
        return app.business.auth._attempts(connection, key)


# ---------- password hashing slots ----------

def test_a_busy_hashing_slot_is_waited_for_then_refused_when_the_queue_is_full(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(auth_module, '_HASH_SLOTS', slots)
    monkeypatch.setattr(auth_module.hashlib, 'scrypt', lambda *args, **kwargs: b'x' * 64)
    monkeypatch.setattr(auth_module, 'HASH_WAIT', 2.0)
    monkeypatch.setattr(auth_module, 'HASH_QUEUE', 1)
    assert slots.acquire(blocking=False)
    threading.Timer(.2, slots.release).start()
    # The slot frees within the wait: the request is served in turn instead of refused.
    started = time.monotonic()
    assert password_hash(PASSWORD).startswith('scrypt:')
    assert time.monotonic() - started >= .15
    # With the queue full a request is refused at once rather than holding a thread for the wait.
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(auth_module, '_hash_waiting', [1])
    started = time.monotonic()
    with pytest.raises(AccessError) as failure:
        password_hash(PASSWORD)
    assert failure.value.status == 503 and time.monotonic() - started < .5
    slots.release()


def test_password_slots_follow_the_environment_within_bounds(monkeypatch):
    monkeypatch.setenv('EVLET_PASSWORD_SLOTS', '1')
    assert auth_module._password_slots() == 1
    for value in ('0', '9', 'two'):
        monkeypatch.setenv('EVLET_PASSWORD_SLOTS', value)
        with pytest.raises(ValueError):
            auth_module._password_slots()
    monkeypatch.delenv('EVLET_PASSWORD_SLOTS')
    assert 1 <= auth_module._password_slots() <= 2


# ---------- failures per account, from every network ----------

def test_many_failures_for_one_account_slow_new_networks_but_not_known_ones(secured, monkeypatch):
    auth = secured.business.auth
    monkeypatch.setattr(auth_module, 'ACCOUNT_FAILURES', 3)
    # The editor signed in from their office before: that network is remembered for the account.
    office = {'client': ('198.51.100.7', 1), 'headers': []}
    assert auth.login(office, {'username': 'editor', 'password': PASSWORD})[0]['user']['username'] == 'editor'
    # Spread-out guessing: each network stays far below its own limits.
    for number in range(3):
        with pytest.raises(AccessError) as failure:
            auth.login({'client': (f'203.0.113.{number}', 1), 'headers': []}, {'username': 'editor', 'password': 'wrong'})
        assert failure.value.status == 401
    # Past the account limit a new network gets one attempt, and the next one within the spacing is turned away.
    with pytest.raises(AccessError) as failure:
        auth.login({'client': ('203.0.113.50', 1), 'headers': []}, {'username': 'editor', 'password': 'wrong'})
    assert failure.value.status == 401
    with pytest.raises(AccessError) as failure:
        auth.login({'client': ('203.0.113.51', 1), 'headers': []}, {'username': 'editor', 'password': PASSWORD})
    assert failure.value.status == 429 and 'many failed sign-ins' in failure.value.message
    # The refused attempt still cost its network one, and nothing for the account.
    network, pair, account = auth._limit_keys('203.0.113.51', 'editor')[:3]
    assert attempts(secured, network) == 1 and attempts(secured, pair) == 0 and attempts(secured, account) == 4
    # The person at their office is not held up, and their success does not add to the account's failures.
    assert auth.login(office, {'username': 'editor', 'password': PASSWORD})[0]['user']['username'] == 'editor'
    assert attempts(secured, account) == 4
    # Another account is unaffected.
    assert auth.login({'client': ('203.0.113.52', 1), 'headers': []}, {'username': 'viewer', 'password': PASSWORD})[0]
    # Once the spacing has passed, a new network may try again.
    with secured.database.transaction(store='sessions') as tx:
        tx.connection.run(qb.delete('evlet_login_limits').where(client=auth._limit_keys('x', 'editor')[4]))
    assert auth.login({'client': ('203.0.113.53', 1), 'headers': []}, {'username': 'editor', 'password': PASSWORD})[0]


def test_a_finished_two_step_sign_in_gives_the_network_its_attempt_back(settled):
    app = settled
    _, raw, _ = enrol(app, 'editor')
    network, pair, account, known, _ = app.business.auth._limit_keys('192.0.2.30', 'editor')
    data = http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD}, client='192.0.2.30')[1]
    # The password was right but the sign-in is not finished: the attempt still counts.
    assert attempts(app, network) == 1 and attempts(app, pair) == 1 and attempts(app, account) == 1
    status, _, _ = http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': totp(raw, int(time.time() // 30) + 1)}, client='192.0.2.30')
    assert status == 200
    assert attempts(app, network) == 0 and attempts(app, pair) == 0 and attempts(app, account) == 0 and attempts(app, known) == 1


# ---------- MCP registration and consent ----------

def test_a_full_registration_table_makes_room_from_stale_unconnected_clients(secured, monkeypatch):
    import evlet.assistants
    monkeypatch.setattr(evlet.assistants, 'MAX_CLIENTS', 2)
    connect(secured, 'editor')
    assert register(secured)[0] == 201
    # Both fresh: nothing may be removed yet.
    status, data, _ = register(secured)
    assert status == 503 and data['error'] == 'temporarily_unavailable'
    with secured.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_oauth_clients SET created_at=created_at-3600')
    # The stale, never connected registration makes way; the connected one stays.
    status, fresh, _ = register(secured)
    assert status == 201
    with secured.database._connection() as connection:
        ids = {row[0] for row in connection.execute('SELECT id FROM evlet_oauth_clients')}
        connected = {row[0] for row in connection.execute('SELECT oauth_client FROM evlet_mcp_clients')}
    assert len(ids) == 2 and connected <= ids and fresh['client_id'] in ids
    assert register(secured)[0] == 503


def test_cancelling_for_a_client_that_never_connected_stays_on_the_workspace(secured):
    allow(secured)
    _, registered, _ = register(secured, uris=('https://evil.example/x',))
    person = client(secured, 'editor')
    query = authorize_query(registered['client_id'], pkce()[1], redirect='https://evil.example/x')
    assert person('POST', '/api/_oauth/authorize', {'query': query, 'approve': False})[1] == {'redirect': '/'}
    # A client that has connected before goes back, as OAuth expects.
    client_id, _, person = connect(secured, 'editor')
    query = authorize_query(client_id, pkce()[1])
    denied = person('POST', '/api/_oauth/authorize', {'query': query, 'approve': False})[1]['redirect']
    assert denied.startswith(CALLBACK + '?') and parse_qs(urlsplit(denied).query)['error'] == ['access_denied']
    # Someone who may not use assistants is never sent anywhere else.
    allow(secured, False)
    assert person('POST', '/api/_oauth/authorize', {'query': query, 'approve': False})[1] == {'redirect': '/'}


# ---------- HTTP lanes ----------

def test_lane_defaults_follow_the_cpu_count():
    from evlet.runtime import HTTPRuntime
    small, large = HTTPRuntime.defaults(1), HTTPRuntime.defaults(16)
    assert (small['write'], small['heavy'], small['init']) == (2, 1, 2)
    assert large == HTTPRuntime.DEFAULTS


def test_a_full_lane_queue_refuses_at_once(monkeypatch):
    from evlet import runtime as module
    monkeypatch.setenv('EVLET_HTTP_QUEUE', '1')
    pool = module.HTTPRuntime({'write': 1})
    lane = pool.lanes['write']
    assert lane.queue == 1

    async def run():
        held = await lane.acquire(5)
        waiter = asyncio.create_task(lane.acquire(5))
        while lane.waiting() != 1:
            await asyncio.sleep(.01)
        started = time.monotonic()
        with pytest.raises(module.Busy):
            await lane.acquire(5)
        assert time.monotonic() - started < .5
        held.release()
        (await waiter).release()
        assert lane.active() == 0 and lane.waiting() == 0
    try:
        asyncio.run(run())
    finally:
        pool.close()
    for value in ('-1', '65', 'x'):
        monkeypatch.setenv('EVLET_HTTP_QUEUE', value)
        with pytest.raises(ValueError):
            module.HTTPRuntime()
