# Continue Evlet locally

Use the existing checkout of `evlet-io/evlet`. Read `AGENTS.md`, `docs/ui.md`,
`docs/cli.md` and `docs/architecture.md` first. Preserve the stash named
`Preserve local foundation work before Milestone 1 continuation`. Do not create a
worktree, merge or deploy without a request.

## Current scope

The native frontend now uses SolidJS and the approved custom component library.
Sign-in opens Docs. Demo Pages contains the original Projects Explorer View and a
Python-authored layouts/state example. Models contains the real generated CRUD
screens; Accounts is available to administrators.

Keep the approved light sidebar, blue active state, fixed top bar and sidebar,
independent list and preview scrolling, and expanded summary cards. Do not bring
back automatic header/card collapse. Each shell and view area supports its own
content and element properties; the public terminology is in `docs/ui.md`.

Python UI definitions live in `evlet/ui`. Components, layouts and views compile to
immutable versioned schemas. The registry validates contracts, routes, component
IDs, extensions and declarative expressions. The frontend resolves registered
renderers, bindings and actions without executable source in schemas. The same
contracts drive the component explorer and examples in Docs.

Generated lists use server queries, URL state, categories, saved local filters,
column preferences, row selection and a record preview. Forms preserve model
layout groups, defaults, exact decimal strings, permissions, children, actions,
private attachments, background jobs and unsaved input. Conditional fields and
extensions share the ordinary form controller. Optional If-Match revisions reject
stale record mutations with 409; existing clients without the header still work.

The Python model/database engines and existing endpoints remain in place. Backend
Milestones 1 through 8 and the review fixes at `7371ed5` are preserved. Upstream
main includes the review fixes in PR #9 at `60a97fa`. Read the existing model,
business, tenancy, jobs, connector, PostgreSQL and operations guides when changing
those areas. SQLite remains the default; PostgreSQL is optional.

## Preserved state

The user's working database is `examples/playground/.data/playground.sqlite3`.
Do not replace it with test data or reset its accounts. Existing migration backups,
attachments and the preserved stash remain in place. This UI upgrade needs no data
migration. Do not assume historical record counts describe current user data.

Browser and HTTP verification used a separate disposable database. Projects under
Demo Pages stores sample records in browser storage. Projects under Models uses
the authenticated database. Keep that distinction visible in Docs.

## Run and validate

```sh
bash scripts/playground.sh --setup
.venv/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python scripts/smoke_tenants.py
.venv/bin/python -m build --no-isolation
```

On later starts omit `--setup`. The local browser address is
http://127.0.0.1:5173. Python source reload is enabled by default; set
`EVLET_RELOAD=0` to disable it. The launcher refuses occupied ports and only stops
its own processes.

The production frontend build also installs assets in `evlet/ui/static`. Build it
before building a wheel or source archive. An installed wheel serves the native UI
at http://127.0.0.1:8000 without Node or Vite. `evlet new` creates an ordinary Python
application with instructions for the bundled UI. The source tree ignores generated
assets; CI builds them before packaging.

Use `scripts/check_package.py` with a clean wheel-installed interpreter and `-I`.
Use `scripts/smoke.py --setup` only against a new disposable database. Optional
PostgreSQL tests require `EVLET_TEST_POSTGRES_DSN` and a disposable cluster with
CREATE/DROP DATABASE permission. They skip when that setting is absent; CI covers
PostgreSQL 14 and 18.

## Validation and boundaries

Local checks cover the backend suite, frontend tests (including all 80 legacy
regressions), TypeScript, the production build, tenant isolation, real HTTP CRUD,
package build and clean wheel installation. Browser checks cover login to Docs,
Projects, Python reactive fields/dialogs, persisted customer creation, model form
sections and saved dropdown values, fixed scroll areas, mobile navigation, direct
URLs, refresh and back navigation. Final counts and CI status belong in the PR.

Date and time controls are custom editable pickers shared by Python pages, model
forms and the Projects demo. Short dates use DDMMYY and times accept entries such
as 6.30pm. Component list and detail panes scroll independently. See `docs/ui.md`
for accepted formats and keyboard behavior. Searchable custom selects replace
native dropdowns. Field prefixes are configurable, with AED set explicitly in the
Playground. Checkboxes keep compact inline labels, and list selection actions use
the existing toolbar without pushing the table down. Confirmations, validation
and action errors use Evlet dialogs. Only the tab close/reload unsaved-change
warning remains browser-owned, as required by browser restrictions.

Vue and PrimeVue remain only in `frontend/legacy` and development dependencies for
regression comparison. The active production bundle uses Solid and the custom
components. Projects and Accounts load separately. The backend adds no mandatory
runtime dependency.

This is the working UI foundation, not every item in the longer architecture
handoff. The current component catalog and extension boundaries are documented in
`docs/ui.md`. Advanced tree/calendar controls, remote preference sync, SSO and a
visual editor remain outside this change. Do not describe this as production
ready. No cloud deployment, public DNS/TLS, live object storage or release activation
was performed.

## CLI and package lifecycle

The cli-app-instances branch builds on solid-ui-playground without changing its
UI or business engines. Read docs/cli.md for project/app/instance commands,
AppDefinition and Instance APIs, staged installs and recovery. Existing working
Playground data remains untouched. The build bundles the maintained examples
and documentation alongside the existing compiled UI. tests/test_cli_lifecycle.py
uses only temporary projects and databases. Do not migrate the working Playground
into this new directory structure implicitly.
