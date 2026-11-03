# Eva and the Tool Engine: phase plan

Status: delivered as stacked PRs. PR 1, the Tool Engine and MCP on it (phases 1 and 2); PR 2,
Eva's runtime with usage, allowances and scheduled tasks (phases 3, 5 and 6, one module);
PR 3, Eva's screens (phase 4). The owner approved the mockup and all twelve recommendations on
02-11-2026. Public documentation: [docs/tools.md](../tools.md) and, with PR 2,
[docs/eva.md](../eva.md).

## Owner decisions (02-11-2026)

1. Keep the current snake_case tool names; new tools follow them.
2. On upgrade, Allow MCP stays on for people with a connected assistant; everyone else, and
   every new person, starts without it.
3. Controls gets an AI group: Eva, MCP, Usage, Tool log.
4. Admins see owner, counts and cost per conversation, never titles or messages.
5. Rates are entered in AED per million tokens; each usage row keeps its rate.
6. The OpenAI adapter uses `urllib`; no new dependency.
7. Reports: the existing report runtime (`list_reports`, `run_report`) and the measures API
   (`aggregate_records`).
8. Outside assistants get a pending approval for finalising, actions and deletes; the person
   approves in Evlet and the client calls again.
9. Scheduled tasks read only by default; changes that need approval are skipped.
10. Eva from a record opens a side panel, kept as a normal chat.
11. Ten tasks per person, at most hourly; admins pause them in Controls → Jobs.
12. Chats are kept until deleted; the Tool log keeps 90 days.

## What we built on

| Spec part | Before | Now |
| --- | --- | --- |
| Tool registry | Nine tools inside `evlet/mcp.py` | `evlet/tools.py`: one `Tool` per tool, eighteen shared tools plus Eva's `create_file` |
| Executor and permissions | MCP calls through `Business.dispatch` as the narrowed person | The same path for every origin, with an origin on `KeyAccess` |
| Remote MCP | OAuth, `/_/mcp`, per-person clients | Unchanged sign-in; tools through `/api/_tools`; Allow MCP; approvals |
| Settings | Controls "MCP & AI" | AI group: Eva, MCP, Usage, Tool log; `eva.*` settings fail closed |
| Execution log | `evlet_mcp_calls` | `evlet_tool_calls` for every origin; old rows moved over |
| Credentials | Connectors and secrets | An OpenAI connector registered when AI is on |
| Scheduler | `jobs.schedule(..., calendar=...)` | One calendar schedule per Eva task, `eva_task` external job |
| Streaming | `/api/_events/stream` | `/api/_eva/runs/<id>/stream` on the same pattern, from stored events |

## Phases

0. **Mockup and plan.** Done; approved 02-11-2026.
1. **Tool Engine.** Done. Registry, argument checks, approvals bound to call and revision,
   `/api/_tools`, the Tool log, opted-in services (`@api(..., tool=True)`).
2. **MCP on the engine.** Done. Stdio and remote MCP call `/api/_tools`; Allow MCP; approvals
   in Settings → AI; the Controls page split.
3. **Eva runtime.** Done. `AI(...)` gate, Controls switch, Allow Eva, OpenAI adapter, agent
   loop with per-answer limits and reauthorization before every step, conversations, runs,
   SSE with replay.
4. **Eva screens.** Done. `/eva` chat, activity rows, approval cards, attachments and the
   Evlet file picker, generated files, Ask Eva panel, Settings → AI, Controls pages, Allow
   switches in Users.
5. **Usage and allowances.** Done. Usage rows with rate snapshots, person and instance
   allowances held atomically, task reserve, Controls → Usage.
6. **Scheduled tasks.** Done. Plain-words schedules, cron and time zone, pause, run now, run
   history with result chats, revalidation at every run.
7. **Hardening.** Partly done in the tests of each phase (permission refusal, revocation
   mid-run, approvals once only, concurrent allowance use, other people's chats, stream
   resume). Still to do: a permission matrix across all three origins, prompt-injection
   fixtures, provider failure and timeout tests against a local fake server, PostgreSQL runs
   of the new tables in CI, and a performance check of the Tool log under load.

## Left for later

- Summarising older conversation context (older messages are left out today, not summarised).
- Eva files in object storage; they are kept in the database, 1 MB each.
- A retention setting for chats.
- More providers behind the same `stream(request)` interface.
- On phones the chat list is hidden; a list toggle in the thread header would help.
