# Changelog

Evlet is pre-1.0. A minor version (0.2, 0.3) may change public APIs; the notes below
say what to change. Evlet is not production ready.

## Unreleased

- **Pickers.** Select fields, and every select on the Settings and Controls pages, open Evlet's own list (current choice ticked, search for long
  lists, Clear for optional fields) instead of the browser's menu. Date, date-time, time and
  duration fields keep their typed input and add a calendar or steppers. List pages filter
  date fields by range, with presets.
- **Where a Currency field's code comes from.** `Currency(currency=...)` also takes
  `"link.field"` (a field of a linked record) and `"settings.app.key"` (an app setting). In
  `/api/schema`, `currency` now only ever holds a code; a field on the record (`"code_field"`)
  is sent as `currency_source: {"field": "code_field"}` instead of `currency: "code_field"`.
  A client that read the field name from `currency` should read `currency_source`.
- **Typed dates and times** are read the forgiving way: `120627`, `1/6/27`, `1206`, `today`,
  `+3`, `935`, `12.35pm`, `9am`, `120627 1235`, `90m`, in the day, month and year order of the
  person's date format. Impossible dates are refused, never guessed ([docs/ui-framework.md](docs/ui-framework.md#typing-dates-and-times)).
- Integer and Decimal fields show their `suffix` after the value at rest too (`5.00%`).
- The Link field's record mark no longer stretches across the input.
- **Form fields** are 40 px tall with a soft focus ring, a 14 px label and a blue required
  mark. `help_text` now shows under the input (an error takes its place). The new
  `tooltip=` field option puts its text behind a ? icon at the end of the input; Controls can
  change it and exports it.

## 0.2.0 (02-11-2026)

This release replaces the browser interface with Evlet's own UI framework, adds the
model engine, and adds Controls and Settings for administrators. It covers #30 to #63.

### Upgrading from 0.1.2

- **Apps must allow Evlet 0.2.** Apps declaring `evlet>=0.1.2,<0.2` refuse to load on
  0.2.0. Change the dependency to `evlet>=0.2.0,<0.3`. New apps get this by default.
- **SolidJS is gone.** The browser interface is Evlet's own UI framework
  (`frontend/src/evlet/`). The Vue harness, the Projects demo and the Python page renderer
  were removed from it. Python pages, native panels, report screens and form dialogs keep
  their Python APIs, routes and tests but are not shown in the browser
  ([docs/ui.md](docs/ui.md#not-rendered-by-this-interface)).
- **Security defaults changed.** Sessions last 12 hours (was 8) and sign-in locks after 5
  failures (was 10). Set `security = Security(session_hours=..., lockout=...)` in an
  instance's `config.py` to choose other starting values. Saved settings looser than the
  new values are clamped.
- **Two-step sign-in is enforced.** People in a role that requires two-step set it up at
  their next sign-in and can't use the app until they do. Admins should sign in right
  after upgrading. `evlet users reset-two-step` resets it from the shell.
- **New core dependency: `cryptography`.** It encrypts connector secrets, mailbox
  sign-ins and two-step secrets (AES-GCM). It installs automatically with Evlet.
- **Proxies must be listed.** `X-Forwarded-For` is only trusted from addresses in
  `Instance(trusted_proxies=...)`. Behind a reverse proxy, add it, or every visitor looks
  like the proxy. Controls warns when it sees forwarded headers from an unlisted proxy.
- **Run `evlet migrate`** for each instance after upgrading. Migrations never run from
  the browser.

### Model engine

- Expressions: automatic rounding to the target field, dates (`today()`, `days()`, date
  arithmetic), Link hops, `when(cond, a, b)`, with a TypeScript mirror in the browser.
- Rules: `initial`, `applies_when`, `required_when`, `locked_when`, `Recompute`
  migrations, and new field types (Phone, Percent, Time, Duration, Tags, Ref).
- Child tables (`Table`) with row limits, paging and batched loading, tested at 1,000 rows.
- States and moves: `State`, `Move`, locked and final states, asked values, `available_when`.
- Corrections: `void`, `revise`, and admin-only `unpost` and `repost` of posted documents.
  Unpost keeps the document number and cancels what was posted with reversing entries. A
  record that has ever posted can't be deleted.
- Effects: posting rows with `Effect`, `Swap` and `Negate`, balance checks and
  `evlet effects check`.
- Sequences: gapless numbering with period resets and scopes.
- Journal: an append-only record history with a hash chain and `journal verify`.
- Measures: totals and counts declared on models, served by one aggregate API.
- Meaning: `key_fields`, `relations`, `Meta.attachments` and `view_for`.
- Posted records are never recalculated by `Recompute` unless an admin runs
  `evlet migrate <instance> --recompute-posted`. Void records are always skipped.

### Browser interface

- Evlet's own UI framework: fine-grained reactivity, no virtual DOM, no runtime
  dependencies.
- A component library, and generated screens for every model: lists, boards, record
  pages, child tables, a command palette, actions and the journal.
- Login, Docs, the account menu and personal settings.
- Fonts are bundled under the OFL. The dark theme is hidden for now.
- testCRM (`examples/testcrm`), a CRM written as `models.py` only, used as the working
  example and end-to-end test.

### Routing and hosts

- A route table with reserved paths, `routes.py` and `evlet routes`.
- Sub-hosts, sign-in realms (desk, portal, public) and their cookies, API scopes, static
  and server frontends, and a customer portal through `evlet_user_links`.
- `evlet proxy` generates reverse-proxy configuration.

### Controls (administrators)

- Users, roles, permission overrides, an append-only audit log and a config version.
- Fields added from Controls (`x_` fields) are visible to every staff role by default.
  Portal roles see them only when an admin grants them.
- Naming series over the gapless sequence counters.
- Apps, routing, jobs and audit pages, and export of overrides as code.
- Opening Controls can be given to a role without making it admin. Only admins grant admin.
- An override that no longer validates denies access and is listed under Needs attention.
- Any change in Controls asks for a two-step code, valid for 10 minutes on that session.

### Settings and security

- Two-step sign-in (TOTP) with backup codes, checked per session.
- Sensitive personal settings ask for a two-step code, or the password when two-step is off.
- Ending access goes through one function. A password change and "Sign out everywhere"
  ask whether to also revoke API keys. A two-step reset, deactivation and break-glass
  always revoke them.

### Connectors and integrations

- Connectors with encrypted secrets, email accounts and personal API keys.
- Remote MCP for AI assistants, with OAuth 2.1 and PKCE.
- Personal mailboxes that link messages to records for models that opt in with
  `Meta.mail`.

### Fixes

- A dropped IMAP connection during sign-in no longer deletes the saved mailbox sign-in.
- The PostgreSQL CI job runs every test file that has PostgreSQL variants.
- Deleting a parent with more than 100 legacy child rows no longer fails.

## 0.1.2

Merged UI and CLI release. See the `archive/release-pypi-0.1.2` tag.
