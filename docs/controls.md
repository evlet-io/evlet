# Controls

`/controls` is the locked admin page of an instance. It is the framework frontend
`evlet.controls`, not a generated model. This release has Overview, Users, Roles,
Permissions and Security.

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
operations, readable and writable fields must stay within the file rule. Write needs read,
no access clears fields and rows, revise needs create. Admin rules can't be overridden.
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
