"""Sessions and MFA tickets remain revoked across stale side-file recovery and login races."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from evlet import Database, LocalAuth, Registry, qb
from evlet.auth import AccessError
from evlet import auth as auth_module
from test_business import PASSWORD, client, hashed, http, secured  # noqa: F401
from test_settings_security import settled  # noqa: F401


def account(app, name):
    with app.database._connection() as connection:
        return dict(connection.run(qb.select('evlet_users').where(username=name)).one())


def test_password_login_finishes_while_main_writer_is_held(secured):
    # The editor has no profile yet: deciding whether MFA is needed must not insert one in main.
    with secured.database.transaction() as tx:
        tx.connection.run(qb.delete('evlet_user_profile').where(user_id=account(secured, 'editor')['id']))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with secured.database.transaction() as tx:
            tx.connection.run(qb.update('evlet_users', active=1).where(username='editor'))
            future = pool.submit(http, secured, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})
            # A successful request before this transaction releases proves the main lock is unnecessary.
            answer = future.result(timeout=5)
            assert answer[0] == 200 and 'user' in answer[1]


def test_revoked_cookie_stays_revoked_when_old_session_rows_return(secured):
    person, auth = client(secured, 'editor'), secured.business.auth
    owner = account(secured, 'editor')['id']
    with secured.database._connection() as connection:
        old = dict(connection.run(qb.select('evlet_sessions').where(user_id=owner)).one())
    auth.update_user(owner, {'password': PASSWORD + ' new'})
    assert person('GET', '/api/customer')[0] == 401
    # Model the side file recovering pre-reset content while main retains the committed reset.
    with secured.database.transaction(store='sessions') as tx:
        tx.connection.run(qb.insert('evlet_sessions', **old))
    assert person('GET', '/api/customer')[0] == 401
    assert http(secured, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD + ' new'})[0] == 200


def test_revocation_during_password_hash_cannot_create_valid_session(secured, monkeypatch):
    checked, resume = threading.Event(), threading.Event()
    original = auth_module._scrypt
    def pause(*args):
        result = original(*args)
        checked.set()
        assert resume.wait(5)
        return result
    monkeypatch.setattr(auth_module, '_scrypt', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(http, secured, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})
        try:
            assert checked.wait(5)
            with secured.database.transaction() as tx:
                secured.business.auth.end_sessions(tx.connection, account(secured, 'editor')['id'])
        finally:
            resume.set()
        assert future.result(timeout=5)[0] == 401


def test_restored_pre_revocation_mfa_ticket_is_refused_without_using_code(settled):
    secured = settled
    client(secured)  # Enrol, retaining real backup codes for the next challenge.
    status, pending, _ = http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    assert status == 200 and pending['two_step'] == 'code'
    owner = account(secured, 'admin')['id']
    with secured.database._connection() as connection:
        old = dict(connection.run(qb.select('evlet_two_step_tickets').where(user_id=owner)).one())
    with secured.database.transaction() as tx:
        secured.business.auth.end_sessions(tx.connection, owner)
    with secured.database.transaction(store='sessions') as tx:
        tx.connection.run(qb.insert('evlet_two_step_tickets', **old))
    code = secured._test_codes['admin'].pop()
    assert http(secured, 'POST', '/api/auth/login', {'ticket': pending['ticket'], 'code': code})[0] == 401
    pending = http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})[1]
    assert http(secured, 'POST', '/api/auth/login', {'ticket': pending['ticket'], 'code': code})[0] == 200
    # A new ticket cannot replay the backup code used by the prior successful transaction.
    pending = http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})[1]
    assert http(secured, 'POST', '/api/auth/login', {'ticket': pending['ticket'], 'code': code})[0] == 401


def test_kept_session_receives_new_generation_on_password_change(secured):
    person, other = client(secured, 'editor'), client(secured, 'editor')
    response = person('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': PASSWORD + ' changed'})
    assert response[0] == 200, response[1]
    assert person('GET', '/api/customer')[0] == 200
    assert other('GET', '/api/customer')[0] == 401


def test_generation_upgrade_preserves_existing_sessions_and_then_revokes_them(tmp_path, hashed):
    database = Database(tmp_path / 'old.sqlite3', Registry())
    database.initialize()
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    auth.initialize()
    with database.transaction() as tx:
        user = tx.connection.run(qb.insert('evlet_users', username='editor', password=hashed, role='editor').returning_id()).id
    scope = {'method': 'GET', 'headers': [(b'host', b'127.0.0.1:8000')], 'client': ('127.0.0.1', 1)}
    _, headers = auth.login(scope, {'username': 'editor', 'password': PASSWORD})
    scope['headers'].append((b'cookie', headers[0][1].split(b';')[0]))
    # An older instance has neither column; upgrading adds both with the same initial generation.
    with database.schema_transaction() as connection:
        connection.drop_column('evlet_users', 'session_generation')
    # Schema retirement helpers target app tables in main; construct the old side-file fixture directly.
    with sqlite3.connect(database.files()[1]) as connection:
        connection.execute('ALTER TABLE evlet_sessions DROP COLUMN session_generation')
    auth.initialize()
    assert auth.session(scope)[0].id == user
    with database.transaction() as tx:
        auth.end_sessions(tx.connection, user)
    with pytest.raises(AccessError) as error:
        auth.session(scope)
    assert error.value.status == 401
    database.close()
