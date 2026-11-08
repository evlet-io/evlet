# API stability for apps

An Evlet app is a separate package that imports Evlet. This page says which Evlet names an
app may rely on, how they change, and what stays internal. Evlet is pre-1.0 and not
production ready, but the rules below hold from 0.3 on.

## The app-facing surface

- **Root exports.** Every name in `evlet.__all__`: fields, `Model`, `Child`, `Registry`,
  migrations, permissions (`Policy`, `Rule`, `own_customer`, `SCOPES`), lifecycle
  (`State`, `Move`, `action`, `validate`), form sections and record layouts (`Section`, `Tab`,
  `Html`, and `Related` and `Chart` as layout blocks), slips, measures, settings, connectors,
  storage, jobs, routing and the application classes. Import them from `evlet`, not from the
  module that defines them. `AppDefinition(views=())` stays accepted for apps scaffolded
  before 0.4; a non-empty `views` or a `ui` registry is refused.
- **Field keywords.** The keywords every field takes (`required`, `default`, `label`,
  `help_text`, `visible_if` and the rest), which are defined once on the field base class.
- **Lifecycle decorators.** `validate.confirm`, `validate.cancel`, `action.confirm` and
  `action.cancel`, plus `Model.to_dict` and `Model.validate`.
- **No `evlet.ui` page names.** The Python page API was removed in 0.4 without a
  deprecation release, by owner decision, since the browser never rendered it
  ([CHANGELOG.md](../CHANGELOG.md)). `evlet.ui.metadata` stays internal.

- **Record times.** Record details carry `_audit` (`created_at`, `created_by`, `modified_at`,
  `modified_by`), and the column names `evlet_created_at`, `evlet_created_by`, `evlet_modified_at`
  and `evlet_modified_by` are reserved on every model ([model-engine.md](model-engine.md#record-times)).

- **Record identity.** Every record has `id`, `name` and `title`. `id` is a 13-character text id in
  JSON and URLs and a `RecordId` in Python; `name` and `title` are reserved field names on record
  models ([model-engine.md](model-engine.md#record-ids)).

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
  append-only table refuses raises `ConflictError` on SQLite and PostgreSQL
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

- **The transaction session in handlers.** `self.context.session` in a Move's `run` method or an
  `@action` handler: the move or action's own `evlet.Session`, open only while the method runs and
  refused (`AccessError`, 403) on every other context
  ([states-actions.md](states-actions.md#the-transaction-session-in-run-methods-and-actions)).

- **Measure trends and colours.** `trend=` and `color=` on `Count`, `CountDistinct`, `Sum`, `Min` and
  `Max` in `Meta.measures`: a trend is a Date field name or `(field, grain, points)` with grain `day`,
  `month` or `year` and 2 to 24 points, on model-level measures only, and a colour is one of the Select
  `COLORS`. The defaults (`month`, 6 points), the `ValueError` at registry build, and the `trend`
  (`field`, `grain`, `points`) and `color` keys in a schema measure entry, present only when declared
  ([measures.md](measures.md#trends-and-colours)).

- **Form focus.** `Meta.focus` on a model and the `focus` option on a `Slip`: one visible, writable,
  stored field (not calculated, hidden, read only, a `Table` or a `Sequence`), checked with a
  `ValueError` at registration or application start. The `focus` key on a model's `/api/schema` entry
  (left out when unset or unreadable for the role) and on a slip surface (left out when unset). It is
  display only ([ui.md](ui.md#where-the-cursor-starts), [slips.md](slips.md)).

- **Job progress.** `JobContext.progress(done, total=None, message='')`, `JobContext.checkpoint`,
  `Task(..., resumable=True)` with its return-value contract (a JSON checkpoint, or `None` when
  done), `progress` in `GET /api/_jobs/<id>`, and `evlet jobs list`
  ([jobs-realtime.md](jobs-realtime.md#progress-and-long-jobs)).

- **How a state shows.** `State(..., show=...)` takes `"badge"` (the default), `"bar"` or `"none"`,
  and `Select(..., colors=...)` maps choices to badge colours. The values and the default are
  public ([states-actions.md](states-actions.md#how-a-state-and-a-status-show)).

- **Currency display.** `set_currency_display(application, value, *, actor='cli')`, the setting
  `display.currencies` with its entry form (`symbol`, `image`, `position`, `space`, `tint`) and its
  limits, and `currency_display` in `/api/schema` ([controls.md](controls.md#currencies)).

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

Apps should pin Evlet to one minor series, such as `evlet>=0.3.0,<0.4`, and run their tests
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
  `evlet.lifecycle`, `evlet.database` helpers, `evlet.cli`, `evlet.controls_*`,
  `evlet.postgres_schema` and `evlet.ui.metadata`.
- Names that start with an underscore, and dunder attributes such as `__fields__`.
- Methods of exported classes that the app guides do not document, such as `Policy.scope_sql`
  or `Registry.plan_for`.
- The stored layout of tables Evlet manages (`evlet_*`), and the browser framework in
  `frontend/`.

HTTP routes and `/api/schema` are described in their own guides. Their changes are noted in
`CHANGELOG.md`, but the snapshot test covers only the Python surface.
