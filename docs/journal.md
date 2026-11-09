# The Journal

The Journal is the history of one record. It lists every change the engine made (created,
changed, moved, voided, revised, unposted, reposted, numbered, files and deletion) and the notes, logged
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
| left out | on for a model with a `State` or `Meta.effects`, off otherwise |
| `Journal()` or `Journal(tamper_evident=True)` | on |
| `None` | off, also for a model with a `State` |

There is one way to turn it off: `journal = None`. Any other value is an error when the
registry is built. `Journal()` on a model without a State makes it a document, so every write
goes through the document pipeline (like a `Sequence` does). Table rows have no journal of
their own: their changes are part of their document's entries, and a `Meta.journal` on a row
model is an error. A model with `Meta.effects` journals by default too (an effect needs a
State, so this changes nothing for valid models today, but the rule names both).

## Storage

Entries live in one table, created at startup when any model journals, on SQLite and
PostgreSQL:

```
evlet_journal(id, model, record_id, kind, actor, at, data, prev_hash, hash)
```

* `model` is the storage name, `record_id` the record's id. An index on
  `(model, record_id, id)` serves the history of one record.
* `actor` is the user id as text, or `engine` for writes without a signed in user (jobs,
  schedules and other system sessions). A write through an AI assistant keeps the person as
  the actor and adds `"via": "<client name>"` to `data`.
* `at` is the UTC time, for example `2026-11-02T08:15:30.125Z`.
* `data` is canonical JSON (sorted keys, no spaces).
* `prev_hash` and `hash` are set only with `tamper_evident=True`.

