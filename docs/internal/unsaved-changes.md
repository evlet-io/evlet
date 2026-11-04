# Unsaved changes: keep edits on leave, never write the record

Status: in progress. Backend first, then the record form. This note follows the owner's design
and records how Evlet implements it.

## Rule

Leaving a record with unsaved edits never changes the record. Only an explicit Save does. Leaving
keeps the edits somewhere else, and the next visit asks to restore or discard them:

| Record | Where the edits go | Until |
|---|---|---|
| Saved (has an id) | `evlet_unsaved`, one framework table for every model, one row per (user, model, record) | Save, Discard, record delete, expiry, cap |
| New (no id yet) | Browser storage, keyed by instance, user and model | Save, Discard, sign-out, another user signing in, expiry, cap |

Nothing goes to the server for a new record until Save, so no record is created, no validator or
event runs and no document number is used.

## Why not the existing routes

`POST /api/_draft/<model>` is a read-only server calculation of a form that is not saved (it
previews computed values and errors). It stores nothing, so it does not fit. The record routes
(`PATCH /api/<model>/<id>`) run validation, events, sequences and history, which is exactly what
leaving must not do. The store is a separate, small route family.

## Server store

```sql
evlet_unsaved(user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE,
              model TEXT NOT NULL, record_id INTEGER NOT NULL,
              changes TEXT NOT NULL, base_revision TEXT NOT NULL,
              saved_at INTEGER NOT NULL, size INTEGER NOT NULL,
              PRIMARY KEY (user_id, model, record_id))
```

* Framework step `0004_unsaved_changes` creates it (the framework migration ledger), and startup
  creates it too on a database the step already ran on. `model` is the model's storage name, as
  in the record URL and `evlet_attachments`.
* `changes` holds only the edited writable fields:

  ```json
  {"values": {"field": "new value"}, "base": {"field": "value when the edit started"},
   "children": {"lines": {"added": [{"key": "n1", "values": {...}}],
                          "changed": {"<row id>": {"values": {...}, "base": {...}}},
                          "removed": ["<row id>"]}}}
  ```

  Added rows can hold their own `children` the same way, and so can changed rows (nested tables).
  `base` is what conflicts are checked against.
* `base_revision` is the record's ETag when the edit started, as `GET /api/<model>/<id>` sent it.

### Routes

| Route | What it does |
|---|---|
| `GET /api/_unsaved` | The caller's own limits and entry count: `{limits, count}`. The browser store reads its limits here |
| `PUT /api/_unsaved/<model>/<id>` | Upsert `{"changes", "base_revision"}`. Idempotent. Answers `{saved_at, size, dropped}` |
| `GET /api/_unsaved/<model>/<id>` | The caller's own entry: `{entry: {changes, base_revision, saved_at, size, stale}, dropped}`, or `{entry: null}` |
| `DELETE /api/_unsaved/<model>/<id>` | Discard the caller's own entry. Answers 204 even when there was none |

* Session sign-in only. API keys, Eva, MCP, the Tool Engine and connectors are refused (they use
  `user.access`), and sub-hosts answer 404. CSRF is checked like every other write.
* PUT and GET check that the caller can read the record and update it now (`policy.record(...,
  'update')`). Fields the caller may not write are dropped and listed in `dropped`; field and table
  names the model does not have are refused with 422. A model with `Meta.autosave = False`, a
  child table, or a person who turned the setting off is refused.
* PUT never runs validators, events, sequences, history or the journal, and never touches the
  record. The values are kept as JSON as the browser sent them; Save validates them later.
* GET drops fields the caller can no longer write (and says so in `dropped`). When the caller can
  read but no longer update the record, the entry is deleted and `dropped` is `"record"`. When the
  caller cannot read it, the entry is deleted and the answer is 404, as for the record. `stale` is
  true when the record's revision moved past `base_revision`.
* Save does not need the browser to call DELETE: a successful record save (PATCH, or a document
  write) deletes the saver's entry in the same transaction. The browser also sends DELETE after a
  save, which is harmless. Deleting a record deletes every user's entry for it, in the delete's
  transaction.
* Turning the personal setting off deletes the person's entries.

### Privacy

Only the owner reads an entry. The table is not a model, so reports, exports, Discovery, Eva,
MCP and the Tool Engine never see it. Controls shows counts and sizes only: total entries and
bytes, the oldest entry's time and per-user counts, never `changes`.

## Browser store

* `localStorage` key `evlet.unsaved.<instance>.<user>`: a map of model to `{values, children,
  saved_at}` for unsaved new records. One entry per model (latest wins). Every access is in
  try/catch; with storage unavailable the edit stays only in the open tab.
* Opening New for that model asks "Restore your unfinished new <Model>? Restore / Start fresh".
* Cleared on Save, Discard and Start fresh, on sign-out, and when another user signs in on that
  browser (the store keeps the last user per instance and wipes the instance's keys when it
  changes). Expired and over-cap entries are purged when the app loads.

## Limits (bloat)

Instance settings in Controls (`unsaved.*`), each with a safe maximum:

| Key | Default | Allowed | What |
|---|---|---|---|
| `unsaved.server_days` | 30 | 1 to 90 | Server entries older than this are purged |
| `unsaved.server_entries` | 200 | 10 to 1000 | Entries per person on the server; the oldest are dropped |
| `unsaved.browser_days` | 7 | 1 to 30 | Browser entries older than this are purged |
| `unsaved.browser_entries` | 20 | 1 to 50 | Unsaved new records per browser user |
| `unsaved.entry_kb` | 256 | 16 to 1024 | Largest entry. A larger one is refused with a clear message and stays only in the open tab |

* One row per user per record (upsert), one per user per model in the browser.
* Delete on Save, Discard and record delete.
* The per-person cap is applied on every PUT, in its transaction.
* Clean-up: the framework task `evlet_unsaved_purge` runs daily on the schedule of the same name
  when jobs are on, and at every server start. It deletes in batches of 500 rows.
* Controls, Jobs page, Unsaved changes section: totals, oldest entry, per-user counts, and
  "Clear entries older than N days" (audited, area Records).

## Opt-out

* `Meta.autosave = False` on a model keeps today's leave prompt. It is validated at registration
  (True or False) and is `autosave` in the schema.
* "Keep unsaved changes when I leave a record" (`keep_unsaved`, on by default) in Settings,
  Appearance. Off: the leave prompt comes back and the person's server entries are deleted.

## Leave behaviour

With autosave on, leaving (route change, another record, panel close, list navigation, tab hidden)
no longer shows "Discard changes?". The form stores the edits and shows a toast: "Unsaved changes
kept, restore when you return". With autosave off, or the setting off, nothing changes.

## Restore and conflicts

Opening a record with an entry shows a bar above the form: "You have unsaved changes from
<relative time>. Restore / Discard". Restore applies the stored changes as unsaved edits; Save is
still needed. When the entry is stale, fields where both the stored edit and the record changed
since `base` are listed with both values; the person keeps theirs or the saved value per field,
or discards everything.

## Owner decisions open

* The safe maximums in the Limits table.
* Whether Controls should also offer "Clear all for one person" (not built; counts only).
