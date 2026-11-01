# The Journal

The Journal is the history of one record. It lists every change the engine made (created,
changed, moved, voided, revised, numbered, files and deletion) and the notes, logged
messages, calls and follow ups people added. Entries are only ever added. Nothing edits or
deletes them.

This guide covers Phase 8 of the model handoff.

## Turning it on

```python
from evlet import Journal, Model, Move, State, Text


class Invoice(Model):
    title = Text(default="")
    status = State(("draft", "issued", "void"), moves=(Move("draft", "issued", "Issue"),),
                   locked=("issued", "void"), final=("void",), voidable=("issued",))

    class Meta:
        journal = Journal(tamper_evident=True)
```

| `Meta.journal` | Result |
|---|---|
| left out | on for a model with a `State`, off otherwise |
| `Journal()` or `Journal(tamper_evident=True)` | on |
| `None` | off, also for a model with a `State` |

There is one way to turn it off: `journal = None`. Any other value is an error when the
registry is built. `Journal()` on a model without a State makes it a document, so every write
goes through the document pipeline (like a `Sequence` does). Table rows have no journal of
their own: their changes are part of their document's entries, and a `Meta.journal` on a row
model is an error. Effects (Phase 6) will also turn the journal on by default.

## Storage

Entries live in one table, created at startup when any model journals, on SQLite and
PostgreSQL:

```
evlet_journal(id, model, record_id, kind, actor, at, data, prev_hash, hash)
```

* `model` is the storage name, `record_id` the record's id. An index on
  `(model, record_id, id)` serves the history of one record.
* `actor` is the user id as text, or `engine` for writes without a signed in user (jobs,
  schedules and other system sessions).
* `at` is the UTC time, for example `2026-11-02T08:15:30.125Z`.
* `data` is canonical JSON (sorted keys, no spaces).
* `prev_hash` and `hash` are set only with `tamper_evident=True`.

Database triggers refuse `UPDATE` and `DELETE` on the table (and `TRUNCATE` on PostgreSQL),
so even raw SQL in trusted code cannot change an entry by mistake. The table is not a model:
no `Session`, HTTP route, connector or MCP tool can write or change it.

Record ids are never reused, so the entries of a deleted record stay with it.

## Engine entries

The engine writes these in the same transaction and savepoint as the change itself. If the
operation fails, even when trusted code catches the failure and the outer transaction
commits, no entry is left behind.

