"""Stored hidden values are never consulted to admit a caller's input."""

import json
from urllib.parse import urlencode

import pytest

from evlet import Boolean, Database, Link, Model, Registry, Rule, State, Text, User
from evlet.auth import AccessError
from test_business import client
from test_rules_types import add_user, start


FULL = Rule(('read', 'create', 'update', 'delete'))


def test_hidden_title_and_state_echoes_have_uniform_refusals(tmp_path):
    class Confidential(Model):
        permissions = {'admin': FULL, 'viewer': Rule(('read',), read_fields=('memo',)),
                       'editor': Rule(('read', 'update'), read_fields=('memo',), write_fields=('memo',))}
        memo = Text(default='visible')
        secret = Text(default='approved')
        status = State(State.Step('approved'), State.Step('denied'))

        class Meta:
            title_field = 'secret'

    app = start(Database(tmp_path / 'echo.sqlite3', Registry(Confidential)))
    admin = client(app)
    record = admin('POST', '/api/confidential', {})[1]
    path = '/api/confidential/' + record['id']
    viewer, editor = add_user(app, 'viewer'), add_user(app, 'editor')
    assert not {'title', 'secret', 'status'} & viewer('GET', path)[1].keys()
    for field in ('title', 'status'):
        for actor in (viewer, editor):
            answers = [actor('POST', '/api/_draft/confidential', {'id': record['id'], 'values': {field: guess}})[:2]
                       for guess in ('approved', 'denied', None, '')]
            assert all(answer == answers[0] and answer[0] == 403 for answer in answers)
        answers = [editor('PATCH', path, {field: guess}, headers={b'if-match': editor('GET', path)[2][b'etag']})[:2]
                   for guess in ('approved', 'denied')]
        assert answers[0] == answers[1] and answers[0][0] == 403
    # Readable unchanged fields still round-trip; trusted state writes retain their own authority.
    assert admin('PATCH', path, {'title': 'approved', 'status': 'approved'},
                 headers={b'if-match': admin('GET', path)[2][b'etag']})[0] == 200


def test_context_scoped_link_filters_match_http_and_keep_denials(tmp_path):
    class Account(Model):
        permissions = {'admin': FULL, 'editor': Rule(('read',), scope=lambda user: {'public': True})}
        label = Text()
        public = Boolean(default=False)

    class Ticket(Model):
        permissions = {'admin': FULL, 'editor': Rule(('read',))}
        account = Link(Account)
        memo = Text()

    app = start(Database(tmp_path / 'queries.sqlite3', Registry(Account, Ticket)))
    admin = client(app)
    allowed = admin('POST', '/api/account', {'label': 'Allowed', 'public': True})[1]['id']
    hidden = admin('POST', '/api/account', {'label': 'Hidden'})[1]['id']
    record = admin('POST', '/api/ticket', {'account': allowed, 'memo': 'test'})[1]
    editor = add_user(app, 'editor')
    account = editor('GET', '/api/auth/session')[1]['user']
    user = User(account['id'], account['username'], account['role'])
    with app.business.context(user, readonly=True) as context:
        for filters in ({'account': allowed}, {'account__in': [allowed]}):
            http = editor('GET', '/api/ticket?' + urlencode({'filters': json.dumps(filters)}))
            assert http[0] == 200
            page = context.list(Ticket, filters=filters)
            assert [{**row, 'id': str(row['id']), 'account': str(row['account'])} for row in page['rows']] == http[1]['items']
            assert str(page['rows'][0]['id']) == record['id']
            assert context.count(Ticket, filters=filters) == page['total'] == 1
        for filters in ({'account': hidden}, {'account__in': [allowed, hidden]}, {'account__ne': allowed}):
            with pytest.raises(AccessError):
                context.list(Ticket, filters=filters)
        with pytest.raises(AccessError):
            context.list(Ticket, order_by=('account',))
