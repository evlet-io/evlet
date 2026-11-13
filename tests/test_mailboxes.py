"""Personal mailboxes: the workspace switch, OAuth with PKCE, app passwords, the sync job and privacy.

Everything talks to fake IMAP, SMTP and OAuth servers started here on loopback.
"""

import base64
import hashlib
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
import socket
import socketserver
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from evlet import Application, Database, Email, Integer, JobQueue, Journal, LocalAuth, Mail, Model, PostgreSQLDatabase, Registry, Rule, Text
from evlet import connectors as connectors_module
from evlet import secrets as vault
from evlet.auth import AccessError, User
from evlet import mailboxes as module
from test_assistants import send
from test_business import PASSWORD, client, http
from test_postgres import pg_dsn  # noqa: F401 - fixture

ALL = Rule(('read', 'create', 'update', 'delete'))
APP_PASSWORD = 'imap-app-password-55e1'
OWN = lambda user: {'owner': user.id}  # noqa: E731


class Customer(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
    title = Text(default='')
    email = Email()

    class Meta:
        journal = Journal()
        mail = Mail(match=('email',), kind='customer')


class Contact(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read', 'create', 'update'), scope=OWN), 'viewer': Rule(('read',), scope=OWN)}
    title = Text(default='')
    email = Email()
    owner = Integer(default=0)

    class Meta:
        journal = Journal()
        mail = Mail(match=('email',), kind='contact')


class Lead(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',), read_fields=('title',))}
    title = Text(default='')
    email = Email()

    class Meta:
        journal = Journal()
        mail = Mail(match='email', kind='lead')


class Supplier(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    title = Text(default='')
    email = Email()

    class Meta:
        journal = Journal()
        mail = Mail(match=('email',))


class Partner(Model):
    """Has an Email field but doesn't opt in, so mail never matches it."""
    permissions = {'admin': ALL, 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    email = Email()


class Deal(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
    title = Text(default='')

    class Meta:
        journal = Journal()  # Emails sent from a deal are kept in its Activity.


MODELS = (Customer, Contact, Lead, Supplier, Partner, Deal)


# ---------- fake servers ----------

def message(uid, sender, subject, *, to='editor@acme.test', body='Hello', reply_to=None):
    item = EmailMessage()
    item['From'], item['To'], item['Subject'], item['Message-ID'] = sender, to, subject, f'<m{uid}@mail.test>'
    if reply_to:
        item['In-Reply-To'] = reply_to
    item.set_content(body)
    return uid, bytes(item)


class _IMAP(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        write = lambda line: self.wfile.write((line if isinstance(line, bytes) else line.encode()) + b'\r\n')
        write('* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN AUTH=XOAUTH2] fake ready')
        while raw := self.rfile.readline():
            tag, _, rest = raw.decode().rstrip('\r\n').partition(' ')
            command, _, arguments = rest.partition(' ')
            command = command.upper()
            server.log.append(rest)
            if command == 'CAPABILITY':
                write('* CAPABILITY IMAP4rev1 AUTH=PLAIN AUTH=XOAUTH2')
                write(f'{tag} OK done')
            elif command == 'LOGIN':
                user, _, password = arguments.partition(' ')
                ok = (user.strip('"'), password.strip('"')) in server.logins
                write(f'{tag} OK logged in' if ok else f'{tag} NO [AUTHENTICATIONFAILED] Invalid credentials')
            elif command == 'AUTHENTICATE':
                write('+ ')
                sent = base64.b64decode(self.rfile.readline().strip()).decode()
                token = re.fullmatch(r'user=([^\x01]+)\x01auth=Bearer ([^\x01]+)\x01\x01', sent)
                write(f'{tag} OK signed in' if token and token[2] in server.tokens else f'{tag} NO [AUTHENTICATIONFAILED] Invalid token')
            elif command == 'EXAMINE':
                uids = [uid for uid, _ in server.mail]
                write(f'* {len(uids)} EXISTS')
                write(f'* OK [UIDVALIDITY {server.validity}] ok')
                write(f'* OK [UIDNEXT {max(uids, default=0) + 1}] ok')
                write(f'{tag} OK [READ-ONLY] done')
            elif command == 'UID':
                verb, _, rest = arguments.partition(' ')
                if verb.upper() == 'SEARCH':
                    uids = [uid for uid, _ in server.mail]
                    if rest.upper().startswith('UID '):
                        start = int(rest.split(' ')[1].split(':')[0])
                        uids = [uid for uid in uids if uid >= start] or uids[-1:]
                    write('* SEARCH ' + ' '.join(map(str, uids)))
                    write(f'{tag} OK done')
                else:
                    wanted, _, parts = rest.partition(' ')
                    for sequence, (uid, raw_message) in enumerate(server.mail, 1):
                        if str(uid) not in wanted.split(','):
                            continue
                        if 'HEADER.FIELDS' in parts:
                            head = raw_message.split(b'\n\n', 1)[0].replace(b'\r\n', b'\n').split(b'\n')
                            data = b'\r\n'.join(line for line in head if line.split(b':')[0].upper() in (b'FROM', b'TO', b'CC', b'SUBJECT', b'MESSAGE-ID', b'IN-REPLY-TO', b'REFERENCES')) + b'\r\n\r\n'
                            meta = f'* {sequence} FETCH (UID {uid} RFC822.SIZE {len(raw_message)} BODY[HEADER.FIELDS (FROM)] {{{len(data)}}}'
                        else:
                            server.bodies.append(uid)
                            data = raw_message
                            meta = f'* {sequence} FETCH (UID {uid} BODY[] {{{len(data)}}}'
                        write(meta)
                        self.wfile.write(data)
                        write(')')
                    write(f'{tag} OK done')
            elif command == 'LOGOUT':
                write('* BYE')
                write(f'{tag} OK bye')
                return
            else:
                write(f'{tag} BAD unknown')


class _SMTP(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        write = lambda line: self.wfile.write(line.encode() + b'\r\n')
        write('220 fake.test ESMTP')
        data, lines, who = False, [], None
        while raw := self.rfile.readline():
            line = raw.decode().rstrip('\r\n')
            if data:
                if line == '.':
                    data = False
                    server.messages.append((who, '\n'.join(lines)))
                    write('250 queued')
                else:
                    lines.append(line)
                continue
            verb = line.split(' ')[0].upper()
            if verb in ('EHLO', 'HELO'):
                write('250-fake.test')
                write('250 AUTH PLAIN XOAUTH2')
            elif verb == 'AUTH':
                mechanism, value = line.split(' ')[1:3]
                decoded = base64.b64decode(value).decode()
                if mechanism.upper() == 'PLAIN':
                    _, user, password = decoded.split('\0')
                    ok = (user, password) in server.logins
                else:
                    token = re.fullmatch(r'user=([^\x01]+)\x01auth=Bearer ([^\x01]+)\x01\x01', decoded)
                    user, ok = token and token[1], bool(token and token[2] in server.tokens)
                who = user if ok else None
                write('235 ok' if ok else '535 authentication failed')
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


class _Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        form = {key: values[0] for key, values in parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()).items()}
        server.requests.append(form)
        reply = lambda status, body: (self.send_response(status), self.send_header('Content-Type', 'application/json'), self.end_headers(),
                                      self.wfile.write(json.dumps(body).encode()))
        if self.path == '/revoke':
            server.revoked.add(form.get('token'))
            return reply(200, {})
        if server.client_error:
            return reply(401, {'error': server.client_error})
        if (form.get('client_id'), form.get('client_secret')) != ('client-1', 'provider-secret-3a9'):
            return reply(401, {'error': 'invalid_client'})
        if form.get('grant_type') == 'authorization_code':
            code = server.codes.pop(form.get('code'), None)
            verifier = form.get('code_verifier', '')
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            if code is None or code['redirect_uri'] != form.get('redirect_uri') or code['challenge'] != expected:
                return reply(400, {'error': 'invalid_grant'})
        elif form.get('grant_type') == 'refresh_token':
            if form.get('refresh_token') not in server.refresh or form['refresh_token'] in server.revoked:
                return reply(400, {'error': 'invalid_grant'})
        else:
            return reply(400, {'error': 'unsupported_grant_type'})
        server.issued += 1
        access, refresh = f'access-{server.issued}', f'refresh-{server.issued}'
        server.tokens.add(access)
        server.refresh.add(refresh)
        claims = base64.urlsafe_b64encode(json.dumps({'email': 'editor@acme.test', 'aud': 'client-1'}).encode()).rstrip(b'=').decode()
        reply(200, {'access_token': access, 'refresh_token': refresh, 'expires_in': server.expires_in, 'token_type': 'Bearer',
                    'id_token': f'e30.{claims}.sig'})


def serve(handler, threading_server=socketserver.ThreadingTCPServer):
    server = threading_server(('127.0.0.1', 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def mail():
    tokens = set()
    imap, smtp = serve(_IMAP), serve(_SMTP)
    provider = serve(_Provider, ThreadingHTTPServer)
    for server in (imap, smtp):
        server.logins, server.tokens = {('editor@acme.test', APP_PASSWORD), ('viewer@acme.test', APP_PASSWORD)}, tokens
    imap.mail, imap.log, imap.bodies, imap.validity, smtp.messages = [], [], [], 7, []
    provider.tokens, provider.codes, provider.refresh, provider.revoked, provider.requests = tokens, {}, set(), set(), []
    provider.issued, provider.expires_in, provider.client_error = 0, 3600, None
    yield imap, smtp, provider
    for server in (imap, smtp, provider):
        server.shutdown()
        server.server_close()


# ---------- the application ----------

def build(database):
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      jobs=JobQueue(database, []))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    admin = client(app)
    for role in ('editor', 'viewer'):
        assert admin('POST', '/api/auth/users', {'username': role, 'password': PASSWORD, 'role': role})[0] == 201
    with database._connection() as connection:
        app.ids = {row[1]: row[0] for row in connection.execute('SELECT id, username FROM evlet_users')}
    with database.transaction() as tx:
        app.records = {
            'customer': tx.create(Customer, title='Acme', email='Buyer@Acme.test').id,
            'mine': tx.create(Contact, title='Mina', email='mina@acme.test', owner=app.ids['editor']).id,
            'other': tx.create(Contact, title='Omar', email='omar@acme.test', owner=app.ids['admin']).id,
            'lead': tx.create(Lead, title='Lina', email='lina@lead.test').id,
            'supplier': tx.create(Supplier, title='Parts', email='sales@parts.test').id,
            'deal': tx.create(Deal, title='Big order').id,
            'partner': tx.create(Partner, email='hello@partner.test').id,
        }
    return app


@pytest.fixture(autouse=True)
def stub_mail_servers(monkeypatch):
    """The stub mail servers listen on this machine, which personal mailboxes may not reach outside the tests."""
    monkeypatch.setattr(module, 'PUBLIC_SERVERS_ONLY', False)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('EVLET_SECRET_KEY', base64.urlsafe_b64encode(b'k' * 32).decode())
    return build(Database(tmp_path / 'mail.sqlite3', Registry(*MODELS)))


def switch(app, on):
    body = {'changes': [{'kind': 'setting', 'key': 'mail.personal', 'value': on}]}
    assert client(app)('POST', '/api/_controls/overrides', body)[0] == 200


def mailbox(imap, smtp, address='editor@acme.test', password=APP_PASSWORD, **extra):
    return {'address': address, 'imap_host': '127.0.0.1', 'imap_port': imap.server_address[1],
            'smtp_host': '127.0.0.1', 'smtp_port': smtp.server_address[1], 'password': password, **extra}


def drain(app):
    while app.business.jobs.run_one():
        pass


def links(app):
    """Messages copied from mailboxes: Journal `message` entries, without their mailbox bookkeeping."""
    with app.database._connection() as connection:
        rows = connection.execute("SELECT model, record_id, data FROM evlet_journal WHERE kind='message' ORDER BY id").fetchall()
    return [(row['model'], row['record_id'], {key: value for key, value in json.loads(row['data']).items() if key not in ('source', 'field')})
            for row in rows]


def messages(c, model, record_id, **kwargs):
    """The message entries of a record's Activity, as a reader sees them."""
    return c('GET', f'/api/{model}/{record_id}/journal?kind=message', **kwargs)


def stored(app):
    """Every mailbox-related row, as text."""
    with app.database._connection() as connection:
        return json.dumps([[{key: (bytes(value).hex() if isinstance(value, (bytes, memoryview)) else value) for key, value in dict(row).items()}
                            for row in connection.execute(f'SELECT * FROM {table}')]
                           for table in ('evlet_mailboxes', 'evlet_mail_states', 'evlet_mail_links', 'evlet_journal', 'evlet_jobs', 'evlet_audit', 'evlet_connectors')])


# ---------- the switch ----------

def test_off_by_default_and_refused_until_an_admin_turns_it_on(app, mail):
    imap, smtp, _ = mail
    editor = client(app, 'editor')
    page = editor('GET', '/api/_settings/me/mailbox')[1]
    assert page == {'allowed': False, 'providers': {'microsoft': False, 'google': False}, 'shared': None, 'mailbox': None}
    assert editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))[:2] == (403, {'error': module.OFF})
    assert editor('POST', '/api/_settings/me/mailbox/oauth', {'provider': 'google'})[:2] == (403, {'error': module.OFF})
    assert editor('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'mail.personal', 'value': True}]})[0] == 404
    status, payload, _ = client(app)('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'mail.personal', 'value': 'yes'}]})
    assert status == 422 and payload['fields'] == {'changes.0': 'Use on or off.'}
    switch(app, True)
    assert editor('GET', '/api/_settings/me/mailbox')[1]['allowed'] is True
    assert editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))[0] == 200
    switch(app, False)
    # Off again: nothing is read, and connecting is refused; the person can still disconnect.
    imap.mail.append(message(1, 'buyer@acme.test', 'While off'))
    drain(app)
    assert links(app) == [] and imap.bodies == []
    assert editor('POST', '/api/_settings/me/mailbox/sync')[0] == 403
    assert editor('DELETE', '/api/_settings/me/mailbox')[1]['mailbox'] is None
    with app.database._connection() as connection:
        audit = [tuple(row) for row in connection.execute("SELECT target, detail FROM evlet_audit WHERE area='Email' ORDER BY id")]
    assert audit == [('mail.personal', 'Changed'), ('editor · own mailbox', 'Connected (Other mailbox)'),
                     ('mail.personal', 'Reset to the file value'), ('editor · own mailbox', 'Disconnected; saved sign-in deleted')]


# ---------- app passwords and the sync ----------

def test_app_password_connect_sync_matching_and_privacy(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    editor = client(app, 'editor')
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, password='wrong'))
    assert status == 422 and payload['fields'] == {'password': 'The server rejected the username or password.'}
    imap.mail += [message(1, 'Buyer <buyer@acme.test>', 'Order question', body='Can you ship on Monday?'),
                  message(2, 'mina@acme.test', 'From my contact'),
                  message(3, 'omar@acme.test', 'Private: someone else\'s contact'),
                  message(4, 'lina@lead.test', 'Lead asks'),
                  message(5, 'sales@parts.test', 'Supplier offer'),
                  message(6, 'stranger@nowhere.test', 'Secret: not for Evlet', body='Do not copy this')]
    page = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))[1]
    assert page['mailbox'] | {'connected_at': 0} == {'provider': 'imap', 'title': 'Other mailbox', 'address': 'editor@acme.test', 'status': 'connected',
                                                      'error': None, 'connected_at': 0, 'synced_at': None, 'send_from': 'mine', 'link_replies': True, 'only_crm': True}
    drain(app)
    found = {(model, record_id): data for model, record_id, data in links(app)}
    records = app.records
    assert set(found) == {('customer', records['customer']), ('contact', records['mine']), ('lead', records['lead'])}
    assert found['customer', records['customer']] == {'text': 'Can you ship on Monday?', 'subject': 'Order question', 'party': 'buyer@acme.test', 'direction': 'in'}
    # Only matching messages were opened; the others' headers were read in memory and nothing of them is stored.
    assert sorted(imap.bodies) == [1, 2, 4]
    text = stored(app)
    for leaked in ('stranger', 'Secret: not for Evlet', 'Do not copy', 'omar@acme.test', 'Supplier offer', APP_PASSWORD):
        assert leaked not in text
    assert editor('GET', '/api/_settings/me/mailbox')[1]['mailbox']['synced_at'] is not None

    # Linked messages follow the record: the owner's contact is hidden from the viewer, the admin reads every record.
    viewer, admin = client(app, 'viewer'), client(app)
    assert messages(editor, 'contact', records['mine'])[1]['items'][0]['data']['subject'] == 'From my contact'
    assert messages(viewer, 'contact', records['mine'])[0] == 404
    assert messages(admin, 'contact', records['mine'])[1]['total'] == 1
    # Messages are the person's Journal entries: the mailbox owner is the actor.
    page = messages(viewer, 'customer', records['customer'])[1]
    assert page['users'][str(page['items'][0]['actor'])] == 'editor' and page['items'][0]['data']['source'] == 'mailbox'
    assert 'field' not in page['items'][0]['data']
    # The old messages route is gone; the Activity tab (the journal) shows them.
    assert editor('GET', f"/api/customer/{records['customer']}/messages")[0] in (404, 405)

    # Admins can't read a personal mailbox: no Controls route shows it, and their own page shows only theirs.
    assert admin('GET', '/api/_settings/me/mailbox')[1]['mailbox'] is None
    everything = json.dumps([admin('GET', path)[1] for path in ('/api/_controls/state', '/api/_controls/connectors', '/api/_controls/overrides')])
    assert 'editor@acme.test' not in everything and APP_PASSWORD not in everything
    assert admin('GET', '/api/_controls/mailboxes')[0] == 404

    # Re-reading the same messages (a lost cursor, a new UIDVALIDITY) links nothing twice.
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_mailboxes SET cursor=NULL')
    imap.validity = 8
    assert editor('POST', '/api/_settings/me/mailbox/sync')[1]['queued'] is True
    drain(app)
    assert len(links(app)) == 3

    # With "only customers and leads" off, addresses on any model the person can read match too.
    assert editor('PATCH', '/api/_settings/me/mailbox', {'only_crm': False})[1]['mailbox']['only_crm'] is False
    imap.mail += [message(7, 'sales@parts.test', 'Second offer'), message(8, 'stranger@nowhere.test', 'Still not'), message(9, 'hello@partner.test', 'Not opted in')]
    # A second Sync now in the same minute is the same job; the schedule picks the mailbox up.
    app.business.mailboxes.run_job(None, {})
    assert [(model, data['subject']) for model, _, data in links(app)[3:]] == [('supplier', 'Second offer')]
    assert editor('PATCH', '/api/_settings/me/mailbox', {'only_crm': 'no'})[0] == 422


def test_field_permissions_apply_as_the_person(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    imap.mail += [message(1, 'lina@lead.test', 'Lead writes', to='viewer@acme.test'), message(2, 'buyer@acme.test', 'Customer writes', to='viewer@acme.test')]
    assert client(app, 'viewer')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, address='viewer@acme.test'))[0] == 200
    drain(app)
    # The viewer can't read Lead.email, so the lead's address doesn't match for them.
    assert [model for model, _, _ in links(app)] == ['customer']


def test_batches_are_bounded_and_link_replies_off_reads_nothing(app, mail, monkeypatch):
    imap, smtp, _ = mail
    monkeypatch.setattr(module, 'BATCH', 2)
    switch(app, True)
    imap.mail += [message(uid, 'buyer@acme.test', f'Message {uid}') for uid in range(1, 6)]
    editor = client(app, 'editor')
    editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    assert app.business.jobs.run_one() and len(links(app)) == 2
    with app.database._connection() as connection:
        mailbox_id = connection.execute('SELECT id FROM evlet_mailboxes').fetchone()[0]
        queued = [row[0] for row in connection.execute("SELECT dedupe FROM evlet_jobs WHERE state='queued'")]
    assert queued == [f'mail:{mailbox_id}:7:2']
    drain(app)
    assert len(links(app)) == 5
    editor('PATCH', '/api/_settings/me/mailbox', {'link_replies': False})
    imap.mail.append(message(6, 'buyer@acme.test', 'Not read'))
    imap.log.clear()
    editor('POST', '/api/_settings/me/mailbox/sync')
    drain(app)
    assert len(links(app)) == 5 and imap.log == []


def test_disconnect_deletes_the_sign_in_and_stops_sync(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    editor = client(app, 'editor')
    editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    with app.database._connection() as connection:
        mailbox_id = connection.execute('SELECT id FROM evlet_mailboxes').fetchone()[0]
    assert editor('DELETE', '/api/_settings/me/mailbox')[1]['mailbox'] is None
    imap.mail.append(message(1, 'buyer@acme.test', 'After disconnect'))
    imap.log.clear()
    drain(app)  # The sync queued on connect finds no mailbox.
    assert app.business.mailboxes.sync(mailbox_id) == 0
    assert imap.log == [] and links(app) == []
    with app.database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_mailboxes').fetchone()[0] == 0
    assert editor('DELETE', '/api/_settings/me/mailbox')[0] == 404


# ---------- sending ----------

def test_sending_as_the_person_or_the_shared_account_and_replies_link_to_the_record(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    shared = {'settings': {'address': 'noreply@acme.test', 'host': '127.0.0.1', 'port': smtp.server_address[1], 'security': 'none', 'username': 'editor@acme.test'},
              'secrets': {'password': APP_PASSWORD}}
    assert client(app)('PUT', '/api/_controls/connectors/smtp:notifications', shared)[0] == 200
    editor = client(app, 'editor')
    editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    mailboxes, users = app.business.mailboxes, app.ids
    person = User(users['editor'], 'editor', 'editor')
    sent = mailboxes.send_mail(person, 'buyer@acme.test', 'Your quote', 'Here it is.', record=(Deal, app.records['deal']))
    assert 'From: editor@acme.test' in smtp.messages[-1][1]
    assert links(app) == [('deal', app.records['deal'], {'text': 'Here it is.', 'subject': 'Your quote', 'party': 'buyer@acme.test', 'direction': 'out'})]
    editor('PATCH', '/api/_settings/me/mailbox', {'send_from': 'shared'})
    mailboxes.send_mail(person, 'buyer@acme.test', 'From the shared account', 'Hi.')
    assert 'From: noreply@acme.test' in smtp.messages[-1][1]
    imap.mail.append(message(1, 'buyer@acme.test', 'Re: Your quote', reply_to=sent))
    drain(app)
    assert sorted((model, data['subject']) for model, _, data in links(app)) == [('customer', 'Re: Your quote'), ('deal', 'Re: Your quote'), ('deal', 'Your quote')]
    with pytest.raises(AccessError):
        mailboxes.send_mail(User(users['viewer'], 'viewer', 'viewer'), 'buyer@acme.test', 'Hi', 'x', record=(Contact, app.records['mine']))


# ---------- OAuth ----------

def provider_settings(provider, imap, smtp, **extra):
    base = f'http://127.0.0.1:{provider.server_address[1]}'
    return {'settings': {'client_id': 'client-1', 'authorize_url': base + '/authorize', 'token_url': base + '/token', 'revoke_url': base + '/revoke',
                         'imap_host': '127.0.0.1', 'imap_port': imap.server_address[1], 'smtp_host': '127.0.0.1', 'smtp_port': smtp.server_address[1], **extra},
            'secrets': {'client_secret': 'provider-secret-3a9'}}


def sign_in(app, person, provider, *, approve=True):
    """Start, approve at the fake provider like a browser would, and come back. Returns (location, state)."""
    url = urlsplit(person('POST', '/api/_settings/me/mailbox/oauth', {'provider': 'google'})[1]['url'])
    params = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert params['code_challenge_method'] == 'S256' and params['redirect_uri'] == 'http://127.0.0.1:8000/_/mail/callback'
    assert params['client_id'] == 'client-1' and params['access_type'] == 'offline' and len(params['state']) >= 40
    code = 'code-' + params['state'][:8]
    provider.codes[code] = {'redirect_uri': params['redirect_uri'], 'challenge': params['code_challenge']}
    query = f"code={code}&state={params['state']}" if approve else f"error=access_denied&state={params['state']}"
    return come_back(app, person, query), params['state']


def come_back(app, person, query):
    """The provider's cross-site redirect arrives without the SameSite=Strict cookie; the page then posts from this origin, with it."""
    status, page, headers = send(app, 'GET', f'/_/mail/callback?{query}', headers={b'sec-fetch-site': b'cross-site'})
    assert status == 200 and headers[b'content-type'].startswith(b'text/html') and b"script-src 'self'" in headers[b'content-security-policy']
    assert b'<script>' not in page and b'src="/_/mail/continue.js"' in page
    fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', page.decode()))
    status, _, headers = send(app, 'POST', '/_/mail/callback', urlencode(fields).encode(), {
        b'content-type': b'application/x-www-form-urlencoded', b'cookie': person.cookie.encode(), b'origin': b'http://127.0.0.1:8000',
        b'sec-fetch-site': b'same-origin'})
    assert status == 302
    return headers[b'location'].decode()


def test_oauth_with_pkce_sealed_tokens_refresh_and_revocation(app, mail):
    imap, smtp, provider = mail
    switch(app, True)
    admin, editor = client(app), client(app, 'editor')
    assert editor('POST', '/api/_settings/me/mailbox/oauth', {'provider': 'google'})[0] == 409
    assert admin('PUT', '/api/_controls/connectors/mail_google', provider_settings(provider, imap, smtp))[0] == 200
    assert editor('GET', '/api/_settings/me/mailbox')[1]['providers'] == {'microsoft': False, 'google': True}

    assert sign_in(app, editor, provider, approve=False)[0] == '/settings/email?mail=denied'
    location, state = sign_in(app, editor, provider)
    assert location == '/settings/email?mail=connected'
    # The state is single use, and another person can't finish someone else's sign-in.
    assert come_back(app, editor, f'code=x&state={state}') == '/settings/email?mail=expired'
    # Another person's attempt neither finishes nor burns the state; a post from elsewhere is refused.
    viewer = client(app, 'viewer')
    url = urlsplit(editor('POST', '/api/_settings/me/mailbox/oauth', {'provider': 'google'})[1]['url'])
    other_state = parse_qs(url.query)['state'][0]
    assert come_back(app, viewer, f'code=x&state={other_state}') == '/settings/email?mail=expired'
    with app.database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_mail_states').fetchone()[0] == 1
    forged = send(app, 'POST', '/_/mail/callback', urlencode({'code': 'x', 'state': other_state}).encode(), {
        b'content-type': b'application/x-www-form-urlencoded', b'cookie': editor.cookie.encode(), b'origin': b'https://evil.test'})
    assert forged[0] == 403
    assert send(app, 'GET', '/_/mail/continue.js')[1] == b'document.forms[0].submit();\n'

    exchange = next(item for item in provider.requests if item['grant_type'] == 'authorization_code')
    assert len(exchange['code_verifier']) >= 43 and exchange['redirect_uri'] == 'http://127.0.0.1:8000/_/mail/callback'
    page = editor('GET', '/api/_settings/me/mailbox')[1]['mailbox']
    assert (page['provider'], page['address'], page['status']) == ('google', 'editor@acme.test', 'connected')
    text = stored(app)
    for secret in ('access-1', 'refresh-1', 'provider-secret-3a9', exchange['code_verifier']):
        assert secret not in text

    # A token about to expire is refreshed before the sync signs in with it.
    imap.mail.append(message(1, 'buyer@acme.test', 'Hello over OAuth'))
    provider.expires_in = 60
    sign_in(app, editor, provider)
    drain(app)
    assert [item['grant_type'] for item in provider.requests][-1] == 'refresh_token'
    assert [data['subject'] for _, _, data in links(app)] == ['Hello over OAuth']

    # A revoked sign-in ends: the tokens are deleted and syncing stops until the person connects again.
    provider.revoked |= provider.refresh
    imap.mail.append(message(2, 'buyer@acme.test', 'After revoke'))
    editor('POST', '/api/_settings/me/mailbox/sync')
    drain(app)
    page = editor('GET', '/api/_settings/me/mailbox')[1]['mailbox']
    assert page['status'] == 'reconnect' and page['error'] == 'Your Google Workspace sign-in ended. Connect your mailbox again.'
    with app.database._connection() as connection:
        assert connection.execute('SELECT secrets FROM evlet_mailboxes').fetchone()[0] is None
    assert len(links(app)) == 1


def test_a_changed_connector_cannot_redirect_a_persons_tokens(app, mail):
    imap, smtp, provider = mail
    switch(app, True)
    admin, editor = client(app), client(app, 'editor')
    admin('PUT', '/api/_controls/connectors/mail_google', provider_settings(provider, imap, smtp))
    sign_in(app, editor, provider)
    other = serve(_IMAP)
    other.logins, other.tokens, other.mail, other.log, other.bodies, other.validity = set(), provider.tokens, [], [], [], 1
    try:
        admin('PUT', '/api/_controls/connectors/mail_google', provider_settings(provider, other, smtp))
        editor('POST', '/api/_settings/me/mailbox/sync')
        drain(app)
        assert other.log == [] and any(line.startswith('AUTHENTICATE') for line in imap.log)
    finally:
        other.shutdown()
        other.server_close()


# ---------- addresses and time limits ----------

@pytest.mark.parametrize('host', ['169.254.169.254', '100.100.100.200', '0.0.0.0'])
def test_personal_servers_follow_the_connector_address_rules(app, mail, host):
    imap, smtp, _ = mail
    switch(app, True)
    editor = client(app, 'editor')
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, imap_host=host, imap_port=993))
    assert status == 422 and payload['fields'] == {'imap_host': connectors_module.NOT_ALLOWED}
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, imap_host='mail.example.test', imap_security='none'))
    assert status == 422 and 'must be on this machine' in payload['fields']['imap_security']
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, imap_host='mail.example.test', imap_port=2525))
    assert status == 422 and payload['fields']['imap_port'] == 'Use port 143 or 993.'


