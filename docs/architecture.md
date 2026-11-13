# Architecture

Status: implemented foundation, not a production business application framework.

## Which object do I use?

| Object | What it is |
| --- | --- |
| `Application` | The ASGI app object you build in Python from a `Database`, usually with `LocalAuth`. |
| `App` | A shortcut `Application` with local defaults for a quick start. It creates `.data/NAME.sqlite3` and loopback-only `LocalAuth` unless you pass your own. |
| `Registry` | Your models. Pass it to `Database`; there is no global registry. |
| `AppDefinition` | The registration object in `hooks.py` for the nested app layout (`evlet app NAME --legacy`). Flat apps are discovered without it. |
| `Instance` | One customer's isolated data directory, set in `instances/NAME/config.py` and served by `evlet serve NAME`. |
| `DevSignIn` | Development only: wraps an `Application` so it serves one-time sign-in links at `/_dev/signin` to loopback clients. `evlet dev` and `evlet playground` use it; never wrap an application you serve. See [cli.md](cli.md#development-sign-in-links). |
| `Tenant`, `TenantRegistry`, `TenantApplication` | Host-routed serving of several customers from Python. `TenantRegistry` is the catalog, `Tenant` is one customer in it, and `TenantApplication` picks the tenant from the Host header and builds its own `Application`. |

## Milestone 1 baseline (historical)

This section records the original foundation. Later sections and [Evlet UI](ui.md)
describe the current implementation; the Vue renderer was replaced by SolidJS, and
SolidJS by Evlet's own UI framework.

1. **A dependency-free Python core.** Small field descriptors and a metaclass
   collect model fields; an explicit `Registry` owns each application's models.
   There is no global registry, ORM, dynamic code loading, or implicit endpoint
   exposure. `Text`, `Integer`, `Currency`, and `Link` are the initial field set.
   Model defaults and validation run on the server. Instances represent validated
   record snapshots; mutation and update APIs are deferred.
2. **SQLite with an explicit transaction API.** `Database` owns configuration and
   opens short-lived connections. Foreign keys are enabled on every connection;
   initialization enables WAL. Currency is stored as integer minor units and
   serialized as a two-decimal string, avoiding floating-point rounding. Currency
   is intentionally limited to two decimal places in v0.1. A `Link` stores the
   referenced record's 63-bit id and uses a real database foreign key; JSON and URLs
   carry the 13-character text form of the id. Schema creation is
   supported; migrations are not. Changing a model requires a new disposable
   development database until migrations exist. Another database can implement
   the same small operations without introducing a generic ORM
   ([database adapters](database-adapters.md)).
3. **Portable ASGI, with Uvicorn only as a development server.** The core
   implements HTTP and lifespan directly. It exposes health, versioned metadata,
   and explicitly registered models' create/list/detail operations. Database work
   runs in a thread so SQLite waits do not block the ASGI event loop. JSON request
   bodies and pagination are bounded. No arbitrary Python methods are exposed.
4. **A neutral, versioned schema and a real Vue renderer.** `/api/schema` describes
   types and relationships without PrimeVue names. Vue 3 and PrimeVue provide the
   shell, forms, relationship selectors, and tables. The Playground deliberately
   uses three explicit forms; a reusable automatic form/list engine belongs to
   Milestone 3. Vite proxies `/api` and `/health` to the backend during development;
   production builds use relative API URLs and require same-origin hosting.
5. **A real, bounded Playground.** Customer → Invoice → Payment demonstrates
   persistence and relationships through public framework APIs. It is not an
   accounting ledger: no posting, balance calculation, payment settlement,
   authentication, authorization, or tenant routing is claimed. Public API writes
   are open to clients that can reach the development server; bind it to loopback.
6. **Tests at public boundaries.** pytest covers validation, registration,
   transactions, SQL constraints, and complete ASGI requests against temporary
   SQLite files. Vitest and Vue Test Utils exercise real PrimeVue components,
   with a deterministic HTTP stub at the network boundary. A separate smoke
   command sends real HTTP requests to the running Playground. CI also builds
   and installs the wheel outside the source tree and builds the frontend.

## Dependencies and their purpose

The model, persistence and ASGI APIs continue to use the standard library.
The integrated CLI installs Uvicorn so `pip install evlet` can immediately serve
an instance, and packaging to validate Python requirement and version standards.
These avoid a custom HTTP server or an incomplete dependency parser.
`cryptography` (44.0.1 or later, below 52; locked at 50.0.2) seals secrets at rest with
AES-GCM: two-step sign-in secrets, backup codes, and connector passwords and API keys for
other services. The server must read them back (a TOTP secret is needed to check a
code), so they can't be hashed, and the standard library has no authenticated cipher. It is
the maintained, audited library for this in Python; its wheels bring cffi and pycparser.
44.0.1 is the first release with the OpenSSL fix for CVE-2024-12797. Only `evlet/secrets.py`
imports it, and only when a secret is sealed or opened. The `dev`
extra adds pytest (tests) and build (distribution checks).
setuptools is the packaging build backend. `requirements-dev.lock` pins the
resolved Python setup dependencies and their transitive dependencies with hashes.

The browser interface is Evlet's own UI framework (`frontend/src/evlet/`, see
[ui-framework.md](ui-framework.md)). It has no runtime dependencies: the production
bundle contains only first-party code. Development uses four packages: Vite (dev
server and production build), TypeScript (type checks), Vitest (tests) and jsdom (the
DOM for tests and `npm run bench:ui`). SolidJS, Vue, PrimeVue and their plugins and
test libraries were removed with the earlier interfaces. `package-lock.json` pins the
complete tree. No backend runtime dependency was added.

