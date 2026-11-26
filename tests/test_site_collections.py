"""Site collections, templates, the render job, the site tools and "Manage sites" (docs/sites.md, docs/tools.md)."""

import json
import time

import pytest

from evlet import Application, Boolean, Database, LocalAuth, Model, Policy, Registry, Rule, Text, hosts, qb, sites
from evlet.auth import KeyAccess, User
from evlet.connector import ConnectorError
from evlet.events import EventStore
from evlet.jobs import JobQueue
from evlet.templates import TemplateError, parse, render
from evlet.tools import ApprovalNeeded
from support import http
from test_live_hosts import here, on, shop_routes

PAGE = '<!doctype html><html><head><title>{}</title></head><body>{}</body></html>'


class Post(Model):
    # Declared permissions make Post a document, written through the pipeline that publishes record events.
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
                   'public': Rule(('read',), read_fields=('title', 'slug', 'summary', 'published'))}
    title = Text(required=True)
    slug = Text()
    summary = Text()
    published = Boolean(default=False)
    # Never public: the public role's Rule leaves it out.
    secret = Text()


COLLECTIONS = {'collections': [{
    'name': 'posts', 'model': 'post', 'filter': {'published': True}, 'order_by': 'title', 'limit': 50,
    'page': '/blog/{slug}.html', 'template': 'post.html',
    'index': {'path': 'blog/index.html', 'template': 'blog.html'}, 'json': '/data/posts.json'}]}

SOURCES = {
    'index.html': PAGE.format('Home', '<a href="/blog/">Blog</a>'),
    '_site/collections.json': json.dumps(COLLECTIONS),
    '_site/post.html': '{{> head}}<h1>{{title}}</h1><p>{{summary}}</p><p>[{{secret}}]</p></body></html>',
    '_site/blog.html': '{{> head}}<ul>{{#each items}}<li><a href="{{_url}}">{{title}}</a></li>{{else}}<li>No posts</li>{{/each}}</ul>'
                       '<p>{{count}} posts</p></body></html>',
    '_site/partials/head.html': '<!doctype html><html><head><title>{{title}}</title></head><body>',
}


def build(tmp_path, **options):
    routes = shop_routes(tmp_path)
    database = Database(tmp_path / 'live.sqlite3', Registry(Post))
    auth = LocalAuth(database, origins=routes.origins(), realms=routes.realms(), cookie_name='__Host-evlet_session')
    app = Application(database, auth=auth, routes=routes, policy=Policy(database.registry),
                      jobs=JobQueue(database, []), events=EventStore(database))
    app.initialize()
    return app


@pytest.fixture
def app(tmp_path):
    app = build(tmp_path)
    yield app
    app.database.close()


def serve(app, name='blog', host='blog.acme.com'):
    hosts.add_host(app.business, host, name, actor='admin')
    [row] = hosts.check_hosts(app.business, [host], actor='admin', addresses=['203.0.113.7'], resolve=here)
    assert row['status'] == 'active'


def get(app, path, host='blog.acme.com'):
    return http(app, 'GET', path, headers=on(host))


def admin(app):
    return User(1, 'admin', 'admin')


def posts(app, *rows):
    ids = []
    with app.business.context(admin(app)) as context:
        for title, slug, published in rows:
            ids.append(context.create(Post, title=title, slug=slug, summary=f'About {title}', published=published,
                                      secret=f'hidden-{title}').id)
    return ids


def site(app, files=SOURCES):
    sites.create_site(app.business, 'blog', actor='admin', routing='pages')
    sites.write_files(app.business, 'blog', files, actor='admin')
    serve(app)


def run_jobs(app, *, after=sites.DEBOUNCE + 1):
    ran = 0
    while app.business.jobs.run_one(now=int(time.time()) + after):
        ran += 1
    return ran


# ---------- templates ----------

