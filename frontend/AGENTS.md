# Browser UI work

This area is the browser interface, built on Evlet's own UI framework in
`src/evlet/`: the reactive core, DOM templates, router, API client, model runtime,
the finite engine (`model/finite.ts`), the component library (`ui/`) and the
generated screens (`app/`). See `docs/ui-framework.md` and `docs/ui.md`. There is no
SolidJS, Vue or legacy harness, and the framework has no runtime dependencies. Do
not add one without a written justification.

`index.html` is the production entry. `evlet.html` (the component Library) is a
development page and is not built. The app's routes must match the paths
`evlet/ui/routes.py` serves.

Keep login, the Docs landing page and generated screens working. Reports render at
`/reports` (`app/report-page.ts`, owner decision of 03-11-2026; see `docs/reports.md`).
Python pages, native panels and account management are not rendered here; do not
reintroduce a renderer for them without an owner decision. UI changes must continue
to enforce backend permissions and consume Python JSON contracts rather than
importing business rules into the browser.

For UI-only work, leave database adapters, migrations, authentication, jobs,
storage, the CLI and application business code unchanged. A task that explicitly
spans both sides may change them with the relevant backend tests.

Before implementing a feature that needs new backend data, define and review its
JSON shape and shared fixture. Implement the backend response and browser consumer
against that same contract. Do not infer a new API from a browser mock.

`src/evlet/tests/api-fixture.json` and `screens-fixture.json` hold responses
recorded from the real ASGI routes; the tests replay them and
`tests/test_ui_contracts.py` checks them against fresh responses. Regenerate
intentional updates from the repo root with
`.venv/bin/python -m scripts.generate_ui_framework_fixtures`, then inspect the diff.
`src/evlet/tests/finite-fixtures.json` comes from `scripts/generate_finite_fixtures.py`.
A failing fixture is evidence of a contract change, not a reason to regenerate it
without review.

Run `npm test` and `npm run build` here. For shared contract changes, also run
`.venv/bin/python -m pytest tests/test_ui_contracts.py tests/test_ui.py` from the
repo root.
