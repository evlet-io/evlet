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
from . import images, periods
from .permissions import CALLER
from .sequences import name_new, raw_write

# Most rows one list call returns, and most rows one child collection may hold in an editor.
ROW_LIMIT = 100


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


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
                raise ConflictError("A unique value or linked record constraint was violated") from exc
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

    def aggregate(self, model, *, measures, group_by=(), filters=None):
        """Exact aggregates over all records; see docs/measures.md."""
        from .measures import aggregate
        with self._connection() as connection:
            return aggregate(Session(connection, self.registry), model, measures, group_by, filters)

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
            record_id = RecordId(record_id)
        except ValueError:
            raise ValidationError({"id": "Use a record id"}) from None
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
        query = SqlQuery(self.registry, model)
        where, values = query.where(filters, search)
        order = query.ordering(order_by)
        rows = self.connection.execute(
            f'SELECT "record".* FROM {table} AS "record"{where}{order} LIMIT ? OFFSET ?',
            [*values, limit, offset],
        ).fetchall()
        return [model._from_row(row) for row in rows]

    def update(self, model, record_id, /, **changes):
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
        self._guard(model)
        current = self.get(model, record_id)
        periods.check(self, model, before=current._values)
        record_id = current.id
        table = identifier(self.registry.name_for(model))
        self.connection.execute(f'DELETE FROM {table} WHERE "id" = ?', (record_id,))

    def related(self, record, field_name):
        self.registry.name_for(type(record))
        field = record.__fields__.get(field_name)
        if not isinstance(field, Link):
            raise ValidationError({field_name: 'Expected a Link field'})
        record_id = getattr(record, field_name)
        return None if record_id is None else self.get(field.target, record_id)

    def count(self, model, *, filters=None, search=""):
        table = identifier(self.registry.name_for(model))
        where, values = SqlQuery(self.registry, model).where(filters, search)
        return self.connection.execute(
            f'SELECT count(*) FROM {table} AS "record"{where}', values,
        ).fetchone()[0]