def test_templates_escape_values_loop_branch_and_include_partials():
    data = {'title': '<b>&"', 'author': {'name': "O'Neil"}, 'tags': ['a<', 'b'], 'posts': [{'title': 'One', 'url': '/one'}], 'none': [],
            'zero': 0, 'yes': True}
    partials = {'foot': '<footer>{{title}}</footer>', 'nested/item': '[{{.}}]'}.get
    text = ('{{title}}|{{author.name}}|{{#each tags}}{{> nested/item}}{{/each}}|{{#each posts}}<a href="{{url}}">{{title}} of {{author.name}}</a>{{/each}}'
            '|{{#each none}}x{{else}}empty{{/each}}|{{#if zero}}z{{else}}no zero{{/if}}|{{#if yes}}yes{{/if}}|{{! note }}{{> foot}}|{{missing}}{{yes}}')
    assert render(text, data, partials=partials) == (
        "&lt;b&gt;&amp;&quot;|O&#x27;Neil|[a&lt;][b]|<a href=\"/one\">One of O&#x27;Neil</a>|empty|no zero|yes|<footer>&lt;b&gt;&amp;&quot;</footer>|true")


@pytest.mark.parametrize('template, message', [
    ('{{{title}}}', 'Raw output is not supported'),
    ('{{& title}}', 'is not a name'),
    ('{{title | upper}}', 'is not a name'),
    ('{{title()}}', 'is not a name'),
    ("{{__import__('os').system('id')}}", 'is not a name'),
    ('{{#with author}}x{{/with}}', 'is not a section'),
    ('{{#each}}x{{/each}}', 'needs one name'),
    ('{{#if a}}x', 'is never closed'),
    ('{{#if a}}x{{/each}}', 'closes nothing open here'),
    ('{{/if}}', 'closes nothing open here'),
    ('{{#if a}}{{else}}{{else}}{{/if}}', 'once'),
    ('{{else}}', 'belongs inside'),
    ('{{> ../../etc/passwd}}', 'needs a partial name'),
    ('{{> /abs}}', 'needs a partial name'),
    ('text {{title', 'is never closed'),
    ('{{#if a}}' * 40 + '{{/if}}' * 40, 'nested more than'),
    ('x' * (300 * 1024), 'over 256 KiB'),
])
def test_hostile_templates_are_refused_with_a_reason(template, message):
    with pytest.raises(TemplateError, match=message):
        render(template, {'a': 1, 'title': 'x'})


def test_templates_read_data_keys_only_and_bound_every_render():
    # A name only reads keys of dicts: Python attributes of the data are never reached.
    assert render('{{title.__class__}}{{title.upper}}{{items.__len__}}|{{.}}', {'title': 'x', 'items': [1]}) == '|'
    assert render('{{a}}{{b}}{{c}}', {'a': None, 'b': {'x': 1}, 'c': [1]}) == ''
    assert parse('plain') == (('text', 'plain'),)
    with pytest.raises(TemplateError, match='more than 8 deep'):
        render('{{> me}}', {}, partials={'me': 'x{{> me}}'}.get)
    with pytest.raises(TemplateError, match='there is no partial nope'):
        render('{{> nope}}', {}, partials={}.get)
    with pytest.raises(TemplateError, match='steps'):
        render('{{#each l}}{{#each l}}{{#each l}}{{#each l}}{{/each}}{{/each}}{{/each}}{{/each}}', {'l': list(range(40))})
    with pytest.raises(TemplateError, match='over 100 characters'):
        render('{{#each l}}{{t}}{{/each}}', {'l': list(range(50)), 't': 'abc'}, max_output=100)


# ---------- collections ----------

