"""Mentions in the activity journal and the per-user inbox (docs/journal.md, docs/internal/rich-text.md).

A note or message written with the rich text composer may mention people:
`<span data-mention="<user id>">Name</span>`. Each mentioned active user gets a row in `evlet_inbox`, the
framework's per-user inbox (kind 'mention'), kept generic so later notifications can use it too.

Permission-safe: an inbox row holds only who, when and which record. The record's title and link are
shown only to someone who can read the record when they look; anyone else learns only that they were
mentioned. Email (the mail connector's outgoing account) follows the same rule and the person's
notification preferences (Settings, Notifications, "Someone mentions me").
"""

import re
import time

from .auth import AccessError, User
from .database import NotFoundError
from .models import ValidationError, checked_body

KIND = 'mention'
PAGE = 50
EVENT_MODEL = '_inbox'


def initialize(database):
    with database.transaction() as tx:
        tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, '
                              'kind TEXT NOT NULL, actor INTEGER, model TEXT NOT NULL, record_id INTEGER NOT NULL, entry_id INTEGER, '
                              'at INTEGER NOT NULL, read_at INTEGER, email TEXT, hidden INTEGER NOT NULL DEFAULT 0)')
        tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_inbox_user ON evlet_inbox(user_id, hidden, read_at, id)')


def _user(tx, user_id):
    row = tx.connection.execute('SELECT id, username, role, active FROM evlet_users WHERE id=?', (user_id,)).fetchone()
    return None if row is None or not row['active'] else User(row['id'], row['username'], row['role'])


def can_read(business, tx, user, model, record_id):
    try:
        business.policy.record(tx, user, model, record_id)
        return True
    except (AccessError, NotFoundError):  # a deleted record reads as not readable too
        return False


def channels(business, user_id, connection):
    """[in Evlet, email] for mentions from the person's notification preferences (both on by default)."""
    value = business.settings.core(user_id, connection).get('notify.' + KIND, [True, True])
    return [bool(value[0]), bool(value[1])] if isinstance(value, list) and len(value) == 2 else [True, True]


def record(business, tx, author, model, record_id, entry_id, html):
    """Inbox rows for the users an entry mentions (not the author). Returns the ids that want an email."""
    from .richtext import mention_ids
    wanted = []
    name = business.registry.name_for(model)
    now = int(time.time())
    for user_id in mention_ids(html):
        if user_id == author.id:
            continue
        person = _user(tx, user_id)
        if person is None:
            continue
        in_app, email = channels(business, user_id, tx.connection)
        if not in_app and not email:
            continue
        # With "in Evlet" turned off the row only carries the email: hidden from the inbox.
        inbox_id = tx.connection.execute(
            'INSERT INTO evlet_inbox(user_id, kind, actor, model, record_id, entry_id, at, read_at, email, hidden) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (user_id, KIND, author.id, name, int(record_id), entry_id, now, None if in_app else now, 'pending' if email else 'off',
             0 if in_app else 1)).lastrowid
        if email:
            wanted.append(inbox_id)
        publish(business, tx, user_id)
    return wanted


def publish(business, tx, user_id):
    """Tell the person's open browsers their inbox changed (an event only they receive)."""
    events = getattr(business, 'events', None)
    if events is not None:
        events.publish_user(tx, EVENT_MODEL, user_id)


def mentionable(business, tx, user, model, record_id, query):
    """Active people to mention on a record, with whether each can open it (to warn while composing)."""
    if not isinstance(query, str) or len(query) > 60:
        raise ValidationError({'q': 'Use at most 60 characters'})
    needle = query.strip().lower()
    rows = tx.connection.execute(
        "SELECT u.id, u.username, u.role, COALESCE(p.full_name, '') AS full_name FROM evlet_users u "
        'LEFT JOIN evlet_user_profile p ON p.user_id=u.id WHERE u.active=1 ORDER BY u.username LIMIT 500').fetchall()
    found = []
    for row in rows:
        names = f"{row['full_name']} {row['username']}".lower()
        if row['id'] == user.id or needle and not all(word in names for word in needle.split()):
            continue  # never yourself
        person = User(row['id'], row['username'], row['role'])
        if getattr(business.controls, 'roles', {}).get(row['role'], {}).get('realm', 'desk') != 'desk':
            continue  # portal customers are never offered
        found.append({'id': row['id'], 'name': row['full_name'] or row['username'], 'username': row['username'],
                      'can_read': can_read(business, tx, person, model, record_id)})
    # Names and usernames that start with what was typed come first.
    starts = lambda item: not any(part.startswith(needle) for part in f"{item['name']} {item['username']}".lower().split())
    return sorted(found, key=lambda item: (starts(item), item['name'].lower()))[:20]


def _label(business, model):
    from .ui.metadata import view_for
    return view_for(model).get('label') or model.__name__


def _title(business, tx, user, model, record_id):
    from .identity import text as text_id
    record = business.policy.record(tx, user, model, record_id)
    title = getattr(record, 'title', None) if 'title' in business.policy.fields(user, model) else None
    return {'model': business.registry.name_for(model), 'id': text_id(record_id), 'label': _label(business, model), 'title': title or text_id(record_id)}