## Package layers

The modules in `evlet/` form eight layers, lowest first. A module imports only from its own
layer or a layer below it. `tests/test_layers.py` holds the map and fails on an import that
points upward; every module must be in the map.

| # | Layer | Purpose | Modules |
|---|---|---|---|
| 1 | `core` | Pure definitions | adapter, qb, identity, fields, models, declarations, expressions, calc, reactivity, currencies, richtext, naming, indexes, query, audit, sections, periods |
| 2 | `access` | Who may do what | auth, permissions |
| 3 | `data` | SQL, files, migrations | database, sqlite, migrations, ledger, writes, storage, object_storage, secrets, sequences, images |
| 4 | `engine` | Document behaviour | documents, states, lifecycle, effects, corrections, journal, measures, unsaved, mentions, meaning |
| 5 | `web` | HTTP application and routes | app, routing, business, forms, slips, workspaces, services, events, jobs, framework, devlink, discovery |
| 6 | `admin` | Controls and settings | controls, controls_export, controls_system, customization, settings, printing |
| 7 | `ai` | Eva, MCP, tools | eva, tools, mcp, connector, connectors, assistants, mailboxes |
| 8 | `site` | Sites, apps, deployment | instances, packages, builds, deployment, tenancy, serverless |

`evlet/ui/`, `evlet/cli/` and `evlet/resources/` sit above all eight layers. A module-level
import never points upward. Some function-level imports still do; `ALLOWED_UPWARD` in the test
lists each one as `(importing module, imported module)`, and the list only shrinks: the test fails
when an entry no longer occurs. Remove an upward import by moving the code to the layer that owns
its data, passing a value in, or letting the higher module register itself with the lower one.

## API and data flow

`Model` definitions → `Registry` → `Database` → `Application` → JSON API → Evlet UI
(generated screens from `/api/schema` and the record routes).

See [Evlet UI](ui.md) for the screens, routes and packaging.
`evlet/routing.py` resolves frontends, hosts and reserved paths into the frozen
route table each instance builds at start-up ([routing.md](routing.md));
`evlet/ui/routes.py` owns UI HTTP routes and browser fallbacks through that table;
`evlet/ui/metadata.py` owns model screen metadata. Recorded API, screen, record and event fixtures are checked
against real HTTP responses; the browser tests replay the API and screen fixtures.

- `GET /health`: executes a database probe and returns `{"status":"ok"}`.
- `GET /api/schema`: returns schema version 1 and registered model metadata.
- `GET /api/{model}?limit=50&offset=0`: returns `items`, `limit`, and `offset`.
- `POST /api/{model}`: validates fields and creates a record (201).
- `GET /api/{model}/{id}`: reads one record or returns 404.

Model names are lowercase class names (e.g. `customer`). IDs are server-generated.
Writes accept JSON objects only. Validation errors return 422, uniqueness/foreign
key conflicts 409, unsupported methods 405, and unknown routes/records 404.

## Milestone 2 decisions

The model engine now adds Boolean, Date, DateTime, fixed-scale Decimal, Select,
Email, URL, and LongText fields; custom validators and cross-field validation;
updates/deletes; and explicit index metadata. No dependency was added.

Database and transaction sessions expose the same small CRUD/query APIs. Partial
updates validate the merged record and preserve omitted values. Deletion uses
foreign-key restrictions. Write transactions acquire their SQLite write lock
before reading so concurrent updates do not overwrite unrelated fields.

Queries compile checked model paths and a bounded operator set into parameterized
SQL. Link traversal uses correlated subqueries, with at most three hops. Ordering
uses record IDs as a deterministic tie-breaker. There is no general ORM, expression
evaluator, or arbitrary SQL input over HTTP.