@pytest.mark.parametrize('host', ['10.0.0.5', '192.168.1.1', '172.16.0.9', '100.64.0.1', 'fc00::1'])
def test_personal_mailboxes_reach_only_public_servers(app, mail, monkeypatch, host):
    imap, smtp, _ = mail
    monkeypatch.setattr(module, 'PUBLIC_SERVERS_ONLY', True)
    switch(app, True)
    editor = client(app, 'editor')
    body = mailbox(imap, smtp, imap_host=host, imap_port=993, smtp_host=host, smtp_port=587)
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', body)
    assert status == 422 and payload['fields'] == {'imap_host': connectors_module.PUBLIC_ONLY}
    assert 'ask an admin' in connectors_module.PUBLIC_ONLY
    assert imap.log == [] and smtp.messages == []


def test_personal_mailboxes_refuse_this_machine_and_plain_connections(app, mail, monkeypatch):
    imap, smtp, _ = mail
    monkeypatch.setattr(module, 'PUBLIC_SERVERS_ONLY', True)
    switch(app, True)
    editor = client(app, 'editor')
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    assert status == 422 and payload['fields'] == {'imap_host': connectors_module.PUBLIC_ONLY, 'smtp_host': connectors_module.PUBLIC_ONLY}
    # The port list and encryption apply whatever the security choice.
    public = {'imap_host': '93.184.216.34', 'smtp_host': '93.184.216.34', 'smtp_port': 587}
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, **public, imap_port=993, imap_security='none'))
    assert status == 422 and 'encrypted' in payload['fields']['imap_security']
    status, payload, _ = editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, **public, imap_port=2525))
    assert status == 422 and payload['fields']['imap_port'] == 'Use port 143 or 993.'
    assert imap.log == []