def test_publish_renders_collections_as_the_public_role_and_serves_them(app):
    posts(app, ('Hello <world>', 'hello', True), ('Draft', 'draft', False), ('Bad slug', '../up', True), ('Second', 'second', True))
    site(app)
    release = sites.publish(app.business, 'blog', actor='admin', note='Launch')
    assert release['generated'] == 4  # two pages, the index and the JSON file
    [info] = release['collections']
    assert info['records'] == 3 and info['pages'] == 2 and info['skipped'][0]['reason'] == 'slug is empty or not a file name'
    status, body, headers = get(app, '/blog/hello.html')
    assert status == 200 and b'<h1>Hello &lt;world&gt;</h1>' in body and b'<title>Hello &lt;world&gt;</title>' in body
    assert headers[b'cache-control'] == b'no-cache'
    # A field the public role can't read never reaches a page or the JSON file, even when a template names it.
    assert b'[]' in body and b'hidden' not in body
    status, body, _ = get(app, '/blog/')
    assert status == 200 and b'<a href="/blog/hello.html">Hello &lt;world&gt;</a>' in body and b'3 posts' in body
    assert b'Draft' not in body
    status, body, _ = get(app, '/data/posts.json')
    data = body if isinstance(body, dict) else json.loads(body)
    assert [item['title'] for item in data['items']] == ['Bad slug', 'Hello <world>', 'Second']
    assert all(set(item) <= {'id', 'title', 'slug', 'summary', 'published', '_url'} for item in data['items']) and b'hidden' not in body
    # The sources are published with the release but never served, and the draft has no generated files.
    assert get(app, '/_site/post.html')[0] == 404 and get(app, '/_site/collections.json')[0] == 404
    assert sites.read_file(app.business, 'blog', '_site/post.html', release=release['release']).startswith(b'{{> head}}')
    assert 'blog/hello.html' not in {item['path'] for item in sites.list_files(app.business, 'blog')}


def test_a_collection_the_public_role_cannot_read_or_filter_is_refused_at_publish(app):
    posts(app, ('One', 'one', True))
    site(app)
    refused = {**COLLECTIONS['collections'][0], 'filter': {'secret': 'hidden-One'}}
    sites.write_file(app.business, 'blog', '_site/collections.json', json.dumps({'collections': [refused]}), actor='admin')
    with pytest.raises(ValueError, match='only fields the public role reads'):
        sites.publish(app.business, 'blog', actor='admin')
    ordered = {**COLLECTIONS['collections'][0], 'order_by': 'secret'}
    sites.write_file(app.business, 'blog', '_site/collections.json', json.dumps({'collections': [ordered]}), actor='admin')
    with pytest.raises(ValueError, match='only fields the public role reads'):
        sites.publish(app.business, 'blog', actor='admin')
    assert sites.list_releases(app.business, 'blog') == []


def test_no_public_rule_means_no_collection(tmp_path):
    class Hidden(Model):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
        title = Text()
    routes = shop_routes(tmp_path)
    database = Database(tmp_path / 'live.sqlite3', Registry(Hidden))
    app = Application(database, auth=LocalAuth(database, origins=routes.origins(), realms=routes.realms()), routes=routes,
                      policy=Policy(database.registry), jobs=JobQueue(database, []))
    app.initialize()
    try:
        site(app, {**SOURCES, '_site/collections.json': json.dumps({'collections': [{**COLLECTIONS['collections'][0], 'model': 'hidden'}]})})
        with pytest.raises(ValueError, match=r'the public role cannot read hidden .*Rule for \("public", Model\)'):
            sites.publish(app.business, 'blog', actor='admin')
    finally:
        database.close()


