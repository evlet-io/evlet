"""Retired app definitions remain removable through the authenticated Controls API."""

import json

from evlet import qb
from tests.test_business import client, hashed, secured


def seed(app, changes):
    with app.database.transaction() as tx:
        for kind, key, value in changes:
            tx.connection.run(qb.insert('evlet_overrides', kind=kind, key=key, value=json.dumps(value), changed_at=1))
    app.business.controls.refresh(force=True)


def save(c, changes):
    return c('POST', '/api/_controls/overrides', {'changes': [dict(kind=kind, key=key, value=value) for kind, key, value in changes]})


def test_removed_targets_reset_together_with_audit_and_config_refresh(secured):
    retired = [
        ('field', 'RetiredModel.retired_field', {'label': 'Old label'}),
        ('layout', 'RetiredModel', [{'title': 'Old section', 'fields': []}]),
        ('list', 'RetiredModel', {'columns': ['old'], 'page_size': 25, 'sort': '-id'}),
        ('naming', 'RetiredModel', [{'id': 'old', 'format': 'OLD-{####}', 'when': ''}]),
        ('perm', 'retired_role|Customer', {'ops': ['read'], 'read': None, 'write': None, 'scope': None}),
        ('perm', 'editor|RetiredModel', {'ops': [], 'read': None, 'write': None, 'scope': None}),
        ('setting', 'retired.setting', True),
        ('state', 'RetiredModel.old', {'label': 'Old state'}),
        ('rule', 'RetiredModel.old', {'label': 'Old rule'}),
    ]
    seed(secured, retired)
    c = client(secured)
    initial = c('GET', '/api/_controls/overrides')[1]
    keys = {(kind, key) for kind, key, _ in retired}
    assert keys <= {(row['kind'], row['key']) for row in initial['items']}
    assert initial['problems']
    status, result, _ = save(c, [(kind, key, None) for kind, key in keys])
    assert status == 200 and result['saved'] == len(keys), result
    current = c('GET', '/api/_controls/overrides')[1]
    assert current['version'] > initial['version']
    assert keys.isdisjoint((row['kind'], row['key']) for row in current['items'])
    assert not current['problems']
    with secured.database._connection() as connection:
        rows = connection.run(qb.select('evlet_audit', 'actor', 'target', 'detail', 'before', 'after')
                              .where(detail='Reset to the file value')).all()
    assert len(rows) == len(keys)
    assert all(row['actor'] == 'admin' and row['before'] is not None and row['after'] is None for row in rows)


def test_retired_reset_does_not_allow_new_invalid_targets_or_partial_batches(secured):
    retired = ('field', 'RetiredModel.old', {'hidden': True})
    seed(secured, [retired])
    c = client(secured)
    for changes in (
        [('field', 'RetiredModel.old', None), ('field', 'RetiredModel.other', {'hidden': True})],
        [('field', 'RetiredModel.other', None)],
        [('unknown', 'RetiredModel.old', None)],
        [('custom', 'RetiredModel', None)],
    ):
        assert save(c, changes)[0] == 422
    stored = c('GET', '/api/_controls/overrides')[1]
    assert next(row for row in stored['items'] if row['key'] == retired[1])['value'] == retired[2]
    assert save(client(secured, 'viewer'), [('field', retired[1], None)])[0] == 404


def test_stored_reset_preserves_admin_only_settings(secured):
    c = client(secured)
    assert c('POST', '/api/_controls/roles', {'id': 'ops', 'base': 'editor'})[0] == 201
    assert c('PATCH', '/api/_controls/roles/ops', {'opens_controls': True})[0] == 200
    from tests.test_business import PASSWORD
    assert c('POST', '/api/_controls/users', {'username': 'operator', 'password': PASSWORD, 'role': 'ops'})[0] == 201
    assert save(c, [('setting', 'security.controls_two_step', False)])[0] == 200
    ops = client(secured, 'operator')
    assert save(ops, [('setting', 'security.controls_two_step', None)])[0] == 403
    assert save(c, [('setting', 'security.controls_two_step', None)])[0] == 200
