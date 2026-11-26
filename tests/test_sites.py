"""Instance sites (evlet.sites): the store, publish and rollback, and serving on their own public host only."""

import os
import stat
import tarfile
import time

import pytest

from evlet import hosts, sites
from evlet.cli.main import main
from evlet.ui.serving import FRONTEND_TYPES, hashed
from support import http
from test_live_hosts import build, here, on, web  # noqa: F401  (web is a fixture)
from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401  (fixtures)

PAGE = '<!doctype html><html><head><title>Home</title></head><body>{}</body></html>'


def serve(app, name='home', host='home.acme.com'):
    """Point an active host from data at a site."""
    hosts.add_host(app.business, host, name, actor='admin')
    [row] = hosts.check_hosts(app.business, [host], actor='admin', addresses=['203.0.113.7'], resolve=here)
    assert row['status'] == 'active' and row['served']


def get(app, path, host='home.acme.com', match=None):
    return http(app, 'GET', path, headers={**on(host), **({b'if-none-match': match} if match else {})})


def test_a_published_site_is_served_on_its_own_host_with_cache_headers_etag_and_csp(tmp_path):
    app = build(tmp_path)
    try:
        site = sites.create_site(app.business, 'home', actor='admin', connect=['https://api.example.com'])
        assert site['current_release'] is None and site['hosts'] == [] and site['max_file_bytes'] == sites.MAX_FILE_BYTES
        sites.write_files(app.business, 'home', {'index.html': PAGE.format('Home one'), 'assets/app-3f9a1c2e.js': 'console.log(1)',
                                                 'logo.png': b'\x89PNG\r\n', 'about/index.html': PAGE.format('About')}, actor='admin')
        serve(app)
        # No release yet: the host answers a plain 404, and the draft is never served.
        assert get(app, '/')[0] == 404
        release = sites.publish(app.business, 'home', actor='admin', note='First')
        assert release['files'] == 4 and release['current'] and release['note'] == 'First'
        status, body, headers = get(app, '/')
        assert status == 200 and b'Home one' in body
        assert headers[b'cache-control'] == b'no-cache' and headers[b'etag'].startswith(b'"')
        policy = headers[b'content-security-policy'].decode()
        assert "script-src 'self';" in policy and "connect-src 'self' https://api.example.com;" in policy
        # A public host never shows the instance: no config block, no id, no version, no release.
        assert b'evlet-config' not in body and release['release'].encode() not in body + b''.join(headers.values())
        with app.database._connection() as connection:
            instance_id = connection.run(__import__('evlet').qb.select('evlet_instance', 'id')).value()
        assert instance_id.encode() not in body + b''.join(headers.values())
        # The same ETag gets 304 with no body; another one gets the page.
        status, body, again = get(app, '/', match=headers[b'etag'])
        assert status == 304 and body == b'' and again[b'etag'] == headers[b'etag'] and again[b'cache-control'] == b'no-cache'
        assert get(app, '/', match=b'"other"')[0] == 200
        assert get(app, '/', match=b'W/' + headers[b'etag'])[0] == 304
        # A hashed file is immutable for a year; any other file keeps no-store.
        status, body, headers = get(app, '/assets/app-3f9a1c2e.js')
        assert status == 200 and body == b'console.log(1)' and headers[b'cache-control'] == b'public, max-age=31536000, immutable'
        assert get(app, '/logo.png')[2][b'cache-control'] == b'no-store'
        assert get(app, '/missing.png')[2][b'cache-control'] == b'no-store'
        # App routing: a path without a file type gets index.html.
        assert b'Home one' in get(app, '/pricing')[1]
        # Neither the primary host nor another host serves the site.
        assert get(app, '/', 'erp.acme.com')[0] != 200 or b'Home one' not in get(app, '/', 'erp.acme.com')[1]
        assert get(app, '/home', 'erp.acme.com')[0] == 404
        hosts.add_host(app.business, 'www.acme.com', 'shop.shop', actor='admin')
        hosts.check_hosts(app.business, ['www.acme.com'], actor='admin', addresses=['203.0.113.7'], resolve=here)
        assert b'Home one' not in get(app, '/', 'www.acme.com')[1] and b'Shop' in get(app, '/', 'www.acme.com')[1]
        # The API and framework internals stay closed on the site's host; /controls is just a path of the site.
        assert get(app, '/api/auth/session')[0] == 404 and get(app, '/_/tls-allowed')[0] == 404
        assert b'Home one' in get(app, '/controls')[1]
        listed = sites.list_sites(app.business)
        assert [(item['name'], item['served_on'], item['current_release']) for item in listed] == [('home', ['home.acme.com'], release['release'])]
    finally:
        app.database.close()


