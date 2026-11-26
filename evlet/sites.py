"""Instance sites: public websites an instance keeps itself, with no app package (docs/sites.md).

A site is a row of evlet_sites (its routing, CSP origins, public API scopes, current release and limits) and
a folder, instances/<name>/sites/<site>/:

    draft/                     files written before publishing (write_file, delete_file); draft/_site/ holds
                               the collections, templates and partials (evlet.site_pages)
    releases/<release>/files/  one published copy of the draft and the pages its collections generated, never
                               changed again
    releases/<release>/source/ the draft's _site/ as published, never served
    releases/<release>/release.json

Publishing copies the draft into a new release folder, then switches the site's current_release column in the
same transaction that bumps the hosts version row, so every process serves the new release within a second.
That column is the only pointer: a release folder is complete before it is named, and no symbolic link is
used anywhere. Rollback switches the column back. The newest KEEP releases are kept, and the current one always.

A site is served only on a host from data whose target is its name (evlet.hosts), with status active: the
primary host and every other host never serve it. Each change is audited under Sites.

When a record of a model the current release's collections read changes, the evlet_site_render job renders the
release again (rerender) into a new one, DEBOUNCE seconds later. The /api/_sites routes (route) and the site
tools (evlet.tools) need "Manage sites" (manages_sites).
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import time

from . import qb
from .adapter import Column, TableDefinition

ROUTINGS = ('app', 'pages')
SITES = TableDefinition('evlet_sites', (
    Column('name', 'TEXT', primary=True),
    Column('routing', 'TEXT', required=True, default='app'),
    # https origins the pages may connect to, space-separated (Frontend csp connect-src).
    Column('connect', 'TEXT', required=True, default=''),
    # The API scopes ("app.scope") whose public endpoints the site may call, space-separated (Frontend api).
    Column('api', 'TEXT', required=True, default=''),
    Column('current_release', 'TEXT'),
    Column('max_file_bytes', 'INTEGER', required=True),
    Column('max_site_bytes', 'INTEGER', required=True),
    Column('max_files', 'INTEGER', required=True),
    Column('created_at', 'INTEGER', required=True),
    Column('created_by', 'TEXT', required=True),
    Column('changed_at', 'INTEGER', required=True),
), checks=(qb.in_('routing', ROUTINGS),))

# Default limits of a site; create_site and update_site may set others, up to the ceilings.
MAX_FILE_BYTES = 10 * 2**20
MAX_SITE_BYTES = 100 * 2**20
MAX_FILES = 5000
CEILINGS = {'max_file_bytes': 100 * 2**20, 'max_site_bytes': 2**30, 'max_files': 50000}
# Releases kept per site; older ones are removed when a new one is published (never the current one).
KEEP = 10
# The file types a site may hold: the static types a frontend serves (evlet.ui.serving.FRONTEND_TYPES).
TYPES = frozenset(('.html', '.js', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.ico', '.woff',
                   '.woff2', '.json', '.map', '.txt', '.xml', '.webmanifest', '.pdf'))
# One path segment: letters, digits, _ . -, never starting with a dot (no dotfiles) or a hyphen.
SEGMENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}')
MAX_PATH, MAX_DEPTH = 512, 16
ORIGIN = re.compile(r'https://[a-z0-9.-]+(:[0-9]+)?')
DRAFT, RELEASES, FILES, SOURCE, RECORD = 'draft', 'releases', 'files', 'source', 'release.json'
RELEASE = re.compile(r'[0-9]{8}T[0-9]{12}Z')
# Collections (evlet.site_pages): the declaration in a release's sources, and the job that renders them again.
DECLARATION = '_site/collections.json'
RENDER_TASK, RENDER_ACTOR = 'evlet_site_render', 'system'
# Seconds a change waits before its site renders again, so a burst of changes renders once.
DEBOUNCE = 10
# The text a file read through the site routes returns at most.
TEXT_LIMIT = 200_000
logger = logging.getLogger('evlet.sites')


def ensure(connection):
    """The framework step 0021_sites: the sites table."""
    connection.ensure_table(SITES)


# ---------- the file store ----------

def check_path(path):
    """path as a validated relative POSIX path of a site file, or ValueError."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        raise ValueError(f'A site file path is a relative path of at most {MAX_PATH} characters, such as about/index.html')
    if path.startswith('/') or '\\' in path or '\x00' in path:
        raise ValueError(f'{path!r} must be a relative path with forward slashes, such as css/site.css')
    parts = path.split('/')
    if len(parts) > MAX_DEPTH:
        raise ValueError(f'{path!r} is more than {MAX_DEPTH} folders deep')
    for part in parts:
        if part in ('', '.', '..'):
            raise ValueError(f'{path!r} must not hold empty, . or .. segments')
        if not SEGMENT.fullmatch(part):
            raise ValueError(f'{path!r}: each segment uses letters, digits, _ . and -, and never starts with a dot or a hyphen')
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in TYPES:
        raise ValueError(f'{path!r} is not a file type a site serves. Use one of: ' + ', '.join(sorted(TYPES)))
    return path


