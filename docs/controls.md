# Controls

`/controls` is the locked admin page of an instance. It is the framework frontend
`evlet.controls`, not a generated model. This release has Overview, Users, Roles,
Permissions, Security, Connectors, Email, API keys, Apps, Models, Naming, Routing, Jobs, Overrides and the Audit log.

The Overview lists what needs attention: an app waiting to be migrated or a server that still
runs the old version, overrides that no longer apply (and what fails closed meanwhile), skipped
naming series, connectors and email accounts whose last test failed, screen extensions that no longer fit a form layout, failed jobs, and roles in
`security.two_step_roles` that can't use two-step sign-in because the `cryptography` package is
missing or the secret key file is refused. An application without a secret key (not an instance)
has no two-step sign-in, and nothing is listed for it.

## Access

- Served on the primary host only. Sub-hosts get 404 for the page, every
  `/api/_controls/*` route and `/api/auth/users`, from the route table. Without a primary
  host (development, or a tenant whose factory keeps the default route table) every host
  the instance accepts counts as primary, except sub-hosts. So each tenant of a
  `TenantApplication` manages its own users on its own host, in its own database.
- Only desk roles open Controls. Portal roles never do, whatever their settings.
- Open to roles with "Opens Controls" on. `admin` always has it; it can't be turned off.
- Opening Controls is not the same as being admin. Only admins can give or remove the admin
  role, change admin accounts (role, active, password, sessions), add admin users, create or
  delete roles, change framework roles and turn "Opens Controls" on or off. Other roles that
  open Controls manage the other users, custom role descriptions, overrides and settings.
- Everyone else gets 404 on the page and on every `/api/_controls/*` route. Anonymous
  visitors to the page go to `/login?next=/controls`.
- `ControlsAccess(allow_ips=...)` in `routes.py` and the `security.controls_ips` setting can
  both narrow it; a request must match both lists when both are set. The address checked is
  `LocalAuth.client_address(scope)`: `X-Forwarded-For` counts only when the connection
  comes from one of the instance's `trusted_proxies` ([routing.md](routing.md)). Sign-in
  throttling and the public endpoint limit read the same address.
- `ControlsAccess(require_two_step=True)` opens Controls only to sessions that finished two-step
  sign-in; others get a message that sends them to Settings and back through sign-in. Roles in
  `security.two_step_roles` need such a session too, when the instance can keep secrets.
