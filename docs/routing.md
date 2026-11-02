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

How each kind is served:

- `evlet`: the desk shell under the frontend's base, `<base>/<model>`,
  `<base>/<model>/new` and `<base>/<model>/<id>`, with every model the role can read.
  Narrowing the shell to the app's own models comes later.
- `static`: files from `dist`, a folder inside the app package, copied into the build
  generation with the app source. Only html, js, css, svg, png, jpg, webp, ico, woff2,
  json and map files are served; other types, dotfiles, `..` and links that leave the
  folder answer 404. Any path without a file type gets `index.html`, so the app can
  route in the browser. Evlet adds `<base href="<base>/">` to `index.html` unless it
  has one, so relative asset paths work at any mount. The CSP is the desk's, plus the
  frontend's `connect-src` origins.
- `server`: `handler(request)` returns a `Response(body, status=200,
  content_type="text/html; charset=utf-8")` or HTML text. It runs in the worker
  thread pool, never on the event loop. Server frontends answer GET only. `request` has
  `path` (under the base), `query` (the raw query string), `host`, `user` (or `None`)
  and `params` (the query as a dict; a repeated key keeps its last value).

Sign-in for frontends: a signed-in user whose role belongs to another realm than the
frontend's `auth` counts as signed out. A `server` frontend with `auth`, and a frontend
of any kind with `roles=(...)`, needs a matching sign-in to load: visitors who are not
signed in go to `/login` on the desk and get 401 on a portal, and other roles get 404.
`roles=` needs `auth`. A `static` or `evlet` frontend without `roles=` loads for anyone,
so it can show its own sign-in screen; its data still goes through the API checks.

Every page the framework serves gets one non-executable block in `<head>`:

```html
<script id="evlet-config" type="application/json">{"frontend":"crm.customer_portal","base":"/","api":"/api","realm":"portal","host":"portal.acme.com","docs":null,"version":"0.1.3"}</script>
```

The desk shell's router and API client read `base`, `docs` and `api` from it, so one
build runs at `/customer-portal` on the primary host and at `/` on a sub-host. `realm` is
the host's realm, which decides the session cookie: a portal frontend also mounted on
the primary host reports `desk` there.

`evlet.controls` serves Controls ([controls.md](controls.md)), and `evlet.settings` serves
Settings ([settings.md](settings.md)) and redirects `/settings/accounts` to `/controls/users`. Flat-layout apps cannot
register frontends yet.

## Sub-hosts, realms and API scopes

```python
# apps/crm/crm/hooks.py
from evlet import ApiScope
portal_api = ApiScope("portal", models=(Customer, Invoice), actions=((Invoice, "accept"),), endpoints=("crm.contact",))
app = AppDefinition(..., frontends=(crm, customer_portal), api_scopes=(portal_api,))

# instances/acme/routes.py
primary = Host("erp.acme.com", root=crm)
hosts = (Host("portal.acme.com", root=customer_portal),)
```

- A sub-host serves its root frontend at `/`. That frontend leaves the primary host
  unless `also_on_primary=True`. Sub-hosts serve `static` and `server` frontends; the
  framework pages (`/controls`, `/docs`, `/app`, `/settings` and the other reserved
  paths except `/assets`, `/login` and `/logout`) answer 404 there. A portal host also
  serves the portal subset of `/settings` (profile, password, notifications) and
  `/api/_settings/me/...`, and loads the framework's own assets when its root frontend has
  no file of that name.
- In development, sub-hosts are `.localhost` names, such as `portal.localhost`, served
  over the development port. With a primary host they are public names on HTTPS.
