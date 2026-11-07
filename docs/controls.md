# Controls

`/controls` is the locked admin page of an instance. It is the framework frontend
`evlet.controls`, not a generated model. This release has Overview, Users, Roles,
Permissions, Security, Connectors, Email, MCP & AI, API keys, Apps, Models, Naming, Routing, Jobs, Overrides and the Audit log.

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
  throttling and the public endpoint limit read the same address. A local or private peer that
  keeps sending forwarded headers without being trusted is listed under Needs attention, each
  peer on its own with its `trusted_proxies=(...)` line, to add only if that address is your
  reverse proxy; the header stays ignored until then.
- `ControlsAccess(require_two_step=True)` opens Controls only to sessions that finished two-step
  sign-in; others get a message that sends them to Settings and back through sign-in. Roles in
  `security.two_step_roles` need such a session too, when the instance can keep secrets: they
  can't use anything until two-step sign-in is set up (see [settings.md](settings.md#when-the-code-is-asked)).
- **Changes ask for a fresh code.** For a person with two-step sign-in on, every request that
  changes something in Controls (any `POST`, `PUT`, `PATCH` or `DELETE` under `/api/_controls/`,
  except trying a naming series, and under the older account routes `/api/auth/users`) needs a
  code from the last 10 minutes on this session. Signing
  in with a code counts. Otherwise the answer is 403 `VERIFY_TWO_STEP: ...`; the page shows a code
  dialog, sends the code to `POST /api/_settings/me/two-step/verify` and sends the change again.
  Reading Controls never asks. An admin setting someone else's password is a Controls change.
