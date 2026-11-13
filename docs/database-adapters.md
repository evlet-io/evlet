# Database adapters

Evlet ships with one database: SQLite, in the four-file layout described in
[performance.md](performance.md). SQLite needs no server, backs up as files, and is the
right fit for the small and medium businesses Evlet is built for.

Evlet 0.4 also had a PostgreSQL adapter. It was removed in 0.5. Its checks were spread
over 26 framework files, 21 test files ran twice, it had its own copy of the schema engine,
and it rewrote SQLite SQL into PostgreSQL SQL token by token. That rewriting caused parity
bugs, for example a hardcoded list of tables whose inserts returned an id.

What replaces it is one boundary:

> Models and actions talk to the query builder. The query builder talks to SQLite. A
> PostgreSQL or MariaDB dialect can be added later, behind the same query builder.

This page describes that boundary. It is for people who work on the framework or write a
dialect. App developers do not need it: they use `Database`, `Session` and `Context` as before.

## The parts

| Module | What it owns |
| --- | --- |
| `evlet/qb.py` | The query builder: database-neutral statements (select, insert, update, delete, upsert) and conditions, as plain Python objects. No SQL text. |
| `evlet/adapter.py` | The connection interface every dialect provides: `run(statement)`, the schema methods migrations use, ids, locks, savepoints and error reading. The neutral shapes (`Column`, `Index`, `TableShape`, `TableDefinition`, `IntegrityFailure`, `Result`) and the error types framework code catches. |
| `evlet/sqlite.py` | The SQLite dialect: the compiler that turns statements into SQLite SQL, and `SQLiteConnection`. |
| `evlet/database.py` | `Database` (the SQLite database: files, connections, transactions) and the neutral record API, `Session`. |
| `evlet/writes.py` | SQLite only: the four files and the fair write line-up. |

Framework modules outside the dialect never write SQL text and never ask which database they
run on. A test, `tests/test_query_builder_boundary.py`, enforces this (see "The plan" below for
the modules that are still being moved).

## The query builder

Use the module by name, so each call reads as what it does:

```python
from . import qb

row = connection.run(qb.select('evlet_users', 'id', 'role').where(username=name)).one()
connection.run(qb.update('evlet_config', version=qb.col('version') + 1).where(id=1))
entry_id = connection.run(qb.insert('evlet_journal', model=name, record_id=record_id, kind=kind,
                                    actor=actor, at=at, data=text).returning_id()).id
```

### Statements

| Build | SQL it stands for |
| --- | --- |
| `qb.select(table, *columns)` | `SELECT`. No columns means every column. `table` is a name or `qb.table(name, alias)`. |
| `.where(*conditions, **equals)` | `WHERE`. Everything given must hold. `name=value` is equality, `name=None` is `IS NULL`. |
| `.join(table, on, left=False)` | `JOIN` or `LEFT JOIN`. |
| `.group_by(*columns)`, `.order_by(*orders)` | `GROUP BY`, `ORDER BY`. `'-name'` sorts down. `qb.asc(column, nulls='first')` and `qb.desc(column, nulls='last')` place empty values. |
| `.limit(n)`, `.offset(n)` | One page of rows. |
| `qb.insert(table, **values)` | `INSERT`. `.returning_id()` returns the new id as `Result.id`. `.returning(column)` returns another column, such as an upsert's counter. |
| `.on_conflict(*columns).do_nothing()` | Insert unless a row with the same key exists. |
| `.on_conflict(*columns).do_update(**values)` | Upsert. `qb.col('excluded.name')` is the value the insert tried to write. |
| `qb.insert_rows_from(table, columns, query)` | `INSERT ... SELECT`. |
| `qb.update(table, **values).where(...)` | `UPDATE`. |
| `qb.delete(table).where(...)` | `DELETE`. |

### Conditions and expressions

- Comparisons: `qb.eq`, `qb.ne`, `qb.lt`, `qb.le`, `qb.gt`, `qb.ge`, `qb.between`.
- Empty values: `qb.is_null`, `qb.not_null`.
- Lists and subqueries: `qb.in_(column, values_or_query)`, `qb.not_in`. An empty list matches no row.
- Text: `qb.contains` (matching case), `qb.contains_ci` (ignoring case), `qb.excludes_ci`
  (empty or not containing, ignoring case), `qb.starts_with`.
