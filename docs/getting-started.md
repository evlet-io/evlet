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

## In a hurry: one command

```sh
evlet setup myproject --app notes --instance acme --dev
```

This runs steps 2 to 5 below in one go: it creates the project, the Notes app and
the acme site, asks whether you trust the app, installs it and starts `evlet dev`
on 127.0.0.1 with a one-time sign-in link. If a step fails, the message names it.
Fix the cause and run the same command again; finished steps are skipped. Without
`--dev` it stops after installing and prints the command that starts the site.
Read on to see what each step does.

## 2. Create a project and app

```sh
evlet setup myproject
cd myproject
evlet app notes
```

The project has two folders: `apps/` for reusable app code and `instances/` for
sites. The new app is one Python package in `apps/notes/`. Its first model,
`apps/notes/models/item.py`, is an Item with a required title and the roles that
may use it. Creating the app does not create a database or run app code.
[The app layout](app-layout.md) describes each folder.

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

Every instance has a built-in Administrator (username `admin`) with no password, and
there is no default password. Set one (at least 15 characters by default), then serve:

```sh
evlet users set-password acme admin
evlet serve acme
```

If you skip the first command, `evlet serve` reminds you at startup:
`The built-in Administrator has no password yet. Set one with: evlet users set-password acme admin`.
(Under `evlet dev` in step 5 you need neither: it prints a one-time sign-in link that opens
"Set your password".)

Open **http://127.0.0.1:8000** and sign in as `admin`. You land on the first list,
**Items**; create a record there. Docs stays reachable from the navigation.

Press Control-C to stop the server. Run the same command to return to your site;
records, accounts and private files remain in `instances/acme/`.

## 5. Make a change

The model is `apps/notes/models/item.py`. Presentation settings such as labels and
`Meta.list_fields` live beside its fields. Use:

```sh
evlet dev --instance acme
```

Python changes reload while you work. At startup it prints a one-time sign-in link for the
built-in Administrator (`admin`; `--user NAME` signs in someone else). The Administrator has no
password at first, so the first link opens a "Set your password" screen. When admins need
two-step sign-in (`Security(admin_two_step=True)`, the default), you then set up your
authenticator app, as at a normal sign-in, and the app opens. After that, each link signs you
straight in with no password or code. Run `evlet dev-link` for a fresh link once one is used or
expired, and `evlet users set-password acme admin` to change the password from the shell
([details](cli.md#development-sign-in-links)). A database field change also needs an
explicit migration: stop the server, run `evlet makemigration notes --instance acme`,
review the file it writes, then `evlet migrate acme`. Follow
[What you can customize](customizing.md) for a complete example.

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
