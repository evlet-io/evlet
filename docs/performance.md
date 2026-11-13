# Performance and hardening measurements

Milestone 8 includes real SQLite benchmarks and regression tests. Evlet 0.4 also
measured PostgreSQL; PostgreSQL was removed in 0.4.5.
Results are local measurements of this workload, not estimated scale or production
capacity guarantees. The framework's larger tenant and employee-count targets
remain unvalidated.

## Reproduce

Use Python 3.12+ and the development lock. Build the frontend before collecting
bundle sizes. Run from the checkout:

```sh
.venv/bin/python scripts/benchmark.py \
  --sqlite /tmp/new-evlet-benchmark.sqlite3 --output /tmp/sqlite-result.json
```

SQLite refuses an existing target file. The benchmark never drops, truncates, or
replaces existing data.
Keep generated benchmark state separate from application state; remove it only
when no longer needed. It does not provision a server or change server settings.

Defaults: 2,000,000 general-ledger rows, 500,000 stock rows, 1,000 account/item keys,
365 posting days, 50 continuous reader threads, 60 seconds, and one synthetic
invoice transaction every three seconds (20 during the run). `--gl`, `--stock`,
`--users`, `--seconds`, and `--requests` adjust the sizes; `--requests` is a minimum
total request count, divided across readers. GL rows must be at least 20,000.

Loading uses deterministic integer minor units and SQLite executemany. It bypasses
model construction to keep fixture creation separate from runtime measurements. The load
transaction commits before measurement. Global ledger totals, stock totals, and
invoice counts are checked. This is synthetic transaction data, not an accounting
posting engine or inventory valuation implementation.

Requests enter the real authenticated ASGI app, check a synthetic viewer session,
filter an indexed account, read 20 rows, count matching rows, and serialize records.
All 50 readers share that test session. There is no socket, proxy, TLS, browser,
sign-in hashing, per-user scoping, or human think time in measured requests.
The writer commits an invoice, two balanced ledger entries, and a stock entry in
one public transaction. Continuous readers overlap all paced writes.

Trial-balance and stock-balance GROUP BY reports are measured three times before
concurrent requests. A posting-day SUM is measured five times before and after an
index is added. These reporting samples are isolated, not concurrent report-load
measurements. Their small sample counts make the reported p95 effectively a maximum.
For a representative deployment, repeat the run, add the actual reporting mix,
include network/TLS, and run for longer.

## Measurement limits

The JSON records CPU/model/RAM, versions, database settings, dataset sizes, load
time, request latency/throughput, write-transaction duration, reporting latency,
index comparison, database size, client CPU, client peak RSS, and frontend assets.
On macOS peak RSS units are bytes; on Linux they are KiB. CPU is process CPU time
and can exceed elapsed wall time with concurrent threads.

Database bytes include SQLite database/sidecar files.
Frontend gzip size is the sum of separately compressed asset files. The existing
748.67 kB JavaScript chunk remains a build advisory; it was not hidden by increasing
the warning threshold.

## Implemented hardening

- Indexed session revocation, session expiry, and login-limit cleanup. Query-plan
  tests verify SQLite uses those indexes.
- Duplicate security headers rejected before authentication.
- Real concurrent read-modify-write tests and multi-tenant replay/revocation stress.

- Trusted batch code walks any number of aggregate groups a page at a time
  (`tx.aggregate(..., limit=, after=)`), each page one keyset query, and takes `in` lists of up
  to 30,000 values. Tests walk 20,000 groups 500 at a time with a traced Python peak under
  1.5 MB; holding all groups at once takes about 5.7 MB.

SQLite writes remain serialized per file. Neither additional API workers nor these local benchmark
numbers establish parallel write capacity or production readiness.

## Local results, 29 October 2026

Apple M3, 8 CPU cores, 16 GiB RAM, macOS 27.0.1, Python 3.12.11. Runs were sequential,
with development test/build processes stopped during measurement. The machine was
not an isolated performance host; other desktop applications could still run.
SQLite 3.53.1 used WAL, synchronous FULL, and a 5-second busy timeout.

| Metric | SQLite |
| --- | ---: |
| Successful requests in about 60 seconds | 53,985 |
| Requests/second | 899.1 |
| Request median / p95 | 55.1 / 67.2 ms |
| Completed invoice transactions | 20 |
| Invoice transaction median / p95 | 16.3 / 25.7 ms |
| Trial balance median, 2m rows | 1,520.1 ms |
| Stock balance median, 500k rows | 338.9 ms |
| Posting-day SUM median before / after index | 39.6 / 5.3 ms |
| Fixture load time | 7.34 s |
| Database size | 85.7 MiB |
| Client peak RSS | 48.2 MiB |
| Client CPU during concurrent phase | 181.8 s |
| Request/write errors | 0 |

