# Milestone 1 architecture

Status: implemented foundation, not a production business application framework.

## Decisions

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
   referenced record ID and uses a real database foreign key. Schema creation is
   supported; migrations are not. Changing a model requires a new disposable
   development database until migrations exist. A future PostgreSQL adapter can
   implement the same small operations without introducing a generic ORM now.
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

The Python runtime core has no third-party dependencies. The `dev` extra adds
pytest (tests), Uvicorn (ASGI development server), and build (distribution checks).
setuptools is the packaging build backend. `requirements-dev.lock` pins the
resolved Python setup dependencies and their transitive dependencies with hashes.

The frontend uses Vue (reactivity/rendering), MIT-licensed PrimeVue 4.5.5 (business
UI controls), and PrimeUIX themes 1.2.5 (the styled theme). PrimeVue 4 is deliberately
pinned; a future major upgrade requires reviewing its license and API changes.
Development dependencies are Vite and its Vue
plugin (SFC compilation/build), Vitest (tests), Vue Test Utils (component mounting),
and jsdom (DOM implementation). `package-lock.json` pins the complete npm tree.

## API and data flow

`Model` definitions → `Registry` → `Database` → `Application` → JSON API → Vue.

- `GET /health`: executes a database probe and returns `{"status":"ok"}`.
- `GET /api/schema`: returns schema version 1 and registered model metadata.
- `GET /api/{model}?limit=50&offset=0`: returns `items`, `limit`, and `offset`.
- `POST /api/{model}`: validates fields and creates a record (201).
- `GET /api/{model}/{id}`: reads one record or returns 404.

Model names are lowercase class names (e.g. `customer`). IDs are server-generated.
Writes accept JSON objects only. Validation errors return 422, uniqueness/foreign
key conflicts 409, unsupported methods 405, and unknown routes/records 404.

## Deferred work

Milestone 2 should add update/delete operations, richer fields, query expressions,
relationship traversal, and explicit migrations. Authentication, permissions,
tenant isolation, serverless hosting, background jobs, realtime, deployment,
benchmarks, and scaling claims remain outside this milestone.
