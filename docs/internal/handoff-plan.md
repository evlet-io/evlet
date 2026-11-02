# Handoff plan: model amendments and the Evlet UI framework

Status: every phase is done (PRs #35 to #50, then the cross-phase fixes), and PRs 1 to 3
of the second handoff sit on top (below); each waits for the owner to merge. This plan adapts [handoff-model.md](handoff-model.md) and
[ui-design/README.md](../ui-design/README.md) to the engine as it stands on `main`
(tag `rollback/pre-native-ui`). Where this plan and the handoff differ, this plan
records the owner's decision and wins.

## Owner decisions (01-11-2026)

1. **One expression language.** Extend the existing finite-v1 language
   (`qty * rate`, `parent.x`, `F("x")`, `lines.sum("net")`, `round()`) instead of
   adding the handoff's `record.x` syntax. Every handoff feature is expressed in it.
   `evlet/ui/expressions.py` stays only for Python UI pages.
2. **Auto-rounding.** A calculated result is quantized to its target field's scale
   with that field's `rounding`: `"half_up"` by default for Currency, `"half_even"`
   by default for Decimal. Explicit `round(x, n)` still rounds half up. This replaces
   `COMPUTE_SCALE_ERROR` for scale mismatches. Range errors stay errors.
3. **New UI drops** the Projects demo, the Python page renderer (`evlet.ui` pages)
   and native TS/JS panels. Their Python APIs remain and are documented as not
   rendered by the new UI. The new UI keeps login, Docs, the shell and generated
   model screens.
4. **One PR per handoff phase**, stacked, each a draft until the owner merges.
   Nothing merges to `main` automatically.
5. **Attribution.** Commits are authored as the owner. No co-author trailers, and no
   mention of tools or assistants in commits, PRs, comments or docs.
6. **Model-only apps.** Core apps (CRM, HRM) should need only a `models.py`. The
   test app is `examples/testcrm/`, the CRM sample from the mockup.

## How the handoff maps onto today's engine

| Handoff | Today | Work in this plan |
|---|---|---|
| Expressions, Python evaluator | finite-v1 compiler and evaluator in Python and TypeScript, shared fixtures | Add dates, Link hops, `when`, auto-rounding |
| `compute=` | finite `compute=` with `stored=` (default stored) | Keep `stored=`; no `store=` alias |
| `initial=` | `fetch="link.field"`, `default=Ref(...)` | Add `initial=` expression; `fetch` and `Ref` stay |
| `applies_when` / `required_when` / `locked_when` | legacy strings `visible_if` / `required_if` / `read_only_if` | Add typed rules; legacy strings stay for legacy roots |
| `Lines(...)` | `Table(Child)` with nesting, `evlet_position`, the `children` envelope | Keep `Table`; add `min_rows`, `max_rows` (to 1,000), row changes, paging, batched loading |
| `State`, `Move`, model `@action` | `Select`, `@action(label, roles)` | Add `State`, `Move`, action `inputs` and `available_when` |
| `Ref()` field (Phases 2, 5, 6: `revises = Ref()`, `source = Ref()`) | `Ref("parent.x")` already means a path | The field type is `Reference()`; `Ref(path)` keeps its meaning |
| `locked_when` | none | Decided on the record as saved before the write, so one write cannot unlock and change a field |
| State locks | none | Block ordinary writes (fields, rows, attachments, deletes). `@action` and `Move(run=)` may change a locked record but obey `locked_when`. Ordinary writes to `editable_when_locked` fields keep the saved calculated values; no stored result may read such a field |
| Void and revise | none | Voiding only through `void()` with a reason; a declared `Move` into `void` is rejected. Admin always keeps void and revise; Rules add roles. `revises` is an indexed `Link` to the same model, not `Ref()` |
| Savepoints | none | Each document operation runs in a savepoint, so a failure caught by trusted code leaves no partial writes |
| `ChildTable` on finite models | legacy child rows skip finite rules | A `ChildTable` whose child uses finite features is rejected; use `Table(Child)` |
| Void, revise, Effects, Sequence, Journal, Measures, meaning metadata | none | New |
| Posted records | none | Frozen calculated values; actions and moves may not change what they read (409); corrections by void or revise, or an admin's Unpost and Repost; Recompute never recalculates posted records unless an admin asks (`evlet migrate --recompute-posted`); previews show kept values |

## Phases and branches

Each phase is a branch stacked on the previous one and a draft PR against it.

| # | Branch | Scope | Status |
|---|---|---|---|
| 1 | `model/01-expressions` | Auto-rounding, dates (`today()`, `days()`, date arithmetic), Link hops (up to three), `when(cond, a, b)`, TypeScript mirror and fixtures | Done, #35 |
| 2 | `model/02-rules` | `initial`, `applies_when`, `required_when`, `locked_when`, `Recompute` migration, new types (Phone, Percent, Time, Duration, Tags, Ref), schema metadata | Done, #36 |
| 3 | `model/03-lines` | `Table` row limits, row-change protocol, paging endpoint, batched child loading, `ChildTable` kept working | Done, #37 |
| 4 | `model/04-state` | `State`, `Move`, locked and final states, move endpoint, `editable_when_locked`, action inputs and `available_when` | Done, #38 |
| 5 | `model/05-void-revise` | `void`, `revise`, operations in `Rule` | Done, #39 |
| 6 | `model/06-effects` | `Effect`, `Swap`, `Negate`, balance check, read-only effect rows, check command | Done, #43 |
| 7 | `model/07-sequences` | `Sequence` field, counter table, period reset, scopes | Done, #40 |
| 8 | `model/08-journal` | Journal table, engine and user entries, permissions, hash chain, verify command | Done, #44 |
| 9 | `model/09-measures` | Aggregate API, `Meta.measures`, HTTP endpoints | Done, #41 |
| 10 | `model/10-meaning` | `key_fields`, `relations`, `Meta.attachments`, `view_for` keys | Done, #42 |
| 11 | `ui-final/01-core` | Reactive core, DOM and templates, router, API client, model runtime over the finite engine | Done, #45 |
| 12 | `ui-final/02-components` | Component library in the README build order, Library page | Done, #46 |
| 13 | `ui-final/03-screens` | Shell, login, Docs, lists and boards, doc view, child table, command palette, actions, journal | Done, #47 |
| 14 | `ui-final/04-testcrm` | `examples/testcrm/models.py`, launcher, browser end-to-end tests, performance budgets | Done, #48 |
| 15 | `ui-final/05-remove-solid` | Remove SolidJS, the Vue harness, the Projects demo and the page renderer; docs | Done, #49 |
| 16 | `ui-final/06-testcrm-findings` | Fixes from the testCRM findings review | Done, #50 |
| 17 | `stack/07-cross-phase-fixes` | Fixes from the cross-phase review: posted records keep their totals, docs brought in line | Done on this branch |

## Phase 15 scope (`ui-final/05-remove-solid`)

Done on this branch:

- `frontend/index.html` loads the Evlet UI framework app; `npm run build` bundles it and
  `scripts/install-ui.mjs` installs it in `evlet/ui/static`. `evlet.html` (Library) stays a
  development page.
- The app's routes follow the server's: `/app/<model>`, `/app/<model>/new`,
  `/app/<model>/<id>` and `/app/<model>/<id>/view` (opens the record). The development-only
  `/ui/...` and `/m/<model>` paths are gone. `/settings/accounts` and `/reports/<name>` show
  a notice; other unmatched addresses (Python page routes) open Docs.
- Removed: the SolidJS app, components and views, the Projects demo, `ViewRenderer` and the
  runtime files only Solid used, `frontend/legacy` and its tests, and the packages solid-js,
  vite-plugin-solid, @solidjs/testing-library, vue, @vitejs/plugin-vue, @vue/test-utils,
  primevue, @primeuix/themes, @testing-library/jest-dom, prettier and @types/node.
- The finite engine moved to `frontend/src/evlet/model/finite.ts`. Fixtures only Python reads
  moved to `tests/fixtures`. The framework's recorded fixtures are now compared with fresh
  ASGI responses in `tests/test_ui_contracts.py`.
- Kept and documented as not rendered: the `evlet.ui` page API with `/api/ui` and
  `/api/ui/resolve`, native panels, the Playground's Python pages, reports and form dialogs,
  and account management (the `/api/auth/users` routes).

Users are now managed in Controls (`/controls/users`, second handoff PR 3);
`/settings/accounts` redirects there.

## Second handoff: frontends and routing, Controls

[handoff-routing-controls-settings.md](handoff-routing-controls-settings.md) lists nine PRs.
PRs 1 to 3 were built on `ui-final/05-remove-solid` and then replayed, commit by commit, on
`stack/07-cross-phase-fixes`, so each branch below stacks on the one before it. All nine are
open as draft PRs #52 to #60, and a tenth PR closes the findings of a review across all of them.

| PR | # | Branch | Scope | Docs |
|---|---|---|---|---|
| 1 | #52 | `h2/01-route-table` | `Frontend`, `Host`, `routes.py`, reserved paths, `resolve_routes`, `evlet routes`; dispatch through the table with no behavior change | [routing.md](../routing.md) |
| 2 | #53 | `h2/02-hosts-realms` | Sub-hosts, sign-in realms and cookies, API scopes, `static` and `server` frontends, user links and `own_customer`, the page config block, `evlet proxy`, `evlet link-user`, the Playground customer portal | [routing.md](../routing.md) |
| 3 | #54 | `h2/03-controls` | Roles, overrides, audit log and config version; Controls access, Users, Roles, Permissions and Security pages; `evlet controls grant`; `/settings/accounts` redirect | [controls.md](../controls.md) |
| 4 | #55 | `h2/04-models` | Field overrides, instance fields and naming series | [controls.md](../controls.md#models) |
| 5 | #56 | `h2/05-pages` | Apps, Routing, Jobs, Overrides with export as code, and Audit | [controls.md](../controls.md) |
| 6 | #57 | `h2/06-settings` | Account menu, personal settings, two-step sign-in and app settings | [settings.md](../settings.md) |
| 7 | #58 | `h2/07-connectors` | Connectors, encrypted secrets, email accounts and API keys | [controls.md](../controls.md#connectors-and-secrets) |
| 8 | #59 | `h2/08-remote-mcp` | Remote MCP for AI assistants with OAuth | [connector.md](../connector.md#remote-mcp-with-oauth) |
| 9 | #60 | `h2/09-mailboxes` | Personal mailboxes | [controls.md](../controls.md#personal-mailboxes) |
| 10 | #61 | `h2/10-cross-review-fixes` | One revocation function, export grouped by setting prefix, instance fields grantable, every PostgreSQL variant in CI, the mailbox page with the switch off, a dropped IMAP connection during sign-in kept apart from a rejected password | this section |
| 11 | #62 | `h2/11-unpost-repost` | Unpost and Repost of posted documents, `--recompute-posted` for Recompute, `unposted` and `reposted` journal entries and audit rows, record screen actions | [states-actions.md](../states-actions.md#unpost-and-repost) |
| 12 | #63 | `h2/12-security-review` | The owner's security decisions of 02-11-2026: untrusted proxy warning, 12-hour sessions and 5 failed sign-ins, when the two-step code is asked, code-only sensitive settings, the choice to revoke API keys, instance fields for every role | this section |

Decisions taken while building them (the owner's where marked):

- **Portal links.** Portal users link to records through the framework table
  `evlet_user_links` (section 7, decision 3).
- **Docs.** Docs are on in development and off when a primary host or `Instance.domain` is
  set, so off in production, unless `Docs(enabled=...)` says otherwise (section 7, decision 4).
- **Trusted proxies.** `X-Forwarded-For` counts only from the instance's `trusted_proxies`;
  sign-in throttling, Controls networks and the public limits read the same address.
  `trusted_proxies` stays an explicit `Instance(...)` setting, empty by default, and a header is
  never trusted on its own. A peer on this machine or a private network that keeps sending
  forwarded headers (20 within 10 minutes) without being listed is shown under Controls' Needs
  attention, each peer on its own line, to add only if it is the reverse proxy. It is noted
  cheaply: counted in memory and written at most every 10 minutes per peer, 50 peers kept.
  `evlet proxy` prints the line for the local proxy (owner's decision, 02-11-2026).
- **Security defaults** (owner's decision, 02-11-2026). Sessions last 12 hours and sign-in locks
  after 5 failed attempts. `security = Security(session_hours=..., lockout=..., password_min=...)`
  in config.py sets other starting values (1 to 168 hours, 3 to 50 attempts, 15 to 64 characters),
  as the owner asked; Controls can only make them stricter.
- **Opens Controls is not admin.** A role may open Controls without being admin. Only admins
  change admin accounts, give the admin role, manage roles and decide who opens Controls.
- **Overrides fail closed.** A stored override that no longer validates becomes no access, the
  strictest setting, or (when the policy can't be built) admin only, and is listed under Needs attention.
- **Two-step sign-in.** It is recorded per session. Required roles set it up at their next
  sign-in, trusting the first use, so admins should sign in right after an upgrade. Admins and
  `evlet users reset-two-step` reset it. A required role can't use the app until it is set up:
  a browser session that did not finish two-step sign-in gets 401 on every route (API keys
  excepted). The code is asked only at sign-in, before changes in Controls (a fresh code on the
  session counts for 10 minutes; reading Controls never asks, and an admin resetting someone's
  password is a Controls change) and before changing one's own password (owner's decision,
  02-11-2026).
- **Sensitive personal settings ask for the code only** (owner's decision, 02-11-2026). With
  two-step sign-in on, turning it off, new backup codes and an email change ask a current code
  and not the password; without it they ask the password. Changing one's own password still
  needs the current password, plus the code when two-step is on.
- **cryptography is a core dependency** (owner's decision), chosen over an optional extra so
  encrypted secrets and two-step sign-in work on every install.
- **Jobs keep their narrowing.** A job queued by an AI assistant or an API key runs with the
  same narrowing, rebuilt from current settings, and fails plainly when the assistant was
  disconnected or the key revoked.
- **No migration from the browser.** Controls shows an app waiting to be migrated; only
  `evlet migrate` changes the database, never a request.
- **Mail matching is opt-in.** A model takes part in mailbox matching only through `Meta.mail`.
- **One revocation matrix.** Every revocation goes through
  `LocalAuth.end_sessions`. A Settings password change and Controls "Sign out everywhere" end
  browser sessions (the current one stays after a password change), AI assistants and unused
  authorization codes. They ask about API keys with "Also revoke API keys (N active)", unticked
  by default, and send an explicit `revoke_keys` boolean (owner's decision, 02-11-2026). A
  two-step reset (Controls or the shell), deactivation and the break-glass grant always revoke
  API keys ([controls.md](../controls.md#ending-access)).
- **Posted records (owner's decision).** Posted records are never recalculated by
  `Recompute` unless an admin asks, for one migration run, with `evlet migrate <instance>
  --recompute-posted` (or `migrate(..., recompute_posted=True)`). Each posted record that
  changes gets a `recomputed` journal entry with `"posted": true`, and the run writes one audit
  row. Browser requests never migrate, so Controls has no switch for it.
- **Unpost and Repost (owner's decision).** Unpost takes a posted document back to the state it
  posted from, with a required reason, keeping its id and number (no number is taken or given
  back). Everything it posted is cancelled with reversal rows, never deleted. It is refused
  while a live record (not void, and locked or posted) links to it, directly, through its
  Table rows or through effect rows, and the refusal names those records. Repost runs the
  posting move again through the normal move path, which re-validates and posts fresh rows.
  Both write `unposted` / `reposted` journal entries and a Controls audit row (area
  `Records`), through `corrections.unposted` and `corrections.reposted`.
- **A record that ever posted is never deleted.** Its effect rows, reversed or not, name it as
  their source and its number stays used, so delete answers 409 and details carry
  `_deletable: false`. Void records are skipped by Recompute even with `--recompute-posted`.
- **Unpost and repost are Rule operations.** Admin keeps them while its Rule reads and updates
  the model, like void; a Rule naming `unpost` or `repost` adds a role, and Controls keeps or
  takes away that grant. Every HTTP surface (sessions, API keys, assistants, `ApiScope`) and
  `Context` check them. The connector and MCP server do not offer them, as for void and revise.
- **What "posted" means.** A record is posted when it has live effect rows: the latest row of
  some effect is an original, not a reversal. Unposting makes it a draft for this purpose, so
  edits recalculate and `Recompute` includes it. The state it returns to is the start of its
  posting move; when moves from several states post, the latest `moved` journal entry decides.
- **Instance fields for every staff role** (owner's decision, 02-11-2026). An instance field
  follows the record for desk roles: every one that reads the model reads it and every one that
  creates or updates it writes it, also when its app rule lists `read_fields` or `write_fields`.
  "All users" means signed-in staff: portal roles (customers) get one only through an override. An admin can hide it
  or make it read only per role with a permission override; a field added later also joins the
  field lists of existing overrides. Stricter only still applies to fields declared in app files.

Fitting them onto the first program:

- testCRM needs no `routes.py`: with no routes file the instance gets the default table, so
  `scripts/testcrm.sh` and its HTTP tests run unchanged.
- The `journal` operation from Phase 8 is a Rule operation like `void` and `revise`: a public
  or custom role may name it, Controls keeps it on save and can take it away, and a custom
  role based on the editor posts like the editor.
- The PostgreSQL CI job runs every test file that uses the `pg_dsn` fixture, so the variants
  added by PRs 4 to 9 run there too ([postgresql.md](../postgresql.md#verification)).

## Quality gates for every phase

1. **Implementation** with tests at public boundaries: real SQLite, authenticated
   ASGI requests, shared Python and TypeScript fixtures for anything both runtimes
   evaluate.
2. **Independent test pass**: a separate reviewer writes adversarial tests from the
   docs only, without reading the implementation first.
3. **Independent code review**: correctness, permissions (account, tenant, row, field,
   action), transactions, stored data and migrations. Confirmed findings are fixed
   with a test that fails first.
4. **Full suite**: `.venv/bin/python -m pytest`, `npm test`, `npm run build`, then CI
   (including PostgreSQL).
5. **UI phases also**: component tests in jsdom, end-to-end flows in a real browser
   against testCRM, performance budgets (below), and a visual check against
   `docs/ui-design/reference/mockup.html` in light and dark at desktop and 390px.

## Performance budgets (UI)

- A 1,000-row child table renders its first screen in under 100 ms and edits a cell,
  recalculating totals, in under 16 ms.
- A list page of 100 rows renders in under 50 ms; scrolling stays at 60 fps.
- One field edit updates only the DOM nodes bound to the cells it changes.
- Loading a document with 1,000 rows uses a bounded number of SQL statements per
  table level, not one per row.

## Rollback

- `main` is untouched until the owner merges. The tag `rollback/pre-native-ui`
  marks the starting point.
- SolidJS is removed only in the last phase, in its own PR, so it can be reverted on
  its own.
