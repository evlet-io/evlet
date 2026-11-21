# The app layout

`evlet app notes` (and `evlet setup myproject --app notes`) creates a flat app: one Python
package in `apps/notes/`. Evlet finds what you put in its folders; there is no registry to
update and no hooks file to edit.

```text
apps/notes/
  __init__.py
  pyproject.toml        name, version, title and Python dependencies
  setup.py              packages the folders below; leave it as generated
  models/
    __init__.py
    item.py             NotesItem, the starter model
  reports/              optional: Report classes
  services/             optional: @api and @cron functions, Task and Schedule objects
  migrations/           optional: written by evlet makemigration
  workspaces.py         optional: Workspace definitions
  tests/
    test_notes.py       tests on a temporary instance (evlet.testing); not part of the build
```

Create `reports/`, `services/` and `migrations/` when you need them, each with an empty
`__init__.py`. Keep helpers inside the package and import them relatively
(`from ..models.item import NotesItem`).

## The essentials

`evlet` exports about 140 names; a new app needs about fifteen of them. Start with these and
look the rest up when a guide calls for one:

| Name | For |
| --- | --- |
| `Model` | A document with its own table, screens and API |
| `Text`, `LongText`, `Integer`, `Decimal`, `Currency`, `Date`, `Boolean`, `Select` | Fields ([model-engine.md](model-engine.md)) |
| `Link` | A field that points at a record of another model (a foreign key) |
| `Child`, `Table` | Rows a document owns, such as invoice lines: a `Child` class, held by a `Table(...)` field |
| `Rule` | One role's permissions on a model, in `permissions = {...}` ([business-capabilities.md](business-capabilities.md)) |
| `State` | The status field `docstate` with its steps and moves ([states-actions.md](states-actions.md)) |
| `validate`, `action` | Checks on every save, and buttons that run a method ([states-actions.md](states-actions.md)) |
| `ValidationError` | Refuse a save or an action with a message for a field |
| `Report`, `Column` | A report in `reports/` and its columns ([reports.md](reports.md)) |
| `api`, `cron` | An HTTP endpoint or a scheduled function in `services/` ([jobs-realtime.md](jobs-realtime.md)) |

Computed fields and conditions use `F` from `evlet.calc` ([finite-calculations.md](finite-calculations.md)).
String formulas (`compute="qty * rate"`, `visible_if=`) are the older language and round differently
([Two formula languages](finite-calculations.md#two-formula-languages)); do not start with them.
Migration operations (`AddField`, `RenameField` and the rest) are written for you by
`evlet makemigration`. Tests use `evlet.testing` ([testing.md](testing.md)).

Some names look alike but belong to different things. Three were renamed in 0.4.x to say what
they are; the old names still work and warn ([api-stability.md](api-stability.md#renamed-in-04x-old-names-deprecated)):

- **`DevApp`, `AppDefinition`, `Application`.** A flat app needs none of them. `Application` is the
  ASGI application that serves an instance; the CLI builds it. `DevApp` (formerly `App`) is a
  shortcut for a local script that serves models without a project (defaults for a database and
  sign-in). `AppDefinition` is the registration object of the older nested layout (below). In the
  CLI, "app" means an app package in `apps/`.
- **`Rule` and `State.Move`.** `evlet.Rule` is a permission rule (who may read, create, update).
  `State.Move` (formerly `State.Rule`) is a move between two steps of a `State`, passed in its
  `rules=(...)`.
- **`Table`, `ChildTable`, `TableSection`.** `Table(...)` is the field that holds a document's
  owned rows. `ChildTable` is its older, nested-layout equivalent; do not use it in new apps.
  `TableSection` places a `Table` in a record layout.
- **`Link`, `AnyLink`, `Ref`.** `Link('Customer')` points at a record of one model (a foreign
  key). `AnyLink()` (formerly `Reference`) points at a record of any model, without a foreign key.
  `Ref('customer.name')` is a path to a value, used in defaults and filters, not a field.
- **`Query`.** `evlet.Query` is a typed lookup that several derived fields share. The module
  `evlet.query` is internal.
- **`Response`.** `evlet.Response` is what a server frontend handler returns ([routing.md](routing.md)). `evlet.testing.TestResponse`
  is what a test client receives.

## What Evlet discovers

When the app is built, Evlet imports every module under `models/`, `reports/`,
`services/` and `migrations/`, and `workspaces.py`:

| You declare | Where | It becomes |
| --- | --- | --- |
| A `Model` class | `models/` (any file, any depth) | The table `notesitem`, the screens at `/app/notesitem` and the routes under `/api/notesitem` |
| A `Report` class | `reports/` | The report `notes.OpenItems` at `/reports/notes.OpenItems` ([reports.md](reports.md)) |
| `@api` and `@cron` functions, `Task` and `Schedule` objects | `services/` | Authenticated endpoints and scheduled work ([jobs-realtime.md](jobs-realtime.md#background-work-in-a-flat-app)) |
| A module with `migration = Migration(...)` | `migrations/` | One step of the app's migration history |
| `Workspace` objects | `workspaces.py` | Sidebar workspaces ([ui.md](ui.md#workspaces)) |

A model's table name is its lowercased class name, and its app-qualified id is
`notes.NotesItem`. To rename a class without changing either, set `storage_name = 'notesitem'`
and `resource_id = 'notes.NotesItem'` on it. Two classes with the same id, a broken field or an
invalid cron expression stop the build with a message.

## Permissions live on the model

A flat app's models deny every role they do not name. The starter declares its rules on the
class:

```python
from evlet import Model, Rule, Text

class NotesItem(Model):
    permissions = {
        'admin': Rule(('read', 'create', 'update', 'delete')),
        'editor': Rule(('read', 'create', 'update')),
        'viewer': Rule(('read',)),
    }
    title = Text(required=True, max_length=160)
```

Leave a role out and it cannot see the model at all. Rules can also restrict fields and rows
([business-capabilities.md](business-capabilities.md)). Owned child rows follow their
document's rules unless they declare their own.

## Changing an installed app

Any source change, even a label, needs `evlet migrate` before the site serves again: `serve`
refuses an app that differs from the instance's `apps.lock`. A change to stored fields also
needs a migration file:

```sh
evlet makemigration notes --instance acme --name done   # writes apps/notes/migrations/m0001_done.py
evlet migrate acme                                     # asks for trust, backs up, migrates
```

The models an app has when it is first installed on an instance need no migration: installing
creates their tables directly, on a new instance and on one that already has other apps, and
records the app's existing migrations without running them. Every change after that needs one,
including a new model.

Review the written file and never edit it once a site has applied it. `evlet migrate acme
--plan --trust` shows what is pending without writing anything. `evlet migrate` builds the app
itself; `evlet build --trust` only checks and builds it ahead of time, without touching any
instance. See [explicit migrations](model-engine.md#explicit-migrations) for the operations.

Under `evlet dev acme` (or `evlet dev --instance acme`), Python changes reload as you save. Stop it before running
`makemigration` and `migrate`.

## Older nested apps

`evlet app notes --legacy` still creates the older nested layout
(`apps/notes/notes/hooks.py`, `models/__init__.py`, `permissions.py`, `migrations/__init__.py`
wired through an `AppDefinition`). Existing nested apps keep working and keep their role
defaults instead of default denial. Use the flat layout for new apps. Moving an app from nested
to flat changes its source and needs a reviewed `evlet migrate`; keep its class and table names
and its applied migrations unchanged.

The design decisions behind this layout are recorded in [amendment-v2.md](amendment-v2.md).