def _digest(content):
    return hashlib.sha256(content).hexdigest()


class SiteStore:
    """The files of one site: a draft folder and immutable release folders. Paths go through check_path; links,
    dotfiles and other types are refused, and the site's limits apply to every write."""

    def __init__(self, folder, *, max_file_bytes=MAX_FILE_BYTES, max_site_bytes=MAX_SITE_BYTES, max_files=MAX_FILES):
        self.folder = Path(folder)
        self.max_file_bytes, self.max_site_bytes, self.max_files = max_file_bytes, max_site_bytes, max_files

    @property
    def draft(self):
        return self.folder / DRAFT

    @property
    def releases(self):
        return self.folder / RELEASES

    def _no_link(self, target, stop):
        """Refuse a symbolic link at target or any folder between it and stop."""
        item = target
        while True:
            if item.is_symlink():
                raise ValueError('Site folders and files cannot be symbolic links')
            if item == stop or item == item.parent:
                return
            item = item.parent

    def _folder(self, name):
        """A folder of the store, made when missing; never a link."""
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._no_link(self.folder, self.folder)
        target = self.folder / name
        if target.is_symlink():
            raise ValueError('Site folders and files cannot be symbolic links')
        target.mkdir(mode=0o700, exist_ok=True)
        return target

    def _walk(self, root):
        """(path, Path) of the regular files under root. Raises ValueError on a link, a dotfile, or a file whose
        path or type a site may not hold; a draft write that did not finish (.tmp-...) is skipped."""
        if not root.is_dir():
            return
        self._no_link(root, self.folder)
        for directory, folders, names in os.walk(root, followlinks=False):
            base = Path(directory)
            folders.sort()
            for name in sorted(folders) + sorted(names):
                item = base / name
                relative = item.relative_to(root).as_posix()
                if item.is_symlink():
                    raise ValueError(f'{relative} is a symbolic link; a site holds regular files only')
                if name in folders:
                    if not SEGMENT.fullmatch(name):
                        raise ValueError(f'{relative} is not a folder name a site may hold')
                    continue
                if name.startswith('.tmp-') and root == self.draft:
                    continue
                if not item.is_file():
                    raise ValueError(f'{relative} is not a regular file')
                yield check_path(relative), item

    def sizes(self, root=None):
        """{path: size} of the draft's files (or root's)."""
        return {path: item.stat().st_size for path, item in self._walk(self.draft if root is None else root)}

    def files(self, root=None):
        """{path: (size, sha256)} of the draft's files (or root's), by path."""
        found = {}
        for path, item in self._walk(self.draft if root is None else root):
            content = item.read_bytes()
            found[path] = (len(content), _digest(content))
        return dict(sorted(found.items()))

    def read(self, path, release=None):
        """A draft file's bytes, or a release's: a path in _site/ reads the release's sources."""
        from .site_pages import is_source
        check_path(path)
        if release is None:
            root = self.draft
        else:
            root = self.release_source(release) if is_source(path) else self.release_files(release)
        target = root / path
        self._no_link(target, self.folder)
        if not target.is_file():
            raise ValueError(f'{path} is not a file of the {"draft" if release is None else "release " + release}')
        return target.read_bytes()

    def write(self, path, content):
        """Write one draft file atomically. Returns (size, sha256)."""
        return self.write_many({path: content})[path]

    def write_many(self, contents):
        """Write draft files ({path: text or bytes}), each atomically, after checking every path and the limits for
        all of them; nothing is written when one is refused. Returns {path: (size, sha256)}."""
        if not isinstance(contents, dict) or not contents:
            raise ValueError('Give the files to write as {path: content}')
        encoded = {}
        for path, content in contents.items():
            check_path(path)
            if isinstance(content, str):
                content = content.encode('utf-8')
            if not isinstance(content, (bytes, bytearray)):
                raise ValueError('Site file content is text or bytes')
            if len(content) > self.max_file_bytes:
                raise ValueError(f'{path} is {len(content)} bytes; a file of this site holds at most {self.max_file_bytes}')
            encoded[path] = bytes(content)
        draft = self._folder(DRAFT)
        current = self.sizes()
        after = {**current, **{path: len(content) for path, content in encoded.items()}}
        if len(after) > self.max_files:
            raise ValueError(f'This site holds at most {self.max_files} files')
        if sum(after.values()) > self.max_site_bytes:
            raise ValueError(f'This site would hold {sum(after.values())} bytes; it holds at most {self.max_site_bytes}')
        for path in encoded:
            for prefix in PurePosixPath(path).parents:
                if prefix.as_posix() in after:
                    raise ValueError(f'{prefix} is a file, so {path} cannot be inside it')
            if (draft / path).is_dir():
                raise ValueError(f'{path} is a folder of the draft')
        written = {}
        for path, content in encoded.items():
            target = draft / path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._no_link(target, draft)
            temporary = target.with_name('.tmp-' + os.urandom(6).hex())
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            written[path] = (len(content), _digest(content))
        return written

    def delete(self, path):
        check_path(path)
        target = self.draft / path
        self._no_link(target, self.folder)
        if not target.is_file():
            raise ValueError(f'{path} is not a file of the draft')
        target.unlink()
        # Folders the delete left empty go too, so the draft lists what it holds.
        parent = target.parent
        while parent != self.draft and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    def release_files(self, release):
        if not isinstance(release, str) or not RELEASE.fullmatch(release):
            raise ValueError(f'{release!r} is not a release id')
        return self.releases / release / FILES

    def release_source(self, release):
        """The folder of a release's _site/ sources (collections, templates, partials), never served."""
        return self.release_files(release).parent / SOURCE

    def release_ids(self):
        """Release ids, oldest first (they sort by the time they were published)."""
        if not self.releases.is_dir():
            return []
        return sorted(item.name for item in self.releases.iterdir()
                      if RELEASE.fullmatch(item.name) and item.is_dir() and not item.is_symlink())

    KEYS = ('release', 'files', 'bytes', 'published_at', 'published_by', 'note', 'generated', 'collections')

    def release_record(self, release, *, full=False):
        """A release's record (full: with its manifest, sources and generated paths)."""
        try:
            record = json.loads((self.releases / release / RECORD).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'release': release, 'files': None, 'bytes': None, 'published_at': None, 'published_by': None, 'note': '',
                    'generated': 0, 'collections': []}
        if full:
            return record
        return {**{key: record.get(key) for key in self.KEYS}, 'generated': len(record.get('generated') or ()),
                'collections': record.get('collections') or []}

    def _sources(self, base):
        """{path: Path} to publish from: the draft, or (to render again) the release base without its generated files."""
        if base is None:
            return {path: item for path, item in self._walk(self.draft)}
        record = self.release_record(base, full=True)
        generated = set(record.get('generated') or ())
        found = {path: item for path, item in self._walk(self.release_files(base)) if path not in generated}
        found.update((path, item) for path, item in self._walk(self.release_source(base)))
        return found

    def snapshot(self, *, actor, note='', render=None, base=None):
        """Copy the draft (or the release base) into a new, complete release folder and return its record. Nothing
        points at it yet. Files in _site/ go to the release's sources, never served; render(read) returns the
        generated files ({path: bytes}) and what the collections wrote, from read(path) -> bytes or None.

        With base, the release is the base's own files and sources with its pages rendered again, and None is
        returned when they render exactly as the base has them."""
        from .site_pages import is_source
        sources = self._sources(base)

        def read(path):
            item = sources.get(path)
            return None if item is None else item.read_bytes()

        generated, collections = render(read) if render is not None else ({}, [])
        served = {path: item for path, item in sources.items() if not is_source(path)}
        clash = sorted(set(served) & set(generated))
        if clash:
            raise ValueError(f'{clash[0]} is a file of the site and a page a collection writes; remove one of them')
        if 'index.html' not in served and 'index.html' not in generated:
            raise ValueError('Write index.html to the draft before publishing; a site serves it at /')
        if base is not None:
            record = self.release_record(base, full=True)
            before = {path: record['manifest'].get(path, {}).get('sha256') for path in record.get('generated') or ()}
            if before == {path: _digest(content) for path, content in generated.items()}:
                return None
        sizes = {path: item.stat().st_size for path, item in sources.items()}
        total = sum(sizes.values()) + sum(len(content) for content in generated.values())
        if len(sources) + len(generated) > self.max_files or total > self.max_site_bytes:
            raise ValueError(f'The release would hold {len(sources) + len(generated)} files and {total} bytes, over the limits of this site')
        releases = self._folder(RELEASES)
        last = (self.release_ids() or [''])[-1]
        release = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        while release <= last or (releases / release).exists():
            time.sleep(0.001)
            release = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        stage = releases / ('.tmp-' + release)
        manifest, source = {}, {}
        try:
            def put(folder, path, content, into):
                target = stage / folder / path
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o444)
                into[path] = {'size': len(content), 'sha256': _digest(content)}

            for path, item in sorted(sources.items()):
                content = item.read_bytes()
                if len(content) != sizes[path]:
                    raise ValueError(f'{path} changed while it was published; publish again')
                put(SOURCE if is_source(path) else FILES, path, content, source if is_source(path) else manifest)
            for path, content in sorted(generated.items()):
                put(FILES, path, content, manifest)
            record = {'release': release, 'files': len(manifest), 'bytes': sum(item['size'] for item in manifest.values()),
                      'published_at': int(time.time()), 'published_by': actor, 'note': note, 'manifest': manifest,
                      'source': source, 'generated': sorted(generated), 'collections': collections,
                      **({'base': base} if base is not None else {})}
            (stage / RECORD).write_text(json.dumps(record, indent=1, sort_keys=True) + '\n', encoding='utf-8')
            (stage / RECORD).chmod(0o444)
            os.replace(stage, releases / release)
        finally:
            if stage.exists():
                shutil.rmtree(stage, onexc=_writable)
        return self.release_record(release)

    def prune(self, keep_also):
        """Remove releases beyond the newest KEEP, never keep_also (the current one). Returns the removed ids."""
        ids = self.release_ids()
        removed = [release for release in ids[:-KEEP] if release != keep_also]
        for release in removed:
            shutil.rmtree(self.releases / release, onexc=_writable)
        return removed

    def remove(self):
        if self.folder.is_symlink():
            raise ValueError('Site folders cannot be symbolic links')
        if self.folder.exists():
            shutil.rmtree(self.folder, onexc=_writable)


