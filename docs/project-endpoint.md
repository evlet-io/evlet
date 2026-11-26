# The project endpoint

A project can have one endpoint: a small Evlet application of its own that runs every instance of the project,
keeps a log of what was done to them, and gives their owner a portal and an MCP address to operate them. Without an
endpoint nothing changes: no project process, no `project.db`, instances served as before with `evlet dev`,
`evlet serve` or one systemd unit each.

Evlet is not production ready; the endpoint is new in 0.4.x and its tools and pages may change within 0.4.

## Add one

```sh
evlet setup myproject --endpoint                         # a new project with an endpoint
evlet setup --endpoint                                   # add one to the project you are in
evlet setup myproject --app notes --instance acme --endpoint
evlet setup --endpoint --endpoint-host project.example.com --apply   # production, behind Caddy
```

`--endpoint` makes `.evlet/project.sqlite3` (project.db) and `.evlet/endpoint.json`, then, in development, serves
the endpoint on `127.0.0.1:7999` and prints a one-time sign-in link, as `evlet dev` does. `--port` picks another
port. Run it again later with `evlet endpoint`; `evlet endpoint link` prints a new link. `evlet setup --endpoint`
runs its steps again safely: an existing project.db is kept.

`--endpoint-host HOSTNAME` is for a server. It asks for the owner's password, writes
`deploy/evlet-project.caddy` (the host, proxied to the endpoint on loopback) and `deploy/evlet-project.service`
(the project's only systemd unit, which runs `evlet endpoint`), and prints the `sudo` steps that install them;
`--apply` runs them, each shown first. `--service-user` picks the account the unit runs as. The endpoint then
answers on `https://HOSTNAME`, with `__Host-` cookies, and trusts the client address Caddy forwards.

`evlet project` prints the endpoint's address and whether it answers (`Endpoint: http://127.0.0.1:7999 (running)`).

## project.db holds credentials and logs only

| What | Where |
| --- | --- |
| The owner account: name, email, password hash, two-step secret, sessions | the framework's account tables in project.db |
| OAuth clients and tokens of MCP clients | the framework's assistant tables |
| The operation log | `operation`, an append-only ledger model |
| The Tool log of MCP calls | `evlet_tool_calls`, as on every instance ([tools.md](tools.md#the-tool-log)) |

Instances, apps and hosts are never copied into it. The endpoint reads them from the files that hold them each time:
`instances/*/config.py`, each instance's `apps.lock` and database, `routes.py`. Two-step and connector secrets are
sealed with `.evlet/project-secret.key`, kept apart from project.db.

`.evlet/endpoint.json` holds the endpoint's port, its public host (null in development) and the port of each
instance: the port of an existing `evlet setup --host` unit when there is one, else the next free port from 8000.

## The owner

The endpoint has one account, the owner: the framework's built-in Administrator (`admin`) of project.db. There are
no roles and no permission rules to set. Every project tool names the permission `(Project, 'read')`, which only
the owner holds; signed out, the portal shows nothing but the way to sign in, and the services answer 401.

Two-step sign-in is optional in development and required in production. `evlet project mode production` refuses
while the project has an endpoint without a public host, or while the owner has not set up two-step sign-in: sign
in, open Settings, turn it on, then switch.

## The operation log

Every lifecycle operation appends two rows: one when it starts and one when it ends. The end row has the result
(`ok` or `failed`), the start and end times, the output lines (as the CLI prints them), the instance's `apps.lock`
before the operation and the error. The start row has the kind, the instance, the arguments and the origin:

| Origin | Where it ran |
| --- | --- |
| `cli` | an `evlet` command: `install`, `migrate`, `makemigration`, `backup`, `restore`, `restart`, `upgrade`, `host add`, `host remove`, `app`, `build`, `instance` |
| `portal` | a button in the portal |
| `mcp` | a tool call of an MCP client |

The CLI writes to the log only when the project has project.db; without it the commands do exactly what they did.
When the log cannot be written, the command still runs and says so on stderr. A start row without an end row was
run by another process that has not finished, or that was stopped.

## The supervisor

With an endpoint, the endpoint process runs each instance as a child process: `evlet dev NAME --no-link` in
development, which reloads app source, and `evlet serve NAME --jobs` in production, each on 127.0.0.1 and its own
port. Instances that have a database start with the endpoint; an empty one starts with `instance_start` once an app
is installed.

- A child that exits without being asked is started again after 1, 2, 5, 10, then 30 seconds for crashes in a row;
  the count resets after a minute of running.
- An operation that needs an instance's exclusive lock (app install, restore, app update, Evlet upgrade) stops its
  child first and starts it again afterwards, whether it succeeded or not.
- Stopping and starting need no `sudo`: the endpoint owns the processes. In production the endpoint's unit is the
  only one; stopping it stops every instance.
- The last 2,000 lines each child printed are kept in memory for `instance_logs`.

## The portal

The portal is the page at `/`, built by the frontend production build with Evlet's own UI framework
(`frontend/src/project/`). It shows the project id and mode, each instance with its id, host, Evlet version, apps,
status and health, the operation log with each operation's live output, and the MCP address, with a button for each
operate tool. A restore always asks first and says that every change since the backup is lost. Sign-in, Settings
(two-step sign-in) and Docs (development only) are the framework's own pages on the endpoint.

## The project tools

The tools are `@api(tool=True)` services of the endpoint (`evlet/cli/project_tools.py`), called by the portal at
`/api/_services/<tool>` and by MCP clients at `/_/mcp` through `list_services` and `call_service`, after the OAuth
sign-in of [assistants](connector.md). A long tool starts an operation and answers `{"operation": id}`: follow it
with `operation_status`. One operation runs at a time; another is refused until it ends. A short change (a file, a
host, an override) runs in its request and answers `{"operation": id, "result": "ok" | "failed", ...}`; it is logged
as one operation too.

There is no shell tool. "Anything in the project" means every Evlet command and the project's files, never arbitrary
processes.

### Tiers

Each tool declares its tier: `operate` (running the project, in both modes) or `develop` (changing it, in
development only). The tier rule is read from `project.json` on every call, never from the client: in production a
develop tool is not in `list_services`, a call gets 403 `Not available in production`, and the portal hides its
buttons. Only the server shell changes the mode (`evlet project mode production|development`); no tool and no
portal page can, so an AI client can never unlock its own tools. Switching to production refuses until the endpoint
has a public host and the owner has set up two-step sign-in. Restart the endpoint afterwards so its children, Docs
and sign-in follow the mode too; the tier rule follows at once.

The project tools never read an instance's records, in either mode; the instance's own MCP endpoint does that. In
development this is a boundary of the tools, not a security one: app code they write runs in the instance and can
read anything there. In production no code changes, so it holds.

#### Operate (development and production)

| Tool | What it does |
| --- | --- |
| `project_status` | The project id, mode, endpoint and MCP address, each instance with id, host, version, apps, status and health, and the tools available now with their tiers |
| `operation_log`, `operation_status` | The newest operations; one operation with its output, live while it runs |
| `instance_logs` | The last lines an instance's process printed. They can hold values that tracebacks print |
| `instance_restart`, `instance_stop`, `instance_start` | Through the supervisor; restart and start wait for `/health` |
| `backup_create`, `backup_list` | A backup set while the instance serves; the sets by name |
| `backup_restore` | Destructive. Without `confirm: true` it only answers with what will be lost; it always asks for approval |
| `app_update_plan`, `app_update` | An installed app from its Git source to a tag, branch or commit: plan, backup, stop, migrate, start, health check, and the previous commit and the backup back on a failure |
| `app_install` | A Git URL, or in development an app in `apps/`. In production only URLs that start with one of the `sources` in `project.json` |
| `evlet_upgrade_plan`, `evlet_upgrade` | Stage a newer Evlet and plan; or upgrade every instance, then the endpoint restarts itself on the new Evlet |
| `host_add`, `host_remove`, `host_check` | Hosts from data ([routing.md](routing.md#hosts-from-data)): through the running instance's management channel, or straight to its database when it is stopped. No restart |

`sources` is a list of URL prefixes in `project.json`, such as `["https://github.com/acme/"]`. Edit it on the server;
no tool changes it.

#### Develop (development only)

| Tool | What it does |
| --- | --- |
| `files_list`, `file_read`, `file_write`, `file_delete` | Files in `apps/`, and the `config.py` and `routes.py` of an instance (config.py is never deleted). UTF-8 text; a write is at most 64 KiB, a read 1 MiB |
| `app_create`, `app_fetch` | `evlet app NAME`; `evlet app --get URL` from any Git URL |
| `makemigration`, `migrate`, `build` | The commands, through the lifecycle core. `migrate` stops the instance, backs up and starts it again; a destructive step is refused |
| `tests_run` | pytest in the project's `.venv` on `apps/` or one app or test path in it, for at most `seconds` (300 by default, up to 900); the operation keeps pytest's last 400 lines and is `ok` when it passed |
| `instance_create`, `instance_delete` | A new instance with its own port; or stop one and move its folder (database, files, `secret.key`) to `.evlet/deleted/`. Delete always asks |
| `controls_override_set`, `settings_set` | A Controls override, or an app's settings, of the running instance, through the management channel |
| `site_files_write`, `site_publish`, `site_rollback` | A site's draft files, a release, or an earlier release again ([sites.md](sites.md)), through the management channel |

The file tools reach nothing else: never `.venv`, `.evlet`, an instance's `data/`, `secret.key`, `control.key`,
`apps.lock`, a part that starts with a dot (`.git`, `.env`) or a link. A path is relative, with `/` between parts and
nothing to normalise; a refused path writes nothing, not even a log row.

### Approvals

MCP clients get the Tool Engine's approvals ([tools.md](tools.md#approvals)): a tool that changes something waits for
the owner's approval in Settings, under AI (`GET /api/_tools/approvals`, `POST /api/_tools/approvals/<id>`), then the
client calls it again. Reads never ask.

- In production `app_install`, `app_update`, `evlet_upgrade`, `backup_restore` and `instance_delete` always ask,
  whatever else is set.
- `backup_restore` and `instance_delete` always ask in development too. The approval says what is lost: every change
  since the backup, or the instance's folder moved away.
- Every other tool may be set never to ask: list it under `never_ask` in `.evlet/endpoint.json`, on the server. The
  portal is the owner's own session and asks no approvals; `backup_restore` there still needs `confirm: true`.

## The management channel

The endpoint reaches a running instance only through `/_/manage/*` on the instance (`evlet/manage.py`), never through
its database or its `/api`:

- It answers only a loopback caller that is not behind a proxy (no `Forwarded` or `X-Forwarded-For` header) and sends
  the instance's token as `Authorization: Bearer ...`. Everything else gets 404, as `/_/tls-allowed` does.
- Each instance has its own token. The endpoint keeps it in `.evlet/control/<instance>.token`; the instance keeps only
  its SHA-256 in `instances/<instance>/control.key`. Both are 0600. The endpoint makes them before it starts the
  instance; an instance without `control.key` (a project without an endpoint) has no management channel.
- The token authenticates these routes only. It is no session, API key or OAuth token: `/api/*`, `/_/mcp`, Controls
  and every other route answer as they do without it (`tests/test_project_mcp.py` walks every route).
- It never reads or writes records. Writes are audited on the instance with the actor `project`.

| Route | What |
| --- | --- |
| `GET health`, `version`, `lock`, `id` | `/health`'s check, the Evlet version and schema, `apps.lock`, the instance id with the project id and mode |
| `GET overrides`, `POST overrides` | Controls overrides; writes in development only |
| `GET settings`, `POST settings` | App settings; writes in development only |
| `GET hosts`, `POST hosts/add`, `hosts/remove`, `hosts/check` | Hosts from data, in both modes |
| `GET sites`, `sites/files`, `sites/releases`; `POST sites/files`, `sites/publish`, `sites/rollback` | Site files and releases; writes in development only |

### evlet restart and evlet upgrade

While the endpoint runs, `evlet restart NAME` and `evlet upgrade` ask it to do the work (`instance_restart`,
`evlet_upgrade`) and print the operation's output as it comes; it is logged with origin `cli`. They reach it on
loopback with `.evlet/control/endpoint.token` (0600). With `--service`, with `--check`, or when the endpoint does not
answer, they use systemd as before.

## Development and production

This table is the whole difference between the modes.

| | Development | Production |
| --- | --- | --- |
| Address | loopback, `.localhost` names: `http://127.0.0.1:7999` | a domain: `https://HOSTNAME` behind Caddy |
| Instances | `evlet dev` children, reloading app source | `evlet serve --jobs` children, no reload |
| Docs | on | off |
| Sign-in | one-time link; password and two-step sign-in optional | password and two-step sign-in required |
| Project MCP | operate and develop tools | operate tools only |
| `app_install` | any Git URL, and apps in `apps/` | Git URLs under `sources` only |

Everything else is the same code, files and processes. Cookies, allowed origins and trusted proxies follow from
having a public host, not from the mode.

A recorded session of an MCP client against a real endpoint, in both modes, is in
[internal/project-mcp-demo.md](internal/project-mcp-demo.md) (made by `docs/internal/project_mcp_demo.py`).
