# Controls

`/controls` is the locked admin page of an instance. It is the framework frontend
`evlet.controls`, not a generated model. This release has Overview, Users, Roles,
Permissions, Security, Connectors, Email, MCP & AI, API keys, Apps, Models, Naming, Periods, Print designs, Currencies, Routing, Jobs, Overrides and the Audit log.

The Overview lists what needs attention: an app waiting to be migrated or a server that still
runs the old version, overrides that no longer apply (and what fails closed meanwhile), skipped
naming series, connectors and email accounts whose last test failed, screen extensions that no longer fit a form layout, failed jobs, and roles that
must use two-step sign-in but can't because the `cryptography` package is
missing or the secret key file is refused. An application without a secret key (not an instance)
has no two-step sign-in, and nothing is listed for it.

## Access

- Served on the primary host only. Sub-hosts get 404 for the page, every
  `/api/_controls/*` route and `/api/auth/users`, from the route table. Without a primary
  host (development, or a tenant whose factory keeps the default route table) every host
  the instance accepts counts as primary, except sub-hosts. So each tenant of a
  `TenantApplication` manages its own users on its own host, in its own database.
- Only desk roles open Controls. Portal roles never do, whatever their settings.
- Every instance has the built-in **System Administrator** (username `admin`, role
  admin), like the Administrator in ERPNext. See [The Administrator](#the-administrator).
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
  sign-in; others get a message that sends them to Settings and back through sign-in. Roles that
  must use two-step sign-in (see [Security](#security-rules)) need such a session too, when the instance can keep secrets: they
  can't use anything until two-step sign-in is set up (see [settings.md](settings.md#when-the-code-is-asked)).
- **Changes ask for a fresh code.** For a person with two-step sign-in on, every request that
  changes something in Controls (any `POST`, `PUT`, `PATCH` or `DELETE` under `/api/_controls/`,
  except trying a naming series, and under the older account routes `/api/auth/users`) needs a
  code from the last 10 minutes on this session. Signing
  in with a code counts. Otherwise the answer is 403 `VERIFY_TWO_STEP: ...`; the page shows a code
  dialog, sends the code to `POST /api/_settings/me/two-step/verify` and sends the change again.
  Reading Controls never asks. An admin setting someone else's password is a Controls change.
- **Passwords set by a role that is not admin.** A role that opens Controls can set the password
  of someone who is not an admin, which lets it sign in as them. Admin accounts are refused (403).
  The audit row says it plainly: `Password set by <username> (role <role>), who is not an admin`,
  with the target and the time. An admin, or someone setting their own, gets `Password set`.
- Admins can **Reset two-step** for a user (`PATCH /api/_controls/users/<id>`
  `{"two_step": "reset"}`), and `evlet users reset-two-step <instance> <username>` does the same
  from the server shell. Both are audited and revoke all the user's access, API keys included
  (see [Ending access](#ending-access)); see
  [settings.md](settings.md#set-up-at-sign-in-trust-on-first-use) for why it exists.
- Break glass from the server shell:

  ```
  evlet controls grant <instance> <username>
  ```

  It makes the user an active admin, revokes all their access including API keys and writes an
  audit row by `cli`. It keeps the allowed networks set in Controls (`security.controls_ips`).
  When there are some, it says so. On an SSH session it also checks the address you connected
  from: if that address is not in them, you are still locked out, and it tells you to run it again
  with `--clear-allowlist`. Your browser may reach the server from another address than your SSH
  session, so check the list it prints either way.

  ```
  evlet controls grant <instance> <username> --clear-allowlist
  ```

  `--clear-allowlist` also clears the allowed networks set in Controls, with a second audit row.
  Running the command again revokes the user's sessions again. It can't change `routes.py`: when
  `ControlsAccess` there limits the networks, the command says so and you edit `routes.py`.
- Saving allowed networks that leave out your own address asks first.

### The Administrator

Every instance has one account it creates itself: username `admin`, full name
"System Administrator", role admin, marked `builtin` in `evlet_users`. New databases get it at
startup. Older ones get it from the framework step `0008_administrator`, which `evlet migrate`
runs; if another account already uses the username `admin`, the step stops with a
message and changes nothing, so rename that account first.

- It starts with **no password**, so nobody can sign in as it until one is set: through the
  development sign-in link and the "Set your password" screen ([cli.md](cli.md#development-sign-in-links)),
  with `evlet users set-password <instance> admin`, by an admin in Controls, or on first-run
  setup in the browser by choosing `admin` as the username.
- Its username, full name and role never change, and it can't be deactivated or deleted. Every
  route refuses with 409 "The Administrator account is built in...": Controls Users, the older
  `/api/auth/users` routes and the profile (`/api/_settings/me/profile` and the photo answer 409
  "The Administrator profile is fixed..."). Connectors and MCP clients use the same routes.
  A change made in the database directly is put back at the next start.
- It may add **one email address** for notifications, on any instance, `evlet serve` included:
  Settings, Profile asks for its current password (and a code when two-step sign-in is on),
  checks the address and saves it at once, with an audit row. Mentions and other notifications
  use it. Other people keep the usual rule (a confirmation link, development only). From the
  server shell, `evlet users set-email <instance> <username> <email>` sets anyone's address,
  audited as `cli`. Its password, two-step sign-in,
  sessions and personal preferences work as for anyone. Sign-in lockout applies too;
  `evlet users set-password` and `evlet users reset-two-step` recover it.
- **Unlimited control.** It passes every role check: every model, row and field whatever the
  admin rules in the app files say, every action, move, report, print design and slip, and every
  Controls page. It does not skip data rules: validation, read-only fields, state rules, period
  locks, foreign keys and transactions apply, its requests go through the normal authenticated
  routes and the audit log names it. API keys it creates still narrow it, and the Controls
  allowlist still applies.
- It belongs to its instance's database. Each tenant of a `TenantApplication` has its own,
  which can't sign in to another tenant, like any other account.
- With two or more admins, the "only active admin" rule counts admins who can sign in: the
  Administrator counts once it has a password.

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
They can only make a rule stricter than the app files. Instance fields are an exception, since
Controls owns them: see [Permissions](#overrides) below. The password minimum and two-step sign-in
for people other than admins are another: they go either way ([Security](#security-rules)). Saving sends every change at once
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

<a id="security-rules"></a>**Security** (kind `setting`). The sign-in rules start from the instance's `config.py`, beside
`instance = Instance(...)`, or from `LocalAuth(security=...)` in an application built by hand:

```python
from evlet import Instance, Security

instance = Instance(name="acme", domain="erp.acme.com")
security = Security(password_min=8, admin_two_step=True, two_step=False, admin_sign_in=False,
                    session_hours=8, lockout=4)
```

| Option | Default | What it does |
|---|---|---|
| `password_min` | 15 | 1 to 64 characters. 1 means no rule: "1" is a valid password. |
| `admin_two_step` | `True` | Admin accounts, the Administrator too, must use two-step sign-in. Only config.py decides this. |
| `two_step` | `False` | Everyone else must use two-step sign-in. Controls can turn it on or off. |
| `admin_sign_in` | `False` | The sign-in page opens with the username `admin` filled in, so you type only the password (and a code when needed). |
| `session_hours` | 12 | 1 to 168. |
| `lockout` | 5 | 3 to 50. |

**Admins follow config.py only.** An account with the admin role always uses config.py's
`password_min` and `admin_two_step`; Controls can't change them. Change config.py and restart.
**Everyone else** starts from the same values, and Controls can change their password minimum and
two-step sign-in in either direction. Session length and lockout can only be made stricter.
Controls > Security shows the admin rules read-only above the settings it can change.

| Key | File value | Rule |
|---|---|---|
| `security.password_min` | 15, or `Security(password_min=...)` | 1 to 64, either direction, for everyone but admins. Applies to new passwords. |
| `security.two_step` | `False`, or `Security(two_step=...)` | On or off, for everyone but admins. |
| `security.session_hours` | 12, or `Security(session_hours=...)` | Down only, 1 at least. Sets the session and cookie length in every realm. Lowering it ends sessions already older than the new length, desk and portal alike. |
| `security.lockout` | 5, or `Security(lockout=...)` | Down only, 3 at least. Failed sign-ins per network and account, and wrong current passwords per account in Settings. |
| `security.two_step_roles` | `[]` | Desk roles other than admin that must use two-step sign-in even with `security.two_step` off. A list saved before 0.4.2 that names admin keeps working; admin is dropped from it, since config.py decides for admin. These roles must use two-step sign-in ([settings.md](settings.md#two-step-sign-in)) when the instance can keep secrets: people who have not set it up do so at their next sign-in (trust on first use), and Controls needs a verified session. |
| `security.controls_two_step` | `True` | Every desk role other than admin that opens Controls must use two-step sign-in, like the roles in `security.two_step_roles`. On by default. Only an admin can switch it off (others get 403). Turning "Opens Controls" on for a role then requires it for that role at once. |
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
role in the API. Added options and defaults apply on every write path. `/api/schema`
shows the merged values (`choices` lists the shown options and `retired_choices` the hidden ones);
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
Record pages have no tabs (0.4): a layout is saved as the plain list of sections, or as `{"sections": [...], "panel":
{...}}`. A layout saved before 0.4 with a record tab order (`tabs`, `default_tab`) keeps its sections and panel; the tab
order is dropped, and sending one now is refused. For a model whose `Meta.layout` declares `Main` or blocks
([record layouts](ui.md#record-layouts)), Controls moves fields and sections; the layout's blocks stay as the app
declares them, a section keeps its place by title, and a section Controls adds goes last. Each model in
`GET /api/_controls/state` lists its record parts in `tabs` (Activity, Details, Revisions, its child tables, Related,
Postings), the names the side panel can hide.
**Side panel**: what a record shows when a list opens it in a side panel. Each section, field and record part (Details aside) has
a panel button, pressed while it shows there; the Side panel card has the summary strip, Files and Record info (the record footer). A layout
that sets this is saved as `{"sections": [...], "panel": {"sections": [...], "fields": [...], "tabs": [...], "blocks": [...]}}`,
each list naming what the panel hides (`blocks` from `summary`, `key_fields`, `files` and `info`). `panel` replaces the
app's `panel=False` flags and `Meta.panel` for this instance, keeping the order `Meta.panel` gives; `{}` shows
everything; a layout without `panel` leaves it to the app, whose choice the toggles start from. Unknown names or a name
given twice are refused. It is stored with the layout and needs no migration. Renaming a section keeps its choice;
removing an instance field drops it. `/api/schema` names only fields the role can read. Hiding is display only: the
same field, row and action checks apply, and the full page shows everything.

**List view** (kind `list`, key `Model`): `{"columns": [...], "page_size": 25 | 50 | 100, "sort": "field" | "-field", "open_in": "page" | "panel"}`,
1 to 8 columns. `/api/schema` drops the columns a role can't read and gives `page_size`, `order_by` and `open_in`.
`open_in` (Open records in) is optional: a list view saved without it keeps the model's `Meta.open_in`.
`list_tabs` (Tabs by) and `list_tab` (Opens on) are optional too and follow the rules of `Meta.list_tabs` and
`Meta.list_tab` ([ui.md](ui.md#list-tabs)): a State, Select or Link field of the model, or `null` for no tabs, and
`"all"`, one of its values or a linked record's title. `list_tab` needs `list_tabs`. A list view saved without them
keeps the model's tabs, and a saved field that is later removed falls back to them. The export writes them as
`Meta.list_tabs` and `Meta.list_tab` notes.
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
an older SQLite the page says so and only adding works. Each tenant has its own instance fields.

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

## Currencies

How each currency code is shown on this site. A `Currency` field's code says which currency an amount
is in ([model-engine.md](model-engine.md#where-a-currency-fields-code-comes-from)); this page says how
that code looks beside the amount:

- **Symbol**: text of 1 to 8 characters, such as `$`, `€`, `د.إ` or `AED`, or a small image.
- **Position**: before or after the amount.
- **Spacing**: a space between the symbol and the amount, or none.
- **Match the text colour** (images only): the image's shape is painted in the text colour, so a
  one-colour symbol reads in light and dark themes. Turn it off for a symbol with its own colours.
- **Decimals**: 0 to 6 places, ISO 4217 by default (AED 2, KWD 3, JPY 0). **Add currency** adds a
  code that is not listed, such as a client's own unit with 5 places.

A code that is not listed shows as it always has: the code, a space, then the amount
(`AED 1,234.50`), with its ISO places. Separators follow each person's region, and amounts are
never converted.

Raising a code's decimals takes effect at once. Lowering them is refused with 409 while a stored
amount in that code has more places, naming how many and the first one; nothing is saved. To lower
them anyway, round those amounts first with the explicit, audited step
`round_stored_money(app, "AED", 2, actor="finance-admin")`, which rounds them half up and then saves
the new decimals ([model-engine.md](model-engine.md#decimal-places-per-currency)).

It lives in Controls, not Settings: Settings is each person's own account, while this changes what
everyone on the site sees, like the other site-wide settings here. It is the setting
`display.currencies` (`{"AED": {"symbol", "image", "position", "space", "tint", "decimals"}}`, only the changed
codes, at most 30), saved through the change bar with the others. Every save writes an audit row
(area `Display`, "Changed AED") whose before and after name each image by type, size and a short
hash, never its bytes. **Reset to default** removes one code; **Reset all to default** removes the
setting. Only admins change it: other roles that open Controls see the page read only, and the
server answers their save with 403. The preview uses the unsaved draft.

**Images** are PNG or WebP, at most 32 KB and 128 x 128 pixels. The type is read from the bytes,
never from the file name or the declared type: a file that only starts like a PNG is refused (every
chunk and checksum is checked), and SVG is never accepted because it can carry script. The image is
kept in the setting as a `data:` URI, like the images of a stored print design, so it needs no file
storage and reaches every process with the setting.

Where the display applies:

| Place | What shows |
|---|---|
| Forms | The symbol at rest, and in the input's side box on its side (the box moves to the end for a suffix) |
| Lists, boards, record summary and child table totals, measures, reports | The symbol before or after the amount |
| Print designs and PDFs | `money()` and every Currency value; an image is embedded in the PDF |
| Filter chips, history, titles and other text-only places | A text symbol, or the ISO code for an image |
| Report CSV | The plain number, never a symbol or an image; a currency column names its code in the header, `Total (AED)` |

On screen an image sits at text height on the number's baseline, with the ISO code as its `alt` text
for screen readers and copying. A right-to-left symbol such as `د.إ` is isolated so it stays on its
side of the digits. `/api/schema` sends the changed codes as `currency_display`, and the places of
every code that does not have 2 as `currency_decimals` (`{"JPY": 0, "KWD": 3, ...}`), so the browser
formats without another request; people see a change on their next page load.

Trusted code can set it the same way, checked and audited:

```python
from evlet import set_currency_display

set_currency_display(app, {"AED": {"image": Path("dirham.png"), "position": "prefix", "tint": True}}, actor="setup")
```

An image may be a `data:` URI, bytes or a `Path`. `None` resets every code. The Playground uses it
to show AED with a placeholder coin image. Evlet does not ship the UAE dirham symbol: download the
official file from the Central Bank of the UAE and upload it here.

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
A run that reports progress shows it under its status: done of total with a bar, and the job's
short message. While a run is running the page reads the list again every three seconds.

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
| `field`, `custom`, `naming`, `layout`, `list` | The file that declares the model, such as `apps/notes/models/item.py` | The field lines as models.py writes them, with the changes merged in, and the `Meta` options: `naming = Naming(...)`, `layout = (Section(...), ...)` with `tabs` and `default_tab`, and `list_fields`, `open_in`, `list_tabs` and `list_tab`. Pasted into the model, they reproduce the layout and list overrides. A model with a [record layout](ui.md#record-layouts) gets its whole `layout = (Tab(...), ...)`, with the Controls sections in place. Field widths (set by the field type) and a list's page size and sort have no `Meta` form, so they are comments. Instance fields keep their `x_` name; rename them while moving them, since apps can't declare `x_` names. Hidden options are a comment. Arguments set in code, such as a callable default or validator, are written as `...` with a comment to copy them from models.py, so the code always compiles. |
| `setting` | `instances/<name>/routes.py` or `config.py`, or the app's `hooks.py` | `ControlsAccess(allow_ips=...)` for `security.controls_ips`; `security = Security(...)` for session length, lockout, minimum password length and `two_step`, which config.py reads (in config.py `password_min` applies to admins too, as the block's comment says) (the block keeps what config.py sets today with the overrides on top; restart, then reset the overrides); `two_step_roles = (...)` for `security.two_step_roles`, `controls_two_step = False` for `security.controls_two_step`, `assistants = {'mcp.rate': 30, ...}` for `mcp.*` and `mail = {'mail.personal': True}`, with their full keys. config.py does not read those four yet, as each block's comment says. A paused schedule is a comment beside the app's schedules |

Paste the code, deploy, then reset the overrides. Overrides that no longer apply are left out.

**MCP & AI** (kind `setting`) is described under [MCP and AI](#mcp-and-ai).

## Workspaces

Site overrides of the app's workspaces ([ui.md](ui.md#workspaces)). They are stored in
`evlet_overrides` with kind `workspace` and the workspace key, never change app files, raise
`evlet_config.version` like every override, and write audit rows (area `Workspaces`: Changed, Added,
Reset to the declared workspace, Removed, with the value before and after). They have their own
routes and are not part of `POST /api/_controls/overrides`, the Overrides page or Export as code.

**Who.** Admins, and desk roles with **Customize workspaces** on (Roles page, `PATCH
/api/_controls/roles/<id>` `{"customize_workspaces": true}`). Only admins turn it on or off; admin
always has it and portal roles never do. It works like "Opens Controls" but is separate from it: a
role with only Customize workspaces reaches the workspace routes and nothing else in Controls, and
"Opens Controls" alone does not give it. The same hosts, allowed networks and two-step rules as
Controls apply, and a change asks for a fresh two-step code. Like the rest of Controls, the routes
stay hidden from everyone else: they answer 404. The one exception is a person who already opens
Controls but whose role lacks Customize workspaces. Controls is not hidden from them, so they get
403 with the reason. The browser learns whether to offer this from `customize_workspaces` in
`GET /api/_settings/me` ([settings.md](settings.md#pages)), which uses the same check as the
routes.

| Route | Does |
|---|---|
| `GET /api/_controls/workspaces` | `{items, problems}`: each workspace with `key`, `label`, `source`, `app`, its saved `override`, `changed_by`, `changed_at`, and how it looks to this person without (`declared`) and with (`effective`) the override |
| `PUT /api/_controls/workspaces/<key>` | Save the override (it replaces the saved one). Answers the listing; 422 names each problem by path, such as `groups.0.items.2.shortcut` |
| `DELETE /api/_controls/workspaces/<key>` | Reset to the declared workspace, or remove a custom one |

The override of a declared or automatic workspace:

```json
{"label": "Sales desk", "icon": "handshake",
 "groups": [
   {"key": "deals", "label": "Pipeline", "items": [
     {"key": "model:deal:large_deals", "label": "Big deals"},
     {"key": "model:client"},
     {"key": "slip:quick_deal", "hidden": true},
     {"shortcut": {"model": "deal", "filters": {"stage": "won"}, "label": "Won deals"}}]},
   {"key": "reports", "hidden": true},
   {"key": "later", "label": "Later", "items": [{"shortcut": {"url": "/docs", "label": "Docs"}}]}]}
```

- `label` and `icon` rename the workspace and change its icon.
- `groups` come first, in this order; declared groups the override doesn't list follow in their
  declared order. A group key the workspace doesn't have adds a group, which needs a label.
- In a group, the listed items come first, then its other declared items. An item listed in another
  group moves there. `hidden` hides a group or an item, `label` renames it.
- `{"key": ...}` names a declared item by its key; for an automatic workspace, any `model:<name>` or
  `report:<name>` of its app. `{"shortcut": {...}}` adds one with the arguments of `Shortcut`
  (`model`, `view`, `filters`, `label`, `report`, `slip`, `url`), checked exactly like a declared
  one. Its key must not be one the workspace already has.
- A new workspace is `{"custom": true, "label": ..., "icon": ..., "groups": [...]}` under a new key
  (lowercase letters, digits and underscores). It holds shortcuts only and lists after the declared
  workspaces, by key.
- Sending `{}` for a declared workspace resets it.

Permission filtering runs after the override, so an override never widens access: a shortcut to a
model, report or slip a role can't use, or with filters on a field it can't read, stays hidden for
that role, and groups and workspaces left empty are hidden. A saved override that no longer
validates after an app update is left out (the workspace shows as declared) and listed in `problems`
and under Needs attention until it is fixed or reset.

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
  connectors with a `port` field). Admin connectors may use private ranges, so an office mail
  server on 10.x or 192.168.x works when an admin sets it up. App connectors that open
  connections should call `evlet.connectors.safe_address(host, port)` before connecting, and pass
  `public=True` when the address was typed in by someone who is not an admin.
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
- A mailbox connected with a password reaches only public mail servers. Private ranges
  (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7), loopback, link-local, metadata, multicast,
  unspecified and reserved addresses are refused, security must be SSL/TLS or STARTTLS, and the
  ports must be 143/993 and 25/465/587. The check runs again on every sync and send, so a saved
  mailbox on a private server stops with an error. For a mail server on a private network, an
  admin sets it up as an Email sending or Email receiving connector instead. Microsoft 365 and
  Google mailboxes use the servers the admin set on their connector, with the connector address
  rules. Token addresses must be https, or http on this machine. Every
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
It shows in the record's Activity and through `GET /api/{model}/{id}/journal` (add
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
Tool Engine, API routes and permission checks as the browser. The reverse proxy must pass
`/_/mcp`, `/_/oauth/*` and `/.well-known/oauth-*` to Evlet; the files `evlet proxy` and
`evlet setup --host` write do ([routing.md](routing.md#evlet-proxy)).

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

**Reachable from the internet.** Admins see a **Check** button next to the address
(`POST /api/_controls/mcp/reach`, no two-step code needed). The server makes two requests
to its own public address, the way an AI client starts: `GET
https://<domain>/.well-known/oauth-protected-resource/_/mcp`, which must describe this
instance, then an unsigned `POST https://<domain>/_/mcp`, which must answer 401 with
`WWW-Authenticate: Bearer resource_metadata=...`. The answer is `{checked, reachable, reason}`,
shown as Yes or No with the reason, such as "Blocked by the proxy" when `/_/mcp` gets 404.
It runs only on the button, never when the page opens, and the answer is kept for 30 seconds.
On loopback or a development server it does not call out and says "Only on a site with a
domain."; with MCP off it asks you to turn it on first. Other roles get 403.

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
(`"Local MCP connector"` for `evlet mcp`). The record's Activity shows "Sara changed it via Claude".
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
| `GET/POST /api/_controls/overrides` | File value, override, who and when; save changes. `display.currencies` needs an admin |
| `GET/POST /api/_controls/users`, `PATCH /api/_controls/users/<id>` | Wraps `LocalAuth`: role, active, password, `{"sessions": "end", "revoke_keys": false}`. Each user row counts active `keys` |
| `/api/auth/users` | The older account routes, kept. They use the same access check, admin rules, fresh two-step code and audit rows; refused access is a 403 there |
| `GET/POST /api/_controls/roles`, `PATCH/DELETE /api/_controls/roles/<id>` | Roles, "Opens Controls", "Customize workspaces" and descriptions |
| `GET /api/_controls/workspaces`, `PUT/DELETE /api/_controls/workspaces/<key>` | Workspace overrides: list, save, reset ([Workspaces](#workspaces)). Admins and roles with "Customize workspaces" |
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