def _writable(function, path, error):
    """shutil.rmtree's retry for the read-only files and folders of a release."""
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(Path(path).parent, 0o700)
    function(path)


# ---------- serving ----------

def frontend(row):
    """The public static Frontend a host serves for a site row (evlet.routing.with_hosts)."""
    from .routing import Frontend
    connect = tuple(row['connect'].split())
    return Frontend(row['name'].replace('-', '_'), kind='static', path='/' + row['name'], auth=None, dist=RELEASES,
                    api=tuple(row['api'].split()), csp={'connect-src': connect} if connect else {}, routing=row['routing'])


def serving(connection, folder):
    """{name: (Frontend, folder of the current release or None)} of every site, for the live route table."""
    if folder is None or not connection.table_exists('evlet_sites'):
        return {}
    result = {}
    for row in connection.run(qb.select('evlet_sites').order_by('name')).all():
        release = row['current_release']
        files = Path(folder) / row['name'] / RELEASES / release / FILES if release else None
        result[row['name']] = (frontend(row), files if files is not None and files.is_dir() and not files.is_symlink() else None)
    return result


# ---------- the API ----------

def _live(business):
    live = getattr(business, 'live_routes', None)
    if live is None:
        raise ValueError('Sites need an application with sign-in')
    if live.sites_folder is None:
        raise ValueError('Sites need an application whose database is a file')
    return live