@pytest.mark.parametrize('collection, message', [
    ({'name': 'p', 'model': 'post'}, 'at least one of page, index or json'),
    ({'name': 'p', 'model': 'post', 'page': 'blog/{slug}.html'}, 'needs a template'),
    ({'name': 'p', 'model': 'post', 'page': 'blog/static.html', 'template': 'post.html'}, 'names a field in braces'),
    ({'name': 'p', 'model': 'post', 'json': '_site/secret.json'}, 'never served'),
    ({'name': 'p', 'model': 'post', 'json': '../up.json'}, 'must not hold empty'),
    ({'name': 'p', 'model': 'post', 'json': 'data/posts.js'}, 'one .json file'),
    ({'name': 'p', 'model': 'post', 'json': 'a.json', 'limit': 5000}, 'limit is a whole number'),
    ({'name': 'p', 'model': 'post', 'json': 'a.json', 'eval': 'x'}, 'use fields'),
    ({'name': 'p', 'model': 'Post', 'json': 'a.json'}, 'model is the API name'),
    ({'name': 'p', 'model': 'nothing', 'json': 'a.json'}, 'not a model the API serves'),
    ({'name': 'p', 'model': 'post', 'index': {'path': 'i.html', 'template': '../x.html'}}, 'must not hold empty'),
])
def test_collection_declarations_are_checked(app, collection, message):
    site(app, {**SOURCES, '_site/collections.json': json.dumps({'collections': [collection]})})
    with pytest.raises(ValueError, match=message):
        sites.publish(app.business, 'blog', actor='admin')


def test_a_generated_page_cannot_replace_a_draft_file(app):
    posts(app, ('One', 'one', True))
    site(app, {**SOURCES, 'blog/index.html': 'static'})
    with pytest.raises(ValueError, match='blog/index.html is a file of the site and a page a collection writes'):
        sites.publish(app.business, 'blog', actor='admin')


# ---------- the render job ----------

def test_a_record_change_renders_the_site_again_after_a_short_wait(tmp_path):
    app, other = build(tmp_path), build(tmp_path)
    try:
        [hello, _] = posts(app, ('Hello', 'hello', True), ('Second', 'second', True))
        site(app)
        first = sites.publish(app.business, 'blog', actor='admin')
        # Another process learns which models the site reads with the next hosts version it reads.
        other.business.live_routes.refresh(force=True)
        assert other.business.live_routes.site_watches == {'post': ('blog',)}
        # A burst of changes queues one render, DEBOUNCE seconds out; nothing runs before then.
        with other.business.context(admin(other)) as context:
            context.update(Post, hello, title='Hello again')
            context.update(Post, hello, summary='Changed twice')
        with app.database._connection() as connection:
            queued = connection.run(qb.select('evlet_jobs', 'run_at').where(task=sites.RENDER_TASK, state='queued')).all()
        assert len(queued) == 1 and queued[0]['run_at'] >= int(time.time()) + sites.DEBOUNCE - 1
        assert run_jobs(app, after=0) == 0
        # Draft edits are never published by a data change: the job renders the current release's own files.
        sites.write_file(app.business, 'blog', 'index.html', PAGE.format('Home', 'Unpublished draft'), actor='admin')
        assert run_jobs(app) == 1
        releases = sites.list_releases(app.business, 'blog')
        assert len(releases) == 2 and releases[0]['current'] and releases[0]['published_by'] == 'system'
        assert releases[0]['note'] == f'Pages rendered again from {first["release"]}'
        assert b'Hello again' in get(app, '/blog/hello.html')[1] and b'Changed twice' in get(app, '/blog/hello.html')[1]
        assert b'Unpublished draft' not in get(app, '/')[1]
        # A change that renders the same pages makes no release.
        with app.business.context(admin(app)) as context:
            context.update(Post, hello, secret='still hidden')
        assert run_jobs(app) == 1 and len(sites.list_releases(app.business, 'blog')) == 2
        # Records leaving the filter leave the site; the render is audited under Sites.
        with app.business.context(admin(app)) as context:
            context.update(Post, hello, published=False)
        run_jobs(app)
        assert get(app, '/blog/hello.html')[0] == 404 and b'1 posts' in get(app, '/blog/')[1]
        with app.database._connection() as connection:
            details = [row['detail'] for row in connection.run(qb.select('evlet_audit', 'detail').where(area='Sites', actor='system'))]
        assert len(details) == 2 and all(detail.startswith('Rendered ') for detail in details)
        # Rendering again keeps the newest KEEP releases too.
        for number in range(sites.KEEP + 2):
            with app.business.context(admin(app)) as context:
                context.update(Post, hello, published=True, title=f'Edit {number}')
            run_jobs(app)
        assert len(sites.list_releases(app.business, 'blog')) == sites.KEEP
    finally:
        app.database.close()
        other.database.close()