- Admins can **Reset two-step** for a user (`PATCH /api/_controls/users/<id>`
  `{"two_step": "reset"}`), and `evlet users reset-two-step <instance> <username>` does the same
  from the server shell. Both are audited and revoke all the user's access, API keys included
  (see [Ending access](#ending-access)); see
  [settings.md](settings.md#set-up-at-sign-in-trust-on-first-use) for why it exists.
- Break glass from the server shell:

  ```
  evlet controls grant <instance> <username>
  ```

  It makes the user an active admin, revokes all their access including API keys, clears the `security.controls_ips`
  setting and writes audit rows by `cli`. It can't change `routes.py`: when `ControlsAccess`
  there limits the networks, the command says so and you edit `routes.py`.
- Saving allowed networks that leave out your own address asks first.

### Ending access

Every revocation goes through one function, `LocalAuth.end_sessions(connection, user_id, *,
keep=None, keys=False)`. It always ends the person's browser sessions, their connected AI
assistants (access and refresh tokens) and their unused authorization codes. `keys=True` also
revokes their API keys.

| What happened | Browser sessions | AI assistants and codes | API keys |
|---|---|---|---|
| Password changed in Settings | Others end, this one stays | End | Kept, unless "Also revoke API keys" is ticked (`"revoke_keys": true`) |
| Controls: Sign out everywhere | End | End | Kept, unless "Also revoke API keys" is ticked (`"revoke_keys": true`) |
| Controls: password set, role change | End | End | Kept |
| Controls: Reset two-step, `evlet users reset-two-step` | End | End | Revoked |
| Controls: Deactivate | End | End | Revoked, and stay revoked if the person is activated again |
| `evlet controls grant` (break glass) | End | End | Revoked |

The two choices show a checkbox, "Also revoke API keys (N active)", unticked by default; the
routes take an explicit `revoke_keys` boolean that defaults to false, and the audit row says how
many keys were revoked. Keys are kept by default because scripts usually belong to the person
on purpose. A two-step reset, deactivation and the break-glass grant are how an account is
taken back, so whatever the other party could have created always goes too.

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
They can only make a rule stricter than the app files. Instance fields are the exception, since
Controls owns them: see [Permissions](#overrides) below. Saving sends every change at once
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
operations (`read`, `create`, `update`, `delete`, `journal`, `void`, `revise`, `unpost`, `repost`), readable and
writable fields must stay within the file rule. Stricter only applies to the fields declared in
app files. An instance field (`x_`, added by Controls) follows the record for every signed-in
staff role, that is every desk role: each one that reads the model reads it, and each one that
creates or updates the model writes it, also when the app rule lists `read_fields` or
`write_fields`. The file value on the Permissions page includes it, so an override can hide it
from a role or make it read only, like any field. Adding a field also adds it to the field lists
of existing desk-role overrides, so it stays hidden from staff only where an admin unticks it.
Portal roles (customers) and public endpoints never get an instance field by default: an admin
grants it to a portal role with a permission override, and fields added later stay hidden from
it until ticked there.
The API, generated screens, API keys, exports and AI assistants all read fields through the same
`Policy`. Write needs read, no access clears fields and
rows, revise needs create. Admin rules can't be overridden. The page shows the first four;
`journal`, `void`, `revise`, `unpost` and `repost` named in the file rule are kept on save. A role based on the
editor journals like the editor when its rule reads and updates the model (`docs/journal.md`).
Row scopes are named presets: `created_by`, `owner`, `account_manager` and `assignee` (for
models with that field), `own_customer` and the app's own (`AppDefinition(scopes={...})`).
A row scope set in the app file can't be changed. The `own_customer` preset is
`permissions.own_customer` (with `field="id"` on the customer model itself): it reads the
user's links (`evlet_user_links`) that the session loads with each request, so a user with
no customer links sees no records. Link users with `evlet link-user` or
`LocalAuth.link_user`. An app scope may be a team scope (`Hierarchy(...).team(...)`) or an
`any_of(...)` scope; Controls offers it on every model it compiles for, that is, where its
fields and Links lead to the hierarchy model
([business-capabilities.md](business-capabilities.md#team-and-hierarchy-scopes)).

**Security** (kind `setting`). The file value of the first three comes from the instance's
`config.py`, beside `instance = Instance(...)`, or from `LocalAuth(security=...)` in an application
built by hand:

```python
from evlet import Instance, Security

instance = Instance(name="acme", domain="erp.acme.com")
security = Security(session_hours=8, lockout=4, password_min=16)
```

`Security` checks its values: `session_hours` 1 to 168, `lockout` 3 to 50 and `password_min` 15
to 64. Left out, they are 12 hours, 5 and 15. Controls can only make them stricter than the
config value. Change config.py and restart to loosen one.

| Key | File value | Rule |
|---|---|---|
| `security.password_min` | 15, or `Security(password_min=...)` | Up only, 64 at most. Applies to new passwords. |
| `security.session_hours` | 12, or `Security(session_hours=...)` | Down only, 1 at least. Sets the session and cookie length in every realm. Lowering it ends sessions already older than the new length, desk and portal alike. |
| `security.lockout` | 5, or `Security(lockout=...)` | Down only, 3 at least. Failed sign-ins per network and account, and wrong current passwords per account in Settings. |
| `security.two_step_roles` | `["admin"]` | Admin stays. These roles must use two-step sign-in ([settings.md](settings.md#two-step-sign-in)) when the instance can keep secrets: people who have not set it up do so at their next sign-in (trust on first use), and Controls needs a verified session. |
| `security.controls_ips` | `[]` | IP addresses and ranges. |

## Models

The Models page changes fields, the form layout and the list view of each model on this
instance. These are overrides too, saved with the change bar.

**Fields** (kind `field`, key `Model.field`). The value holds only what changed:

| Prop | Rule |
|---|---|
| `label`, `help_text`, `tooltip`, `placeholder` | Text of at most 200 characters (80 for a label). A label can't be empty. |
| `hidden` | Left out of forms and lists. The API still returns the field. A required field can't be hidden. |
| `required` | Can be turned on, not off. Rows saved before stay empty until someone edits them; an API create or update must fill the field (see below). |
| `read_only` | Can be turned on, not off. No role can write it through the API; actions and server code still can. |
| `default` | Must pass the field's own check. New records only. Links take no default. |
| `options_add` | Choice fields: new options in lowercase letters, digits and underscores. |
| `options_hidden` | Choice fields: options from the app hidden from new values. A record that holds one keeps it, and saving it unchanged is allowed. One option stays shown, and the default from models.py can't be hidden until another default is set. |

**Turning on required affects existing records.** Records saved before keep an empty value.
The next time anyone changes such a record through the API, the PATCH must also fill the
field, even when it only changes something else; otherwise it is refused with "This field is
required" (422). Actions, jobs and other app code are not checked. Fill old records first, or expect
people to be asked for the value the next time they save one.

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

**Form layout** (kind `layout`, key `Model`): `[{"title": ..., "fields": [{"name": ..., "width": 6 | 12}], "collapsible": true, "collapsed": false | true | "empty"}]`,
1 to 12 sections, each with a title and at least one field, each field placed once. Fields placed nowhere, such as a field an
app adds later or an instance field, go to the last section. Without a Controls layout the app file's rule holds: a declared
`Meta.layout` leaves such fields to its Other group (added when the file layout has none), and the default Details group
takes every field. Section ids stay positional (`main_details`, `section_2`, ...).
The browser renders forms from this layout; `width: 12` makes a field full width.
Each section has **Can collapse** (`collapsible`) and **Starts**: open (`collapsed: false`), closed (`true`)
or closed when empty (`"empty"`), as `evlet.Section` has ([business-capabilities.md](business-capabilities.md#collapsible-sections)).
Both are optional and kept only on sections that can collapse, so layouts saved before them still load, as
sections that always show. A Starts value other than these, or Starts on a section that can't collapse, is refused.
**Fields go** sets `"flow": "columns"` (down each column) on a section; across, then down (`rows`) is the default and
is not stored. Fields and sections move by dragging their grip with a mouse or a finger, or from the keyboard: Space on a
grip lifts it, the arrow keys move it (across section edges), Space drops it and Escape puts it back, each step
announced to screen readers. The arrow buttons still work.
**Record tabs** orders the record page's tabs the same way and chooses **Open records on**. A layout that sets them is
saved as `{"sections": [...], "tabs": ["details", ...], "default_tab": "details" | null}`; without them it stays the
plain list of sections. Tab names are the record's own tabs and the model's child tables (`tabs` in each model of
`GET /api/_controls/state`). The export writes them as `Meta.tabs` and `Meta.default_tab` notes.

**List view** (kind `list`, key `Model`): `{"columns": [...], "page_size": 25 | 50 | 100, "sort": "field" | "-field", "open_in": "page" | "panel"}`,
1 to 8 columns. `/api/schema` drops the columns a role can't read and gives `page_size`, `order_by` and `open_in`.
`open_in` (Open records in) is optional: a list view saved without it keeps the model's `Meta.open_in`.
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
out, and `Recompute` neither names nor changes them; it still skips records with posted effects (unless an
admin migrates with `--recompute-posted`). A
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

## Periods

Models with `Meta.period` ([model-engine.md](model-engine.md#closed-periods)) are listed on the
Periods page with the date field that places a record in a period and, with `Meta.period_scope`,
the model whose records each have their own lock. The page shows every lock (all records, or one
scope record such as a company), who set it and when, and the last 50 changes.

Only admins set a lock, with the recent two-step code every Controls change asks. A lock saves at
once and applies to every process and script right away: it is read from `evlet_period_locks` in
the writing transaction. Each change writes an audit row in area `Periods` ("Locked up to
31-03-2026" or "Opened") with the earlier and the new date. Overrides in Controls never change
locks, and trusted code that writes a closed period with `unlocked_periods(reason)` writes an
audit row too.

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

The page has two panes. On the left is a list of the jobs: every task a schedule runs or a
recent run used, with All on top. Each shows how many of the recent runs are its own and the
state and time of its latest run. The right pane shows the selected job's schedules and its runs,
newest first; All shows every schedule and every run, with the job as a column. A run shows its
state (Queued, Running, Done or Failed), when it was due (the exact time on hover), its attempts
and, when it failed, the error. The selection is kept in the address as `?job=<name>`, so a
reload or a link opens the same job. The list is a set of tabs: the arrow keys, Home and End
move through it. On a phone it becomes a row of chips above the runs.

The server sends the schedules from the apps and the last 50 jobs across all jobs, so the
runs of one job are picked from those, and the page says so once 50 are listed. Pausing a schedule saves at once as the setting
override `schedule.<name>.active = false` and is applied with `set_schedule_active`; resuming
resets it. A pause for a schedule no app declares any more is listed under Needs attention.
Run now enqueues one job like the schedule would; a paused schedule must be resumed first.
The failed count in the menu and Needs attention covers every failed job. Retry works on failed jobs only, and the job
keeps its idempotency key.

### Unsaved changes

What people left unsaved on records ([ui.md](ui.md#unsaved-changes)): the total number of
entries and their size, the oldest entry and, per person, their count, size and latest entry.
Never what was changed. "Clear entries older than N days" deletes them now and writes an audit
row (area Records).

The limits are settings, saved through the change bar like the others, each in a safe range:

| Setting | Default | Range |
|---|---|---|
| `unsaved.server_days`: server entries are deleted after | 30 days | 1 to 90 |
| `unsaved.server_entries`: entries per person on the server, oldest dropped | 200 | 10 to 1000 |
| `unsaved.browser_days`: unsaved new records kept in the browser for | 7 days | 1 to 30 |
| `unsaved.browser_entries`: unsaved new records per person in one browser | 20 | 1 to 50 |
| `unsaved.entry_kb`: the largest entry; a larger one stays only in the open tab | 256 KB | 16 to 1024 |

Expired and over-cap entries are removed at every server start and daily by the framework
schedule `evlet_unsaved_purge` (listed with the app schedules when jobs run), 500 rows at a time.
Each person's cap also applies as soon as they leave a record. The browser removes its own
expired and extra entries when the app loads.

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
| `layout`, `list` | `apps/<app>/<package>/views/__init__.py` | `FormView(model=..., layout=Column(children=[FormLayout(...)]))`, `ListView(model=..., columns=..., page_size=..., order_by=..., open_in=...)` |
| `setting` | `instances/<name>/routes.py` or `config.py`, or the app's `hooks.py` | `ControlsAccess(allow_ips=...)` for `security.controls_ips`; `security = Security(...)` for session length, lockout and minimum password length, which config.py reads (the block keeps what config.py sets today with the overrides on top; restart, then reset the overrides); `two_step_roles = (...)` for `security.two_step_roles`, `assistants = {'mcp.rate': 30, ...}` for `mcp.*` and `mail = {'mail.personal': True}`, with their full keys. config.py does not read those three yet, as each block's comment says. A paused schedule is a comment beside the app's schedules |

Paste the code, deploy, then reset the overrides. Overrides that no longer apply are left out.

**MCP & AI** (kind `setting`) is described under [MCP and AI](#mcp-and-ai).

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
- **A saved secret stays with its server.** A field that says where secrets go is
  *identifying*: by default any field whose name contains url, host, server, port, user,
  address, account, endpoint, gateway, issuer, domain, tenant, region, bucket or security;
  set `ConnectorField(..., identifies=True)` or `False` to say so explicitly. When a test or
  a save changes an identifying setting, the saved secrets are not used: the form asks for
  them again ("Enter the password again. The server details changed..."). So a saved password
  is never sent to a different server, by a test or by a save.
- **Encryption.** Secrets are sealed with `evlet.secrets` (AES-GCM, the `cryptography`
  package), bound to their connector id (purpose `evlet-connector:<id>`), so a changed or moved
  value is detected and refused. It is the same key as two-step secrets and backup codes
  ([settings.md](settings.md#the-secret-key)): `instances/<name>/secret.key`, created with mode
  0600 the first time anything is sealed, or `EVLET_SECRET_KEY` (32 bytes, URL-safe base64) for
  containers. Once anything is sealed, a missing key file is an error and never replaced.
  An application built in code uses `Application(secret_key=path)` for both; without it,
  connector secrets use a key file next to its SQLite file and two-step sign-in stays off.
  A PostgreSQL application needs `EVLET_SECRET_KEY` or `secret_key`.
  The instance refuses to start when the key file is a link, belongs to another user, or
  can be read by anyone but its owner.
- **Rotation.** `evlet secrets rotate <instance>` writes a new key and re-encrypts every
  sealed value listed in `evlet.secrets.SEALED`: connector secrets, two-step secrets, two-step
  set-ups and sign-ins in progress, backup codes, personal mailbox secrets (passwords and
  Microsoft or Google tokens) and mailbox sign-ins in progress. API keys are stored as SHA-256 hashes, not
  sealed, so rotation leaves them alone. The old key stays in the file until all rows are done, so an interruption
  loses nothing. A row that can't be read is reported and left as it is, the others are
  re-encrypted, and the old key stays in the file until that row is entered again and rotation
  runs once more. The command refuses an instance without a database. The key file is read on
  each use, so running processes pick up the new key straight away. With
  `EVLET_SECRET_KEY`, rotate the key outside Evlet: the variable can hold several keys
  separated by commas; the first one encrypts and any of them decrypts.
- **Testing.** `POST /api/_controls/connectors/<id>/test` runs the connector's `test` on the
  saved values, or on draft values sent in the body, and answers within 10 seconds with
  `{"ok": true, "message": ...}` or `{"ok": false, "error": ...}` in plain words. A test of
  exactly the saved values sets the status Controls shows. Tests run on two threads per
  process at most; a third gets 429 until one ends, and each person can run 10 tests a minute.
  A test still running at 10 seconds is abandoned but keeps its thread until it ends, so
  abandoned tests can't pile up. A server that refuses, stays silent or answers something
  else gets the same message, "Couldn't connect to the service...", so tests can't be used to
  map ports.
- **Addresses.** Mail connections resolve the server once, refuse link-local addresses
  (including 169.254.169.254), other cloud metadata addresses, unspecified, multicast and
  reserved addresses, and then connect to the address they checked. Loopback is allowed only
  with security None, and None only reaches loopback. Mail ports are 25, 465 and 587 for SMTP
  and 143 and 993 for IMAP unless security is None (`Connector(ports=...)` sets this for app
  connectors with a `port` field). App connectors that open connections should call
  `evlet.connectors.safe_address(host, port)` before connecting.
- **Error messages.** Messages from a test are scrubbed of secrets, including quoted,
  escaped, URL-encoded and partial copies, but an app's `ConnectionFailed` messages must
  still never include a secret or a full server reply.
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

## Personal mailboxes

People can connect their own mailbox to send from records as themselves and see emails with
the people on their records. It is off until an admin turns on "Let people connect their own
mailbox" on the Email page:

| Key | File value | Rule |
|---|---|---|
| `mail.personal` | false | Off refuses new connections and pauses every personal mailbox: nothing is read and emails go from the shared account. People can still change options and disconnect. Settings, Your email says so and disables every Connect button and the app-password form. |

Images in rich text have their own limits, separate from a model's file attachments (which stay
at 1 MiB and ten files). Set them as setting overrides (`PUT` a `setting` override, audit area Files):

| Key | File value | Rule |
|---|---|---|
| `files.image_max_kb` | 2048 | Largest image after the browser shrinks it, in KiB, 64 to 8192 (8 MiB, what private storage reads back). |
| `files.images_per_record` | 50 | Images per record across its rich text fields, 1 to 200. |

A stored value that no longer validates turns it off. Changes are audited under `Email`.

**Which records mail matches.** Apps opt models in, naming the `Email` fields to match:

```python
from evlet import Email, Journal, Mail, Model, Text

class Contact(Model):
    full_name = Text()
    email = Email()

    class Meta:
        title_field = "full_name"
        journal = Journal()
        mail = Mail(match=("email",), kind="contact")
```

`kind` is `customer`, `contact`, `lead` or `other` (the default). A model without `Meta.mail` is
never matched, whatever fields it has. The fields must be stored `Email` fields; the model can't
be a table row, and it must keep a journal (a model with a State does; others add
`journal = Journal()`), since messages are Journal entries. A CRM app would declare its
organizations or customers, contacts and leads; testCRM declares Contact and Lead (its
Organization has no email field).

**Connecting.**

- *Microsoft 365 and Google* sign in with OAuth: the authorization code flow with PKCE (S256)
  and a single-use state that expires after 10 minutes and belongs to the person who started
  it. Each provider is a connector in Controls (`mail_microsoft`, `mail_google`) holding the
  client ID and secret of the workspace's own app registration. The sign-in, token, IMAP and
  SMTP addresses default to the provider's and can be replaced, which is how tests use a fake
  provider. The provider sends the person back to `/_/mail/callback`. The session cookie is
  SameSite=Strict, so it doesn't come with that cross-site redirect: the callback answers with a
  small page (no inline script; `/_/mail/continue.js` submits it) that posts the code and state
  back to `/_/mail/callback` from this origin, where the cookie is sent. The post must come from
  the instance's origin, the person must be signed in, and the state must be theirs; another
  person's attempt leaves the state in place. It then exchanges the code, reads the address from
  the ID token (which must name the client in `aud`), checks that IMAP accepts the token and
  returns to `/settings/email?mail=connected` (or `denied`, `expired`, `failed`, `mailbox`, `off`,
  `signin`). Mail uses XOAUTH2 on IMAP and SMTP. Tokens are refreshed shortly before they expire,
  one refresh per mailbox at a time.
- Disconnecting a Google mailbox also revokes its sign-in at Google (best effort). Microsoft has
  no revocation address; the tokens are deleted here and expire there.
- *Any other mailbox* uses IMAP and SMTP with a password or app password. Both servers must
  accept it before it is saved.
- Tokens and passwords are sealed with the instance secret key, bound to the person, and never
  returned. Rotation (`evlet secrets rotate`) covers them.
- The servers and token address are fixed when the person connects. A later change to the
  connector can't send someone's tokens to another server; it applies when they connect again.
- Personal servers follow the connector address rules: link-local, metadata, multicast,
  unspecified and reserved addresses are refused, loopback only with security None, and mail
  ports 143/993 and 25/465/587. Token addresses must be https, or http on this machine. Every
  connection has the 10-second time limit, and connecting and Sync now count toward the
  connection test rate.

**Options per person**, saved at once and audited:

| Option | Default | Meaning |
|---|---|---|
| `send_from` | `mine` | `mine` sends from the person's mailbox, `shared` from the default outgoing account. |
| `link_replies` | on | Copy matching emails onto records. Off: nothing is read from the mailbox; it is used only to send. |
| `only_crm` | on | "Only read emails with customers and leads". See below. |

**What is copied.** A sync job (`evlet_mail_sync`, on the jobs queue) reads the inbox read only.
A new mailbox starts with the last 30 days. Each run reads at most 50 messages per mailbox and
20 mailboxes; a mailbox with more queues its next batch at once, and every connected mailbox is
synced every five minutes. Scheduled runs pick only mailboxes that read mail and belong to
active people, the longest unattempted first, and stamp each attempt, so failing mailboxes can't
hold the others back. One mailbox's run stops after two minutes, however slowly its server
answers, and reads at most 2 MB in one piece. A per-mailbox cursor (UIDVALIDITY and the last
UID) and a key per message (its Message-ID and content) make syncing idempotent: reading the same
messages again links nothing twice, and a different message reusing a Message-ID is kept too.
Sync now queues one job per mailbox a minute. Without a jobs queue, it runs the sync in the
request.

For each message, the From, To and Cc addresses other than the person's own are matched,
without regard to case, against the opted-in `Email` fields **as the person**: with their row
scopes and only fields they can read.

- With "Only read emails with customers and leads" on, only models with `kind` `customer`,
  `contact` or `lead` are searched.
- With it off, every opted-in model is searched.

A reply (In-Reply-To or References) to a message this person linked to a record links to that
record as well, while they can still read it, even when the sender isn't on any record yet. A
forged References header only works with the earlier message's Message-ID, which the sender
must have seen.

A message that matches no record and answers none is skipped: its text is never fetched, and
nothing of it is stored, not even its headers or Message-ID. A matching message is linked to each
matching record (10 at most) with its subject, plain text (10,000 characters at most, from its
first megabyte), the matching address and the direction. Changing an option applies to messages
read after the change. A message that can't be parsed (deep nesting, broken headers) is skipped
and the cursor moves past it.

A sign-in the provider no longer accepts (`invalid_grant`) or a password the server rejects is
deleted and syncing stops; the page asks the person to connect again. Every other failure,
including a refused client ID or secret, is shown on the page and retried. Logs name only the
mailbox id and the type of a failure, never addresses, subjects or text. Disconnecting deletes
the saved sign-in and any pending sign-in, and stops syncing at once. Messages already linked to
records stay there.

**Privacy.** No Controls route reads a personal mailbox, its messages or its credentials. A
linked message is a Journal `message` entry on the record ([journal.md](journal.md)), with the
mailbox owner as the actor and `{"text", "subject", "party", "direction", "source": "mailbox"}`.
It shows in the record's Activity tab and through `GET /api/{model}/{id}/journal` (add
`?kind=message` for messages only), only to people who can read that record; API keys and AI
assistants only within their scope. The entry also keeps the `Email` field that matched; the
Journal read path leaves the address out (`party` is `null`, the entry `partial`) for readers who
can't read that field, and never returns the field's name. `evlet_mail_links(model, record_id,
message_key, entry_id)` is only an index: it keeps a message once per record (a different message
reusing a Message-ID is kept too) and finds the records a reply belongs to, among the entries the
same person added. The older `GET /api/{model}/{id}/messages` route is removed.

**Sending.** Trusted code sends as a person with
`business.mailboxes.send_mail(user, to, subject, text, html=None, record=None)`: from their own
mailbox when it is connected, the workspace allows it and they chose `mine`, otherwise from the
default outgoing account. With `record=(Model, id)` the person must be able to update the
record, and the email is linked to it, so replies to it are linked there too.

The personal page is `/settings/email` in the Settings shell (the `MyEmailPage` component,
`frontend/src/evlet/settings/email.ts`). Its routes are under `/api/_settings/me/mailbox`, so they
don't clash with `/api/_settings/me/email`, which changes the person's sign-in address.

## API keys

Keys let scripts call the API as a user: `Authorization: Bearer evk_...`. A key is shown
once when it is created; only its prefix (`evk_` and 8 characters) and the SHA-256 of the
whole key are stored in `evlet_api_keys`.

- **Never more than the user.** A key acts as its user with that user's current role, row
  scopes and field rules, narrowed by its scope:
  `{"access": "read" | "write", "models": null | ["Customer", ...]}`. A read key refuses
  every request that isn't GET, including reports and draft evaluation, which are POST. Through
  the Tool Engine it may still call read tools, `run_report` included ([tools.md](tools.md)). A key
  with models sees only those models and their child rows. A key with `models: null` covers
  whatever its user's role can read today, so it grows and shrinks with the role. When the
  user loses access or is deactivated, so does the key. Creating a key for a model the user
  can't read, or a write key for a user who can only read, is refused.
- **Jobs.** A job queued by a key request runs with the key's scope. If the key is revoked
  before the job runs, the job fails with "The API key that queued this job was revoked."
- **Where keys work.** Every `/api` route that a session can use. Only a well-formed key
  (`evk_` + 8 characters + `_` + 43 characters) skips the request protection header; other
  Authorization values don't. `GET /api/auth/session` answers with the key's user. Keys never
  open Controls (404) and can't use the other `/api/auth/*` routes or `/api/_settings/*` (403).
- **Desk only.** Keys belong to the desk realm. A portal or public host refuses every key
  (403 "API keys work on desk hosts only."), a key can't be created for a user whose role is a
  portal role (422), a key stops working (401) when its user moves to a portal role, and
  `/api/_settings/me/keys` is 404 for portal sessions. Portal users have no API keys; portal
  API access goes through the portal's own sessions and API scopes.
- **Controls and custom roles.** A key is checked against its user's role through the same
  policy as a session, so Controls permission and field overrides and custom roles (with their
  base) apply to it at once, without changing the key. A key scope only narrows: creating a key
  for models the role no longer reads is refused.
- **Limits.** 300 requests a minute per key, per process; then 429. Invalid keys are counted
  per network and prefix (20 a minute) and per network (100 a minute) after the key is
  looked up, so a valid key is never slowed by someone else's failures. The network is the
  one the trusted proxy settings report, when they are configured. At most 20 active keys
  per user. `last_used` is updated at most once a minute.
- **No expiry.** Keys don't expire. Revoke a key that is no longer used; Controls marks keys
  unused for 30 days.
- **Controls** lists every key and creates keys that act as any active user; only admins
  create or revoke keys that act as admins. **Personal keys** use
  `GET/POST /api/_settings/me/keys` and `DELETE /api/_settings/me/keys/<id>`, for the person
  signed in. Revoking is immediate: the key gets 401 from then on. Creating and revoking
  write audit rows, without the key.

The personal page is `/settings/keys` in the Settings shell (the `MyKeysPage` component,
`frontend/src/evlet/settings/keys.ts`), for desk users.

## MCP and AI

The AI group has four pages: **Eva**, **MCP**, **Usage** and **Tool log**. Each person needs
**Allow Eva** to use Eva and **Allow MCP**, set in Users, to connect an assistant; both are off
for new people and grant no record access. **Usage** shows tokens, requests, estimated cost in
AED, cost by day and by person, and the largest conversations by owner and number only. On
upgrade, people who had a connected assistant keep it. Turning it off signs out the person's
assistants at once. Changes are audited under Users. **Tool log** lists every tool call from
MCP clients and the API, with redacted inputs, for 90 days ([tools.md](tools.md)).

### Eva

Eva is off unless config.py turns it on ([eva.md](eva.md)); these settings then apply. Each
person also needs **Allow Eva** (`PATCH /api/_controls/users/<id>` with `{"allow_eva"}`).

| Key | File value | Rule |
|---|---|---|
| `eva.enabled` | false | Only when config.py turns AI on. Off hides Eva from everyone and stops scheduled tasks. |
| `eva.model`, `eva.choose_model` | first model, false | A model named in config.py; whether people may pick another. |
| `eva.confirm` | create and update never, confirm and action ask | `ask` or `never` per kind of change. Cancelling, reopening and deleting always ask. |
| `eva.instance_limit`, `eva.user_limit`, `eva.person_limits` | no limit | `{amount, unit: aed, tokens or requests, period: day, week or month}`. |
| `eva.task_reserve` | 10 | Percent of the instance allowance kept for scheduled tasks, 0 to 50. |
| `eva.max_steps`, `eva.max_tools`, `eva.max_seconds` | 12, 30, 120 | Per answer. |
| `eva.rates` | none | AED per million tokens per model: input, cached and output. |
| `eva.disabled_tools` | `[]` | Tools Eva may not call. |

A stored Eva value that no longer validates fails closed: Eva turns off, every change asks,
allowances become AED 0, limits drop to their minimum and an unreadable tool list disables
every tool. `GET /api/_controls/eva` reports whether config.py turned AI on (never the key)
and `GET /api/_controls/eva/usage?period=month|last_month|7d|YYYY-MM` the usage by day, person
and conversation (owner and number only, never titles).

### MCP

People connect AI assistants (MCP clients such as Claude) to the instance. Remote clients
use `https://<primary host>/_/mcp` and sign in with OAuth; local ones run `evlet mcp`
([connector.md](connector.md)). Both act as the person who signed in and go through the same
Tool Engine, API routes and permission checks as the browser.

| Key | File value | Rule |
|---|---|---|
| `mcp.enabled` | true | Off stops every client at once: `/_/mcp` answers 403 and sign-in pages are gone. |
| `mcp.allow_writes` | false | The page asks before turning it on. Write tools need it on, and each write still needs the user's permission. |
| `mcp.rate` | 60 | Tool calls a minute per client, 1 to 60. The framework cap stays at 60. |
| `mcp.disabled_tools` | `[]` | Tool names. A disabled tool isn't listed and is refused. |
| `mcp.hidden_models` | `[]` | Model names. Hidden models (and their child rows) never reach an AI client or Eva, even an admin's. |

A stored value that no longer validates fails closed: the switches turn off, the rate becomes
1, and a list that isn't a list disables every tool or hides every model. Changes are audited
under `MCP`.

The page also lists connected clients (who, last call, number of calls) and totals for the
last 24 hours: reads, writes and calls denied by permissions or by these settings. Admins can sign a client out at
once; only admins sign out an admin's client. Per-request detail is only on each person's own
AI page. Clients also count in the Users page sessions, and "Sign out everywhere", a password
or role change, a two-step reset, deactivation and the break-glass grant sign them out too
([Ending access](#ending-access)).

**Desk only.** `/_/mcp`, the OAuth endpoints and the consent page answer only on the primary
host, so portal and other sub-hosts get 404. Consent needs a desk session, and a token or
refresh token stops working (401, `invalid_grant`) once its person's role is a portal role. The
client address used for the per-network limits is read through the trusted proxies, like
sign-in. An assistant acts as the person's role through the same policy as the browser, so
Controls permission and field overrides and custom roles apply to it; the `mcp.*` settings and
the person's own app choice only narrow it further.

**Journal.** A record changed through an assistant is journaled with the person as the actor,
like any of their writes, and the entry names the client: `"via": "Claude"` on the entry
(`"Local MCP connector"` for `evlet mcp`). The Activity tab shows "Sara changed it via Claude".
The client name is the one the client registered, so it is a label, not proof of identity.

The personal page is `/settings/ai` in the Settings shell (the `MyAIPage` component,
`frontend/src/evlet/settings/ai.ts`). It shows the connect steps, the person's assistants with "Can make changes"
and Disconnect, the apps assistants can use (only narrower than the person's own access; once
chosen, apps installed later stay off), their last 50 requests from the past 30 days and the
local `evlet mcp` steps. "Can make changes" has effect only while
`mcp.allow_writes` is on.

## Audit log

`evlet_audit` is append-only: triggers refuse update, delete and replace. Users, roles,
overrides, connectors, email accounts, personal mailboxes (connect, disconnect, options), API
keys, AI assistants (consent, sign-out, "Can make changes", MCP settings), migrations and jobs all
write to it. Records (area `Records`) lists each unpost and repost of a posted record (who, the
record and the reason) and each migration run that recalculated posted records (actor `cli`). Periods lists
each lock change and each `unlocked_periods` block (actor `python`). The Audit log page searches who, what and the detail, filters by area and pages
newest first, 50 rows at a time.

## API

| Route | Purpose |
|---|---|
| `GET /api/_controls/state` | Instance, apps, models, roles, routes, settings, attention list, recent audit rows |
| `GET/POST /api/_controls/overrides` | File value, override, who and when; save changes |
| `GET/POST /api/_controls/users`, `PATCH /api/_controls/users/<id>` | Wraps `LocalAuth`: role, active, password, `{"sessions": "end", "revoke_keys": false}`. Each user row counts active `keys` |
| `/api/auth/users` | The older account routes, kept. They use the same access check, admin rules, fresh two-step code and audit rows; refused access is a 403 there |
| `GET/POST /api/_controls/roles`, `PATCH/DELETE /api/_controls/roles/<id>` | Roles, "Opens Controls" and descriptions |
| `POST /api/_controls/fields/<Model>`, `DELETE /api/_controls/fields/<Model>/<x_name>` | Add or remove an instance field: `{name, type, label, help_text, tooltip, required, default, options, target}` |
| `GET/POST /api/_controls/naming/<Model>/counters` | Counters; set one with `{prefix, value, note}` |
| `POST /api/_controls/naming/<Model>/try` | `{values, series?}`: the matching series and name, and a check of each draft series. No counter changes |
| `GET /api/_controls/overrides/export?kind=&key=` | Export as code: `[{file, code}]` |
| `GET /api/_controls/routing`, `POST /api/_controls/routing/check` | Route table, hosts and proxy config; DNS and certificate check |
| `GET /api/_controls/jobs` | Schedules and recent jobs |
| `GET /api/_controls/unsaved`, `POST /api/_controls/unsaved/clear` | Unsaved changes: `{entries, bytes, oldest, limits, people}` (counts and sizes only); clear `{"days"}` older than that, audited |
| `POST /api/_controls/jobs/schedules/<name>` | `{active}`: pause or resume |
| `POST /api/_controls/jobs/schedules/<name>/run`, `POST /api/_controls/jobs/<id>/retry` | Run now; retry a failed job |
| `GET /api/_controls/audit?area=&q=&before=` | `{items, next, areas}`, newest first |
| `GET /api/_controls/periods` | `{models, scopes, locks, history}`: models with `Meta.period`, the records locks can be set for, every lock and the last 50 changes |
| `POST /api/_controls/periods` | `{"scope": null or {"model", "id"}, "date": "YYYY-MM-DD" or null}`: set or clear one lock. Admin only |
| `GET /api/_controls/connectors` | Declared connectors with their fields, and every saved connector and email account |
| `GET/PUT/DELETE /api/_controls/connectors/<id>` | One connector (`whatsapp`) or account (`smtp:accounts`): read, save `{"settings", "secrets"}`, disconnect |
| `POST /api/_controls/connectors/<id>/test`, `POST .../default` | Test within 10 seconds; make an outgoing account the default |
| `GET /api/_controls/mcp`, `DELETE /api/_controls/mcp/<id>` | MCP address, connected clients, 24-hour totals; sign a client out |
| `GET /api/_controls/tools?origin=&outcome=&tool=&q=&before=` | The Tool log, newest first, 50 rows a page |
| `PATCH /api/_controls/users/<id>` with `{"allow_eva"}` or `{"allow_mcp"}` | Allow Eva or Allow MCP, on its own, saved at once |
| `GET/POST /api/_controls/keys`, `DELETE /api/_controls/keys/<id>` | API keys: list, create `{"name", "user", "scope"}` (the key is in this reply only), revoke |
| `GET/POST/PATCH/DELETE /api/_settings/me/mailbox` | The person's own mailbox: read, connect with a password, change options, disconnect. Sessions only |
| `POST /api/_settings/me/mailbox/oauth`, `POST .../sync` | Start a Microsoft 365 or Google sign-in (`{"provider"}`, answers `{"url"}`); sync now |
| `GET/POST /_/mail/callback`, `GET /_/mail/continue.js` | Where the provider sends the person back; the page posts the code from this origin |