def _check_name(name):
    from .routing import SITE
    if not isinstance(name, str) or not SITE.fullmatch(name):
        raise ValueError(f'{name!r} is not a site name: lowercase letters, digits, _ and -, starting with a letter')
    return name


def _row(connection, name):
    if not connection.table_exists('evlet_sites'):
        raise ValueError('This instance has no sites table yet. Run evlet migrate')
    return connection.run(qb.select('evlet_sites').where(name=name)).one()


def _site(connection, name):
    row = _row(connection, _check_name(name))
    if row is None:
        raise ValueError(f'{name} is not a site of this instance')
    return row


def _store(live, row):
    return SiteStore(live.sites_folder / row['name'], max_file_bytes=row['max_file_bytes'],
                     max_site_bytes=row['max_site_bytes'], max_files=row['max_files'])


def _settings(live, routing, connect, api, limits):
    """Validated column values of the settings given (None leaves one out)."""
    values = {}
    if routing is not None:
        if routing not in ROUTINGS:
            raise ValueError('A site routing is "app" (every path gets index.html) or "pages" (one file per page)')
        values['routing'] = routing
    if connect is not None:
        connect = (connect,) if isinstance(connect, str) else tuple(connect)
        bad = [item for item in connect if not isinstance(item, str) or not ORIGIN.fullmatch(item)]
        if bad:
            raise ValueError(f'CSP origins are https origins such as https://api.example.com: {bad[0]!r}')
        values['connect'] = ' '.join(dict.fromkeys(connect))
    if api is not None:
        api = (api,) if isinstance(api, str) else tuple(api)
        known = live.code.api_scopes()
        unknown = [item for item in api if item not in known]
        if unknown:
            raise ValueError(f'{unknown[0]!r} is not an API scope of an installed app' + (': use one of ' + ', '.join(sorted(known)) if known else ''))
        values['api'] = ' '.join(dict.fromkeys(api))
    for key, value in limits.items():
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= CEILINGS[key]:
            raise ValueError(f'{key} is a whole number from 1 to {CEILINGS[key]}')
        values[key] = value
    return values