def test_publish_switches_releases_rollback_serves_the_old_one_and_other_processes_follow(tmp_path):
    app, other = build(tmp_path), build(tmp_path)
    try:
        sites.create_site(app.business, 'home', actor='admin', routing='pages')
        sites.write_file(app.business, 'home', 'index.html', PAGE.format('One'), actor='admin')
        first = sites.publish(app.business, 'home', actor='admin')
        serve(app)
        started = time.monotonic()
        while b'One' not in get(other, '/')[1]:
            assert time.monotonic() - started < 2, 'the other process did not pick up the site'
            time.sleep(0.05)
        tag = get(app, '/')[2][b'etag']
        sites.write_file(app.business, 'home', 'index.html', PAGE.format('Two'), actor='admin')
        # A draft write changes nothing that is served.
        assert b'One' in get(app, '/')[1]
        second = sites.publish(app.business, 'home', actor='admin')
        assert second['release'] > first['release'] and b'Two' in get(app, '/')[1]
        # The old ETag no longer matches.
        assert get(app, '/', match=tag)[0] == 200
        started = time.monotonic()
        while b'Two' not in get(other, '/')[1]:
            assert time.monotonic() - started < 2
            time.sleep(0.05)
        assert sites.rollback(app.business, 'home', actor='admin')['release'] == first['release']
        assert b'One' in get(app, '/')[1] and get(app, '/', match=tag)[0] == 304
        assert [(item['release'], item['current']) for item in sites.list_releases(app.business, 'home')] == [
            (second['release'], False), (first['release'], True)]
        sites.rollback(app.business, 'home', second['release'], actor='admin')
        assert b'Two' in get(app, '/')[1]
        with pytest.raises(ValueError, match='is not a release of site home'):
            sites.rollback(app.business, 'home', '20200101T000000000000Z', actor='admin')
        with pytest.raises(ValueError, match='is not a release id'):
            sites.rollback(app.business, 'home', '../draft', actor='admin')
        # Pages routing: /about without a page is the site's 404.
        assert get(app, '/about')[0] == 404
        # Releases are read-only on disk and never change.
        folder = tmp_path / 'sites/home/releases' / first['release'] / 'files/index.html'
        assert not os.access(folder, os.W_OK) or os.geteuid() == 0
        assert stat.S_IMODE(folder.stat().st_mode) == 0o444 and b'One' in folder.read_bytes()
        assert sites.read_file(app.business, 'home', 'index.html', release=first['release']) == PAGE.format('One').encode()
        with app.database._connection() as connection:
            audit = [tuple(row) for row in connection.execute("SELECT actor, target, detail FROM evlet_audit WHERE area = 'Sites' ORDER BY id")]
        assert audit == [('admin', 'home', 'Created'), ('admin', 'home', 'Draft: wrote index.html'), ('admin', 'home', f'Published {first["release"]}'),
                         ('admin', 'home', 'Draft: wrote index.html'), ('admin', 'home', f'Published {second["release"]}'),
                         ('admin', 'home', f'Rolled back to {first["release"]}'), ('admin', 'home', f'Rolled back to {second["release"]}')]
    finally:
        app.database.close()
        other.database.close()


def test_the_newest_ten_releases_are_kept_and_the_current_one_always(tmp_path):
    app = build(tmp_path)
    try:
        sites.create_site(app.business, 'home', actor='admin')
        made = []
        for number in range(3):
            sites.write_file(app.business, 'home', 'index.html', PAGE.format(number), actor='admin')
            made.append(sites.publish(app.business, 'home', actor='admin')['release'])
        sites.rollback(app.business, 'home', made[0], actor='admin')
        store = sites.SiteStore(tmp_path / 'sites/home')
        # Publishing makes the new release current, so the oldest ones beyond KEEP go.
        for number in range(sites.KEEP + 1):
            sites.write_file(app.business, 'home', 'index.html', PAGE.format(number), actor='admin')
            made.append(sites.publish(app.business, 'home', actor='admin')['release'])
        assert store.release_ids() == made[-sites.KEEP:]
        # The current release is never pruned, even when it is the oldest.
        sites.rollback(app.business, 'home', made[-sites.KEEP], actor='admin')
        assert store.prune(made[-sites.KEEP]) == []
        sites.write_file(app.business, 'home', 'index.html', PAGE.format('last'), actor='admin')
        sites.publish(app.business, 'home', actor='admin')
        assert len(store.release_ids()) == sites.KEEP
    finally:
        app.database.close()


