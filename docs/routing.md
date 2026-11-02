# Frontends and routing

An instance resolves every host and path once, at start-up, into a frozen route
table. Dispatch, the allowed browser origins and `evlet routes` all read that table.
It is never cached to disk.

An instance with no `routes.py` serves the same URLs as before: the desk shell at
`/`, `/login`, `/docs`, `/app/...`, `/reports/<name>`, `/assets/...` and the
registered Python page routes. `/settings/accounts` redirects (301) to `/controls/users`.

## Frontends

A frontend is a browser entry point. Apps register them explicitly in
`AppDefinition`, the same way they register models:

```python
# apps/crm/crm/frontends.py
from evlet import Frontend

crm = Frontend("crm", path="/crm", title="CRM")
customer_portal = Frontend("customer_portal", kind="static", dist="portal/dist",
                           path="/customer-portal", auth="portal", api=("crm.portal",))

# apps/crm/crm/hooks.py
from .frontends import crm, customer_portal
app = AppDefinition(..., frontends=(crm, customer_portal))
```

| Field | Rule |
| --- | --- |
| `name` | Lowercase identifier, unique in its app. The global id is `<app>.<name>`. |
| `kind` | `evlet`, `static` (needs `dist`, a folder inside the app package) or `server` (needs a callable `handler`). |
| `path` | The default mount path. Exactly one lowercase segment, such as `/crm`. |
| `auth` | `"desk"` (default), `"portal"` or `None` for public. `roles=(...)` narrows it. |
| `api` | API scopes the frontend may call from a sub-host. |
| `title`, `icon` | Navigation labels. |
| `csp` | Static frontends only: `{"connect-src": ("https://...",)}`. |

The framework has its own frontends: `evlet.desk` at `/app` (the generated screens),
`evlet.docs` at `/docs`, `evlet.controls` at `/controls` and `evlet.settings` at
`/settings`.

Status: app frontends are validated and checked for clashes, but they are not served
yet, so an instance with any app frontend fails at boot with a "not available yet"
message naming it. `evlet.controls` serves Controls ([controls.md](controls.md)), and
`evlet.settings` only redirects `/settings/accounts` to `/controls/users`.
Sub-hosts, sign-in realms, API scopes and the `static` and `server` kinds come next.
Flat-layout apps cannot register frontends yet.

## Reserved paths

`evlet.routing.RESERVED` lists the first segments the framework owns. Matching is per
segment and ignores case: `/api` blocks `/api` and `/API/x`, not `/apis`. Any
segment starting with `_` is locked for framework internals, such as `/_/mcp`.

| First segment | Status |
| --- | --- |
| `controls`, `settings`, `api`, `app`, `assets`, `files`, `reports`, `login`, `logout`, `auth`, `setup`, `health`, `ws`, `.well-known`, `form`, `kb` | Locked |
| `docs` | Framework. The instance can turn it off. |
| `report`, `dashboard`, `search`, `print` | Reserved, 404 until built |
| anything starting with `_` | Locked |

The framework serves `/_/mcp`, `/_/oauth/*` and `/.well-known/oauth-*` for AI assistants
([connector.md](connector.md#remote-mcp-with-oauth)), on the primary host only. Other paths
under `/_/` and `/.well-known/` are 404.

No app frontend, `PageView` route or `routes.py` entry may use one. Python page
routes must also start with a fixed segment, not a parameter. Boot fails with a
message that names the app and the path.

## Instance routes file

`instances/<name>/routes.py` is optional trusted Python. It is loaded on every start,
after the apps, under the same checks as `config.py`: it must be a regular file
inside the instance folder, never a symbolic link.

```python
from evlet import Host, Docs, ControlsAccess
from crm.frontends import crm

primary = Host("erp.acme.com", root=crm)   # "/" redirects to /sales
paths = {crm: "/sales"}                      # optional moves
docs = Docs(enabled=False)
controls = ControlsAccess(allow_ips=("10.0.0.0/8",), require_two_step=True)
```

- `primary` names the HTTPS host. With no `primary`, `Instance.domain` is used. When
  both are set they must be the same name, or boot fails. Either one selects the
  HTTPS origin and secure cookies, and allows binding beyond loopback. `evlet dev`
  refuses both.
- `paths` moves app frontends to another single segment. Framework frontends can't move.
- `root` on the primary host will make `/` redirect to that frontend. It fails at
  boot as "not available yet" until app frontends are served.
- `docs`: Docs are on in development and off when a primary host or `Instance.domain`
  is set. `Docs(enabled=True)` or `Docs(enabled=False)` overrides that. Moving docs
  or limiting them to roles is not available yet. `GET /api/auth/session` reports it
  as `application.docs`: `"/docs"` or `null`. With Docs off, the browser starts on
  the first list the role can open and shows no Docs links or shortcut.
- `controls` can only tighten access to Controls. It is stored with the route table
  for the Controls page.
- `hosts` (sub-hosts) are not available yet; boot fails if it is set.
- Any other top-level name fails boot, so a typo such as `primay` is caught. Imports
  (modules, classes, functions, frontends) and names starting with `_` are skipped.
  Each known name must have its type: `paths` a dict, `hosts` a tuple, and so on.
- `evlet dev` refuses an instance whose `routes.py` sets `primary`, before it starts
  the server. Host headers must be plain lowercase `host` or `host:port`; on a primary
  host they must match its origin exactly, the same set `LocalAuth` accepts.

## Resolution

`resolve_routes(definitions, instance_config, routes_module, port=8000)` in
`evlet/routing.py` runs in this order:

1. Framework rows.
2. App frontends at their default paths, in app order.
3. Moves and the primary root from `routes.py`.
4. The clash check across the table, per host and first segment, ignoring case.
   Two apps on one path fail with, for example,
   `crm.portal and sales.portal both want /portal on erp.acme.com. Move one in instances/acme/routes.py.`
   Python pages from any app may share a segment with each other, but not with a frontend.
5. Freeze.

The `RouteTable` is immutable. It exposes `match(host, path)` returning
`(frontend, base, rest)` or `None`, `redirect(host, path)`, `hosts()`, `origins()`
and `rows()`. Paths with no mounted base belong to the desk shell at `/`, which
serves the host root and the Python page routes.

## `evlet routes`

```text
$ evlet routes acme
HOST           PATH        SERVES           SIGN-IN   SOURCE
erp.acme.com   /controls   evlet.controls   control   locked
erp.acme.com   /settings   evlet.settings   desk      locked
erp.acme.com   /app        evlet.desk       desk      locked
erp.acme.com   /reports    evlet.reports    desk      locked
erp.acme.com   /login      evlet.login      public    locked
erp.acme.com   /assets     evlet.assets     public    locked
erp.acme.com   /health     evlet.health     public    locked
erp.acme.com   /api/*      evlet.api        desk      locked
erp.acme.com   /sales      crm.crm          desk      routes.py
erp.acme.com   /           -> /sales        desk      routes.py
```

In development the host column shows `localhost`. `--port` sets the port used for the
loopback origins.
