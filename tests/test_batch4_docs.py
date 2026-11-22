"""Batch 4: Context.list skips the count when its page already tells the exact total (PERF-12), the split
document pipeline keeps its behaviour (CQ-14), and the restore and unsaved routes keep their 404/405 (CQ-20)."""

import pytest

from evlet import Model, Rule, Text, User
from evlet.auth import AccessError
from test_rules_types import application

ALL = ('read', 'create', 'update', 'delete')
ADMIN = User(1, 'admin', 'admin')


class Note(Model):
    permissions = {'admin': Rule(ALL)}
    title = Text(required=True)
    kind = Text()


@pytest.fixture
def app(tmp_path):
    return application(tmp_path, Note)


def counted(context, call):
    """call() with the SELECT count(...) statements it runs on this context's real SQLite connection."""
    statements = []
    connection = context._tx.connection
    connection.set_trace_callback(lambda sql: statements.append(sql) if 'count(' in sql.lower() else None)
    try:
        result = call()
    finally:
        connection.set_trace_callback(None)
    return result, statements


def test_a_short_page_tells_the_total_without_a_count(app):
    with app.business.context(ADMIN) as context:
        for number in range(7):
            context.create(Note, title=f'n{number}', kind='odd' if number % 2 else 'even')
        page, counts = counted(context, lambda: context.list(Note, limit=50))
        assert (len(page['rows']), page['total'], counts) == (7, 7, [])
        page, counts = counted(context, lambda: context.list(Note, filters={'kind': 'odd'}, limit=5, offset=1))
        assert (len(page['rows']), page['total'], counts) == (2, 3, [])
        # Totals stay exact on the trusted API: a full page, or an empty page past the end, still counts.
        page, counts = counted(context, lambda: context.list(Note, limit=3))
        assert (len(page['rows']), page['total'], len(counts)) == (3, 7, 1)
        page, counts = counted(context, lambda: context.list(Note, limit=3, offset=20))
        assert (page['rows'], page['total'], len(counts)) == ([], 7, 1)
        page, counts = counted(context, lambda: context.list(Note, filters={'kind': 'none'}))
        assert (page['rows'], page['total'], counts) == ([], 0, [])
        assert context.count(Note) == 7 and context.count(Note, filters={'kind': 'even'}) == 4


def test_the_split_write_pipeline_keeps_create_update_and_unchanged_saves(app):
    with app.business.context(ADMIN) as context:
        note = context.create(Note, title='first')
        context.update(Note, note.id, title='second')
        assert context.update(Note, note.id, title='second')['title'] == 'second'  # Unchanged: the early result.
        assert context.get(Note, note.id)['title'] == 'second'
        with pytest.raises(AccessError) as caught:
            context.action(Note, 0, 'anything')
        assert (caught.value.status, caught.value.message) == (404, 'Record not found')
        with pytest.raises(AccessError) as caught:
            context.action(Note, note.id, 'title')
        assert (caught.value.status, caught.value.message) == (403, 'Action is unavailable')


def test_restore_route_keeps_its_405_for_another_method_or_a_longer_path(tmp_path):
    from support import client
    from test_statemanager import Contract, Delivery, move
    from evlet import Database, Registry
    from test_rules_types import start
    app = start(Database(tmp_path / 'routes.sqlite3', Registry(Contract, Delivery)))
    c = client(app)
    status, body, _ = c('POST', '/api/contract', {'code': 'C-9'})
    assert status == 201, body
    url = f"/api/contract/{body['id']}"
    move(c, url, 'sign')
    for method, path in (('GET', url + '/restore'), ('POST', url + '/restore/extra')):
        status, body, _ = c(method, path, {} if method == 'POST' else None)
        assert (status, body.get('error')) == (405, 'Method not allowed'), (method, path, body)
    assert c('GET', '/api/contract/0/versions')[0] == 404
