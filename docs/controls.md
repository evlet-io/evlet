# Controls

`/controls` is the locked admin page of an instance. It is the framework frontend
`evlet.controls`, not a generated model. This release has Overview, Users, Roles,
Permissions, Security, Connectors, Email, MCP & AI and API keys.

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

## API keys

Keys let scripts call the API as a user: `Authorization: Bearer evk_...`. A key is shown
once when it is created; only its prefix (`evk_` and 8 characters) and the SHA-256 of the
whole key are stored in `evlet_api_keys`.

- **Never more than the user.** A key acts as its user with that user's current role, row
  scopes and field rules, narrowed by its scope:
  `{"access": "read" | "write", "models": null | ["Customer", ...]}`. A read key refuses
  every request that isn't GET, including reports and draft evaluation, which are POST. A key
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

The personal page is the `MyKeysPage` component (`frontend/src/evlet/settings/keys.ts`),
ready for the Settings shell.

## MCP and AI

People connect AI assistants (MCP clients such as Claude) to the instance. Remote clients
use `https://<primary host>/_/mcp` and sign in with OAuth; local ones run `evlet mcp`
([connector.md](connector.md)). Both act as the person who signed in and go through the same
API routes and permission checks as the browser.

| Key | File value | Rule |
|---|---|---|
| `mcp.enabled` | true | Off stops every client at once: `/_/mcp` answers 403 and sign-in pages are gone. |
| `mcp.allow_writes` | false | The page asks before turning it on. Write tools need it on, and each write still needs the user's permission. |
| `mcp.rate` | 60 | Tool calls a minute per client, 1 to 60. The framework cap stays at 60. |
| `mcp.disabled_tools` | `[]` | Tool names. A disabled tool isn't listed and is refused. |
| `mcp.hidden_models` | `[]` | Model names. Hidden models (and their child rows) never reach an AI client, even an admin's. |

A stored value that no longer validates fails closed: the switches turn off, the rate becomes
1, and a list that isn't a list disables every tool or hides every model. Changes are audited
under `MCP`.

The page also lists connected clients (who, last call, number of calls) and totals for the
last 24 hours: reads, writes and calls denied by permissions or by these settings. Admins can sign a client out at
once; only admins sign out an admin's client. Per-request detail is only on each person's own
AI page. Clients also count in the Users page sessions, and "Sign out everywhere", a password
or role change and deactivation sign them out too.

The personal page is the `MyAIPage` component (`frontend/src/evlet/settings/ai.ts`), ready for
the Settings shell. It shows the connect steps, the person's assistants with "Can make changes"
and Disconnect, the apps assistants can use (only narrower than the person's own access; once
chosen, apps installed later stay off), their last 50 requests from the past 30 days and the
local `evlet mcp` steps. "Can make changes" has effect only while
`mcp.allow_writes` is on.

## Audit log

`evlet_audit` is append-only: triggers refuse update, delete and replace. Users, roles,
overrides, connectors, email accounts, API keys and AI assistants (consent, sign-out,
"Can make changes", MCP settings) all write to it.

## API

| Route | Purpose |
|---|---|
| `GET /api/_controls/state` | Instance, apps, models, roles, routes, settings, attention list, recent audit rows |
| `GET/POST /api/_controls/overrides` | File value, override, who and when; save changes |
| `GET/POST /api/_controls/users`, `PATCH /api/_controls/users/<id>` | Wraps `LocalAuth`: role, active, password, `{"sessions": "end"}` |
| `/api/auth/users` | The older account routes, kept. They use the same access check, admin rules and audit rows; refused access is a 403 there |
| `GET/POST /api/_controls/roles`, `PATCH/DELETE /api/_controls/roles/<id>` | Roles, "Opens Controls" and descriptions |
| `GET /api/_controls/connectors` | Declared connectors with their fields, and every saved connector and email account |
| `GET/PUT/DELETE /api/_controls/connectors/<id>` | One connector (`whatsapp`) or account (`smtp:accounts`): read, save `{"settings", "secrets"}`, disconnect |
| `POST /api/_controls/connectors/<id>/test`, `POST .../default` | Test within 10 seconds; make an outgoing account the default |
| `GET /api/_controls/mcp`, `DELETE /api/_controls/mcp/<id>` | MCP address, connected clients, 24-hour totals; sign a client out |
| `GET/POST /api/_controls/keys`, `DELETE /api/_controls/keys/<id>` | API keys: list, create `{"name", "user", "scope"}` (the key is in this reply only), revoke |
