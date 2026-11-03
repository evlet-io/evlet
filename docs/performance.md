# Performance and hardening measurements

Milestone 8 includes real SQLite and PostgreSQL benchmarks and regression tests.
Results are local measurements of this workload, not estimated scale or production
capacity guarantees. The framework's larger tenant and employee-count targets
remain unvalidated.

## Reproduce

Use Python 3.12+, the development lock, and the optional PostgreSQL lock for that
adapter. Build the frontend before collecting bundle sizes. Run from the checkout:

```sh
.venv/bin/python scripts/benchmark.py \
  --sqlite /tmp/new-evlet-benchmark.sqlite3 --output /tmp/sqlite-result.json

# Configure EVLET_BENCHMARK_DSN through a protected environment/service setting.
# It must identify a dedicated, empty PostgreSQL database.
.venv/bin/python scripts/benchmark.py \
  --postgres --output /tmp/postgres-result.json
```

SQLite refuses an existing target file. PostgreSQL refuses a database with any
public tables. The benchmark never drops, truncates, or replaces existing data.
Keep generated benchmark state separate from application state; remove it only
when no longer needed. It does not provision a server or change server settings.

Defaults: 2,000,000 general-ledger rows, 500,000 stock rows, 1,000 account/item keys,
365 posting days, 50 continuous reader threads, 60 seconds, and one synthetic
invoice transaction every three seconds (20 during the run). `--gl`, `--stock`,
`--users`, `--seconds`, and `--requests` adjust the sizes; `--requests` is a minimum
total request count, divided across readers. GL rows must be at least 20,000.

Loading uses deterministic integer minor units, SQLite executemany, and PostgreSQL
COPY. It bypasses model construction to keep fixture creation separate from runtime
measurements. Both adapters use equivalent model schemas and indexes. The load
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

PostgreSQL server CPU and memory are separate processes and **are excluded from
client resource figures**. Database bytes include SQLite database/sidecar files or
PostgreSQL's database size; they exclude the whole cluster WAL and server installation.
Frontend gzip size is the sum of separately compressed asset files. The existing
748.67 kB JavaScript chunk remains a build advisory; it was not hidden by increasing
the warning threshold.

## Implemented hardening

- Indexed session revocation, session expiry, and login-limit cleanup. Query-plan
  tests verify SQLite uses those indexes.
- Duplicate security headers rejected before authentication.
- Real concurrent read-modify-write tests and multi-tenant replay/revocation stress.
- PostgreSQL snapshot, foreign-key/unique constraints, numeric precision, migrations,
  private files, child records, actions, jobs, events, and permission tests.
- A bounded PostgreSQL pool after sustained tests exposed failures with connection
  creation on every operation. Session state is discarded and unfinished writes
  rolled back before a connection returns to the pool. No write retry is added.

The existing hostname tenant catalog remains SQLite-only. PostgreSQL writes remain
serialized per database. Neither additional API workers nor these local benchmark
numbers establish parallel write capacity or production readiness.

## Local results, 29 October 2026

Apple M3, 8 CPU cores, 16 GiB RAM, macOS 27.0.1, Python 3.12.11. Runs were sequential,
with development test/build processes stopped during measurement. The machine was
not an isolated performance host; other desktop applications could still run.
SQLite 3.53.1 used WAL, synchronous FULL, and a 5-second busy timeout. PostgreSQL
14.18 used its local TCP listener, 128 MiB shared buffers, 100 server connections,
fsync/synchronous_commit/full_page_writes enabled, and a client pool capped at 10.

| Metric | SQLite | PostgreSQL |
| --- | ---: | ---: |
| Successful requests in about 60 seconds | 53,985 | 58,312 |
| Requests/second | 899.1 | 971.2 |
| Request median / p95 | 55.1 / 67.2 ms | 51.3 / 55.4 ms |
| Completed invoice transactions | 20 | 20 |
| Invoice transaction median / p95 | 16.3 / 25.7 ms | 29.2 / 34.3 ms |
| Trial balance median, 2m rows | 1,520.1 ms | 62.5 ms |
| Stock balance median, 500k rows | 338.9 ms | 17.4 ms |
| Posting-day SUM median before / after index | 39.6 / 5.3 ms | 30.0 / 1.9 ms |
| Fixture load time | 7.34 s | 7.11 s |
| Database size | 85.7 MiB | 236.4 MiB |
| Client peak RSS | 48.2 MiB | 51.0 MiB |
| Client CPU during concurrent phase | 181.8 s | 94.5 s |
| Request/write errors | 0 | 0 |

PostgreSQL server resource usage is not included in those client figures. The
single-run throughput difference is not a general database ranking. PostgreSQL's
reporting planner differs from SQLite's; inspect plans and repeat with application
queries before choosing an adapter. The posting-day comparison shows why indexes
must follow actual filters, rather than being added to every field.

