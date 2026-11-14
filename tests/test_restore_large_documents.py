"""Complete server release snapshots restore paginated and nested rows without weakening HTTP writes."""

from evlet import Child, Database, Model, Registry, Rule, State, Table, Text, ValidationError, qb, validate
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import start
from test_states import etag


def test_restore_complete_nested_release_preserves_ids_and_public_page_guard(tmp_path):
    failure = []
    class Part(Child):
        text = Text(default='part')
    class Line(Child):
        text = Text(default='line')
        parts = Table(Part)
    class Report(Model):
        Meta = FiniteMeta
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'restore'))}
        lines = Table(Line, max_rows=101)
        status = State(State.Step('draft'), State.Step('issued', release=True), rules=(
            State.Rule('draft', 'issued', 'Issue', key='issue'), State.Rule('issued', 'draft', 'Reopen', key='reopen')))
        @validate
        def valid(self):
            if failure:
                raise ValidationError({'_form': 'No changes now'})

    app = start(Database(tmp_path / 'report.sqlite3', Registry(Report)))
    person = client(app)
    status, first, _ = person('POST', '/api/report', {'children': {'lines': [
        {'text': str(index), 'children': {'parts': [{'text': f'part{index}'}]}} for index in range(101)]}})
    assert status == 201, first
    url = '/api/report/' + first['id']
    def snapshot():
        with app.database._connection() as connection:
            return {app.registry.name_for(child.model): [dict(row) for row in connection.run(
                qb.select(app.registry.name_for(child.model), 'id', 'text', 'evlet_owner', 'evlet_position').order_by('evlet_owner', 'evlet_position', 'id'))]
                    for child in app.registry.children}
    before = snapshot()
    for move in ('issue', 'reopen'):
        result = person('POST', url + '/moves/' + move, {}, headers=etag(person, url))
        assert result[0] == 200, result[1]
    # A partial GET must never be accepted as the full replacement for a paginated collection.
    partial = [{'id': row['id'], 'text': row['text']} for row in first['children']['lines']]
    assert person('PATCH', url, {'children': {'lines': partial}}, headers=etag(person, url))[0] == 422
    deleted = first['children']['lines'][0]['id']
    assert person('PATCH', url, {'children': {'lines': {'delete': [deleted]}}}, headers=etag(person, url))[0] == 200
    changed = snapshot()
    failure.append(True)
    rejected = person('POST', url + '/restore', {'version': 0, 'remark': 'Back'}, headers=etag(person, url))
    assert rejected[0] == 422 and snapshot() == changed
    failure.clear()
    restored = person('POST', url + '/restore', {'version': 0, 'remark': 'Back'}, headers=etag(person, url))
    assert restored[0] == 200, restored[1]
    assert restored[1]['children_pages']['lines']['total'] == 101
    assert snapshot() == before
    assert person('PATCH', url, {'children': {'lines': partial}}, headers=etag(person, url))[0] == 422
