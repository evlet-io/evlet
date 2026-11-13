"""The SQLite adapter: every SQLite-specific schema, trigger, id and error detail (docs/database-adapters.md).

Evlet's own connections and the server-shell connections (writes.connect) are SQLiteConnection objects.
The four files and the write line-up are in evlet/writes.py; opening and transactions in evlet/database.py.
"""

from contextlib import contextmanager
import re
import sqlite3

from . import adapter, qb
from .adapter import Column, Index, IntegrityFailure, TableShape
from .models import identifier

# Dropping a column needs SQLite 3.35 or later.
DROP_COLUMN = (3, 35, 0)

# Append-only tables that kept their trigger names from before the adapter; others use evlet_<table>.
_APPEND_ONLY_PREFIX = {'evlet_journal': 'evlet_journal', 'evlet_document_revisions': 'evlet_revision', 'evlet_audit': 'evlet_audit'}

# One column as Evlet writes it in CREATE TABLE: "name" TYPE, then NOT NULL, then its foreign key.
_DEFINITION = r' (\w+)( NOT NULL)?( REFERENCES "[A-Za-z0-9_]+"\("id"\))?'


def column_sql(column):
    """A Column as SQLite column definition text."""
    sql = f'{identifier(column.name)} {column.type}'
    if column.required:
        sql += ' NOT NULL'
    if column.references:
        sql += f' REFERENCES {identifier(column.references)}("id")'
        if column.on_delete is not None:
            if column.on_delete not in ('cascade', 'set null'):
                raise ValueError("on_delete is 'cascade' or 'set null'")
            sql += ' ON DELETE ' + column.on_delete.upper()
    if column.default is not None:
        sql += ' DEFAULT ' + literal(column.default)
    return sql


def sqlite_version(connection):
    return connection.execute('SELECT sqlite_version()').fetchone()[0]


def _text(message):
    return message.replace("'", "''")