def test_the_store_refuses_traversal_dotfiles_links_other_types_and_anything_over_its_limits(tmp_path):
    app = build(tmp_path)
    try:
        sites.create_site(app.business, 'home', actor='admin', max_file_bytes=100, max_site_bytes=150, max_files=3)
        for path in ('../escape.html', '/abs.html', 'a/../b.html', '.env', 'a/.git/config.json', 'a//b.html', 'a\\b.html', 'run.exe',
                     'index.php', 'x.html\x00', '', '-a.html', 'a/' * 17 + 'b.html'):
            with pytest.raises(ValueError):
                sites.write_file(app.business, 'home', path, 'x', actor='admin')
        assert not (tmp_path / 'escape.html').exists() and sites.list_files(app.business, 'home') == []
        with pytest.raises(ValueError, match='holds at most 100'):
            sites.write_file(app.business, 'home', 'big.html', 'x' * 101, actor='admin')
        sites.write_file(app.business, 'home', 'index.html', 'x' * 80, actor='admin')
        with pytest.raises(ValueError, match='would hold 160 bytes'):
            sites.write_file(app.business, 'home', 'two.html', 'x' * 80, actor='admin')
        # A rewrite of a file counts its new size only.
        sites.write_file(app.business, 'home', 'index.html', 'x' * 100, actor='admin')
        sites.write_files(app.business, 'home', {'a.css': 'a', 'b.css': 'b'}, actor='admin')
        with pytest.raises(ValueError, match='at most 3 files'):
            sites.write_file(app.business, 'home', 'c.css', 'c', actor='admin')
        # One refused file stops the whole batch.
        with pytest.raises(ValueError):
            sites.write_files(app.business, 'home', {'b.css': 'bb', '.hidden.css': 'x'}, actor='admin')
        assert sites.read_file(app.business, 'home', 'b.css') == b'b'
        with pytest.raises(ValueError, match='index.html is a file'):
            sites.update_site(app.business, 'home', actor='admin', max_files=10)
            sites.write_file(app.business, 'home', 'index.html/x.html', 'x', actor='admin')
        with pytest.raises(ValueError, match='whole number'):
            sites.update_site(app.business, 'home', actor='admin', max_files=0)
        # A link or a dotfile put into the draft from outside stops publishing; a link never reaches a release.
        draft = tmp_path / 'sites/home/draft'
        (tmp_path / 'secret.txt').write_text('secret')
        os.symlink(tmp_path / 'secret.txt', draft / 'leak.txt')
        with pytest.raises(ValueError, match='symbolic link'):
            sites.publish(app.business, 'home', actor='admin')
        with pytest.raises(ValueError, match='symbolic link'):
            sites.read_file(app.business, 'home', 'leak.txt')
        (draft / 'leak.txt').unlink()
        (draft / '.env').write_text('x')
        with pytest.raises(ValueError):
            sites.publish(app.business, 'home', actor='admin')
        (draft / '.env').unlink()
        os.symlink(tmp_path, tmp_path / 'sites/home/draft/linked')
        with pytest.raises(ValueError, match='symbolic link'):
            sites.write_file(app.business, 'home', 'linked/x.html', 'x', actor='admin')
        (draft / 'linked').unlink()
        release = sites.publish(app.business, 'home', actor='admin')
        serve(app)
        assert get(app, '/a.css')[1] == b'a'
        for path in ('/../secret.txt', '/%2e%2e/secret.txt', '/.env', '/a/.git/config', '/index.html/..', '/..%2fsecret.txt'):
            status, body, _ = get(app, path)
            assert b'secret' not in body, path
        assert release['files'] == 3
        # Settings are checked too.
        with pytest.raises(ValueError, match='https origins'):
            sites.update_site(app.business, 'home', actor='admin', connect=['http://insecure.example.com'])
        with pytest.raises(ValueError, match='not an API scope'):
            sites.update_site(app.business, 'home', actor='admin', api=['shop.nothing'])
        with pytest.raises(ValueError, match='routing'):
            sites.update_site(app.business, 'home', actor='admin', routing='spa')
        for name in ('Home', 'a.b', '1site', 'x' * 64, ''):
            with pytest.raises(ValueError, match='not a site name'):
                sites.create_site(app.business, name, actor='admin')
        with pytest.raises(ValueError, match='already a site'):
            sites.create_site(app.business, 'home', actor='admin')
    finally:
        app.database.close()