def _changed(business, live, tx, actor, name, detail, before, after):
    from .hosts import _bump
    if before is not None or after is not None or detail:
        business.controls.audit(tx.connection, actor, 'Sites', name, detail, before, after)
    _bump(tx.connection)


def _public(live, connection, row):
    store = _store(live, row)
    hosts = [host for host, target in _hosts(connection) if target == row['name']]
    return {'name': row['name'], 'routing': row['routing'], 'connect': row['connect'].split(), 'api': row['api'].split(),
            'current_release': row['current_release'], 'max_file_bytes': row['max_file_bytes'],
            'max_site_bytes': row['max_site_bytes'], 'max_files': row['max_files'], 'hosts': hosts,
            'served_on': [host for host in hosts if host in live.application.routes.data_hosts()],
            'releases': len(store.release_ids()), 'problem': _problem(store, row), 'created_at': row['created_at'],
            'created_by': row['created_by'], 'changed_at': row['changed_at']}


def _problem(store, row):
    """Why a site with a release serves nothing, or None (a restore can name a release removed since)."""
    release = row['current_release']
    if release and not store.release_files(release).is_dir():
        return f'Release {release} is missing from sites/{row["name"]}/releases. Publish again or roll back'
    return None


def _hosts(connection):
    """(host, target) of every host from data."""
    if not connection.table_exists('evlet_hosts'):
        return []
    return [(row['host'], row['target']) for row in connection.run(qb.select('evlet_hosts', 'host', 'target').order_by('host')).all()]


def list_sites(business):
    """Every site, by name: its settings, current release, limits, the hosts from data naming it and the
    ones serving it now."""
    live = _live(business)
    live.refresh(force=True)
    with business.database._connection() as connection:
        if not connection.table_exists('evlet_sites'):
            return []
        rows = connection.run(qb.select('evlet_sites').order_by('name')).all()
        return [_public(live, connection, row) for row in rows]


def get_site(business, name):
    live = _live(business)
    live.refresh(force=True)
    with business.database._connection() as connection:
        return _public(live, connection, _site(connection, name))


def create_site(business, name, *, actor, routing='app', connect=(), api=(), max_file_bytes=None, max_site_bytes=None,
                max_files=None):
    """Add a site with an empty draft and no release. A host serves it once evlet.hosts.add_host names it and
    its DNS check passes; until it is published that host answers 404. Returns the site."""
    live = _live(business)
    _check_name(name)
    values = {'max_file_bytes': MAX_FILE_BYTES, 'max_site_bytes': MAX_SITE_BYTES, 'max_files': MAX_FILES}
    values.update(_settings(live, routing, connect, api, {'max_file_bytes': max_file_bytes, 'max_site_bytes': max_site_bytes,
                                                          'max_files': max_files}))
    now = int(time.time())
    folder = live.sites_folder / name
    with business.database.transaction() as tx:
        if _row(tx.connection, name) is not None:
            raise ValueError(f'{name} is already a site of this instance')
        if folder.exists() or folder.is_symlink():
            raise ValueError(f'sites/{name} already exists on disk; move it away first')
        tx.connection.run(qb.insert('evlet_sites', name=name, current_release=None, created_at=now, created_by=actor,
                                    changed_at=now, **values))
        _changed(business, live, tx, actor, name, 'Created', None, {key: values[key] for key in sorted(values)})
    SiteStore(folder)._folder(DRAFT)
    return get_site(business, name)


