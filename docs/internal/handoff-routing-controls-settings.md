# Handoff: frontends and routing, Controls, Settings

Date: 01-11-2026. Owner: Aslam.

This builds on the previous handoff (`docs/handoff.md`: Solid UI foundation and the CLI app/instance lifecycle). It covers three things that were designed since then:

1. **Frontends and routing.** Apps can ship several frontends, mount them at paths or on their own hosts, and the instance resolves every route at start-up.
2. **Controls** (`/controls`). The locked admin page for the instance: users, roles, permissions, model customization, naming series, connectors, AI access, jobs and overrides.
3. **Settings** (`/settings`). The per-person settings and the per-app business settings that open from the account menu.

Two interactive mockups come with this file. Put them in `docs/design/`:

- `controls-mockup.html`: Controls.
- `settings-mockup.html`: the workspace shell, account menu and Settings.

Open them in a browser and click through them. They are the reference for layout, wording, states and validation messages. They are plain HTML and JS made for review, so don't copy their code into the product.

---

## 0. Before you start

- Read `AGENTS.md`, `docs/handoff.md`, `docs/ui.md`, `docs/cli.md` and `docs/architecture.md`.
- Keep the stash `Preserve local foundation work before Milestone 1 continuation`.
- Don't create worktrees, merge, deploy or publish unless asked.
- Never touch the working Playground database at `examples/playground/.data/playground.sqlite3`. Use disposable databases for tests and browser checks.
- **The references in this file come from the published wheel `evlet 0.1.2`.** `main` may have moved since then. Before changing a referenced area, open the current file and confirm the behavior described here. If they differ, follow the code and note the difference in the PR.
- **Commits and PRs.**
  - Commits are authored as Aslam / Dxbitz, with no co-author lines.
  - Commit messages, PR descriptions, code comments, README and CHANGELOG contain no generator attribution and no mention of AI tooling.
  - Keep code lean: no verbose comments, no field descriptions.
  - User-facing text is short, plain and human.
- The UI is being rebuilt as Evlet's own renderer (fine-grained reactivity, no virtual DOM). Build every screen here on whichever renderer is current on `main`. Don't add new Solid-only dependencies.

## 1. Current state (what exists today)

| Area | Where | What it does now |
|---|---|---|
| Static UI serving | `evlet/ui/serving.py` `static_response` | Serves one bundled `index.html` for every UI path and `/assets/*` from `evlet/ui/static`, with a fixed CSP. |
| Path dispatch | `evlet/app.py` `Application._dispatch` | `/health`, `/api/ui`, `/api/ui/resolve`, then for GET non-API paths: built-ins `'/', '/login', '/docs', '/settings/accounts'`, `/docs/*`, `/assets/*`, generated `/app/<model>[/new|/<id>[/view]]`, and custom `PageView` routes. Anything else is a 404. |
| Reserved view routes | `evlet/ui/registry.py` `ViewRegistry.register` | Rejects `/` and the first segments `api, app, assets, login, docs, health, settings`. Rejects ambiguous routes using `ambiguous()`. |
| View resolution | `evlet/ui/registry.py` `ViewRegistry.resolve` | Custom view routes first, then the `/app/...` pattern. |
| Host and origin checks | `evlet/auth.py` `LocalAuth.__init__`, `LocalAuth.guard` | One `origins` tuple. `hosts` are derived from it. Rejects unknown Host or Origin, cross-site fetches, and non-GET requests without `x-evlet-request: 1`. One cookie name, `evlet_session`. |
| Roles | `evlet/auth.py` `ROLES = ('admin','editor','viewer')` | A fixed tuple, validated on user create/update. Refuses to demote or deactivate the last active admin. |
| Permissions | `evlet/permissions.py` `Rule`, `Policy` | `(role, Model) -> Rule(operations, read_fields, write_fields, scope)`. The default is read for viewer and everything for the other roles. Write needs read. |
| Instance | `evlet/instances.py` `Instance`, `AppDefinition`, `build_instance`, `load_runtime` | `Instance(name, database, storage, domain)`. A single `domain` gives one HTTPS origin and secure cookies. `AppDefinition(models, views, actions, children, attachments, permissions, migrations, tasks, schedules, ui)`. `build_instance` merges apps, rejects duplicate permission rules and schedule names, and builds `Application`. |
| Instance folder | `docs/cli.md` "Instance configuration and isolation" | `instances/<name>/config.py`, `apps.lock`, `data/database.sqlite3`, `data/backups/`, `files/private/`. |
| Tenancy | `evlet/tenancy.py` | A separate registry of tenants and `domains` (managed and custom, with DNS TXT verification). Host goes to tenant. |
| Fields | `evlet/fields.py` | `Text, Integer, Decimal, Currency, Boolean, Date, DateTime, Select, Email, URL, LongText, Link`. Fields take `label, help_text, placeholder, hidden, read_only, required, default, unique, index`. There is no multi-select field. |
| Model layout | `evlet/ui/registry.py` `generated_layout` | Uses `model["layout"]` groups (`label`, `fields`) or one "Details" group. Form section ids are `main_details`, `section_2`, ... (`docs/ui.md`). |
| MCP | `evlet/mcp.py`, `evlet/connector.py` | stdio only. Calls the authenticated HTTP API with a CLI session (`evlet login`). Has 9 tools, 4 of them writes, gated by `allow_writes`. Rate limit is 60 calls per minute. |
| Jobs | `evlet/jobs.py`, `docs/jobs-realtime.md` | Queue, interval schedules, `set_schedule_active`, retry of failed jobs. |

Existing behavior to keep working through every phase:

- `/`, `/login`, `/docs`, `/app/...` and custom `PageView` routes still resolve as they do now, on an instance with no `routes.py`.
- `/settings/accounts` keeps working until Phase 3, when it redirects (301) to `/controls/users`.
- Every `/api/*` endpoint keeps its path and contract.

## 2. Terms

