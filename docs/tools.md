# The Tool Engine

One set of tools serves every AI entry point: MCP clients (remote and `evlet mcp`), the API, and
Eva when it arrives. Every tool call runs as the person, through the same routes and permission
checks as the browser, and is checked again each time it runs. A tool listed earlier grants
nothing. Tools never get database, SQL, file system or shell access, and never act as anyone
else. The transaction session that a move's `run` method or an action handler gets
(`self.context.session`) is refused on every context a tool reaches, services and reports
included; a tool that runs an action runs the app's own handler
([states-actions.md](states-actions.md#the-transaction-session-in-run-methods-and-actions)).

## Tools

`evlet/tools.py` holds one definition per tool: its schema, whether it reads, writes or
destroys, and which confirmation group a change belongs to. MCP tool lists are generated from
it.

| Tool | Kind |
| --- | --- |
| `discover_models`, `describe_model`, `list_records`, `get_record` | Read |
| `aggregate_records` (the measures API), `record_history` (the journal) | Read |
| `list_reports`, `run_report` | Read |
| `list_files`, `read_file` | Read |
| `list_services`, `job_status` | Read |
| `create_record`, `update_record` | Write |
| `change_state` (State moves; confirm, cancel, reopen), `run_action`, `call_service` | Write |
| `delete_record` | Destructive |

A tool is listed only when it can apply (an app with reports, actions, documents, files or
journals), but listing grants nothing: every call is authorized again by the route it uses.
Changes to a saved record read it first and send its revision, so the change, and any
approval, name the same version. `read_file` returns UTF-8 text only (2 MB at most, 60,000
characters); other files report their size.

`change_state` takes `{model, id, to, values?, reason?}`. For a model with a `State`, `to` is the
state to move to, and the call goes to `POST /api/{model}/{id}/moves/{to}`, so the move's roles,
`when` guard, closed periods and current state are checked as they are for the browser. `values`
fills the fields the move asks for. For a confirmable document, `to` is `confirm`, `cancel` or
`reopen`, and `cancel` takes a `reason`. The tool is listed when some model is confirmable or
has a State with moves the role can see. A move into one of the State's `cancelled` states is
in the destructive group; every other move is in the confirm group.

Business services become tools only when the app marks them. Their docstring is what
assistants read; public endpoints cannot be tools:

```python
@api(method='GET', permission=(Project, 'read'), tool=True)
def project_overview(context, project: RecordId):
    """One project at a glance: its status, progress, budget and how many of its tasks are done."""
```

A parameter annotated `RecordId` takes a record's text id (`"01hc1sd9vc1ye"`) and arrives as a
`RecordId`; the tool schema describes it as a string. Services and actions take `str`, `int`,
`bool`, `float` and `RecordId` arguments.

The Playground offers `playground.project_overview` this way.

## Approvals

Being allowed to make a change does not always mean making it without a click. For MCP
clients:

| Kind of change | MCP clients |
| --- | --- |
| Read, search, totals, files | Run |
| Create or change a record | Run; the client asks its user |
| Confirm or move a document, app actions, business services | Approved in Evlet |
| Cancel, reopen, delete, a move into a cancelled state | Approved in Evlet |

The API never asks: a key or session is the person's own program. Formal approvals in apps
always apply as well.

An approval-needing call answers with an error that names the request. The person approves
it in Settings → AI assistants ("Waiting for your approval"), and the client calls the same
tool again with the same arguments. The approval covers that one call with those arguments on
that revision of the record, from that client, once, for 24 hours. If the record changes first, a new approval
is needed. Only a signed-in browser session can answer an approval, never a key or an
assistant. A change the person's role cannot make is refused at once, without asking anyone:
an operation the role lacks, a field it can't write, or a move it can't make from the record's
current state.

Eva has its own settings for this, and one more rule: once an Eva chat has read stored data,
every change asks ([eva.md](eva.md#when-eva-asks-first)).

## Routes

| Route | Purpose |
| --- | --- |
| `GET /api/_tools` | The tools this session or key may call now, with schemas |
| `POST /api/_tools/execute` | `{"tool", "arguments"}`: 200 with `result`, 202 with `approval`, or the error |
| `GET /api/_tools/approvals`, `POST /api/_tools/approvals/<id>` | A person's approvals, and `{"approve": true or false}`. Sessions only |
| `GET /api/_tools/calls` | A person's own tool calls. Sessions only |
| `GET /api/_controls/tools?origin=&outcome=&tool=&q=&before=` | The Tool log (Controls) |
| `GET /api/_reports` | The reports this person can run, used by `list_reports`. `run_report` adds `view`, the report's browser address ([reports.md](reports.md)) |

A read-only API key may call `POST /api/_tools/execute` and run reports through `run_report`;
its write tools are refused. Called directly, `POST /api/_reports/<name>` still needs a write key.

## The Tool log

`evlet_tool_calls` keeps one row per call for 90 days: when, who, origin (`mcp`, `api`, and
later `eva` and `schedule`), client, tool, record, read or write, outcome (`ok`, `denied`,
`failed`, `approval`), time, redacted inputs and the error. Inputs keep identifiers as they
are and only the names of the fields sent in values and filters, never their values. It
replaces `evlet_mcp_calls`. The framework migration `0002_tool_calls` moves its rows over and
drops it when `evlet migrate` runs after the upgrade, after the backup
([app-upgrades.md](app-upgrades.md#upgrade-evlet)). Record changes still go to each
record's journal, and settings changes and approval answers to the audit log.

Controls → AI → Tool log lists it with filters. People see their own requests in Settings.
