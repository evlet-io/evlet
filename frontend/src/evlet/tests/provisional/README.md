# Provisional fixtures

`invoice-fixture.json` is written by hand. The engine this branch sits on (`model/03-lines`) does
not have the later phases yet, so their responses cannot be recorded here. Each key follows the
documented JSON of its phase exactly:

| Keys | Phase and source |
|---|---|
| `schema.models[invoice].state`, `actions[].inputs`, `available_when`, `editable_when_locked`, `state: true` on the State field | Phase 4, `docs/states-actions.md` (shape checked against `evlet/states.py` on `model/10-meaning`) |
| `_locked`, `_moves`, `_actions`, `_void`, `_revise`, `revised_by`, `revises`, `_original`, `voidable`, `revisable`, `permissions.void`/`revise` | Phases 4 and 5, `docs/states-actions.md` |
| `sequence` on the `number` field | Phase 7, `docs/sequences.md` |
| `effects`, `effect_target`, `_effects` | Phase 6, `docs/effects.md` |
| `measures`, `invoice_measures`, `invoice_record_measures`, `invoice_aggregate_by_status` | Phase 9, `docs/measures.md` |
| `key_fields`, `relations`, `attachment_limits`, `invoice_related_*` | Phase 10, `docs/meaning.md` |
| `journal`, `invoice_journal` | Phase 8: no doc exists yet; the shape follows the outline in `docs/handoff-model.md` (`kind`, `actor`, `at`, `data`) |

They are used by `screens-list.test.ts` (board and saved views) and `screens-document.test.ts`.

Once the phases are stacked under the screens, record these responses from the real routes in
`scripts/generate_ui_framework_fixtures.py`, replace this file and delete this folder. A test that
then fails shows where the screens and the engine disagree.