| Term | Meaning |
|---|---|
| Frontend | A browser entry point declared by an app or the framework. It has a name, a kind, a default path, an auth realm and an API scope. |
| Host | A hostname served by the instance. There is exactly one primary host; the others are sub-hosts such as `portal.acme.com`. |
| Route table | The frozen result of resolving every host and path at start-up. |
| Realm | The sign-in space a frontend belongs to: `desk` (staff), `portal` (customers) or `public` (no sign-in). |
| Override | A value stored in the instance database that replaces a value from an app file, for this instance only. |
| Instance field | A field added to a model from Controls. Its name always starts with `x_`. |
| Naming series | Rules that generate a record's name field, for example `INV-2026-0143`. |

---

## 3. Phase 1: frontends and the route table

### 3.1 Frontend declaration

Add `evlet/routing.py` (or `evlet/web/`, your call) and export it from `evlet`.

```python
from evlet import Frontend

crm = Frontend("crm", kind="evlet", path="/crm", title="CRM")
customer_portal = Frontend(
    "customer_portal", kind="static", dist="portal/dist",
    path="/customer-portal", auth="portal", api=("crm.portal",),
)
site = Frontend("site", kind="server", handler=render_site, path="/site", auth=None)
```

Fields:

| Field | Rule |
|---|---|
| `name` | Lowercase identifier. Unique within its app. Its global id is `<app>.<name>`. |
| `kind` | One of the three below. |
| `path` | The default mount path. Exactly one segment, for example `/crm`. `/` is not allowed as a default; only `routes.py` can mount at a host root. |
| `auth` | `"desk"` (default), `"portal"`, or `None` for public. Optional `roles=(...)` narrows it further. |
| `api` | API scopes this frontend may call when it is served on a sub-host (see 3.7). Defaults to the desk API. |
| `title`, `icon` | Used in navigation, Controls and the account menu. |

The three kinds:

- **`evlet`**: the framework UI shell. It is scoped to this frontend's models and views, using the existing `views` and `ui=ViewRegistry` from the app. Generated routes live under its base: `<base>/<model>`, `<base>/<model>/new`, `<base>/<model>/<id>`, `<base>/<model>/<id>/view`. A `PageView` route is relative to the base.
- **`static`**: a prebuilt folder inside the app package, built with any library. The app builds it; Evlet never runs Node.
  - Unmatched paths under the base fall back to the folder's `index.html`.
  - Serve it with the same path traversal guard as `static_response`.
  - Allowed types: html, js, css, svg, png, jpg, webp, ico, woff2, json and map.
- **`server`**: `handler(request) -> Response`. This is trusted Python that returns HTML. Use it for pages that must render on the server, such as public pages that need SEO.
  - `request` carries `path`, `query`, `host`, `user` (or `None`) and `params`.
  - Handlers run in a thread pool, not on the event loop.

Register frontends explicitly, the same way models are registered, with no file scanning:

```python
# apps/crm/crm/hooks.py
from .frontends import crm, customer_portal
app = AppDefinition(..., frontends=(crm, customer_portal))
```

Add `frontends: tuple = ()` to `AppDefinition` in `evlet/instances.py`.

The framework declares its own frontends in core (see 3.3). The existing bundled UI becomes the framework frontend `evlet.desk`, mounted at `/app`. It shows every permitted model, exactly as `/app/...` behaves today.

### 3.2 Runtime config for every frontend

Every page the framework serves (all three kinds) gets one inline JSON block in `<head>`. It is not executable:

```html
<script id="evlet-config" type="application/json">
{"frontend":"crm.customer_portal","base":"/","api":"/api","realm":"portal","host":"portal.acme.com","version":"0.3.0"}
</script>
```

- The router base, links and API calls must read `base` and `api` from this block. Nothing may hardcode `/app` or a host.
- This lets one build run at `/customer-portal` on the primary host and at `/` on `portal.acme.com`.
- Update the current UI runtime's router and API client to use it. That is the only change the existing shell needs in this phase.
- The CSP stays as strict as `static_response` today. A `static` frontend may add `connect_src` hosts through `Frontend(csp={"connect-src": (...)})`. Nothing else can be loosened.

### 3.3 Reserved paths

This replaces the hardcoded lists in `ViewRegistry.register` and `Application._dispatch` with one table in core (`evlet/routing.py: RESERVED`):

| Path (first segment) | Status | Owner |
|---|---|---|
| `/controls` | Locked. Primary host only. | Framework, Phase 3 |
| `/settings` | Locked | Framework, Phase 4 |
| `/api` | Locked | Framework |
| `/app` | Locked | `evlet.desk` (generated screens) |
| `/assets`, `/files` | Locked | Framework |
| `/login`, `/logout`, `/auth`, `/setup` | Locked | Framework |
| `/health`, `/ws`, `/.well-known` | Locked | Framework |
| `/form` | Locked | Framework web forms (later) |
| `/kb` | Locked | Framework knowledge base (later) |
| `/docs` | Framework. Instance can hide, move or replace it. | Framework |
| `/report`, `/dashboard`, `/search`, `/print` | Reserved. 404 until built. | Framework (planned) |
| anything starting with `_` (for example `/_/mcp`) | Locked | Framework internals |

Rules:

- Match per segment and ignore case. `/api` blocks `/api` and `/API/x`, not `/apis`.
- No app frontend, `PageView` route or `routes.py` entry may use a locked or reserved path. Boot fails with a message that names the app and the path.
- New framework internals go under `/_/`, so this public list stops growing.
- **Docs default.** `/docs` is on in development and off when `Instance.domain` is set, unless `routes.py` turns it on. Public docs show the framework version to anyone probing.

### 3.4 Instance routes file

