"""Connector review findings: saved secrets stay with their server, checked addresses, bounded tests, rotation."""

import base64
import socket
import socketserver
import threading
import time

import pytest

from evlet import ConnectionFailed, Connector, ConnectorField
from evlet import connectors as module
from evlet.connectors import SecretKey, rotate, scrub
from test_business import client, hashed
from test_connectors import MAIL_PASSWORD, SECRET, account, app, build, mail_servers, serve


class Capture(socketserver.StreamRequestHandler):
    """An SMTP server that accepts any sign-in and keeps what it was sent."""

    def handle(self):
        write = lambda line: self.wfile.write(line.encode() + b'\r\n')
        write('220 capture ESMTP')
        while raw := self.rfile.readline():
            line = raw.decode().rstrip('\r\n')
            self.server.messages.append(line if not line.upper().startswith('AUTH') else base64.b64decode(line.split(' ')[2]).decode())
            verb = line.split(' ')[0].upper()
            if verb in ('EHLO', 'HELO'):
                write('250-capture')
                write('250 AUTH PLAIN')
            elif verb == 'AUTH':
                write('235 ok')
            elif verb == 'QUIT':
                write('221 bye')
                return
            else:
                write('250 ok')


@pytest.fixture
def capture():
    server = serve(Capture)
    yield server
    server.shutdown()
    server.server_close()


def moved(port, **settings):
    return {'settings': {'address': 'mailbox@example.test', 'host': '127.0.0.1', 'port': port, 'security': 'none', **settings}}


def test_a_saved_mail_password_never_goes_to_another_server(app, mail_servers, capture):
    smtp, imap = (server.server_address[1] for server in mail_servers)
    admin = client(app)
    assert admin('PUT', '/api/_controls/connectors/smtp:main', account(smtp))[0] == 200
    assert admin('PUT', '/api/_controls/connectors/imap:inbox', account(imap))[0] == 200
    again = 'Enter the password again. The server details changed, so the saved one is not used.'
    for row_id, port in (('smtp:main', capture.server_address[1]), ('imap:inbox', capture.server_address[1])):
        status, payload, _ = admin('POST', f'/api/_controls/connectors/{row_id}/test', moved(port))
        assert (status, payload['fields']) == (422, {'password': again})
        status, payload, _ = admin('PUT', f'/api/_controls/connectors/{row_id}', moved(port))
        assert (status, payload['fields']) == (422, {'password': again})
    for change in ({'username': 'someone-else'}, {'address': 'other@example.test'}):
        assert admin('PUT', '/api/_controls/connectors/smtp:main', moved(smtp, **change))[0] == 422
    assert not any(MAIL_PASSWORD in line for line in capture.messages)
    # Settings that don't say where the secret goes keep it; a new value with the new server is fine.
    assert admin('POST', '/api/_controls/connectors/smtp:main/test', moved(smtp))[1]['ok']
    assert admin('PUT', '/api/_controls/connectors/smtp:main', {**moved(capture.server_address[1]), 'secrets': {'password': 'new-one'}})[0] == 200
    assert app.business.connectors.values('smtp:main')['password'] == 'new-one'


def test_an_app_connector_keeps_its_secret_only_with_the_same_gateway(app):
    admin = client(app)
    assert ConnectorField('gateway', 'Gateway URL').identifies and not ConnectorField('mode', 'Mode', 'select', options=('a',)).identifies
    assert ConnectorField('target', 'Target', identifies=True).identifies
    assert admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://wa.example.test'}, 'secrets': {'api_key': SECRET}})[0] == 200
    assert admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'mode': 'test'}})[0] == 200
    status, payload, _ = admin('POST', '/api/_controls/connectors/whatsapp/test', {'settings': {'gateway': 'https://evil.example.test'}})
    assert status == 422 and 'api_key' in payload['fields']
    assert admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://evil.example.test'}})[0] == 422
    assert app.business.connectors.values('whatsapp')['gateway'] == 'https://wa.example.test'


@pytest.mark.parametrize('host', ['169.254.169.254', '0.0.0.0', '224.0.0.1', 'fd00:ec2::254', 'fe80::1', '127.0.0.1'])
def test_mail_connections_refuse_metadata_link_local_and_loopback_addresses(app, host):
    body = {**account(587, security='starttls'), 'settings': {**account(587)['settings'], 'host': host, 'security': 'starttls'}}
    status, result, _ = client(app)('POST', '/api/_controls/connectors/smtp:probe/test', body)
    assert status == 200 and result == {'ok': False, 'error': module.NOT_ALLOWED}
    with pytest.raises(ConnectionFailed, match='not allowed'):
        module.safe_address(host, 587)


