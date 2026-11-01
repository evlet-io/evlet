# Your first site

You need Python 3.12 or newer. These steps create a Notes app and a site called
Acme. The command-line tool calls a site an **instance**.

## 1. Install Evlet

Create a Python environment in a working folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade evlet
```

On Windows, use `py -3.12 -m venv .venv` and activate it with
`.venv\Scripts\Activate.ps1` in PowerShell. Use `python --version` to check the
active interpreter. Keep this environment active when running Evlet commands.

## 2. Create a project and app

```sh
evlet setup myproject
cd myproject
evlet app notes --legacy
```

The project has two folders: `apps/` for reusable app code and `instances/` for
sites. The new app lives in `apps/notes/`. It starts with an Item model containing
a required title. Creating the app does not create a database or run app code.

## 3. Create your site

```sh
evlet instance acme
evlet install notes --instance acme
```

Installation asks whether you trust the app. Python app code runs on your
computer, so review downloaded apps before accepting. For scripts, use `--trust`
after that review. Installation creates the site's database and records the app
version and contents in `instances/acme/apps.lock`.

## 4. Open it

```sh
evlet serve acme
```

Open **http://127.0.0.1:8000**. Create the first administrator using a username and
a password of at least 15 characters. Sign in, open **Items** under Models and
create a record. Docs is the first page after sign-in.

Press Control-C to stop the server. Run the same command to return to your site;
records, accounts and private files remain in `instances/acme/`.

## 5. Make a change

The live model is `apps/notes/notes/models/__init__.py`. Presentation settings such
as labels and `Meta.list_fields` live beside its fields. Use:

```sh
evlet dev --instance acme
```

Python changes reload while you work. A database field change also needs an
explicit migration. Follow [What you can customize](customizing.md) for a complete
example, then stop the server and run `evlet migrate acme` before restarting.

## Another site, same app

```sh
evlet instance beta
evlet install notes --instance beta
evlet serve beta --port 8002
```

Beta has its own administrator, database and files. It shares the Notes source
with Acme. Stop every affected site before changing shared app source for an
upgrade, then migrate each site. See [App upgrades](app-upgrades.md).

Use `evlet playground` for temporary examples and `evlet docs` for the installed
version's offline guides. Neither needs a project. The Playground is separate
from your sites and does not keep its database after it exits.
