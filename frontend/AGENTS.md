# Browser UI work

This area owns Solid components, generated model screens, browser navigation,
state, API and event clients, styles and browser tests. `legacy/` is only a Vue
regression harness; it shares the active API, event and schema helpers.

Keep the approved Projects demo, login, Docs landing page and generated screens
working. UI changes must continue to enforce backend permissions and consume
Python JSON contracts rather than importing business rules into the browser.

For UI-only work, leave database adapters, migrations, authentication, jobs,
storage, the CLI and application business code unchanged. A task that explicitly
spans both sides may change them with the relevant backend tests.

Before implementing a feature that needs new backend data, define and review its
JSON shape and shared fixture. Implement the backend response and browser consumer
against that same contract. Do not infer a new API from a browser mock.

`src/tests/ui-fixture.json` contains the Playground page and model contracts.
`src/tests/record-fixture.json` contains list, detail-with-children and event
responses. Python compares them against actual authenticated ASGI responses;
browser tests render or consume them. Generate intentional updates from the repo
root with `.venv/bin/python -m scripts.generate_ui_fixtures`, then inspect the diff.
A failing fixture is evidence of a contract change, not a reason to regenerate it
without review.

Run `npm test` and `npm run build` here. For shared contract changes, also run
`.venv/bin/python -m pytest tests/test_ui_contracts.py tests/test_ui.py` from the
repo root. The production build uses Solid; the Vue plugin belongs only to tests.