- Logic: `qb.and_`, `qb.or_`, `qb.not_`, `qb.exists`, `qb.not_exists`, `qb.TRUE`, `qb.FALSE`.
- Hierarchies: `qb.in_tree(column, table, parent, roots, include_roots=...)`, the rows at or
  below some roots following a parent link (team row scopes). A cycle stops the walk.
- Functions: `qb.count()`, `qb.count(column)`, `qb.sum`, `qb.min`, `qb.max`, `qb.coalesce`,
  `qb.nullif`, `qb.lower`, `qb.length`.
- Windows: `qb.over(qb.row_number(), partition_by=[...], order_by=[...])`.
- Other: `qb.col('alias.name')`, `qb.named(expression, 'name')`, `qb.case(column, {key: value})`,
  `qb.all_columns('alias')`, `qb.from_query(query, alias)`, and `+`, `-`, `*` on expressions.

### Rules

- The first argument of a condition names a column. The other arguments are values, always
  bound as parameters, unless they are `qb.col(...)` or another expression.
- Every table, column and alias name is checked with `evlet.models.identifier`.
- Statements are immutable: each method returns a new statement, so a base query can be reused.
- Nothing takes SQL text. A condition the builder cannot express is a missing builder
  function: add it to `qb.py` and to the dialect, with a conformance test.

`connection.run(statement)` returns a `Result`: `.one()` (a row or `None`), `.all()`,
`.value()` (the first column of the first row, or `None`), iteration over rows, `.rowcount`
and `.id`. Rows read by index and by column name.

`evlet/query.py` builds on the query builder: `SqlQuery.where(filters, search)` returns one
condition over the alias `"record"`, and `SqlQuery.ordering(order_by)` returns the orders.
`Policy.scope(user, model)` returns a row scope as a condition the same way.

## The schema half

Migrations and framework tables use named methods on the connection instead of DDL text:

| Method | Contract |
| --- | --- |
| `table_exists(name)`, `tables()`, `columns(table)` | What the database holds. |
| `inspect(table)` | A `TableShape`: columns (name, Evlet type, required, primary key, the table a column references) and indexes (name, columns, unique, partial). Migrations compare models against this one shape. |
| `broken_foreign_key()` | A table with a value its foreign key does not find, or `None`. |
| `create_table(table, columns)` | A model table: an integer `id` the database numbers, then the given `Column`s. |
| `add_column`, `rename_column`, `drop_column`, `cannot_drop_columns()` | Column changes, keeping data. |
| `set_required(table, column, required)`, `drop_reference(table, column)` | Constraint changes, keeping the stored rows. |
| `create_index(table, base, columns, unique=, partial=)`, `drop_index(name)` | Model indexes. `partial` names a column: only rows where it is set are covered. |
| `ensure_table(TableDefinition)`, `ensure_index(name, table, columns, unique=)` | Framework tables (`evlet_*`), created when missing. A `TableDefinition` lists columns, a numbered id, a primary key, unique groups and checks (query builder conditions). |
| `make_append_only`, `drop_append_only`, `is_append_only` | Refuse `UPDATE`, `DELETE` and replacing inserts on a table. |
| `guard_id`, `has_id_guard` | Refuse an `UPDATE` that changes a row's `id`. |

Ids, locks, transactions and errors:

| Method | Contract |
| --- | --- |
| `insert_many(table, columns, rows)` | Insert rows, return their new ids in order. |
| `reserve_ids(table, count)` | Ids of a ledger table later inserts never take. Only in a write transaction. |
| `lock(numbers, milliseconds)` | Named locks until the transaction ends. SQLite holds one write lock per file already, so it only checks that a transaction is open. |
| `snapshot()` | A read-only transaction whose reads all see one moment. |
| `savepoint(name)` | A context manager: on an error, the changes inside it are undone and the error raised again. |
| `integrity_failure(exc)` | For an integrity error, an `IntegrityFailure` (message, and for a unique violation the table and columns). `None` otherwise. |

Framework code catches `DatabaseError`, `IntegrityError` and `OperationalError` from
`evlet.adapter`. They are the DB-API classes from Python's `sqlite3` module. Another dialect
raises them, or subclasses, for its driver's errors, and never puts connection strings, SQL
values or server details in a message. A busy database raises `writes.Busy`.

## Adding a database later

A new database is two classes and a compiler:

1. **A `Database` subclass** that overrides how connections are opened and transactions run:
   `__init__`, `_connection()`, `transaction(store=None)`, `schema_transaction()`,
   `read_only()`, `owns(session)`, `upgrade_storage()`, `backup()`, `files()` and `close()`.
   Their contracts are in the docstrings of `evlet/database.py`. Writers must not interleave:
   Evlet relies on one write transaction at a time per database for sequences, ledgers and
   the job queue.