class SQLiteConnection(adapter.Connection, sqlite3.Connection):
    """A SQLite connection. It creates side tables in their own files (evlet/writes.py)."""

    def execute(self, sql, *args):
        from .writes import placed
        return super().execute(placed(sql) if sql.lstrip()[:6].upper() == 'CREATE' else sql, *args)

    def run(self, statement):
        sql, values = compile_statement(statement)
        return adapter.Result(self.execute(sql, values), returning=getattr(statement, 'returns', None) is not None)

    def snapshot(self):
        self.execute('BEGIN')

    # ---------- reading the schema ----------

    def _schemas(self):
        return [row[1] for row in super().execute('PRAGMA database_list').fetchall() if row[1] != 'temp']

    def table_exists(self, name):
        return any(self.execute(f"SELECT 1 FROM \"{schema}\".sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
                   for schema in self._schemas())

    def tables(self):
        names = set()
        for schema in self._schemas():
            names.update(row[0] for row in self.execute(
                f"SELECT name FROM \"{schema}\".sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'"))
        # The empty table a write transaction claims in each side file (writes.prepare) is the adapter's own.
        names.discard('evlet_turn')
        return names

    def columns(self, table):
        return [row[1] for row in self.execute(f'PRAGMA table_info({identifier(table)})')]

    def inspect(self, table):
        quoted = identifier(table)
        links = {}
        for row in self.execute(f'PRAGMA foreign_key_list({quoted})'):
            # Evlet's foreign keys point at "id" with no ON DELETE action; anything else reads as a different reference.
            links[row[3]] = row[2] if (row[4], row[6]) == ('id', 'NO ACTION') else f'{row[2]}({row[4]}) ON DELETE {row[6]}'
        columns = tuple(Column(row[1], row[2].upper(), bool(row[3]), bool(row[5]), links.get(row[1]))
                        for row in self.execute(f'PRAGMA table_info({quoted})'))
        indexes = tuple(
            Index(row[1], tuple(item[2] for item in self.execute('SELECT * FROM pragma_index_info(?)', (row[1],))),
                  bool(row[2]), bool(row[4]))
            for row in self.execute(f'PRAGMA index_list({quoted})').fetchall())
        return TableShape(columns, indexes)

    def broken_foreign_key(self):
        row = self.execute('PRAGMA foreign_key_check').fetchone()
        return None if row is None else row[0]

    # ---------- changing the schema ----------

    def ensure_table(self, definition):
        # Framework table names are written bare (they are checked identifiers), so a side table is placed in
        # its own file (evlet/writes.py).
        identifier(definition.name)
        parts = []
        if definition.numbered:
            parts.append('id INTEGER PRIMARY KEY AUTOINCREMENT')
        for column in definition.columns:
            parts.append(column_sql(column) + (' PRIMARY KEY' if column.primary else ''))
        if definition.primary_key:
            parts.append(f'PRIMARY KEY ({", ".join(identifier(name) for name in definition.primary_key)})')
        for names in definition.unique:
            parts.append(f'UNIQUE ({", ".join(identifier(name) for name in names)})')
        for check in definition.checks:
            parts.append(f'CHECK ({Compiler(literal=True).expression(check)})')
        self.execute(f'CREATE TABLE IF NOT EXISTS {definition.name} ({", ".join(parts)})')

    def ensure_index(self, name, table, columns, *, unique=False):
        identifier(name)
        identifier(table)
        covered = ', '.join(identifier(column) for column in columns)
        self.execute(f'CREATE {"UNIQUE " if unique else ""}INDEX IF NOT EXISTS {name} ON {table}({covered})')

    def create_table(self, table, columns):
        definitions = ['"id" INTEGER PRIMARY KEY AUTOINCREMENT', *(column_sql(column) for column in columns)]
        self.execute(f'CREATE TABLE {identifier(table)} ({", ".join(definitions)})')

    def drop_table(self, table):
        self.execute(f'DROP TABLE {identifier(table)}')

    def rename_table(self, old, new):
        self.execute(f'ALTER TABLE {identifier(old)} RENAME TO {identifier(new)}')

    def add_column(self, table, column, default=None):
        sql = f'ALTER TABLE {identifier(table)} ADD COLUMN {column_sql(column)}'
        if default is not None:
            sql += ' DEFAULT ' + self.execute('SELECT quote(?)', (default,)).fetchone()[0]
        self.execute(sql)

    def rename_column(self, table, old, new):
        self.execute(f'ALTER TABLE {identifier(table)} RENAME COLUMN {identifier(old)} TO {identifier(new)}')

    def drop_column(self, table, column):
        # SQLite refuses to drop a column that is part of a foreign key.
        self.drop_reference(table, column)
        self.execute(f'ALTER TABLE {identifier(table)} DROP COLUMN {identifier(column)}')

    def cannot_drop_columns(self):
        version = sqlite_version(self)
        if tuple(int(part) for part in version.split('.')[:3]) < DROP_COLUMN:
            return f'Removing a field needs SQLite 3.35 or later, and this server has {version}. Update SQLite to remove fields. Adding fields still works.'
        return None

    def set_required(self, table, column, required):
        self._edit_column(table, column, (lambda match: f' {match[1]} NOT NULL{match[3] or ""}') if required
                          else (lambda match: f' {match[1]}{match[3] or ""}'),
                          'making a field required needs it' if required else
                          'retiring a required field, dropping a Link field and making a field optional need it')

    def drop_reference(self, table, column):
        self._edit_column(table, column, lambda match: f' {match[1]}{match[2] or ""}',
                          'retiring a Link field and dropping a Link field need it')

    def _edit_column(self, table, column, change, why):
        """Change one column's constraints in its table's CREATE statement, keeping the stored rows.

        This is SQLite's documented procedure for constraint changes that leave stored content as it is: the
        statement is edited in place and the schema version bumped, inside the open transaction, so a
        rollback restores it. Only columns Evlet created can be edited."""
        from .migrations import SchemaError
        sql = self.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        match = re.compile(re.escape(identifier(column)) + _DEFINITION).search(sql)
        if match is None:
            raise SchemaError(f'{table}.{column}: the column definition was not created by Evlet')
        edited = sql[:match.start()] + identifier(column) + change(match) + sql[match.end():]
        if edited == sql:
            return
        version = self.execute('PRAGMA schema_version').fetchone()[0]
        self.execute('PRAGMA writable_schema = ON')
        try:
            self.execute("UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?", (edited, table))
            self.execute(f'PRAGMA schema_version = {int(version) + 1}')
        except sqlite3.DatabaseError as exc:
            # SQLite builds in defensive mode (SQLITE_DBCONFIG_DEFENSIVE) refuse schema edits.
            raise SchemaError(f'{table}: this SQLite build does not allow changing column constraints in place ({exc}); {why}') from exc
        finally:
            self.execute('PRAGMA writable_schema = OFF')

    def create_index(self, table, base, columns, *, unique=False, partial=None):
        name, suffix = base, 1
        while self.execute('SELECT 1 FROM sqlite_master WHERE name = ?', (name,)).fetchone():
            suffix += 1
            name = f'{base}_{suffix}'
        covered = ', '.join(identifier(column) for column in columns)
        sql = f'CREATE {"UNIQUE " if unique else ""}INDEX {identifier(name)} ON {identifier(table)} ({covered})'
        if partial is not None:
            sql += f' WHERE {identifier(partial)} IS NOT NULL'
        self.execute(sql)
        return name

    def drop_index(self, name):
        self.execute(f'DROP INDEX {identifier(name)}')

    # ---------- append-only tables and fixed ids ----------

    @staticmethod
    def _append_only_names(table):
        prefix = _APPEND_ONLY_PREFIX.get(table, f'evlet_{table}')
        return tuple(f'{prefix}_no_{event}' for event in ('update', 'delete', 'replace'))

    def make_append_only(self, table, message):
        quoted = identifier(table)
        refuse = f"BEGIN SELECT RAISE(ABORT, '{_text(message)}'); END"
        update, delete, replace = (identifier(name) for name in self._append_only_names(table))
        self.execute(f'CREATE TRIGGER IF NOT EXISTS {update} BEFORE UPDATE ON {quoted} {refuse}')
        self.execute(f'CREATE TRIGGER IF NOT EXISTS {delete} BEFORE DELETE ON {quoted} {refuse}')
        # REPLACE deletes the old row without firing DELETE triggers (unless recursive triggers are on, as Evlet's
        # own connections have them), so an insert may not reuse a key: the primary key or a UNIQUE column.
        shape = self.inspect(table)
        keys = [tuple(column.name for column in shape.columns if column.primary)]
        keys += [index.columns for index in shape.indexes if index.unique and index.name.startswith('sqlite_autoindex_')
                 and index.columns != keys[0]]
        exists = ' OR '.join(f'EXISTS (SELECT 1 FROM {quoted} WHERE '
                             + ' AND '.join(f'{identifier(name)} = NEW.{identifier(name)}' for name in key) + ')'
                             for key in keys if key)
        self.execute(f'CREATE TRIGGER IF NOT EXISTS {replace} BEFORE INSERT ON {quoted} WHEN {exists} {refuse}')

    def drop_append_only(self, table):
        for name in self._append_only_names(table):
            self.execute(f'DROP TRIGGER IF EXISTS {identifier(name)}')

    def is_append_only(self, table):
        from .migrations import SchemaError
        expected = set(self._append_only_names(table))
        present = {row[0] for row in self.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (table,))}
        found = present & expected
        if found and found != expected:
            raise SchemaError(f'{table}: the append-only triggers are incomplete; run explicit migrations')
        return bool(found)

    def guard_id(self, table, message):
        self.execute(f'CREATE TRIGGER IF NOT EXISTS {identifier(f"evlet_{table}_fixed_id")} BEFORE UPDATE OF "id" ON {identifier(table)} '
                     f"WHEN NEW.\"id\" IS NOT OLD.\"id\" BEGIN SELECT RAISE(ABORT, '{_text(message)}'); END")

    def has_id_guard(self, table):
        return self.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                            (f'evlet_{table}_fixed_id',)).fetchone() is not None

    # ---------- ids and locks ----------

    def insert_many(self, table, columns, rows):
        # One writer at a time and AUTOINCREMENT ids: the rows take the next ids in order.
        rows = [tuple(row) for row in rows]
        sql, _ = compile_statement(qb.insert(table, **{name: None for name in columns}))
        self.executemany(sql, rows)
        last = self.execute('SELECT last_insert_rowid()').fetchone()[0]
        return list(range(last - len(rows) + 1, last + 1))

    @contextmanager
    def savepoint(self, name):
        identifier(name)
        self.execute(f'SAVEPOINT {name}')
        try:
            yield
        except BaseException as exc:
            try:
                self.execute(f'ROLLBACK TO SAVEPOINT {name}')
                self.execute(f'RELEASE SAVEPOINT {name}')
            except Exception:
                # The connection itself failed; the enclosing transaction rolls back.
                raise exc from None
            raise
        self.execute(f'RELEASE SAVEPOINT {name}')

    def reserve_ids(self, table, count):
        # The ids follow the table's last id, under the write transaction, and a rollback returns them.
        if not self.in_transaction:
            raise ValueError('tx.reserve_ids works inside database.transaction()')
        row = self.execute('SELECT seq FROM sqlite_sequence WHERE name = ?', (table,)).fetchone()
        top = max(row[0] if row else 0, self.execute(f'SELECT coalesce(max("id"), 0) FROM {identifier(table)}').fetchone()[0])
        if row is None:
            self.execute('INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)', (table, top + count))
        else:
            self.execute('UPDATE sqlite_sequence SET seq = ? WHERE name = ?', (top + count, table))
        return list(range(top + 1, top + count + 1))

    def lock(self, numbers, milliseconds=None):
        # A write transaction already holds the file's only write lock, so there is nothing more to take.
        if not self.in_transaction:
            raise ValueError('tx.lock works inside database.transaction()')

    # ---------- errors ----------

    def integrity_failure(self, exc):
        if not isinstance(exc, sqlite3.IntegrityError):
            return None
        message = str(exc)
        # "UNIQUE constraint failed: table.a, table.b"
        match = re.fullmatch(r'UNIQUE constraint failed: (.+)', message)
        pairs = [part.strip().split('.', 1) for part in match[1].split(',')] if match else []
        table = pairs[0][0] if pairs and len(pairs[0]) == 2 else None
        columns = tuple(pair[1] for pair in pairs if len(pair) == 2 and pair[0] == table)
        return IntegrityFailure(message, table, columns)


