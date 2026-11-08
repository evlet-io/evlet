# Ledger models and transaction locks

Some tables hold many rows that only trusted Python writes: movements, positions and balances,
points, meter readings. As normal models they carry columns and machinery they never use: text
record ids, `name` and `title`, record times, revisions, unsaved changes, forms and generated
screens. A **ledger model** drops all of that. It is still a model and a table, declared and
migrated the same way, but stored lean and with no public surface.

A ledger model computes nothing. Rows are stored exactly as given, and the business logic that
decides them stays in the app.

```python
from evlet import Date, Decimal, Journal, Link, Model, Text


class PointMove(Model):
    member = Link(Member, required=True)
    on_date = Date(required=True)
    reason = Text(default='')
    points = Decimal(scale=2, required=True)

    class Meta:
        ledger = True
        append_only = True              # the database refuses UPDATE and DELETE
        journal = None                  # or Journal(), Journal(tamper_evident=True)
        timestamps = 'created'          # None (default), "created", "modified" or "both"
        indexes = [('member', 'on_date')]
        period = 'on_date'              # closed periods, as for any model


class PointBalance(Model):              # a cache the app keeps up to date
    member = Link(Member, required=True)
    points = Decimal(scale=2, required=True)

    class Meta:
        ledger = True
        timestamps = 'modified'
        indexes = [(('member',), {'unique': True})]
```

## When to use one

Use a ledger model for high-volume rows that people never edit in a form, such as movements that
an action or a job writes. Use a normal model when people create or change records through
screens, when records need states, moves, actions or child tables, or when callers (HTTP, MCP,
Eva, the connector) must reach them.

## What `ledger = True` changes