def test_delete_waits_for_the_host_and_removes_the_files_and_a_host_needs_an_existing_site(tmp_path):
    app = build(tmp_path)
    try:
        with pytest.raises(ValueError, match='not a site of this instance'):
            hosts.add_host(app.business, 'home.acme.com', 'home', actor='admin')
        sites.create_site(app.business, 'home', actor='admin')
        sites.write_file(app.business, 'home', 'index.html', PAGE.format('x'), actor='admin')
        sites.publish(app.business, 'home', actor='admin')
        serve(app)
        with pytest.raises(ValueError, match='Remove that host first'):
            sites.delete_site(app.business, 'home', actor='admin')
        hosts.remove_host(app.business, 'home.acme.com', actor='admin')
        sites.delete_site(app.business, 'home', actor='admin')
        assert not (tmp_path / 'sites/home').exists() and sites.list_sites(app.business) == []
        assert get(app, '/')[0] != 200
        with pytest.raises(ValueError, match='not a site'):
            sites.publish(app.business, 'home', actor='admin')
    finally:
        app.database.close()


def test_the_site_types_are_static_types_and_hashed_names_need_a_digit_and_a_letter():
    assert sites.TYPES <= set(FRONTEND_TYPES)
    assert hashed('assets/index-BdK3x9aQ.js') and hashed('app.3f9a1c2e.css')
    assert not hashed('logo-original.png') and not hashed('photo-20240101.png') and not hashed('index.html')


# ---------- evlet sites, backup and restore ----------

def test_evlet_sites_puts_publishes_rolls_back_and_backups_keep_the_sites_folder(web, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda host, port, **options: here(host, port))
    folder = tmp_path / 'build'
    (folder / 'assets').mkdir(parents=True)
    (folder / 'index.html').write_text(PAGE.format('From the CLI'))
    (folder / 'assets/site-1a2b3c4d.css').write_text('body{}')
    (folder / '.DS_Store').write_text('x')
    project = ['--project', str(web)]
    main(['sites', 'create', 'acme', 'home', '--routing', 'pages', *project])
    assert 'Created site home' in capsys.readouterr().out
    main(['sites', 'put', 'acme', 'home', str(folder), *project])
    captured = capsys.readouterr()
    assert 'Wrote 2 file(s) to the draft of home' in captured.out and '.DS_Store' in captured.err
    main(['sites', 'files', 'acme', 'home', *project])
    assert 'assets/site-1a2b3c4d.css' in capsys.readouterr().out
    main(['sites', 'publish', 'acme', 'home', '--note', 'first', *project])
    assert 'Published home release' in capsys.readouterr().out
    main(['host', 'add', 'acme', 'home.acme.com', '--root', 'home', '--live', '--ip', '203.0.113.7', *project])
    capsys.readouterr()
    main(['sites', 'list', 'acme', *project])
    assert 'home.acme.com' in capsys.readouterr().out.splitlines()[1]
    (folder / 'index.html').write_text(PAGE.format('Second'))
    main(['sites', 'put', '--instance', 'acme', 'home', str(folder / 'index.html'), *project])
    main(['sites', 'publish', 'acme', 'home', *project])
    capsys.readouterr()
    app = runtime(web)
    try:
        status, body, headers = http(app, 'GET', '/', headers=on('home.acme.com'))
        assert status == 200 and b'Second' in body and headers[b'cache-control'] == b'no-cache'
    finally:
        app.database.close()
    main(['sites', 'rollback', 'acme', 'home', *project])
    assert 'serves release' in capsys.readouterr().out
    main(['sites', 'releases', 'acme', 'home', *project])
    releases = capsys.readouterr().out.splitlines()
    assert len(releases) == 3 and '(current)' in releases[2] and 'first' in releases[2]
    app = runtime(web)
    try:
        assert b'From the CLI' in http(app, 'GET', '/', headers=on('home.acme.com'))[1]
    finally:
        app.database.close()
    # evlet backup archives the sites folder with the database; restore leaves it as it is.
    main(['backup', 'acme', *project])
    out = capsys.readouterr().out
    archive = next(web.glob('instances/acme/data/backups/*-sites.tar.gz'))
    assert 'Sites:' in out and stat.S_IMODE(archive.stat().st_mode) == 0o600
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert 'sites/home/draft/index.html' in names and any(name.endswith('/files/assets/site-1a2b3c4d.css') for name in names)
    backup = next(item for item in web.glob('instances/acme/data/backups/*.sqlite3') if '-' not in item.name)
    main(['sites', 'put', 'acme', 'home', str(folder / 'index.html'), '--as', 'later.html', *project])
    main(['restore', str(backup), '--instance', 'acme', '--yes', 'acme', *project])
    capsys.readouterr()
    assert (web / 'instances/acme/sites/home/draft/later.html').is_file()
    app = runtime(web)
    try:
        assert b'From the CLI' in http(app, 'GET', '/', headers=on('home.acme.com'))[1]
    finally:
        app.database.close()
    with pytest.raises(SystemExit):
        main(['sites', 'delete', 'acme', 'home', '--yes', *project])
    assert 'Remove that host first' in capsys.readouterr().err
    main(['host', 'remove', 'acme', 'home.acme.com', *project])
    main(['sites', 'delete', 'acme', 'home', '--yes', *project])
    assert 'Deleted site home' in capsys.readouterr().out and not (web / 'instances/acme/sites/home').exists()