def test_a_saved_personal_mailbox_is_checked_again_on_each_connection(app, mail, monkeypatch):
    imap, smtp, _ = mail
    switch(app, True)
    editor = client(app, 'editor')
    assert editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))[0] == 200
    monkeypatch.setattr(module, 'PUBLIC_SERVERS_ONLY', True)
    imap.log.clear()
    imap.mail.append(message(1, 'buyer@acme.test', 'Never read'))
    drain(app)
    state = editor('GET', '/api/_settings/me/mailbox')[1]['mailbox']
    assert state['status'] == 'error' and state['error'] == connectors_module.PUBLIC_ONLY
    assert imap.log == [] and links(app) == []
    with pytest.raises(connectors_module.ConnectionFailed, match='public mail server'):
        app.business.mailboxes.send_mail(User(app.ids['editor'], 'editor', 'editor'), 'buyer@acme.test', 'Hello', 'Hi')
    assert smtp.messages == []


def test_admin_connectors_may_use_private_mail_servers():
    # An office mail server on a private network still works when an admin sets it up.
    for host in ('10.0.0.5', '192.168.1.1', '100.64.0.1', 'fc00::1'):
        assert connectors_module.safe_address(host, 587) == (host, 587)
        with pytest.raises(connectors_module.ConnectionFailed, match='public mail server'):
            connectors_module.safe_address(host, 587, public=True)
    assert connectors_module.safe_address('93.184.216.34', 993, public=True) == ('93.184.216.34', 993)


