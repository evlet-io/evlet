# Instance sites

An instance can serve its own public websites, with no app package. A static frontend
([routing.md](routing.md#public-websites)) has to sit in an app package and changes with a
release of that app; a site belongs to one instance and changes when someone publishes it, from
the CLI, from Python or (in a later release) from the site tools. A site is served only on a
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
`0021_sites`) and a folder of the instance:

```text
instances/acme/sites/home/
  draft/                                 files written before publishing
  releases/20261126T101500123456Z/
    files/                               the published copy of the draft, read-only
    release.json                         who published it, when, its note and a manifest
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
| `publish(business, site, *, actor, note='')` | Publish the draft; returns the release |
| `rollback(business, site, release=None, *, actor)` | Serve an earlier release again; returns it |
| `list_releases(business, site)` | Releases, newest first: `release`, `files`, `bytes`, `published_at`, `published_by`, `note`, `current` |

These are trusted calls, like the hosts functions: they check what is written, not who writes
it. The HTTP and tool surfaces that call them check permissions first.

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

`evlet host add NAME HOST --root SITE --live` serves a site on a host.

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