def shown(business, tx, user, row, names):
    model = business.registry.models.get(row['model'])
    readable = model is not None and can_read(business, tx, user, model, row['record_id'])
    actor = names.get(row['actor'], 'Someone')
    target = _title(business, tx, user, model, row['record_id']) if readable else None
    text = (f"{actor} mentioned you on {target['label']} {target['title']}" if target
            else f"{actor} mentioned you on a record you can't open")
    return {'id': row['id'], 'kind': row['kind'], 'at': row['at'], 'read': row['read_at'] is not None, 'actor': actor,
            'record': target, 'text': text}


def unread(connection, user_id):
    return connection.execute('SELECT count(*) FROM evlet_inbox WHERE user_id=? AND hidden=0 AND read_at IS NULL', (user_id,)).fetchone()[0]


def route(business, scope, body, user):
    """GET /api/_inbox (?limit=&offset=), GET /api/_inbox/count, POST /api/_inbox/read {"ids": [...]} or {"all": true}.
    Each person reads and marks only their own rows."""
    from urllib.parse import parse_qs
    path, method = scope['path'], scope['method']
    with business.database.transaction() as tx:
        if path == '/api/_inbox/count' and method == 'GET':
            return 200, {'unread': unread(tx.connection, user.id)}, []
        if path == '/api/_inbox/read' and method == 'POST':
            checked_body(body, {'ids', 'all'}, 'Send ids or all', field='ids')
            now = int(time.time())
            if body.get('all') is True:
                tx.connection.execute('UPDATE evlet_inbox SET read_at=? WHERE user_id=? AND read_at IS NULL', (now, user.id))
            else:
                ids = body.get('ids')
                if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(item) is not int for item in ids):
                    raise ValidationError({'ids': 'Send 1 to 100 inbox ids'})
                tx.connection.execute(f'UPDATE evlet_inbox SET read_at=? WHERE user_id=? AND read_at IS NULL AND id IN ({", ".join("?" for _ in ids)})',
                                      (now, user.id, *ids))
            publish(business, tx, user.id)
            return 200, {'unread': unread(tx.connection, user.id)}, []
        if path != '/api/_inbox' or method != 'GET':
            raise AccessError(404, 'Route not found')
        query = parse_qs(scope.get('query_string', b'').decode('latin-1'))
        limit, offset = query.get('limit', [str(PAGE)])[0], query.get('offset', ['0'])[0]
        if not re.fullmatch('[0-9]{1,3}', limit) or not re.fullmatch('[0-9]{1,9}', offset) or not 1 <= int(limit) <= 100:
            raise ValidationError({'query': 'Use a limit of 1 to 100 and a nonnegative offset'})
        rows = tx.connection.execute('SELECT * FROM evlet_inbox WHERE user_id=? AND hidden=0 ORDER BY id DESC LIMIT ? OFFSET ?',
                                     (user.id, int(limit), int(offset))).fetchall()
        actors = sorted({row['actor'] for row in rows if row['actor'] is not None})
        names = {}
        if actors:
            names = {row['id']: row['name'] for row in tx.connection.execute(
                "SELECT u.id, COALESCE(NULLIF(p.full_name, ''), u.username) AS name FROM evlet_users u LEFT JOIN evlet_user_profile p ON p.user_id=u.id "
                f'WHERE u.id IN ({", ".join("?" for _ in actors)})', actors)}
        items = [shown(business, tx, user, row, names) for row in rows]
        total = tx.connection.execute('SELECT count(*) FROM evlet_inbox WHERE user_id=? AND hidden=0', (user.id,)).fetchone()[0]
        return 200, {'items': items, 'total': total, 'unread': unread(tx.connection, user.id)}, []


def send_emails(business, inbox_ids):
    """After the entry's transaction committed: email each mentioned person who wants it, through the
    mail connector's outgoing account. The message names the record only to someone who can read it,
    and carries none of the note. The outcome is kept on the row (sent, failed, no_email, no_account)."""
    from .connectors import ConnectionFailed
    for inbox_id in inbox_ids:
        with business.database.transaction() as tx:
            row = tx.connection.execute("SELECT * FROM evlet_inbox WHERE id=? AND email='pending'", (inbox_id,)).fetchone()
            if row is None:
                continue
            person = _user(tx, row['user_id'])
            profile = tx.connection.execute('SELECT email FROM evlet_user_profile WHERE user_id=?', (row['user_id'],)).fetchone() \
                if person else None
            address = profile['email'] if profile is not None else None
            actor = tx.connection.execute("SELECT COALESCE(NULLIF(p.full_name, ''), u.username) AS name FROM evlet_users u "
                                          'LEFT JOIN evlet_user_profile p ON p.user_id=u.id WHERE u.id=?', (row['actor'],)).fetchone()
            item = shown(business, tx, person, row, {row['actor']: actor['name'] if actor else 'Someone'}) if person else None
        outcome = 'no_email'
        if item is not None and address:
            if not business.connectors.accounts('smtp'):
                outcome = 'no_account'
            else:
                target = item['record']
                # A link only to a record they can read, and only when the app has one public address.
                origins = sorted(getattr(business.auth, 'origins', ()))
                link = f"\n\nOpen it: {origins[0]}/app/{target['model']}/{target['id']}" if target and len(origins) == 1 else ''
                try:
                    business.connectors.send_mail(address, item['text'], item['text'] + '.' + link)
                    outcome = 'sent'
                except (ConnectionFailed, ValueError):
                    outcome = 'failed'
        with business.database.transaction() as tx:
            tx.connection.execute('UPDATE evlet_inbox SET email=? WHERE id=?', (outcome, inbox_id))


__all__ = ['initialize', 'record', 'route', 'mentionable', 'send_emails', 'unread']