def test_a_render_after_a_publish_or_rollback_never_undoes_it(app):
    [hello] = posts(app, ('Hello', 'hello', True))
    site(app)
    first = sites.publish(app.business, 'blog', actor='admin')
    second = sites.publish(app.business, 'blog', actor='admin')
    sites.rollback(app.business, 'blog', actor='admin')
    with app.business.context(admin(app)) as context:
        context.update(Post, hello, title='New title')
    run_jobs(app)
    current = sites.get_site(app.business, 'blog')['current_release']
    record = sites.list_releases(app.business, 'blog')[0]
    assert current == record['release'] and record['note'] == f'Pages rendered again from {first["release"]}' and current != second['release']
    # A site without collections never queues a render.
    sites.write_file(app.business, 'blog', '_site/collections.json', '{"collections": []}', actor='admin')
    sites.publish(app.business, 'blog', actor='admin')
    assert app.business.live_routes.site_watches == {}
    with app.business.context(admin(app)) as context:
        context.update(Post, hello, title='Not watched')
    assert run_jobs(app) == 0


# ---------- Manage sites, the routes and the tools ----------

def people(app):
    auth = app.business.auth
    with app.database.transaction() as tx:
        tx.connection.run(qb.insert('evlet_roles', id='webmaster', kind='custom', base='editor', realm='desk', opens_controls=0,
                                    description='Edits the website'))
        tx.connection.run(qb.insert('evlet_overrides', kind='setting', key='mcp.allow_writes', value='true'))
    app.business.controls.refresh(force=True)
    return auth.create_user(username='editor', password='a long enough password', role='editor'), \
        auth.create_user(username='webby', password='a long enough password', role='webmaster')


def mcp(user):
    return User(user.id, user.username, user.role, access=KeyAccess(True, None, None, 0))