The posting-day comparison shows why indexes must follow actual filters, rather than
being added to every field.

Raw results: [SQLite](benchmarks/2026-10-29-sqlite.json).

The frontend assets total 756,380 bytes, or 167,723 bytes when each asset is gzipped.
These figures exclude the HTML shell and HTTP headers. Distribution validation and
final test counts are recorded below. Those figures are from 29 October 2026; see
"Frontend bundle after removing SolidJS" for the current bundle.

## Release checks

Local validation on 29 October 2026 passed the complete backend suite (307 tests
in the final collection). All 80 frontend tests and the production build passed.
Source/wheel builds and installation into a clean environment passed. The base wheel
ran without optional dependencies.

The framework wheel is 72,452 bytes for this build. This excludes Python, Node and
application assets, so it is not the complete deployment footprint.

## Busy writing on SQLite

SQLite lets one writer at a time change a file. Before 0.4.3 every other writer waited inside
SQLite, which retries at random rather than in order, so a long job could keep requests waiting
for seconds and then fail them with "database is locked". From 0.4.3 an instance keeps four
SQLite files and its writers take turns.

**Four files.** `data/database.sqlite3` keeps records, the audit trail, the journal, the job
queue (with job progress and checkpoints), accounts, roles and settings: everything that must
save in the same transaction as a record. `data/sessions.sqlite3` keeps sign-in sessions,
two-step tickets and sign-in attempts. `data/logs.sqlite3` keeps the tool-call log, public rate
counts and proxy header notes. `data/eva.sqlite3` keeps Eva's chats, messages, files, usage and
tasks. All four use WAL. Every connection attaches the other three, so SQL names tables as before
and joins across files work. A database file with another name, such as `app.sqlite3`, has
`app-sessions.sqlite3` and so on next to it.

A write locks only the file it changes. `database.transaction()` writes the main file, and
takes the turn of a side file only when it touches one of its tables.
`database.transaction(store="sessions")` (or `"logs"`, `"eva"`) writes that file only; changing
any other file there is refused. Sign-in, sign-out, the tool-call log and Eva's run writes use
these, so a long job step on the main file never holds them up.

SQLite cannot enforce a foreign key from one file to another, so Evlet checks those links
itself: sign-in requires an active account, and at startup sessions, Eva chats, files and tasks
of an account that no longer exists are removed, along with the schedule of an Eva task that is
gone. After a crash the main file is atomic as before. The other files are best effort for the
last moment of writes: a session started or a log line written just before the crash may be
missing.

**Turns.** Inside one process each file has a line of writers (`evlet/writes.py`). Requests
wait in one queue and job steps in another, each first come first served. When the file is
free the oldest request goes next, unless the oldest job step has waited 0.25 seconds: then it
goes, so a job is never starved by a stream of requests. A resumable job gives up its turn after
every step and joins the back of the line. While people are writing (someone waited during the
step, or wrote to the file in the last second), a job rests after each step for as long as the step
held the file, and requests that arrive meanwhile go at once. So under load a job gets about half
of the file's time and keeps going; a job on its own runs at full speed. A writer that waits more than 5 seconds
(`Database.write_wait`) gets the usual "database is locked" error, answered as before. Other
processes, such as a CLI command or a second server process, are not in the line: against them
SQLite's own busy wait still applies.

**Slow writes.** A write transaction that holds a file longer than 1 second logs a warning
naming the file and the request (`POST /api/...`) or the job (`job #12 import_invoices`). Set
`Database.slow_write` to another number of seconds, or to `None` to turn it off.

**Batched counters.** Busy last-seen writes (an API key's last use, an AI assistant's call count
and last use, proxy header notes) are added to a list in memory and saved by one background
thread about every 250 ms, in one short transaction per file. Adding never touches SQLite, so a
request never waits on them. They are saved on shutdown (`Database.close()`, the ASGI lifespan
and at process exit); a crash loses at most the last quarter second of them. Pages that show
these numbers save the batch first. Tool-call rows are not batched: the per-minute MCP rate and
the order of the log read them, so each is one short write to `logs.sqlite3`.

