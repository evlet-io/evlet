# What you can customize

An app is ordinary Python. The Notes starter from `evlet app notes` is one package in
`apps/notes/`; Evlet discovers the models, reports and services you put in its folders
([the app layout](app-layout.md)). Edit those files; you do not need to copy or modify the
framework.

## Models, forms and lists

Open `apps/notes/models/item.py`. Add a field to the existing model:

```python
from evlet import Boolean, Model, Rule, Text

class NotesItem(Model):
    permissions = {
        'admin': Rule(('read', 'create', 'update', 'delete')),
        'editor': Rule(('read', 'create', 'update')),
        'viewer': Rule(('read',)),
    }
    title = Text(required=True, max_length=160)
    done = Boolean(default=False)

    class Meta:
        label = 'Item'
        plural = 'Items'
        title_field = 'title'
        list_fields = ('title', 'done')
        search_fields = ('title',)
```

Evlet uses these fields for the database, validation, API and generated forms.
`list_fields` selects the default list columns. A user's column selection is
saved only after they change it, and resets when the default columns change.
`open_in = 'panel'` opens a record from the list in a side panel beside it
instead of on its own page; see "Side panel" in [ui-framework.md](ui-framework.md).
`list_tabs = 'docstate'` makes the list's tabs from that State (or a Select or Link field) and
`list_tab = 'active'` opens the list on that tab; see [List tabs](ui.md#list-tabs).
See [the model engine](model-engine.md) for fields, validators and query rules.
More models go in more files under `models/`; each is discovered.

## Migrations

The new `done` column needs a migration. Stop the server and run:

```sh
evlet makemigration notes --instance acme --name done
```

It compares the instance's database with your models and writes
`apps/notes/migrations/m0001_done.py`:

```python
from evlet import AddField, Boolean, Migration

migration = Migration(
    '0001_done',
    AddField('notesitem', 'done', Boolean(default=False)),
)
```

Review it, then apply it and start the site again:

```sh
evlet migrate acme
evlet serve acme
```

Confirm trust after reviewing your source change. The migration keeps existing
items and gives them `done=False`. Do not edit migrations already applied to a
site. A changed label, page or permission rule needs no migration file, but
`serve` still requires `evlet migrate acme` to record the reviewed source change.

## Screens

The list, board and record screens come from the model. Set `Meta.layout` with
`Main(...)` and `Section(...)`, `Meta.list_fields` and `Meta.open_in` to shape them, and
use `evlet dev --instance acme` to see changes. [The UI guide](ui.md) lists every option.
Model permissions still apply to every record request.

## Actions

After adding and migrating the `done` field, add an action to the model:

```python
from evlet import action

class NotesItem(Model):
    ...
    @action('Mark done')
    def mark_done(self):
        self.done = True
```

The generated record screen shows the action to permitted users (`admin` and
`editor` by default; `@action('Mark done', roles=('admin',))` narrows it). The method runs
inside a transaction; it changes the record through `self` and other rows through
`self.context.session`. Raise `ValidationError` for a business rule failure. Keep external
side effects out of the database transaction unless you have a separate idempotency design.
[States, moves and model actions](states-actions.md) cover inputs and availability.

## Permissions

The `permissions` mapping on the model is the whole rule set: a role it does not name
cannot see the model. To stop editors deleting and viewers seeing items at all:

```python
    permissions = {
        'admin': Rule(('read', 'create', 'update', 'delete')),
        'editor': Rule(('read', 'create', 'update')),
    }
```

Rules can also restrict fields and rows; see [business capabilities](business-capabilities.md).
Hiding a field or button in the UI does not grant or remove backend permission.

## Reports

Put a `Report` class in `apps/notes/reports/` (with an empty `__init__.py`) and run
`evlet migrate acme`; it appears under **Reports** as `notes.<ClassName>`. See
[reports](reports.md).

## App identity and dependencies

`apps/notes/pyproject.toml` contains the app name, version, title and Python
dependencies. The generated app requires Evlet 0.4.0 or newer in the 0.4 series.
Keep helpers inside the package and import them relatively. Review dependency updates
and keep an environment lock when deploying.

The starter preserves underscores in model names, so `shop_x` and `shopx` get
different initial models (`Shop_xItem` and `ShopxItem`). Model names across installed
apps must be unique.