- **Lean rows.** The table has an integer primary key `id` and the declared columns, nothing
  else: no `name`, `title`, record time columns, revision or unsaved-change columns. The database
  assigns ids in insert order (the SQLite rowid with `AUTOINCREMENT`, a PostgreSQL `bigint`
  identity). They always increase, are never reused, and are plain `int` in Python. They are not
  the time-based [record ids](model-engine.md#record-ids) and have no text form.
- **No public surface.** `ledger = True` means `Meta.api = None`, and any other `api` value is
  refused. The model is left out of every HTTP route, the schema, `/api/ui`, generated screens,
  Discovery, MCP, Eva, tools, the connector, search, exports, Controls and the sidebar. Trusted
  Python is the only writer. Measures and Python reports still read it as a table.
- **Stored fields only.** A ledger model declares `Text`, `Integer`, `Decimal`, `Currency`,
  `Percent`, `Boolean`, `Date`, `DateTime`, `Select` and `Link` fields. `required`, `default`,
  `unique`, `index`, bounds, scale checks and coercion apply as usual. Class creation refuses, with
  a message naming the problem: other field types (`State`, `Sequence`, `RichText`, `LongText`,
  `Tags`, `MultiSelect`, `Reference`, `Email` and the rest), computed, query, fetched and virtual
  fields, field rules, child tables, actions, moves, lifecycle handlers, change reactions, and
  screen options (`help_text`, `hidden`, `read_only`, `visible_if`, Select `colors`, layouts,
  tabs, `Meta.status` and every other Meta option not listed below).
- **Links.** A ledger model may link to a normal model or to another ledger model, with real
  foreign keys. A normal model may link to a ledger row: callers see that field as the plain
  integer id, read only (the schema describes it as an `integer`), and cannot write it.

Ledger models are part of the saved schema and the startup check like any model.

## Reading and writing

Reads and writes use the transaction session and join the caller's transaction:

```python
with database.transaction() as tx:
    row = tx.create(PointMove, member=member_id, on_date=today, points='10.00')
    ids = tx.insert_many(PointMove, [
        {'member': member_id, 'on_date': today, 'points': '-2.50'},
        {'member': other_id, 'on_date': today, 'points': '4.00'},
    ])                                  # [row.id + 1, row.id + 2]
    tx.get(PointMove, ids[0])
    tx.list(PointMove, filters={'member': member_id}, order_by=('-id',), limit=100)
    tx.count(PointMove, filters={'points__lt': '0'})
    database.aggregate(PointMove, measures={'points': Sum('points')}, group_by=('member',))
```

- `tx.insert_many(model, rows)` validates every row like a create, then inserts them all with one
  prepared statement and returns their ids in order. It is for ledger models only. A row may give
  an `id` that `tx.reserve_ids` returned in the same transaction (below).
- `tx.update` and `tx.delete` work on a mutable ledger model. On an append-only one they are
  refused before any SQL. Raw SQL that updates or deletes rows of an append-only table is refused
  by the database triggers, and Evlet raises `ConflictError` (`step is append only: its rows are
  never changed or deleted`) on SQLite and PostgreSQL alike. The transaction rolls back.
- Field types validate values before SQL. All SQL is parameterised and identifiers are validated.
- Measures (`Sum`, `Count`, `Min`, `Max`, grouping) are exact on SQLite and PostgreSQL. A report's
  `run()` reads a ledger model through `self.context.aggregate` and `self.context.list`, which
  apply the person's `Rule(scope=...)` on the model, so declare `permissions` on a ledger model
  whose reports need row scopes ([reports.md](reports.md)).

## Reserving ids (`tx.reserve_ids`)

Rows written in one batch sometimes point at each other: a row that names itself as the first of
its chain, or a reversal that names the row it reverses. Reserve their ids first, then give them
to `insert_many`:

```python
class Step(Model):
    serial = Text(required=True)
    previous = Link('step')
    root = Link('step')

    class Meta:
        ledger = True
        append_only = True
        indexes = [('serial', 'id')]       # the latest row per serial, read by id


with database.transaction() as tx:
    first, second = tx.reserve_ids(Step, 2)
    tx.insert_many(Step, [
        {'id': first, 'serial': 'S1', 'root': first},
        {'id': second, 'serial': 'S1', 'previous': first, 'root': first},
        {'serial': 'S2'},               # no id: the table's next id, after the reserved ones
    ])
```

- `tx.reserve_ids(model, count)` returns `count` new ids (1 to 100,000) of a ledger model, in
  increasing order. It works inside `database.transaction()` only.
- `insert_many` (and `tx.create`) accept an `id` only when this transaction reserved it, and each
  reserved id once. Any other `id` is refused with a `ValidationError`, because an id the
  database did not hand out could clash with a later row.
- Rows inserted later without an id never take a reserved id, in this transaction or a later one.
- Foreign keys are checked as each row is inserted. A row may link to its own id and to rows
  earlier in the batch; put a row before the rows that link to it.
- **SQLite.** The ids follow the table's last id, under the transaction's write lock
  (`BEGIN IMMEDIATE`, one writer at a time). A rollback hands them back, so the next transaction
  may reserve the same numbers.
- **PostgreSQL.** The ids come from the table's identity sequence with `nextval`. A rollback does
  not return them, so ids may have gaps. They are never handed out twice.

Ids always increase, but gaps are normal. Never read meaning into them, such as a count of rows.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `append_only` | `False` | Database triggers refuse `UPDATE` and `DELETE` (and so `REPLACE`, upsert updates and, on PostgreSQL, `TRUNCATE`), as on `evlet_audit`. The session refuses updates and deletes first, with a clear error. Inserts work |
| `journal` | `None` | The [Journal](journal.md): `created`, `changed` and `deleted` entries per row, written by the session as the engine or as `tx.actor`. `Journal(tamper_evident=True)` hashes them and `evlet journal verify` checks them |
| `timestamps` | `None` | `"created"` adds `created_at`, `"modified"` adds `modified_at`, `"both"` adds both. The framework fills them on insert and update; a write that names them is refused |
| `indexes` | `[]` | Composite and unique indexes: `("a", "b")` or `(("a", "b"), {"unique": True})`. An entry may name the row `id`, such as `("serial", "id")`. Normal models declare them the same way ([model-engine.md](model-engine.md#composite-and-unique-indexes)). A single field can also take `index=True` or `unique=True` |
| `period` | `None` | [Closed periods](model-engine.md#closed-periods), exactly as for normal models, `unlocked_periods(reason)` included |

`append_only` and `timestamps` are for ledger models only; a normal model that sets them is
refused. A write that breaks a unique `indexes` entry raises `ValidationError` naming its fields.

Journaling every row of a large append-only ledger doubles the writes, and the rows already
never change, so it is rarely needed. Use it for a mutable ledger whose changes must be traced.

## Migrations

A ledger model migrates with the [explicit migrations](model-engine.md#explicit-migrations) of any
model.

- `CreateModel(PointMove)` creates the table, its indexes and, for `append_only`, its triggers.
- `AddField` adds a nullable field or one with a default. Turning `timestamps` on later is
  `AddField('pointmove', 'created_at', DateTime())` (or `modified_at`).
- `AddIndex('pointmove', 'member')` indexes one field. `AddIndex('pointmove', ('member', 'on_date'))`
  adds a `Meta.indexes` entry, with `unique=True` when it is unique. An entry may name `id`.
- `SetAppendOnly('pointmove', True)` turns append only on, also for a table that has rows: it only
  adds the triggers. `SetAppendOnly('pointmove', False)` turns it off and is the only way to.
- On an append-only model, `RenameField`, `RetireField`, `DropField` and `AlterField` raise
  `SchemaError`, because they rewrite stored rows. Turn append only off in an earlier migration
  first.
- Switching an existing normal model to `ledger = True`, or back, is refused in this version.
  Create a new ledger model and move the rows with a `RunData` step.

`evlet migrate --plan` and `database.plan()` suggest these operations, and the frozen class a
suggestion writes keeps the ledger `Meta` options.

## Reviewing ledger models

```bash
evlet ledger list --instance acme
```

```text
Model         Table         Rows     Append only  Journal         Timestamps  Period
PointMove     pointmove     1204331  yes          off             created     on_date
PointBalance  pointbalance  812      no           off             modified    off
```

It reads one snapshot without writing, so it may run beside a server. A missing flag is easy to
spot in review and in tests. `evlet.ledger.describe(database)` returns the same rows as dicts.

## Worked example: movements and a balance cache

The app writes an append-only movement and updates the balance cache in the same transaction, so
the two never disagree. `tx.lock` stops two writers from both reading the same balance.

```python
from evlet import ValidationError


def spend(database, member_id, points, reason, on_date):
    """Spend points (a number or exact decimal); refuse when the balance does not cover them."""
    with database.transaction() as tx:
        tx.lock(('points', member_id))
        [balance] = tx.list(PointBalance, filters={'member': member_id}) or [None]
        current = balance.points if balance else 0
        if current < points:
            raise ValidationError({'points': f'Only {current} points left'})
        move = tx.create(PointMove, member=member_id, on_date=on_date, reason=reason, points=-points)
        if balance is None:
            tx.create(PointBalance, member=member_id, points=current - points)
        else:
            tx.update(PointBalance, balance.id, points=current - points)
        return move.id
```

Rebuilding the cache from the movements is app code too: group the movements with
`database.aggregate(..., group_by=('member',))` and write the totals. With more than 500
members, page through the groups with `tx.aggregate(..., limit=1000, after=page[-1])`, and
select a batch of members with a long `member__in` list
([paging](measures.md#paging-through-many-groups),
[long `in` lists](model-engine.md#long-in-lists-in-trusted-code)).

## Transaction locks (`tx.lock`)

A ledger writer often checks a balance before it writes. Two transactions that read the same
balance at once could both pass the check. A row lock (`SELECT ... FOR UPDATE`) cannot protect a
key whose row does not exist yet, so the transaction session has named locks on business keys:

```python
with database.transaction() as tx:
    tx.lock(('stock', item_id, warehouse_id), ('open_item', 42), timeout=5)
    # read, check, write ...
```

- `tx.lock(*keys, timeout=None)` takes one or more keys. A key is a nonempty tuple of text
  (1 to 200 characters) and integer (signed 64-bit) parts; record ids are integers. Anything else
  is refused.
- The call blocks until every lock is held. Locks are released when the transaction commits or
  rolls back; there is no unlock call. Calling it outside `database.transaction()` is refused.
- **Choosing keys.** Name what the check protects, not the row you will write: the balance of
  one member (`('points', member_id)`), the stock of one item in one warehouse, one open item.
  Every writer that checks the same thing must use the same key. Keep keys narrow, so writers on
  different things do not wait for each other.
- **Take all locks in one call, at the start.** The method sorts the keys before locking, so two
  callers that pass the same keys in any order cannot deadlock each other. Two separate calls in
  different orders can.
- **PostgreSQL.** Each key becomes a stable signed 64-bit number (BLAKE2b of the key's canonical
  JSON, never Python's `hash()`), locked with `pg_advisory_xact_lock`. Two keys that share a
  number only make a writer wait when it need not; they never let two writers in together. With
  `timeout` (seconds), `lock_timeout` bounds the wait and is restored once the locks are held.
  Without one, the database's own lock timeout (`PostgreSQLDatabase(timeout=...)`, 5 seconds by
  default) bounds it. A wait that runs out raises `LockTimeout` ("busy, try again"), and the
  transaction rolls back as usual.
- **Evlet writes are serialised today.** Every PostgreSQL write transaction also takes one
  database-wide advisory lock ([postgresql.md](postgresql.md)), so two Evlet transactions never
  overlap and a key lock never waits on another Evlet transaction. `tx.lock` still waits for any
  other holder of the same key. It is the business-key contract ledger writers code against, which
  stays correct if the global lock is ever relaxed.
- **SQLite.** A write transaction starts with `BEGIN IMMEDIATE`, so only one writer runs at a
  time. `tx.lock` checks the keys and returns.

```python
from evlet import LockTimeout

try:
    spend(database, member_id, 5, 'Gift card', today)
except LockTimeout:
    ...  # tell the person the record is busy and to try again
```

## Volume

[benchmarks/2026-11-08-ledger-sqlite.json](benchmarks/2026-11-08-ledger-sqlite.json) records
1,000,000 rows inserted with `insert_many` in batches of 10,000 on SQLite
(`scripts/benchmark_ledger.py`): 20.4 seconds, about 49,000 rows a second, on an Apple M3 laptop
with another test run sharing the machine. A grouped `Sum` over all 1,000,000 rows took under a
second. It is a measurement, not a pass/fail threshold.