- **Realms.** A host's realm is `desk` on the primary host and the root frontend's
  `auth` on a sub-host: `portal`, or `public` when `auth=None`. Each role has a realm in
  `evlet_roles` ([controls.md](controls.md#roles)): the framework `portal` role and custom
  portal roles sign in on portal hosts only, and desk roles on desk hosts only. A
  mismatch gets the same "Invalid username or password" as a wrong password. Public
  hosts have no sign-in. Sessions record their realm in `evlet_sessions.realm`, and a
  session holds only while the user's role is still of that realm.
- **Cookies** are host-only, with no `Domain`, and named per realm: `evlet_session`
  for the desk and `evlet_portal_session` for portals. Sessions and cookies in every
  realm last `security.session_hours` from Controls (8 hours unless lowered).
- **API scope.** `/api` on a sub-host serves `/api/auth/session`, `login` and `logout`,
  plus exactly what its frontend's `api` scopes list. Everything else answers 404 there,
  and permissions and row scopes still apply to every call:

  | Route | Allowed when the scope lists |
  | --- | --- |
  | `GET/POST /api/<model>`, `GET/PATCH/DELETE /api/<model>/<id>` | the model in `models` |
  | `POST /api/<model>/<id>/actions/<name>`, `.../moves/<name>` | `(Model, "<name>")` in `actions` |
  | `POST /api/<model>/<id>/void`, `.../revise` | `(Model, "void")` or `(Model, "revise")` in `actions` |
  | `/api/<model>/measures`, `/api/<model>/aggregate`, `/api/<model>/<id>/measures` | `"measures"` in `extras` |
  | `/api/<model>/<id>/related/...` | `"related"` in `extras` |
  | `/api/<model>/<id>/lines/...` | `"lines"` in `extras` |
  | `/api/<model>/<id>/attachments...` | `"attachments"` in `extras` |
  | `POST /api/_draft/<model>` | `"drafts"` in `extras` |
  | `/api/_services/<name>` | the name in `endpoints` |

  Every route except services also needs the model in `models`. Schema, UI, events,
  jobs, discovery, accounts and setup are never served on a sub-host.
- The `portal` role, roles based on it and the `public` role have no access to any model
  unless an app grants it in its permission rules. Controls, `/api/_controls/*` and
  `/api/auth/users` answer 404 on every sub-host.

### Portal users and their customer

`evlet_user_links(user_id, model, record_id)` links accounts to records. The
`own_customer(field="customer", model="customer")` row scope reads it: a portal user
sees the rows whose `field` holds a record of `model` they are linked to. Use
`field="id"` on the linked model itself.

```python
from evlet import Rule, own_customer
permissions = {
    ("portal", Customer): Rule(("read",), scope=own_customer("id")),
    ("portal", Invoice): Rule(("read",), scope=own_customer()),
}
auth.link_user(user.id, Customer, 42)      # or: evlet link-user acme buyer customer 42
auth.unlink_user(user.id, Customer, 42)
auth.user_links(user.id)                   # (("customer", 42),)
```

Links are read with the session on each request, so a change applies at once. The
Controls row scope preset `own_customer` is this same scope. A list
may filter a Link to a row-scoped model with an `eq` or `in` filter when every id is a
record the user can read, for example `{"customer": 42}` for their own customer. Other
filters and sorting on such a Link are refused with 403, since they could show which
hidden record a row points to.

### Public endpoints

Model endpoints are always private, and `auth=None` on a frontend makes nothing
public. A service endpoint opts in with `@api(..., public=True)`. It runs as the
`public` role, so its permission needs a rule such as `("public", Lead): Rule(("read",
"create"))`. Each client address gets at most 30 calls a minute and 300 an hour on
each public endpoint. This limit is built in and can't be turned off; a refused call
gets 429 with `Retry-After`.

### Client addresses behind a proxy

Behind a reverse proxy every request comes from the proxy. Name it in `config.py`:

```python
instance = Instance(name="acme", domain="erp.acme.com", trusted_proxies=("127.0.0.1", "::1"))
```

When the connection comes from a trusted proxy, the client is the rightmost
`X-Forwarded-For` address that is not itself a trusted proxy, so addresses a client
adds on the left are ignored. Otherwise forwarded headers are ignored. It is empty by
default, and `routes.py` can't set it. The public limit, sign-in throttling, first-run
setup, the Controls allowlist and `/_/tls-allowed` use this address. Python code reads it with
`LocalAuth.client_address(scope)`, or `evlet.auth.client_address(scope, trusted_proxies)`.

The Playground has a working example: `examples/playground/portal` is a static portal
served at `http://portal.localhost:8000` (and `/portal`), limited to customers and
invoices through `own_customer`.

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
- `root` on the primary host makes `/` redirect to that frontend.
- `docs`: Docs are on in development and off when a primary host or `Instance.domain`
  is set. `Docs(enabled=True)` or `Docs(enabled=False)` overrides that.
  `Docs(path="/help")` moves them to another free segment, and `Docs(roles=("admin",))`
  sends visitors who are not signed in to `/login` and answers 404 to other roles.
  Docs are never served on sub-hosts.
  `GET /api/auth/session` reports Docs as `application.docs`: the path, or `null` when
  Docs are off, on a sub-host, or the user's role is outside `Docs(roles=...)`. With
  Docs off, the browser starts on the first list the role can open and shows no Docs
  links or shortcut.
- `controls` can only tighten access to Controls. It is stored with the route table
  for the Controls page.
- `hosts` lists sub-hosts, each with a root frontend (see above).
- Any other top-level name fails boot, so a typo such as `primay` is caught. Imports
  (modules, classes, functions, frontends) and names starting with `_` are skipped.
  Each known name must have its type: `paths` a dict, `hosts` a tuple, and so on.
- `evlet dev` refuses an instance whose `routes.py` sets `primary`, before it starts
  the server. Host headers must be plain lowercase `host` or `host:port`; on a primary
  host they must match its origin exactly, the same set `LocalAuth` accepts.

## Resolution

`resolve_routes(definitions, instance_config, routes_module, port=8000, roots=None)` in
`evlet/routing.py` runs in this order (`roots` maps app names to their package
folders, where static frontends keep `dist`):

1. Framework rows.
2. App frontends at their default paths, in app order.
3. Moves, sub-hosts and the primary root from `routes.py`.
4. The clash check across the table, per host and first segment, ignoring case.
   Two apps on one path fail with, for example,
   `crm.portal and sales.portal both want /portal on erp.acme.com. Move one in instances/acme/routes.py.`
   Python pages from any app may share a segment with each other, but not with a frontend.
5. Freeze.

The `RouteTable` is immutable. It exposes `match(host, path)` returning
`(frontend, base, rest)` or `None`, `redirect(host, path)`, `hosts()`, `origins()`,
`realm(host)`, `realms()`, `scopes(host)` and `rows()`. Paths with no mounted base
belong to the desk shell at `/`, which serves the host root and the Python page routes.
`LocalAuth(origins=table.origins(), realms=table.realms())` takes its hosts and realms
from the table.

## `evlet routes`

```text
$ evlet routes acme
HOST              PATH        SERVES                SIGN-IN   SOURCE
erp.acme.com      /controls   evlet.controls        control   locked
erp.acme.com      /settings   evlet.settings        desk      locked
erp.acme.com      /app        evlet.desk            desk      locked
erp.acme.com      /reports    evlet.reports         desk      locked
erp.acme.com      /login      evlet.login           public    locked
erp.acme.com      /assets     evlet.assets          public    locked
erp.acme.com      /health     evlet.health          public    locked
erp.acme.com      /api/*      evlet.api             desk      locked
erp.acme.com      /sales      crm.crm               desk      routes.py
erp.acme.com      /           -> /sales             desk      routes.py
portal.acme.com   /           crm.customer_portal   portal    routes.py
portal.acme.com   /api/*      crm.portal (scoped)   portal    routes.py
```

In development the host column shows `localhost`. `--port` sets the port used for the
loopback origins.

## `evlet proxy`

`evlet proxy acme --caddy` (or `--nginx`) prints a reverse proxy configuration for
the instance's primary host and sub-hosts, forwarding to `127.0.0.1:<port>`. It
answers 404 for `/_/*` on every host, and for `/controls*` and `/docs*` on sub-hosts,
as the instance does. Its first line reminds you to set `trusted_proxies`.

```text
# Set Instance(trusted_proxies=("127.0.0.1", "::1")) in instances/acme/config.py so Evlet reads client addresses from X-Forwarded-For.

erp.acme.com {
	@internal path /_/*
	respond @internal 404
	reverse_proxy 127.0.0.1:8000
}

portal.acme.com {
	@internal path /_/*
	respond @internal 404
	@primary_only path_regexp (?i)^/(controls|docs)(/|$)
	respond @primary_only 404
	reverse_proxy 127.0.0.1:8000
}
```

- `--write PATH` writes it to that file. Without it nothing is written, and the
  command never reloads Caddy or nginx; review the file and reload it yourself.
- `--check` resolves each host's A and AAAA records and reports whether they point at
  this server, found from its outgoing address. Behind NAT, pass `--ip` with the public
  address. It exits with status 1 when a host points elsewhere or has no record.
- `--write` creates the file with mode 0644; it holds no secrets.
- The nginx output expects certificates under `/etc/letsencrypt/live/<host>/`, and keeps
  connections open for an hour so the event stream works.

### Many instances on one server

Caddy can get certificates on demand instead of listing every host. Each instance
answers `GET /_/tls-allowed?domain=<host>` from its route table: 200 for its own hosts,
404 otherwise. It answers only when the client address is on this machine, so Caddy
asks it directly at `127.0.0.1:<port>`; the `evlet proxy` output also refuses `/_/*`
from outside, because a proxied request arrives from the loopback address too.

```text
{
	on_demand_tls {
		ask http://127.0.0.1:8000/_/tls-allowed
	}
}

https:// {
	tls {
		on_demand
	}
	reverse_proxy 127.0.0.1:8000
}
```

Caddy takes one `ask` URL, so with several instances on different ports, keep one site
block per instance (the `evlet proxy` output) or put a small script in front that asks
each instance in turn.
