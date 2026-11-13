# API stability for apps

An Evlet app is a separate package that imports Evlet. This page says which Evlet names an
app may rely on, how they change, and what stays internal. Evlet is pre-1.0 and not
production ready, but the rules below hold from 0.3 on.

## The app-facing surface

- **Root exports.** Every name in `evlet.__all__`: fields, `Model`, `Child`, `Registry`,
  migrations, permissions (`Policy`, `Rule`, `own_customer`, `SCOPES`), the state manager
  (`State` with `State.Step`, `State.Rule` and `State.Remark`, the hooks `validate`, `state`,
  `state.exit` and `release`, and `action`), form sections and record layouts (`Section`,
  `Main`, `Html`, and `Related` and `Chart` as layout blocks), slips, measures, settings, connectors,
  storage, jobs, routing and the application classes. Import them from `evlet`, not from the
  module that defines them. `AppDefinition(views=())` stays accepted for apps scaffolded
  before 0.4; a non-empty `views` or a `ui` registry is refused.
- **Field keywords.** The keywords every field takes (`required`, `default`, `label`,
  `help_text`, `visible_if` and the rest), which are defined once on the field base class.
- **States and moves.** `State(*steps, rules=(), default=, show=)`, `State.Step(key, label,
  color=, lock=, release=, final=)` and `State.Rule(from_, to, label, key=, by=, when=, remark=,
  ask=, primary=, reject=)` with rule keys, `POST /api/{model}/{id}/moves/{key}` with its body,
  checks and answers, `context.move(model, id, key, *, remark=None, **values)`, `_moves`,
  `_locked`, the schema's `state` entry, the Journal's `rule` and `remark`, `versioned=False`,
  and `by=()` for rules only the application makes, with its 403 `<label> is made by the
  application only` ([states-actions.md](states-actions.md)). `State.Level` and `self_approve=`
  are declared but not public until approval levels land (0.4.6, PR 2). 0.4.6 removed `Move`,
  `confirmable`, `track_changes`, `Meta.revisable`, void, revise, unpost and repost,
  `before_close`, `changed`, `Model.validate()` and the lifecycle decorators without a
  deprecation release, by owner decision ([CHANGELOG.md](../CHANGELOG.md)).
- **Records.** `Model.to_dict`.
- **No `evlet.ui` page names.** The Python page API was removed in 0.4 without a
  deprecation release, by owner decision, since the browser never rendered it
  ([CHANGELOG.md](../CHANGELOG.md)). `evlet.ui.metadata` stays internal.

