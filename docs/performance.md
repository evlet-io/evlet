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
final test counts are recorded below.

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

## UI framework

`cd frontend && npm run bench:ui` runs `frontend/scripts/bench-ui.mjs`: the framework's
TypeScript sources in Node with jsdom, no extra dependencies, production checks off. Budgets
come from [handoff-plan.md](handoff-plan.md).

### Local results, 1 November 2026

Apple M3, 8 CPU cores, 16 GiB RAM, macOS 27.0.1, Node 24.16.0, jsdom 30.1.3. Medians of 15
renders, 25 edits and 10 batches.

| Case | Median | Budget |
| --- | ---: | ---: |
| 1,000-row keyed list, first render (3 bound cells per row) | 34.7 ms | 100 ms |
| 1,000-line record, edit one quantity: engine, totals and DOM | 3.9 ms | 16 ms |
| 10,000 cells bound to text nodes, one batch update | 6.0 ms | none set |

The 1,000-line record is the recorded `order` model (Link lookups, a date from a lookup, a
line amount and two totals). Opening it with its calculation session took 51 ms and its
first render 39 ms. One quantity edit changed exactly three DOM nodes (the quantity, the
line amount and the total), measured with a MutationObserver; no row was re-rendered.

jsdom is not a browser. Its DOM operations are slower than a browser's and it does no
layout or paint, so these numbers bound the framework's own work, not frame times. Browser
timings (first screen, 60 fps scrolling) are measured against testCRM in phase
`ui/04-testcrm`.
