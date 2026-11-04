# API stability for apps

An Evlet app is a separate package that imports Evlet. This page says which Evlet names an
app may rely on, how they change, and what stays internal. Evlet is pre-1.0 and not
production ready, but the rules below hold from 0.3 on.

## The app-facing surface

- **Root exports.** Every name in `evlet.__all__`: fields, `Model`, `Child`, `Registry`,
  migrations, permissions (`Policy`, `Rule`, `own_customer`, `SCOPES`), lifecycle
  (`State`, `Move`, `action`, `validate`), slips, measures, settings, connectors, storage,
  jobs, routing and the application classes. Import them from `evlet`, not from the
  module that defines them.
- **Field keywords.** The keywords every field takes (`required`, `default`, `label`,
  `help_text`, `visible_if` and the rest), which are defined once on the field base class.
- **Lifecycle decorators.** `validate.confirm`, `validate.cancel`, `action.confirm` and
  `action.cancel`, plus `Model.to_dict` and `Model.validate`.
- **`evlet.ui` names used in the app docs.** `Bind`, `Button`, `Card`, `Column`,
  `ComponentDefinition`, `CustomComponent`, `Field`, `FormLayout`, `FormView`, `Grid`,
  `ListView`, `Navigate`, `PageView`, `Select`, `Text`, `TextInput`, `ViewRegistry` and
  `When`. The browser does not render Python pages today ([ui.md](ui.md)), but their API
  stays.

- **Record identity.** Every record has `id`, `name` and `title`. `id` is a 13-character text id in
  JSON and URLs and a `RecordId` in Python; `name` and `title` are reserved field names on record
  models ([model-engine.md](model-engine.md#record-ids)).

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
  `evlet.postgres_schema` and `evlet.native`.
- Names that start with an underscore, and dunder attributes such as `__fields__`.
- Methods of exported classes that the app guides do not document, such as `Policy.scope_sql`
  or `Registry.plan_for`.
- The stored layout of tables Evlet manages (`evlet_*`), and the browser framework in
  `frontend/`.

HTTP routes and `/api/schema` are described in their own guides. Their changes are noted in
`CHANGELOG.md`, but the snapshot test covers only the Python surface.