def test_provider_addresses_follow_the_rules_too(app, mail):
    imap, smtp, provider = mail
    switch(app, True)
    admin, editor = client(app), client(app, 'editor')
    admin('PUT', '/api/_controls/connectors/mail_google', provider_settings(provider, imap, smtp, authorize_url='http://accounts.example.test/auth'))
    assert editor('POST', '/api/_settings/me/mailbox/oauth', {'provider': 'google'})[1]['fields'] == {'provider': connectors_module.NOT_ALLOWED}
    admin('PUT', '/api/_controls/connectors/mail_google', provider_settings(provider, imap, smtp, token_url='https://169.254.169.254/token'))
    assert sign_in(app, editor, provider)[0] == '/settings/email?mail=failed'
    assert provider.requests == []


def test_a_silent_mail_server_times_out(app, mail, monkeypatch):
    imap, smtp, _ = mail
    monkeypatch.setattr(connectors_module, 'TIMEOUT', 1)
    switch(app, True)
    silent = socket.socket()
    silent.bind(('127.0.0.1', 0))
    silent.listen(5)
    try:
        started = time.monotonic()
        status, payload, _ = client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, imap_port=silent.getsockname()[1]))
        assert time.monotonic() - started < 3
        assert status == 422 and payload['fields'] == {'imap_host': connectors_module.UNREACHABLE}
    finally:
        silent.close()