# ---------- the dialect: query builder statements as SQLite SQL ----------

class Compiler:
    """Turns one evlet/qb.py statement into SQLite SQL text and its bound values, in order."""

    COMPARE = {'=', '!=', '<', '<=', '>', '>='}
    ARITHMETIC = {'+', '-', '*', '/', '%'}

    def __init__(self, *, literal=False):
        # literal: write values into the SQL (table definitions take no parameters); otherwise bind them.
        self.literal = literal
        self.values = []

    def statement(self, item):
        if isinstance(item, qb.Select):
            return self.select(item)
        if isinstance(item, qb.Insert):
            return self.insert(item)
        if isinstance(item, qb.Update):
            return self.update(item)
        if isinstance(item, qb.Delete):
            return self.delete(item)
        raise TypeError(f'Not a query builder statement: {type(item).__name__}')

    # Statements

    def select(self, query):
        source = query.table
        if query.columns:
            columns = ', '.join(self.output(item) for item in query.columns)
        else:
            columns = f'{identifier(source.alias or source.name)}.*' if isinstance(source, qb.Table) else '*'
        sql = f'SELECT {"DISTINCT " if query.unique else ""}{columns} FROM {self.table(source)}'
        for join in query.joins:
            sql += f' {"LEFT " if join.left else ""}JOIN {self.table(join.table)} ON {self.expression(join.on)}'
        sql += self.where(query.conditions)
        if query.groups:
            sql += ' GROUP BY ' + ', '.join(self.expression(item) for item in query.groups)
        if query.having_conditions:
            if not query.groups:
                raise ValueError('.having() needs .group_by()')
            sql += ' HAVING ' + ' AND '.join(self.expression(item) for item in query.having_conditions)
        if query.orders:
            sql += ' ORDER BY ' + ', '.join(self.order(item) for item in query.orders)
        if query.count is not None:
            sql += ' LIMIT ' + self.expression(qb.expression(query.count))
        if query.skip is not None:
            if query.count is None:
                sql += ' LIMIT -1'
            sql += ' OFFSET ' + self.expression(qb.expression(query.skip))
        return sql

    def insert(self, statement):
        sql = f'INSERT INTO {identifier(statement.table)}'
        if statement.source is not None:
            source = statement.source
            if statement.conflict is not None and not source.conditions:
                # SQLite reads "SELECT ... ON CONFLICT" as a join unless the SELECT has a WHERE.
                source = source.where(qb.TRUE)
            sql += f' ({", ".join(identifier(name) for name in statement.columns)}) {self.select(source)}'
        elif statement.values:
            names = ', '.join(identifier(name) for name, _ in statement.values)
            sql += f' ({names}) VALUES ({", ".join(self.expression(item) for _, item in statement.values)})'
        else:
            sql += ' DEFAULT VALUES'
        conflict = statement.conflict
        if conflict is not None:
            target = f' ({", ".join(identifier(name) for name in conflict.columns)})' if conflict.columns else ''
            if conflict.updates is None:
                sql += f' ON CONFLICT{target} DO NOTHING'
            else:
                sql += f' ON CONFLICT{target} DO UPDATE SET ' + self.assignments(conflict.updates)
        if statement.returns is not None:
            sql += f' RETURNING {identifier(statement.returns)}'
        return sql

    def update(self, statement):
        return f'UPDATE {identifier(statement.table)} SET {self.assignments(statement.assignments)}{self.where(statement.conditions)}'

    def delete(self, statement):
        return f'DELETE FROM {identifier(statement.table)}{self.where(statement.conditions)}'

    # Parts

    def table(self, source):
        if isinstance(source, qb.Derived):
            return f'({self.select(source.query)}) AS {identifier(source.alias)}'
        return identifier(source.name) + (f' AS {identifier(source.alias)}' if source.alias else '')

    def where(self, conditions):
        if not conditions:
            return ''
        return ' WHERE ' + ' AND '.join(self.expression(item) for item in conditions)

    def assignments(self, pairs):
        return ', '.join(f'{identifier(name)} = {self.expression(item)}' for name, item in pairs)

    def output(self, item):
        if isinstance(item, qb.Named):
            return f'{self.expression(item.expression)} AS {identifier(item.name)}'
        return self.expression(item)

    def order(self, item):
        sql = self.expression(item.expression) + (' DESC' if item.descending else ' ASC')
        if item.nulls is not None:
            if item.nulls not in ('first', 'last'):
                raise ValueError("Use nulls='first' or 'last'")
            sql += ' NULLS ' + item.nulls.upper()
        return sql

    def expression(self, item):
        if isinstance(item, qb.Column):
            return (f'{identifier(item.table)}.' if item.table else '') + identifier(item.name)
        if isinstance(item, qb.AllColumns):
            return f'{identifier(item.table)}.*'
        if isinstance(item, qb.Value):
            if self.literal:
                return literal(item.value)
            self.values.append(item.value)
            return '?'
        if isinstance(item, qb.Compare):
            if item.operator not in self.COMPARE:
                raise ValueError(f'Unknown comparison: {item.operator}')
            return f'{self.expression(item.left)} {item.operator} {self.expression(item.right)}'
        if isinstance(item, qb.IsNull):
            return f'{self.expression(item.expression)} IS {"NOT " if item.negated else ""}NULL'
        if isinstance(item, qb.In):
            return self.within(item)
        if isinstance(item, qb.Between):
            return f'{self.expression(item.expression)} BETWEEN {self.expression(item.low)} AND {self.expression(item.high)}'
        if isinstance(item, qb.Text):
            return self.text(item)
        if isinstance(item, qb.InTree):
            return self.in_tree(item)
        if isinstance(item, qb.All):
            return '(' + ' AND '.join(self.expression(part) for part in item.items) + ')' if item.items else '1=1'
        if isinstance(item, qb.AnyOf):
            return '(' + ' OR '.join(self.expression(part) for part in item.items) + ')' if item.items else '1=0'
        if isinstance(item, qb.Not):
            return f'NOT ({self.expression(item.item)})'
        if isinstance(item, qb.Exists):
            return f'{"NOT " if item.negated else ""}EXISTS ({self.select(item.query)})'
        if isinstance(item, qb.Select):
            return f'({self.select(item)})'
        if isinstance(item, qb.Arithmetic):
            if item.operator not in self.ARITHMETIC:
                raise ValueError(f'Unknown arithmetic: {item.operator}')
            return f'({self.expression(item.left)} {item.operator} {self.expression(item.right)})'
        if isinstance(item, qb.Function):
            return self.function(item)
        if isinstance(item, qb.Case):
            pairs = ' '.join(f'WHEN {self.expression(key)} THEN {self.expression(value)}' for key, value in item.pairs)
            return f'CASE {self.expression(item.expression)} {pairs} END'
        if isinstance(item, qb.When):
            pairs = ' '.join(f'WHEN {self.expression(condition)} THEN {self.expression(value)}' for condition, value in item.pairs)
            return f'CASE {pairs} ELSE {self.expression(item.otherwise)} END'
        if isinstance(item, qb.Window):
            window = []
            if item.partition_by:
                window.append('PARTITION BY ' + ', '.join(self.expression(part) for part in item.partition_by))
            if item.order_by:
                window.append('ORDER BY ' + ', '.join(self.order(part) for part in item.order_by))
            return f'{self.function(item.function)} OVER ({" ".join(window)})'
        raise TypeError(f'Not a query builder expression: {type(item).__name__}')

    def function(self, item):
        if item.name not in qb.FUNCTIONS:
            raise ValueError(f'Unknown function: {item.name}')
        if item.name == 'count' and not item.arguments:
            return 'count(*)'
        if item.name == 'count_distinct':
            return f'count(DISTINCT {self.expression(item.arguments[0])})'
        return f'{item.name}({", ".join(self.expression(part) for part in item.arguments)})'

    def within(self, item):
        column = self.expression(item.expression)
        keyword = 'NOT IN' if item.negated else 'IN'
        if isinstance(item.values, qb.Select):
            return f'{column} {keyword} ({self.select(item.values)})'
        if not item.values:
            return '1=1' if item.negated else '1=0'
        return f'{column} {keyword} ({", ".join(self.expression(part) for part in item.values)})'

    def in_tree(self, item):
        if not item.roots:
            return '1=0'
        table, parent = identifier(item.table), identifier(item.parent)
        marks = ', '.join(self.expression(qb.Value(root)) for root in item.roots)
        start = f'SELECT id FROM {table} WHERE {"id" if item.include_roots else parent} IN ({marks})'
        # The range join keeps a database from hashing the join and scanning the table at each level; SQLite
        # plans both forms alike. UNION (not UNION ALL) stops at a cycle.
        return (f'{self.expression(item.expression)} IN (WITH RECURSIVE evlet_team(id) AS ({start} UNION SELECT below.id '
                f'FROM evlet_team JOIN {table} AS below ON below.{parent} >= evlet_team.id AND below.{parent} <= evlet_team.id) '
                'SELECT id FROM evlet_team)')

    def text(self, item):
        column = self.expression(item.expression)
        if item.kind == 'contains':
            return f'instr({column}, {self.expression(item.text)}) > 0'
        if item.kind == 'contains_ci':
            return f'instr(lower({column}), lower({self.expression(item.text)})) > 0'
        if item.kind == 'excludes_ci':
            return f'({column} IS NULL OR instr(lower({column}), lower({self.expression(item.text)})) = 0)'
        if item.kind == 'starts_with':
            first = self.expression(item.text)
            return f'substr({column}, 1, length({first})) = {self.expression(item.text)}'
        if item.kind == 'ends_with':
            # Each placeholder is compiled where it stands, so the bound values stay in order.
            longer = f'length({column}) >= length({self.expression(item.text)})'
            tail = f'substr({column}, length({column}) - length({self.expression(item.text)}) + 1)'
            return f'({longer} AND {tail} = {self.expression(item.text)})'
        raise ValueError(f'Unknown text match: {item.kind}')


def literal(value):
    """A value written into SQL text: only for table definitions, whose values come from framework code."""
    if value is None:
        return 'NULL'
    if type(value) in (int, bool):
        return str(int(value))
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f'Not a literal value: {type(value).__name__}')


def compile_statement(statement):
    """(sql, values) for one query builder statement."""
    compiler = Compiler()
    sql = compiler.statement(statement)
    return sql, compiler.values