**Connections.** Opening a connection and attaching three files costs about a millisecond, so each
thread keeps its last connection for its next operation. It is used again only while the four
files are the same files on disk (a restore or a replaced file opens a new one); any open
transaction is rolled back before it is kept. `Database.close()` closes them.

### Local results, 12-11-2026

Apple M3, Python 3.12, SQLite 3.54.1. 0.4.2 is release `de25c03`.

`scripts/busy_queue_benchmark.py` is the owner's acceptance check. Eight people each open a list of
customers and save a customer, then pause 0.2 s, for 20 s. Run A has the people alone. Run B adds a
background job writing customers in back-to-back transactions of 0.1 s or 1 s, like a data
generator. Run C is run B while sign-in attempts, the tool-call log and Eva write their own files.

| Run | Release | Saves | Failed | Save wait median / p95 | Side files p95 |
| --- | --- | ---: | ---: | --- | --- |
| A, people only | 0.4.2 | 768 | 0 | 1 ms / 11 ms | |
| A, people only | 0.4.3 | 779 | 0 | 0.9 ms / 3.0 ms | |
| B, 0.1 s steps | 0.4.2 | 42 | 16 ("database is locked") | 3,163 ms / 5,071 ms | |
| B, 0.1 s steps | 0.4.3 | 768 | 0 | 1.0 ms / 5.9 ms | |
| C, 0.1 s steps | 0.4.3 | 776 | 0 | 0.8 ms / 4.6 ms | sign-in 2.9 ms, tool log 1.6 ms, Eva 1.0 ms |
| B, 1 s steps | 0.4.2 | 40 | 19 ("database is locked") | 2,851 ms / 4,718 ms | |
| B, 1 s steps | 0.4.3 | 394 | 0 | 2.6 ms / 975 ms | |
| C, 1 s steps | 0.4.3 | 394 | 0 | 2.2 ms / 983 ms | sign-in 4.0 ms, tool log 2.1 ms, Eva 1.4 ms |

On 0.4.2 people managed about one save in twenty and half of those failed. On 0.4.3 nothing
fails. With 0.1 s steps people work as fast as without the job, and the job still writes about
half as many rows as it does alone (249,162 in 20 s, against 447,700 before jobs rested). With
1 s steps a save waits at most the step in progress, and each step logs `A write held
database.sqlite3 for 1.02 s (job #1 data generator)`. Sign-in, the tool log and Eva never wait on
the job. Reads never take a turn; under load they slow only by sharing the processor with the job.

`scripts/benchmark_busy_writes.py` runs a resumable job of 20 steps, each saving 50 records and
holding the database for `--step` seconds, while six threads save one record every 10 ms:

| Step | Release | Saves | Failed | Median wait | 95th percentile | Longest wait | Job |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.1 s | 0.4.2 | 13 | 0 | 12 ms | 2,222 ms | 2,222 ms | 2.2 s |
| 0.1 s | 0.4.3 | 1,082 | 0 | 0.8 ms | 104 ms | 112 ms | 4.4 s |
| 1.0 s | 0.4.2 | 10 | 18 ("database is locked") | 3,568 ms | 4,620 ms | 4,620 ms | 20.2 s |
| 1.0 s | 0.4.3 | 9,660 | 0 | 0.5 ms | 1.4 ms | 1,014 ms | 40.3 s |

A step that holds the file for a second still makes a save that arrives during it wait up to a
second: keep job steps short. `tests/test_sqlite_files.py` checks both cases in a shorter form.

One record read with `database.get` and one update with `database.update`, 500 times each on one
thread: 0.4.2 took 680 µs and 957 µs; 0.4.3 takes 67 µs and 164 µs, since it no longer opens a
new connection for each.

## Owned tables

Documents with owned tables (`Table(Child)`) load, save and page with a bounded number
of SQL statements. Rows load one table level at a time: one statement per chunk of 500
owner IDs, using window functions to keep the first rows per owner and count the rest.
Row scopes are applied in the same statement for reads, and with one statement per level
when a write needs the hidden rows. Link and Reference targets are checked once per target
model and chunk, not per row. Reads, title-only saves and saves that change a few rows use
a statement count that does not grow with the number of rows. Writes still cost one
statement per row they create or change: one INSERT per created row and one UPDATE per row
whose values change (a change set updating 100 rows with a new nested row each runs about
330 statements). Deletes and position-only changes are batched.

Reproduce from the checkout (a temporary SQLite file, authenticated ASGI requests, no
socket):

```sh
.venv/bin/python scripts/benchmark_lines.py
```

