"""Unsaved changes: edits a person left on a saved record, kept apart from the record until Save.

Leaving a record with unsaved edits never changes the record. The browser sends the edited
writable fields here; the next visit offers to restore or discard them. Nothing here runs
validators, events, sequences, history or the journal. See docs/internal/unsaved-changes.md.
"""

import json
import re
import time

from . import qb
from .adapter import Column, TableDefinition
from .auth import AccessError
from .identity import path_id
from .models import ValidationError, checked_body

TABLE = TableDefinition('evlet_unsaved', (
    Column('user_id', 'INTEGER', required=True, references='evlet_users', on_delete='cascade'),
    Column('model', 'TEXT', required=True),
    Column('record_id', 'INTEGER', required=True),
    Column('changes', 'TEXT', required=True),
    Column('base_revision', 'TEXT', required=True),
    Column('saved_at', 'INTEGER', required=True),
    Column('size', 'INTEGER', required=True),
), primary_key=('user_id', 'model', 'record_id'))
TASK = 'evlet_unsaved_purge'
BATCH = 500
DAY = 86400
REVISION = re.compile(r'"[0-9a-f]{64}"')
# (default, smallest, largest) of each instance setting; Controls refuses values outside the range.
LIMITS = {
    'unsaved.server_days': (30, 1, 90),
    'unsaved.server_entries': (200, 10, 1000),
    'unsaved.browser_days': (7, 1, 30),
    'unsaved.browser_entries': (20, 1, 50),
    'unsaved.entry_kb': (256, 16, 1024),
}
TOO_LARGE = ('These unsaved changes are larger than {kb} KB, so they are not kept. '
             'They stay in this tab until you save or close it.')


def ensure(connection):
    """Create the table. Framework step 0004 and startup both call it."""
    connection.ensure_table(TABLE)
    connection.ensure_index('evlet_unsaved_saved_at', 'evlet_unsaved', ('saved_at',))


def autosave(model):
    """Meta.autosave: whether leaving a record of this model keeps its unsaved edits (True by default)."""
    value = getattr(getattr(model, 'Meta', None), 'autosave', True)
    if type(value) is not bool:
        raise ValueError(f'{model.__name__}: Meta.autosave is True or False')
    return value


def check_setting(key, value):
    """A limit as Controls saves it: a whole number in its safe range."""
    _, low, high = LIMITS[key]
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Use a whole number from {low} to {high}.')
    return value


def strictest(key, value):
    """A stored limit that no longer validates: clamped into its range, or the smallest."""
    _, low, high = LIMITS[key]
    return min(max(value, low), high) if type(value) is int else low


