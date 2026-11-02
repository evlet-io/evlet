# Automatic application generation

Milestone 3 generates navigation, forms, lists, and record details from registered
Python models. The same Vue/PrimeVue components render every model. The Playground
adds Project alongside Customer, Invoice, and Payment to demonstrate this.

## Define a screen in Python

```python
from evlet import Currency, Model, Registry, Select, Text

class Asset(Model):
    class Meta:
        label = "Asset"
        plural = "Assets"
        description = "Keep track of equipment."
        title_field = "name"
        list_fields = ("name", "status", "cost")
        search_fields = ("name",)

    name = Text(required=True, max_length=120, placeholder="Equipment name")
    status = Select(["available", "in_use"], default="available")
    cost = Currency(default="0.00", label="Purchase cost")

registry = Registry(Asset)
```

Pass the registry to `Database` and `Application`. Apply an explicit `CreateModel`
migration when adding this model to an existing database, as described in the
[model engine guide](model-engine.md#explicit-migrations). No frontend model map,
form template, table columns, or navigation entry is required.

`Meta` is optional. Its supported options are:

| Option | Default |
| --- | --- |
| `label` | Class name split into words |
| `plural` | Label plus `s`, `es` after s, x, z, ch or sh, and `ies` for a y after a consonant (Ledger entries); override irregular plurals |
| `description` | Empty text |
| `title_field` | Visible `name`, `title`, `reference`, first short text field, or `id` |
| `list_fields` | ID plus the first four visible fields excluding LongText |
| `search_fields` | First eight visible short text fields |

Lists and search accept up to eight distinct fields. A title field must be a
visible short text field or `id`. Search fields must be visible text types.
Invalid field references and unknown options fail when constructing the registry.
`id` is always included in the generated list. Changing these options does not
change the stored schema or require a migration.

## Field controls and defaults

All Milestone 2 field types are supported. Text, Email, URL, and Date use matching
inputs. LongText uses a multiline input; Select uses declared choices. Required
Boolean uses a checkbox and nullable Boolean also offers an unset state. Currency
and Decimal remain strings throughout editing and submission. DateTime accepts
an ISO value with an explicit timezone, preserving the backend's UTC contract.

Fields accept optional `label`, `help_text`, `placeholder`, `hidden`, and
`read_only` arguments. These are neutral metadata, with no Vue or PrimeVue names.
A hidden field is omitted from generated forms, lists, and details. It remains
available through the API; hiding is not a permission boundary. A read-only field
is disabled in the renderer, and HTTP POST/PATCH rejects attempts to set it.
Server Python code can still set it. Required hidden or read-only fields need an
appropriate server default or application-owned creation path.

Static defaults are serialized into the schema without changing their types.
Callable defaults are marked as callable and are never evaluated while generating
metadata. An untouched default is omitted on create so the server applies it.
Edits send only fields the user changed. Clearing an optional input sends null;
changing a required value to an invalid value leaves validation to the backend.
Failed saves retain entered values and show errors alongside the matching fields.

Browser integer input and record actions support JavaScript's safe integer range
(up to 9,007,199,254,740,991 in magnitude). Larger stored integers are marked as
outside that range; unsafe record IDs cannot be read, changed, or deleted by the
browser. Use the Python API for those values. Decimal and Currency values do not
have this browser number limitation because they remain strings.

## Search, filters, ordering, and pagination

List controls send requests to the server. They do not filter a downloaded table.
Search matches any configured search field, or an exact ID (with optional `#`).
Search is limited to 200 characters. Text matching uses SQLite's ASCII case
folding and treats SQL wildcard characters literally.

Add filters to combine typed comparisons with AND. Search matches are grouped
with OR and combined with those filters using AND. The renderer provides equality,
null checks, text comparisons, and numeric/date ranges according to field type.
The existing API also supports bounded membership and relationship paths. Sorting
uses a selected field and direction, with the backend's ID tie-breaker.

`GET /api/{model}` now accepts `q` alongside `filters`, `order_by`, `limit`, and
`offset`. Its response adds `total`, the count after search and filters. Records
and count are read from the same SQLite snapshot. Pages contain 20 rows in the
renderer and retain the API's maximum of 100. Next is disabled on the final page,
including when it contains exactly 20 records.

Python callers can use `database.list(..., search="text")`,
`database.count(..., search="text")`, and
`records, total = database.page(Model, limit=20, filters={...}, search="text")`.

## Relationships and details

Link inputs search the target model on the server and load choices in pages of
20. More choices can be requested without downloading every record. Editing
resolves the selected record separately when it falls outside the current page.
Lists fetch labels for only the related IDs present on the page, in bounded
batches. Labels use the target model's `title_field`, falling back to its ID.

View loads a fresh record and displays all visible fields. Link values in the
detail view open the related record. Edit also loads the record before editing.
Delete requires confirmation and retains the backend's foreign-key restrictions.
Pending older page and record responses cannot overwrite a newer navigation.

## Metadata contract

`GET /api/schema` returns `version: 1` and `ui_version: 1`. Existing field names,
types, and validation properties remain available, with display properties,
default kinds, and view definitions added. `Registry.ui_schema()` returns this
contract. `Registry.schema()` remains the storage definition used by migrations;
UI configuration and defaults do not alter migration checksums or snapshots.

The renderer rejects unsupported schema/UI versions, field types, and unresolved
relationship targets before enabling writes. It renders labels and record text
as ordinary text. It does not execute code or HTML from metadata.

## Upgrade the Playground

Stop the launcher, then run:

```bash
.venv/bin/python -m examples.playground.migrate
bash scripts/playground.sh
```

The command backs up the database and applies `0002_projects`. Existing Customer,
Invoice, and Payment rows keep their IDs, values, and links. Fresh startup records
both shipped migrations as its baseline. `EVLET_DATABASE` selects another file.

Authentication, roles, permissions, child tables, attachments, and custom business
actions remain Milestone 4 work. The app is still a local development Playground.