def test_the_playground_publishes_its_news_site_and_controls_serves_it_on_a_host(tmp_path, monkeypatch, hashed):
    import socket
    from evlet import LocalAuth
    from examples.playground.app import build_app, playground_routes, publish_news_site
    from examples.playground.migrate import configured_database
    from test_controls import add_user, on_host
    add_user.hashed = hashed
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'news.sqlite3'))
    routes = playground_routes(8000)
    database = configured_database()
    app = build_app(database, LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False), routes=routes)
    app.initialize()
    try:
        from examples.playground.models import Project
        with app.database.transaction() as tx, tx.maintenance():
            done = tx.create(Project, title='Shipped bridge', docstate='done', budget='9000.00', notes='internal only')
            tx.create(Project, title='Secret plan', docstate='planned')
        site = publish_news_site(app)
        assert site['routing'] == 'pages' and site['current_release'] and publish_news_site(app) == site
        add_user(app, 'admin', 'admin')
        admin = on_host(app, 'admin', b'127.0.0.1:8000')
        status, body, _ = admin('POST', '/api/_controls/routing/hosts', {'host': 'news.localhost', 'target': 'news'})
        assert status == 200, body
        monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **options: [(2, 1, 6, '', ('127.0.0.1', 0))])
        monkeypatch.setattr(hosts, 'server_addresses', lambda: {'127.0.0.1'})
        assert admin('POST', '/api/_controls/routing/hosts/news.localhost/check', {})[1]['items'][0]['served']
        status, page, headers = http(app, 'GET', '/about/', headers={b'host': b'news.localhost:8000'})
        assert status == 200 and b'<h1>About</h1>' in page and headers[b'cache-control'] == b'no-cache'
        assert http(app, 'GET', '/assets/news-1a2b3c4d.css', headers={b'host': b'news.localhost:8000'})[2][b'cache-control'] == sites_immutable()
        assert b'<h1>About</h1>' not in str(http(app, 'GET', '/about/', headers={b'host': b'127.0.0.1:8000'})[1]).encode()
        # Its collection: finished projects only, as the public role reads them, with no budget or notes.
        status, page, _ = http(app, 'GET', '/projects/', headers={b'host': b'news.localhost:8000'})
        assert status == 200 and b'Shipped bridge' in page and b'Secret plan' not in page
        status, page, _ = http(app, 'GET', f'/projects/{done.id}.html', headers={b'host': b'news.localhost:8000'})
        assert status == 200 and b'<h1>Shipped bridge</h1>' in page
        data = http(app, 'GET', '/data/projects.json', headers={b'host': b'news.localhost:8000'})[1]
        assert [item['title'] for item in data['items']] == ['Shipped bridge'] and 'budget' not in data['items'][0]
        assert '9000' not in str(data) and 'internal only' not in str(data)
    finally:
        app.database.close()


def sites_immutable():
    from evlet.ui.serving import IMMUTABLE
    return IMMUTABLE
