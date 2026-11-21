# {{title}}

This is a flat Evlet application package. Start with `models/item.py`.
Declare permissions on each model. Add reports under `reports/` and ordinary
helpers or explicit API/cron functions anywhere under `services/`.

From the containing project, install it into an instance with
`evlet install {{name}} --instance INSTANCE --trust` and develop with
`evlet dev INSTANCE`. Install and dev build the app themselves; `evlet build`
is optional.

Installing creates the app's tables from its models, on a new instance and on
one that already has other apps. After that, every model change needs a
migration: `evlet makemigration {{name}} --instance INSTANCE`, review the file,
then `evlet migrate INSTANCE`.

`tests/test_{{name}}.py` tests the app on a temporary instance with real SQLite
and the real HTTP API: `.venv/bin/python -m pytest apps/{{name}}/tests`. See the
testing guide (`evlet docs`, "Testing an app") and the app layout guide ("App
layout") for child tables, queries, dialogs, reports, native additions and
upgrade rules.