Create `instances/<name>/routes.py`. It is optional. With no file, every frontend mounts at its default path on the primary host, and `/` redirects to `/app` (today's behavior).

```python
from evlet import Host, Docs, ControlsAccess
from crm.frontends import crm, customer_portal

primary = Host("erp.acme.com", root=crm)          # "/" redirects to /crm
hosts = (
    Host("portal.acme.com", root=customer_portal),  # served at portal.acme.com/
)
paths = {crm: "/sales"}                               # optional moves
docs = Docs(enabled=False)                            # or Docs(path="/help", roles=("admin",))
controls = ControlsAccess(allow_ips=("10.0.0.0/8",), require_two_step=True)
```

- `primary` replaces `Instance.domain` for routing. Keep `Instance.domain` working: if `routes.py` has no `primary`, use `Host(Instance.domain)`. If both are set and differ, boot fails.
- A frontend moved to a sub-host root is **removed** from the primary host by default. `Host(..., root=fe, also_on_primary=True)` keeps both addresses.
- `ControlsAccess` can only tighten access (see 4.2). Its path and role gate can't change.
- `routes.py` is trusted code. Import it like `config.py`, under the same symlink and read checks as `read_instance`.

### 3.5 Resolution

Add `resolve_routes(definitions, instance_config, routes_module) -> RouteTable` in `evlet/routing.py`. Call it from `build_instance` on every start. Never cache it to disk. It runs in this order:

1. Core locked and framework routes (`RESERVED`, `evlet.desk`, `evlet.docs`, `evlet.controls`, `evlet.settings`).
2. Every app's frontends at their default paths on the primary host, in `apps.lock` order.
3. Moves and hosts from `routes.py`.
4. The clash check across the whole table, per host and per first segment. Any duplicate or reserved hit fails boot, for example: `crm.portal and sales.portal both want /portal on erp.acme.com. Move one in instances/acme/routes.py.`
5. Freeze. `RouteTable` is immutable and exposes:
   - `match(host, path) -> (frontend, base, rest)` or `None`
   - `hosts()`
   - `origins()`
   - `rows()`, for the CLI and Controls.

The `RouteTable` rows are the single source for:

- `LocalAuth` origins and hosts
- dispatch
- `evlet routes`
- `evlet proxy`
- the Controls Routing page.

### 3.6 Dispatch

Change `Application._dispatch` (`evlet/app.py`):

1. Look up the host in the route table. If the host is unknown, keep today's 403 from `guard`.
2. `/health` and `/api/*` behave as now, but the API is filtered by the host's API scope (3.7).
3. For GET on other paths, match the longest mounted base on that host:
   - `evlet` kind: serve the shell `index.html` with the config block. The client resolves the view through `/api/ui/resolve?path=...&frontend=...`.
   - `static` kind: serve the file, or `index.html` when there is no match.
   - `server` kind: call the handler.
   - No match: 404.
4. The host root `/` either redirects to the host's `root` frontend base or serves it, when `root` was set on a sub-host.

`ViewRegistry.resolve` gains a `frontend` argument. With `frontend="evlet.desk"`, the current `/app/...` pattern applies unchanged.

### 3.7 Auth realms, cookies and API scope

Changes to `evlet/auth.py`:

- **Origins and hosts.** `LocalAuth(origins=...)` takes the full origin list from `RouteTable.origins()`. `guard` stays as it is.
- **Cookies.** Keep them host-only, with no `Domain` attribute, so `portal.acme.com` and `erp.acme.com` never share a session. Keep the cookie name per realm: `evlet_session` (desk) and `evlet_portal_session`.
- **Realm column.** Add `realm` to `evlet_sessions`.
  - Login on a host whose frontend realm is `portal` only accepts users whose role realm is `portal`.
  - Desk login only accepts desk roles. A mismatch gets the same generic "Wrong username or password" as a bad password.
- **API scope on sub-hosts.** `/api/*` on a sub-host only allows:
  - the auth endpoints
  - the endpoints named by its frontends' `api` scopes, for example `crm.portal`, which the app defines as an allowlist of models and actions.

  Everything else returns 404 there.
- **Public endpoints.** Model endpoints are private by default. A frontend with `auth=None` doesn't make any endpoint public.
  - An app opts in per action or endpoint, for example `Action(..., public=True)`.
  - Public endpoints get a built-in rate limit per IP: 30 requests a minute, 300 an hour. This is not optional.

### 3.8 CLI

- `evlet routes <instance>` prints the table. Mark locked rows and show where each row came from:

```
HOST              PATH        SERVES                          SIGN-IN   SOURCE
erp.acme.com      /controls   evlet.controls                  control   locked
erp.acme.com      /app        evlet.desk                      desk      locked
erp.acme.com      /sales      crm.crm                         desk      routes.py
erp.acme.com      /           -> /sales                       desk      routes.py
portal.acme.com   /           crm.customer_portal             portal    routes.py
portal.acme.com   /api/*      crm.portal (scoped)             portal    routes.py
```

- `evlet proxy <instance> --caddy|--nginx [--write PATH] [--check]` prints or writes the reverse proxy config from `RouteTable.hosts()`.
  - Blocks `/controls*` and `/docs*` on sub-hosts at the proxy too.
  - `--check` resolves each host's A/AAAA record and reports whether it points at this server's public IP.
  - It writes no system files without `--write`, and never reloads a service.
  - For many instances on one server, document Caddy on-demand TLS with an `ask` endpoint at `/_/tls-allowed?domain=` that answers from the route tables.

### 3.9 Tests for Phase 1

- An instance with no `routes.py` serves the same URLs as before. Reuse the existing UI route tests.
- Two apps with the same default path fail boot, and the message names both apps.
- An app frontend on a reserved path fails boot. So does a `PageView` on one.
- A sub-host serves its root frontend.
- `/controls` on a sub-host returns 404.
- `/api/<model>` outside the scope returns 404 on a sub-host.
- A portal user can't sign in on the desk host, and a desk user can't sign in on the portal host.
- The portal and desk cookies are host-only and separate.
- A static frontend: path traversal is rejected, unknown types return 404, the SPA fallback works, and the config block is present.
- `evlet routes` output is stable, so use snapshot tests.
- `evlet proxy --caddy` gives a stable snapshot.

---

## 4. Phase 2 and 3: Controls (`/controls`)

The reference is `controls-mockup.html`, with every section in it.

### 4.1 Principles

- **Not a model.** Controls is a framework page with its own code, mounted as `evlet.controls`. It is not generated from a `model.py`.
- **Loaded from apps.** Apps, models, permissions, frontends, connectors, schedules and naming series are read from the installed apps on every start, as now.
- **Overrides live in the database.** Changes are stored in the instance database as overrides. App files are never written.
- **Every override can be undone.** Each one can be reset to the file value, and all of them can be exported as Python for the right app file.
- **Stricter only.** An override can make things stricter than the file, never looser. The rules are listed per area below.
- **Two kinds of change.**
  - Override changes (permissions, fields, layout, list views, naming, security and MCP settings) are drafts. The page shows a change bar (Review, Discard, Save).
  - Instance data (users, roles, mail accounts, connector credentials, API keys, schedule pause, counters) saves straight away.
- **Everything is audited.** Every saved change writes an audit row. The audit log can't be edited or deleted.

### 4.2 Access

- Served on the primary host only. Any other host returns 404, from the route table and the proxy.
- Allowed for users whose role has `opens_controls = true`.
  - `admin` always has it. This can't be changed in the UI, the API or `routes.py`.
  - Portal-realm roles can never have it.
- Everyone else gets 404, not 403. Anonymous users are redirected to `/login?next=/controls`.
- `ControlsAccess` in `routes.py` can add an IP allowlist and require two-step sign-in. It can't remove the role gate.
- **Break glass:** `evlet controls grant <instance> <username>` from the server shell sets that user's role to admin and activates them, with an audit row whose author is `cli`.
- The page and every `/api/_controls/*` endpoint check access on the server. Hiding things in the UI is not access control.

### 4.3 New tables

These live in the instance database. Add them with framework migrations alongside the existing `evlet_*` tables.

```sql
evlet_roles(id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('framework','custom')),
            base TEXT, realm TEXT CHECK(realm IN ('desk','portal')),
            opens_controls INTEGER NOT NULL DEFAULT 0, description TEXT)

evlet_overrides(kind TEXT, key TEXT, value TEXT NOT NULL,  -- JSON
                changed_by INTEGER, changed_at INTEGER, PRIMARY KEY(kind, key))
-- kind: perm | field | custom | layout | list | naming | setting

evlet_audit(id INTEGER PRIMARY KEY, at INTEGER, actor TEXT, area TEXT,
            target TEXT, detail TEXT, before TEXT, after TEXT)

evlet_config(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)

evlet_counters(model TEXT, prefix TEXT, value INTEGER NOT NULL, last_name TEXT,
               how TEXT CHECK(how IN ('issued','set')), note TEXT,
               changed_by TEXT, changed_at INTEGER, PRIMARY KEY(model, prefix))

evlet_custom_fields(model TEXT, name TEXT, definition TEXT,  -- JSON
                    created_by TEXT, created_at INTEGER, PRIMARY KEY(model, name))

evlet_connectors(id TEXT PRIMARY KEY, app TEXT, status TEXT, settings TEXT,
                 secrets BLOB, changed_by TEXT, changed_at INTEGER)

evlet_api_keys(id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, prefix TEXT,
               hash TEXT, scope TEXT, created_at INTEGER, last_used INTEGER, revoked_at INTEGER)
```

### 4.4 How overrides apply at runtime

- `build_instance` loads the file definitions, then reads `evlet_overrides` and `evlet_custom_fields` and merges them before building `Registry`, `Policy`, the UI metadata and the naming rules. The order is: framework default, then app file, then instance override.
- **Save** runs in one transaction: validate every draft item, write the overrides, write the audit rows, then `UPDATE evlet_config SET version = version + 1`.
- **Picking up changes.** Each process (API and worker) keeps the config version it built with. At the start of each request or job it reads `evlet_config.version`, which is one indexed row. If the version changed, it rebuilds `Policy`, the UI metadata and the naming rules before handling the request.
- **Schema changes.** Instance fields need a registry rebuild, described in 4.8.
- **Live updates.** After a save, publish a `config.changed` event on the existing event stream, so open browsers can reload their metadata.

### 4.5 Roles

- Replace `ROLES` in `evlet/auth.py` with lookups on `evlet_roles`.
- A framework migration seeds `admin`, `editor` and `viewer` as `framework` roles. Framework roles can't be deleted.
- **Custom roles** have a `base` (`editor` or `viewer`) and a `realm`. For any model without an app rule or override, `Policy.rule` falls back to the base role's framework default.
- **App rules for custom roles.** Apps may name a custom role in `permissions.py`, for example `("sales", Lead)`. The role is created on install if it's missing, with the base the app declares: `AppDefinition(roles={"sales": "editor"})`.
- **Deleting a role** is refused while any user has it, and its permission overrides are removed with it.
- **Validation.** Role ids are lowercase letters, digits and `_`, 2 to 31 characters, starting with a letter. `controls` is not allowed.

### 4.6 Controls API

Everything sits under `/api/_controls/`, behind the Controls access check and CSRF. JSON only.

| Endpoint | Purpose |
|---|---|
| `GET /api/_controls/state` | Apps, models, frontends, roles, the route table rows and the attention list. |
| `GET /api/_controls/overrides` | File value, current override, who changed it and when, per kind and key. |
| `POST /api/_controls/overrides` | `{"changes":[{kind,key,value or null}]}`, applied atomically. `null` resets to the file value. The reply lists validation errors per item. |
| `GET /api/_controls/overrides/export` | Python code grouped by target file (4.13). |
| `GET/POST /api/_controls/users`, `PATCH /api/_controls/users/<id>` | Wraps the existing `LocalAuth` user functions. Moves `/settings/accounts` here. |
| `GET/POST/DELETE /api/_controls/roles` | Role management. |
| `GET/POST /api/_controls/naming/<model>/counters` | List counters and set one (4.9). |
| `POST /api/_controls/fields/<model>`, `DELETE /api/_controls/fields/<model>/<name>` | Instance fields (4.8). |
| `GET/PUT /api/_controls/connectors/<id>`, `POST .../test` | Connectors (4.11). |
| `GET/POST/DELETE /api/_controls/keys` | API keys. |
| `GET /api/_controls/jobs`, `POST .../schedules/<name>`, `POST .../jobs/<id>/retry` | Uses the existing `jobs` APIs. |
| `GET /api/_controls/audit?area=&q=&before=` | Paginated, newest first. |

### 4.7 Per-area rules

**Users**

- The list, invite, role, active, two-step status, sessions and password reset link.
- Keep the existing last-active-admin guard. The error message: "This is the only active admin. Make someone else an admin first, so the instance always has one."

**Roles**

- Framework and custom roles, the base role, the realm, user count, the "Opens Controls" switch (admin is locked on) and a link to Permissions.

**Permissions** (kind `perm`, key `role|Model`)

- Value: `{"ops":[...],"read":[fields]|null,"write":[fields]|null,"scope":"<scope id>"|null}`.
- Write needs read, and turning read off clears the others. These match `Policy.__init__` checks.
- If ops has no write, write is `null`. If ops is empty, read and scope are `null`.
- `admin` rules can't be overridden.
- Row scopes are named presets, not free code. Each preset compiles to the existing `scope(user) -> filters` callable:
  - `created_by`
  - `owner`, when the model has `owner`
  - `account_manager`
  - `assignee`
  - `own_customer`, for the portal

  Apps can register more presets with `AppDefinition(scopes={...})`.
- An override equal to the file value is deleted, not stored.

**Security** (kind `setting`)

| Key | File default | Rule |
|---|---|---|
| `security.password_min` | 15 | Can only go up, to 64 at most. |
| `security.session_hours` | 12 | Can only go down. |
| `security.lockout` | 5 | Can only go down, to 3 at least. |
| `security.two_step_roles` | `["admin"]` | Admin can't be removed. |
| `security.controls_ips` | empty | IPs and CIDRs only. |

**Apps** (read only, apart from migrate)

- Version, source, models, frontends, connectors and the number of permission rules.
- When a migration is waiting, a "Back up and migrate" button calls the existing `migrate` code path, which takes a backup first.
- Apps are never installed from the browser. The page shows the `evlet install` command.

**Models**: see 4.8.

**Naming**: see 4.9.

**Routing** (read only)

- The `RouteTable.rows()` table, with the source and locked state of each row.
- Hosts, with DNS and certificate checks using the same code as `evlet proxy --check`.
- "Proxy config" shows the `evlet proxy` output. All edits go through `routes.py`.

**Connectors, Email, MCP & AI, API keys**: see 4.11 and 4.12.

**Jobs**

- Schedules from apps. Pausing one is stored as an override of kind `setting`, key `schedule.<name>.active`, and applied with the existing `set_schedule_active`. "Run now" enqueues a job.
- Recent jobs, and retry for failed ones only. A retry keeps its idempotency key, as now.

**Overrides**

- All overrides, the file value next to the instance value, who changed each and when, Reset, "Show code", and "Export as code".

**Audit log**

- Search and an area filter. Read only.

### 4.8 Model customization

Kind `field`, key `Model.field`. The value is a partial object:

| Prop | Rule |
|---|---|
| `label`, `help_text`, `placeholder` | Free text. Label can't be empty. |
| `hidden` | Allowed, unless the field is required. A hidden field still exists in the API. |
| `required` | Can be turned on. If the file says required, it stays required. Existing empty rows are not back-filled; edits must fill the field. |
| `read_only` | Can be turned on. If the file says read only, it stays read only. |
| `default` | It must pass the field's `clean`. It applies to new records only. |
| `options_add` (Select) | Extra options. They must be lowercase identifiers and not already present. |
| `options_hidden` (Select) | File options hidden from new values. Records that already hold one keep it, and saving such a record unchanged is allowed. At least one option must stay visible. |

- **Enforced on the server.** Apply these inside model validation, so `required`, `read_only`, `default` and the option rules are checked by the API, not only by the form.
- **Metadata.** `/api/schema` and `/api/ui` return the merged values.

**Instance fields** (table `evlet_custom_fields`, override kind `custom` for export)

- **Allowed types:** `Text, LongText, Integer, Decimal, Currency, Date, Boolean, Select, Link`.
- **Name.** It is always `x_` plus a slug, lowercase, 40 characters at most. It must not clash with a file field or a framework column (`id`, `created_by`, `created_at`, `modified_by`, `modified_at`, `tenant`, `revision`).
- **Add.**
  1. Back up the database with the existing backup path.
  2. `ALTER TABLE <table> ADD COLUMN x_... NULL` inside a transaction.
  3. Insert the definition and bump the config version.
  4. Rebuild the registry: build a new `Registry` and `Database` from the merged definitions, then swap it in under a lock between requests.
- **Remove.**
  1. Back up the database.
  2. `ALTER TABLE ... DROP COLUMN`. This needs SQLite 3.35 or later. Check `sqlite_version()` at start-up and disable removal with a clear message if it's older.
  3. Remove the field from every override that references it: list columns, layout, permission field lists and naming conditions.
- **App migrations never touch `x_` columns.** If an app later adds a field with the same name as an instance field without the prefix, nothing clashes. That is why the prefix exists.
- **Required instance fields** can't be added with a required flag while the table has rows unless a default is given.

**Form layout** (kind `layout`, key `Model`)

- Value: `[{"title": str, "fields": [{"name": str, "width": 6|12}]}]`.
- Section ids stay positional (`main_details`, `section_2`, ...), as in `docs/ui.md`, so extensions keep working. If an extension targets a section that no longer exists, it is applied to the last section and the problem is reported on the Controls Overview.
- Every field appears exactly once. Fields that aren't placed are added to the last section on load, so new app fields never disappear.
- A section can't have an empty title. There must be at least one section.
- It maps onto the existing `model["layout"]` groups that `generated_layout` reads.

**List view** (kind `list`, key `Model`)

- Value: `{"columns": [...], "page_size": 25|50|100, "sort": "field"|"-field"}`.
- At least one column. Every column must be a field the role can read; the server drops columns the user can't read.

### 4.9 Naming series

**Declaration in models.py**

```python
from evlet import Model, Naming, Series

class Invoice(Model):
    class Meta:
        naming = Naming("reference", Series("INV-{YYYY}-{####}"), date_field="posting_date")
```

- **The target field** must be a unique `Text` field. It becomes read only in forms and the API: the server always assigns it on create.
- **Imports.** Records created with a name already set (imports, `Database` sessions) keep the name they were given. A trusted session can pass `name=...`; the HTTP API can't.
- **Dates.** `date_field` is optional. Date tokens use that field's value, or the creation time in the instance time zone. Add `Instance(timezone="Asia/Dubai")`, defaulting to UTC.
- **The override** (kind `naming`, key `Model`) replaces the whole series list: `[{"id", "label", "format", "when"}]`.

**Format**

- Tokens:
  - `{YYYY}` and `{YY}`: year
  - `{MM}`: month
  - `{DD}`: day
  - exactly one counter `{#...#}`, where the number of `#` (3 to 8) sets the zero padding
  - `{field_name}`: the cleaned value of a field. Text fields only, with `/` and spaces replaced by `-`.
- 40 characters at most after expansion. Braces must be balanced.

**Series order**

- Series are checked from the top, and the first whose `when` matches is used.
- The last series must have an empty `when`, and it is the only one allowed to. This guarantees every record gets a name. Moving a series below the last one is not allowed.

**Condition language.** Store it as source text. Parse it on save and again on boot.

```
expr    := or
or      := and ("or" and)*
and     := unary ("and" unary)*
unary   := "not" unary | "(" expr ")" | compare
compare := FIELD op value | FIELD "in" "(" value ("," value)* ")"
op      := "==" | "!=" | ">" | ">=" | "<" | "<="
value   := STRING | NUMBER | "true" | "false" | "empty"
STRING  := double or single quoted, no escapes
FIELD   := a field of this model, including x_ fields. A Link compares its id.
```

- No functions, attributes, arithmetic or other identifiers. 500 characters at most, nesting depth 10 at most.
- **Comparing values.** Numeric fields compare as `Decimal`. Text compares exactly. Dates compare as ISO strings. `empty` matches `None` and `""`.
- **Error messages,** matching the mockup:
  - `Use == to compare, not =.`
  - `totl isn't a field on this model. Did you mean total?`
  - `Put text values in quotes, like "export".`
  - `A quote is not closed.`
- **A field that no longer exists.** If a condition names a field that was removed, boot does not fail. That series is skipped and listed on the Controls attention list.
- **Where it runs.** Put the parser and evaluator in `evlet/expressions.py`, separate from `evlet/ui/expressions.py`. It evaluates on the cleaned values before insert, inside the create transaction.

**Counters** (`evlet_counters`)

- There is one counter per `(model, resolved prefix)`. The prefix is the expanded text before the counter token. A new year or month gives a new prefix, so it starts at 1.
- **Issuing a number,** inside the create transaction:

  ```sql
  INSERT INTO evlet_counters(model,prefix,value,last_name,how,changed_by,changed_at)
  VALUES (?,?,1,?, 'issued', ?, ?)
  ON CONFLICT(model,prefix) DO UPDATE SET value=value+1, how='issued', ...
  RETURNING value
  ```

  Then build the name and update `last_name`. On PostgreSQL use the same statement, which is supported.
- **If the name already exists** (from an import), increment and retry, up to 1000 times, then fail with a clear error.
- **Setting a counter.**
  - It needs a reason. It can't go below the number in `last_name`, so names never clash.
  - It writes `how='set'`, the note, who changed it and when, plus an audit row.
- **What the page shows** for each counter: the current value, the next name, and the last change. That is either "`INV-2026-0142` issued by fatima, 01 Nov 2026, 19:52" or "Set by hand by owner" with its note.
- **Try it.** The Controls page has a test panel: the user enters field values and sees which series matches and the name it would produce. Use the same evaluator through `POST /api/_controls/naming/<model>/try`. It must not touch any counter.

### 4.10 Export as code

`GET /api/_controls/overrides/export` returns `[{file, code}]`, grouped by target:

| Kind | Target file | Shape |
|---|---|---|
| `perm` | `<app>/<pkg>/permissions.py` | `rules = {("sales", Invoice): Rule(operations=("read",), read_fields=None, write_fields=None, scope=SCOPES["owner"]), ...}` |
| `field`, `custom`, `naming` | `<app>/<pkg>/models.py` | The field lines with the merged arguments, and `Meta.naming = Naming(...)`. Custom fields lose the `x_` prefix only if the user renames them while moving them. |
| `layout`, `list` | `<app>/<pkg>/views/__init__.py` | `FormView(model=..., layout=FormLayout(children=[...]))`, `ListView(model=..., columns=[...], page_size=..., order_by=...)` |
| `setting` | `instances/<name>/routes.py` or `config.py` | `ControlsAccess(...)`, `Security(...)` |

This is how an override moves into an app: the user pastes the code into the app, deploys it, then resets the override.

### 4.11 Connectors and secrets

- **Declaring connectors.** Apps declare them with `AppDefinition(connectors=(Connector("whatsapp", title=..., fields=(...), test=callable),))`. Framework connectors are SMTP, IMAP, local disk, S3, OIDC sign-in, webhooks and an AI model provider.
- **Field types:** text, number, select, toggle, role, secret.
- **Secrets.**
  - They are stored encrypted in `evlet_connectors.secrets`.
  - The key lives outside the database: `instances/<name>/secret.key` (mode 0600, created on first use), or the `EVLET_SECRET_KEY` environment variable for containers.
  - A secret is never returned by any API, not even to admins. The API only reports `"saved": true`. Replacing one needs the new value.
- **Testing.** `POST .../test` calls the connector's `test` with a 10-second timeout and returns `ok` or a plain-language error.
- **Email accounts** are SMTP/IMAP connectors with several named accounts and one default for outgoing mail. Apps pick an account by name and fall back to the default.

### 4.12 MCP and AI (Controls side)

Settings, all of kind `setting`:

| Key | File default | Rule |
|---|---|---|
| `mcp.enabled` | true | |
| `mcp.allow_writes` | false | Turning it on needs a confirmation step. |
| `mcp.rate` | 60 | 1 to 60. The framework cap stays at 60. |
| `mcp.disabled_tools` | empty | |
| `mcp.hidden_models` | empty | Hidden models never appear to AI clients, even for admins. |

- **Read them in `evlet/mcp.py` and in the remote endpoint.** `allow_writes` only gives access to the write tools. Each write still goes through the user's permissions.
- **Connected clients.** The page lists them per user (`evlet_mcp_clients`, see 5.4). Admins can sign one out. The page also shows counts for the last 24 hours: reads, writes, and calls denied by permissions.

### 4.13 Tests for Controls

- Access:
  - A non-control role gets 404 on the page and on every `/api/_controls/*` endpoint.
  - A sub-host gets 404.
  - The break-glass CLI works.
- Overrides:
  - Applying a set is atomic: one invalid item applies none.
  - Each "stricter only" rule rejects the looser value, one test per rule.
  - The version bump makes a second process pick up the new `Policy`. Test with two `Application` objects on one database.
- Permissions:
  - A custom role falls back to its base role.
  - An app rule for a custom role works.
  - Deleting a role still assigned to users is refused.
- Fields and layout:
  - A required override is enforced by `POST /api/<model>`.
  - A hidden option is rejected on create and kept on an unchanged update.
  - An unplaced field is added to the last section.
- Instance fields:
  - Adding one takes a backup and adds the column. Removing one takes a backup, drops it and cleans the overrides that used it.
  - When SQLite is too old, removal is disabled with a message.
- Naming:
  - Each grammar rule has a test, including every error message listed above.
  - First match wins, and the last series catches everything else.
  - A year change starts a new counter.
  - 50 concurrent creates give no duplicate names.
  - A counter can't be set below the last issued number.
  - "Try it" doesn't change any counter.
- Export: the generated code compiles (`compile()`), and loading it into a temporary app gives the same effective values.

---

## 5. Phase 4 and later: Settings (`/settings`)

The reference is `settings-mockup.html`.

### 5.1 Entry point

- There is one account area: the name and photo at the **bottom of the sidebar** in the desk shell. The top bar has no avatar and no gear icon; it keeps search and notifications.
- Clicking the account area opens a menu, in this order:
  - name and email
  - Your profile, Appearance, Sign-in & security
  - **All settings**
  - Controls (only for roles that open Controls, opening in a new tab)
  - Sign out
- "All settings" opens `/settings/<last page>`. The default is `/settings/profile`.
- `/settings` is the framework frontend `evlet.settings`. Every `evlet`-kind frontend links to it. On a portal host it serves the portal subset: profile, password and notifications.

### 5.2 Pages

| Path | Section | Saves |
|---|---|---|
| `/settings/profile` | Photo (PNG, JPG or WebP, 2 MB at most, kept in private storage), full name, short name, job title, mobile, email signature. Email change goes through a confirmation link to the new address. The username is read only. | Instantly |
| `/settings/appearance` | Theme (light only for now; dark shown as coming later), density, text size, start page, sidebar counts, language (Arabic switches to RTL), time zone, date format, number format, first day of the week | Instantly |
| `/settings/security` | Password change: the instance minimum applies, the current password is needed, other sessions are signed out. Two-step sign-in with TOTP and 8 backup codes; it can't be turned off when the role requires it. Sessions, with sign out per device and everywhere else. | On submit |
| `/settings/notifications` | Event by channel (in Evlet, email), plus quiet hours. Security emails can't be turned off. | Instantly |
| `/settings/email` | Personal mailbox (5.5) | On connect |
| `/settings/ai` | Personal AI assistants (5.4) | Instantly |
| `/settings/keys` | Personal API keys. Shown once. Can't exceed the user's own access. | On create |
| `/settings/app/<app>` | App settings (5.3) | Save bar for "For everyone"; instantly for "Just for you" |

- **Search.** A settings search box covers every label, including app settings, and jumps to the page.
- **Wording.** Copy and validation messages follow the mockup.
- **Applied everywhere.** Date, number and time zone preferences apply in every list and form through the UI runtime, not per screen.

New tables:

```sql
evlet_user_profile(user_id PRIMARY KEY, full_name, short_name, title, phone,
                   photo_file, signature, two_step_secret BLOB, backup_codes TEXT)
evlet_user_prefs(user_id, scope, key, value, PRIMARY KEY(user_id, scope, key))
-- scope 'core' for appearance, region and notifications; '<app>' for app preferences
evlet_email_tokens(token_hash PRIMARY KEY, user_id, new_email, expires)
```

`two_step_secret` and the backup codes use the same secret key as the connectors. Backup codes are stored hashed.

### 5.3 App settings, by module

Apps declare settings in `settings.py` and register them in `AppDefinition`:

```python
from evlet import AppSettings, UserPreferences, Text, Decimal, Integer, Select, Boolean

class BillingSettings(AppSettings):
    editors = ("admin", "accounts")
    sections = {"Tax": ("trn", "vat", "rounding"),
                "Invoices": ("terms", "send_from", "lock", "subject")}
    trn = Text(max_length=15, label="Company TRN", help_text="15 digits, from the FTA certificate")
    vat = Decimal(scale=2, default="5", label="Default VAT rate", suffix="%")
    rounding = Select(["0.01", "0.25", "1"], default="0.01", label="Round invoice totals to")
    terms = Integer(default=30, minimum=0, label="Default payment terms", suffix="days")
    send_from = Select(["accounts@acme.com", "noreply@acme.com", "sender"], default="accounts@acme.com")
    lock = Boolean(default=True, label="Lock invoices once they are sent")
    subject = Text(default="Invoice {reference} from {company}", label="Email subject")

class BillingPreferences(UserPreferences):
    print_format = Select(["standard", "tax", "simple"], default="standard")
    cc_me = Boolean(default=False, label="Copy me on invoices I send")

app = AppDefinition(..., settings=BillingSettings, preferences=BillingPreferences)
```

- **Reuse the field classes.** Use the existing `evlet/fields.py` classes and their `clean` validation.
  - Add `suffix`, matching the existing `prefix` in `docs/ui.md`.
  - Add `MultiSelect`, which the mockup uses for working days and duplicate checks. Store it as a JSON array.
- **"For everyone"** values are stored in `evlet_app_settings(app, key, value, changed_by, changed_at)`.
  - Only roles in `editors` (plus admin) can change them. Everyone else sees them read only, with the lock note from the mockup.
  - Changes go through a save bar ("Save for everyone") and are audited.
  - A value that differs from the default shows "Default: ..." with a reset button.
- **"Just for you"** values are stored in `evlet_user_prefs` with `scope = <app>` and save instantly.
- **Visibility.** A user sees an app's settings page only if they can read at least one of that app's models.
- **Server access.** `services.settings.get("billing", "vat")` and `services.preferences(user).get("billing", "cc_me")` are available to actions, tasks and `server` frontends. Values are cached per config version (4.4).
- **API.**
  - `GET /api/_settings/app/<app>` returns the values, defaults and `can_edit`.
  - `PATCH` is for editors only.
  - `GET/PATCH /api/_settings/me/...` covers the personal pages.

### 5.4 Personal AI assistants (needs remote MCP)

The mockup assumes people connect Claude by pasting `https://<primary>/_/mcp` into a custom connector. Today `evlet/mcp.py` is stdio only. Supporting this needs:

- **A remote endpoint.** A Streamable HTTP MCP endpoint at `/_/mcp`, reusing the existing tool definitions and checks in `evlet/mcp.py`.
- **OAuth for sign-in.** OAuth 2.1 authorization: discovery metadata under `/.well-known/`, dynamic client registration, PKCE, and a consent page in the desk realm. A client signs in as the user who approves it.
- **A clients table:** `evlet_mcp_clients(id, user_id, client_name, created_at, last_used, calls, allow_changes, apps JSON, revoked_at)`.
- **Two limits on changes.**
  - The per-client "Can make changes" switch only works when `mcp.allow_writes` is on.
  - The apps list can only narrow what the user can already see.
- **Sessions.** Connected clients appear in the user's sessions list and in Controls.

This is its own PR (Phase 6). Until then, the AI page shows the stdio steps (`evlet login` then `evlet mcp`) and the clients that use them.

### 5.5 Personal mailboxes (Phase 7)

- **Workspace switch.** Controls → Email gets "Let people connect their own mailbox". This is a `setting` override, default off.
- **Providers.** Microsoft 365 and Google use OAuth. Their client id and secret are connectors in Controls. Any other mailbox uses IMAP/SMTP with an app password, stored encrypted.
- **Options per person:**
  - Send from my address or the shared one.
  - Link replies to records.
  - "Only read emails with customers and leads", on by default.

  With that last option on, only messages whose addresses match a Customer, Contact or Lead are copied in. Nothing else leaves the mailbox.
- **Privacy.** Admins can't read a personal mailbox. Linked messages are visible only to people who can read the record they are linked to.

---

## 6. Order of work

One PR per row. Each must pass the existing suite plus its own tests, build the frontend and wheel, and keep the Playground untouched.

| PR | Scope | Done when |
|---|---|---|
| 1 | `Frontend`, `Host`, `routes.py`, `RESERVED`, `resolve_routes`, `evlet routes`. Dispatch goes through the table, with no behavior change. | Old URLs still work and the clash and reserved tests pass. |
| 2 | Sub-hosts, realms and cookies, API scope, `static` and `server` kinds, the config block in the UI runtime, `evlet proxy`. | All Phase 1 tests (3.9) pass. A sample static portal app works in the Playground project. |
| 3 | `evlet_roles`, overrides, audit, config version, the Controls shell and access, Users, Roles, Permissions, Security, `evlet controls grant`, and the `/settings/accounts` redirect. | The access, override, role and permission tests pass. |
| 4 | Model customization (fields, options, layout, list), instance fields, naming series and counters. | The field, layout, instance field and naming tests pass. |
| 5 | The Apps, Routing, Jobs, Overrides (with export) and Audit pages. | The export compile test passes and every Controls page works on a disposable instance. |
| 6 | Settings: the account menu, profile, appearance and region, security (TOTP), notifications, app settings and preferences, settings search. | The personal pages save, app settings respect `editors`, and region preferences show in lists. |
| 7 | The connector framework, the secret key, SMTP/IMAP shared accounts, API keys (Controls and personal). | Secrets are never returned and connector tests time out cleanly. |
| 8 | Remote MCP at `/_/mcp` with OAuth, `evlet_mcp_clients`, the MCP settings in Controls, the personal AI page. | Claude can connect through a custom connector, and writes stay blocked while `mcp.allow_writes` is off. |
| 9 | Personal mailboxes. | Off by default, and nothing is read outside customers and leads when that option is on. |

## 7. Open decisions

Bring these to Aslam before building the PR they block. The recommendation comes first.

1. **Encryption library (blocks PR 7).** The Python standard library has no AES.
   - Recommended: an optional extra `evlet[secrets]` using `cryptography` (AES-GCM), required only when a secret is saved.
   - Alternative: make `cryptography` a core dependency. That breaks the "core needs only the standard library" rule in `docs/connector.md`.
2. **Instance field prefix (blocks PR 4).** Recommended: `x_`. It's short and can't be confused with framework fields.
3. **Portal users and their customer (blocks PR 2).** The `own_customer` scope needs to know a user's customer.
   - Recommended: a framework table `evlet_user_links(user_id, model, record_id)`, with scope presets reading it.
   - Alternative: each app keeps its own link.
4. **Docs in production (PR 1).** Recommended: off when `Instance.domain` or `routes.primary` is set, on in development.
5. **Who sees AI activity (PR 8).** Recommended: each person sees their own requests in Settings, and admins see only the totals and the client list in Controls.

## 8. Reference: mockup screens to check while building

| Screen | Mockup | Look for |
|---|---|---|
| Controls Overview | controls `#overview` | The attention list and the override count. |
| Permissions | controls `#permissions` | Role chips, source labels, the field and row drawer, the change bar and Review. |
| Models | controls `#models` | The field drawer, Add field, the layout drag and drop with preview, Source with overrides highlighted. |
| Naming | controls `#naming` | Series order, condition errors, Try it, counters with last change and Set counter. |
| Routing | controls `#routing` | Locked rows, host checks, proxy config. |
| Overrides | controls `#overrides` | Reset, Show code, Export as code. |
| Account menu | settings, click the name at the bottom left | Order of items. "Preview the mockup as" is only for review; don't build it. |
| App settings | settings `#app-billing` | "For everyone" and "Just for you", the lock note for non-editors, "Save for everyone". |
| AI assistants | settings `#me-ai` | The connect steps, and the writes-off state. |
