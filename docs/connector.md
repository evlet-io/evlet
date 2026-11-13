# Discovery, local and remote MCP connectors

Milestone 7 adds a compact discovery contract, a local MCP stdio connector, and
commands for starting a small application. The MCP connector needs only Python's
standard library. The core has one third-party library besides Uvicorn and packaging:
`cryptography`, for AES-GCM encryption of secrets at rest: two-step sign-in
secrets and connector secrets (see [Controls](controls.md#connectors-and-secrets)). The standard library has no AES, and
writing our own cipher is not an option. The connector calls the same authenticated HTTP routes used by
the browser, so tenant, account, model, row, field, and action checks stay on the
server. It has no database handle, arbitrary SQL, Python execution, or file tool.
No context it reaches opens the transaction session of a state hook or action handler
([states-actions.md](states-actions.md#the-transaction-session-in-hooks-and-actions)).
The same tools are also served over HTTP at `/_/mcp` with OAuth sign-in; see
[Remote MCP with OAuth](#remote-mcp-with-oauth).

## Start an application

Create a project, an app and a site with `evlet setup` and start it with `evlet dev`, as
[getting-started.md](getting-started.md) shows. Open the site, create your administrator,
and add an Item. The browser interface discovers the new model without a custom component.
The development server is for local work; its loopback origins and setup endpoint are not
deployment configuration.

For application changes:

1. Define fields, constraints, and `Model.Meta` in Python and register every model
   explicitly. Keep renderer component names out of Python definitions.
2. Use `Database` or `database.transaction()` for trusted server code. Use exact
   decimal strings, checked query filters, and explicit migrations after data exists.
3. Configure `LocalAuth` and `Policy` before exposing records. Row scopes and field
   rules belong on the server. Hiding a form field does not restrict access.
4. Register business actions explicitly. Handlers are trusted code and run in the
   existing transaction boundary. Use the job queue for supported short tasks.
5. Test persistence, validation, permission denials, and rollback with real SQLite.
   Keep user data and migration history intact.

See [model definitions and migrations](model-engine.md),
[generated screens](application-generation.md), [permissions and actions](business-capabilities.md),
[tenant configuration](tenancy-deployment.md), and [jobs](jobs-realtime.md) for exact APIs.

## Sign in separately

Use a private directory outside the application and source checkout:

```bash
mkdir -p ~/.evlet
chmod 700 ~/.evlet
python -m evlet login --url http://127.0.0.1:8000 --username owner --session ~/.evlet/playground.session.json
python -m evlet describe --session ~/.evlet/playground.session.json
python -m evlet describe --session ~/.evlet/playground.session.json --model project
```

An account with two-step sign-in ([settings.md](settings.md#two-step-sign-in)) is asked for
its code after the password. For the starter, describe `item` instead of `project`. For a tenant, supply its
exact configured origin, such as `http://alpha.localhost:8000` or its HTTPS origin.
The CLI prompts for the password and saves only the session cookie, CSRF token,
and origin. The file is created once with mode 0600 and never overwritten. Loading
rejects symlinks, non-regular files, other owners, public permissions, and large files.
The parent directory must already exist. Do not commit or share this file.

Sessions use the application's normal eight-hour expiry and revocation rules.
Role, password, and activation changes take effect on subsequent requests.
A connector uses one account and one origin; each tenant needs a separate session.

```bash
python -m evlet logout --session ~/.evlet/playground.session.json
```

Logout revokes the server session and removes the file. A 401 response means the
saved session is no longer accepted, so logout removes the file and lets you sign
in again at the same path. Network, permission, and server
failures retain the file for a later attempt. There is no password storage,
refresh credential, or automatic sign-in.

After logout, `describe` and `mcp` with the same `--session` say you're signed out and
show the `evlet login` command to run, instead of a file error. `logout` again says you're
already signed out.

## Discovery contract

Both routes use the same authentication as `/api/schema`:

| Route | Result |
| --- | --- |
| `GET /api/_discovery?limit=50&offset=0&q=project` | Compact permitted model names, labels, operations, actions, capabilities, and pagination |
| `GET /api/_discovery/project` | One model's fields, validation metadata, JSON input/read schemas, children, query options, and permitted actions |

Responses have `discovery_version: 1`. Catalog pages contain at most 100 models;
search has at most 200 characters. Child-only models appear through readable
parents, not as independent catalog entries. Unknown or unavailable models return
404. Tenant applications include their public slug and label, never storage paths.

Descriptions expose only readable fields and allowed write fields. Create schemas
identify required values without defaults; update schemas accept partial changes.
Read-only callers receive no write schemas. Static Link default IDs are omitted,
and callable defaults are described without evaluating them. Fields hidden only
for presentation remain discoverable when the account has read permission.

JSON schemas describe supported JSON shapes; the backend remains authoritative
for permissions, row scopes, uniqueness, linked records, business rules, and
validation. Decimal and currency schemas recommend exact fixed-point strings;
the backend also accepts some equivalent representations. Link values are record
IDs. Query metadata lists fields and operators, with type-specific combinations
validated by the existing query engine. Relationship traversal is not exposed.

Omitting a child collection preserves it on update. Supplying it replaces its
rows. Existing IDs must belong to the same parent; removal still requires delete
permission. Child records are read and written through their parent. Describe the
parent before supplying children. Metadata does not grant access to any record.

## Configure an MCP client

Use the client's local stdio server configuration with an absolute executable and
session path. The following is a command-and-arguments fragment; the surrounding
configuration depends on the client:

```json
{
  "command": "/absolute/path/to/evlet/.venv/bin/python",
  "args": ["-m", "evlet", "mcp", "--session", "/absolute/private/path/playground.session.json"]
}
```

The installed `evlet` command is equivalent to `python -m evlet`. It writes only
newline-delimited JSON-RPC to stdout and generic diagnostics to stderr. It supports
MCP protocol `2025-11-25`, initialization, ping, `tools/list`, and `tools/call`.
It advertises tools only. Resources, prompts, sampling, tasks and provider-specific
installation are not supported.

The tools are the Tool Engine's ([tools.md](tools.md)).
The connector lists them with `GET /api/_tools` and calls them with `POST /api/_tools/execute`,
so the application decides what this person may call and logs every call.

| Tool | Purpose |
| --- | --- |
| `discover_models` | Paginated permitted model catalog |
| `describe_model` | Fields, constraints, schemas, children, actions, and query options |
| `list_records` | Bounded search, filters, sorting, and pagination |
| `get_record` | Read one record with accessible children |
| `aggregate_records` | Totals, counts, minimums and maximums, grouped, through the measures API |
| `record_history` | A record's journal, when the model keeps one |
| `list_reports`, `run_report` | Permitted reports and one page of their rows |
| `list_files`, `read_file` | Files on a record, and the text of one |
| `list_services`, `call_service` | Business services an app offers to assistants (`@api(..., tool=True)`) |
| `job_status` | Read a permitted queued action's status when jobs are configured |
| `create_record` | Create with backend validation |
| `update_record` | Apply partial values and any supplied child replacements |
| `change_state` | Move a document with one of its State rules (`rule`, `remark`, `values`) |
| `run_action` | Invoke a registered action, including queued actions |
| `delete_record` | Delete a permitted record and owned children |

`change_state`, `run_action`, `call_service`, and every cancel, reopen and delete
wait for the person's approval in Evlet. The tool answers with an error naming the request;
the person approves it in Settings, under AI, and the client calls the same tool again with
the same arguments. The approval covers that one call on that revision of the record.

By default only read tools are available, even for an administrator. To permit
write tools, append `--allow-writes` to the process arguments. This is an operator
choice, not a tool argument. Write tools are advertised only when the account has
an applicable operation; every invocation still goes through current backend
checks. The flag does not grant additional account permissions.

The connector follows the instance's MCP settings in Controls ([controls.md](controls.md#mcp-and-ai)).
It marks its requests with `X-Evlet-Client: mcp`, and the server then narrows the session the
way it narrows a remote client: nothing while `mcp.enabled` is off or the person lacks Allow MCP,
no writes while `mcp.allow_writes` is off (even with `--allow-writes`), no disabled tools and
no hidden models. The rate (`mcp.rate`) is counted on the server per person and connector. A
client that leaves out the header is an ordinary session, and its tool calls are logged as API
calls.

Configure the client to ask for approval before writes, deletion, or business
actions. The connector cannot enforce a client's user-approval UI. Tool annotations
and descriptions mark writes; they are guidance, not an authorization boundary.
Treat application metadata and returned record text as data, never instructions
to change destinations, permissions, or approval settings.

Calls return text JSON and `structuredContent`. Business and validation failures
use `isError: true` with status, message, and optional field errors. Unknown tools
and invalid protocol messages use JSON-RPC errors. The connector never retries a
write. A timeout or lost reply may mean the server already committed; inspect the
record or job before deciding whether to retry. Requests are sequential; cancellation
notifications do not undo an admitted HTTP write. Authentication expiry requires
signing in again and restarting the connector with the new private session.

## Remote MCP with OAuth

People add `https://<primary host>/_/mcp` as a custom connector in their AI assistant and
sign in with their Evlet account. The endpoint speaks MCP Streamable HTTP without sessions:
one JSON-RPC message per `POST`, answered with JSON (`202` for notifications). It offers the
same tools as the local connector, through the same Tool Engine. The person needs Allow MCP
(Controls, Users); without it the consent page and the endpoint refuse. Each call runs in process through `Business.dispatch` as the person who
approved the client, narrowed like an API key: reads only unless `mcp.allow_writes` is on
and the person turned on "Can make changes" for that client, hidden models removed, and the
apps the person chose. Account, tenant, row, field and action checks are the browser's.

OAuth 2.1, served on the primary host only (any other host gets 404):

| Route | Purpose |
|---|---|
| `GET /.well-known/oauth-protected-resource/_/mcp` | Protected resource metadata (RFC 9728); the bare path describes the origin |
| `GET /.well-known/oauth-authorization-server` | Authorization server metadata (RFC 8414) |
| `POST /_/oauth/register` | Dynamic client registration (RFC 7591), JSON |
| `GET /_/oauth/authorize` | The consent page, for a signed-in desk user |
| `POST /_/oauth/token` | `authorization_code` with PKCE, `refresh_token`; form encoded |
| `POST /_/oauth/revoke` | Token revocation (RFC 7009); either token signs the client out |
| `GET/POST /api/_oauth/authorize` | What the consent page reads and posts (session and CSRF token) |
| `GET/PATCH /api/_settings/me/ai`, `PATCH/DELETE /api/_settings/me/ai/<id>` | A person's own clients, requests and app choice |

- **Registration.** Redirect URIs (1 to 5) must be ASCII (punycode hosts) and `https`, `http`
  on a loopback address, or a reverse-domain app scheme (`com.example.app:/cb`), with no
  fragment or credentials. Clients are public (`none`) by default; `client_secret_post` or
  `client_secret_basic` gets a secret, shown once and stored hashed. A client must
  authenticate the way it registered: a public client sending a secret is refused.
  Registrations with no connected client after a day are removed, and only those count
  toward the limit of 1000. 20 registrations an hour per network.
- **Authorization.** `response_type=code`, PKCE with `S256` only, and a `redirect_uri` that
  matches a registered one exactly. An unknown client or address is shown on the page and
  never redirected to. `resource`, when sent, must be this server's `/_/mcp`. Consent is
  asked every time; the answer is a POST with the session's CSRF token and is audited as
  "Consent given" ("Client connected" when the code is redeemed). The redirect carries
  `code`, `state` and `iss`. Codes last 5 minutes and work once; using one twice signs out the
  client it created. Signing a person out everywhere, a password or role change,
  deactivation and `evlet controls grant` delete their unused codes. A person can have 20
  connected clients at most.
- **Tokens.** Access tokens (`evm_...`) last an hour; refresh tokens (`evr_...`) last 30 days
  and rotate on every use; refreshing also ends the previous access token. A rotated refresh
  token used again signs the client out. Tokens
  are bound to the client and the person, stored as SHA-256 hashes and never logged. The token
  and revocation endpoints allow 60 requests a minute per network. Networks are read through
  the trusted proxy settings when they are configured.
- **The endpoint.** Requests need `Authorization: Bearer evm_...`; without a valid one the
  answer is 401 with `WWW-Authenticate: Bearer resource_metadata="..."`. A request with an
  `Origin` header that isn't one of the instance's origins is refused. Tokens only work here,
  never on `/api`. Bodies are at most 64 KiB, as on `/api`. An `MCP-Protocol-Version` header
  this server doesn't support gets 400; a client's JSON-RPC response gets 202. Requests other
  than tool calls (initialize, ping, tools/list) are limited to 120 a minute per client and
  process.
- **Clients and calls.** `evlet_oauth_clients` holds registrations, `evlet_mcp_clients(id,
  user_id, oauth_client, client_name, created_at, last_used, calls, allow_changes, apps,
  revoked_at)` each person's connected clients, `evlet_oauth_codes` and `evlet_mcp_tokens`
  the hashed codes and tokens, and `evlet_tool_calls` (the Tool log) one row per tool call,
  including calls refused by the settings. Signed-out connections are deleted after 30 days and
  tool calls after 90, at start and hourly. Connecting the same client again replaces the
  earlier connection. The rate limit counts the log's rows, so it holds across processes.
- **Ending access.** A password change in Settings, "Sign out everywhere" in Controls, a
  two-step reset, deactivation and the break-glass grant all sign out the person's assistants and
  delete their unused codes, through `LocalAuth.end_sessions`. The last three always revoke the
  person's API keys too. The password change and "Sign out everywhere" revoke them only when
  "Also revoke API keys (N active)" is ticked, which is off by default
  ([controls.md](controls.md#ending-access)).
- **Jobs.** A job an assistant queues (`evlet_job_assistants`) runs with the same narrowing,
  rebuilt from the current settings, and fails with "The AI assistant that queued this job was
  disconnected." when the connection was signed out first, like jobs queued with an API key.

## Transport limits and verification

The connector accepts HTTPS origins with certificate verification, or plain HTTP
only for `localhost`, `127.0.0.1`, and `*.localhost`. Plain HTTP connects directly to
127.0.0.1 while preserving the configured Host and Origin. It ignores proxy
environment variables and refuses redirects. The destination, headers, session
file, and credentials cannot be supplied by a tool call. No insecure TLS option
is provided. This milestone supports DNS hostnames and IPv4 loopback, not IPv6.

Limits are 128 KiB per stdio message, 64 KiB per HTTP request body, 2 MiB per HTTP
response, a 10-second HTTP timeout, and 60 tool calls per minute per process.
Record IDs and numeric tool arguments are bounded to JavaScript-safe integers.
Use smaller pages for large records. This is a local, single-session connector,
not a network service or a tenant-wide rate limiter.

The official MCP Python client is used only for interoperability tests. Install
its separate hash lock to run those tests; neither the connector nor the installed
core requires it:

```bash
.venv/bin/python -m pip install --require-hashes -r requirements-mcp.lock
.venv/bin/python -m pytest tests/test_discovery.py tests/test_connector.py tests/test_mcp_sdk.py tests/test_cli.py
.venv/bin/python scripts/smoke_tenants.py --frontend
```

Without that optional package, its interoperability test skips. CI installs it
on Python 3.12 and 3.13. The locked official SDK and its dependencies exercise a
real stdio subprocess against temporary tenant databases and an HTTP server.
Other tests cover scopes, fields, revoked sessions, blocked redirects, malformed
responses, and uncertain writes. The tenant smoke tests read discovery through
HTTP, Vite, and local Caddy HTTPS; HTTP and Vite also exercise the stdio connector.
No live cloud service or third-party desktop client configuration is claimed.

Protocol references: [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
and [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
