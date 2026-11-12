"""Mentions in the activity journal and the per-user inbox (evlet/mentions.py).

Notes written with the rich text composer keep sanitised HTML; mentions become inbox rows for the
people mentioned, permission-safe (no title or link for someone who cannot read the record), with an
unread count, a realtime hint only that person receives, and email when they want it."""
import pytest

from evlet import Database, Journal, Model, Registry, Rule, Text
from evlet.richtext import mention_ids, sanitise
from test_business import client
from test_rules_types import add_user, start


class Case(Model):
    class Meta:
        journal = Journal()
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
                   'viewer': Rule(())}
    title = Text(default='')


@pytest.fixture
def app(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Case)))
    add_user(app, 'editor')
    add_user(app, 'viewer')
    return app


def users(app):
    with app.database.transaction() as tx:
        return {row['username']: row['id'] for row in tx.connection.execute('SELECT id, username FROM evlet_users')}


def mention(user_id, name):
    return f'<span data-mention="{user_id}">{name}</span>'


def case(admin, title='Leak in unit 4'):
    status, created, _ = admin('POST', '/api/case', {'title': title})
    assert status == 201, created
    return created['id']


def test_the_sanitiser_keeps_mentions_only_where_asked():
    markup = '<p>Hi <span data-mention="3" onclick="x()" class="y">Priya</span> <span data-mention="abc">no</span></p>'
    assert sanitise(markup, mentions=True)[0] == '<p>Hi <span data-mention="3">Priya</span> no</p>'
    assert sanitise(markup, mentions=True)[1] == 'Hi @Priya no'
    # A RichText field's value is not a journal: a mention there is plain text.
    assert sanitise(markup)[0] == '<p>Hi Priya no</p>'
    assert mention_ids('<p><span data-mention="4">A</span><span data-mention="4">A</span><span data-mention="5">B</span></p>') == [4, 5]


def test_picker_lists_active_people_and_who_can_open_the_record(app):
    admin = client(app)
    record = case(admin)
    status, found, _ = admin('GET', f'/api/case/{record}/mentionable?q=')
    assert status == 200
    by_name = {item['username']: item for item in found['items']}
    assert by_name['editor']['can_read'] is True and by_name['viewer']['can_read'] is False
    status, found, _ = admin('GET', f'/api/case/{record}/mentionable?q=edi')
    assert [item['username'] for item in found['items']] == ['editor']
    # Never yourself; names that start with the typed text come first.
    assert 'admin' not in by_name
    status, found, _ = admin('GET', f'/api/case/{record}/mentionable?q=v')
    assert [item['username'] for item in found['items']][0] == 'viewer'
    # Someone who cannot post on the record cannot list people for it.
    assert client(app, 'viewer')('GET', f'/api/case/{record}/mentionable?q=')[0] in (403, 404)


def test_a_note_stores_html_and_mentions_fill_the_inbox(app):
    admin, editor = client(app), client(app, 'editor')
    ids = users(app)
    record = case(admin)
    html = f'<p>Please check, {mention(ids["editor"], "Ed")} and {mention(ids["admin"], "me")}<script>x()</script></p>'
    status, entry, _ = admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': html})
    assert status == 201, entry
    assert entry['data']['html'] == f'<p>Please check, {mention(ids["editor"], "Ed")} and {mention(ids["admin"], "me")}</p>'
    assert entry['data']['text'] == 'Please check, @Ed and @me'
    # The author is never told about their own mention.
    assert admin('GET', '/api/_inbox/count')[1] == {'unread': 0}
    status, inbox, _ = editor('GET', '/api/_inbox')
    assert status == 200 and inbox['unread'] == 1
    [item] = inbox['items']
    assert item['kind'] == 'mention' and item['actor'] == 'System Administrator' and item['read'] is False
    assert item['record']['title'] == 'Leak in unit 4' and item['text'] == 'System Administrator mentioned you on Case Leak in unit 4'
    # The journal names mentioned people with their current username.
    page = admin('GET', f'/api/case/{record}/journal')[1]
    assert page['users'][str(ids['editor'])] == 'editor'
    status, read, _ = editor('POST', '/api/_inbox/read', {'ids': [item['id']]})
    assert status == 200 and read == {'unread': 0}