def test_keys_and_assistants_cannot_use_the_mailbox_routes(app, mail):
    admin = client(app)
    token = admin('POST', '/api/_controls/keys', {'name': 'Script', 'user': 'editor', 'scope': {'access': 'write', 'models': None}})[1]['token']
    headers = {b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''}
    assert http(app, 'GET', '/api/_settings/me/mailbox', headers=headers)[0] == 403
    assert http(app, 'GET', '/api/_settings/me/mailbox')[0] == 401


def test_secret_rotation_covers_mailboxes(app, mail, tmp_path, monkeypatch):
    imap, smtp, _ = mail
    switch(app, True)
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    monkeypatch.delenv('EVLET_SECRET_KEY')
    key = vault.SecretKey(tmp_path / 'secret.key')
    key.write([b'k' * 32], new=True)
    done, failed = vault.rotate(tmp_path / 'mail.sqlite3', key)
    # One key for everything sealed on the instance: the mailbox and the two-step secrets of the test's admin.
    assert failed == [] and done >= 1
    app.business.connectors.key = key
    imap.mail.append(message(1, 'buyer@acme.test', 'After rotation'))
    drain(app)
    assert len(links(app)) == 1


# ---------- review findings ----------

def mailbox_id(app):
    with app.database._connection() as connection:
        return connection.execute("SELECT id FROM evlet_mailboxes WHERE address='editor@acme.test'").fetchone()[0]


