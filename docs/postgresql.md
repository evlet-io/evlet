# PostgreSQL

Milestone 8 adds `PostgreSQLDatabase`, using the optional Psycopg 3 driver. SQLite
remains the default and the core wheel still imports and runs without a driver.
Psycopg supplies parameter binding, protocol handling, and maintained connection
management. The optional Psycopg pool reuses connections and bounds server usage;
there is no ORM, broker, or mandatory database service. Sustained-load testing of
short-lived connections exposed connection failures, so the adapter uses the
maintained driver pool instead of implementing its own pool.

```sh
python -m pip install --require-hashes -r requirements-postgres.lock
```

For package consumers, install `evlet[postgres]` in the application's environment.
Keep credentials in an operator-managed secret or service environment, not source
files, command arguments, browser metadata, or logs.

```python
import os
from evlet import PostgreSQLDatabase

# registry contains the application's ordinary Python models.
database = PostgreSQLDatabase(os.environ['APP_DATABASE_DSN'], registry,
                              migrations=migrations, timeout=5, max_connections=10)
database.initialize()
```

Provision a dedicated empty UTF-8 database and a dedicated role first. The adapter
never creates or selects tenant databases from HTTP input. It uses the `public`
schema, validates identifiers, binds values, and rejects identifiers over 63 bytes.
Each instance lazily opens a pool of at most `max_connections` connections (default
10, allowed range 1 to 100), with at most 100 waiting callers. Construct a database
instance inside each worker process; inherited instances fail after a fork. Call
`database.close()` after stopping requests and workers. ASGI lifespan shutdown does
this automatically. A later operation may reopen the pool. The pool rolls back
unfinished transactions and discards session state before reuse. `timeout` bounds
pool checkout, connection establishment,
statements, lock waits, and idle transactions, in seconds, between 1 and 300.

## Supported behavior

The public database/session CRUD, relationships, typed filters, search, pagination,
exact fixed-scale numbers, and explicit migration APIs are unchanged. Integer and
scaled numeric storage use BIGINT; generated IDs use PostgreSQL identity columns.
Nullable sort order matches SQLite explicitly. Text matching follows the database's
collation and case rules; test non-ASCII search against the chosen database locale.
PostgreSQL does not store NUL characters in text. IDs may have gaps after rollback.

Text fields with `index=True` or `unique=True` must declare `max_length <= 500`,
for example `Text(max_length=500, unique=True)` or
`LongText(max_length=500, index=True)`. Wider indexed definitions fail with
`SchemaError` when constructing the database, before opening a connection. This
conservative limit covers 2,000 UTF-8 bytes on standard 8 KiB PostgreSQL pages and
keeps ordinary and unique B-tree indexes usable for every validated value. Values
above the declared character limit return the usual field validation error (HTTP
422). Unindexed text keeps its declared length; SQLite's limits are unchanged.
Do not shorten an existing field definition without checking stored data and the
supported migration restrictions. See the upstream
[B-tree limits](https://www.postgresql.org/docs/14/btree-intro.html).

Authenticated single-application HTTP routes also support accounts, permissions,
actions, child rows, private database attachments, local private files, jobs,
schedules, events, and discovery. The Playground selects PostgreSQL when
`EVLET_POSTGRES_DSN` is set, otherwise it keeps its existing SQLite configuration.
The app and `.venv/bin/python -m examples.playground.migrate` share this selection.
The migration command upgrades only the selected database and closes its pool on
exit. SQLite upgrades retain their automatic backup. For PostgreSQL, take and
verify an operator-managed backup before running the command; it does not create
one or open the configured SQLite file.
Use a disposable database for this development example; browser setup is enabled.

The existing hostname tenant catalog and provisioning commands remain SQLite-only.
They reject a PostgreSQL factory. Do not substitute a shared database or use schema
names supplied by requests. Running separate authenticated PostgreSQL applications
behind separately configured exact hosts is possible; automatic PostgreSQL tenant
provisioning and catalog integration are not provided in this release. The local
catalog remains single-node. No SQLite-to-PostgreSQL data conversion is automatic.

## Transactions and concurrency

Each explicit write transaction acquires a database-local transaction advisory lock
before reading state. This preserves existing multi-record action, account,
attachment collection, schedule, and queue invariants across processes. It permits
MVCC readers but serializes framework writes within one database. It is deliberately
conservative and does **not** deliver the parallel write capacity described as a
future scalability target. Separate databases have independent locks.

Read pages, counts, and permission serialization use one repeatable-read, read-only
snapshot. Writes use read committed after acquiring the advisory lock. All writes
must go through `database.transaction()`; external SQL writers do not participate
in this contract. Trusted `tx.connection` uses the framework's small SQL dialect,
including positional `?` values. It is not a general SQLite SQL compatibility layer.
PostgreSQL-specific trusted operations can use the wrapped `raw` Psycopg connection.
Do not retain either connection after the context closes.

No automatic business-operation retry is performed. The pool can reconnect before
handing out a connection; it does not replay a transaction. A connection loss during commit can leave an
uncertain outcome. Inspect state using the application's idempotency/reference
keys before retrying a write. Keep handlers short and put external side effects
behind an explicit idempotency design. Additional external poolers need separate validation of transaction, timeout, and
connection initialization behavior.

## Migrations

`initialize()` checks the stored schema definition, migration checksums, column
types, identity/primary keys, foreign keys, and indexes. Existing definitions must
match. `database.migrate(history)` applies supported AddField, RenameField,
AddIndex, and CreateModel operations atomically, then validates data and history.
Rows are validated through a server cursor so a large table is not loaded into
client memory. Renaming an indexed field preserves its index; reusing the old field
name allocates an unused index name for the new field, including unique indexes.
Identity sequences and primary-key indexes use reserved `evlet_` names, so model
tables can reuse names such as `item_id_seq` or `item_pkey`. Explicit migrations
also move older generated names into that namespace atomically, retaining
sequence values, primary-key constraints, and record IDs.
The same type-change/removal restrictions as SQLite apply.

Stop writers and workers before migration, back up and test recovery, then call
`migrate` from the release's operator command. Rollback reverses DDL, data changes,
and migration history on failure. Never run migration automatically in a request.
Large-table DDL and row validation can require a maintenance window; this release
does not implement online migrations or concurrent index construction.

## Verification

Set `EVLET_TEST_POSTGRES_DSN` to an isolated test cluster. The test role must be able
to create and drop databases. Tests generate uniquely named databases and remove
only those databases, including after failure.

```sh
python -m pytest tests/test_postgres.py tests/test_hardening.py
```

Tests exercise real server constraints, migration history/rollback, snapshot reads,
concurrent read-modify-write operations, authentication, field and row permissions,
actions, child rows, attachments, job execution, event replay, and revocation.
CI includes PostgreSQL 14 and 18. Local results are recorded in the handoff.

The SQL binding and locking design follows the upstream
[Psycopg parameter API](https://www.psycopg.org/psycopg3/docs/basic/params.html) and
[PostgreSQL lock documentation](https://www.postgresql.org/docs/current/explicit-locking.html).