def test_someone_who_cannot_open_the_record_learns_nothing_about_it(app):
    admin, viewer = client(app), client(app, 'viewer')
    ids = users(app)
    record = case(admin, 'Confidential merger')
    admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': f'<p>{mention(ids["viewer"], "Vi")} the price is 5m</p>'})
    status, inbox, _ = viewer('GET', '/api/_inbox')
    [item] = inbox['items']
    assert item['record'] is None
    assert item['text'] == "System Administrator mentioned you on a record you can't open"
    flat = repr(inbox)
    assert 'Confidential' not in flat and 'price' not in flat and str(record) not in flat


def test_inbox_rows_are_each_persons_own(app):
    admin, editor, viewer = client(app), client(app, 'editor'), client(app, 'viewer')
    ids = users(app)
    record = case(admin)
    admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': f'<p>{mention(ids["editor"], "Ed")}</p>'})
    item = editor('GET', '/api/_inbox')[1]['items'][0]
    assert viewer('GET', '/api/_inbox')[1]['items'] == []
    # Marking another person's row does nothing.
    viewer('POST', '/api/_inbox/read', {'ids': [item['id']]})
    assert editor('GET', '/api/_inbox/count')[1] == {'unread': 1}
    assert editor('POST', '/api/_inbox/read', {'all': True})[1] == {'unread': 0}


def test_inactive_and_unknown_users_are_not_notified(app):
    admin = client(app)
    ids = users(app)
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_users SET active=0 WHERE id=?', (ids['editor'],))
    record = case(admin)
    status, _, _ = admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': f'<p>{mention(ids["editor"], "Ed")} {mention(999, "Ghost")}</p>'})
    assert status == 201
    with app.database.transaction() as tx:
        assert tx.connection.execute('SELECT count(*) FROM evlet_inbox').fetchone()[0] == 0


def test_preferences_decide_the_inbox_and_email(app, monkeypatch):
    admin, editor = client(app), client(app, 'editor')
    ids = users(app)
    sent = []
    monkeypatch.setattr(app.business.connectors, 'accounts', lambda kind='smtp': [{'id': 'smtp:main'}])
    monkeypatch.setattr(app.business.connectors, 'send_mail', lambda to, subject, text, **options: sent.append((to, subject, text)))
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT OR IGNORE INTO evlet_user_profile(user_id) VALUES (?)', (ids['editor'],))
        tx.connection.execute("UPDATE evlet_user_profile SET email='ed@example.test' WHERE user_id=?", (ids['editor'],))
    record = case(admin, 'Roof repair')
    admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': f'<p>{mention(ids["editor"], "Ed")} secret detail</p>'})
    [(to, subject, text)] = sent
    assert to == 'ed@example.test' and subject == 'System Administrator mentioned you on Case Roof repair'
    assert 'secret detail' not in text
    # In Evlet off, email on: no inbox row, still an email.
    assert editor('PATCH', '/api/_settings/me/prefs', {'notify.mention': [False, True]})[0] == 200
    admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': f'<p>{mention(ids["editor"], "Ed")} again</p>'})
    assert len(sent) == 2 and editor('GET', '/api/_inbox')[1]['total'] == 1
    # Both off: nothing at all.
    assert editor('PATCH', '/api/_settings/me/prefs', {'notify.mention': [False, False]})[0] == 200
    admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': f'<p>{mention(ids["editor"], "Ed")} third</p>'})
    assert len(sent) == 2 and editor('GET', '/api/_inbox')[1]['total'] == 1


def test_no_email_without_an_outgoing_account(app):
    admin = client(app)
    ids = users(app)
    record = case(admin)
    admin('POST', f'/api/case/{record}/journal', {'kind': 'note', 'html': f'<p>{mention(ids["editor"], "Ed")}</p>'})
    with app.database.transaction() as tx:
        assert tx.connection.execute('SELECT email FROM evlet_inbox').fetchone()[0] in ('no_email', 'no_account')


def test_the_inbox_change_reaches_only_its_person(app):
    from evlet import EventStore
    admin = client(app)
    ids = users(app)
    store = EventStore(app.database)
    store.initialize()
    with app.database.transaction() as tx:
        store.publish_user(tx, '_inbox', ids['editor'])
    from evlet.auth import User
    editor, viewer = User(ids['editor'], 'editor', 'editor'), User(ids['viewer'], 'viewer', 'viewer')
    assert {'type': 'inbox.changed'} in store.page(editor, app.business.policy, after='0')['items']
    assert store.page(viewer, app.business.policy, after='0')['items'] == []
    assert admin  # signed in
