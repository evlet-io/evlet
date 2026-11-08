# {{name}}

From the project root:

```sh
evlet instance demo
evlet install {{name}} --instance demo
evlet serve demo
```

Open http://127.0.0.1:8000 and create your administrator. The framework includes
the browser UI. No Node.js setup is needed.

Run `evlet dev --instance demo` for Python reloads. Model changes need an explicit
migration: stop the server, add a migration, then run `evlet migrate demo`.
Released migration definitions must remain frozen. Instance data is outside this
app, in `instances/demo/`. Never commit that data or secrets to the app repository.

`{{name}}/hooks.py` declares models, actions, permissions and migrations.
Read `evlet docs` for the package contract, dependencies, tasks and deployment.
Tests run with `python -m pytest` after installing pytest in your environment.