| Kind | Written when | `data` |
|---|---|---|
| `created` | a record is created, including a revision's copy | `fields` and `rows` |
| `changed` | an update or an action changed something | `fields`, `rows`, and `action` for an action |
| `moved` | a move is saved | `from`, `to`, `move` (the label), and `fields` and `rows` for anything else the move changed |
| `voided` | a record is voided | `from`, `to` (`void`) and `reason` |
| `revised` | a record is revised (after its `voided` entry) | `copy` (the new record's id) and `reason` |
| `number` | a Sequence number is assigned | `field` and `number` |
| `file` | an attachment is added or removed | `action` (`added` or `removed`), `id`, `name`, and `size` when added |
| `deleted` | the record is deleted | `{}` |
| `effects` | Effects post or reverse rows (Phase 6, not written yet) | set by Effects |

`fields` maps each changed stored field to its old and new values in their HTTP form:

```json
{"fields": {"name": {"old": "First", "new": "Second"}, "total": {"old": "13.00", "new": "16.00"}}}
```

`rows` covers every Table (and `ChildTable`) collection at every depth:

```json
{"rows": {"lines": {
  "added": [{"id": 7, "fields": {"qty": {"old": null, "new": "1.0000"}}}],
  "updated": [{"id": 5, "fields": {"qty": {"old": "2.0000", "new": "3.0000"}},
               "rows": {"remarks": {"added": [{"id": 9, "fields": {"text": {"old": null, "new": "y"}}}]}}}],
  "removed": [{"id": 6, "fields": {"rate": {"old": "3.00", "new": null}}}],
  "reordered": true
}}}
```

A removed row lists its last values and its own removed rows. `reordered` is set when rows
that stayed changed their order. A create lists every value that is not empty.

Not in `fields`: the row link to the parent, row positions, and Sequence numbers (each
assignment is its own `number` entry). A move's `fields` leave out the State, since `from`
and `to` say it. A write that changes nothing writes no entry.

Raw `Session.create`, `Session.update` and `Session.delete` (trusted code, migrations,
`maintenance()`), `sequences.backfill()` and direct `Storage.add` calls write no entries.

## User entries

People add these through `POST /api/{model}/{id}/journal`, with `kind` and its values:

| Kind | Values |
|---|---|
| `note` | `text` |
| `message` | `text`, `subject`, `party`, `direction` (`in` or `out`, default `out`) |
| `call` | `text`, `party`, `direction`, `minutes` (0 to 1440) |
| `follow_up` | `text`, `due` (`YYYY-MM-DD`), `assignee` (a user id) |
| `follow_up_done` | `follow_up` (the follow up entry's id), `text` |

* `text` is required except for `follow_up_done`. Text is trimmed, at most 10,000
  characters (200 for `subject` and `party`) and must not contain NUL characters. Unknown
  values are refused with 422.
* A `message` is a logged message. Evlet does not send email.
* The `assignee` must be an active user who can read the record (row scopes included).
* A follow up is completed by appending `follow_up_done` that names it. A follow up of
  another record, or any other entry, is a 422; completing it twice is a 409.
* Entries can be added in any state, including locked and void records, because they do not
  change the record.

The answer is `201` with the entry as the caller sees it.

From trusted Python code:

```python
from evlet import journal

with app.business.context(user) as context:
    journal.post(context, Invoice, 4, "note", text="Called the customer")
    page = journal.entries(context, Invoice, 4, limit=20)
```

Both apply the same checks as HTTP.

## Permissions

Reading needs read access to the record within the caller's row scope (otherwise 403 or
404). Posting also needs the `journal` operation:

```python
permissions = {
    "admin": Rule(("read", "create", "update", "delete")),
    "editor": Rule(("read", "create", "update")),
    "viewer": Rule(("read", "journal")),
}
```

* By default admin and editor may post, when their Rule lets them read the model; viewers
  may not.
* When any Rule of the model names `journal`, admin and the roles that name it may post. So
  the Rule above lets the viewer post and stops the editor.
* A Rule naming `journal` on a model without a journal fails at startup.

## Reading the history

`GET /api/{model}/{id}/journal?limit=50&offset=0` returns one page, newest first:

```json
{"items": [
   {"id": 12, "kind": "note", "actor": 2, "at": "2026-11-02T08:15:30.125Z", "data": {"text": "Called back"}},
   {"id": 11, "kind": "follow_up", "actor": 2, "at": "...", "data": {"text": "Send the contract", "due": "2026-11-10", "assignee": 3}, "done": null},
   {"id": 10, "kind": "changed", "actor": 1, "at": "...", "data": {"fields": {"name": {"old": null, "new": "First"}}}, "partial": true}
 ],
 "total": 3, "limit": 50, "offset": 0, "users": {"1": "admin", "2": "editor", "3": "viewer"}}
```

* `limit` is 1 to 100 (default 50). Other query keys are a 422.
* `actor` is a user id, or `null` for the engine. `users` names the actors and assignees on
  the page.
* A follow up has `done`: the id of the entry that completed it, or `null`.

Every entry is listed, so `total` matches what the caller can page through. What the caller
cannot read is removed, and the entry is marked `"partial": true`:

* Values of fields the caller cannot read are left out, at every depth.
* A Link or Reference value naming a record the caller cannot see (a row scope) shows as
  `null`, as in record details.
* Rows of a collection the caller cannot read are left out. Under a row scope on the rows,
  rows the caller cannot see are left out, and so are all removed rows, because they can no
  longer be checked against the scope. Updated rows with nothing readable left are dropped.
* For a caller who cannot read the State, `moved`, `voided`, `revised` and `effects`
  entries are shown as `changed` with only the readable field and row changes. A `number`
  entry for a field the caller cannot read is shown as `changed` with no data. This keeps
  states and numbers private, as the rest of the API does.

Row and Link checks run in batches: one statement per model and chunk of ids for the page.

## Details, schema and events

* Record details (GET, writes, moves, void and revise answers) of a journaled model carry
  `_journal`: `{"count": 12, "open_follow_ups": 1}`. It is outside the ETag, so adding a note
  does not change the record's revision. It costs one statement.
* `/api/schema` entries of journaled models carry
  `journal: {"tamper_evident": true, "kinds": [...]}`, where `kinds` are the user kinds this
  role may post (empty for viewers by default).
* The `EventStore` stays the way to learn about live changes; it is not history. Engine
  entries come with the record's usual change event. A user entry publishes an event with
  `"journal": true` (`{"model": "invoice", "id": 4, "deleted": false, "journal": true}`), so
  an open record view can refresh its activity without reloading the record.

## Tamper evidence

With `Journal(tamper_evident=True)` each entry stores:

* `prev_hash`: the `hash` of the record's previous entry, or `null` for its first entry.
* `hash`: the SHA-256 of the canonical JSON array
  `[model, record_id, kind, actor, at, data, prev_hash]`, where `data` is the stored JSON
  text.

Writers are serialized on both databases, so the chain has no forks.

Check an instance:

```sh
evlet journal verify acme
```

It opens the instance database read only, reads every entry in one snapshot, and prints
`Journal verified: N hashed entries, no breaks`, or one line per break and exits with 1:

```
Entry 2 (invoice 1): the hash does not match the entry
1 break in 3 hashed entries
```

A break is an entry whose content no longer matches its hash, whose `prev_hash` does not match
the entry before it (an entry was removed or reordered), or an entry without a hash after
hashed entries of the same record. For a PostgreSQL database, call
`evlet.journal.verify(database)` from Python; it returns `(entries checked, breaks)`.

What it does not do:

* Someone who can write the database can recompute every hash. The chain shows accidental or
  careless edits; to detect a deliberate rewrite, keep a copy of recent hashes elsewhere.
* Removing a record's latest entries, or all of them, leaves a valid shorter chain.
* Turning `tamper_evident` on later starts each record's chain at its next entry. Turning it
  off later makes `verify` report the unhashed entries that follow hashed ones.

## Limits

* `RenameModel` and `RenameField` migrations do not rewrite stored entries: older entries keep
  the old names (and old field names are then left out of reads, since they are no longer
  readable fields).
* A create or update of a large document stores every changed row in one entry; a
  1,000-row document gives an entry of that size. A PATCH of a 1,000-row document costs at
  most three more statements with a hashed journal than without one (the previous hash, the
  entry and the `_journal` summary).
* The connector, the MCP server and the SolidJS screens do not show the journal yet; the
  new UI will.