class Unsaved:
    def __init__(self, business):
        self.business, self.database = business, business.database
        if business.jobs is not None:
            from .jobs import Task
            if TASK in business.jobs.tasks:
                raise ValueError(f'The task name {TASK} is reserved')
            business.jobs.tasks[TASK] = Task(TASK, self.run_job, max_attempts=1)

    # ---------- setup and clean-up ----------

    def initialize(self):
        with self.database.transaction() as tx:
            ensure(tx.connection)
        self.purge()

    def schedule(self):
        """The daily clean-up schedule, created once when jobs are on."""
        jobs = self.business.jobs
        if jobs is None:
            return
        with self.database._connection() as connection:
            if connection.run(qb.select('evlet_schedules', qb.value(1)).where(name=TASK)).one():
                return
        jobs.schedule(TASK, TASK, {}, every=DAY)

    def limit(self, key):
        return self.business.controls.settings.get(key, LIMITS[key][0])

    def limits(self):
        return {key.removeprefix('unsaved.'): self.limit(key) for key in LIMITS}

    def run_job(self, tx, context, payload):
        self.purge_in(tx.connection, batches=20)

    def purge(self, *, now=None, batches=20):
        """Delete expired entries and each person's entries over the cap, a batch per transaction."""
        removed = 0
        for _ in range(batches):
            with self.database.transaction() as tx:
                count = self.purge_in(tx.connection, now=now, batches=1)
            removed += count
            if count < BATCH:
                break
        return removed

    def purge_in(self, connection, *, now=None, batches=1):
        now = int(time.time()) if now is None else now
        cutoff = now - self.limit('unsaved.server_days') * DAY
        removed = 0
        for _ in range(batches):
            rows = connection.run(qb.select('evlet_unsaved', 'user_id', 'model', 'record_id')
                                  .where(qb.lt('saved_at', cutoff)).order_by('saved_at').limit(BATCH)).all()
            for row in rows:
                connection.run(qb.delete('evlet_unsaved').where(user_id=row[0], model=row[1], record_id=row[2]))
            removed += len(rows)
            if len(rows) < BATCH:
                break
        cap = self.limit('unsaved.server_entries')
        for (user_id,) in connection.run(qb.select('evlet_unsaved', 'user_id').group_by('user_id').having(qb.gt(qb.count(), cap))
                                         .order_by('user_id').limit(BATCH)).all():
            removed += self.trim(connection, user_id, cap)
        return removed

    @staticmethod
    def trim(connection, user_id, cap):
        """Keep a person's newest `cap` entries."""
        rows = connection.run(qb.select('evlet_unsaved', 'model', 'record_id').where(user_id=user_id)
                              .order_by('-saved_at', 'model', 'record_id').limit(BATCH).offset(cap)).all()
        for row in rows:
            connection.run(qb.delete('evlet_unsaved').where(user_id=user_id, model=row[0], record_id=row[1]))
        return len(rows)

    # ---------- hooks from record writes ----------

    @staticmethod
    def record_deleted(connection, model_name, record_id):
        """Every person's entry for a deleted record, in the delete's transaction."""
        connection.run(qb.delete('evlet_unsaved').where(model=model_name, record_id=record_id))

    @staticmethod
    def record_saved(connection, user, model_name, record_id):
        """The saver's own entry, in the save's transaction."""
        if user is not None and getattr(user, 'id', 0):
            connection.run(qb.delete('evlet_unsaved').where(user_id=user.id, model=model_name, record_id=record_id))

    @staticmethod
    def clear_user(connection, user_id):
        connection.run(qb.delete('evlet_unsaved').where(user_id=user_id))

    # ---------- HTTP ----------

    def api(self, scope, body, user):
        path, method = scope['path'], scope['method']
        if path == '/api/_unsaved':
            if method != 'GET':
                raise AccessError(405, 'Method not allowed')
            return 200, self.info(user), []
        parts = path.split('/')
        if len(parts) != 5 or method not in ('GET', 'PUT', 'DELETE'):
            raise AccessError(404, 'Route not found')
        model = self.business.registry.models.get(parts[3])
        record_id = path_id(parts[4])
        if model is None or model in self.business.child_models or record_id is None:
            raise AccessError(404, 'Route not found')
        if not autosave(model):
            raise AccessError(404, "This model doesn't keep unsaved changes")
        name = parts[3]
        if method == 'DELETE':
            with self.database.transaction() as tx:
                self.record_saved(tx.connection, user, name, record_id)
            return 204, None, []
        if method == 'PUT':
            return self.put(user, model, name, record_id, body)
        return self.get(user, model, name, record_id)

    def info(self, user):
        """The person's limits, entry count and setting; /api/_settings/me carries it too."""
        with self.database._connection() as connection:
            count = connection.run(qb.select('evlet_unsaved', qb.count()).where(user_id=user.id)).value()
        return {'limits': self.limits(), 'count': int(count), 'enabled': self.enabled(user)}

    def enabled(self, user):
        return self.business.settings.core(user.id).get('keep_unsaved', True) is not False

    def access(self, tx, user, model, record_id):
        """The record when the person can read it and update it now; AccessError otherwise."""
        policy = self.business.policy
        record = policy.record(tx, user, model, record_id, 'read')
        policy.record(tx, user, model, record_id, 'update')
        return record

    def put(self, user, model, name, record_id, body):
        if not self.enabled(user):
            raise AccessError(403, 'You turned off keeping unsaved changes in Settings.')
        checked_body(body, {'changes', 'base_revision'}, 'Send changes and base_revision', exact=True)
        revision = body['base_revision']
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise ValidationError({'base_revision': 'Send the revision the record was opened with'})
        dropped = []
        changes = self.clean(user, model, body['changes'], '', dropped, strict=True)
        encoded = json.dumps(changes, separators=(',', ':'), ensure_ascii=False, sort_keys=True)
        size = len(encoded.encode())
        kb = self.limit('unsaved.entry_kb')
        if size > kb * 1024:
            raise AccessError(413, TOO_LARGE.format(kb=kb))
        now = int(time.time())
        with self.database.transaction() as tx:
            self.access(tx, user, model, record_id)
            if empty(changes):
                self.record_saved(tx.connection, user, name, record_id)
                return 200, {'saved_at': None, 'size': 0, 'dropped': dropped}, []
            tx.connection.run(
                qb.insert('evlet_unsaved', user_id=user.id, model=name, record_id=record_id, changes=encoded, base_revision=revision,
                          saved_at=now, size=size)
                .on_conflict('user_id', 'model', 'record_id')
                .do_update(changes=qb.col('excluded.changes'), base_revision=qb.col('excluded.base_revision'),
                           saved_at=qb.col('excluded.saved_at'), size=qb.col('excluded.size')))
            self.trim(tx.connection, user.id, self.limit('unsaved.server_entries'))
        return 200, {'saved_at': now, 'size': size, 'dropped': dropped}, []

    def get(self, user, model, name, record_id):
        with self.database.transaction() as tx:
            row = tx.connection.run(qb.select('evlet_unsaved', 'changes', 'base_revision', 'saved_at', 'size')
                                    .where(user_id=user.id, model=name, record_id=record_id)).one()
            policy = self.business.policy
            try:
                record = policy.record(tx, user, model, record_id, 'read')
            except AccessError as exc:
                # The person can't see the record any more: the entry goes, and the answer is the record's.
                refused = exc
                self.record_saved(tx.connection, user, name, record_id)
            else:
                refused = None
        if refused is not None:
            raise refused
        with self.database.transaction() as tx:
            if row is None:
                return 200, {'entry': None, 'dropped': []}, []
            if row['saved_at'] < int(time.time()) - self.limit('unsaved.server_days') * DAY:
                self.record_saved(tx.connection, user, name, record_id)
                return 200, {'entry': None, 'dropped': []}, []
            try:
                policy.record(tx, user, model, record_id, 'update')
            except AccessError:
                self.record_saved(tx.connection, user, name, record_id)
                return 200, {'entry': None, 'dropped': 'record'}, []
            dropped = []
            try:
                stored = json.loads(row['changes'])
            except ValueError:
                stored = {}
            changes = self.clean(user, model, stored, '', dropped, strict=False)
            if empty(changes):
                self.record_saved(tx.connection, user, name, record_id)
                return 200, {'entry': None, 'dropped': dropped or 'record'}, []
            stale = row['base_revision'].encode() != self.business.record_revision(tx, user, record)
        return 200, {'entry': {'changes': changes, 'base_revision': row['base_revision'], 'saved_at': row['saved_at'],
                               'size': row['size'], 'stale': stale}, 'dropped': dropped}, []

    def clean(self, user, model, node, prefix, dropped, *, strict, added=False):
        """Only the fields and rows the person may write now. Unknown names are refused (strict) or dropped."""
        def refuse(message):
            if strict:
                raise ValidationError({prefix.rstrip('.') or 'changes': message})
            return {}
        allowed = {'values', 'base', 'children', *(('key',) if added else ())}
        if not isinstance(node, dict) or set(node) - allowed:
            return refuse('Send values, base and children')
        values, base, children = node.get('values', {}), node.get('base', {}), node.get('children', {})
        if not all(isinstance(item, dict) for item in (values, base, children)):
            return refuse('Send values, base and children as objects')
        declared = model.__declared_fields__
        unknown = [key for key in (*values, *base) if key not in declared]
        if unknown and strict:
            raise ValidationError({prefix + unknown[0]: 'Unknown field'})
        policy = self.business.policy
        writable = policy.fields(user, model, write=True)
        result = {}
        kept = {key: value for key, value in values.items() if key in writable}
        dropped.extend(prefix + key for key in values if key in declared and key not in writable)
        if kept:
            result['values'] = kept
            kept_base = {key: value for key, value in base.items() if key in kept}
            if kept_base:
                result['base'] = kept_base
        if added and isinstance(node.get('key'), str) and len(node['key']) <= 64:
            result['key'] = node['key']
        owned = {child.name: child for child in self.business.children.values() if child.parent is model}
        tables = {}
        for table, change in children.items():
            child = owned.get(table)
            if child is None:
                if strict:
                    raise ValidationError({prefix + str(table): 'Unknown table'})
                continue
            if not isinstance(change, dict) or set(change) - {'added', 'changed', 'removed'}:
                if strict:
                    raise ValidationError({prefix + table: 'Send added, changed and removed rows'})
                continue
            added_rows, changed_rows, removed_rows = change.get('added', []), change.get('changed', {}), change.get('removed', [])
            if not isinstance(added_rows, list) or not isinstance(changed_rows, dict) or not isinstance(removed_rows, list):
                if strict:
                    raise ValidationError({prefix + table: 'Send added and removed as lists and changed by row id'})
                continue
            bad = [row for row in (*changed_rows, *removed_rows) if not isinstance(row, str) or path_id(row) is None]
            if bad:
                if strict:
                    raise ValidationError({prefix + table: 'Use row ids'})
                continue
            entry = {}
            can = lambda operation: policy.allows(user, child.model, 'read') and policy.allows(user, child.model, operation)
            if added_rows:
                if can('create'):
                    rows = [self.clean(user, child.model, row, f'{prefix}{table}.+.', dropped, strict=strict, added=True) for row in added_rows]
                    rows = [row for row in rows if row.get('values') or row.get('children')]
                    if rows:
                        entry['added'] = rows
                else:
                    dropped.append(f'{prefix}{table}.added')
            if changed_rows:
                if can('update'):
                    rows = {row: self.clean(user, child.model, value, f'{prefix}{table}.{row}.', dropped, strict=strict) for row, value in changed_rows.items()}
                    rows = {row: value for row, value in rows.items() if not empty(value)}
                    if rows:
                        entry['changed'] = rows
                else:
                    dropped.append(f'{prefix}{table}.changed')
            if removed_rows:
                if can('delete'):
                    entry['removed'] = list(dict.fromkeys(removed_rows))
                else:
                    dropped.append(f'{prefix}{table}.removed')
            if entry:
                tables[table] = entry
        if tables:
            result['children'] = tables
        return result

    # ---------- Controls ----------

    def summary(self):
        """Counts and sizes for Controls; never the contents."""
        with self.database._connection() as connection:
            total = connection.run(qb.select('evlet_unsaved', qb.count(), qb.coalesce(qb.sum('size'), 0), qb.min('saved_at'))).one()
            people = connection.run(
                qb.select(qb.table('evlet_unsaved', 'n'), 'u.id', 'u.username', qb.named(qb.count(), 'entries'),
                          qb.named(qb.sum('n.size'), 'bytes'), qb.named(qb.max('n.saved_at'), 'latest'))
                .join(qb.table('evlet_users', 'u'), qb.eq('u.id', qb.col('n.user_id')))
                .group_by('u.id', 'u.username').order_by('-entries', 'u.username').limit(200)).all()
        # Every count and size here is a whole number, whatever type the database sums to.
        return {'entries': int(total[0]), 'bytes': int(total[1]), 'oldest': total[2], 'limits': self.limits(),
                'people': [{'id': row['id'], 'username': row['username'], 'entries': int(row['entries']), 'bytes': int(row['bytes']), 'latest': row['latest']}
                           for row in people]}

    def clear_older(self, actor, body):
        if not isinstance(body, dict) or set(body) != {'days'} or type(body['days']) is not int or not 0 <= body['days'] <= 365:
            raise ValidationError({'days': 'Use a whole number of days from 0 to 365.'})
        cutoff = int(time.time()) - body['days'] * DAY
        from .controls import audit
        with self.database.transaction() as tx:
            count = tx.connection.run(qb.select('evlet_unsaved', qb.count()).where(qb.lt('saved_at', cutoff))).value()
            tx.connection.run(qb.delete('evlet_unsaved').where(qb.lt('saved_at', cutoff)))
            audit(tx.connection, actor.username, 'Records', 'Unsaved changes',
                  f'Cleared {count} unsaved {"entry" if count == 1 else "entries"} older than {body["days"]} days')
        return {'cleared': count, **self.summary()}


def empty(node):
    return not node.get('values') and not node.get('children')
