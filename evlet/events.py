"""Transactional record invalidations with permission-filtered replay."""

from pathlib import Path
import re
import secrets

from .auth import AccessError
from .database import Session, NotFoundError
from .models import ValidationError


class EventStore:
    def __init__(self, database, *, retention=1000):
        if type(retention) is not int or not 100 <= retention <= 100000:
            raise ValueError('Retain between 100 and 100000 events')
        self.database, self.retention = database, retention

    def initialize(self):
        with self.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_events (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE, model TEXT NOT NULL, record_id INTEGER NOT NULL, deleted INTEGER NOT NULL)')

    def publish(self, tx, model, record_id, *, deleted=False, journal=False):
        """Record that a record changed (or was deleted), or with journal=True that its journal gained an
        entry; clients refresh the record or its journal. Stored as deleted = 0, 1 or 2."""
        if not self.database.owns(tx):
            raise ValueError('Event transaction belongs to another database')
        if type(record_id) is not int or not 1 <= record_id <= 2**63 - 1:
            raise ValueError('Use a valid record ID')
        token = secrets.token_hex(24)
        event_id = tx.connection.execute('INSERT INTO evlet_events(token,model,record_id,deleted) VALUES (?,?,?,?)', (token, self.database.registry.name_for(model), record_id, 1 if deleted else 2 if journal else 0)).lastrowid
        tx.connection.execute('DELETE FROM evlet_events WHERE id<=?', (event_id - self.retention,))
        return token

    def page(self, user, policy, *, after=None):
        if after is not None and after != '0' and (not isinstance(after, str) or not re.fullmatch('[0-9a-f]{48}', after)):
            raise ValidationError({'cursor': 'Invalid event cursor'})
        with self.database._connection() as db:
            db.execute('BEGIN')
            tx = Session(db, self.database.registry)
            latest = db.execute('SELECT id,token FROM evlet_events ORDER BY id DESC LIMIT 1').fetchone()
            end = latest['token'] if latest else '0'
            if after is None:
                return {'items': [], 'cursor': end, 'reset': False, 'more': False}
            current = 0 if after == '0' else db.execute('SELECT id FROM evlet_events WHERE token=?', (after,)).fetchone()
            if current is None:
                return {'items': [], 'cursor': end, 'reset': True, 'more': False}
            current = current if current == 0 else current[0]
            oldest = db.execute('SELECT min(id) FROM evlet_events').fetchone()[0]
            if oldest is not None and current < oldest - 1:
                return {'items': [], 'cursor': end, 'reset': True, 'more': False}
            rows = db.execute('SELECT * FROM evlet_events WHERE id>? ORDER BY id LIMIT 100', (current,)).fetchall()
            items = []
            for row in rows:
                model = self.database.registry.models.get(row['model'])
                if model is None or not policy.allows(user, model, 'read'):
                    continue
                if row['deleted'] == 1:
                    # A deleted row cannot be rechecked against a current row scope.
                    if policy.rule(user, model).scope is not None:
                        continue
                else:
                    try:
                        policy.record(tx, user, model, row['record_id'])
                    except (AccessError, NotFoundError):
                        continue
                item = {'model': row['model'], 'id': row['record_id'], 'deleted': row['deleted'] == 1}
                if row['deleted'] == 2:
                    item['journal'] = True
                items.append(item)
            return {'items': items, 'cursor': rows[-1]['token'] if rows else after, 'reset': False, 'more': bool(rows and latest and rows[-1]['id'] < latest['id'])}
