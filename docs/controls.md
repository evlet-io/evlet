# Controls

`/controls` is the locked admin page of an instance. It is the framework frontend
`evlet.controls`, not a generated model. This release has Overview, Users, Roles,
Permissions and Security.

## Access

- Served on the primary host only. Any other host gets 404. In development the loopback
  hosts count as primary.
- Open to roles with "Opens Controls" on. `admin` always has it; it can't be turned off.
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

  It makes the user an active admin, ends their sessions and writes an audit row by `cli`.

## Roles

`evlet_roles` holds every role. `admin`, `editor` and `viewer` are framework roles and
can't be deleted. A custom role has a base (`editor` or `viewer`) and a realm (`desk` for
now). On any model without its own rule, a custom role gets its base role's rule from the
app files.

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
A value equal to the file value is removed instead of stored. Each save writes audit rows
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
| `security.session_hours` | 8 | Down only. |
| `security.lockout` | 10 | Down only, 3 at least. Failed sign-ins per network and account. |
| `security.two_step_roles` | `["admin"]` | Admin stays. Saved now, enforced once two-step sign-in exists. |
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
| `GET/POST /api/_controls/roles`, `PATCH/DELETE /api/_controls/roles/<id>` | Roles, "Opens Controls" and descriptions |
