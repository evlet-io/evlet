# Eva and the Tool Engine

Eva is Evlet's built-in AI assistant. It runs on the Tool Engine ([tools.md](tools.md)), the one
set of tools that Eva, MCP clients and the API share. Every tool call runs as the person, through the same routes
and permission checks as the browser, and is checked again each time it runs. Eva never gets
database, SQL, file system or shell access, and it never acts as anyone else. It never reaches
the transaction session of a move or action handler (`self.context.session`) either; see
[tools.md](tools.md).

Eva is off by default. The framework works the same with no AI provider configured.

## Turning Eva on

Three switches, all needed:

1. **config.py** (the hard gate). An instance's `config.py`, or `Application(ai=...)`:

   ```python
   from evlet import AI

   ai = AI(enabled=True, models={'Standard': 'gpt-4.1', 'Fast and low cost': 'gpt-4.1-mini'})
   ```

   `models` maps the names people see to the provider's model ids. Without `ai`, or with
   `enabled=False`, Eva does not exist: no routes (they answer 404), no tables, no provider
   requests, no threads and no Eva entries in the interface. Controls cannot override it.
2. **Controls → AI → Eva.** Turn Eva on for the instance (`eva.enabled`, off by default) and
   choose the default model. Add the OpenAI key under **Connectors → OpenAI**. It is stored
   encrypted with the other connector secrets and never sent to the browser.
3. **Controls → Users.** Turn on **Allow Eva** for each person who may use it. It is off for
   everyone by default and grants no access to records.

The Playground turns Eva on when `EVLET_EVA_MODEL` names an OpenAI model.

`AI(...)` takes `provider='openai'` (the default) or an object with `stream(request)` (tests use
a scripted one), `base_url` (an https address, http only on this machine) and
`max_output_tokens` (256 to 64,000, default 4,000). The OpenAI adapter uses the standard
library (`urllib`) and Chat Completions with streaming. It adds no dependency. It never follows
a redirect: a 3xx answer is a provider error, so the API key is only ever sent to `base_url`.

## What people see

- **Eva** in the sidebar, at `/eva`. Conversations sit in their own column: search, rename,
  archive and delete. Each answer streams in. Each tool call shows as a grey line while it
  runs, then folds into a row that opens for its tool, status, time, inputs (redacted),
  records and errors. A refused call says so, and Eva carries on.
- **Approvals.** Some changes wait for a click (see below). The card names the change and the
  record, and the run waits. The approval covers that one call with those arguments on that
  revision of the record. If the record changes first, Eva asks again.
- **Files.** Upload PDF, CSV, Excel, Word, text or image files (1 MB each, 5 per message), or
  choose a file attached to a record the person can open. PDFs and images go to the model as
  they are; Excel and Word are read as text with the standard library. Eva makes files with
  `create_file` (CSV, text, Markdown, JSON, HTML, or an Excel workbook from rows). Files belong
  to the person and the chat.
- **Ask Eva** on a record opens a side panel with the record as the chat's context.
- **Scheduled tasks** at `/eva/tasks` (see below).
- **Settings → AI** shows the person's allowance, their tasks and the changes waiting for their
  approval from outside assistants.
- When an allowance is used up, the message box says so and when it resets. Chats, files and
  results stay readable.

Everything shown is rebuilt from what Evlet stored, so reopening a chat shows the same rows.
A dropped connection never runs anything twice.

## Who can see what

Conversations belong to their owner. Nobody else can open them, admins included. Controls →
Usage shows owners, counts and cost per conversation, never titles or messages. The Tool log
shows tool calls with redacted inputs: identifiers as they are, and only the names of the
fields sent in values and filters.

Models hidden from AI in Controls → MCP (`mcp.hidden_models`) are hidden from Eva too.

## When Eva asks first

Being allowed to do something does not always mean doing it without a click. Controls →
AI → Eva sets this per kind of change (`eva.confirm`):

| Kind of change | Eva default | MCP clients |
| --- | --- | --- |
| Read, search, totals, files | Never asks | Never asks |
| Create a record or draft (`create`) | Never asks | The client asks |
| Change a record (`update`) | Never asks | The client asks |
| Confirm a document (`confirm`) | Asks | Approved in Evlet |
| App actions and business services (`action`) | Asks | Approved in Evlet |
| Cancel, reopen, delete | Always asks | Approved in Evlet |

The API never asks: a key or session is the person's own program. Formal approvals in apps
always apply as well, and a conversational yes never bypasses them.

An outside assistant that hits an approval gets an error naming the request. The person
approves it in Settings → AI, and the assistant calls the same tool again with the same
arguments. Only a signed-in browser session can answer an approval, never a key or an
assistant.

## Allowances and usage

Every model request is counted: input, cached input, output and reasoning tokens, requests,
tool calls, time and estimated cost. Costs use the rates in Controls (`eva.rates`, AED per
million tokens per model) and are kept in millionths of a dirham, so totals are exact. Each
row keeps the rate it used. Cached input is part of input and reasoning is part of output, so
neither is counted twice.