def page_of(app, role='editor'):
    return client(app, role)('GET', '/api/_settings/me/mailbox')[1]['mailbox']


def test_unreadable_messages_are_skipped_and_nothing_reaches_the_logs(app, mail, caplog, monkeypatch):
    imap, smtp, _ = mail
    caplog.set_level(logging.DEBUG)
    switch(app, True)
    poison = (b'From: x@stranger.test ' + b'(' * 5000 + b')' * 5000 + b'\r\nTo: editor@acme.test\r\nSubject: Stranger poison\r\n'
              b'Message-ID: <p1@x.test>\r\n\r\nbody\r\n')
    deep = b''.join(b'Content-Type: multipart/mixed; boundary="b%d"\r\n\r\n--b%d\r\n' % (i, i) for i in range(3000)) + b'Content-Type: text/plain\r\n\r\nhi\r\n'
    nested = (b'From: buyer@acme.test\r\nTo: editor@acme.test\r\nSubject: Deep parts\r\nMessage-ID: <p2@x.test>\r\nMIME-Version: 1.0\r\n' + deep)
    imap.mail += [(1, poison), (2, nested), message(3, 'buyer@acme.test', 'Legit after poison')]
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)
    app.business.mailboxes.run_job(None, {})
    assert sorted(data['subject'] for _, _, data in links(app)) == ['Deep parts', 'Legit after poison']
    assert page_of(app)['status'] == 'connected'
    # A whole run that fails is recorded on the mailbox, never raised, and logged by type only.
    imap.mail.append(message(4, 'buyer@acme.test', 'Another'))
    monkeypatch.setattr(module.Mailboxes, 'match', lambda *args: (_ for _ in ()).throw(ValueError('buyer@acme.test Secret subject')))
    assert app.business.mailboxes.sync(mailbox_id(app)) == 0
    assert (page_of(app)['status'], page_of(app)['error']) == ('error', module.FAILED)
    for leaked in ('stranger', 'Stranger poison', 'Secret subject', 'buyer@acme.test', 'Legit'):
        assert leaked not in caplog.text
    assert 'Mailbox' in caplog.text and 'ValueError' in caplog.text