def update_site(business, name, *, actor, routing=None, connect=None, api=None, max_file_bytes=None, max_site_bytes=None,
                max_files=None):
    """Change a site's routing, CSP origins, API scopes or limits; the next request serves the change. Returns the site."""
    live = _live(business)
    values = _settings(live, routing, connect, api, {'max_file_bytes': max_file_bytes, 'max_site_bytes': max_site_bytes,
                                                     'max_files': max_files})
    with business.database.transaction() as tx:
        row = _site(tx.connection, name)
        changed = {key: value for key, value in values.items() if row[key] != value}
        if changed:
            tx.connection.run(qb.update('evlet_sites', changed_at=int(time.time()), **changed).where(name=name))
            _changed(business, live, tx, actor, name, 'Changed', {key: row[key] for key in sorted(changed)}, dict(sorted(changed.items())))
    live.refresh(force=True)
    return get_site(business, name)


def delete_site(business, name, *, actor):
    """Remove a site, its draft and its releases. Refused while a host from data names it: remove the host first."""
    live = _live(business)
    with business.database.transaction() as tx:
        row = _site(tx.connection, name)
        hosts = [host for host, target in _hosts(tx.connection) if target == name]
        if hosts:
            raise ValueError(f'{", ".join(hosts)} serves site {name}. Remove that host first (evlet host remove)')
        tx.connection.run(qb.delete('evlet_sites').where(name=name))
        _changed(business, live, tx, actor, name, 'Deleted', {'current_release': row['current_release']}, None)
    _store(live, row).remove()
    live.refresh(force=True)


def _store_of(business, name):
    live = _live(business)
    with business.database._connection() as connection:
        row = _site(connection, name)
    return live, row, _store(live, row)


def list_files(business, site):
    """The draft's files: [{path, size, sha256}], by path."""
    _, _, store = _store_of(business, site)
    return [{'path': path, 'size': size, 'sha256': digest} for path, (size, digest) in store.files().items()]


def read_file(business, site, path, *, release=None):
    """The bytes of a draft file, or of a file of a release."""
    _, _, store = _store_of(business, site)
    return store.read(path, release)


def write_file(business, site, path, content, *, actor):
    """Write one draft file (text or bytes); nothing is served until publish. Returns {path, size, sha256}."""
    return write_files(business, site, {path: content}, actor=actor)[0]


def write_files(business, site, contents, *, actor):
    """Write several draft files ({path: text or bytes}) after checking all of them against the paths, types and
    limits; nothing is written when one is refused. Returns [{path, size, sha256}]."""
    live, row, store = _store_of(business, site)
    written = store.write_many(contents)
    with business.database.transaction() as tx:
        for path, (size, digest) in written.items():
            business.controls.audit(tx.connection, actor, 'Sites', site, f'Draft: wrote {path}', None,
                                    {'path': path, 'size': size, 'sha256': digest})
    return [{'path': path, 'size': size, 'sha256': digest} for path, (size, digest) in written.items()]


def delete_file(business, site, path, *, actor):
    """Remove one draft file."""
    live, row, store = _store_of(business, site)
    store.delete(path)
    with business.database.transaction() as tx:
        business.controls.audit(tx.connection, actor, 'Sites', site, f'Draft: deleted {path}', {'path': path}, None)


def list_releases(business, site):
    """The site's releases, newest first: [{release, files, bytes, published_at, published_by, note, current}]."""
    _, row, store = _store_of(business, site)
    return [{**store.release_record(release), 'current': release == row['current_release']}
            for release in reversed(store.release_ids())]


def _switch(business, live, name, release, actor, detail, *, current=False):
    """Point the site at release. current: switch only while the site still serves that release (a render job
    must not undo a publish or rollback that happened while it rendered)."""
    with business.database.transaction() as tx:
        row = _site(tx.connection, name)
        if current is not False and row['current_release'] != current:
            raise ValueError(f'Site {name} was published or rolled back while its pages were rendered; they render again next change')
        tx.connection.run(qb.update('evlet_sites', current_release=release, changed_at=int(time.time())).where(name=name))
        _changed(business, live, tx, actor, name, detail, {'current_release': row['current_release']}, {'current_release': release})
    live.refresh(force=True)


