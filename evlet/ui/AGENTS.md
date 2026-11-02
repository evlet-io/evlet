# Python UI contract work

This package owns UI definitions, expressions, compiler validation, component
contracts, view registration, model screen metadata and browser route serving.
Keep these definitions renderer-neutral and preserve public imports.

`routes.py` handles `/api/ui`, `/api/ui/resolve` and browser route fallbacks through
one dispatch hook in `Application`. Authentication and permission decisions remain
with the existing auth and business services. `metadata.py` owns `Model.Meta`
translation; `evlet.metadata` remains a compatibility re-export.

For UI-only work, leave database storage, schema migrations, authentication policy,
jobs and CLI lifecycle behavior unchanged. A task that explicitly spans both sides
may change them with the relevant backend tests. Never bypass record, field,
action or tenant checks to make a screen work.

Agree the JSON shape and shared fixture before adding a feature across Python and
the browser. Page and record fixtures live in `tests/fixtures` (generator
`scripts/generate_ui_fixtures.py`); the UI framework's recorded responses live in
`frontend/src/evlet/tests` (generator `scripts/generate_ui_framework_fixtures.py`). Python checks real authenticated HTTP output,
and browser tests use the same payloads. Review fixture diffs before accepting a
contract change, and keep older public imports working.

Run `.venv/bin/python -m pytest tests/test_ui.py tests/test_ui_contracts.py` from
the repo root. Run the full Python and frontend suites for shared contract changes.
See `docs/ui.md` for the public API and ownership boundary.
