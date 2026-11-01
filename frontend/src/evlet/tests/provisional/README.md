# Provisional fixtures

`journal-fixture.json` is written by hand. The engine under the screens does not have the
journal yet (Phase 8), so its responses cannot be recorded. No doc exists for it yet; the shape
follows the outline in `docs/handoff-model.md` (`kind`, `actor`, `at`, `data`):

| Keys | Phase and source |
|---|---|
| `invoice_journal` (`GET /api/{model}/{id}/journal`) and `journal: true` on the schema's invoice model, added by `screens-helpers.ts` | Phase 8, `docs/handoff-model.md` |

Every other response the screen tests replay is recorded from the real routes by
`scripts/generate_ui_framework_fixtures.py` (`api-fixture.json`, `screens-fixture.json`).

Once Phase 8 is stacked under the screens, record the journal from the real route in that
script, drop the schema overlay and delete this folder. A test that then fails shows where the
screens and the engine disagree.