def test_site_tools_need_manage_sites_and_publish_asks_for_approval(app):
    posts(app, ('Hello', 'hello', True))
    editor, webby = people(app)
    sites.create_site(app.business, 'blog', actor='admin', routing='pages')
    serve(app)
    tools = app.business.tools
    names = set(('site_list', 'site_files_list', 'site_file_read', 'site_files_write', 'site_publish', 'site_rollback'))
    # Without Manage sites the tools are not listed, and a call is refused by the route as well.
    assert not names & {tool.name for tool in tools.listing(mcp(editor))}
    with pytest.raises(ConnectorError) as refused:
        tools.execute(mcp(editor), 'site_list', {})
    assert refused.value.status == 403 and 'Manage sites' in str(refused.value.message)
    with pytest.raises(ConnectorError) as refused:
        tools.execute(mcp(editor), 'site_publish', {'site': 'blog'})
    assert refused.value.status == 403
    assert not names & {tool.name for tool in tools.listing(mcp(webby))}
    # An admin turns Manage sites on for the role (Controls, Roles); only desk roles can have it.
    controls = app.business.controls
    with pytest.raises(Exception, match='Only an admin'):
        controls.change_role(User(editor.id, 'editor', 'editor'), 'webmaster', {'manage_sites': True})
    controls.change_role(admin(app), 'webmaster', {'manage_sites': True})
    with pytest.raises(Exception, match='Admin always manages sites'):
        controls.change_role(admin(app), 'admin', {'manage_sites': False})
    with pytest.raises(Exception, match='Portal roles can never manage sites'):
        controls.change_role(admin(app), 'portal', {'manage_sites': True})
    roles = {row['id']: row for row in controls.role_list()}
    assert roles['webmaster']['manage_sites'] and roles['admin']['manage_sites'] and not roles['editor']['manage_sites']
    listed = {tool.name: tool for tool in tools.listing(mcp(webby))}
    assert names <= set(listed) and listed['site_publish'].group == 'confirm' and listed['site_files_write'].group == 'update'
    assert not names & {tool.name for tool in tools.listing(User(webby.id, 'webby', 'webmaster', access=KeyAccess(True, None, 9)), 'api')}
    web = mcp(webby)
    assert [item['name'] for item in tools.execute(web, 'site_list', {})['items']] == ['blog']
    written = tools.execute(web, 'site_files_write', {'site': 'blog', 'files': SOURCES})
    assert {item['path'] for item in written['items']} == set(SOURCES)
    assert {item['path'] for item in tools.execute(web, 'site_files_list', {'site': 'blog'})['items']} == set(SOURCES)
    assert tools.execute(web, 'site_file_read', {'site': 'blog', 'path': '_site/post.html'})['text'] == SOURCES['_site/post.html']
    with pytest.raises(ConnectorError) as refused:
        tools.execute(web, 'site_files_write', {'site': 'blog', 'files': {'.env': 'SECRET=1'}})
    assert refused.value.status == 422 and 'never starts with a dot' in str(refused.value.message)
    with pytest.raises(ConnectorError):
        tools.execute(web, 'site_files_write', {'site': 'blog', 'files': {'a.html': 7}})
    # Publishing asks the person first, in Evlet; once approved, the same call publishes.
    with pytest.raises(ApprovalNeeded) as asked:
        tools.execute(web, 'site_publish', {'site': 'blog', 'note': 'Agent launch'})
    approval = asked.value.approval
    assert approval['summary']['group'] == 'confirm' and approval['summary']['operation'] == 'publish site'
    assert get(app, '/blog/hello.html')[0] == 404
    tools.decide(User(webby.id, 'webby', 'webmaster'), approval['id'], True)
    release = tools.execute(web, 'site_publish', {'site': 'blog', 'note': 'Agent launch'})
    assert release['current'] and release['published_by'] == 'webby' and b'Hello' in get(app, '/blog/hello.html')[1]
    read = tools.execute(web, 'site_file_read', {'site': 'blog', 'path': 'blog/hello.html', 'release': release['release']})
    assert '<h1>Hello</h1>' in read['text']
    # Rolling back asks too; the log keeps file names, never file contents.
    with pytest.raises(ApprovalNeeded):
        tools.execute(web, 'site_rollback', {'site': 'blog'})
    with app.database._connection() as connection:
        logged = [json.loads(row['inputs']) for row in connection.run(qb.select('evlet_tool_calls', 'inputs').where(tool='site_files_write'))]
    assert logged[0]['files'] == sorted(SOURCES)
    # The site routes check Manage sites for every caller, the person's own session included.
    for user, expected in ((mcp(editor), 403), (mcp(webby), 200), (User(webby.id, 'webby', 'webmaster', access=KeyAccess(False, None, None, 0)), 200)):
        try:
            status = app.business.dispatch({'type': 'http', 'method': 'GET', 'path': '/api/_sites', 'query_string': b'',
                                            'headers': [(b'host', b'erp.acme.com'), (b'x-evlet-request', b'1')], 'evlet.assistant': user}, None)[0]
        except Exception as exc:
            status = exc.status
        assert status == expected
    # A read-only assistant can't write through the routes either.
    with pytest.raises(Exception) as refused:
        app.business.dispatch({'type': 'http', 'method': 'POST', 'path': '/api/_sites/blog/publish', 'query_string': b'',
                               'headers': [(b'host', b'erp.acme.com'), (b'x-evlet-request', b'1')],
                               'evlet.assistant': User(webby.id, 'webby', 'webmaster', access=KeyAccess(False, None, None, 0))}, {})
    assert refused.value.status == 403

