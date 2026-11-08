"""Small SQLite persistence API with explicit transaction boundaries."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .fields import ACTIVE, EMPTY_LAYER, Link, MISSING, Reference, RichText
from . import audit
from .identity import RecordId, new_id, sequential
from .models import ValidationError, identifier
from .query import SqlQuery
from .naming import has_title, title_of
from . import images, ledger, periods
from .ledger import is_ledger
from .permissions import CALLER
from .sequences import name_new, raw_write

# Most rows one list call returns, and most rows one child collection may hold in an editor.
ROW_LIMIT = 100


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


CONSTRAINT = 'A unique value or linked record constraint was violated'


def conflict(registry, exc):
    """The Evlet error for a database integrity failure, the same on SQLite and PostgreSQL: ValidationError
    naming the fields of a unique Meta.indexes entry (evlet/indexes.py), ConflictError for a write an
    append-only table refuses (evlet/ledger.py), and ConflictError for any other unique or foreign key."""
    from .indexes import violated
    diag = getattr(exc, 'diag', None)
    text = (diag.message_primary or '') if diag is not None else str(exc)
    if text.endswith(' ' + ledger.APPEND_ONLY):
        return ConflictError(f'{text}: its rows are never changed or deleted')
    return violated(registry, exc) or ConflictError(CONSTRAINT)


@contextmanager
def using(database):
    """Make a database's Layer (its instance fields and added options) active. Nested uses of the same
    database keep the Layer the outer use took, so one request sees one set of fields throughout."""
    current = ACTIVE.get()
    if current is not None and current[0] is database:
        yield
        return
    token = ACTIVE.set((database, database.layer))
    try:
        yield
    finally:
        ACTIVE.reset(token)


class Database:
    # Set by Controls: this database's instance fields and added options, and the instance timezone.
    layer = EMPTY_LAYER
    timezone = 'UTC'

    def __init__(self, path, registry, *, migrations=None, must_exist=False):
        if str(path) == ":memory:":
            raise ValueError("Use a SQLite file (a temporary file in tests)")
        configured = Path(path)
        self.path = configured.parent.resolve() / configured.name if must_exist else configured
        self.must_exist = must_exist
        self.registry = registry
        self.migrations = None if migrations is None else tuple(migrations)

    @contextmanager
    def _connection(self):
        if self.must_exist and (self.path.is_symlink() or self.path.absolute().resolve() != self.path.absolute()):
            raise sqlite3.OperationalError('Database paths must not use symbolic links')
        target = self.path.absolute().as_uri() + '?mode=rw' if self.must_exist else self.path
        connection = sqlite3.connect(target, timeout=5, uri=self.must_exist)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            # REPLACE then fires DELETE triggers, so append-only tables refuse it too (evlet/ledger.py).
            connection.execute("PRAGMA recursive_triggers = ON")
            with using(self):
                yield connection
        finally:
            connection.close()

    def activated(self):
        """Make this database's instance fields and added options active for code outside a transaction,
        such as a thread started inside a request or records built before writing them."""
        return using(self)

    def owns(self, session):
        """Check the physical database before accepting a bound feature write."""
        if not isinstance(session.connection, sqlite3.Connection):
            return False
        path = session.connection.execute('PRAGMA database_list').fetchone()[2]
        return Path(path).resolve() == self.path.resolve()

    def columns(self, connection, table):
        return {row['name'] for row in connection.execute(f'PRAGMA table_info({identifier(table)})')}

    def initialize(self):
        from .migrations import initialize
        initialize(self)

    def migrate(self, migrations, *, recompute_posted=False, allow_destructive=False, allow_maintenance=False):
        from .migrations import migrate
        return migrate(self, migrations, recompute_posted=recompute_posted, allow_destructive=allow_destructive,
                       allow_maintenance=allow_maintenance)

    def plan(self, migrations):
        """What migrate(migrations) would do, without writing: a MigrationPlan whose text() lists the pending
        migrations and any difference between them and the models."""
        from .migrations import plan
        return plan(self, migrations)

    @contextmanager
    def transaction(self):
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield Session(connection, self.registry, self.timezone)
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise conflict(self.registry, exc) from exc
            except BaseException:
                connection.rollback()
                raise

    def create(self, model, /, **values):
        with self.transaction() as session:
            return session.create(model, **values)

    def get(self, model, record_id):
        with self._connection() as connection:
            return Session(connection, self.registry).get(model, record_id)

    def update(self, model, record_id, /, **changes):
        with self.transaction() as session:
            return session.update(model, record_id, **changes)

    def delete(self, model, record_id):
        with self.transaction() as session:
            session.delete(model, record_id)

    def related(self, record, field_name):
        with self._connection() as connection:
            return Session(connection, self.registry).related(record, field_name)

    def list(self, model, *, limit=50, offset=0, filters=None, order_by=None, search=""):
        with self._connection() as connection:
            return Session(connection, self.registry).list(
                model, limit=limit, offset=offset, filters=filters, order_by=order_by, search=search)

    def count(self, model, *, filters=None, search=""):
        with self._connection() as connection:
            return Session(connection, self.registry).count(model, filters=filters, search=search)

    def aggregate(self, model, *, measures, group_by=(), filters=None, limit=None, after=None):
        """Exact aggregates over all records; see docs/measures.md. With `limit` (and `after`), one page of
        groups in a stable order, for trusted code that walks any number of groups."""
        from .measures import aggregate
        with self._connection() as connection:
            return aggregate(Session(connection, self.registry), model, measures, group_by, filters, limit, after)

    def measures(self, model, record_id=None, *, filters=None):
        """Declared Meta.measures: model-level values, or per-record values when record_id is given."""
        from .measures import measures
        with self._connection() as connection:
            connection.execute("BEGIN")
            return measures(Session(connection, self.registry), model, record_id, filters)

    def page(self, model, **query):
        """Return records and their filtered count from one read snapshot."""
        with self._connection() as connection:
            connection.execute("BEGIN")
            session = Session(connection, self.registry)
            records = session.list(model, **query)
            total = session.count(model, filters=query.get('filters'), search=query.get('search', ''))
            return records, total

    def close(self):
        """SQLite connections already close at the end of each operation."""

    def check(self):
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()


# The most ids one tx.reserve_ids call hands out.
RESERVE_LIMIT = 100_000


def ledger_id(value):
    """A ledger row id: a positive integer."""
    if type(value) is not int and not isinstance(value, RecordId) or not 1 <= value <= 2**63 - 1:
        raise ValueError('Use the integer id of a row')
    return int(value)


def refuse_timestamps(model, values):
    for name in ('created_at', 'modified_at'):
        if name in values and name in model.__fields__:
            raise ValidationError({name: f'{name} is filled by the framework'})


def today(zone):
    """Today's date in a timezone, as YYYY-MM-DD."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc if zone == 'UTC' else ZoneInfo(zone)).date().isoformat()


