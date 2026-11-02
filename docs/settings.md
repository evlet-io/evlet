# Settings

`/settings` is the framework frontend `evlet.settings`: each person's own settings and the
settings of each app. It opens from the account area at the bottom of the sidebar. The top
bar keeps search and notifications only.

## Account menu

Clicking the name at the bottom of the sidebar opens, in this order: name and email, Your
profile, Appearance, Sign-in & security, All settings, Controls (only for roles that open
Controls; it opens in a new tab) and Sign out. All settings opens the last settings page the
person used, kept on the server: `GET /settings` redirects to `/settings/<last page>`, and
`/settings/profile` the first time.

## Pages

| Path | What it holds | Saves |
| --- | --- | --- |
| `/settings/profile` | Photo, full name, short name, job title, mobile, email signature, email (through a link) and the read-only username | Instantly |
| `/settings/appearance` | Theme (light; dark is shown as coming later), density, text size, start page, sidebar counts, language (Arabic is right-to-left), time zone, date format, number format, first day of the week | Instantly |
| `/settings/security` | Password, two-step sign-in, where you are signed in | On submit |
| `/settings/notifications` | Event by channel (in Evlet, email), quiet hours | Instantly |
| `/settings/email` | Your own mailbox: connect Microsoft 365, Google or another mailbox, send from it, link replies to records, sync and disconnect ([controls.md](controls.md#personal-mailboxes)). Desk only | Instantly |
| `/settings/ai` | The changes outside assistants are waiting for you to approve ([tools.md](tools.md#approvals)). Remote AI assistants: the `/_/mcp` address, your connected assistants with "Can make changes" and Disconnect, the apps they can use and your recent requests ([controls.md](controls.md#mcp-and-ai)); and, under On this computer, the command line steps (`evlet login`, then `evlet mcp`) with the sessions signed in that way. Desk only | Instantly |
| `/settings/keys` | Your API keys for scripts: create (shown once), scope and revoke ([controls.md](controls.md#api-keys)). Desk only | Instantly |
| `/settings/app/<app>` | An app's settings: "For everyone" and "Just for you" | Save bar; instantly |

A search box above the page list finds any setting by its label, app settings included.

**Region everywhere.** Date format, number format and time zone change every list and form
through the UI runtime (`setRegion` in `model/format.ts`), not page by page. Typed dates and
numbers are read in the same format. The defaults are DD-MM-YYYY, 1,234.56 and Gulf
Standard Time.

**Photo.** PNG, JPG or WebP, 2 MB at most, checked by its first bytes. It is kept in the
instance's private file storage and served only to its owner at `GET /api/_settings/me/photo`.

**Email change.** It needs a current code with two-step sign-in on, or the password without it.
`POST /api/_settings/me/email` records a one-time token (stored hashed in `evlet_email_tokens`,
24 hours) and the address changes when its owner opens `/settings/profile?confirm=<token>`.
Outgoing mail is not set up yet, so this works on development instances only: the page shows the
link and the `evlet.settings` log has it. An instance with a domain answers "Email is not set up
yet" and records and logs nothing.

**Password.** The current password is needed, plus a current code (`"code"`) when two-step
sign-in is on, and the instance minimum applies (`security.password_min`). Every other browser
session of the person ends, and so do their connected AI assistants and unused authorization
codes. API keys are kept unless the person ticks "Also revoke API keys (N active)", which sends
`"revoke_keys": true`; it is unticked by default and shown when they have active keys
([controls.md](controls.md#ending-access)). Wrong current passwords count against the account's
sign-in lockout (`security.lockout`, 5 unless config.py's `Security(...)` sets it).

**Start page.** Only Docs or a model the person can read.

**Theme.** The dark theme's styles and `applyTheme` stay in the frontend; only the choice is
hidden until it is ready, so everyone sees the light theme.

**Notifications.** Only the choices are stored for now. Security emails (a new device, a
changed password) can't be turned off.

## Two-step sign-in

Two-step sign-in uses an authenticator app (TOTP, 6 digits every 30 seconds, one step either
side) and 8 backup codes that each work once.

- **Turning it on.** Settings asks for the password (two-step is off, so there is no code yet),
  then shows a key to add to the app and asks for a code from it. The backup codes are shown
  once, after that.
- **Signing in.** After a correct password, `POST /api/auth/login` answers
  `{"two_step": "code", "ticket": ...}` with no session. The browser sends `{"ticket", "code"}`
  to the same route; a code from the app or an unused backup code starts the session. A ticket
  lasts 5 minutes and allows 5 tries; a code is accepted once. Wrong codes also count per
  account, from any network: after 10 in 15 minutes the answer is "Too many wrong codes. Try
  again in 15 minutes."
- **Verified sessions.** A session that finished the code step (or set the app up while signing
  in) is marked verified in `evlet_sessions.two_step`. Turning two-step on from Settings does not
  verify the session it was done from; signing in again does.
- **Required roles.** Roles in Controls' `security.two_step_roles` (admin always) must use it.
  Someone in such a role who has not set it up gets `{"two_step": "setup", "ticket", "secret",
  "uri"}` and turns it on while signing in. They can't turn it off. Until it is set up they can't
  use the app at all: a browser session of such a role that did not finish two-step sign-in (one
  from before the role required it, say) gets 401 "Your role needs two-step sign-in. Sign in
  again to set it up." on every route and page, so they sign in again and set it up then. API keys
  are not sessions and keep working.
- **Controls.** Controls opens only for verified sessions when `ControlsAccess(require_two_step=True)`
  is set or the person's role is in `security.two_step_roles` (when the instance can keep secrets).

### When the code is asked

With two-step sign-in on, the code is asked only:

- **at sign-in**;
- **before a change in Controls**, at most every 10 minutes per session. Every modifying request
  to `/api/_controls/*` checks when this session last gave a code (`evlet_sessions.two_step`
  holds that time; signing in with a code sets it). Older than 10 minutes, the answer is 403
  `VERIFY_TWO_STEP: Enter a code from your authenticator app to make this change.` The page then
  asks for a code, sends it to `POST /api/_settings/me/two-step/verify` `{"code"}` and sends the
  change again. Reading Controls never asks. An admin setting someone else's password counts as
  a Controls change. Only a verified session can be refreshed this way;
- **before changing your own password**, together with the current password;
- **for sensitive personal settings**: turning it off, making new backup codes, changing the
  email and creating a personal API key ask only a current code or a backup code, not the password.

Without two-step sign-in on, those personal settings and starting the set-up ask for the password
instead, and Controls changes ask for nothing more.
- **The command line.** `evlet login` asks for the code without showing it. Accounts that still
  need to set it up get "Set up two-step sign-in in the browser first".
- **Who set it up.** Each set-up writes an audit row and records when and from which kind of
  browser. Settings and Controls show it.

### Set-up at sign-in: trust on first use

Setting the app up while signing in means that whoever first signs in with a person's password
binds their own authenticator app. Someone who learned an admin's password before the admin
signed in after the upgrade could do that, and the admin would then be asked for a code they
don't have. Admins should sign in and set it up right after an instance upgrades. If an account
was taken this way, or a phone is lost:

- an admin uses **Reset two-step** on the user in Controls (audited), or
- on the server, `evlet users reset-two-step <instance> <username>` (audited as `cli`).

Both clear the app and the backup codes and revoke all the person's access: sessions, AI
assistants and API keys ([controls.md](controls.md#ending-access)). They set it up again at
their next sign-in (a required role) or in Settings.

### The secret key

`evlet.secrets` is the one place secrets are kept: connector secrets, two-step secrets, two-step
set-ups and sign-ins in progress, backup codes, personal mailbox secrets and mailbox sign-ins in
progress all use the same key and file, and
`evlet secrets rotate <instance>` re-encrypts all of them ([controls.md](controls.md#connectors-and-secrets)).

```python
from evlet import secrets
sealed = secrets.seal(key, b"value", "two_step:42")   # purpose binds the value to its place
secrets.open(key, sealed, "two_step:42")              # b"value"; another purpose fails
secrets.available(key)                                # cryptography installed, key configured and valid
secrets.sealed_exists(connection)                     # anything in secrets.SEALED holds a value
secrets.rotate(database_path, key)                    # (re-encrypted count, unreadable rows)
key = secrets.SecretKey(path)                         # .check(), .keys(), .write()
```

- Keys come from `EVLET_SECRET_KEY` (comma-separated) or `instances/<name>/secret.key`: one
  32-byte key per line in URL-safe base64. The file must be a regular file owned by the
  server's user with mode 0600; a link, another owner or a readable mode is refused. It is
  written atomically with `O_NOFOLLOW`.
- The first key seals; any listed key opens, so a rotation puts the new key first and keeps the
  old one until everything is sealed again. Two-step secrets (`two_step:<user id>`) and backup
  codes (`backup_codes:<user id>`) are sealed with their purpose.
- The key file is created on first use only while nothing is sealed yet. Once something is,
  a missing or changed key is never replaced: sign-in for people with two-step on, and set-up
  for required roles, answer 503 "Two-step sign-in is unavailable: the secret key is missing
  or changed. Ask your admin." Restore `secret.key` from the backup taken with the database.
- AES-GCM comes from the `cryptography` package, a core dependency. If it can't be imported, or
  on an application that is not an instance and passes no `secret_key`, two-step sign-in is unavailable: Settings says so, it can't be turned
  on, and required roles sign in with their password. The instance logs a warning on start
  when required roles exist but the package is missing or the key file is refused.

## App settings

Apps declare settings next to their models and register them in `AppDefinition`:

```python
from evlet import AppDefinition, AppSettings, Boolean, Decimal, Integer, MultiSelect, Select, UserPreferences

class BillingSettings(AppSettings):
    editors = ("accounts",)
    sections = {"Tax": ("vat", "rounding"), "Invoices": ("terms", "days")}
    vat = Decimal(scale=2, default="5", label="Default VAT rate", suffix="%")
    rounding = Select(["0.01", "0.25", "1"], default="0.01", label="Round invoice totals to")
    terms = Integer(default=30, minimum=0, label="Default payment terms", suffix="days")
    days = MultiSelect(["mon", "tue", "wed", "thu", "fri"], default=["mon", "tue", "wed", "thu", "fri"], label="Working days")

class BillingPreferences(UserPreferences):
    cc_me = Boolean(default=False, label="Copy me on invoices I send")

app = AppDefinition(models=(Invoice,), settings=BillingSettings, preferences=BillingPreferences)
```

An `Application` built directly registers them with
`app.business.settings.register("billing", BillingSettings, BillingPreferences, (Invoice,))`,
as the Playground does for Projects.

- Settings take the existing field classes and their validation. Calculated, link, reference,
  sequence and state fields are refused. `sections` names every field once; without it there
  is one "General" section.
- **For everyone** values are stored in `evlet_app_settings`. Admin and the roles in
  `editors` change them through the save bar; everyone else sees them read only with a lock
  note. Each change writes an audit row (area Settings) and raises the config version. A
  value equal to the default is removed, and a changed value shows its default with a reset.
- **Just for you** values are stored in `evlet_user_prefs` under the app's name and save at
  once.
- **Visibility.** A person sees an app's settings when they can read at least one of its
  models. Portal users never see app settings.
- **Server code.** `services.settings.get("billing", "vat")` and
  `services.preferences(user).get("billing", "cc_me")` return cleaned values (a `Decimal`,
  a tuple for `MultiSelect`). Instances pass `services` to an app's `actions(services)`
  factory; `app.business.settings.values` and `app.business.settings.preferences(user)` are
  the same objects. Shared values are read again only when the config version moves.

## Portal hosts

On a portal host `/settings` serves the portal subset: profile, password and notifications
(account events only). `/api/_settings/me/...` is served there; app settings, two-step
sign-in, appearance and the other pages answer 404.

## API

| Route | Purpose |
| --- | --- |
| `GET /api/_settings/me` | Profile, preferences, notifications, security (two-step state, sessions, active `api_keys`), pages, apps with their labels, the last page, whether Controls opens |
| `PATCH /api/_settings/me/profile` | `full_name`, `short_name`, `title`, `phone`, `signature` |
| `PATCH /api/_settings/me/prefs` | Appearance and region keys, `notify.<event>` as `[in Evlet, email]`, `quiet` as `{on, from, to}`, `last_page` |
| `GET/POST/DELETE /api/_settings/me/photo` | `{"type", "data"}` with base64 data |
| `POST /api/_settings/me/email`, `POST /api/_settings/me/email/confirm` | Ask for a link (`{"email", "code"}` with two-step on, `{"email", "password"}` without); confirm with `{"token"}` |
| `POST /api/_settings/me/password` | `{"current", "new", "code"?, "revoke_keys"?}`: `code` when two-step is on; `revoke_keys` defaults to false. Answers `{"changed", "revoked_keys"}` |
| `POST /api/_settings/me/two-step`, `.../two-step/confirm`, `.../two-step/codes`, `.../two-step/off` | Start (`{"password"}`), confirm with `{"code"}`, new backup codes and turn off (`{"code"}`) |
| `POST /api/_settings/me/two-step/verify` | `{"code"}`: a fresh code for this verified session, so changes in Controls need none for 10 minutes. Answers `{"verified", "until"}` |
| `DELETE /api/_settings/me/sessions[/<id>]` | Sign out one other session, or every other one |
| `GET/POST /api/_settings/me/keys`, `DELETE /api/_settings/me/keys/<id>` | Your API keys: list (with the models you can read), create `{"name", "scope", "code"}` with two-step on or `{"name", "scope", "password"}` without (the key is in this reply only), revoke. Desk sessions only |
| `GET /api/_settings/app/<app>` | Sections with values and defaults, `can_edit`, and the person's preferences |
| `PATCH /api/_settings/app/<app>` | `{"values": {key: value or null}}`, editors only; one invalid value saves none |
| `PATCH /api/_settings/me/app/<app>` | The person's preferences in that app |

## Tables

```sql
evlet_user_profile(user_id PRIMARY KEY, full_name, short_name, title, phone, email, photo_file, signature,
                   two_step_secret BLOB, backup_codes TEXT, two_step_since, two_step_last, two_step_pending BLOB, two_step_agent)
evlet_user_prefs(user_id, scope, key, value, PRIMARY KEY (user_id, scope, key))  -- scope 'core' or the app
evlet_email_tokens(token_hash PRIMARY KEY, user_id, new_email, expires)
evlet_app_settings(app, key, value, changed_by, changed_at, PRIMARY KEY (app, key))
evlet_two_step_tickets(token_hash PRIMARY KEY, user_id, realm, expires, attempts, secret BLOB)
```

`evlet_sessions` also records when each session started, its browser and when it finished
two-step sign-in. SQLite and PostgreSQL both create these tables on start.

## Not built yet

The QR code for the authenticator key (the key is shown as text), the dark theme, personal
API keys, personal mailboxes, remote MCP with request counts, and sending notifications.