Schema initialization now verifies structure and records a schema snapshot.
Applications declare their shipped migration history on `Database`; fresh startup
records it as the baseline in the same transaction. Existing schemas still need
explicit upgrades, and startup checks the declared history for changes.
Explicit migration operations cover additions, field renames, and indexes. A pending batch
and its history commit together only after validation. Checksum/history and schema
checks reject edited history and unsupported model changes, including changes that
could reinterpret scaled decimal data. Legacy databases require validation against
their original models before adopting a baseline. The Playground supplies those
frozen definitions and creates a SQLite backup before an upgrade.

The existing forms remain explicit. Their edit/delete/query controls and richer
field inputs demonstrate the model engine through the public HTTP API. The neutral
schema stays at version 1 because its existing shape and field semantics are
preserved; it now includes additional types and index/bound metadata.

See [the model engine guide](model-engine.md) for exact APIs and migration limits.

## Milestone 3 decisions

`Registry.ui_schema()` compiles labels, view definitions, supported field controls,
and default metadata. It is separate from `Registry.schema()`, so presentation
changes do not affect database snapshots or migration history. `Model.Meta` and
field display options remain Python-owned and contain no renderer component names.
The HTTP schema adds UI version 1 while retaining schema version 1.

The browser renderer (first SolidJS, now Evlet's own UI framework) builds navigation,
lists, forms, and details from that contract.
A new Project model exercises the same components as the original three models.
Forms omit untouched defaults on creation and send only changed values on edits.
Callable defaults run only when the backend creates a record. Read-only fields
reject HTTP writes; hidden fields affect presentation, not access permissions.

Search compiles bounded, parameterized OR matches across configured text fields,
combined with typed AND filters. Page records and filtered counts share a SQLite
read snapshot. Link choices use server search and pagination, selected records are
resolved separately, and list labels are fetched in bounded ID batches. Request
sequence guards protect navigation from older asynchronous responses.

See [application generation](application-generation.md) for the public contract,
examples, browser integer bounds, and the explicit Playground upgrade.

## Milestone 4 decisions

The Playground now configures LocalAuth on Application. Local accounts and revocable
sessions use reserved SQLite tables; passwords use standard-library scrypt. Host,
Origin, custom request headers, session CSRF tokens, and cookie attributes protect
browser requests. Explicit Policy rules enforce model, field, and row access.
Permissions filter records and query counts on the server. Authenticated HTTP
queries reject relationship traversal until a permission-aware join planner exists.
The trusted Python query API remains unchanged.

ChildTable declares ownership through a required Link. Parent and child writes use
one transaction. Registered Action handlers run inside the same transaction API and
are trusted business code. The Playground demonstrates a Project completion rule.
Attachments use private SQLite BLOBs so deletion and backups share the record's
transaction and storage boundary. No public files directory or new dependency is
needed. Grouped form layouts remain renderer-neutral metadata.

See [business capabilities](business-capabilities.md) for the public APIs, security
boundaries, upgrade path, and limits.

## Milestone 5 decisions

An operator-managed SQLite catalog maps verified hostnames to immutable tenant
contexts. The ASGI router resolves Host before database access and supplies an
application with its own accounts, sessions, database, and file directory. Runtime
database connections use existing-file mode. Forwarded host headers never select
a tenant. Cached applications are replaced when their domain set changes.

Private attachments now use a small storage interface. SQLite remains the default
for existing applications. Tenant applications use immutable local files with
transactional metadata, database-bound storage directories, and explicit orphan
collection. Existing BLOB attachments remain readable without copying them.

The Playground provides trusted provisioning and DNS TXT verification commands.
Production tenants have no browser administrator setup and use secure host-prefixed
cookies. Deployment rendering emits explicit verified Caddy hosts and a restricted
single-worker systemd service. Local smoke checks exercise real Vite and Caddy
traffic; public DNS, public certificates, and Linux service operation need separate
deployment verification. See [tenancy and deployment](tenancy-deployment.md).

## Milestone 6 decisions

The optional queue shares the application's SQLite transaction boundary. Enqueue
commits with business changes. Random claim leases fence stale workers, and task
writes commit with completion and an event. User accounts and target permissions
are rechecked at execution. Short handlers hold one database write lock; this is
not a distributed or long-running job system. External side effects require their
own idempotency handling. Interval scheduling coalesces missed occurrences.

Record invalidations are retained in SQLite with opaque replay cursors and current
permission filtering. SSE rechecks sessions and tenant activation on delivery;
buffered transports use polling. The renderer updates lists and details without
replacing unsaved forms. The event log is a hint, not a record store or audit log.

S3Storage adds private remote bytes with transactional local metadata, namespace
binding, conditional writes, and explicit orphan collection. The boto3 extra is
optional and has its own hash lock; core imports still require no external package.
The experimental LambdaHTTP adapter translates AWS payload-v2 requests to ASGI and
buffers the response. It has no end-to-end test and its SSE route returns 501. It provides no persistence, lifespan, worker, or deployment. Durable
serverless database support is a separate requirement.

See [jobs, realtime, and adapters](jobs-realtime.md) for public contracts and limits.

## Milestone 7 decisions

Discovery derives from the existing permission-filtered UI contract, without
changing UI or database schema versions. The compact catalog avoids repeating
field metadata; per-model descriptions include JSON schemas for reads and permitted
writes. Callable defaults are not evaluated, static Link defaults are not exposed,
and child-only models remain reachable through readable parents.

The MCP implementation is a small sequential stdio server. It authenticates through
one application session and forwards tools to existing HTTP routes. The backend
remains the authority for tenant, model, field, row, action, and job access. Reads
are the default; an operator must enable writes at process startup. Tool input
cannot change the origin, session, or HTTP headers. Transport bounds, no redirects,
private session files, and no automatic write retries keep this boundary explicit.

The MCP connector uses only the standard library. The official MCP SDK is optional test
instrumentation, pinned with transitive hashes in `requirements-mcp.lock` and
constrained by the development lock. It verifies protocol interoperability without
adding a runtime dependency. The starter command writes a small authenticated
consumer application using public exports and refuses existing directories.

See [discovery and local connectors](connector.md) for contracts, commands, client
approval responsibilities, limits, and the application development workflow.

## Deferred work

WebSockets, arbitrary schema conversions, parallel write scheduling, and
production-target validation remain later work.


## Milestone 8 decisions

Private storage, events, and jobs check their owning database through a shared
boundary instead of assuming a SQLite file. Session revocation/expiry and login
counter cleanup now have explicit indexes. Repeated security headers are rejected
before authentication. Nullable ordering is explicit.

Reproducible benchmarks use synthetic large ledgers and real databases, with
continuous authenticated ASGI readers, paced invoice transactions, SQL reports,
index comparisons, and measured resource/bundle sizes. They do not model a complete
accounting system or prove deployment capacity. See [performance](performance.md)
and [operations](production-operations.md).

## Database adapters

SQLite is the one database Evlet ships with. Framework code reads and writes through the
query builder (`evlet/qb.py`): plain statement objects run with `connection.run(...)`. The SQLite
dialect (`evlet/sqlite.py`) compiles them to SQL and implements the connection interface in
`evlet/adapter.py`. `evlet/database.py` opens connections and runs transactions, and
`evlet/writes.py` holds the SQLite files and their write line-up. Other modules never write SQL
text or ask which database they run on; the modules still being moved are listed in
[database adapters](database-adapters.md), with the plan and how another database would be added.

## Projects, app packages and instances

The integrated CLI composes existing Application, Registry, Policy, migration,
job and UI APIs through explicit AppDefinition registrations. Instance config
selects isolated SQLite/local storage. Git fetches only perform static validation;
trusted installation stages database changes and publishes a separate app lock
with a recoverable journal. Existing framework modules retain their public names.
See [the CLI guide](cli.md) for commands, resource packaging and boundaries.

## Document lifecycle amendment

Confirmable models now use decorator validators/actions, same-identity revisions,
immutable snapshots and unified history. See [document lifecycle](document-lifecycle.md)
for the APIs, permission and transaction contract, approval boundaries and adoption
from existing State/Move applications.

## Ledger models

`Meta.ledger = True` is a storage mode of `Model`, not a second persistence API. A ledger model is
declared, registered and migrated like any model and read through the same session, measures and
reports, but its table holds an integer id the database assigns and the declared columns only, and
`Meta.api` is forced to `None`, so every caller surface already leaves it out. Append-only tables use
database triggers, as `evlet_audit` and `evlet_journal` do. Named locks (`tx.lock`) only check that a
write transaction is open on SQLite, whose writers are already serialised. The framework adds no
finance or stock concepts; balancing, reversals and cache rebuilds are app code. See [ledger models](ledger.md).

## The Tool Engine

One Tool Engine (`evlet.tools`) serves MCP clients and the API, and will serve Eva. Each tool is
a call to an existing authenticated route, dispatched in process as the person, so there is no
second permission system and no AI-specific business code. The engine adds argument checks,
approvals bound to the exact call and record revision, and one log of every call. See
[the Tool Engine](tools.md).

## Eva

Eva (`evlet.eva`) runs on the Tool Engine. It adds the provider adapter (standard library,
OpenAI first), conversations, runs streamed over SSE from stored events, files, usage,
allowances held atomically before each request, and scheduled tasks on the existing jobs
system. Eva is off unless config.py turns it on, and then Controls and each person's Allow
Eva must too. See [Eva](eva.md).