def test_mail_ports_are_limited_and_failures_read_the_same(app, monkeypatch, mail_servers):
    monkeypatch.setattr(module, 'TIMEOUT', 1)
    admin = client(app)
    body = {**account(2525), 'settings': {**account(2525)['settings'], 'host': 'mail.example.test', 'security': 'starttls'}}
    status, payload, _ = admin('PUT', '/api/_controls/connectors/smtp:odd', body)
    assert (status, payload['fields']) == (422, {'port': 'Use port 25, 465 or 587.'})
    closed = socket.socket()
    closed.bind(('127.0.0.1', 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    silent = socket.socket()
    silent.bind(('127.0.0.1', 0))
    silent.listen(5)
    try:
        results = [admin('POST', '/api/_controls/connectors/smtp:probe/test', account(port))[1] for port in (closed_port, silent.getsockname()[1])]
    finally:
        silent.close()
    assert results[0] == results[1] == {'ok': False, 'error': module.UNREACHABLE}
    ok = admin('POST', '/api/_controls/connectors/smtp:probe/test', account(mail_servers[0].server_address[1]))[1]
    assert ok == {'ok': True, 'message': 'Connected.'}


def test_connection_tests_run_two_at_a_time_and_are_rate_limited(app, monkeypatch):
    monkeypatch.setattr(module, 'TIMEOUT', 1)
    admin = client(app)
    body = {'settings': {'url': 'https://slow.example.test'}}
    results = []
    threads = [threading.Thread(target=lambda: results.append(admin('POST', '/api/_controls/connectors/slow/test', body)[:2])) for _ in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    assert admin('POST', '/api/_controls/connectors/slow/test', body)[:2] == (429, {'error': module.BUSY})
    for thread in threads:
        thread.join()
    assert [status for status, _ in results] == [200, 200]
    # The abandoned tests still hold their slots until they end, so threads stay bounded.
    assert admin('POST', '/api/_controls/connectors/slow/test', body)[0] == 429
    time.sleep(4)
    monkeypatch.setattr(module, 'TEST_RATE', 5)  # four tests above, one more allowed
    assert admin('POST', '/api/_controls/connectors/whatsapp/test', {'settings': {'gateway': 'g'}, 'secrets': {'api_key': SECRET}})[0] == 200
    assert admin('POST', '/api/_controls/connectors/whatsapp/test', {'settings': {'gateway': 'g'}, 'secrets': {'api_key': SECRET}})[:2] == (
        429, {'error': 'Too many connection tests. Try again in a minute.'})


def test_scrub_removes_quoted_encoded_and_partial_secrets():
    secret = 'abc\\SECRET value+/'
    for leak in (repr(secret), 'abc%5CSECRET%20value%2B%2F', 'abc%5CSECRET+value%2B%2F', secret[:-1], 'abc\\\\SECRET value+/'):
        assert 'SECRET' not in scrub(f'Gateway refused key {leak}.', [secret])


def test_rotation_reports_unreadable_rows_and_finishes_the_rest(app, tmp_path, mail_servers):
    admin = client(app)
    smtp = mail_servers[0].server_address[1]
    for name in ('a', 'b'):
        assert admin('PUT', f'/api/_controls/connectors/smtp:{name}', account(smtp, password=f'password-{name}'))[0] == 200
    with app.database.transaction() as tx:
        blob = tx.connection.execute("SELECT secrets FROM evlet_connectors WHERE id='smtp:a'").fetchone()[0]
        tx.connection.execute("UPDATE evlet_connectors SET secrets=? WHERE id='smtp:b'", (blob,))
    key = SecretKey(tmp_path / 'secret.key')
    assert rotate(app.database.path, key) == (1, ['smtp:b'])
    assert len((tmp_path / 'secret.key').read_text().split()) == 2  # the old key stays for the unreadable row
    assert app.business.connectors.values('smtp:a')['password'] == 'password-a'
    missing = tmp_path / 'none' / 'database.sqlite3'
    with pytest.raises(ValueError, match='no database'):
        rotate(missing, key)
    assert not missing.exists()
