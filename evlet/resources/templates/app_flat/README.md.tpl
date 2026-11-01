# {{title}}

This is a flat Evlet application package. Start with `models/item.py`.
Declare permissions on each model. Add reports under `reports/` and ordinary
helpers or explicit API/cron functions anywhere under `services/`.

From the containing project, build reviewed source with `evlet build --trust`.
Install it into a selected instance with
`evlet install {{name}} --instance INSTANCE --trust`.

Use explicit migrations for changes to an installed schema. Build does not
migrate or activate an instance. See the installed declarative app guide for
child tables, queries, dialogs, reports, native additions and upgrade rules.