Database triggers refuse `UPDATE` and `DELETE` on the table (and `TRUNCATE` on PostgreSQL),
so even raw SQL in trusted code cannot change an entry by mistake. On SQLite a third trigger
refuses an insert that reuses an existing id, because `INSERT OR REPLACE` deletes the old row
without firing DELETE triggers. The triggers are created with `IF NOT EXISTS` (or checked by
name on PostgreSQL) at every start, so an older database gets new ones. The table is not a
model: no `Session`, HTTP route, connector or MCP tool can write or change it. Someone with
direct database access can still drop the triggers; see "Tamper evidence".

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
| `unposted` | an admin unposts a posted record (before the `effects` entry of its reversal rows) | `from`, `to` (the state it posted from) and `reason` |
| `reposted` | an unposted record is posted again (after the move's `moved` and `effects` entries) | `to`, `move` (the label), and `reason` when given |
| `number` | a Sequence number is assigned | `field` and `number` |
| `file` | an attachment is added or removed | `action` (`added` or `removed`), `id`, `name`, and `size` when added |
| `deleted` | the record is deleted | `{}` |
| `effects` | a move posted [effect](effects.md) rows, or a void or unpost wrote their mirror rows | `targets` |
| `recomputed` | a `Recompute` migration changed stored results (actor: the engine) | `fields` and `rows` (`updated` only), and `posted: true` for a posted record an admin included |

`fields` maps each changed stored field to its old and new values in their HTTP form:

```json
{"fields": {"name": {"old": "First", "new": "Second"}, "total": {"old": "13.00", "new": "16.00"}}}
```

`rows` covers every Table (and `ChildTable`) collection at every depth:

```json
{"rows": {"lines": {
  "added": [{"id": "01hc1sd9vc1yg", "fields": {"qty": {"old": null, "new": "1.0000"}}}],
  "updated": [{"id": "01hc1sd9vc1ye", "fields": {"qty": {"old": "2.0000", "new": "3.0000"}},
               "rows": {"remarks": {"added": [{"id": "01hc1sdb0j8m4", "fields": {"text": {"old": null, "new": "y"}}}]}}}],
  "removed": [{"id": "01hc1sd9vc1yf", "fields": {"rate": {"old": "3.00", "new": null}}}],
  "reordered": true
}}}
```

A removed row lists its last values and its own removed rows. `reordered` is set when rows
that stayed changed their order. A create lists every value that is not empty.
A Table with section rows ([model-engine.md](model-engine.md#section-rows)) adds `sections`:
`{"added": [{"id", "label"}], "renamed": [{"id", "old", "new"}], "removed": [{"id", "label"}],
"moved": true}`, each key only when it applies. A section has moved when it changed places with
another section or now sits above another row; rows added or removed around it do not move it.

A `created` entry lists every value of every row, so an entry for a large document is large:
creating a document with 1,000 lines stores an entry of about 400 KB, and a page of 50
entries can carry several such entries. Read the journal in pages (`limit` and `offset`)
and keep the page small for documents with many rows.

`Recompute` changes a record with postings only when an admin migrates with
`--recompute-posted` (see [finite-calculations.md](finite-calculations.md)); its entry then
has `"posted": true`, shown to readers of the State. Otherwise `recomputed` entries appear only
on records without live postings, locked or not.

Unpost and repost also write a row in the Controls audit log (area `Records`), in the same
transaction as the entries.

An `effects` entry follows the `moved`, `voided` or `unposted` entry of the same operation and lists
each target model that received rows:

```json
{"targets": [{"target": "ledgerentry", "rows": 3, "reversal": false, "balanced": true},
             {"target": "stockmove", "rows": 2, "reversal": false}]}
```

`rows` counts the rows written, `reversal` is true for the mirror rows of a void or an unpost, and
`balanced` is present for an effect that declares a balanced pair. Targets that received no
rows are left out, and no entry is written when no rows were written.

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
  characters (200 for `subject` and `party`), must be valid Unicode (a lone surrogate is
  `Expected valid Unicode text`, as for record fields) and must not contain NUL characters.
  Unknown values are refused with 422.
* Requests are limited to 64 KiB of JSON, so 10,000 characters always fit as plain text, but
  a client that escapes non-ASCII characters (`\u00e9` is six bytes) can pass the request
  limit first and get 413.
* A `message` is a logged message. Evlet does not send email.
* A `note` or `message` may send `html` instead of `text`: formatted text from the activity composer
  (Evlet's rich text editor), at most 40,000 characters. The server sanitises it like a `RichText`
  field and also keeps mentions, `<span data-mention="<user id>">Name</span>`; it stores the
  sanitised `html` and derives `text` (a mention reads as `@Name`). Reading the journal names
  mentioned people in `users` with their current username, so a renamed person shows correctly.

### Mentions

Each active user an entry mentions (other than its author) gets a row in their inbox,
`evlet_inbox` (kind `mention`), the framework's per-user inbox that later notifications can share.

* **Permission-safe.** A row holds only who mentioned them, when, and which record. When the
  person reads their inbox, the record's label, title and link are shown only if they can open the
  record then; otherwise the row says "Omar mentioned you on a record you can't open" and nothing
  else, not the note.
* **While composing** `GET /api/{model}/{id}/mentionable?q=` lists up to 20 active desk users whose
  name or username matches, each with `can_read`, so the composer can warn "Priya can't open this
  record". It needs the `journal` operation on the record.
* **The inbox:** `GET /api/_inbox` (`?limit=&offset=`) answers `{items, total, unread}`, newest
  first; `GET /api/_inbox/count` answers `{unread}`; `POST /api/_inbox/read` takes `{"ids": [...]}`
  or `{"all": true}`. Each person reads and marks only their own rows. A change sends the event
  `{"type": "inbox.changed"}` to that person only ([jobs-realtime.md](jobs-realtime.md)).
* **Preferences** (Settings, Notifications, "Someone mentions me", in Evlet and email, both on by
  default). With "in Evlet" off there is no inbox row; with email on, an email goes through the
  mail connector's outgoing account once the entry is committed, to the person's profile email. It
  has the same text as the inbox row, a link only to a record they can open (when the app has one
  address), and none of the note. The outcome is kept on the row (`sent`, `failed`, `no_email`,
  `no_account`). Entries posted from trusted Python create inbox rows but send no email.
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

Both apply the same checks as HTTP. `post()` runs in its own savepoint like other document
operations: a failure caught by trusted code leaves no entry, and posting to a record whose
own operation is running (from its `changed()` reaction, say) is refused as a recursive
change.

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

* Admin may always post when its Rule reads the model. The editor may post when its Rule
  reads and updates the model (row scopes apply to both). Viewers, and editors that only
  read, may not.
* A Rule naming `journal` adds that role, like `void` and `revise`; naming a role never
  removes admin or the editor. So the Rule above lets the viewer post as well.
* A custom role (Controls, `docs/controls.md`) based on the editor posts like the editor.
  A Controls override can take `journal` away from a role whose file Rule names it.
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
* `via` names the AI assistant a write came through, when it did (see
  [Controls](controls.md#mcp-and-ai)). It is taken out of `data`.
* `?kind=message` (any entry kind) keeps one kind, such as the emails on a record.
* A `message` copied from a personal mailbox ([Controls](controls.md#personal-mailboxes)) has
  `"source": "mailbox"`, and its `party` is `null` (the entry `partial`) for a reader who can't
  read the record's `Email` field that matched.
* A follow up has `done`: the id of the entry that completed it, or `null`.

Every entry is listed, so `total` matches what the caller can page through. What the caller
cannot read is removed, and the entry is marked `"partial": true`:

* Values of fields the caller cannot read are left out, at every depth.
* A Link or Reference value naming a record the caller cannot see (a row scope) shows as
  `null`, as in record details.
* Rows of a collection the caller cannot read are left out. Under a row scope on the rows,
  rows the caller cannot see are left out, and so are all removed rows, because they can no
  longer be checked against the scope. Updated rows with nothing readable left are dropped.
* For a caller who cannot read the State, `moved`, `voided`, `revised`, `unposted`,
  `reposted` and `effects` entries are shown as `changed` with only the readable field and row changes. A `number`
  entry for a field the caller cannot read is shown as `changed` with no data. This keeps
  states and numbers private, as the rest of the API does.
* In an `effects` entry, targets the caller does not read fully (read permission and no row
  scope, as for `_effects` in details) are left out, and so is `balanced` when the caller
  cannot read both balanced fields. When no target is left, the entry is shown as `changed`
  with no data.

Row and Link checks run in batches: one statement per model and chunk of ids for the page.

What stays visible of a change the caller cannot read: the entry itself, as a `partial`
`changed` entry with its actor and time, and the actor's user name in `users`. So a reader can
tell that someone changed something hidden, and when, but not what. The record's change events
already tell readers the same. An action's name is shown only to roles that can read the State
(or on models without one), since which action ran says something about the state.

## Details, schema and events

* Record details (GET, writes, moves, void, revise, unpost and repost answers) of a journaled model carry
  `_journal`: `{"count": 12, "open_follow_ups": 1}`. It is outside the ETag, so adding a note
  does not change the record's revision. It costs one statement.
* `/api/schema` entries of journaled models carry
  `journal: {"tamper_evident": true, "kinds": [...]}`, where `kinds` are the user kinds this
  role may post (empty for viewers by default).
* The `EventStore` stays the way to learn about live changes; it is not history. Engine
  entries come with the record's usual change event. A user entry publishes an event with
  `"journal": true` (`{"model": "invoice", "id": "01hc1sd9vc1ye", "deleted": false, "journal": true}`), so
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
the entry before it (an entry was removed or reordered), or an entry without a hash when its
model is tamper evident (hashes stripped) or follows hashed entries of the same record. The
models that declared `tamper_evident=True` are recorded in `evlet_journal_hashed` at startup,
so the command knows them without loading the apps.

The command reads one instance's SQLite database. If the database uses WAL and its shared
memory file cannot be created read only, it opens the database normally with
`PRAGMA query_only`, which still refuses every write. For a PostgreSQL database, or a tenant
database of a `TenantRegistry`, call `evlet.journal.verify(database)` from Python with that
database; it returns `(entries checked, breaks)` and also uses the registry's declarations.

What it does not do:

* Someone who can write the database can recompute every hash. The chain shows accidental or
  careless edits; to detect a deliberate rewrite, keep a copy of recent hashes elsewhere.
* Removing a record's latest entries, or all of them, leaves a valid shorter chain.
* Turn `tamper_evident` on before a model has entries: once declared, every unhashed entry of
  the model is reported, including older ones. Turning it off later keeps the model recorded,
  so `verify` reports the unhashed entries written after.

## Limits

* `RenameField` migrations do not rewrite stored entries: older entries keep the old field
  name, which is then left out of reads, since it is no longer a readable field. Evlet has no
  model rename migration.
* A create or update of a large document stores every changed row in one entry; a
  1,000-row document gives an entry of that size. A PATCH of a 1,000-row document costs at
  most four more statements with a hashed journal than without one (the previous hash, the
  entry, the SQLite insert guard trigger and the `_journal` summary).
* The connector and the MCP server do not show the journal yet. The UI shows it in the
  record view's Activity tab.