def _renderer(business, row):
    """render(read) for SiteStore.snapshot: the site's collections (evlet.site_pages), rendered as the public role."""
    from . import site_pages

    def render(read):
        collections = site_pages.declared(read)
        if not collections:
            return {}, []
        files, info, _ = site_pages.render(business, read, collections, max_file_bytes=row['max_file_bytes'])
        return files, info
    return render


def publish(business, site, *, actor, note=''):
    """Copy the draft into a new release, render its collections into it, and serve it: every process switches
    within a second. Keeps the newest KEEP releases. Returns the release."""
    live, row, store = _store_of(business, site)
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError('A release note is text of at most 500 characters')
    record = store.snapshot(actor=actor, note=note, render=_renderer(business, row))
    _switch(business, live, site, record['release'], actor, f'Published {record["release"]}')
    store.prune(record['release'])
    return {**record, 'current': True}


def rerender(business, site, *, actor=RENDER_ACTOR):
    """Render the collections of the site's current release again, from that release's own files and templates
    (never the draft), into a new release that is served at once. Returns it, or None when nothing changed (or
    the site has no release or no collections). The render job calls it after a model it reads changed."""
    live, row, store = _store_of(business, site)
    current = row['current_release']
    if current is None or not store.release_files(current).is_dir():
        return None
    if not store.release_source(current).joinpath(DECLARATION).is_file():
        return None
    record = store.snapshot(actor=actor, note=f'Pages rendered again from {current}', render=_renderer(business, row), base=current)
    if record is None:
        return None
    try:
        _switch(business, live, site, record['release'], actor, f'Rendered {record["release"]} again from {current}', current=current)
    except ValueError:
        shutil.rmtree(store.releases / record['release'], onexc=_writable)
        raise
    store.prune(record['release'])
    return {**record, 'current': True}


def rollback(business, site, release=None, *, actor):
    """Serve an earlier release again: release, or the one published before the current one. Returns it."""
    live, row, store = _store_of(business, site)
    ids = store.release_ids()
    current = row['current_release']
    if release is None:
        older = [item for item in ids if current is None or item < current]
        if not older:
            raise ValueError(f'Site {site} has no release before {current}' if current else f'Site {site} has no release yet')
        release = older[-1]
    elif release not in ids:
        store.release_files(release)  # a malformed id says so
        raise ValueError(f'{release} is not a release of site {site}')
    if not store.release_files(release).is_dir():
        raise ValueError(f'Release {release} of site {site} has no files')
    if release != current:
        _switch(business, live, site, release, actor, f'Rolled back to {release}')
    return {**store.release_record(release), 'current': True}


# ---------- rendering again when records change ----------

