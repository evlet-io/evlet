# Evlet

Evlet is a Python application framework. Define your models in Python and get
forms, lists, permissions and APIs in the browser. SQLite is the default;
PostgreSQL is optional.

## Install, create a site, open it

Use Python 3.12 or newer in a virtual environment:

```sh
python -m pip install --upgrade evlet
evlet setup myproject
cd myproject
evlet app notes
evlet instance acme
evlet install notes --instance acme
evlet serve acme
```

Read the trust prompt, then open **http://127.0.0.1:8000** and create your first
administrator. There is no default password. Your site keeps its data when you
stop and restart it.

An **app** contains reusable Python code. An **instance** is a site with its own
database, accounts and files. You can install the same app on several instances.
The package includes the browser UI, Playground and offline guides. Node is not
needed to create or serve Python-only apps. App-owned native TS/JS extensions
need Node 24 when building their assets.

The [declarative app guide](docs/amendment-v2.md) covers flat app packages,
owned child tables, reactive fields, dialogs, reports, services and native additions.
[Finite calculations](docs/finite-calculations.md) describe the finite-v1 formula
language, nested owned tables and the shared Python/browser calculation engine.
[States, moves and model actions](docs/states-actions.md) describe document states,
locks and actions with validated inputs. [Document numbers](docs/sequences.md) describe
`Sequence` fields and naming series. [Measures](docs/measures.md) describe exact aggregates, declared model
measures and their HTTP routes.
[Model meaning](docs/meaning.md) describes key fields, related records and
attachment limits.
[Effects](docs/effects.md) describe the rows a
document posts to other models when it enters a state.
[The Journal](docs/journal.md) describes each record's append-only
history, notes and follow ups, and its tamper-evident hash chain.

For virtual environment setup and each step explained, read
[Your first site](docs/getting-started.md).

## Build your app

- [What you can customize](docs/customizing.md): models, lists, pages, actions,
  permissions and migrations, with examples from the Notes starter.
- [CLI guide](docs/cli.md): projects, app sources, instances, trust and deployment
  boundaries.
- [Upgrade or retire an app](docs/app-upgrades.md): the supported workflow today,
  backups and moving a project.
- [Model engine](docs/model-engine.md) and [business capabilities](docs/business-capabilities.md):
  database queries, accounts, child rows, actions and private attachments.
- [UI guide](docs/ui.md): the browser interface, its routes and the Python page API.
- [Reports](docs/reports.md): filters, typed rows, totals, a chart, CSV export and print, in the browser and for Eva.
- [Slips](docs/slips.md): forms presented from code that save nothing until handed in.
- [Routing](docs/routing.md): frontends, reserved paths, `routes.py` and `evlet routes`.
- [API stability](docs/api-stability.md): the names apps can rely on, deprecations and what is internal.
- [UI framework](docs/ui-framework.md): Evlet's own browser framework and components.

`evlet dev --instance acme` reloads Python changes while you work. At startup it
prints a one-time sign-in link, so you can skip the sign-in form. Stop the server
and run `evlet migrate acme` when a model change needs a migration.

## Explore without a project

```sh
evlet playground
evlet docs
```

The Playground uses temporary demo data. Use an instance for records you want to keep. Offline documentation
opens at **http://127.0.0.1:8001**.

`evlet playground` prints a sign-in link when it starts:

```text
Sign in as demo-admin (works once, for 10 minutes):
  http://127.0.0.1:8000/_dev/signin?token=...
```

Open it to sign straight in. On an instance with no accounts, `evlet dev` first creates an
admin named `admin` (`--user NAME` picks another name) with a random password that is never
shown; on an empty Playground it is `demo-admin`. Set a real password later with
`evlet users set-password`. Run
`evlet dev-link` in another terminal for a fresh link once the first one is used or
expired. Use `--user NAME` to sign in as someone else, or `--no-link` to skip the
link. Links exist only under `evlet playground` and `evlet dev`, never under
`evlet serve`; see [development sign-in links](docs/cli.md#development-sign-in-links).

## Optional capabilities

PostgreSQL, S3 and PDF printing are available as `evlet[postgres]`, `evlet[s3]` and
`evlet[print]` ([print designs](docs/printing.md)). The existing
[tenant deployment](docs/tenancy-deployment.md), [jobs and live updates](docs/jobs-realtime.md),
[connector](docs/connector.md), [Tool Engine](docs/tools.md), [Eva](docs/eva.md), [PostgreSQL](docs/postgresql.md) and
[operations](docs/production-operations.md) guides describe their limits.
No database server, Redis, Docker or worker is required for a local SQLite app.

## Work on Evlet itself

See [the contributor guide](CONTRIBUTING.md) for a source checkout, frontend
builds and package checks. [Milestone notes](docs/internal/milestones.md) record the
completed backend work. Development builds use a `.dev0` version so they can be
distinguished from published packages. This branch contains unreleased fixes.

## License

Evlet uses the [MIT License](LICENSE).
