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
| `/settings/appearance` | Theme (light; dark comes later), density, text size, start page, sidebar counts, language (Arabic is right-to-left), time zone, date format, number format, first day of the week | Instantly |
| `/settings/security` | Password, two-step sign-in, where you are signed in | On submit |
| `/settings/notifications` | Event by channel (in Evlet, email), quiet hours | Instantly |
| `/settings/email` | Personal mailboxes come in a later release; this page says so | |
| `/settings/ai` | The command line steps for an MCP assistant (`evlet login`, then `evlet mcp`) and the assistants signed in that way | |
| `/settings/app/<app>` | An app's settings: "For everyone" and "Just for you" | Save bar; instantly |

A search box above the page list finds any setting by its label, app settings included.

**Region everywhere.** Date format, number format and time zone change every list and form
through the UI runtime (`setRegion` in `model/format.ts`), not page by page. Typed dates and
numbers are read in the same format. The defaults are DD-MM-YYYY, 1,234.56 and Gulf
Standard Time.

**Photo.** PNG, JPG or WebP, 2 MB at most, checked by its first bytes. It is kept in the
instance's private file storage and served only to its owner at `GET /api/_settings/me/photo`.

**Email change.** `POST /api/_settings/me/email` records a one-time token (stored hashed in
`evlet_email_tokens`, 24 hours) and the address changes when its owner opens
`/settings/profile?confirm=<token>`. Outgoing mail is not set up yet, so the link is written
to the `evlet.settings` log; on a development instance the page also shows it.

**Password.** The current password is needed and the instance minimum applies
(`security.password_min`). Every other session of the person ends.

**Notifications.** Only the choices are stored for now. Security emails (a new device, a
changed password) can't be turned off.

## Two-step sign-in

Two-step sign-in uses an authenticator app (TOTP, 6 digits every 30 seconds) and 8 backup
codes that each work once.

- **Turning it on.** Settings shows a key to add to the app and asks for a code from it.
  The backup codes are shown once, after that.
- **Signing in.** After a correct password, `POST /api/auth/login` answers
  `{"two_step": "code", "ticket": ...}` with no session. The browser sends
  `{"ticket", "code"}` to the same route; a code from the app or an unused backup code
  starts the session. A ticket lasts 5 minutes and allows 5 tries. A code is accepted once.
- **Required roles.** Roles in Controls' `security.two_step_roles` (admin always) must use
  it. Someone in such a role who has not set it up gets `{"two_step": "setup", "ticket",
  "secret", "uri"}` and turns it on while signing in. They can't turn it off.
- **Controls.** `ControlsAccess(require_two_step=True)` opens Controls only to people who
  use two-step sign-in.
- **The command line.** `evlet login` asks for the code. Accounts that still need to set it
  up do that in the browser first.

The secret and the hashed backup codes are sealed with the instance's secret key
(`instances/<name>/secret.key`, created on first use, mode 0600) using AES-GCM from the
`cryptography` package, through `evlet.secrets.seal` and `open`. Without that package, or
on an application that is not an instance, two-step sign-in is unavailable: Settings says so,
it can't be turned on, and required roles sign in with their password.

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
| `GET /api/_settings/me` | Profile, preferences, notifications, security (two-step state, sessions), pages, apps with their labels, the last page, whether Controls opens |
| `PATCH /api/_settings/me/profile` | `full_name`, `short_name`, `title`, `phone`, `signature` |
| `PATCH /api/_settings/me/prefs` | Appearance and region keys, `notify.<event>` as `[in Evlet, email]`, `quiet` as `{on, from, to}`, `last_page` |
| `GET/POST/DELETE /api/_settings/me/photo` | `{"type", "data"}` with base64 data |
| `POST /api/_settings/me/email`, `POST /api/_settings/me/email/confirm` | Ask for a link; confirm with `{"token"}` |
| `POST /api/_settings/me/password` | `{"current", "new"}` |
| `POST /api/_settings/me/two-step`, `.../two-step/confirm`, `.../two-step/codes`, `DELETE .../two-step` | Start, confirm with `{"code"}`, new backup codes, turn off |
| `DELETE /api/_settings/me/sessions[/<id>]` | Sign out one other session, or every other one |
| `GET /api/_settings/app/<app>` | Sections with values and defaults, `can_edit`, and the person's preferences |
| `PATCH /api/_settings/app/<app>` | `{"values": {key: value or null}}`, editors only; one invalid value saves none |
| `PATCH /api/_settings/me/app/<app>` | The person's preferences in that app |

## Tables

```sql
evlet_user_profile(user_id PRIMARY KEY, full_name, short_name, title, phone, email, photo_file, signature,
                   two_step_secret BLOB, backup_codes TEXT, two_step_since, two_step_last, two_step_pending BLOB)
evlet_user_prefs(user_id, scope, key, value, PRIMARY KEY (user_id, scope, key))  -- scope 'core' or the app
evlet_email_tokens(token_hash PRIMARY KEY, user_id, new_email, expires)
evlet_app_settings(app, key, value, changed_by, changed_at, PRIMARY KEY (app, key))
evlet_two_step_tickets(token_hash PRIMARY KEY, user_id, realm, expires, attempts, secret BLOB)
```

`evlet_sessions` also records when each session started and its browser, for the sessions
list. SQLite and PostgreSQL both create these tables on start.

## Not built yet

The QR code for the authenticator key (the key is shown as text), the dark theme, personal
API keys, personal mailboxes, remote MCP with request counts, and sending notifications.