def watching(sites):
    """{model API name: (site, ...)} of the collections each site's current release declares, from the live
    route table's sites ({name: (Frontend, files folder)}). Read from disk when the hosts version moves."""
    found = {}
    for name, (_, files) in sorted(sites.items()):
        if files is None:
            continue
        declaration = files.parent / SOURCE / DECLARATION
        try:
            if declaration.is_symlink() or not declaration.is_file():
                continue
            document = json.loads(declaration.read_text(encoding='utf-8'))
            models = {item['model'] for item in document['collections'] if isinstance(item.get('model'), str)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
        for model in models:
            found.setdefault(model, ())
            found[model] += (name,)
    return found


def changed(business, tx, model):
    """Queue the render job of each site whose current release reads model, DEBOUNCE seconds from now, unless
    one is already waiting: a burst of changes renders once. Called with every record event (Business.changed)."""
    live = getattr(business, 'live_routes', None)
    if live is None or business.jobs is None or not live.site_watches:
        return
    names = live.site_watches.get(business.registry.name_for(model))
    if not names:
        return
    for name in names:
        payload = json.dumps({'site': name}, separators=(',', ':'))
        waiting = tx.connection.run(qb.select('evlet_jobs', 'id').where(task=RENDER_TASK, state='queued', payload=payload).limit(1)).one()
        if waiting is None:
            business.jobs.enqueue(tx, RENDER_TASK, {'site': name}, run_at=int(time.time()) + DEBOUNCE, system_role='viewer')


def render_job(business):
    """The evlet_site_render task: render one site's collections again (an external task: no transaction is held
    while it reads records and writes files)."""
    def run(context, payload):
        try:
            record = rerender(business, payload['site'])
        except ValueError as exc:
            # A site deleted since, or a collection that no longer renders: the current release stays, and why is logged.
            logger.warning('Site %s was not rendered again: %s', payload.get('site'), exc)
            return {'rendered': None, 'error': str(exc)[:500]}
        return {'rendered': None if record is None else record['release']}
    return run


def register_task(business):
    if business.jobs is None:
        return
    from .jobs import Task
    business.jobs.tasks.setdefault(RENDER_TASK, Task(RENDER_TASK, render_job(business), max_attempts=1, external=True))


# ---------- who manages sites ----------

def manages_sites(business, user):
    """Whether user may manage the instance's sites: admin, or a desk role with "Manage sites" on (Controls,
    Roles). It holds for the person's own sessions, their API keys and the AI assistants acting for them."""
    controls = getattr(business, 'controls', None)
    if controls is None or user is None or not user.id:
        return False
    role = controls.roles.get(user.role)
    if role is None or role.get('realm') != 'desk':
        return False
    return user.role == 'admin' or bool(role.get('manage_sites'))


def available(business):
    """Whether this instance can hold sites at all (an application with sign-in whose database is a file)."""
    live = getattr(business, 'live_routes', None)
    return live is not None and live.sites_folder is not None


# ---------- HTTP: /api/_sites ----------

def route(business, scope, body, user):
    """The site routes, for the site tools and the browser. Each needs "Manage sites" (manages_sites).

    GET  /api/_sites                          the sites
    GET  /api/_sites/<site>/files             the draft's files
    GET  /api/_sites/<site>/file?path=..[&release=..]   one file's text
    POST /api/_sites/<site>/files             {"files": {path: text}}: write draft files
    GET  /api/_sites/<site>/releases          the releases, newest first
    POST /api/_sites/<site>/publish           {"note": ...}
    POST /api/_sites/<site>/rollback          {"release": ...} (optional)
    """
    from urllib.parse import parse_qs
    from .auth import AccessError
    from .models import ValidationError, checked_body
    if not available(business):
        raise AccessError(404, 'This instance has no sites')
    if not manages_sites(business, user):
        raise AccessError(403, 'Managing sites needs an admin, or a role with "Manage sites" on (Controls, Roles).')
    method, parts = scope['method'], scope['path'].removeprefix('/api/_sites').split('/')[1:]
    actor = user.username
    try:
        if not parts and method == 'GET':
            return 200, {'items': list_sites(business)}, []
        if len(parts) != 2:
            raise AccessError(404, 'Route not found')
        site, what = parts
        _check_name(site)
        if what == 'files' and method == 'GET':
            return 200, {'items': list_files(business, site)}, []
        if what == 'file' and method == 'GET':
            query = parse_qs(scope.get('query_string', b'').decode('latin-1'), max_num_fields=4)
            if set(query) - {'path', 'release'} or 'path' not in query or any(len(value) != 1 for value in query.values()):
                raise ValidationError({'query': 'Use path and, for a release, release'})
            path, release = query['path'][0], query.get('release', [None])[0]
            content = read_file(business, site, path, release=release)
            return 200, file_text(path, content), []
        if what == 'files' and method == 'POST':
            checked_body(body, {'files'}, 'Use files: {path: text}', nonempty=True)
            files = body['files']
            if not isinstance(files, dict) or not files or len(files) > 500 or any(not isinstance(value, str) for value in files.values()):
                raise ValidationError({'files': 'Give 1 to 500 files as {path: text}'})
            return 200, {'items': write_files(business, site, files, actor=actor)}, []
        if what == 'releases' and method == 'GET':
            return 200, {'items': list_releases(business, site)}, []
        if what == 'publish' and method == 'POST':
            checked_body(body, {'note'}, 'Use note')
            return 200, publish(business, site, actor=actor, note=body.get('note', '')), []
        if what == 'rollback' and method == 'POST':
            checked_body(body, {'release'}, 'Use release')
            return 200, rollback(business, site, body.get('release'), actor=actor), []
    except ValueError as exc:
        if isinstance(exc, ValidationError):
            raise
        message = str(exc)
        raise AccessError(404 if 'is not a site of this instance' in message else 422, message) from None
    raise AccessError(404 if what not in ('files', 'file', 'releases', 'publish', 'rollback') else 405, 'Route not found')


def file_text(path, content):
    """A site file as a tool reads it: its text, or its size when it isn't UTF-8 text."""
    try:
        text = content.decode('utf-8')
    except UnicodeError:
        return {'path': path, 'size': len(content), 'text': None, 'note': 'This file is not text.'}
    return {'path': path, 'size': len(content), 'text': text[:TEXT_LIMIT], 'truncated': len(text) > TEXT_LIMIT}