`tests/test_lines.py` counts statements with `sqlite3` trace callbacks and asserts upper
bounds: 12 for a detail of 1,000 rows and 10 for one of 100 rows with 9 nested rows each, 35 for a
title-only PATCH, and 37 for a one-row change set. A detail costs one statement to name who created
and last changed the record ([model-engine.md](model-engine.md#record-times)), once per request.

### Local results, 1 November 2026

Apple M3, 8 CPU cores, 16 GiB RAM, macOS 27.0.1, Python 3.12.16, SQLite 3.54.1. Counts
include the session and CSRF checks of each request. Timings are wall time per request;
20 runs (4 for creates), median and p95. PATCH timings exclude reading the ETag.

| Case | Statements | Median | p95 |
| --- | ---: | ---: | ---: |
| Create, 1,000 rows | 1,018 | 82.5 ms | 83.6 ms |
| GET detail, 1,000 rows (100 embedded, ETag over all) | 9 | 7.9 ms | 11.4 ms |
| GET one page of 100 lines, 1,000 rows | 8 | 1.9 ms | 2.0 ms |
| PATCH title, 1,000 rows | 30 | 76.3 ms | 77.8 ms |
| PATCH one row (change set), 1,000 rows | 32 | 76.0 ms | 77.2 ms |
| Create, 100 rows x 9 nested | 1,116 | 37.7 ms | 38.0 ms |
| GET detail, 100 rows x 9 nested | 8 | 5.2 ms | 5.3 ms |
| GET one page of 100 lines, 100 x 9 nested | 8 | 5.5 ms | 8.9 ms |
| PATCH title, 100 rows x 9 nested | 29 | 38.5 ms | 39.3 ms |
| PATCH one row (change set), 100 x 9 nested | 31 | 33.7 ms | 39.5 ms |

Before batching, on the same machine and the same kind of document (counts are exact;
times are single runs): a detail of 100 linked rows took 607 statements, a title-only
PATCH 2,021 and a one-row change 2,227. With 100 rows of 9 nested rows, a detail took
2,407 statements (18 ms) and a title-only PATCH 9,221 (80 ms). Collections above 100 rows
could not be loaded at all.

A save still settles the whole document in Python (building the calculation session,
validating every row and comparing the saved tree), so its time grows with the rows even
though its statement count does not. At 1,000 rows that is about 75 ms on this machine.
A detail reads every readable row to compute its ETag, then embeds the first 100.
These are local measurements of one workload, not capacity guarantees.

## Team row scopes

A `Hierarchy.team` scope (see [business-capabilities.md](business-capabilities.md#team-and-hierarchy-scopes))
is one non-correlated `IN (WITH RECURSIVE ...)` subquery. The database computes the team once
per statement, not once per row: SQLite materializes it (`MATERIALIZE evlet_team`) and finds
each level with the parent index. The recursive step joins on `parent >= id AND parent <= id`.
The range join keeps a database from choosing a hash join that scans the whole table at every
level. SQLite plans it the same as a plain `=` join. Declare the parent Link with `index=True`;
without it each level scans the table.

Each statement that applies the scope recomputes the team, so its cost grows with the team
size. A list page applies it to the page, the count and one batched Link check per target
model (list pages serialize their rows in one batch). A single-record check (detail, write,
action, attachment) also computes the whole team. `tests/test_hierarchy_scopes.py` asserts the
SQLite plan.

Reproduce from the checkout (a temporary SQLite file, authenticated ASGI requests, no socket):

```sh
.venv/bin/python scripts/benchmark_hierarchy.py
```

The data set has 10,000 employees with 10 leave rows each (100,000 rows): a manager over a
chain 500 levels deep, a manager over 5,000 reports in a ten-way tree four levels deep, and
4,498 others reporting to the chief. A leaf sees one employee. Each case checks the
returned total against the expected team.

### Local results, 3 November 2026

Apple M3, 8 CPU cores, 16 GiB RAM, macOS 27.0.1, Python 3.12.16, SQLite 3.54.1. 20 runs per
case, wall time per request including the session check, median and p95.

| Case | SQLite median | SQLite p95 |
| --- | ---: | ---: |
| Deep chain (501 people): leave page of 50 and count | 10.0 ms | 17.0 ms |
| Deep chain: leave aggregate (count, sum) | 7.5 ms | 20.2 ms |
| Deep chain: employee page | 7.8 ms | 19.7 ms |
| Wide tree (5,001 people, 50,010 leave rows): leave page and count | 25.8 ms | 57.1 ms |
| Wide tree: leave aggregate | 17.9 ms | 31.2 ms |
| Wide tree: employee page | 21.3 ms | 23.0 ms |
| Leaf (1 person): leave page and count | 5.6 ms | 7.3 ms |

Before list pages serialized their rows in one batch, each row's Link check recomputed the
team: the wide tree's leave page took 315 ms on SQLite (50 team computations). These are
local measurements of one synthetic workload, not capacity guarantees.

## Unpost dependents

Before an unpost, the engine looks for live records that depend on the record
([states-actions.md](states-actions.md#unpost-and-repost)). It reads the ids of the record's
Table rows (one statement per table level and chunk of 500) and of its effect rows (one per
effect), then runs one statement per chunk of ids for every Link field whose target is the
model, one of its Table row models or one of its effect targets, and for every `Reference`
field of every model (a Reference may name any model). Link and Reference columns are indexed
only when declared with `index=True`, so most of these statements scan their table. Documents
of matched Table rows are found one statement per level and chunk, and each candidate costs
one read and one posting check. Unpost is a rare admin action, so the scans are accepted;
details do not run them (`_unpost` does not check dependents). Index the Link and Reference
fields that point at posting documents, their rows or their effect targets when those tables
grow large.

## Frontend bundle after removing SolidJS

Measured on 2 November 2026 with the same method (`frontend/dist/assets`, each file
gzipped on its own, HTML shell excluded):

| Build | Files | Bytes | Gzipped |
| --- | ---: | ---: | ---: |
| SolidJS interface (`ui/03-screens`) | 7 | 318,257 | 93,097 |
| Evlet UI framework (`ui/05-remove-solid`) | 3 | 321,392 | 97,079 |

The SolidJS build split Accounts and the Projects demo into chunks loaded on demand. The
new build is one script and one stylesheet, loaded together. The stylesheet still carries
the Library page's styles, which only the development page uses. The frontend's installed
npm packages fell from 193 to 73, and its direct dependencies from 14 to 4.

## UI framework

`cd frontend && npm run bench:ui` runs `frontend/scripts/bench-ui.mjs`: the framework's
TypeScript sources in Node with jsdom, no extra dependencies, production checks off. Budgets
come from [handoff-plan.md](internal/handoff-plan.md).

### Local results, 1 November 2026

Apple M3, 8 CPU cores, 16 GiB RAM, macOS 27.0.1, Node 24.16.0, jsdom 30.1.3. Medians of 15
renders, 25 edits and 10 batches.

| Case | Median | Budget |
| --- | ---: | ---: |
| 1,000-row keyed list, first render (3 bound cells per row) | 49.6 ms | 100 ms |
| 1,000-line record, edit one quantity: engine, totals and DOM | 4.3 ms | 16 ms |
| 10,000 cells bound to text nodes, one batch update | 8.1 ms | none set |

The 1,000-line record is the recorded `order` model (Link lookups, a date from a lookup, a
line amount and two totals). Opening it with its calculation session took 56 ms and its
first render 50 ms. One quantity edit changed exactly three DOM nodes (the quantity, the
line amount and the total), measured with a MutationObserver; no row was re-rendered.
Every dynamic value sits between two comment markers, which costs about 15 ms of the
first render here; it keeps nested content correct when rows move or branches switch.

### Components, 1 November 2026

Same machine and settings, run while other work was using the CPU (load average about 4.5;
the 1,000-row keyed list above took 77.7 ms in the same run, against 49.6 ms unloaded), so
these medians are on the high side.

| Case | Median | Budget |
| --- | ---: | ---: |
| ChildTable over the 1,000-line record, first render | 21.3 ms | 100 ms |
| ChildTable, commit one quantity with Enter: engine, line amount, totals row, summary and the next cell's editor | 6.8 ms | 16 ms |
| DataTable, 100 list rows with a selection column and a status pill per row, all rows in view | 45.0 ms | 50 ms |

The ChildTable keeps only the rows in view in the DOM (21 of 1,000 at a 560 px viewport) and
renders more as it scrolls, so its first render does not grow with the number of lines. A
committed edit made 17 DOM changes (MutationObserver records): the quantity cell switching back from its editor, the
line amount, the footer total, the summary total and the editor opening on the cell below.
DataTable also virtualizes; the benchmark gives it a viewport tall enough to render all
100 rows.

### Screens, 2 November 2026

Same machine and settings, `npm run bench:ui`. Both cases are measured from building the page to
its rows in the DOM, with the fetch answered at once by a stub: the time is the screen's own
work, parsing included.

| Case | Median | Budget |
| --- | ---: | ---: |
| List page of `order`, 100 rows: header, saved views, toolbar, DataTable, load and first render | 8.7 ms | 50 ms |
| Record view of `order` opened on its 1,000-line table tab: read the detail, attach calculations, header, summary and ChildTable | 50.3 ms | 100 ms |

The list page renders the rows in view (21 of 100 at its 640 px viewport); the record view
renders 21 of 1,000 lines. Most of the record's time is opening it (parsing 1,000 rows and
attaching the calculation session); the ChildTable itself renders in about 13 ms.

jsdom is not a browser. Its DOM operations are slower than a browser's and it does no
layout or paint, so these numbers bound the framework's own work, not frame times. Browser
timings (first screen, 60 fps scrolling) are measured against testCRM in phase
`ui/04-testcrm`.

### testCRM, 2 November 2026

testCRM ([testcrm.md](internal/testcrm.md)) in two settings on the same machine.

In jsdom, `npm run bench:ui` (section 8), over responses recorded from testCRM as the sales
user `sara`. The 1,000-line deal repeats the recorded lines of the 150-line deal: each line has a
product Link, three `initial` suggestions from the product, an amount with a discount and a VAT
amount through `when()` and `parent.vat_rate`; the deal has five sums and totals over them.

| Case | Median | Budget |
| --- | ---: | ---: |
| Deals list, 100 deals: build, load and first render | 11.1 ms | 50 ms |
| Deal opened on its 1,000-line tab: detail, calculations, header, summary and ChildTable | 161.9 ms | 100 ms |
| Deal, 1,000 lines: commit one quantity with Enter (line amount, VAT, five totals) | 8.8 ms | 16 ms |

Opening the 1,000-line deal missed its budget. A CPU profile (`node --cpu-prof` on the bench)
showed the calculation engine doing whole-document work once per linked record: each product,
organization and contact read in the background was handed to the engine in a batch of its own,
and every batch copied the document for rollback, walked every calculation and every observer.
In branch `ui-final/06-testcrm-findings`:

* records that arrive together are given to the engine in one batch, and today's date and the
  records read before go in with the first one;
* `set`, `setToday` and `setLookups` check everything before they change anything, so inside a
  batch they no longer copy the whole document;
* the settle walk keeps each calculation's row, plan field and inputs from the last bind instead
  of splitting node keys and copying input sets on every batch;
* observers of removed rows are looked for only when rows were added or removed.

Measured with `npm run bench:ui` on the same machine, before and after:

| Case | Before | After | Budget |
| --- | ---: | ---: | ---: |
| testCRM deal opened on its 1,000-line tab | 159.4 ms | 80.2 ms | 100 ms |
| testCRM deal, 1,000 lines: commit one quantity | 8.7 ms | 4.8 ms | 16 ms |
| 1,000-line `order`: edit one quantity | 4.3 ms | 2.0 ms | 16 ms |
| ChildTable, 1,000 rows: commit one cell | 4.3 ms | 2.4 ms | 16 ms |

The numbers were measured when the record page had tabs (before 0.4). The rest of the first screen is the engine's first settle of every line and the 21 grid
rows in view.

In a real browser (Chromium in an embedded browser pane, 1280 x 800, development build
served by Vite through its proxy to the testCRM backend on loopback, SQLite). The pane was not in
the foreground, so the browser may have throttled timers; these are upper bounds.

| Case | Measured | Budget |
| --- | ---: | ---: |
| Deal with 1,000 lines: link clicked to the 1,000 lines paged in and the first screen shown, network included (one detail and nine line pages) | 433 ms | none for the network |
| Same deal: commit one quantity with Enter, five edits | 13 to 23 ms (median 18 ms) | 16 ms |
| Deal with 150 lines: commit one quantity | 9.4 ms | 16 ms |
| Deals list, 102 deals (50 per page, 23 rows in the DOM): link clicked to rows shown, network included | 23.5 ms | 50 ms |
| Deals board, 100 cards: link clicked to every card shown, network included | 27.5 ms | 50 ms |

The 1,000-line edit in the browser is near the 16 ms budget and over it in some runs, against
8.8 ms in jsdom; the development build keeps its checks on. Scrolling at 60 fps was not
measured: the pane gave no frame timing while it was hidden.
