"""Connectors: declarations, the secret key, encrypted secrets, tests with a time limit, and email accounts.

Mail tests talk only to fake SMTP and IMAP servers started here on loopback.
"""

import base64
import json
import logging
import os
import socket
import socketserver
import stat
import threading
import time

import pytest

from evlet import Application, ConnectionFailed, Connector, ConnectorField, Database, LocalAuth
from evlet import connectors as module
from evlet.secrets import SecretKey, SecretKeyError, rotate
from examples.playground.models import registry
from test_business import PASSWORD, client, hashed
from test_cli_lifecycle import environment_stub, project

SECRET = 'Sup3r-Secret-Value-7d1f'
MAIL_PASSWORD = 'mailbox-app-password-91c2'


def check_whatsapp(values):
    if values['api_key'] != SECRET:
        raise ConnectionFailed('The gateway rejected the API key.')


WHATSAPP = Connector('whatsapp', title='WhatsApp', category='Messaging', test=check_whatsapp, fields=(
    ConnectorField('gateway', 'Gateway URL'), ConnectorField('api_key', 'API key', 'secret'),
    ConnectorField('lead', 'Create a Lead for new numbers', 'toggle', default=True, required=False),
    ConnectorField('mode', 'Mode', 'select', options=('live', 'test'), default='live'),
    ConnectorField('role', 'Role for new users', 'role', default='viewer'),
    ConnectorField('limit', 'Messages per day', 'number', default=500)))
SLOW = Connector('slow', title='Slow service', fields=(ConnectorField('url', 'URL'),), test=lambda values: time.sleep(5))


