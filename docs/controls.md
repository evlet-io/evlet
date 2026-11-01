# Controls

`/controls` is the locked admin page of an instance. It is the framework frontend
`evlet.controls`, not a generated model. This release has Overview, Users, Roles,
Models, Naming, Permissions and Security.

## Access

- Served on the primary host only. Any other host gets 404. In development the loopback
  hosts count as primary.
- Open to roles with "Opens Controls" on. `admin` always has it; it can't be turned off.
- Opening Controls is not the same as being admin. Only admins can give or remove the admin
  role, change admin accounts (role, active, password, sessions), add admin users, create or
  delete roles, change framework roles and turn "Opens Controls" on or off. Other roles that
  open Controls manage the other users, custom role descriptions, overrides and settings.
- Everyone else gets 404 on the page and on every `/api/_controls/*` route. Anonymous
  visitors to the page go to `/login?next=/controls`.
- `ControlsAccess(allow_ips=...)` in `routes.py` and the `security.controls_ips` setting can
  both narrow it; a request must match both lists when both are set.
- `ControlsAccess(require_two_step=True)` refuses Controls with a message until two-step
  sign-in exists.
- Break glass from the server shell:

  ```
  evlet controls grant <instance> <username>
  ```

  It makes the user an active admin, ends their sessions, clears the `security.controls_ips`
  setting and writes audit rows by `cli`. It can't change `routes.py`: when `ControlsAccess`
  there limits the networks, the command says so and you edit `routes.py`.
- Saving allowed networks that leave out your own address asks first.

## Roles

`evlet_roles` holds every role. `admin`, `editor` and `viewer` are framework roles and
can't be deleted. A custom role has a base (`editor` or `viewer`) and a realm (`desk` for
now). On any model without its own rule, a custom role gets its base role's rule from the
app files. Overrides on `editor` or `viewer` do not carry over to custom roles based on them.
The ids `controls`, `portal` and `public` are reserved.

Apps can declare roles and name them in permissions:

```python
app = AppDefinition(models=(Lead,), roles={"sales": "editor"},
                    permissions={("sales", Lead): Rule(("read", "create"), scope=SCOPES["owner"])})
```

Declared roles are created on start when missing and can't be deleted in Controls. A role
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
operations, readable and writable fields must stay within the file rule. Write needs read,
no access clears fields and rows, revise needs create. Admin rules can't be overridden.
Row scopes are named presets: `created_by`, `owner`, `account_manager` and `assignee` (for
models with that field), `own_customer` and the app's own (`AppDefinition(scopes={...})`).
A row scope set in the app file can't be changed. `own_customer` reads portal customer
links (`evlet_user_links`), which arrive with portal hosts; until then it can't be chosen
and matches no records.

**Security** (kind `setting`):

| Key | File value | Rule |
|---|---|---|
| `security.password_min` | 15 | Up only, 64 at most. Applies to new passwords. |
| `security.session_hours` | 8 | Down only. Lowering it ends sessions already older than the new length. |
| `security.lockout` | 10 | Down only, 3 at least. Failed sign-ins per network and account. |
| `security.two_step_roles` | `["admin"]` | Admin stays. Saved now, enforced once two-step sign-in exists. |
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
| `options_hidden` | Choice fields: options from the app hidden from new values. A record that holds one keeps it, and saving it unchanged is allowed. One option stays shown. |

Fields the app or the server fills (calculated fields, State, Sequence and naming fields) take
only the label, help text, placeholder and hidden. The rules are checked by the API on every
create and update, legacy and document models alike; `/api/schema` and `/api/ui` show the merged
values (`choices` lists the shown options and `retired_choices` the hidden ones).

**Form layout** (kind `layout`, key `Model`): `[{"title": ..., "fields": [{"name": ..., "width": 6 | 12}]}]`,
1 to 12 sections, each with a title, each field placed once. Fields placed nowhere, such as a field an
app adds later, go to the last section. Section ids stay positional (`main_details`, `section_2`, ...).
The browser renders forms from this layout; `width: 12` makes a field full width.

**List view** (kind `list`, key `Model`): `{"columns": [...], "page_size": 25 | 50 | 100, "sort": "field" | "-field"}`,
1 to 8 columns. `/api/schema` drops the columns a role can't read and gives `page_size` and `order_by`.

### Instance fields

Add field creates a column on this instance only, named `x_` plus a slug, 40 characters at most.
App models can't declare `x_` names, so an app never clashes with one, and app migrations and
schema checks leave `x_` columns alone. Types: Text, LongText, Integer, Decimal, Currency, Date,
Boolean, Select and Link.

Adding and removing save at once, one at a time: a SQLite backup into `data/backups/` (next to the
database file), then `ALTER TABLE` in a transaction with the definition in `evlet_custom_fields`, an
audit row and a new config version. Every process attaches the field to the model class when it
sees the new version. A required field needs a default when the table has rows; the default fills
them. Required is asked for on API writes only, so app code that does not know the field keeps
working. Removing drops the column and takes the field out of every override that names it:
its own field override, list columns and sort, layout, permission field lists (a role left with no
readable field loses access) and naming series that use it. Removal needs SQLite 3.35 or later; on
an older SQLite the page says so and only adding works. On PostgreSQL the change runs in one
transaction and backups are the operator's job. Tenant instances don't have instance fields yet.

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

## Audit log

`evlet_audit` is append-only: triggers refuse update, delete and replace. Users, roles and
overrides all write to it.

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