def storage_columns(model, values, names):
    """Column names and storage values for the named fields; a Reference field fills two columns."""
    columns, stored = [], []
    for name in names:
        field = model.__fields__[name]
        for (column, _), value in zip(field.columns(name), field.to_columns(values.get(name))):
            columns.append(column)
            stored.append(value)
    return columns, stored


def insert(connection, table, names, values, *, id_title=False, actor=None):
    """Insert one row under a new record id and return the id. A taken id (a collision of the 21 random
    bits within one millisecond) inserts nothing, so another id is drawn; other constraints still raise.
    With id_title, a standard title with nothing else to show takes the text id (evlet/naming.py), in the
    same statement. The row's created and modified times and `actor` (evlet/audit.py) go in the same statement."""
    position = names.index('title') if id_title and values[names.index('title')] is None else None
    stamp_names, stamp_values = audit.created(actor)
    names, values = [*names, *stamp_names], [*values, *stamp_values]
    record_id = _insert(connection, table, names, values, position)
    if position is not None and sequential():
        connection.execute(f'UPDATE {table} SET "title" = ? WHERE "id" = ?', (str(record_id), record_id))
    return record_id


def stored_values(model, values, record_id=None):
    """values with the standard title filled in, ready for storage_columns."""
    if not has_title(model):
        return values
    return {**values, 'title': title_of(model, values, record_id)}