def build(path, hashed, **options):
    database = Database(path / 'connectors.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    app = Application(database, auth=auth, connectors=(WHATSAPP, SLOW), **options)
    app.initialize()
    with database.transaction() as tx:
        for role in ('admin', 'editor', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password', (role, hashed, role))
    return app


@pytest.fixture
def app(tmp_path, hashed, monkeypatch):
    monkeypatch.delenv('EVLET_SECRET_KEY', raising=False)
    return build(tmp_path, hashed)


# ---------- fake mail servers ----------

class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        write = lambda line: self.wfile.write(line.encode() + b'\r\n')
        write('220 fake.test ESMTP')
        data, lines = False, []
        while raw := self.rfile.readline():
            line = raw.decode().rstrip('\r\n')
            if data:
                if line == '.':
                    data = False
                    self.server.messages.append('\n'.join(lines))
                    write('250 queued')
                else:
                    lines.append(line)
                continue
            verb = line.split(' ')[0].upper()
            if verb in ('EHLO', 'HELO'):
                write('250-fake.test')
                write('250 AUTH PLAIN')
            elif verb == 'AUTH':
                _, user, password = base64.b64decode(line.split(' ')[2]).split(b'\0')
                write('235 ok' if (user.decode(), password.decode()) == ('mailbox@example.test', MAIL_PASSWORD) else '535 authentication failed')
            elif verb in ('MAIL', 'RCPT', 'RSET', 'NOOP'):
                write('250 ok')
            elif verb == 'DATA':
                data, lines = True, []
                write('354 go ahead')
            elif verb == 'QUIT':
                write('221 bye')
                return
            else:
                write('502 not implemented')


class _IMAPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        write = lambda line: self.wfile.write(line.encode() + b'\r\n')
        write('* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] fake ready')
        while raw := self.rfile.readline():
            tag, _, rest = raw.decode().rstrip('\r\n').partition(' ')
            command, _, arguments = rest.partition(' ')
            command = command.upper()
            if command == 'CAPABILITY':
                write('* CAPABILITY IMAP4rev1 AUTH=PLAIN')
                write(f'{tag} OK done')
            elif command == 'LOGIN':
                user, _, password = arguments.partition(' ')
                ok = (user.strip('"'), password.strip('"')) == ('mailbox@example.test', MAIL_PASSWORD)
                write(f'{tag} OK logged in' if ok else f'{tag} NO [AUTHENTICATIONFAILED] Invalid credentials')
            elif command == 'EXAMINE':
                write('* 0 EXISTS')
                write(f'{tag} OK [READ-ONLY] done')
            elif command == 'LOGOUT':
                write('* BYE')
                write(f'{tag} OK bye')
                return
            else:
                write(f'{tag} BAD unknown')


def serve(handler):
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler)
    server.daemon_threads = True
    server.messages = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def mail_servers():
    servers = serve(_SMTPHandler), serve(_IMAPHandler)
    yield servers
    for server in servers:
        server.shutdown()
        server.server_close()


def account(port, password=MAIL_PASSWORD, **settings):
    return {'settings': {'address': 'mailbox@example.test', 'host': '127.0.0.1', 'port': port, 'security': 'none', **settings},
            'secrets': {'password': password}}


# ---------- the secret key ----------

def test_a_missing_key_file_is_created_0600_on_first_use(tmp_path, monkeypatch):
    monkeypatch.delenv('EVLET_SECRET_KEY', raising=False)
    key = SecretKey(tmp_path / 'secret.key')
    key.check()
    assert not (tmp_path / 'secret.key').exists()
    blob = key.encrypt(b'value', b'context')
    assert stat.S_IMODE(os.stat(tmp_path / 'secret.key').st_mode) == 0o600
    assert key.decrypt(blob, b'context') == b'value' and b'value' not in blob


@pytest.mark.parametrize('mode', [0o640, 0o604, 0o644])
def test_looser_key_file_permissions_refuse_to_start(tmp_path, hashed, monkeypatch, mode):
    monkeypatch.delenv('EVLET_SECRET_KEY', raising=False)
    SecretKey(tmp_path / 'secret.key').encrypt(b'x', b'y')
    os.chmod(tmp_path / 'secret.key', mode)
    with pytest.raises(SecretKeyError, match='chmod 600'):
        build(tmp_path, hashed, secret_key=tmp_path / 'secret.key')
    os.chmod(tmp_path / 'secret.key', 0o600)
    (tmp_path / 'link.key').symlink_to(tmp_path / 'secret.key')
    with pytest.raises(SecretKeyError, match='not a link'):
        SecretKey(tmp_path / 'link.key').check()


def test_the_environment_key_wins_and_must_be_valid(tmp_path, monkeypatch):
    monkeypatch.setenv('EVLET_SECRET_KEY', base64.urlsafe_b64encode(os.urandom(32)).decode())
    key = SecretKey(tmp_path / 'secret.key')
    assert key.decrypt(key.encrypt(b'value', b'c'), b'c') == b'value'
    assert not (tmp_path / 'secret.key').exists()
    monkeypatch.setenv('EVLET_SECRET_KEY', 'too-short')
    with pytest.raises(SecretKeyError, match='32-byte keys'):
        key.check()


def test_tampered_or_moved_ciphertext_is_detected(app):
    admin = client(app)
    for name in ('whatsapp',):
        assert admin('PUT', f'/api/_controls/connectors/{name}', {'settings': {'gateway': 'https://wa.example.test'}, 'secrets': {'api_key': SECRET}})[0] == 200
    connectors = app.business.connectors
    assert connectors.values('whatsapp')['api_key'] == SECRET
    with app.database.transaction() as tx:
        blob = bytearray(tx.connection.execute("SELECT secrets FROM evlet_connectors WHERE id='whatsapp'").fetchone()[0])
        blob[-1] ^= 1
        tx.connection.execute("UPDATE evlet_connectors SET secrets=? WHERE id='whatsapp'", (bytes(blob),))
    with pytest.raises(SecretKeyError, match='altered'):
        connectors.values('whatsapp')
    status, result, _ = admin('POST', '/api/_controls/connectors/whatsapp/test', {})
    assert status == 200 and result == {'ok': False, 'error': module.UNREADABLE}
    # A ciphertext copied to another row is refused too: each row is bound to its id.
    key = connectors.key
    with pytest.raises(SecretKeyError):
        key.decrypt(key.encrypt(b'{}', module.row_context('smtp:a')), module.row_context('smtp:b'))
    # Keeping an unreadable secret is refused; replacing it works.
    status, payload, _ = admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://wa.example.test'}})
    assert status == 422 and payload['fields']['api_key'] == module.UNREADABLE
    assert admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://wa.example.test'}, 'secrets': {'api_key': SECRET}})[0] == 200
    assert connectors.values('whatsapp')['api_key'] == SECRET


def test_rotation_re_encrypts_every_secret_under_a_new_key(app, tmp_path, mail_servers):
    admin = client(app)
    admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://wa.example.test'}, 'secrets': {'api_key': SECRET}})
    admin('PUT', '/api/_controls/connectors/smtp:notifications', account(mail_servers[0].server_address[1]))
    key_file = tmp_path / 'secret.key'
    before = key_file.read_text()
    assert rotate(app.database.path, SecretKey(key_file)) == (2, [])
    after = key_file.read_text()
    assert after != before and len(after.split()) == 1 and stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    assert app.business.connectors.values('whatsapp')['api_key'] == SECRET
    assert app.business.connectors.values('smtp:notifications')['password'] == MAIL_PASSWORD


# ---------- declarations and saving ----------

def test_declarations_are_checked(tmp_path):
    with pytest.raises(ValueError, match='field type'):
        ConnectorField('token', 'Token', 'password')
    with pytest.raises(ValueError, match='Select fields need options'):
        ConnectorField('mode', 'Mode', 'select')
    with pytest.raises(ValueError, match='lowercase'):
        Connector('WhatsApp', title='WhatsApp')
    with pytest.raises(ValueError, match='unique names'):
        Connector('dup', title='Dup', fields=(ConnectorField('a', 'A'), ConnectorField('a', 'A again')))
    with pytest.raises(ValueError, match='declared twice'):
        database = Database(tmp_path / 'x.sqlite3', registry)
        Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False), connectors=(WHATSAPP, WHATSAPP))