def test_a_server_that_sends_too_much_or_too_slowly_is_cut_off(app, mail, monkeypatch):
    imap, smtp, _ = mail
    switch(app, True)
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)
    monkeypatch.setattr(connectors_module._Limits, 'LITERAL', 10_000)
    imap.mail.append(message(1, 'buyer@acme.test', 'Huge', body='A' * 50_000))
    assert app.business.mailboxes.sync(mailbox_id(app)) == 0 and page_of(app)['status'] == 'error'

    class Drip(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'* OK [CAPABILITY IMAP4rev1] fake\r\n')
            self.rfile.readline()
            for byte in b'* CAPABILITY IMAP4rev1 ' + b'X' * 200:
                time.sleep(0.2)
                try:
                    self.wfile.write(bytes([byte]))
                except OSError:
                    return

    drip = serve(Drip)
    monkeypatch.setattr(module, 'RUN_SECONDS', 2)
    try:
        with app.database.transaction() as tx:
            settings = json.loads(tx.connection.execute('SELECT settings FROM evlet_mailboxes').fetchone()[0])
            settings['imap']['port'] = drip.server_address[1]
            tx.connection.execute('UPDATE evlet_mailboxes SET settings=?', (json.dumps(settings),))
        started = time.monotonic()
        assert app.business.mailboxes.sync(mailbox_id(app)) == 0
        assert time.monotonic() - started < 5 and page_of(app)['status'] == 'error'
    finally:
        drip.shutdown()
        drip.server_close()


def test_a_connection_dropped_during_sign_in_keeps_the_saved_sign_in(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)

    class HangUp(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] fake\r\n')
            while line := self.rfile.readline():
                tag, command = line.split(b' ', 2)[:2]
                if command.upper().startswith(b'LOGIN'):
                    return
                self.wfile.write(b'* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n' + tag + b' OK done\r\n')

    server = serve(HangUp)
    try:
        with app.database.transaction() as tx:
            settings = json.loads(tx.connection.execute('SELECT settings FROM evlet_mailboxes').fetchone()[0])
            settings['imap']['port'] = server.server_address[1]
            tx.connection.execute('UPDATE evlet_mailboxes SET settings=?', (json.dumps(settings),))
        assert app.business.mailboxes.sync(mailbox_id(app)) == 0
        assert page_of(app)['status'] == 'error'
        with app.database.transaction() as tx:
            assert tx.connection.execute('SELECT secrets FROM evlet_mailboxes').fetchone()[0] is not None
    finally:
        server.shutdown()
        server.server_close()


def test_a_hidden_email_field_is_not_shown_as_the_party(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    imap.mail.append(message(1, 'lina@lead.test', 'Lead mail'))
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)
    assert messages(client(app, 'editor'), 'lead', app.records['lead'])[1]['items'][0]['data']['party'] == 'lina@lead.test'
    item = messages(client(app, 'viewer'), 'lead', app.records['lead'])[1]['items'][0]
    assert item['data']['party'] is None and item['data']['subject'] == 'Lead mail' and item['partial']


def test_send_only_and_failing_mailboxes_do_not_hold_up_the_others(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)
    closed = socket.socket()
    closed.bind(('127.0.0.1', 0))
    port = closed.getsockname()[1]
    closed.close()
    with app.database.transaction() as tx:
        base = json.loads(tx.connection.execute('SELECT settings FROM evlet_mailboxes').fetchone()[0])
        for index in range(50):
            user_id = tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES (?, 'x', 'viewer')", (f'u{index}',)).lastrowid
            sending_only = index < 25
            settings = {**base, 'link_replies': not sending_only, 'imap': {**base['imap'], 'port': port}}
            tx.connection.execute("INSERT INTO evlet_mailboxes(user_id,provider,address,settings,secrets,status,linking,created_at) VALUES (?,'imap',?,?,?,'connected',?,0)",
                                  (user_id, f'u{index}@x.test', json.dumps(settings), module.seal(app.business, tx.connection, {'password': 'x'}, connectors_module.mailbox_context(user_id)),
                                   int(not sending_only)))
    imap.mail.append(message(1, 'buyer@acme.test', 'Should sync'))
    for _ in range(2):
        app.business.mailboxes.run_job(None, {})
    assert [data['subject'] for _, _, data in links(app)] == ['Should sync']


def test_only_a_revoked_grant_ends_a_sign_in(app, mail):
    imap, smtp, provider = mail
    switch(app, True)
    client(app)('PUT', '/api/_controls/connectors/mail_google', provider_settings(provider, imap, smtp))
    editor = client(app, 'editor')
    provider.expires_in = 60
    sign_in(app, editor, provider)
    drain(app)
    provider.client_error = 'invalid_client'
    imap.mail.append(message(1, 'buyer@acme.test', 'While misconfigured'))
    app.business.mailboxes.sync(mailbox_id(app))
    page = page_of(app)
    assert page['status'] == 'error' and 'Ask an admin' in page['error'] and links(app) == []
    with app.database._connection() as connection:
        assert connection.execute('SELECT secrets FROM evlet_mailboxes').fetchone()[0] is not None
    provider.client_error = None
    app.business.mailboxes.sync(mailbox_id(app))
    assert [data['subject'] for _, _, data in links(app)] == ['While misconfigured'] and page_of(app)['status'] == 'connected'
    # Disconnecting also revokes the sign-in at the provider.
    editor('DELETE', '/api/_settings/me/mailbox')
    assert any(token.startswith('refresh-') for token in provider.revoked if token)


def test_an_id_token_without_an_audience_is_refused(app, mail, monkeypatch):
    imap, smtp, provider = mail
    switch(app, True)
    client(app)('PUT', '/api/_controls/connectors/mail_google', provider_settings(provider, imap, smtp))
    monkeypatch.setattr(module, 'claims', lambda token: {'email': 'editor@acme.test'})
    assert sign_in(app, client(app, 'editor'), provider)[0] == '/settings/email?mail=failed'