def _insert(connection, table, names, values, title_position=None):
    if sequential():
        # Recorded fixtures: the table's own autoincrement numbers rows 1, 2, 3 and never reuses one.
        sql = f'INSERT INTO {table} ({", ".join(identifier(name) for name in names)}) VALUES ({", ".join("?" for _ in names)})' \
            if names else f'INSERT INTO {table} DEFAULT VALUES'
        return RecordId(connection.execute(sql, values).lastrowid)
    columns = ', '.join(['"id"', *(identifier(name) for name in names)])
    placeholders = ', '.join('?' for _ in range(len(names) + 1))
    for _ in range(8):
        record_id = new_id()
        if title_position is not None:
            values = [*values[:title_position], str(record_id), *values[title_position + 1:]]
        cursor = connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT ("id") DO NOTHING',
                                    [record_id, *values])
        if cursor.rowcount == 1:
            return record_id
    raise ConflictError('Could not find a free record id')


class Session:
    """Available inside Database.transaction(); do not retain it afterwards."""

    def __init__(self, connection, registry, timezone='UTC'):
        self.connection = connection
        self.registry = registry
        self.timezone = timezone
        self._maintenance = False
        # The user id writes are recorded under (evlet/audit.py); None is the engine.
        self.actor = None
        # Period locks read once per transaction, and whether unlocked_periods() is active (evlet/periods.py).
        self._period_locks = None
        self._periods_unlocked = False
        # Ledger ids tx.reserve_ids handed out and insert_many has not used yet, as {model: {id}}.
        self._reserved = {}

    @contextmanager
    def maintenance(self):
        """Audited repairs only: raw writes to finite-v1 documents skip settlement, so the
        caller must restore calculated invariants (for example by resaving through Context)."""
        previous, self._maintenance = self._maintenance, True
        try:
            yield self
        finally:
            self._maintenance = previous

    def unlocked_periods(self, reason):
        """For migrations and data repairs only: write records dated in closed periods inside this block.
        The reason is required; it is logged and written to the audit log. See evlet/periods.py."""
        return periods.unlocked(self, reason)

    def _guard(self, model):
        if not self._maintenance and (getattr(self.registry.root_of(model), 'confirmable', False)
                                     or getattr(self.registry.root_of(model), 'track_changes', False)):
            raise ValueError(f'{model.__name__} has lifecycle or history tracking; write it through Context')
        if not self._maintenance and model in getattr(self.registry, 'effect_targets', ()):
            raise ValueError(f'{model.__name__} rows are posted by the effects of other records and are read only; '
                             'use session.maintenance() for an audited repair')
        plan_for = getattr(self.registry, 'plan_for', None)
        if not self._maintenance and plan_for is not None and plan_for(model) is not None:
            raise ValueError(f'{model.__name__} is a finite-v1 document; write it through Context.create/update '
                             '(document settlement) or inside session.maintenance() for an audited repair')

    def _state(self, model, value, current):
        """A State changes only through its moves; raw writes may set it only inside maintenance()."""
        name = getattr(model, '__state__', None)
        if name is None or self._maintenance or value is MISSING:
            return
        expected = model.__declared_fields__[name].default if current is None else current._values.get(name)
        if value != expected:
            raise ValueError(f'{model.__name__}.{name} changes only through its moves; use Context.move(), '
                             'or session.maintenance() for an audited repair')

    def create(self, model, /, **values):
        if is_ledger(model):
            return self.get(model, self.insert_many(model, [values])[0])
        self._guard(model)
        self._state(model, values.get(getattr(model, '__state__', None) or '', MISSING), None)
        table = identifier(self.registry.name_for(model))
        record = model(**values)
        numbers = raw_write(self, model, values, None, record._values)
        # A trusted session may give a naming field a value (imports); otherwise the series names it.
        name = name_new(self.connection, self.registry, model, record._values, today(self.timezone), record=record)
        if name:
            numbers = {**numbers, self.registry.naming[model].field: name}
        if numbers:
            record = model(**{**record._values, **numbers})
        periods.check(self, model, after=record._values)
        self.check_references(model, record._values)
        names, stored = storage_columns(model, stored_values(model, record._values), model.__fields__)
        created = insert(self.connection, table, names, stored, id_title=has_title(model), actor=self.actor)
        images.settle(self, model, created, record._values)
        return self.get(model, created)

    def get(self, model, record_id):
        try:
            record_id = ledger_id(record_id) if is_ledger(model) else RecordId(record_id)
        except ValueError as exc:
            raise ValidationError({"id": str(exc) if is_ledger(model) else "Use a record id"}) from None
        table = identifier(self.registry.name_for(model))
        row = self.connection.execute(
            f'SELECT * FROM {table} WHERE "id" = ?', (record_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Record not found")
        return model._from_row(row)

    def list(self, model, *, limit=50, offset=0, filters=None, order_by=None, search=""):
        if type(limit) is not int or not 1 <= limit <= ROW_LIMIT:
            raise ValidationError({"limit": f"Use an integer between 1 and {ROW_LIMIT}"})
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise ValidationError({"offset": "Use a non-negative 64-bit integer"})
        table = identifier(self.registry.name_for(model))
        # Trusted code: `in` lists may be long (evlet/query.py TRUSTED_IN_VALUES).
        query = SqlQuery(self.registry, model, trusted=True)
        where, values = query.where(filters, search)
        order = query.ordering(order_by)
        rows = self.connection.execute(
            f'SELECT "record".* FROM {table} AS "record"{where}{order} LIMIT ? OFFSET ?',
            [*values, limit, offset],
        ).fetchall()
        return [model._from_row(row) for row in rows]

    def update(self, model, record_id, /, **changes):
        if is_ledger(model):
            return self._ledger_update(model, record_id, changes)
        self._guard(model)
        current = self.get(model, record_id)
        self._state(model, changes.get(getattr(model, '__state__', None) or '', MISSING), current)
        record = model(**{**current._values, **changes})
        raw_write(self, model, changes, current, record._values)
        if changes:
            periods.check(self, model, before=current._values, after=record._values)
            self.check_references(model, {name: record._values[name] for name in changes})
            table = identifier(self.registry.name_for(model))
            values = stored_values(model, record._values, current.id)
            names, stored = storage_columns(model, values, [*changes, *(['title'] if has_title(model) and 'title' not in changes else [])])
            stamp_names, stamp_values = audit.modified(self.actor)
            names, stored = [*names, *stamp_names], [*stored, *stamp_values]
            assignments = ', '.join(f'{identifier(name)} = ?' for name in names)
            self.connection.execute(f'UPDATE {table} SET {assignments} WHERE "id" = ?', stored + [current.id])
            if any(isinstance(model.__fields__.get(name), RichText) for name in changes):
                images.settle(self, model, current.id, record._values)
        return self.get(model, current.id)

    def check_references(self, model, values):
        """Reference fields have no SQL foreign key, so each write checks that the record exists."""
        for name, value in values.items():
            if isinstance(model.__declared_fields__.get(name), Reference) and value is not None:
                # A caller cannot name a model it has no surface on (Meta.api = None).
                target = (self.registry.exposed if CALLER.get() else self.registry.models).get(value[0])
                if target is None:
                    raise ValidationError({name: 'Use a registered model name'})
                if self.connection.execute(f'SELECT 1 FROM {identifier(value[0])} WHERE "id" = ?', (value[1],)).fetchone() is None:
                    raise ValidationError({name: 'The referenced record does not exist'})

    def add_standard_columns(self, model):
        """For a data migration that fills names: give the model's table the standard name and title
        columns now instead of in framework step 0003, which runs after app migrations. Idempotent."""
        from .naming import add_columns
        postgres = not isinstance(self.connection, sqlite3.Connection)
        add_columns(self.connection, postgres, self.registry.name_for(model), model)
        audit.add_columns(self.connection, postgres, self.registry.name_for(model))

    def delete(self, model, record_id):
        if is_ledger(model):
            return self._ledger_delete(model, record_id)
        self._guard(model)
        current = self.get(model, record_id)
        periods.check(self, model, before=current._values)
        record_id = current.id
        table = identifier(self.registry.name_for(model))
        self.connection.execute(f'DELETE FROM {table} WHERE "id" = ?', (record_id,))

    # Ledger models (evlet/ledger.py)

    def insert_many(self, model, rows):
        """Insert rows of a ledger model with one prepared statement and return their ids, in order.

        Each row is a dict of field values, validated like a create. A row may also give an `id` that
        tx.reserve_ids returned in this transaction, so rows of one batch can link to each other. Timestamps
        are filled, closed periods are checked, and a journaled model gets one `created` entry per row."""
        model = self._ledger(model, 'insert_many')
        if isinstance(rows, (str, bytes, dict)):
            raise ValidationError({'rows': 'Use a list of rows, each a dict of field values'})
        rows = list(rows)
        if not rows:
            return []
        table = identifier(self.registry.name_for(model))
        stamps = {key: ledger.now() for key in ('created_at', 'modified_at') if key in model.__fields__}
        names = list(model.__fields__)
        records, stored, given = [], [], []
        reserved = self._reserved.get(model, set())
        for index, values in enumerate(rows):
            if not isinstance(values, dict):
                raise ValidationError({'rows': f'Row {index + 1} is not a dict of field values'})
            refuse_timestamps(model, values)
            values = dict(values)
            row_id = values.pop('id', None)
            if row_id is not None:
                try:
                    row_id = ledger_id(row_id)
                except ValueError as exc:
                    raise ValidationError({'id': f'Row {index + 1}: {exc}'}) from None
                if row_id not in reserved or row_id in given:
                    raise ValidationError({'id': f'Row {index + 1}: id {row_id} was not reserved with tx.reserve_ids in this '
                                                 'transaction, or is used twice'})
            given.append(row_id)
            record = model(**values, **stamps)
            periods.check(self, model, after=record._values)
            records.append(record)
            stored.append(storage_columns(model, record._values, names)[1])
        columns = storage_columns(model, records[0]._values, names)[0]
        sql = f'INSERT INTO {table} ({", ".join(identifier(name) for name in columns)}) VALUES ({", ".join("?" for _ in columns)})' \
            if columns else f'INSERT INTO {table} DEFAULT VALUES'
        if any(row_id is not None for row_id in given):
            # Reserved ids are below the table's next id, so rows without one still take new ids.
            named = ', '.join(identifier(name) for name in ('id', *columns))
            with_id = f'INSERT INTO {table} ({named}) VALUES ({", ".join("?" for _ in range(len(columns) + 1))})'
            ids = []
            for row_id, values in zip(given, stored):
                if row_id is None:
                    ids.append(self.connection.execute(sql, values).lastrowid)
                else:
                    self.connection.execute(with_id, [row_id, *values])
                    ids.append(row_id)
            reserved.difference_update(given)
        elif isinstance(self.connection, sqlite3.Connection):
            # One writer at a time (BEGIN IMMEDIATE) and AUTOINCREMENT ids: the rows take the next ids in order.
            self.connection.executemany(sql, stored)
            last = self.connection.execute('SELECT last_insert_rowid()').fetchone()[0]
            ids = list(range(last - len(stored) + 1, last + 1))
        else:
            ids = [self.connection.execute(sql, values).lastrowid for values in stored]
        if model in getattr(self.registry, 'journals', {}):
            for record_id, record in zip(ids, records):
                self._ledger_journal(model, record_id, 'created', None, record)
        return ids

    def reserve_ids(self, model, count):
        """Reserve `count` new ids of a ledger model and return them in increasing order, so rows inserted in
        this transaction can name each other (or themselves) before insert_many stores them with those ids.

        Rows inserted later without an id never take a reserved one. On SQLite the ids follow the table's last
        id, under the write transaction (BEGIN IMMEDIATE), and a rollback returns them. On PostgreSQL they come
        from the table's identity sequence (nextval), so they may have gaps and a rollback leaves a gap."""
        model = self._ledger(model, 'reserve_ids')
        if type(count) is not int or not 1 <= count <= RESERVE_LIMIT:
            raise ValidationError({'count': f'Use an integer between 1 and {RESERVE_LIMIT}'})
        table = self.registry.name_for(model)
        if isinstance(self.connection, sqlite3.Connection):
            if not self.connection.in_transaction:
                raise ValueError('tx.reserve_ids works inside database.transaction()')
            row = self.connection.execute('SELECT seq FROM sqlite_sequence WHERE name = ?', (table,)).fetchone()
            top = max(row[0] if row else 0, self.connection.execute(f'SELECT coalesce(max("id"), 0) FROM {identifier(table)}').fetchone()[0])
            if row is None:
                self.connection.execute('INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)', (table, top + count))
            else:
                self.connection.execute('UPDATE sqlite_sequence SET seq = ? WHERE name = ?', (top + count, table))
            ids = list(range(top + 1, top + count + 1))
        else:
            ids = self.connection.reserve(table, count)
        self._reserved.setdefault(model, set()).update(ids)
        return ids

    def _ledger(self, model, operation):
        model = self.registry.resolve(model)
        if not is_ledger(model):
            raise ValueError(f'{operation} writes ledger models (Meta.ledger = True); {model.__name__} is a record model')
        return model

    def _ledger_update(self, model, record_id, changes):
        if model.__append_only__:
            raise ValueError(f'{model.__name__} is append only: its rows are never changed. Insert a new row instead')
        refuse_timestamps(model, changes)
        current = self.get(model, record_id)
        if 'modified_at' in model.__fields__:
            changes = {**changes, 'modified_at': ledger.now()} if changes else changes
        record = model(**{**current._values, **changes})
        if not changes:
            return current
        periods.check(self, model, before=current._values, after=record._values)
        table = identifier(self.registry.name_for(model))
        names, stored = storage_columns(model, record._values, list(changes))
        assignments = ', '.join(f'{identifier(name)} = ?' for name in names)
        self.connection.execute(f'UPDATE {table} SET {assignments} WHERE "id" = ?', [*stored, current.id])
        if model in getattr(self.registry, 'journals', {}):
            self._ledger_journal(model, current.id, 'changed', current, record)
        return self.get(model, current.id)

    def _ledger_delete(self, model, record_id):
        if model.__append_only__:
            raise ValueError(f'{model.__name__} is append only: its rows are never deleted. Insert a row that reverses it instead')
        current = self.get(model, record_id)
        periods.check(self, model, before=current._values)
        self.connection.execute(f'DELETE FROM {identifier(self.registry.name_for(model))} WHERE "id" = ?', (current.id,))
        if model in getattr(self.registry, 'journals', {}):
            self._ledger_journal(model, current.id, 'deleted', current, None)

    def _ledger_journal(self, model, record_id, kind, before, after):
        """A Journal entry for a ledger row, as the engine or `session.actor`, in this transaction."""
        from . import journal
        if not getattr(self, '_journal_ready', False):
            journal.ensure_table(self.connection, postgres=not isinstance(self.connection, sqlite3.Connection),
                                 hashed=[self.registry.name_for(item) for item, value in self.registry.journals.items() if value.tamper_evident])
            self._journal_ready = True
        old = {} if before is None else before._values
        new = {} if after is None else after._values
        fields = {name: {'old': field.serialize(old.get(name)), 'new': field.serialize(new.get(name))}
                  for name, field in model.__fields__.items() if old.get(name) != new.get(name)}
        data = {'fields': fields} if fields else {}
        actor = journal.ENGINE if self.actor is None else str(self.actor)
        journal.insert(self.connection, self.registry, model, record_id, kind, actor, data)

    def lock(self, *keys, timeout=None):
        """Hold named locks until this transaction commits or rolls back, such as
        `tx.lock(("stock", item_id, warehouse_id))`. Each key is a nonempty tuple of text and integer parts.

        Take every lock the transaction needs in one call, at its start. On PostgreSQL each key is a
        transaction advisory lock, taken in a fixed order so callers cannot deadlock each other; with a
        `timeout` in seconds, a longer wait raises LockTimeout and the transaction rolls back. On SQLite a
        write transaction already holds the database's only write lock, so the call only checks the keys."""
        if not keys:
            raise ValueError('tx.lock needs at least one key, such as ("stock", item_id, warehouse_id)')
        numbers = sorted({ledger.lock_hash(key) for key in keys})
        milliseconds = ledger.lock_timeout(timeout)
        if isinstance(self.connection, sqlite3.Connection):
            if not self.connection.in_transaction:
                raise ValueError('tx.lock works inside database.transaction()')
            return
        self.connection.lock(numbers, milliseconds)

    def related(self, record, field_name):
        self.registry.name_for(type(record))
        field = record.__fields__.get(field_name)
        if not isinstance(field, Link):
            raise ValidationError({field_name: 'Expected a Link field'})
        record_id = getattr(record, field_name)
        return None if record_id is None else self.get(field.target, record_id)

    def aggregate(self, model, *, measures, group_by=(), filters=None, limit=None, after=None):
        """Exact aggregates inside this transaction, so they see its uncommitted writes; the same arguments
        and result as Database.aggregate (docs/measures.md). Trusted: no permissions apply.

        Without `limit`, more than 500 groups is an error. With `limit` (1 to 10,000), one page of groups
        ordered by their keys, empty values first; pass the page's last group as `after` for the next page."""
        from .measures import aggregate
        return aggregate(self, model, measures, group_by, filters, limit, after)

    def count(self, model, *, filters=None, search=""):
        table = identifier(self.registry.name_for(model))
        where, values = SqlQuery(self.registry, model, trusted=True).where(filters, search)
        return self.connection.execute(
            f'SELECT count(*) FROM {table} AS "record"{where}', values,
        ).fetchone()[0]