def test_settings_are_validated_by_type_and_secrets_need_a_new_value(app):
    admin = client(app)
    status, payload, _ = admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': '', 'mode': 'beta', 'role': 'admin', 'limit': '5', 'lead': 'yes'}})
    assert status == 422 and payload['fields'] == {
        'gateway': 'Fill in gateway url.', 'api_key': 'Enter the api key.', 'mode': 'Choose one of the options.',
        'role': 'Choose an existing desk role other than admin.', 'limit': 'Use a whole number.', 'lead': 'Use true or false.'}
    assert admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://wa.example.test'}, 'secrets': {'api_key': SECRET}})[0] == 200
    status, payload, _ = admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {}, 'secrets': {'api_key': '  '}})
    assert status == 422 and payload['fields']['api_key'] == 'Paste a new value, or leave it out to keep the saved one.'
    status, item, _ = admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'mode': 'test'}})
    assert status == 200 and item['settings']['mode'] == 'test' and item['secrets'] == {'api_key': {'saved': True}}
    assert app.business.connectors.values('whatsapp')['api_key'] == SECRET
    assert admin('GET', '/api/_controls/connectors/nothing')[0] == 404
    assert admin('PUT', '/api/_controls/connectors/whatsapp:extra', {})[0] == 404
    assert admin('PUT', '/api/_controls/connectors/smtp', {})[0] == 404
    for role in ('editor', 'viewer'):
        assert client(app, role)('GET', '/api/_controls/connectors')[:2] == (404, {'error': 'Route not found'})


