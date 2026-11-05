# Business capabilities

Milestone 4 adds local accounts, backend permissions, explicit actions, owned child
collections, private attachments, and grouped layouts. The Playground uses these
features for Projects. It is still a local development application.

## Accounts and sessions

Start the Playground and create its first administrator in the browser. There is
no default password. Initial setup is enabled only for this local example, accepts
loopback clients, and closes as soon as an account exists. Do not enable setup
behind a proxy. Administrators can add accounts, change roles, and disable accounts
from **Accounts**. The last active administrator cannot be disabled or demoted.

- **Admin:** record access and account administration.
- **Editor:** record access, including child rows, actions, and attachments.
- **Viewer:** read access, including attachment downloads.

Passwords must contain 15 to 128 characters. The standard-library scrypt hash uses
a random 16-byte salt, N=131072, r=8, and p=1. These parameters follow the
[OWASP password-storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
There are no new runtime dependencies.

Sessions last eight hours. Cookies are HttpOnly, SameSite=Strict, and Secure by
default. The Playground explicitly permits insecure cookies only for its loopback
HTTP origins. The database stores a SHA-256 session-token digest. Logout, password
changes, role changes, and disabling an account invalidate the affected sessions.
Sign-in attempts are limited to ten per client-network and username pair in 15
minutes, with a broader 100-attempt network cap. Routable IPv6 clients share their /64 prefix; IPv4 clients reached through a
dual-stack socket keep their own address, and loopback or link-local addresses are
never merged.
Success clears only that account's pair counter, not the network cap or other users'
counters. Password hashing admits at most two concurrent operations per process;
excess work gets HTTP 503 without waiting for a hashing slot, and a busy response
is not counted as a sign-in attempt. Proxy forwarding headers are not
trusted. There is no SSO, email recovery, MFA, or tenant account directory yet.

Use exact origins when configuring authentication:

```python
from evlet import Application, LocalAuth

auth = LocalAuth(database, origins=('https://app.example.com',))
app = Application(database, auth=auth)
```

The ASGI lifespan initializes account and attachment tables after the application
schema passes validation. Python `auth.create_user(username=..., password=...,
role='admin')` can provision an account after initialization. `auth.update_user(id,
{'password': ...})` supports a trusted local recovery command. Read passwords with
`getpass`; do not put real passwords in shell arguments, history, or source files.

`Application(database)` remains an unauthenticated development API for compatibility.
It accepts only loopback Host names by default and rejects foreign Origins.
A trusted development deployment can pass explicit Host header values, for example
`Application(database, hosts=('dev.example.test',))`; this does not enable
authentication or make the API suitable for public access.
Passing business features without authentication raises an error. The Playground
always configures authentication. It also passes `playground=True`, which only changes
the sign-in and sidebar labels. `GET /api/auth/session` reports it as
`application.playground`. Other applications leave it at the default `False`. Direct Python database sessions are trusted and
do not apply HTTP permissions.

### HTTP access

- `GET /api/auth/session` returns the signed-in user and CSRF token, or `user: null`.
- `POST /api/auth/setup` creates the first local administrator when explicitly enabled.
- `POST /api/auth/login` accepts username/password and issues a new session cookie.
- `POST /api/auth/logout` revokes the current session.
- Admin-only `GET/POST /api/auth/users` lists or creates accounts. Lists return
  items, total, offset, and limit; use `?offset=100` for the next page of 100.
- Admin-only `PATCH /api/auth/users/{id}` accepts role, active, or password.

All API routes except session discovery, setup, and login require a session. Health
is public and returns no record data. Mutations require `X-Evlet-Request: 1`, and
authenticated mutations also require `X-CSRF-Token` from session discovery or login.
The server checks Host and Origin against its configured origins, rejects cross-site
fetches, and does not enable CORS. This follows the
[OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
for custom headers, session-bound tokens, and cookie protections.

The local example permits localhost and 127.0.0.1 on ports 8000, 5173, and 4173.
Do not treat these development settings as a deployment configuration.

## Permissions

`Policy(registry)` grants Admin and Editor read/create/update/delete, and Viewer
read access. Override a role/model pair with an explicit `Rule`:

```python
from evlet import Policy, Rule

policy = Policy(registry, rules={
    ('editor', Customer): Rule(
        operations=('read', 'create', 'update'),
        read_fields=('customer_name', 'email', 'active'),
        write_fields=('customer_name', 'email'),
        scope=lambda user: {'active': True},
    ),
    ('viewer', Payment): Rule(operations=()),
})
app = Application(database, auth=auth, policy=policy)
```

A scope returns the same bounded typed filter object as an ordinary query, or one of
the team forms below. It is
combined with client filters using AND in SQL, before pagination and counting.
The same scope checks record reads, writes, actions, attachments, and referenced
Link values. Creates and updates that leave the allowed scope roll back. Write
permissions require read access. Omitted field sets mean all fields; empty tuples
mean no fields. `read_only` fields also reject HTTP writes.

Before any Rule, one framework rule decides what a model allows at all, so no Rule or
Controls override can widen it. Effect targets take no create, update or delete
([effects.md](effects.md#effect-rows-are-read-only)). While a request is served, a model with
`Meta.api = "read"` takes no write of any kind and a model with `Meta.api = None` takes nothing
([model-engine.md](model-engine.md#how-much-of-a-model-callers-reach)). The app code the request
runs (actions, moves, reactions, dialogs, services, jobs) is trusted and writes as usual, so a
posting action can be the only way into a ledger. A period lock (`Meta.period`) applies to
every writer, trusted code included ([model-engine.md](model-engine.md#closed-periods)).

### Team and hierarchy scopes

A filter object combines its filters with AND. Two scope forms cover "own records or the
team's" rules, such as employees, managers and HR on leave and payslips:

```python
from evlet import Boolean, Hierarchy, Link, Model, Rule, any_of

TEAM = Hierarchy('people.Employee', parent='reports_to')

class Employee(Model):
    permissions = {'viewer': Rule(('read',), scope=TEAM.team())}
    reports_to = Link('people.Employee', index=True)

class Leave(Model):
    permissions = {'viewer': Rule(('read', 'update'),
                                  scope=any_of(TEAM.team('employee'), {'public': True}))}
    employee = Link(Employee, index=True)
    public = Boolean(default=False)
```

- `Hierarchy(model, parent=...)` declares a tree of records of one model through a Link to
  the same model. `team(field='id', include_self=True)` is a row scope: rows whose `field`
  names the user's linked record of that model (`LocalAuth.link_user`), or any record below
  it at any depth. `field` is `id` on the hierarchy model or a Link (or Link path, such as
  `employee__reports_to`) to it. `include_self=False` keeps only the records below, such as a
  manager's reports' payslips without their own. A user with no linked record sees no rows.
- `any_of(*scopes)` allows a row when any of its scopes or filter objects allows it. An empty
  filter object among them allows every row.

The team is resolved inside one SQL statement with a recursive query (`WITH RECURSIVE`) on
SQLite and PostgreSQL, so it has no size limit and needs no Python lookups. The values bound
are the user's linked record IDs only. `UNION` stops at a cycle in the data. Policies check
each declared team when they are built: the parent must be a Link to the same model, and the
field must lead to it. Declare the parent Link with `index=True` so each level is an index
lookup; see [performance.md](performance.md#team-row-scopes) for plans and measurements.

These scopes apply wherever a filter-object scope does: lists, counts, search, detail, writes,
actions, attachments, related records, measures and aggregates, journal reads, realtime
events, Link values in responses, the Link filter guard, and filter lists with child table
groups (`EXISTS`) on list, aggregate and measures routes. HR roles without a scope see
every row. Controls offers an app-registered team or `any_of` scope for every model it compiles
for. The Playground's Employees show a manager, a team member and an outsider
(`examples/playground/team.py`).

A link decides what a team scope shows, so `LocalAuth.link_user` and `unlink_user` write each
change to the Controls audit log (area Users, with an `actor` argument; `evlet link-user` records
`cli`). A move in the tree is an ordinary edit of the parent Link: declare the hierarchy model with
`track_changes = True` to keep who moved whom and when, as the Playground's Employee does.

Operations may also name `print` (printing with a print design; admin has it by default, see
[printing.md](printing.md#permission)) and `void`, `revise`, `unpost` and `repost` on models that support them.
Admin always has them; naming one in a Rule adds that role. See the "Void and revise" and
"Unpost and repost" sections of [states-actions.md](states-actions.md).

Restricted fields are removed from responses, search, and schema metadata. Querying
or sorting on unavailable fields is rejected. Link IDs are returned as null when
the target record is outside the user's scope. Cross-model filter/order paths are
rejected by the authenticated API because they could expose another model's rows
or fields. Python queries retain relationship traversal. Direct Link filters are
also rejected when the target has a row scope. These restrictions are deliberate;
a permission-aware join planner is future work.

Child table filters (`{"child": "prices", ...}`, see [model-engine.md](model-engine.md#filter-lists-and-child-tables))
follow the same rules on the child rows: the role must be able to read the child model, every
field named in the group must be readable on it, and a child model with a row scope of its own
cannot be queried. An unknown field, a field the role cannot read, an unknown table and a table
the role cannot read all give the same 403 "Query field is unavailable", before any value is
compared. A restricted field is refused, never answered, so filters cannot be used to guess its
values. The parent's row scope, tenant and account checks apply as for any list.

A scope on the signed-in user pairs with `default=CurrentUser`, which fills a field from that
user when a record is created without a value: the username in a `Text` field, the account id
in an `Integer` field. Other field types refuse it. A value the caller sends wins and still has
to fit the scope. Updates never apply the default again. The schema marks the field
`default_kind: "user"` and the record view fills it in on a new record.

`CurrentUser` is only a default. A role that may write the field can still change it, on create
or later. For ownership, declare the field `read_only=True` (it is still filled on create), leave
it out of the role's `write_fields`, or rely on a row scope, which refuses a value outside it.

A `business.context()` needs a `User`: its reads and writes are checked against that user's
role, so a context opened with `None` refuses them with a plain `ValueError`.

```python
class Deal(Model):
    permissions = {'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'owner': user.username})}
    owner = Text(required=True, default=CurrentUser)
```

Schema permissions guide the UI. Backend checks remain authoritative. Hidden fields
and grouped layouts are presentation options, not access controls. A stale screen
cannot bypass a changed role; the next request ends the revoked session.

## Child tables and transactions

A child is an ordinary registered model with a required Link to its parent:

```python
from evlet import Boolean, ChildTable, Link, Model, Text

class ProjectTask(Model):
    project = Link(Project, required=True, index=True)
    title = Text(required=True, max_length=160)
    done = Boolean(required=True, default=False)

app = Application(database, auth=auth, children=(
    ChildTable(Project, 'tasks', ProjectTask, 'project', 'Tasks'),
))
```

Register ProjectTask with the same Registry and create its table through an explicit
migration. Child models do not have standalone HTTP CRUD routes or navigation.
Parent details include `children: {tasks: [...]}`. Parent POST/PATCH accepts that
same structure. Omit children to preserve them; include a collection to replace its
rows. Existing rows carry their IDs, new rows omit IDs, and omitted existing IDs
are deleted. An existing row containing only its ID is retained with read access and is not
updated. This lets create-only or delete-only users preserve other rows in the
collection. Sending changes to an existing row still requires update access.
Clients cannot set the parent Link, move rows between parents, or repeat a row ID. A collection contains at most 100 rows. Nested child collections
are not supported. Declarative document models also accept change sets
(`create`, `update`, `delete`) for these collections. Each schema `children` entry says
which with `row_changes` (true on document models, false here); see
[owned tables](finite-calculations.md#owned-tables). The form checks create permission for new-row inputs, update
permission for existing-row inputs, and delete permission for removing saved rows.
An unsaved row can be discarded without delete permission.

Parent and child changes commit in one SQLite transaction. Validation, permission,
or foreign-key errors roll back all changes. Child model/field/row permissions also
apply. Parent deletion removes owned children only when the user can delete each
row. Ordinary Links outside the collection still restrict deletion. Concurrent
collection replacements use the last successful write; there is no optimistic
version conflict UI in this milestone.

## Registered actions

```python
from evlet import Action, ValidationError

def start(tx, user, project):
    if project.status != 'planned':
        raise ValidationError({'_form': 'Only a planned project can be started'})
    tx.update(Project, project.id, status='active')

app = Application(database, auth=auth, actions=(
    Action(Project, 'start', 'Start project', start),
))
```

`POST /api/project/{id}/actions/start` accepts an empty JSON object. Only registered
actions are callable. Action roles default to Admin and Editor; the caller also
needs update access to the parent within its row scope. The handler receives the
transaction, authenticated user, and current parent record. All its writes commit
together or roll back. The response is a fresh, permission-filtered parent detail.

Handlers are trusted application code and can write fields that normal forms cannot.
They must implement the business rules and any additional permissions for other
records they touch. They must not call external services inside the transaction.
Do not expose an arbitrary method dispatcher.

Model actions declared with `@action` on a document may also take validated `inputs`
and declare `available_when`. A `State` field with `Move` declarations changes a
document's state through `POST /api/{model}/{id}/moves/{to}` and can lock it against
ordinary writes. See [states-actions.md](states-actions.md).

The Playground's Start action moves a planned Project to active. Complete requires
an active Project with at least one task and every task done, then writes status
`done` and progress `100` together. Status is read-only in ordinary HTTP writes.
This is a project workflow, not accounting posting. Completion checks the task
snapshot in that transaction; it does not freeze subsequent Project or task edits.

## Private attachments

Enable attachments explicitly with `attachments=(Project,)`. The detail panel lets
readers download files and users with update access upload or remove them.

`GET/POST /api/project/{id}/attachments` lists or uploads files. Upload JSON has
`name` and a base64 `content` value. `GET/DELETE .../attachments/{attachment_id}`
downloads or removes a file. Each file is nonempty and at most 1 MiB; each record
has at most ten files. Filenames cannot contain paths or control characters.
`Model.Meta.attachments = Attachments(max_files=..., max_size=...)` also enables
attachments and lowers these limits per model; see [meaning.md](meaning.md#attachments).

Content is stored as a BLOB in the same SQLite database. There is no public file
folder or filesystem path supplied by the client. Download and deletion check both
the owner record and attachment ID. Downloads use application/octet-stream, an
attachment disposition, nosniff, and a restrictive content security policy. Parent
deletions remove attachments in the same transaction. Back up the entire database
to preserve accounts, records, and files together. Virus scanning and external/object
storage are not implemented.

## Images in rich text

A model with a `RichText` field takes images in its text, without enabling attachments. Each image
is a private attachment of the record (`evlet_attachments` with purpose `image`), and the stored
HTML holds only its id: `<figure class="rt-md"><img data-attachment="12" alt="..."></figure>`, never
a URL or a `data:` URI ([internal/rich-text.md](internal/rich-text.md)).

- `POST /api/{model}/{id}/attachments` with `{"name", "content", "purpose": "image"}` adds an image
  to a saved record; it needs update access and write access to one of its rich text fields.
  `POST /api/{model}/_images` holds an image for a record not saved yet, for the person who added
  it (`GET /api/{model}/_images/{image id}` shows it to them only); it needs create access.
- The type comes from the bytes: PNG, JPEG, GIF or WebP. SVG and anything else are refused. The
  browser shrinks images to at most 2,000 px on the long side and re-encodes them before upload.
  Size and count limits are Controls settings (`files.image_max_kb`, default 2 MiB, at most 8 MiB;
  `files.images_per_record`, default 50, at most 200), separate from the file limits.
- `GET /api/{model}/{id}/attachments/{image id}` serves an image inline with its own type,
  `nosniff`, a sandbox policy and private caching, to whoever can read the record. Images do not
  appear in the record's file list or count towards its files.
- Saving a record links the held images its rich text refers to and deletes the record's images no
  rich text refers to any more, in the save transaction. A reference to another record's image, to
  someone else's held image or to a purged one is refused with a field error. Images belong to the
  record's own fields; a child table row's rich text takes none.
- Held images nobody saved are purged after 24 hours: on every upload, and by the hourly framework
  task `evlet_image_purge` when the application runs jobs.
- Print Design embeds a record's images in its PDF, and `Mailboxes.send_mail(..., html=..., record=...)`
  sends them inline (`cid:`), never as links.

## Layout metadata

`Model.Meta.layout` groups visible fields without referring to browser components:

```python
class Meta:
    layout = (
        ('Project', ('title', 'customer', 'status')),
        ('Planning', ('budget', 'progress', 'starts_on')),
        ('Notes', ('notes',)),
    )
```

Use at most eight groups and list each visible field at most once. Visible fields a
layout leaves out follow in an Other group, in declaration order. A field listed twice,
or an unknown or hidden name, fails registration with a message naming it. The default
is one Details group. The UI schema retains version 1 with additive layout,
permissions, actions, children, child_only, and attachments metadata. Presentation
changes do not alter migration checksums.

## Upgrade and validation

Stop the local servers and run `.venv/bin/python -m examples.playground.migrate`.
The command backs up an existing database before applying `0003_project_tasks`.
Account/session/attachment tables use the reserved `evlet_` prefix and are created
on authenticated startup. Existing records are not assigned to users or rewritten.

The smoke command prompts for an existing Admin or Editor account. CI uses
`scripts/smoke.py --setup` against a fresh disposable database, generates a temporary
password internally, and never prints it. That option refuses a database with an
existing account. Its account is retained only in that disposable test database.

Tenant routing, hosting, custom domains, storage adapters, jobs, realtime, external
integrations, and production hardening remain later milestones. Nothing in this
milestone merges or deploys the application.

Child collection replacement applies to the rows the user can see. Hidden rows
are preserved; submitting a hidden child ID is rejected. Deleting a parent still
requires permission to delete every owned child.