def test_replies_link_to_the_record_they_answer_and_reused_ids_keep_both(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)
    person = User(app.ids['editor'], 'editor', 'editor')
    sent = app.business.mailboxes.send_mail(person, 'new.person@nowhere.test', 'Quote', 'Here.', record=(Deal, app.records['deal']))
    imap.mail.append(message(1, 'new.person@nowhere.test', 'Re: Quote', reply_to=sent))
    # A message linked by someone else to another record doesn't pull this one there.
    with app.database.transaction() as tx:
        module.link_message(app.business, tx, User(app.ids['admin'], 'admin', 'admin'), Supplier, app.records['supplier'], module.key_of('<admin@acme.test>'),
                            {'text': 'x', 'subject': 'x', 'party': 'x', 'direction': 'out'})
    imap.mail.append(message(2, 'buyer@acme.test', 'Hi', reply_to='<admin@acme.test>'))
    first, second = message(3, 'buyer@acme.test', 'First'), message(4, 'buyer@acme.test', 'Second, same id')
    imap.mail += [first, (4, second[1].replace(b'<m4@mail.test>', b'<m3@mail.test>'))]
    client(app, 'editor')('POST', '/api/_settings/me/mailbox/sync')
    drain(app)
    subjects = sorted((model, data['subject']) for model, _, data in links(app) if model != 'supplier')
    assert subjects == [('customer', 'First'), ('customer', 'Hi'), ('customer', 'Second, same id'), ('deal', 'Quote'), ('deal', 'Re: Quote')]


def test_sync_now_is_one_job_a_minute_and_throttled(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    editor = client(app, 'editor')
    editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    for _ in range(3):
        editor('POST', '/api/_settings/me/mailbox/sync')
    with app.database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evlet_jobs WHERE dedupe LIKE 'mail:%:now:%'").fetchone()[0] == 1
    statuses = [editor('POST', '/api/_settings/me/mailbox/sync')[0] for _ in range(10)]
    assert 429 in statuses


def test_linking_needs_update_and_keys_follow_their_scope(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    imap.mail.append(message(1, 'buyer@acme.test', 'Hello'))
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)
    with pytest.raises(AccessError):
        app.business.mailboxes.send_mail(User(app.ids['editor'], 'editor', 'editor'), 'a@b.test', 'Hi', 'x', record=(Supplier, app.records['supplier']))
    admin = client(app)
    token = admin('POST', '/api/_controls/keys', {'name': 'Deals only', 'user': 'editor', 'scope': {'access': 'read', 'models': ['Deal']}})[1]['token']
    headers = {b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''}
    assert http(app, 'GET', f"/api/customer/{app.records['customer']}/journal?kind=message", headers=headers)[0] in (403, 404)
    token = admin('POST', '/api/_controls/keys', {'name': 'All', 'user': 'editor', 'scope': {'access': 'read', 'models': None}})[1]['token']
    headers = {b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''}
    assert http(app, 'GET', f"/api/customer/{app.records['customer']}/journal?kind=message", headers=headers)[1]['total'] == 1


def test_matching_splits_long_address_lists(app):
    wanted = {f'person{index}@example.test' for index in range(40_000)} | {'buyer@acme.test'}
    found = app.business.mailboxes.match(User(app.ids['editor'], 'editor', 'editor'), wanted, True)
    assert found == {'buyer@acme.test': [(Customer, app.records['customer'], 'email')]}


def test_mail_declarations_are_checked(tmp_path):
    with pytest.raises(ValueError, match='kind'):
        Mail(match=('email',), kind='vendor')

    class Wrong(Model):
        permissions = {'admin': ALL}
        title = Text(default='')

        class Meta:
            mail = Mail(match=('title',), kind='customer')

    database = Database(tmp_path / 'wrong.sqlite3', Registry(Wrong))
    with pytest.raises(ValueError, match='stored Email fields'):
        Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))


# ---------- PostgreSQL ----------

def test_postgres_mailboxes(pg_dsn, mail, monkeypatch):  # noqa: F811
    imap, smtp, _ = mail
    monkeypatch.setenv('EVLET_SECRET_KEY', base64.urlsafe_b64encode(b'p' * 32).decode())
    app = build(PostgreSQLDatabase(pg_dsn, Registry(*MODELS)))
    switch(app, True)
    editor = client(app, 'editor')
    imap.mail += [message(1, 'buyer@acme.test', 'Over PostgreSQL'), message(2, 'omar@acme.test', 'Not mine')]
    assert editor('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))[0] == 200
    drain(app)
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_mailboxes SET cursor=NULL')
    editor('POST', '/api/_settings/me/mailbox/sync')
    drain(app)
    assert [data['subject'] for _, _, data in links(app)] == ['Over PostgreSQL']
    assert messages(editor, 'customer', app.records['customer'])[1]['total'] == 1
    assert editor('PATCH', '/api/_settings/me/mailbox', {'send_from': 'shared'})[1]['mailbox']['send_from'] == 'shared'
    assert editor('DELETE', '/api/_settings/me/mailbox')[1]['mailbox'] is None


def test_mail_needs_the_record_journal(tmp_path):
    class Unjournaled(Model):
        permissions = {'admin': ALL}
        email = Email()

        class Meta:
            mail = Mail(match=('email',), kind='contact')

    database = Database(tmp_path / 'plain.sqlite3', Registry(Unjournaled))
    with pytest.raises(ValueError, match='Add Meta.journal = Journal()'):
        Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))


def test_a_message_is_kept_once_per_record_and_threads_through_the_index(app, mail):
    imap, smtp, _ = mail
    switch(app, True)
    imap.mail.append(message(1, 'buyer@acme.test', 'First'))
    client(app, 'editor')('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp))
    drain(app)
    with app.database._connection() as connection:
        index = [dict(row) for row in connection.execute('SELECT * FROM evlet_mail_links')]
        entry = connection.execute("SELECT id FROM evlet_journal WHERE kind='message'").fetchone()[0]
    assert len(index) == 1 and set(index[0]) == {'model', 'record_id', 'message_key', 'entry_id'} and index[0]['entry_id'] == entry
    (_, _, data), = links(app)
    editor = User(app.ids['editor'], 'editor', 'editor')
    with app.database.transaction() as tx:
        # The same message again is not stored twice; another message reusing its Message-ID is.
        assert module.link_message(app.business, tx, editor, Customer, app.records['customer'], index[0]['message_key'], data, 'email') is None
        assert module.link_message(app.business, tx, editor, Customer, app.records['customer'], index[0]['message_key'], {**data, 'text': 'Other'}, 'email')
    assert [item['text'] for _, _, item in links(app)] == [data['text'], 'Other']
