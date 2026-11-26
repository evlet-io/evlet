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
sign-in of [assistants](connector.md). Each declares a tier; every tool of this release is `operate`. A tool that
changes something starts an operation and answers `{"operation": id}`: follow it with `operation_status`. One
operation runs at a time; another is refused until it ends.

| Tool | What it does |
| --- | --- |
| `project_status` | The project id, mode, endpoint and MCP address, and each instance with id, host, version, apps, status and health |
| `operation_log`, `operation_status` | The newest operations; one operation with its output, live while it runs |
| `instance_logs` | The last lines an instance's process printed. They can hold values that tracebacks print |
| `instance_restart`, `instance_stop`, `instance_start` | Through the supervisor; restart and start wait for `/health` |
| `backup_create`, `backup_list` | A backup set while the instance serves; the sets by name |
| `backup_restore` | Destructive. Without `confirm: true` it only answers with what will be lost |
| `app_update_plan`, `app_update` | An installed app from its Git source to a tag, branch or commit: plan, backup, stop, migrate, start, health check, and the previous commit and the backup back on a failure |
| `app_install` | A Git URL, or in development an app in `apps/`. In production only URLs that start with one of the `sources` in `project.json` |
| `evlet_upgrade_plan`, `evlet_upgrade` | Stage a newer Evlet and plan; or upgrade every instance, then the endpoint restarts itself on the new Evlet |

`sources` is a list of URL prefixes in `project.json`, such as `["https://github.com/acme/"]`. Edit it on the server;
no tool changes it.

MCP clients get the framework's approvals: tools that change something wait for the owner's approval in Settings,
as `call_service` does on any instance ([tools.md](tools.md#approvals)).

## Development and production

| | Development | Production |
| --- | --- | --- |
| Address | `http://127.0.0.1:7999` | `https://HOSTNAME` behind Caddy |
| Instances | `evlet dev` children, reloading | `evlet serve --jobs` children |
| Sign-in | one-time link; password and two-step optional | password and two-step sign-in |
| Docs | on | off |
| `app_install` | Git URLs and apps in `apps/` | Git URLs under `sources` only |

Everything else is the same code, files and processes.

## Not yet

The develop tier of tools, choosing the tiers MCP clients see by mode, and a management channel for the CLI to ask a
running endpoint (so `evlet restart` goes through it) come next. Until then `evlet restart` and `evlet upgrade`
from the shell use systemd as before; on a project whose endpoint runs the instances, use the portal or the tools.
`host_add`, `host_remove` and `host_check` wait for hosts read from data.