def test_secrets_never_appear_in_responses_logs_or_audit_rows(app, mail_servers, caplog):
    caplog.set_level(logging.DEBUG)
    admin = client(app)
    smtp = mail_servers[0].server_address[1]
    responses = [
        admin('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://wa.example.test'}, 'secrets': {'api_key': SECRET}}),
        admin('PUT', '/api/_controls/connectors/smtp:notifications', account(smtp)),
        admin('PUT', '/api/_controls/connectors/smtp:notifications', account(smtp, password=MAIL_PASSWORD)),
        admin('POST', '/api/_controls/connectors/whatsapp/test', {}),
        admin('POST', '/api/_controls/connectors/whatsapp/test', {'settings': {}, 'secrets': {'api_key': SECRET + 'x'}}),
        admin('POST', '/api/_controls/connectors/smtp:notifications/test', {}),
        admin('POST', '/api/_controls/connectors/smtp:notifications/test', {'to': 'someone@example.test'}),
        admin('GET', '/api/_controls/connectors'), admin('GET', '/api/_controls/connectors/whatsapp'),
        admin('GET', '/api/_controls/connectors/smtp:notifications'), admin('GET', '/api/_controls/state'),
        admin('GET', '/api/_controls/overrides'),
    ]
    assert [status for status, _, _ in responses] == [200] * len(responses)
    assert responses[3][1]['ok'] and responses[5][1]['ok'] and responses[6][1] == {'ok': True, 'message': 'Sent. Check the inbox in a minute.'}
    assert responses[4][1] == {'ok': False, 'error': 'The gateway rejected the API key.'}
    assert responses[1][1]['secrets'] == {'password': {'saved': True}}
    with app.database._connection() as connection:
        audit = json.dumps([dict(row) for row in connection.execute('SELECT * FROM evlet_audit')])
        stored = [bytes(row[0]) for row in connection.execute('SELECT secrets FROM evlet_connectors')]
    text = json.dumps([payload for _, payload, _ in responses]) + audit + caplog.text
    for secret in (SECRET, MAIL_PASSWORD):
        assert secret not in text
        assert all(secret.encode() not in blob for blob in stored)
    assert 'Set up' in audit and 'Password replaced' in audit


def test_a_connector_test_that_hangs_ends_cleanly_at_the_time_limit(app, monkeypatch):
    assert module.TIMEOUT == 10
    monkeypatch.setattr(module, 'TIMEOUT', 1)
    admin = client(app)
    started = time.monotonic()
    status, result, _ = admin('POST', '/api/_controls/connectors/slow/test', {'settings': {'url': 'https://slow.example.test'}})
    assert time.monotonic() - started < 3
    assert status == 200 and result == {'ok': False, 'error': module.UNREACHABLE}


def test_a_mail_server_that_never_answers_times_out(app, monkeypatch):
    monkeypatch.setattr(module, 'TIMEOUT', 1)
    silent = socket.socket()
    silent.bind(('127.0.0.1', 0))
    silent.listen(5)
    try:
        started = time.monotonic()
        status, result, _ = client(app)('POST', '/api/_controls/connectors/smtp:silent/test', account(silent.getsockname()[1]))
        assert time.monotonic() - started < 3
        assert status == 200 and result == {'ok': False, 'error': module.UNREACHABLE}
    finally:
        silent.close()


# ---------- email accounts ----------

