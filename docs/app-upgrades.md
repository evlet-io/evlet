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
`migrate` validates the current instance without making another full backup.

For an Evlet framework upgrade, update Evlet in the environment, check the app's
version constraints and release notes, then follow the same stopped-site backup,
migration and verification process. Main uses a development version between
releases; a source build is not the same artifact as the last PyPI release.

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

Changed installs save a timestamped SQLite backup under the instance's
`data/backups/`. Unchanged migrations do not add one. There is no retention command.
After verifying recovery, remove obsolete backup files according to your own
retention policy while keeping the source, lock and private files needed to restore
each retained snapshot. Never delete the active `data/database.sqlite3` file.
