# Discovery and local connectors

Milestone 7 adds a compact discovery contract, a local MCP stdio connector, and
commands for starting a small application. The MCP connector needs only Python's
standard library. The core has one third-party library besides Uvicorn and packaging:
`cryptography`, for AES-GCM encryption of connector secrets at rest (see
[Controls](controls.md#connectors-and-secrets)). The standard library has no AES, and
writing our own cipher is not an option. The connector calls the same authenticated HTTP routes used by
the browser, so tenant, account, model, row, field, and action checks stay on the
server. It has no database handle, arbitrary SQL, Python execution, or file tool.

## Start an application

From an environment with the framework and Uvicorn installed:

```bash
python -m evlet new ./my-app
cd my-app
python -m uvicorn app:create_app --factory --host 127.0.0.1 --port 8000
```

The destination must not exist. This writes `models.py`, `app.py`, `.gitignore`,
and a README. `Item` demonstrates required text, a boolean default, and neutral
list/search metadata. It uses public framework exports, persists in `.data/`, and
starts with local authentication. No account or password is generated.

Run `npm --prefix frontend run dev` from the framework checkout in another terminal.
Open `http://127.0.0.1:5173`, create your administrator, and add an Item. The browser
interface discovers the new model without a custom component. Ports 8000 and
5173 must be available. This starter is for local development; its exact loopback
origins, setup endpoint, and non-Secure cookies are not deployment configuration.

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

For the starter, describe `item` instead of `project`. For a tenant, supply its
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
It advertises tools only. Resources, prompts, sampling, tasks, remote MCP HTTP,
OAuth, and provider-specific installation are outside this milestone.

| Tool | Purpose |
| --- | --- |
| `discover_models` | Paginated permitted model catalog |
| `describe_model` | Fields, constraints, schemas, children, actions, and query options |
| `list_records` | Bounded search, filters, sorting, and pagination |
| `get_record` | Read one record with accessible children |
| `job_status` | Read a permitted queued action's status when jobs are configured |
| `create_record` | Create with backend validation |
| `update_record` | Apply partial values and any supplied child replacements |
| `delete_record` | Delete a permitted record and owned children |
| `run_action` | Invoke a registered action, including queued actions |

By default only read tools are available, even for an administrator. To permit
write tools, append `--allow-writes` to the process arguments. This is an operator
choice, not a tool argument. Write tools are advertised only when the account has
an applicable operation; every invocation still goes through current backend
checks. The flag does not grant additional account permissions.

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
