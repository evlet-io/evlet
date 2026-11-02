# Handoff plan: model amendments and the Evlet UI framework

Status: in progress. This plan adapts [handoff-model.md](handoff-model.md) and
[ui-design/README.md](ui-design/README.md) to the engine as it stands on `main`
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
| Posted records | none | Frozen calculated values; corrections only by void or revise; Recompute skips locked and posted records |

## Phases and branches

Each phase is a branch stacked on the previous one and a draft PR against it.

| # | Branch | Scope |
|---|---|---|
| 1 | `model/01-expressions` | Auto-rounding, dates (`today()`, `days()`, date arithmetic), Link hops (up to three), `when(cond, a, b)`, TypeScript mirror and fixtures |
| 2 | `model/02-rules` | `initial`, `applies_when`, `required_when`, `locked_when`, `Recompute` migration, new types (Phone, Percent, Time, Duration, Tags, Ref), schema metadata |
| 3 | `model/03-lines` | `Table` row limits, row-change protocol, paging endpoint, batched child loading, `ChildTable` kept working |
| 4 | `model/04-state` | `State`, `Move`, locked and final states, move endpoint, `editable_when_locked`, action inputs and `available_when` |
| 5 | `model/05-void-revise` | `void`, `revise`, operations in `Rule` |
| 6 | `model/06-effects` | `Effect`, `Swap`, `Negate`, balance check, read-only effect rows, check command |
| 7 | `model/07-sequences` | `Sequence` field, counter table, period reset, scopes |
| 8 | `model/08-journal` | Journal table, engine and user entries, permissions, hash chain, verify command |
| 9 | `model/09-measures` | Aggregate API, `Meta.measures`, HTTP endpoints |
| 10 | `model/10-meaning` | `key_fields`, `relations`, `Meta.attachments`, `view_for` keys |
| 11 | `ui/01-core` | Reactive core, DOM and templates, router, API client, model runtime over the finite engine |
| 12 | `ui/02-components` | Component library in the README build order, Library page |
| 13 | `ui/03-screens` | Shell, login, Docs, lists and boards, doc view, child table, command palette, actions, journal |
| 14 | `ui/04-testcrm` | `examples/testcrm/models.py`, launcher, browser end-to-end tests, performance budgets |
| 15 | `ui/05-remove-solid` | Remove SolidJS, the Vue harness, the Projects demo and the page renderer; docs |

## Phase 15 scope (`ui/05-remove-solid`)

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

Open for the owner: the browser has no Accounts screen, so adding users needs the HTTP
routes until one is built on the framework.

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