2. **A connection class** that implements `evlet.adapter.Connection`, with `run(statement)`
   calling its compiler.
3. **A compiler** for the query builder, like `Compiler` in `evlet/sqlite.py`. It is the only
   place that knows the database's SQL: placeholders, quoting, `RETURNING`, upserts,
   window functions, recursive queries, text matching and limits.

The app plugs it in explicitly where it builds its database today:

```python
database = MariaDBDatabase(settings.database_url, registry, migrations=migrations)
application = Application(database)
```

There is no entry point or automatic discovery.

Exact money stays in the framework: amounts are integers in minor units at a fixed scale (see
[finite-calculations.md](finite-calculations.md)). A dialect stores them in a signed 64-bit
integer column, never as a floating point number.

## Checking a dialect: the conformance tests

`tests/test_database_adapter.py` is the checklist. Each test uses only the query builder, the
connection interface and `Database` transactions:

- every statement and condition of the query builder returns the same rows: comparisons,
  empty values, lists, text matching, joins, subqueries, `EXISTS`, groups, aggregates,
  ordering with empty values first or last, pages, windows, derived tables, `CASE`,
  arithmetic, hierarchies with cycles, upserts, `INSERT ... SELECT` and row counts;
- names are checked and values never become SQL;
- values round-trip exactly, including 64-bit integers, bytes and money;
- foreign keys are enforced and ids come back from inserts, `insert_many` and `reserve_ids`;
- `inspect` returns the neutral shape, and every schema change keeps the stored data;
- framework table definitions enforce their keys, unique groups and checks;
- append-only tables and the id guard refuse what they should, with the right error;
- integrity errors become `ValidationError` and `ConflictError`, a busy database `writes.Busy`;
- savepoints and failed transactions roll back, schema changes included;
- a database written by Evlet 0.4.3 (`tests/fixtures/sqlite-0.4.3`) opens unchanged.

To check a new dialect, point the `adapter_database` fixture at its `Database` and run:

```sh
.venv/bin/python -m pytest tests/test_database_adapter.py
```

Then run the whole suite against it. The other tests build `Database` in `tests/conftest.py`
and their own fixtures, so a full run means changing those to build the new database.

## The plan

The move onto the query builder is done in steps, each its own pull request, so each stays
reviewable. `tests/test_query_builder_boundary.py` lists the modules not moved yet with the
number of raw `execute()` calls each still has. The numbers may only go down.

| Step | Modules | State |
| --- | --- | --- |
| 1 (0.5) | Remove PostgreSQL. Add the query builder, the SQLite compiler and the conformance tests. Move the data layer: `database.py` (the record API), `query.py`, `migrations.py`, `documents.py`, row scopes in `permissions.py`, and the record helpers `naming`, `audit`, `identity`, `ledger`, `periods`, `sequences`, `sections`, `images`, `lifecycle`, `journal`. | This pull request |
| 2 | Accounts and sign-in: `auth.py`, `settings.py`, `secrets.py`, `unsaved.py`. | Follow-up |
| 3 | Controls: `controls.py`, `controls_system.py`, `customization.py`, the rest of `permissions.py`. | Follow-up |
| 4 | Jobs and messages: `jobs.py`, `events.py`, `mailboxes.py`, `mentions.py`, `connectors.py`, `tools.py`, `devlink.py`, `services.py`. | Follow-up |
| 5 | Eva: `eva.py`, `assistants.py`. | Follow-up |
| 6 | The rest: `measures.py`, `business.py`, `effects.py`, `corrections.py`, `currencies.py`, `forms.py`, `printing.py`, `slips.py`, `framework.py`, `storage.py`, `object_storage.py`. Then remove the temporary text bridge (`SqlQuery.where_sql`, `ordering_sql`, `reference_sql`, `Policy.scope_sql`, `compile_condition`). | Follow-up |
| 7 | The instance tools that open SQLite files directly (`instances.py`, `tenancy.py`, `cli/backup.py`, `cli/install.py`, `cli/journal.py`, `cli/serve.py`, `cli/site.py`): decide which stay SQLite tools and which go through `Database`. | Follow-up |

Until a module is moved, it calls `execute()` with SQLite SQL, and the modules that still
consume filters or row scopes as text use the bridge. Both go away step by step. New framework
code uses the query builder from the start.
