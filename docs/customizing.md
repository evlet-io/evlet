# What you can customize

An app is ordinary Python. The Notes starter already connects its model, views,
actions, permissions and migrations through `notes/hooks.py`. Edit those modules;
you do not need to copy or modify the framework.

## Models, forms and lists

This walkthrough uses the supported `evlet app notes --legacy` scaffold.
For the default flat package and declarative features, use the
[declarative app guide](amendment-v2.md).

Open `apps/notes/notes/models/__init__.py`. Add a field to the existing model:

```python
from evlet import Boolean, Model, Text

class NotesItem(Model):
    title = Text(required=True, max_length=160)
    done = Boolean(default=False)

    class Meta:
        label = 'Item'
        plural = 'Items'
        title_field = 'title'
        list_fields = ('title', 'done')
        search_fields = ('title',)

models = (NotesItem,)
```

Evlet uses these fields for the database, validation, API and generated forms.
`list_fields` selects the default list columns. A user's column selection is
saved only after they change it, and resets when the default columns change.
See [the model engine](model-engine.md) for fields, validators and query rules.

## Migrations

Keep the frozen initial model in `notes/migrations/__init__.py` unchanged. Add
`AddField` and `Boolean` to that module's imports, then append this after its
existing `migrations` definition:

```python
migrations = (*migrations,
    Migration('0002_done', AddField('notesitem', 'done', Boolean(default=False))),
)
```

Stop the server and run:

```sh
evlet migrate acme
evlet serve acme
```

Confirm trust after reviewing your source change. The migration keeps existing
items and gives them `done=False`. Do not edit migrations already applied to a
site. A changed label, page or permission rule needs no schema migration, but
normal `serve` still requires `migrate` to record the reviewed source change.

## Pages

Replace `notes/views/__init__.py` with:

```python
from evlet.ui import Column, PageView, Text

views = (
    PageView('notes_home', route='/notes', title='Notes',
             layout=Column(children=[Text('Your notes workspace')])),
)
```

The starter's hooks already register `views`. Use `evlet dev --instance acme`
to see changes. Pages, layout, state and component options are described in
[the UI guide](ui.md). Model permissions still apply to every record request.

## Actions

After adding and migrating the `done` field, replace `notes/actions/__init__.py`:

```python
from evlet import Action
from ..models import NotesItem

def mark_done(tx, user, record):
    tx.update(NotesItem, record.id, done=True)

actions = (Action(NotesItem, 'mark_done', 'Mark done', mark_done),)
```

The generated record screen shows the action to permitted users. The handler runs
inside a transaction and uses its `tx` session. Raise `ValidationError` for a
business rule failure. Keep external side effects out of the database transaction
unless you have a separate idempotency design.

## Permissions

Replace `notes/permissions.py` with:

```python
from evlet import Rule
from .models import NotesItem

rules = {
    ('viewer', NotesItem): Rule(operations=('read',)),
    ('editor', NotesItem): Rule(operations=('read', 'create', 'update')),
}
```

Editors can create and update items but cannot delete them. Viewers can read.
Administrators retain the default access. Rules can also restrict fields and rows;
see [business capabilities](business-capabilities.md). Hiding a field or button in
the UI does not grant or remove backend permission.

## App identity and dependencies

`apps/notes/pyproject.toml` contains the app name, version, title and Python
dependencies. The generated app requires Evlet 0.2.0 or newer in the 0.2 series.
Keep helpers inside the `notes/` package and import them relatively. Root-level
Python helper files are not exposed by the app loader. Review dependency updates
and keep an environment lock when deploying.

The starter preserves underscores in model names, so `shop_x` and `shopx` get
different initial models. This is a scaffold convention; handwritten model names
across installed apps must still be unique.
