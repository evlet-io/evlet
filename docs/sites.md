# Instance sites

An instance can serve its own public websites, with no app package. A static frontend
([routing.md](routing.md#public-websites)) has to sit in an app package and changes with a
release of that app; a site belongs to one instance and changes when someone publishes it, from
the CLI, from Python or, for an AI assistant, from the site tools of the instance MCP
([Site tools](#site-tools-and-manage-sites)). A site is served only on a
public sub-host of its own, a [host from data](routing.md#hosts-from-data) whose target is the
site's name.

```sh
evlet sites create acme home
evlet sites put acme home ./build          # a folder: its files keep their relative paths
evlet sites publish acme home --note "Launch"
evlet host add acme www.acme.com --root home --live
```

The host is served once its DNS check passes; the site is served on it from then on, and every
later publish or rollback reaches every process of the instance within about a second, without a
restart.

## What a site is

A site is a row of the framework table `evlet_sites` (added by the framework migration
`0021_sites`; `0022_manage_sites` adds the role flag Manage sites) and a folder of the instance:

```text
instances/acme/sites/home/
  draft/                                 files written before publishing
    _site/                               collections, templates and partials (never served)
  releases/20261126T101500123456Z/
    files/                               the published copy of the draft and the generated pages, read-only
    source/_site/                        the draft's _site/ folder as published, read-only, never served
    release.json                         who published it, when, its note, a manifest and what was generated
```

The row holds:

| Column | Meaning |
|---|---|
| `name` | The site name: lowercase letters, digits, `_` and `-`, starting with a letter; at most 63 characters. A host's target without a dot is a site name. |
| `routing` | `app` (default): every path without a file type gets `index.html`, so a single-page app routes in the browser. `pages`: `/about/` serves `about/index.html`, `/about` serves `about.html`, anything else gets the site's own `404.html` with status 404 ([routing.md](routing.md#multi-page-sites)). |
| `connect` | The https origins the pages may connect to, added to CSP `connect-src`. |
| `api` | The API scopes (`app.scope`, defined by installed apps) whose public endpoints the site may call, as `Frontend(api=...)` ([routing.md](routing.md#public-endpoints)). |
| `current_release` | The release served, or empty before the first publish. |
| `max_file_bytes`, `max_site_bytes`, `max_files` | The site's limits. |

## Files

Every file goes through the site store (`evlet.sites.SiteStore`):

- **Paths** are relative POSIX paths of at most 512 characters and 16 folders. Each segment uses
  letters, digits, `_`, `.` and `-` and never starts with a dot or a hyphen, so there are no
  dotfiles, no `..`, no absolute paths and no backslashes.
- **Types**: the static types a frontend serves: `.html`, `.css`, `.js`, `.json`, `.map`, `.svg`,
  `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.avif`, `.ico`, `.woff`, `.woff2`, `.txt`, `.xml`,
  `.webmanifest` and `.pdf`. Anything else is refused.
- **No links.** A symbolic link anywhere in a site folder is refused: a write through it, a read of
  it, and a publish of a draft that holds one (someone may have put it there from a shell). A
  release only ever holds regular files.
- **Limits** (defaults, per site): 10 MiB per file, 100 MiB for all draft files together, 5000
  files. `create_site` and `update_site` (`--max-file-bytes`, `--max-site-bytes`, `--max-files`)
  set others, up to 100 MiB per file, 1 GiB and 50,000 files. A write over a limit is refused
  and changes nothing; a batch (`write_files`, `evlet sites put` with a folder) is checked whole
  before its first file is written.
- Each draft file is written to a temporary name and renamed, so a reader never sees half a file.

## Publish and roll back

**Publish** copies the draft into a new release folder: the files are copied and made read-only
under a temporary name, then the finished folder is renamed into `releases/`. Only then does the
site's `current_release` column change, in the same transaction that bumps the hosts version row
(`evlet_hosts_version`). That column is the only pointer: no symbolic link and no pointer file is
used, and a release folder is complete before anything names it. Every process reads the version
row at most once a second and serves the new release when it moved. The draft stays as it is, so
the next change starts from it. Publishing needs an `index.html` in the draft.

**Releases never change** once published. Their id is the UTC time of the publish
(`20261126T101500123456Z`), so they sort by age. The newest 10 are kept; publishing removes older
ones, never the current one.

**Rollback** serves an earlier release again: the one before the current release, or the one
named. It changes only the pointer, so it is as fast as a publish and needs no copy.

## Collections

A site can have pages made from records: a blog post per `Post`, a product page per `Product`, a
list of them, and a JSON file for pages that render in the browser. The site declares them in
`_site/collections.json` in its draft, next to their templates:

```json
{"collections": [{
  "name": "posts",
  "model": "post",
  "filter": {"published": true},
  "order_by": "-published_on",
  "limit": 100,
  "fields": ["title", "slug", "summary", "published_on"],
  "page": "/blog/{slug}.html",
  "template": "post.html",
  "index": {"path": "/blog/index.html", "template": "blog.html"},
  "json": "/data/posts.json"
}]}
```

| Key | Meaning |
|---|---|
| `name` | The collection's name, unique in the site |
| `model` | The API name of a model (`/api/<model>`); child models and models the API does not serve are refused |
| `filter`, `order_by` | As the list route takes them (`filters`, `order_by`: [api.md](api.md)) |
| `limit` | Records read at most, 1 to 1000 (default 100) |
| `fields` | The record fields the templates and the JSON file get (default: every field the public role reads), plus `id` |
| `page` | One page per record. `{field}` takes the record's value, which must be a valid path segment; a record whose value is empty or isn't one is skipped and listed in the release's `collections` |
| `template` | The page's template, a file of `_site/` |
| `index` | One page with all of them: `{"path", "template"}` |
| `json` | One JSON file, `{"items": [...]}` |

A collection needs at least one of `page`, `index` and `json`. Paths may start with `/`, never
point into `_site/`, and follow the usual path and type rules. A generated file can't have the
path of a file in the draft; publishing says which.

**Publishing renders the collections into the release**, so a release is complete and immutable
like any other: rollback serves exactly what was published then. The `_site/` folder is published
with the release (`source/`) and is never served, on any host. `read_file(..., release=...)` of a
`_site/` path reads it there.

**Records are read as the `public` role**, through the same permission machinery as public API
reads: the role's Rule for the model, its row scope, its readable fields and `Meta.api`. A field
the public role can't read never reaches a template or a JSON file, even when a template names it,
and a `filter` or `order_by` on such a field refuses the publish. Without a Rule for
`('public', Model)` the publish is refused and says so:

```python
class Post(Model):
    permissions = {..., 'public': Rule(('read',), read_fields=('title', 'slug', 'summary', 'published_on'),
                                       scope=lambda user: {'published': True})}
```

Each page template gets the record's fields at the top level, `_url` (where the page is served:
`/blog/hello.html`, or `/blog/` for an `index.html`) and `collection`. An index template gets
`items` (the records, each with `_url` when the collection has pages), `count` and `collection`.
The JSON file holds the same records.

### Rendering again when records change

When a record of a model that a site's current release reads is created, changed or deleted, the
framework job `evlet_site_render` is queued for that site, 10 seconds out (`evlet.sites.DEBOUNCE`).
While it waits, more changes queue nothing more, so a burst of edits renders once. The job:

- renders the collections of the **current release** again, from that release's own files,
  templates and partials, never the draft: unpublished draft edits never go live because data changed;
- publishes the result as a **new release** (published by `system`, note
  `Pages rendered again from <release>`), audited under Sites, and served within a second;
- makes no release when every generated file comes out the same;
- never undoes a publish or rollback that happened while it rendered: it gives up, and the next
  change renders again.

Renders count against the newest 10 releases like publishes do, so a site whose data changes
often keeps fewer of its own publishes to roll back to; publish again to pin a version you want.
Changes are seen through the routes, tools, actions, jobs and document contexts, everything that
publishes a record event; writes that bypass them (raw Python sessions) do not queue a render.
The job needs the instance's job runner, like every framework job; without one, pages change on
the next publish. A collection that no longer renders (a removed field, the public Rule gone)
leaves the current release in place and the job logs why.

## Templates

Templates are logic-less (`evlet.templates`, standard library only):

| Tag | Does |
|---|---|
| `{{title}}`, `{{author.name}}` | The value, HTML-escaped. A dotted name reads into nested objects (a Link's `label`). Missing values, objects and lists show nothing; `true`/`false` show as words |
| `{{.}}` | The current item itself, inside `{{#each}}` over plain values |
| `{{#each items}}…{{/each}}` | Once per item; the item's keys come first, then the outer ones |
| `{{#if summary}}…{{/if}}` | Shown when the value is there: not missing, empty, `0`, `false`, `[]` or `{}` |
| `{{else}}` | Inside `#if` or `#each` (shown for an empty list) |
| `{{> header}}` | The partial `_site/partials/header.html`, with the same data |
| `{{! note }}` | A comment, left out |

Nothing else parses: no expressions, filters, helpers, calls or raw (unescaped) output. A name
only reads keys of the JSON-like data given, never attributes of Python objects, so a template an
agent writes cannot run anything. Templates are at most 256 KiB, sections nest at most 32 deep and
partials 8 deep; a render takes at most a million steps and writes at most the site's file limit.
Any other tag, or going over a bound, refuses the publish with the file and line.

## Serving

A site is served only on a host from data whose target is its name and whose status is `active`
([routing.md](routing.md#hosts-from-data)); only such a host gets 200 from `/_/tls-allowed`. The
primary host, `routes.py` sub-hosts and every other host never serve it, and the draft is never
served anywhere. A host is refused for a name that is not a site, and a site cannot be deleted
while a host names it.

The site's host is a public host: it serves the site's files and the public service endpoints of
the site's API scopes, and every other `/api/` path, `/_...` and `/.well-known/` is 404. Pages
carry no config block, and nothing a site serves holds the instance id, the Evlet version, the
release id or anything about who is signed in. Before the first publish the host answers 404.

| Response | Cache-Control | Other |
|---|---|---|
| A page (HTML) | `no-cache` | `ETag` of its bytes; a matching `If-None-Match` gets `304 Not Modified` with no body |
| A file with a content hash in its name | `public, max-age=31536000, immutable` | |
| Any other file, a 404 or an error | `no-store` | |

A name carries a content hash when the part after its last `-` or `.` before the type is 8 or
more letters, digits and `_` with at least one digit and one letter, as bundlers write them
(`index-3f9a1c2e.js`, `app.BdK3x9aQ.css`); `photo-20240101.png` and `logo-original.png` do not.
Give a file such a name only when its content never changes under it.

The Content-Security-Policy is the one of every Evlet page (`script-src 'self'`, no inline
scripts, `frame-ancestors 'none'`), with the site's `connect` origins added to `connect-src`.

## Python API

The functions in `evlet/sites.py` take the application's `Business`; the ones that change
something take an `actor` name too, and each such change is written to the audit log under
**Sites** (Controls, Audit). They raise `ValueError` with a message that says what to do.

| Function | What it does |
|---|---|
| `create_site(business, name, *, actor, routing='app', connect=(), api=(), max_file_bytes=None, max_site_bytes=None, max_files=None)` | Add a site with an empty draft |
| `update_site(business, name, *, actor, routing=None, connect=None, api=None, ...limits)` | Change its settings; `None` leaves one as it is |
| `list_sites(business)`, `get_site(business, name)` | Settings, current release, limits, `hosts` (hosts from data naming it), `served_on` (the active ones), `releases`, and `problem` when its current release is missing |
| `delete_site(business, name, *, actor)` | Remove the row, the draft and every release; refused while a host names it |
| `write_file(business, site, path, content, *, actor)`, `write_files(business, site, {path: content}, *, actor)` | Write draft files (text or bytes); returns `{path, size, sha256}` |
| `delete_file(business, site, path, *, actor)` | Remove a draft file |
| `list_files(business, site)`, `read_file(business, site, path, *, release=None)` | The draft's files; one file of the draft or of a release |
| `publish(business, site, *, actor, note='')` | Publish the draft, rendering its collections; returns the release (`generated`: how many files the collections wrote, `collections`: what each read and skipped) |
| `rerender(business, site, *, actor='system')` | Render the current release's collections again into a new release; `None` when nothing changed. The render job calls it |
| `manages_sites(business, user)` | Whether the user may manage sites ("Manage sites") |
| `rollback(business, site, release=None, *, actor)` | Serve an earlier release again; returns it |
| `list_releases(business, site)` | Releases, newest first: `release`, `files`, `bytes`, `published_at`, `published_by`, `note`, `current` |

These are trusted calls, like the hosts functions: they check what is written, not who writes
it. The HTTP and tool surfaces that call them check permissions first.

## Site tools and Manage sites

In production a site's content is managed through the instance MCP (`/_/mcp`) and Eva, with the
Tool Engine ([tools.md](tools.md#site-tools)):

| Tool | Kind | Calls |
|---|---|---|
| `site_list` | Read | `GET /api/_sites` |
| `site_files_list` | Read | `GET /api/_sites/<site>/files` |
| `site_file_read` | Read | `GET /api/_sites/<site>/file?path=...&release=...` |
| `site_files_write` | Write (create/change group) | `POST /api/_sites/<site>/files` `{"files": {path: text}}` |
| `site_publish` | Write, approved in Evlet | `POST /api/_sites/<site>/publish` `{"note"}` |
| `site_rollback` | Write, approved in Evlet | `POST /api/_sites/<site>/rollback` `{"release"}` |

They are listed, and the routes answer, only for people with **Manage sites**: admins, and desk
roles an admin turned it on for (Controls, Roles, or `PATCH /api/_controls/roles/<id>`
`{"manage_sites": true}`; [controls.md](controls.md#roles)). Anyone else gets 403 from every
`/api/_sites` route, whichever way they call it. The tools write text files only (up to 500 a
call); binary files go in with `evlet sites put`. A tool writes the draft; nothing is served
until `site_publish`, which waits for the person's click like other confirm-group changes.
The routes are on the instance's own hosts only; a site's host answers 404 for them.

## `evlet sites`

| Command | What it does |
|---|---|
| `evlet sites list NAME` | The sites with their routing, current release and the hosts serving them |
| `evlet sites create NAME SITE [--routing app\|pages] [--connect ORIGIN ...] [--api APP.SCOPE ...] [--max-file-bytes N] [--max-site-bytes N] [--max-files N]` | Add a site |
| `evlet sites set NAME SITE [same options] [--no-connect] [--no-api]` | Change a site's settings |
| `evlet sites put NAME SITE PATH [--as DRAFT_PATH] [--replace]` | Write a file, or every file of a folder, into the draft; dotfiles and dot folders are left out. `--replace` also removes draft files the folder does not have |
| `evlet sites files NAME SITE` / `evlet sites rm NAME SITE PATH` | List the draft / remove a draft file |
| `evlet sites publish NAME SITE [--note TEXT]` | Publish the draft |
| `evlet sites rollback NAME SITE [RELEASE]` | Serve the release before the current one, or the one named |
| `evlet sites releases NAME SITE` | The releases, newest first |
| `evlet sites delete NAME SITE [--yes]` | Delete a site |

`evlet host add NAME HOST --root SITE --live` serves a site on a host. `evlet sites publish`
renders the site's collections like every publish; `evlet sites put` writes `_site/` with the rest
of a folder (it is not a dot folder).

## Backups

`evlet backup` writes `<time>-sites.tar.gz` next to the database set when the instance has
sites (or puts `sites/` into `<time>-files.tar.gz` with `--with-files`), readable by its owner
only, links left out. The database names each site's current release, so keep the two together.
`evlet restore` replaces the database files only and leaves `sites/` as it is. A restored database
that names a release removed since lists it as a problem (`list_sites`) and its host answers 404
until you publish again or roll back; to put the folder back too, extract the archive into
`instances/<name>/` with the server stopped.

## Without sites

An instance without sites behaves as before: the framework migration adds an empty table, and no
folder is made until a site is created.