Allowances (`eva.instance_limit`, `eva.user_limit`, and per person `eva.person_limits`) are an
amount in AED, tokens or requests per day, week (from Monday) or month, in the instance time
zone. `eva.task_reserve` keeps a share of the instance allowance that chats cannot use, so
scheduled tasks still run. Before each request Eva holds its largest possible cost (the
request plus a full answer) against both the person's and the instance's allowance, in one
transaction that also takes a lock row, so two requests cannot both take the last of an
allowance. The hold is replaced by the real usage when the answer arrives, and released if the
provider refused before answering.

An AED allowance needs a rate for the model in use. While a model has none, Eva refuses its
requests with a message saying so rather than counting them as free, and Controls lists the
models still missing a rate. Token and request allowances work without rates.

`eva.max_steps`, `eva.max_tools` and `eva.max_seconds` limit one answer.

Outside assistants use their own models, so their tokens are not counted.

## Scheduled tasks

A person writes what Eva should do and when ("Every Monday at 9 AM, review my overdue
invoices"). Evlet reads common schedules from plain words (every day, weekdays, named days,
the 1st of each month, hourly) and shows the cron expression and the next runs; anything else
is entered as a five-field cron expression with a time zone. Tasks run at most once an hour,
ten per person.

Each task is a calendar schedule named `eva_task_<id>` in Evlet's jobs system, running the
`eva_task` job as its owner. App releases leave these schedules alone, and admins can pause
them in Controls → Jobs. An administrator's pause wins: the owner can't resume the task, run it
now or re-enable it by changing its timing until it is resumed in Controls, and jobs already
queued for it don't run. Every run checks again that Eva is on, the owner is active with Allow
Eva, and the allowance has room; it then runs with the owner's permissions on that day. A task
is read only unless the person allows changes, and changes that need approval are always
skipped because nobody is there to approve them. A run that finds the owner without Eva
access pauses the task and says why. Each run writes a chat, linked from the task's history.
Scheduled tasks need the jobs worker.

## Tools

Eva uses the Tool Engine's tools ([tools.md](tools.md)) plus its own `create_file`, as the
person, with the origin `eva` (or `schedule` for scheduled tasks) in the Tool log. Approvals
follow Eva's confirmation settings above.

## Eva's routes

All under `/api/_eva`, signed-in desk sessions only, 404 when Eva is not available to the
person.

| Route | Purpose |
| --- | --- |
| `GET /api/_eva` | Models, allowance, whether tasks run, pending approvals, file limits |
| `GET/POST /api/_eva/conversations` | List (`q`, `archived=1`, `before`), create (`title`, `context`) |
| `GET/PATCH/DELETE /api/_eva/conversations/<id>` | Messages, runs with their activity rows, files and usage; rename or archive; delete |
| `POST /api/_eva/conversations/<id>/messages` | `{"text", "files", "model"}`: starts a run (202) |
| `GET /api/_eva/runs/<id>/stream` | Server-sent events, about 25 seconds per connection; resume with `Last-Event-ID` |
| `GET /api/_eva/runs/<id>/events?after=` | The same events for polling |
| `POST /api/_eva/runs/<id>/cancel` | Stop at the next step. Finished steps stay done |
| `POST /api/_eva/files`, `GET /api/_eva/files/<id>` | Upload (`name`, base64 `content`), download |
| `GET /api/_eva/library?q=`, `POST /api/_eva/files/import` | Files on readable records and earlier chats; copy one in |
| `GET/POST /api/_eva/tasks`, `GET/PATCH/DELETE /api/_eva/tasks/<id>` | Scheduled tasks |
| `POST /api/_eva/tasks/parse`, `POST /api/_eva/tasks/<id>/run` | Read a schedule from words; run now |
| `GET /api/_controls/eva`, `GET /api/_controls/eva/usage?period=` | Controls: configuration and usage |

Events: `run.started`, `message.delta`, `tool.started`, `tool.completed`, `tool.failed`,
`approval.requested`, `approval.answered`, `usage.updated`, `run.completed`, `run.failed`,
`run.cancelled`. Runs are kept with their status (`running`, `awaiting_approval`,
`completed`, `failed`, `cancelled`). A run that no process is working on (not running here and
silent for five minutes) is marked failed at start and whenever its chat is used again, so a
restart never leaves a chat blocked. Stopping a run whose process is gone closes it at once.

## Limits of this release

- One provider adapter (OpenAI Chat Completions). The interface takes any object with
  `stream(request)`.
- Older messages beyond about 120,000 characters are left out of the model's context; they
  stay stored. There is no summarising of older context yet.
- Runs execute on a thread in the process that received the message; scheduled runs execute
  in the jobs worker.
- Eva files are kept in the database, 1 MB each.
- Conversations are kept until their owner deletes them. There is no retention setting yet.