Raw results: [SQLite](benchmarks/2026-10-29-sqlite.json) and
[PostgreSQL](benchmarks/2026-10-29-postgresql.json). The previous unpooled PostgreSQL
stress run failed and is not included as a successful comparison. The accepted run
uses the bounded pool and includes the same continuous reader/paced writer workload.

The frontend assets total 756,380 bytes, or 167,723 bytes when each asset is gzipped.
These figures exclude the HTML shell and HTTP headers. Distribution validation and
final test counts are recorded below. Those figures are from 29 October 2026; see
"Frontend bundle after removing SolidJS" for the current bundle.

## Release checks

Local validation on 29 October 2026 passed the complete 306-test backend suite with
real PostgreSQL enabled, plus the subsequently added pool-checkout timeout test
(307 tests in the final collection). All 80 frontend tests and the production build
passed. Source/wheel builds and installation into a clean environment passed. The
base wheel ran without optional dependencies; installing the hash-locked PostgreSQL
extra then passed an installed-package persistence round trip.

The framework wheel is 72,452 bytes for this build. This excludes optional drivers,
Python, Node, PostgreSQL server binaries, and application assets, so it is not the
complete deployment footprint. The PostgreSQL binary driver download was about
4.8 MB on this Mac; platform artifacts differ.

A disposable PostgreSQL 14 logical dump/restore retained the test invoice reference
and its exact 12,345 minor-unit value (123.45). This checks a simple local recovery
round trip, not complete application/storage recovery, PITR, or a production RTO.
CI additionally runs the PostgreSQL integration suite on versions 14 and 18. Check
the PR's current results before accepting cross-version support.

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
bounds: 10 for a detail of 1,000 rows or of 100 rows with 9 nested rows each, 32 for a
title-only PATCH, and 35 for a one-row change set.

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
each level with the parent index; PostgreSQL plans a `Recursive Union` without a per-row
`SubPlan`. The recursive step joins on `parent >= id AND parent <= id`. PostgreSQL cannot hash
that join, so it walks the parent index level by level. With a plain `=` join it guessed ten
rows per level, chose a hash join and scanned the whole employee table at every level: 840 ms
for a 500-level chain of 10,000 employees, against 1.8 ms with the range join. SQLite plans
both forms the same. Declare the parent Link with `index=True`; without it each level scans
the table on both databases.

Each statement that applies the scope recomputes the team, so its cost grows with the team
size. A list page applies it to the page, the count and one batched Link check per target
model (list pages serialize their rows in one batch). A single-record check (detail, write,
action, attachment) also computes the whole team. `tests/test_hierarchy_scopes.py` asserts the
SQLite plan; `tests/test_postgres.py` asserts the PostgreSQL plan and that a manager with 150
indirect reports sees exactly their team in lists and aggregates.

Reproduce from the checkout (a temporary SQLite file, or an empty PostgreSQL database named
by `EVLET_BENCHMARK_DSN`; authenticated ASGI requests, no socket):

```sh
.venv/bin/python scripts/benchmark_hierarchy.py
.venv/bin/python scripts/benchmark_hierarchy.py --postgres
```

The data set has 10,000 employees with 10 leave rows each (100,000 rows): a manager over a
chain 500 levels deep, a manager over 5,000 reports in a ten-way tree four levels deep, and
4,498 others reporting to the chief. A leaf sees one employee. Each case checks the
returned total against the expected team.

### Local results, 3 November 2026

Apple M3, 8 CPU cores, 16 GiB RAM, macOS 27.0.1, Python 3.12.16, SQLite 3.54.1, PostgreSQL
14.18 on the same machine with default settings. 20 runs per case, wall time per request
including the session check, median and p95.

| Case | SQLite median | SQLite p95 | PostgreSQL median | PostgreSQL p95 |
| --- | ---: | ---: | ---: | ---: |
| Deep chain (501 people): leave page of 50 and count | 10.0 ms | 17.0 ms | 14.9 ms | 22.1 ms |
| Deep chain: leave aggregate (count, sum) | 7.5 ms | 20.2 ms | 6.9 ms | 26.8 ms |
| Deep chain: employee page | 7.8 ms | 19.7 ms | 10.0 ms | 18.4 ms |
| Wide tree (5,001 people, 50,010 leave rows): leave page and count | 25.8 ms | 57.1 ms | 56.9 ms | 146.0 ms |
| Wide tree: leave aggregate | 17.9 ms | 31.2 ms | 28.1 ms | 83.9 ms |
| Wide tree: employee page | 21.3 ms | 23.0 ms | 32.8 ms | 41.0 ms |
| Leaf (1 person): leave page and count | 5.6 ms | 7.3 ms | 7.1 ms | 11.6 ms |

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

Only the active tab's panel is rendered, so the Details tab costs nothing while the lines tab is
open. The rest of the first screen is the engine's first settle of every line and the 21 grid
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