- Admins can **Reset two-step** for a user (`PATCH /api/_controls/users/<id>`
  `{"two_step": "reset"}`), and `evlet users reset-two-step <instance> <username>` does the same
  from the server shell. Both are audited and end the user's sessions; see
  [settings.md](settings.md#set-up-at-sign-in-trust-on-first-use) for why it exists.
- Break glass from the server shell:

  ```
  evlet controls grant <instance> <username>
  ```

  It makes the user an active admin, ends their sessions, clears the `security.controls_ips`
  setting and writes audit rows by `cli`. It can't change `routes.py`: when `ControlsAccess`
  there limits the networks, the command says so and you edit `routes.py`.
- Saving allowed networks that leave out your own address asks first.

## Roles

`evlet_roles` holds every role, with its realm. The realm decides where its users sign in:
desk roles on desk hosts, portal roles on portal hosts ([routing.md](routing.md)).

| Role | Kind | Realm | Default on a model without a rule |
|---|---|---|---|
| `admin` | framework | desk | Everything. Always opens Controls. |
| `editor` | framework | desk | Read, create, update, delete |
| `viewer` | framework | desk | Read |
| `portal` | framework | portal | Nothing until an app grants it. Never opens Controls. |

Framework roles can't be deleted. `public` is not a role in this table: public endpoints run
as it, no account can have it and no role can take its id. `controls` is reserved too.

A custom role has a base and a realm. Desk roles start from `editor` or `viewer`; portal
roles start from `portal` and never open Controls. On any model without its own rule, a
custom role gets its base role's rule from the app files, so a portal role sees what the app
grants `portal`. Overrides on a framework role do not carry over to custom roles based on it.
Users can have any role in the table; a portal user signs in on portal hosts only.

Apps can declare roles and name them in permissions:

```python
app = AppDefinition(models=(Lead,), roles={"sales": "editor"},
                    permissions={("sales", Lead): Rule(("read", "create"), scope=SCOPES["owner"])})
```

An app may declare a portal role too, `roles={"reseller": "portal"}`. Declared roles are
created on start when missing and can't be deleted in Controls. A role
that users still have can't be deleted; deleting one removes its permission overrides.

## Overrides

Overrides are stored in `evlet_overrides(kind, key, value)` and never change app files.
They can only make a rule stricter. Saving sends every change at once
(`POST /api/_controls/overrides`, `{"changes": [{kind, key, value}]}`, `null` resets); one
invalid change saves none, and the reply names each invalid item as `changes.<index>`.
A value equal to the file value is removed instead of stored. A stored override that no
longer validates (after an app update, say) fails closed: a permission override becomes no
access for that role and model, a setting takes its strictest value, and a policy that can't
be built denies every role but admin. Each one is listed under Needs attention. Each save writes audit rows
and raises `evlet_config.version`; every process reads that version once per request or
job and rebuilds its `Policy` and settings when it moved. Open pages get a
`config.changed` event.

**Permissions** (kind `perm`, key `role|Model`, value
`{"ops": [...], "read": [...] | null, "write": [...] | null, "scope": id | null}`):
operations (`read`, `create`, `update`, `delete`, `journal`, `void`, `revise`), readable and
writable fields must stay within the file rule. Write needs read, no access clears fields and
rows, revise needs create. Admin rules can't be overridden. The page shows the first four;
`journal`, `void` and `revise` named in the file rule are kept on save. A role based on the
editor journals like the editor when its rule reads and updates the model (`docs/journal.md`).
Row scopes are named presets: `created_by`, `owner`, `account_manager` and `assignee` (for
models with that field), `own_customer` and the app's own (`AppDefinition(scopes={...})`).
A row scope set in the app file can't be changed. The `own_customer` preset is
`permissions.own_customer` (with `field="id"` on the customer model itself): it reads the
user's links (`evlet_user_links`) that the session loads with each request, so a user with
no customer links sees no records. Link users with `evlet link-user` or
`LocalAuth.link_user`.

**Security** (kind `setting`):

| Key | File value | Rule |
|---|---|---|
| `security.password_min` | 15 | Up only, 64 at most. Applies to new passwords. |
| `security.session_hours` | 8 | Down only. Sets the session and cookie length in every realm. Lowering it ends sessions already older than the new length, desk and portal alike. |
| `security.lockout` | 10 | Down only, 3 at least. Failed sign-ins per network and account. |
| `security.two_step_roles` | `["admin"]` | Admin stays. These roles must use two-step sign-in ([settings.md](settings.md#two-step-sign-in)) when the instance can keep secrets: people who have not set it up do so at their next sign-in (trust on first use), and Controls needs a verified session. |
| `security.controls_ips` | `[]` | IP addresses and ranges. |

## Models

The Models page changes fields, the form layout and the list view of each model on this
instance. These are overrides too, saved with the change bar.

**Fields** (kind `field`, key `Model.field`). The value holds only what changed:

| Prop | Rule |
|---|---|
| `label`, `help_text`, `placeholder` | Text. A label can't be empty. |
| `hidden` | Left out of forms and lists. The API still returns the field. A required field can't be hidden. |
| `required` | Can be turned on, not off. Rows saved before stay empty until someone edits them; an API create or update must fill the field. |
| `read_only` | Can be turned on, not off. No role can write it through the API; actions and server code still can. |
| `default` | Must pass the field's own check. New records only. Links take no default. |
| `options_add` | Choice fields: new options in lowercase letters, digits and underscores. |
| `options_hidden` | Choice fields: options from the app hidden from new values. A record that holds one keeps it, and saving it unchanged is allowed. One option stays shown, and the default from models.py can't be hidden until another default is set. |

A field can't be made both required and read only without a default, since no one could then
create a record.

Fields the app or the server fills (calculated fields, State, Sequence and naming fields) take
only the label, help text, placeholder and hidden. A field declared with `default=CurrentUser` keeps
that default; Controls can't replace it with a fixed value. Required and hidden options apply to what people
send through the API (create and update, legacy and document models alike); actions, services, jobs
and other app code may still leave a field empty or set a hidden option. Read only applies to every
role in the API. Added options and defaults apply on every write path. `/api/schema` and `/api/ui`
show the merged values (`choices` lists the shown options and `retired_choices` the hidden ones);
sections and labels of fields a role can't read are left out.

**Form layout** (kind `layout`, key `Model`): `[{"title": ..., "fields": [{"name": ..., "width": 6 | 12}]}]`,
1 to 12 sections, each with a title and at least one field, each field placed once. Fields placed nowhere, such as a field an
app adds later or an instance field, go to the last section. Without a Controls layout the app file's rule holds: a declared
`Meta.layout` leaves such fields to its Other group (added when the file layout has none), and the default Details group
takes every field. Section ids stay positional (`main_details`, `section_2`, ...).
The browser renders forms from this layout; `width: 12` makes a field full width.

**List view** (kind `list`, key `Model`): `{"columns": [...], "page_size": 25 | 50 | 100, "sort": "field" | "-field"}`,
1 to 8 columns. `/api/schema` drops the columns a role can't read and gives `page_size` and `order_by`.
The sort is also the server's order for a list request that names none, when the role can read it.

### Instance fields

Add field creates a column on this instance only, named `x_` plus a slug, 40 characters at most.
App models can't declare `x_` names, so an app never clashes with one, and app migrations and
schema checks leave the columns listed in `evlet_custom_fields` alone. A name may not match an
existing column ignoring case, a Reference's `<name>_model` and `<name>_id` included. Types: Text,
LongText, Integer, Decimal, Currency, Date, Boolean, Select and Link.

Adding and removing save at once, one at a time: a SQLite backup into `data/backups/` (next to the
database file), then `ALTER TABLE` in a transaction with the definition in `evlet_custom_fields`, an
audit row and a new config version. Model classes from app code are never changed: each database
keeps its own set of instance fields (with the options Controls added and its naming series) and
makes it active for each request, job and transaction, so applications that share model classes
stay apart. A change waits for every request, job and event page in flight in its process and holds
new ones until it is done, so one slow request delays it; add and remove fields at a quiet time.
Requests that start after it see the new set; other processes pick it up when they see the new version. A record keeps the instance fields
it was built with, so reading them works after its transaction ends; instance fields are read only on
a record, like other fields (use `Database.update`). Code outside a request, job or transaction, such
as a thread started inside a request or a record built before it is written, runs inside
`with database.activated():` to see the instance fields. A
required field needs a default when the table has rows; the default fills them. Required is asked
for on people's API writes only, so app code that does not know the field keeps working. Removing drops the column and takes the field out of every override that names it:
its own field override, list columns and sort, layout, permission field lists (a role left with no
readable field loses access) and naming series that use it. Removal needs SQLite 3.35 or later; on
an older SQLite the page says so and only adding works. On PostgreSQL the change runs in one
transaction and backups are the operator's job. Each tenant has its own instance fields.

Instance fields are plain values. No formula reads them, so finite calculation plans leave them
out, and `Recompute` neither names nor changes them; it still skips records with posted effects. A
posted or locked record follows the same write rules for an instance field as for any other field.

### When an override no longer applies

An override that fails its check on start fails closed and is listed under Needs attention:
a field override makes the field read only for every role until it is fixed or reset; a layout,
list or naming override falls back to the app file. A naming series whose condition names a
field that no longer exists is skipped, and the rest of the list still applies.

## Naming

Models with `Meta.naming` (see [Document numbers](sequences.md#naming-series)) appear on the
Naming page. The series list is an override (kind `naming`, key `Model`):
`[{"id", "label", "format", "when"}]`. Series are checked from the top and the first whose
condition matches names the record; the last series has no condition, and only it. Conditions
and formats are checked when saved and again on start.

Counters are instance data and save at once. Each shows its current value, the next name and the
last change: the last name issued and who issued it, or "Set by hand" with its reason. Setting a
counter needs a reason, writes an audit row and can't go below the number in the last issued
name. Try it shows which series a record would use and its name; it never changes a counter.

## Apps

Read only. Each app shows its version, source, models, frontends,
connectors, schedules and number of permission rules. Apps are installed from the server shell
(`evlet install <app> --instance <name>`), never from the browser.

When an app's source no longer matches `apps.lock`, it is waiting to be migrated. The page and
Needs attention show the command; migrations never run from the browser. Stop the server first,
since Evlet never changes the database under a running server, then run `evlet migrate <name>`
on the server. It takes a backup into `data/backups/` before the migrations. If a server still
runs the version it started with, the page says to restart it.

## Routing

Read only. The route table (`RouteTable.rows()`) with each row's sign-in realm, source and
locked state, and the hosts: the primary host, each sub-host and the sign-in realm it serves
(`RouteTable.realm`). Proxy config and the host check (DNS, the same report as
`evlet proxy --check`, and the certificate of each host that points here) use `evlet proxy`.
Both need a primary host (`routes.py` or `Instance.domain`); without one the page shows why.
Each person can check hosts once every 10 seconds, and each DNS lookup gives up after 5 seconds.
Change routes in the instance's `routes.py`.

## Jobs

Schedules from the apps, and the last 50 jobs. Pausing a schedule saves at once as the setting
override `schedule.<name>.active = false` and is applied with `set_schedule_active`; resuming
resets it. A pause for a schedule no app declares any more is listed under Needs attention.
Run now enqueues one job like the schedule would; a paused schedule must be resumed first.
The failed count in the menu and Needs attention covers every failed job. Retry works on failed jobs only, and the job
keeps its idempotency key.

## Overrides

Every saved override with the app file value next to this instance's value, who changed it and
when. Reset goes through the change bar like any other change. Instance fields are listed too;
they are removed on the Models page.

**Export as code** (`GET /api/_controls/overrides/export`, or `?kind=&key=` for one) returns
`[{file, code}]` grouped by the file each change belongs in:

| Kind | File | Code |
|---|---|---|
| `perm` | `apps/<app>/<package>/permissions.py` | `rules = {("editor", Lead): Rule(operations=..., read_fields=..., write_fields=..., scope=...)}`. The `own_customer` preset is written as the public `own_customer()`, or `own_customer(field='id')` on the customer model |
| `field`, `custom`, `naming` | The file that declares the model, such as `apps/notes/models/item.py` | The field lines as models.py writes them, with the changes merged in, and `Meta.naming = Naming(...)`. Instance fields keep their `x_` name; rename them while moving them, since apps can't declare `x_` names. Hidden options are a comment. Arguments set in code, such as a callable default or validator, are written as `...` with a comment to copy them from models.py, so the code always compiles. |
| `layout`, `list` | `apps/<app>/<package>/views/__init__.py` | `FormView(model=..., layout=Column(children=[FormLayout(...)]))`, `ListView(model=..., columns=..., page_size=..., order_by=...)` |
| `setting` | `instances/<name>/routes.py` or `config.py` | `ControlsAccess(allow_ips=...)`; `Security(...)`, which config.py does not read yet; a comment for a paused schedule |

Paste the code, deploy, then reset the overrides. Overrides that no longer apply are left out.

## Connectors and secrets

Connectors are connections to other services. Apps declare them; the framework declares
SMTP (email sending) and IMAP (email receiving). Settings and secrets are instance data:
they save at once, with an audit row, and are stored in
`evlet_connectors(id, app, status, settings, secrets, changed_by, changed_at)`.

```python
from evlet import AppDefinition, ConnectionFailed, Connector, ConnectorField

def check(values):
    if not gateway_accepts(values["gateway"], values["api_key"]):
        raise ConnectionFailed("The gateway rejected the API key.")

app = AppDefinition(models=(Lead,), connectors=(
    Connector("whatsapp", title="WhatsApp", category="Messaging", test=check, fields=(
        ConnectorField("gateway", "Gateway URL"),
        ConnectorField("api_key", "API key", "secret"),
        ConnectorField("mode", "Mode", "select", options=("live", "test"), default="live"),
        ConnectorField("lead", "Create a Lead for new numbers", "toggle", default=True, required=False),
    )),
))
```

Field types are `text`, `number`, `select`, `toggle`, `role` (a desk role other than admin)
and `secret`. `Application(connectors=...)` registers them for an application built in code.
Trusted server code reads a saved connector with `business.connectors.values("whatsapp")`
(settings and decrypted secrets, or `None` when it isn't set up).

- **Secrets are write only.** No API returns one, not even to admins: a secret field reads
  `{"saved": true}`. Leaving a secret out keeps the saved value; replacing it needs the new
  value, and an empty one is refused. Secrets never go into audit rows or log lines.
- **Encryption.** Secrets are encrypted with AES-GCM (the `cryptography` package), bound to
  their connector id, so a changed or moved value is detected and refused. The key lives
  outside the database: `instances/<name>/secret.key`, created with mode 0600 the first time
  a secret is saved, or `EVLET_SECRET_KEY` (32 bytes, URL-safe base64) for containers.
  An application built in code keeps it next to its SQLite file unless it passes
  `Application(secret_key=path)`; a PostgreSQL application needs `EVLET_SECRET_KEY`.
  The instance refuses to start when the key file is a link, belongs to another user, or
  can be read by anyone but its owner.
- **Rotation.** `evlet secrets rotate <instance>` writes a new key and re-encrypts every
  saved secret. The old key stays in the file until all rows are done, so an interruption
  loses nothing. The key file is read on each use, so running processes pick it up. With
  `EVLET_SECRET_KEY`, rotate the key outside Evlet: the variable can hold several keys
  separated by commas; the first one encrypts and any of them decrypts.
- **Testing.** `POST /api/_controls/connectors/<id>/test` runs the connector's `test` on the
  saved values, or on draft values sent in the body, and answers within 10 seconds with
  `{"ok": true, "message": ...}` or `{"ok": false, "error": ...}` in plain words. A test of
  exactly the saved values sets the status Controls shows. A test still running at
  10 seconds is abandoned.
- **Not yet.** The handoff also lists local disk, S3, single sign-on (OIDC), webhooks and an
  AI model provider as framework connectors. They are not declared yet: each needs code
  that uses its settings, which arrives with the feature that needs it.

## Email

Email accounts are SMTP (outgoing) and IMAP (incoming) connectors with several named
accounts each: ids `smtp:<name>` and `imap:<name>`, where the name is lowercase letters,
digits, `-` or `_`. Each has an address, server, port, security (STARTTLS, SSL/TLS, or none
for a server on this machine only), an optional username and a password. The first outgoing
account becomes the default; Make default moves it. The default can't be removed while other
outgoing accounts exist. Apps send through an account by name and fall back to the default:

```python
services.connectors.send_mail("customer@example.com", "Invoice INV-0142", text, account="accounts")
```

`send_mail(to, subject, text, *, account=None, html=None, reply_to=None)` takes one address
or up to 50, returns the Message-ID and raises `ConnectionFailed` with a plain message when
the server refuses. `services` is the namespace an app's `actions(services)` receives; in code,
use `app.business.connectors`. Sending a test from the Email page posts `{"to": address}` to
the account's test route. Reading mail from IMAP accounts arrives later; today the IMAP test
signs in and opens the inbox read only.

## API keys

Keys let scripts call the API as a user: `Authorization: Bearer evk_...`. A key is shown
once when it is created; only its prefix (`evk_` and 8 characters) and the SHA-256 of the
whole key are stored in `evlet_api_keys`.

- **Never more than the user.** A key acts as its user with that user's current role, row
  scopes and field rules, narrowed by its scope:
  `{"access": "read" | "write", "models": null | ["Customer", ...]}`. A read key refuses
  every request that isn't GET. A key with models sees only those models and their child
  rows. When the user loses access or is deactivated, so does the key. Creating a key for
  a model the user can't read, or a write key for a user who can only read, is refused.
- **Where keys work.** Every `/api` route that a session can use, without CSRF headers. Keys
  never open Controls (404) and can't use `/api/auth/*` or `/api/_settings/*` (403).
- **Limits.** 300 requests a minute per key, and 20 invalid keys a minute per network, per
  process; then 429. At most 20 active keys per user. `last_used` is updated at most once a
  minute.
- **Controls** lists every key and creates keys that act as any active user; only admins
  create or revoke keys that act as admins. **Personal keys** use
  `GET/POST /api/_settings/me/keys` and `DELETE /api/_settings/me/keys/<id>`, for the person
  signed in. Revoking is immediate: the key gets 401 from then on. Creating and revoking
  write audit rows, without the key.

The personal page is the `MyKeysPage` component (`frontend/src/evlet/settings/keys.ts`),
ready for the Settings shell.

## Audit log

`evlet_audit` is append-only: triggers refuse update, delete and replace. Users, roles,
overrides, connectors, email accounts, API keys, migrations and jobs all write to it. The Audit log page searches who, what and the
detail, filters by area and pages newest first, 50 rows at a time.

## API

| Route | Purpose |
|---|---|
| `GET /api/_controls/state` | Instance, apps, models, roles, routes, settings, attention list, recent audit rows |
| `GET/POST /api/_controls/overrides` | File value, override, who and when; save changes |
| `GET/POST /api/_controls/users`, `PATCH /api/_controls/users/<id>` | Wraps `LocalAuth`: role, active, password, `{"sessions": "end"}` |
| `/api/auth/users` | The older account routes, kept. They use the same access check, admin rules and audit rows; refused access is a 403 there |
| `GET/POST /api/_controls/roles`, `PATCH/DELETE /api/_controls/roles/<id>` | Roles, "Opens Controls" and descriptions |
| `POST /api/_controls/fields/<Model>`, `DELETE /api/_controls/fields/<Model>/<x_name>` | Add or remove an instance field: `{name, type, label, help_text, required, default, options, target}` |
| `GET/POST /api/_controls/naming/<Model>/counters` | Counters; set one with `{prefix, value, note}` |
| `POST /api/_controls/naming/<Model>/try` | `{values, series?}`: the matching series and name, and a check of each draft series. No counter changes |
| `GET /api/_controls/overrides/export?kind=&key=` | Export as code: `[{file, code}]` |
| `GET /api/_controls/routing`, `POST /api/_controls/routing/check` | Route table, hosts and proxy config; DNS and certificate check |
| `GET /api/_controls/jobs` | Schedules and recent jobs |
| `POST /api/_controls/jobs/schedules/<name>` | `{active}`: pause or resume |
| `POST /api/_controls/jobs/schedules/<name>/run`, `POST /api/_controls/jobs/<id>/retry` | Run now; retry a failed job |
| `GET /api/_controls/audit?area=&q=&before=` | `{items, next, areas}`, newest first |
| `GET /api/_controls/connectors` | Declared connectors with their fields, and every saved connector and email account |
| `GET/PUT/DELETE /api/_controls/connectors/<id>` | One connector (`whatsapp`) or account (`smtp:accounts`): read, save `{"settings", "secrets"}`, disconnect |
| `POST /api/_controls/connectors/<id>/test`, `POST .../default` | Test within 10 seconds; make an outgoing account the default |
| `GET/POST /api/_controls/keys`, `DELETE /api/_controls/keys/<id>` | API keys: list, create `{"name", "user", "scope"}` (the key is in this reply only), revoke |