- **Record times.** Record details carry `_audit` (`created_at`, `created_by`, `modified_at`,
  `modified_by`), and the column names `evlet_created_at`, `evlet_created_by`, `evlet_modified_at`
  and `evlet_modified_by` are reserved on every model ([model-engine.md](model-engine.md#record-times)).

- **Record identity.** Every record has `id`, `name` and `title`. `id` is a 13-character text id in
  JSON and URLs and a `RecordId` in Python; `name` and `title` are reserved field names on record
  models ([model-engine.md](model-engine.md#record-ids)). Evlet makes every id; a caller-supplied `id`
  on any create or update path is refused with a `ValidationError` on `id` (HTTP 422), and the
  database refuses a raw id `UPDATE` ([model-engine.md](model-engine.md#only-evlet-makes-ids)).

- **Rich text storage.** `RichText` values are the HTML subset in
  [internal/rich-text.md](internal/rich-text.md): the allowed tags and attributes, and the canonical
  form the server writes, are public, so other clients and stored records keep working. Adding a
  node type (images, then mentions) widens the subset; nothing is removed without a deprecation.
  Images are `<figure class="rt-sm|rt-md|rt-full"><img data-attachment="<id>" alt="...">` with an
  optional `<figcaption>`. Journal notes and messages also keep mentions, `<span data-mention="<user id>">`. The
  `<name>_text` column is internal.

- **Model `Meta` options.** `tests/test_public_api.py` pins the option names, among them `api`
  (`"full"`, `"read"` or `None`), `period`, `period_scope` and `status` (the Select or State shown
  as the status badge, [states-actions.md](states-actions.md#meta-status)), `list_tabs` and `list_tab`
  (the list's tabs and the one it opens on, [ui.md](ui.md#list-tabs)). `Session.unlocked_periods(reason)`
  and `Context.unlocked_periods(reason)` are public for trusted code
  ([model-engine.md](model-engine.md#closed-periods)).

- **Compact record pages.** `Meta.cards` (zero to five derived fields, including those `derive()` sets, or per-record measures),
  `Main(fields, *tables)` as the first block of `Meta.layout`, `Section(..., description=None)`, and
  the schema keys they add: `cards` on a model entry, `"compact": true` on `record_layout`,
  `"main": true` on the Main blocks and layout section, and `description` on a layout section
  ([ui.md](ui.md#compact-record-page)).

- **Ledger models and transaction locks.** The `Meta` options `ledger`, `append_only`, `timestamps`
  (`None`, `"created"`, `"modified"` or `"both"`) and `indexes`, the `created_at` and `modified_at`
  fields that timestamps add, integer ledger row ids, `Session.insert_many(model, rows)`,
  `Session.lock(*keys, timeout=None)` with `LockTimeout`, the `SetAppendOnly` migration, `AddIndex`
  with a tuple of fields, and `evlet ledger list`. A key's lock number (BLAKE2b of its canonical
  JSON) never changes between releases ([ledger.md](ledger.md)).

- **Indexes, id reservation and append-only errors.** `Meta.indexes` on normal models, with the
  same entry form as ledger models, and the saved schema's `indexes` list on a normal model's
  entry (left out when there are none). A broken unique entry raises `ValidationError` with the
  entry's fields as keys (HTTP 422). Ledger entries may name `id`. `Session.reserve_ids(model,
  count)` and an `id` in `insert_many` rows for reserved ids. A raw update or delete that an
  append-only table refuses raises `ConflictError`
  ([model-engine.md](model-engine.md#composite-and-unique-indexes), [ledger.md](ledger.md)).

- **Aggregates in a transaction.** `Session.aggregate(model, *, measures, group_by=(), filters=None,
  limit=None, after=None)` (`tx.aggregate`), with the arguments, limits and result of
  `Database.aggregate` ([measures.md](measures.md#ad-hoc-aggregates-in-python)). `evlet.measures`
  itself stays internal.

- **Trusted set-based queries.** `limit` (1 to 10,000) and `after` on `Session.aggregate` and
  `Database.aggregate` page through any number of groups in a stable order
  ([measures.md](measures.md#paging-through-many-groups)). `Session.list`, `Session.count`,
  `Session.aggregate` and their `Database` forms take `in` and `nin` lists of up to 30,000
  values in all per query
  ([model-engine.md](model-engine.md#long-in-lists-in-trusted-code)). HTTP, `Context`, Eva,
  tools, MCP and Connector keep 100 values per `in` and 500 groups per aggregate.

- **The transaction session in handlers.** `self.context.session` in a `@state` or `@state.exit`
  hook or an `@action` handler: the move or action's own `evlet.Session`, open only while the
  method runs and refused (`AccessError`, 403) on every other context
  ([states-actions.md](states-actions.md#the-transaction-session-in-hooks-and-actions)).

- **Derived values.** `Meta.derives` with `derive(self)` (field, Table and
  `"table.field"` row field names): when it runs, the read-only
  `self.context.session` it gets (`get`, `list`, `count`, `related`, `aggregate`), the
  `DERIVED_FIELD_WRITE` refusal (keyed `table.field` for a row field), the `read_only` flag on
  a derived field, a derived row field and a derived `children` entry in `/api/schema`, `self.model`
  (the model class) on the drafts it gets, and trusted `session.create`, `update` and
  `delete` of a model with `derive()` or of its rows settling the saved document, with
  `children=` on its create and update (a bare
  `Database` refuses them; `maintenance()` skips it) ([model-engine.md](model-engine.md#derived-values)).

- **Measure trends and colours.** `trend=` and `color=` on `Count`, `CountDistinct`, `Sum`, `Min` and
  `Max` in `Meta.measures`: a trend is a Date field name or `(field, grain, points)` with grain `day`,
  `month` or `year` and 2 to 24 points, on model-level measures only, and a colour is one of the Select
  `COLORS`. The defaults (`month`, 6 points), the `ValueError` at registry build, and the `trend`
  (`field`, `grain`, `points`) and `color` keys in a schema measure entry, present only when declared
  ([measures.md](measures.md#trends-and-colours)).

- **Image fields.** `Image()` and `Meta.image`
  ([model-engine.md](model-engine.md#image-fields)): the field type `"image"` in `/api/schema`, its
  value as the text id of a private image attachment in JSON (`int` in Python), the model-level
  `image` key (left out when unset or unreadable for the role), the upload body
  `{"name", "content", "purpose": "image"}` on `/api/{model}/{id}/attachments` and
  `/api/{model}/_images`, the image routes `GET /api/{model}/{id}/attachments/{image id}` and
  `GET /api/{model}/_images/{image id}`, PNG, JPEG, GIF and WebP only, `copy=` fixed at `False`, and the
  `ValueError` for a `Meta.image` that names no Image field.

- **Form focus.** `Meta.focus` on a model and the `focus` option on a `Slip`: one visible, writable,
  stored field (not calculated, hidden, read only, a `Table` or a `Sequence`), checked with a
  `ValueError` at registration or application start. The `focus` key on a model's `/api/schema` entry
  (left out when unset or unreadable for the role) and on a slip surface (left out when unset). It is
  display only ([ui.md](ui.md#where-the-cursor-starts), [slips.md](slips.md)).

- **Section rows.** `Table(..., sections=True)` (a boolean, `ValueError` otherwise, and when the row
  model has a field named `section` or `derive()` builds the Table); `self.lines.sections()` as
  `(label, rows)` pairs with a leading `None` group; iteration yielding real rows only; section
  rows written as `{"section": label}` or `{"id", "section"}` from Python, `children=` and HTTP
  change sets and full lists; the `{"id", "section"}` entries in a record's `children` and in
  `lines/` pages; `sections` and `section_sums` on a schema `children` entry; the Journal's
  `sections` change; and the `sections` template variable on print designs
  ([model-engine.md](model-engine.md#section-rows)). The `evlet_sections` table is internal.

- **Duplicate options.** `Meta.duplicate` on a model (default `True`) and `copy=` on every field
  and on `Table` (default `True`), each checked as a boolean with a `ValueError` (`Meta.duplicate`
  when the registry is built, `copy=` when the field or table is defined). The `duplicate: false` key
  on a model's `/api/schema` entry and `copy: false` on a field or `children` entry, present only
  when off. Display only: they are not part of the stored schema or migration checksums
  ([ui.md](ui.md#duplicate)).

- **Job progress.** `JobContext.progress(done, total=None, message='')`, `JobContext.checkpoint`,
  `Task(..., resumable=True)` with its return-value contract (a JSON checkpoint, or `None` when
  done), `progress` in `GET /api/_jobs/<id>`, and `evlet jobs list`
  ([jobs-realtime.md](jobs-realtime.md#progress-and-long-jobs)).

- **Jobs that act.** `JobContext.business(tx, *, user=None, system=None, role=None)` with the identities and checks
  in [jobs-realtime.md](jobs-realtime.md#a-jobs-business-context), `User.system`, the Journal's
  `via` for a system name, `Context.enqueue(task, payload, *, key=None, target=None)` queuing after
  commit, `_job` on an action's detail, and `JobQueue.wake()` and `JobQueue.wait(timeout)`
  ([jobs-realtime.md](jobs-realtime.md#queue-a-job-from-an-action)).

- **Workspaces.** `Workspace`, `NavGroup` and `Shortcut` with their arguments, discovery of an app
  package's `workspaces.py`, `Application(workspaces=...)` and `AppDefinition(workspaces=...)`, the
  key scheme, and the `workspaces` entry of `/api/schema` with its item kinds (`model`, `report`,
  `slip`, `url`) and `source` values. The report catalog's `source_kind` (`"ledger"`, `"model"` or
  null) is public too ([ui.md](ui.md#workspaces), [reports.md](reports.md)). So are the workspace
  override routes (`/api/_controls/workspaces`), their value format, the `custom` source and the
  role flag `customize_workspaces`, with their answers to people without access: 404, or 403 for
  someone who opens Controls without the flag ([controls.md](controls.md#workspaces)).

- **How a state shows.** `State(..., show=...)` takes `"badge"` (the default), `"bar"` or `"none"`,
  and `State.Step(color=...)` and `Select(..., colors=...)` take the badge colours `blue`, `green`,
  `amber`, `rose`, `violet`, `teal` and `slate`. The values and the default are public
  ([states-actions.md](states-actions.md#how-a-state-and-a-status-show)).

- **Currency display.** `set_currency_display(application, value, *, actor='cli')`, the setting
  `display.currencies` with its entry form (`symbol`, `image`, `position`, `space`, `tint`,
  `decimals`) and its limits, and `currency_display` in `/api/schema` ([controls.md](controls.md#currencies)).
- **Currency decimals.** `currency_decimals(code, displays=None)`, `currency_unit(code, displays=None)`,
  `round_money(value, code, rounding='half_up', displays=None)`, `set_currency_decimals(application,
  decimals, *, actor='cli')`, `round_stored_money(application, code, decimals, *, actor='cli')`, `currency_decimals` in `/api/schema`, the 409 refusal when lowering a code's
  decimals, Currency storage at 6 places with values serialized with at least 2, and the
  `currency` entry of a finite plan's Currency targets ([model-engine.md](model-engine.md#decimal-places-per-currency)).

For each of these, the contract covers the name and the arguments it takes: their names,
order, whether they are keyword-only, and their defaults. Model `Meta` options and the
behaviour each guide documents are public too. `tests/test_public_api.py` pins the names and
signatures, so CI fails on any change to them that was not made on purpose.

## How a public name or argument changes

- **Adding** a name, or an optional argument, is allowed in any release.
- **Removing or renaming** a name or argument, or changing what an argument means, needs a
  deprecation first. The old form keeps working and raises a `DeprecationWarning` that names
  the replacement. It stays for at least one minor release (deprecated in 0.3, removed in 0.4
  at the earliest).
- Every change to the surface, additions included, gets a `CHANGELOG.md` entry that says what
  an app must change, and a snapshot update in the same pull request.

A deprecation is a plain warning at the call site:

```python
warnings.warn("Foo(bar=...) is deprecated; use Foo(baz=...)", DeprecationWarning, stacklevel=2)
```

Apps should pin Evlet to one minor series, such as `evlet>=0.4.0,<0.5`, and run their tests
with `-W error::DeprecationWarning` before they move to the next series.

## Changing the surface

1. Make the change, with a deprecation if it removes or changes anything.
2. Regenerate the snapshot:

   ```bash
   .venv/bin/python tests/test_public_api.py
   ```

3. Review the diff of `tests/fixtures/public-api.json`, and add a `CHANGELOG.md` entry.

## Internal

Apps must not depend on these. They can change in any release without a deprecation:

- Modules and names that are not listed above, even when they can be imported, such as
  `evlet.statemanager` and `evlet.guards` (import from `evlet`), `evlet.database` helpers, `evlet.cli`, `evlet.controls_*`, `evlet.sqlite`
  and `evlet.ui.metadata`.
- The query builder (`evlet.qb`) and the database adapter interface (`evlet.adapter`, the
  connection methods and the `Database` methods listed in [database-adapters.md](database-adapters.md)).
  They are documented for framework work and for people who write a dialect, and are provisional:
  they may change in a minor release, with a `CHANGELOG.md` note.
- Names that start with an underscore, and dunder attributes such as `__fields__`.
- Methods of exported classes that the app guides do not document, such as `Policy.scope`
  or `Registry.plan_for`.
- The stored layout of tables Evlet manages (`evlet_*`), and the browser framework in
  `frontend/`.

HTTP routes and `/api/schema` are described in their own guides. Their changes are noted in
`CHANGELOG.md`, but the snapshot test covers only the Python surface.
