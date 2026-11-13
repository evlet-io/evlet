"""Transactional record invalidations with permission-filtered replay."""

import re
import secrets

from . import qb
from .adapter import Column, TableDefinition
from .auth import AccessError
from .database import Session, NotFoundError
from .identity import RecordId, text as text_id
from .models import ValidationError


TABLE = TableDefinition('evlet_events', (
    Column('token', 'TEXT', required=True), Column('model', 'TEXT', required=True),
    Column('record_id', 'INTEGER', required=True), Column('deleted', 'INTEGER', required=True),
), numbered=True, unique=(('token',),))


class EventStore:
    def __init__(self, database, *, retention=1000):
        if type(retention) is not int or not 100 <= retention <= 100000:
            raise ValueError('Retain between 100 and 100000 events')
        self.database, self.retention = database, retention

    def initialize(self):
        with self.database.transaction() as tx:
            tx.connection.ensure_table(TABLE)

    def publish(self, tx, model, record_id, *, deleted=False, journal=False):
        """Record that a record changed (or was deleted), or with journal=True that its journal gained an
        entry; clients refresh the record or its journal. Stored as deleted = 0, 1 or 2."""
        if not self.database.owns(tx):
            raise ValueError('Event transaction belongs to another database')
        try:
            record_id = RecordId(record_id)
        except ValueError:
            raise ValueError('Use a valid record ID') from None
        token = secrets.token_hex(24)
        event_id = tx.connection.run(qb.insert('evlet_events', token=token, model=self.database.registry.name_for(model), record_id=record_id,
                                               deleted=1 if deleted else 2 if journal else 0).returning_id()).id
        tx.connection.run(qb.delete('evlet_events').where(qb.le('id', event_id - self.retention)))
        return token

    def publish_config(self, tx, version):
        """Tell open browsers that Controls saved a change (config.changed), with the new config version."""
        token = secrets.token_hex(24)
        event_id = tx.connection.run(qb.insert('evlet_events', token=token, model='_config', record_id=version, deleted=0).returning_id()).id
        tx.connection.run(qb.delete('evlet_events').where(qb.le('id', event_id - self.retention)))
        return token

    def publish_user(self, tx, channel, user_id):
        """Tell one person's open browsers that something of theirs changed (their inbox): only they
        receive it, as {"type": "<channel without _>.changed"}, with no other detail."""
        token = secrets.token_hex(24)
        event_id = tx.connection.run(qb.insert('evlet_events', token=token, model=channel, record_id=user_id, deleted=0).returning_id()).id
        tx.connection.run(qb.delete('evlet_events').where(qb.le('id', event_id - self.retention)))
        return token

    def page(self, user, policy, *, after=None):
        if after is not None and after != '0' and (not isinstance(after, str) or not re.fullmatch('[0-9a-f]{48}', after)):
            raise ValidationError({'cursor': 'Invalid event cursor'})
        with self.database._connection() as db:
            db.snapshot()
            tx = Session(db, self.database.registry)
            latest = db.run(qb.select('evlet_events', 'id', 'token').order_by('-id').limit(1)).one()
            end = latest['token'] if latest else '0'
            if after is None:
                return {'items': [], 'cursor': end, 'reset': False, 'more': False}
            current = 0 if after == '0' else db.run(qb.select('evlet_events', 'id').where(token=after)).value()
            if current is None:
                return {'items': [], 'cursor': end, 'reset': True, 'more': False}
            oldest = db.run(qb.select('evlet_events', qb.min('id'))).value()
            if oldest is not None and current < oldest - 1:
                return {'items': [], 'cursor': end, 'reset': True, 'more': False}
            rows = db.run(qb.select('evlet_events').where(qb.gt('id', current)).order_by('id').limit(100)).all()
            items = []
            for row in rows:
                if row['model'] == '_config':
                    items.append({'type': 'config.changed', 'version': row['record_id']})
                    continue
                if row['model'] == '_inbox':
                    if row['record_id'] == user.id:
                        items.append({'type': 'inbox.changed'})
                    continue
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
                item = {'model': row['model'], 'id': text_id(row['record_id']), 'deleted': row['deleted'] == 1}
                if row['deleted'] == 2:
                    item['journal'] = True
                items.append(item)
            return {'items': items, 'cursor': rows[-1]['token'] if rows else after, 'reset': False, 'more': bool(rows and latest and rows[-1]['id'] < latest['id'])}
