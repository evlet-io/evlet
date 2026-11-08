# Upgrade or retire an app

Apps are shared source code; instances hold separate data. There is no automated
`upgrade`, `uninstall`, `restore` or backup-cleanup command yet. Use the explicit
workflow below.

## Upgrade an app

1. Stop every instance using the app and any separate workers. Review the new app
   version, its dependency changes and appended migrations.
2. Keep a copy of the project while it is stopped, including app source,
   `instances/*/config.py`, `apps.lock`, databases and private files. Keep the Python
   environment's dependency lock too. An automatic SQLite backup contains only
   database data, not uploaded files or app source.
3. Update the app's source to the reviewed revision. For an app fetched from Git,
   fetch the remote and check out the app's reviewed tag or commit. For example:

   ```sh
   git -C apps/notes fetch origin
   git -C apps/notes switch --detach v0.1.1
   ```

   This tag belongs to your app, not to Evlet. The scaffold has no Git repository
   until you create one. For a local app, make and review the source changes.
4. Install any changed Python dependencies in the same environment. `evlet app
   --get` fetches a new app; it does not overwrite or update an existing app.
5. Migrate every affected instance, then start and check them:

   ```sh
   evlet migrate acme
   evlet serve acme
   ```

   Migration asks for trust when app contents change. Reviewed automation can use
   `evlet migrate acme --trust`. Do not skip a failed migration or manually edit
   `apps.lock` to force startup.

A changed installation is staged and checked before publication. On a migration
failure the current database and lock remain intact. Restore the matching old
source before restarting an instance whose migration failed. An unchanged
`migrate`, with the same apps and the same Evlet version, validates the current
instance without making another full backup.

## Upgrade Evlet

Evlet's own tables (accounts, sessions, jobs, the journal and the rest) have a
version too. `apps.lock` and the database's installed manifest record the Evlet
version and the last framework migration an instance was migrated with:

```json
{"apps": {...}, "evlet": {"version": "0.3.0", "schema": "0005_unsaved_changes"}}
```

After you update Evlet in the environment, `evlet serve` and `evlet dev` refuse to
start an instance migrated with a different Evlet version, with a message that
names both versions and asks you to run `evlet migrate`. Locks written before
framework versioning have no `evlet` entry and are treated the same way. To upgrade:

1. Stop every server and worker for the instance. Check each app's Evlet version
   constraint and the Evlet release notes.
2. Keep the stopped-site copy described above.
3. Run `evlet migrate acme`. It backs up the database first, then on a staged copy
   runs app migrations, then pending framework migrations, then the additive
   startup tables. Only a fully successful run replaces the database and lock.
   The output lists the framework migrations it applied and the backup path.
4. Start the instance and check it.

Framework migrations are ordered, checksummed steps recorded in the
`evlet_framework_migrations` table with the Evlet version that applied them. The
first step, `0001_startup_tables`, adopts the tables that Evlet 0.2.0 and earlier
create at startup, so an existing instance needs no other change. Later steps are
where Evlet makes changes that can move or drop data. `0002_tool_calls` moves the
MCP call log (`evlet_mcp_calls`) into the Tool Engine's `evlet_tool_calls` and drops
the old table. `0003_record_names` gives every record table the standard `name` and
`title` columns ([model-engine.md](model-engine.md#name-and-title)) and fills each title
from the models; names stay empty for existing rows. `0004_record_times` adds the created and
modified time and user columns to every model table and fills them from the Journal or the record id
([model-engine.md](model-engine.md#record-times)). `0005_unsaved_changes` adds `evlet_unsaved`, the
unsaved changes people leave on saved records ([ui.md](ui.md#unsaved-changes)). Steps never run while an instance is served.
`evlet migrate acme --plan` lists the pending ones without writing anything. If a step fails, the staged copy is
discarded and the database and lock stay as they were; restore the earlier Evlet
before restarting.

A database migrated by a newer Evlet, or whose recorded framework migrations differ
from the installed Evlet, is refused. Install the Evlet release that migrated it.
Do not delete rows from `evlet_framework_migrations` or edit the `evlet` lock entry
to force startup.

Applications started outside an instance, such as `evlet playground`, tests and
an `Application` built in your own code, apply framework migrations themselves when they start, so
a new database needs no extra step. Processes that start together take turns; the
first applies the steps and the others find them applied. Do not keep data you care about in those.

Main uses a development version between releases; a source build is not the same
artifact as the last PyPI release.

### From 0.3 to 0.4

Evlet 0.4 removes code the browser never used. Check each app before you upgrade:

- Search the app for `evlet.ui` imports. Delete `PageView`, `ListView`, `FormView` and
  `ViewRegistry` definitions and move what they set into `Model.Meta`: `layout` with
  `Section(...)`, `list_fields`, `tabs`, `default_tab` and `open_in`. Controls, Export as code
  writes these for an instance's layout and list overrides.
- An `AppDefinition(views=views)` whose `views` is empty keeps working. Remove `ui=` and any
  non-empty `views=`; Evlet refuses them at start-up.
- Delete `.ts` and `.js` panel modules under `views/` and their `package-data` entry.
- Replace `from evlet.metadata import ...` with `from evlet.ui.metadata import ...`. That module
  is internal, so prefer `Registry.ui_schema()` or `/api/schema` where they cover the need.

## Retire an app

There is no supported in-place uninstall. Do not delete its source, remove entries
from `apps.lock`, drop its tables or remove its migration history by hand. Those
changes can break other apps and prevent startup.

You can keep the app installed and restrict its model access through permission
rules. If you need a site without it, create a new instance with only the retained
apps and use a tested application-specific export/import to move the records you
need. Verify links, accounts and private files before switching users. Preserve the
original instance and backups until the replacement has been checked.

## Move a project

Stop all servers and workers, copy the whole project, recreate its Python
environment and start the chosen instance from the new location. Local app lock
sources are project-relative, such as `apps/notes`; Git apps keep their remote URL
and commit. Ordinary folder moves do not need renewed trust.

Older locks used absolute local paths. Matching entries ending in `apps/<name>`
remain readable after a move. The next `evlet migrate <instance>` converts the lock
and database manifest together to the portable form. A source-content change still
requires trust. Keep `apps.lock` and its matching database together.

## Keep backups manageable

Changed installs and Evlet upgrades save a timestamped SQLite backup under the
instance's `data/backups/`. Unchanged migrations do not add one. There is no retention command.
After verifying recovery, remove obsolete backup files according to your own
retention policy while keeping the source, lock and private files needed to restore
each retained snapshot. Never delete the active `data/database.sqlite3` file.