def test_smtp_and_imap_accounts_against_local_servers(app, mail_servers):
    smtp, imap = (server.server_address[1] for server in mail_servers)
    admin = client(app)
    assert admin('POST', '/api/_controls/connectors/smtp:wrong/test', account(smtp, password='nope'))[1] == {
        'ok': False, 'error': 'The server rejected the username or password.'}
    assert admin('POST', '/api/_controls/connectors/imap:wrong/test', account(imap, password='nope'))[1] == {
        'ok': False, 'error': 'The server rejected the username or password.'}
    assert admin('POST', '/api/_controls/connectors/imap:support/test', account(imap))[1]['ok']
    status, payload, _ = admin('PUT', '/api/_controls/connectors/smtp:remote', {**account(smtp), 'settings': {**account(smtp)['settings'], 'host': 'mail.example.test'}})
    assert status == 422 and 'must be on this machine' in payload['fields']['security']
    status, payload, _ = admin('PUT', '/api/_controls/connectors/smtp:bad', {**account(smtp), 'settings': {**account(smtp)['settings'], 'address': 'not-an-address'}})
    assert status == 422 and payload['fields'] == {'address': 'Enter a valid email address.'}
    first = admin('PUT', '/api/_controls/connectors/smtp:notifications', account(smtp))[1]
    second = admin('PUT', '/api/_controls/connectors/smtp:accounts', account(smtp))[1]
    assert admin('PUT', '/api/_controls/connectors/imap:support', account(imap))[1]['default'] is False
    assert first['default'] and not second['default']
    connectors = app.business.connectors
    connectors.send_mail('customer@example.test', 'Invoice INV-1', 'Your invoice is attached.', account='accounts')
    connectors.send_mail(['a@example.test', 'b@example.test'], 'Welcome', 'Hello.')
    assert len(mail_servers[0].messages) == 2 and 'Subject: Invoice INV-1' in mail_servers[0].messages[0]
    assert 'To: a@example.test, b@example.test' in mail_servers[0].messages[1]
    with pytest.raises(ConnectionFailed, match='no outgoing email account named sales'):
        connectors.send_mail('a@example.test', 'Hi', 'Hello.', account='sales')
    with pytest.raises(ValueError):
        connectors.send_mail('a@example.test\nBcc: x@example.test', 'Hi', 'Hello.')
    assert admin('DELETE', '/api/_controls/connectors/smtp:notifications')[:2] == (409, {'error': 'This is the default outgoing account. Make another one the default first.'})
    assert admin('POST', '/api/_controls/connectors/smtp:accounts/default')[1]['default']
    assert admin('DELETE', '/api/_controls/connectors/smtp:notifications')[0] == 204
    assert admin('POST', '/api/_controls/connectors/imap:support/default')[0] == 404
    with app.database._connection() as connection:
        details = [tuple(row) for row in connection.execute("SELECT target, detail FROM evlet_audit WHERE area='Email' ORDER BY id")]
    assert details == [('notifications', 'Set up'), ('accounts', 'Set up'), ('support', 'Set up'),
                       ('accounts', 'Made the default outgoing account'), ('notifications', 'Removed')]


def test_no_secret_key_means_no_saved_secrets(tmp_path, hashed, monkeypatch):
    monkeypatch.delenv('EVLET_SECRET_KEY', raising=False)
    app = build(tmp_path, hashed)
    app.business.connectors.key = SecretKey(None)
    status, payload, _ = client(app)('PUT', '/api/_controls/connectors/whatsapp', {'settings': {'gateway': 'https://wa.example.test'}, 'secrets': {'api_key': SECRET}})
    assert status == 503 and 'No secret key is set up' in payload['error']


def test_an_instance_keeps_its_key_in_the_instance_folder_and_rotates_it(project, monkeypatch, capsys):
    from evlet import AppDefinition, User
    from evlet.cli.install import install
    from evlet.cli.main import main
    from test_cli_lifecycle import runtime
    monkeypatch.delenv('EVLET_SECRET_KEY', raising=False)
    with pytest.raises(ValueError, match='Register Connector definitions'):
        AppDefinition(connectors=('whatsapp',))
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    try:
        assert app.business.connectors.key.path == project / 'instances/acme/secret.key'
        app.business.connectors.save(User(1, 'owner', 'admin'), 'imap:support', account(993))
        key_file = project / 'instances/acme/secret.key'
        assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
        before = key_file.read_text()
        main(['secrets', 'rotate', 'acme', '--project', str(project)])
        assert 'Re-encrypted 1 saved secret.' in capsys.readouterr().out
        assert key_file.read_text() != before
        assert app.business.connectors.values('imap:support')['password'] == MAIL_PASSWORD
    finally:
        app.database.close()
    os.chmod(key_file, 0o644)
    with pytest.raises(SecretKeyError, match='chmod 600'):
        runtime(project)
