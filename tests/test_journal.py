"""Phase 8: the Journal. Engine and user entries, permissions, append-only storage and hash chains, on real SQLite."""

import shutil
import sqlite3

import pytest

from evlet import (Application, Attachments, Boolean, Child, ConflictError, Currency, Database, Decimal, EventStore, Integer, Journal, LocalAuth,
                   Link, Model, Move, Registry, Rule, Sequence, State, Table, Text, action)
from evlet import journal as journal_module
from evlet.auth import User
from evlet.calc import F
from evlet.identity import RecordId
from evlet.permissions import Policy
from finite_fixtures import FiniteMeta
from test_business import PASSWORD, client, http
from test_rules_types import ALL, add_user, start


class Account(Model):
    permissions = {**ALL, 'viewer': Rule(('read',), scope=lambda user: {'public': True})}
    title = Text(default='')
    public = Boolean(default=True)


class Remark(Child):
    text = Text(default='')


class Item(Child):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update', 'delete')),
                   'viewer': Rule(('read',), read_fields=('qty', 'rate', 'shared'), scope=lambda user: {'shared': True})}
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))
    secret = Text(default='')
    shared = Boolean(default=True)
    remarks = Table(Remark)


class InvoiceMeta(FiniteMeta):
    revisable = True
    journal = Journal(tamper_evident=True)


class Invoice(Model):
    Meta = InvoiceMeta
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise')),
                   'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'owner': user.id}),
                   'viewer': Rule(('read',), read_fields=('title', 'owner', 'account'))}
    title = Text(default='')
    cost = Currency(default=0)
    owner = Integer(default=0)
    account = Link(Account)
    number = Sequence('INV-{n:03}', assign_on='issued')
    lines = Table(Item)
    total = Currency(compute=lines.sum('amount'))
    status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),),
                   locked=('issued', 'void'), final=('void',), voidable=('issued',))

    @action('Rename', inputs={'title': Text(required=True)})
    def rename(self, title):
        self.title = title


class Memo(Model):
    """No State: the journal is turned on by Meta.journal, which also makes the model a document."""
    class Meta:
        journal = Journal()
        attachments = Attachments(max_files=3, max_size=1000)
    permissions = ALL
    title = Text(default='')


class Ticket(Model):
    """A Rule names 'journal' for the viewer, which adds it to admin and the editor (read and update)."""
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
                   'viewer': Rule(('read', 'journal'))}
    title = Text(default='')
    status = State(('open', 'closed'), moves=(Move('open', 'closed', 'Close'),))


class Notice(Model):
    """The editor only reads, so only admin may post; naming 'journal' for the viewer never removes admin."""
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)),
                   'viewer': Rule(('read', 'journal'))}
    title = Text(default='')
    status = State(('open', 'closed'), moves=(Move('open', 'closed', 'Close'),))


class Plain(Model):
    permissions = ALL
    title = Text(default='')


class Silent(Model):
    """A State with the journal turned off."""
    class Meta:
        journal = None
    permissions = ALL
    title = Text(default='')
    status = State(('open', 'closed'), moves=(Move('open', 'closed', 'Close'),))


MODELS = (Account, Invoice, Memo, Ticket, Notice, Plain, Silent)


@pytest.fixture
def app(tmp_path):
    return seed(start(Database(tmp_path / 'db.sqlite3', Registry(*MODELS))))


# The text ids of the accounts the latest seed() created, by name.
ACCOUNTS = {}


def seed(app):
    """Users admin (1), editor (2) and viewer (3); a public and a private account."""
    add_user(app, 'editor')
    add_user(app, 'viewer')
    c = client(app)
    for name, public in (('Public', True), ('Private', False)):
        status, account, _ = c('POST', '/api/account', {'title': name, 'public': public})
        assert status == 201, account
        ACCOUNTS[name] = account['id']
    return app


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def edit(c, url, body):
    return c('PATCH', url, body, headers=etag(c, url))


def post(c, url, operation, body=None):
    return c('POST', f'{url}/{operation}', body or {}, headers=etag(c, url))


def history(c, url, query=''):
    status, page, _ = c('GET', f'{url}/journal{query}')
    assert status == 200, page
    return page


def new_invoice(c, **values):
    body = {'title': 'First', 'cost': '5', 'owner': 2, 'account': ACCOUNTS['Private'], 'children': {'lines': [
        {'_key': 'a', 'qty': '2', 'rate': '5', 'secret': 's1', 'children': {'remarks': [{'_key': 'r1', 'text': 'x'}]}},
        {'_key': 'b', 'qty': '1', 'rate': '3', 'shared': False}]}, **values}
    status, record, _ = c('POST', '/api/invoice', body)
    assert status == 201, record
    return record, f"/api/invoice/{record['id']}"


def stored(app):
    with app.database.transaction() as tx:
        return [dict(zip(('id', 'model', 'record_id', 'kind', 'actor', 'prev_hash', 'hash'), row)) for row in tx.connection.execute(
            'SELECT id, model, record_id, kind, actor, prev_hash, hash FROM evlet_journal ORDER BY id').fetchall()]


# Declarations

def test_journal_declarations_defaults_and_rules():
    with pytest.raises(ValueError, match='True or False'):
        Journal(tamper_evident='yes')
    registry = Registry(*MODELS)
    assert set(registry.journals) == {Invoice, Memo, Ticket, Notice}
    assert registry.journals[Invoice].tamper_evident and not registry.journals[Memo].tamper_evident
    assert Memo.__declaration_mode__
    with pytest.raises(ValueError, match='Journal'):
        Registry(type('Wrong', (Model,), {'Meta': type('Meta', (), {'journal': True}), 'title': Text()}))
    row = type('Row', (Child,), {'Meta': type('Meta', (), {'journal': Journal()}), 'text': Text()})
    with pytest.raises(ValueError, match='Table rows'):
        Registry(type('Holder', (Model,), {'Meta': FiniteMeta, 'rows': Table(row)}))
    with pytest.raises(ValueError, match='no journal'):
        Policy(Registry(Plain), rules={('editor', Plain): Rule(('read', 'journal'))})
    policy = Policy(registry)
    roles = lambda model: {role for role in ('admin', 'editor', 'viewer') if policy.allows(User(1, role, role), model, 'journal')}
    assert roles(Invoice) == roles(Memo) == {'admin', 'editor'}
    assert roles(Ticket) == {'admin', 'editor', 'viewer'} and roles(Notice) == {'admin', 'viewer'}
    assert roles(Plain) == roles(Silent) == set()


def test_custom_roles_and_overrides_decide_who_may_journal():
    """A role based on the editor journals like the editor; Controls overrides can take 'journal' away."""
    registry = Registry(*MODELS)
    roles = {'admin': 'admin', 'editor': 'editor', 'viewer': 'viewer', 'portal': 'portal', 'sales': 'editor', 'clerk': 'viewer'}
    policy = Policy(registry, roles=roles)
    journalers = lambda policy, model: {role for role in roles if policy.allows(User(1, role, role), model, 'journal')}
    assert journalers(policy, Memo) == {'admin', 'editor', 'sales'}
    assert journalers(policy, Ticket) == {'admin', 'editor', 'viewer', 'sales', 'clerk'}
    stricter = Policy(registry, roles=roles, overrides={('viewer', Ticket): Rule(('read',)), ('sales', Memo): Rule(('read',))})
    assert journalers(stricter, Ticket) == {'admin', 'editor', 'sales', 'clerk'}
    assert journalers(stricter, Memo) == {'admin', 'editor'}


# Engine entries

def check_engine(app):
    c = client(app)
    record, url = new_invoice(c)
    rows = {row['qty']: row['id'] for row in record['children']['lines']}
    created = history(c, url)['items'][0]
    assert (created['kind'], created['actor']) == ('created', 1)
    fields = created['data']['fields']
    assert fields['title'] == {'old': None, 'new': 'First'} and fields['total'] == {'old': None, 'new': '13.00'}
    assert fields['status'] == {'old': None, 'new': 'draft'} and fields['account'] == {'old': None, 'new': ACCOUNTS['Private']}
    # Numbers have their own entries; row links and positions are not values.
    assert 'number' not in fields and 'revises' not in fields
    added = created['data']['rows']['lines']['added']
    assert [item['fields']['qty']['new'] for item in added] == ['2.0000', '1.0000']
    assert set(added[0]['fields']) == {'qty', 'rate', 'amount', 'secret', 'shared'}
    assert added[0]['rows']['remarks']['added'][0]['fields'] == {'text': {'old': None, 'new': 'x'}}
    # A reorder alone is recorded as such.
    edit(c, url, {'children': {'lines': {'order': [rows['1.0000'], rows['2.0000']]}}})
    assert history(c, url)['items'][0]['data'] == {'rows': {'lines': {'reordered': True}}}
    # Field and row changes in one write: an update, a removal with its old values, and an addition.
    status, saved, _ = edit(c, url, {'title': 'Second', 'children': {'lines': {
        'update': [{'id': rows['2.0000'], 'qty': '3', 'children': {'remarks': {'create': [{'_key': 'r2', 'text': 'y'}]}}}],
        'delete': [rows['1.0000']], 'create': [{'_key': 'c', 'qty': '1', 'rate': '1'}]}}})
    assert status == 200, saved
    changed = history(c, url)['items'][0]
    assert changed['kind'] == 'changed' and changed['data']['fields'] == {
        'title': {'old': 'First', 'new': 'Second'}, 'total': {'old': '13.00', 'new': '16.00'}}
    lines = changed['data']['rows']['lines']
    assert lines['added'][0]['id'] == saved['_row_ids']['c']
    assert lines['updated'] == [{'id': rows['2.0000'], 'fields': {'qty': {'old': '2.0000', 'new': '3.0000'}, 'amount': {'old': '10.00', 'new': '15.00'}},
                                 'rows': {'remarks': {'added': [{'id': saved['_row_ids']['r2'], 'fields': {'text': {'old': None, 'new': 'y'}}}]}}}]
    assert lines['removed'][0]['id'] == rows['1.0000'] and lines['removed'][0]['fields']['rate'] == {'old': '3.00', 'new': None}
    assert 'reordered' not in lines
    # A write that changes nothing adds nothing.
    count = history(c, url)['total']
    edit(c, url, {'title': 'Second'})
    assert history(c, url)['total'] == count
    # An action names itself.
    assert post(c, url, 'actions/rename', {'title': 'Third'})[0] == 200
    assert history(c, url)['items'][0]['data'] == {'fields': {'title': {'old': 'Second', 'new': 'Third'}}, 'action': 'rename'}
    # A move, then the number it assigned.
    assert post(c, url, 'moves/issued')[0] == 200
    number, moved = history(c, url)['items'][:2]
    assert moved['kind'] == 'moved' and moved['data'] == {'from': 'draft', 'to': 'issued', 'move': 'Issue'}
    assert number['kind'] == 'number' and number['data'] == {'field': 'number', 'number': 'INV-001'}
    # A void keeps its reason.
    assert post(c, url, 'void', {'reason': 'Wrong customer'})[0] == 200
    assert history(c, url)['items'][0]['data'] == {'from': 'issued', 'to': 'void', 'reason': 'Wrong customer'}
    # A revise voids the original, links it to the copy, and the copy starts with `created`.
    other, other_url = new_invoice(c, title='Other')
    post(c, other_url, 'moves/issued')
    status, copy, _ = post(c, other_url, 'revise', {'reason': 'New prices'})
    assert status == 201, copy
    revised, voided = history(c, other_url)['items'][:2]
    assert voided['data'] == {'from': 'issued', 'to': 'void', 'reason': 'New prices'}
    assert revised['kind'] == 'revised' and revised['data'] == {'copy': copy['id'], 'reason': 'New prices'}
    copied = history(c, f"/api/invoice/{copy['id']}")
    assert copied['total'] == 1 and copied['items'][0]['kind'] == 'created'
    assert copied['items'][0]['data']['fields']['revises'] == {'old': None, 'new': other['id']}
    assert len(copied['items'][0]['data']['rows']['lines']['added']) == 2
    # Attachments, and a deleted record whose history stays.
    memo = c('POST', '/api/memo', {'title': 'Memo'})[1]
    memo_url = f"/api/memo/{memo['id']}"
    status, attached, _ = c('POST', memo_url + '/attachments', {'name': 'a.txt', 'content': 'aGVsbG8='})
    assert status == 201, attached
    assert c('DELETE', f"{memo_url}/attachments/{attached['id']}")[0] == 204
    removed, added_file, created_memo = history(c, memo_url)['items']
    assert created_memo['kind'] == 'created' and created_memo['data'] == {'fields': {'title': {'old': None, 'new': 'Memo'}}}
    assert added_file['data'] == {'action': 'added', 'id': attached['id'], 'name': 'a.txt', 'size': 5}
    assert removed['data'] == {'action': 'removed', 'id': attached['id'], 'name': 'a.txt'}
    assert c('DELETE', memo_url, headers=etag(c, memo_url))[0] == 204
    assert c('GET', memo_url + '/journal')[0] == 404
    assert [row['kind'] for row in stored(app) if row['model'] == 'memo'] == ['created', 'file', 'file', 'deleted']
    # Models without a journal.
    plain = c('POST', '/api/plain', {'title': 'Plain'})[1]
    assert c('GET', f"/api/plain/{plain['id']}/journal")[0] == 404
    silent = c('POST', '/api/silent', {'title': 'Silent'})[1]
    assert post(c, f"/api/silent/{silent['id']}", 'moves/closed')[0] == 200
    assert c('GET', f"/api/silent/{silent['id']}/journal")[0] == 404
    assert {row['model'] for row in stored(app)} == {'invoice', 'memo'}
    models = {model['name']: model for model in c('GET', '/api/schema')[1]['models']}
    assert 'journal' not in models['plain'] and 'journal' not in models['silent']
    assert models['memo']['journal'] == {'tamper_evident': False, 'kinds': ['note', 'message', 'call', 'follow_up', 'follow_up_done']}


def test_engine_writes_entries_for_every_change(app):
    check_engine(app)


# Reading with permissions

def check_reading(app):
    c, editor, viewer = client(app), client(app, 'editor'), client(app, 'viewer')
    record, url = new_invoice(c)
    rows = {row['qty']: row['id'] for row in record['children']['lines']}
    post(c, url, 'moves/issued')
    seen = history(viewer, url)
    number, moved, created = seen['items']
    # The viewer reads title, owner and account; the private account's id is hidden; so is the State.
    assert created['partial'] and created['data']['fields'] == {
        'title': {'old': None, 'new': 'First'}, 'owner': {'old': None, 'new': 2}, 'account': {'old': None, 'new': None}}
    # Rows outside the viewer's row scope are left out; row fields it cannot read are removed.
    added = created['data']['rows']['lines']['added']
    assert [item['id'] for item in added] == [rows['2.0000']]
    assert added[0]['fields'] == {'qty': {'old': None, 'new': '2.0000'}, 'rate': {'old': None, 'new': '5.00'}, 'shared': {'old': None, 'new': True}}
    assert added[0]['rows']['remarks']['added'][0]['fields'] == {'text': {'old': None, 'new': 'x'}}
    # Entries about a State or a number the caller cannot read show as changes with nothing in them.
    assert moved == {**moved, 'kind': 'changed', 'data': {}, 'partial': True}
    assert number['kind'] == 'changed' and number['data'] == {} and number['partial']
    assert seen['total'] == 3 and seen['users'] == {'1': 'admin'}
    # The editor reads everything of its own invoices, and nothing of others.
    mine = history(editor, url)['items']
    assert mine[1]['data'] == {'from': 'draft', 'to': 'issued', 'move': 'Issue'} and 'partial' not in mine[2]
    _, theirs = new_invoice(c, owner=1)
    assert editor('GET', theirs + '/journal')[0] == 404
    assert editor('POST', theirs + '/journal', {'kind': 'note', 'text': 'Hi'})[0] == 404
    # An action's name is left out for roles that cannot read the State.
    renamed, renamed_url = new_invoice(c)
    assert post(c, renamed_url, 'actions/rename', {'title': 'Renamed'})[0] == 200
    assert history(viewer, renamed_url)['items'][0]['data'] == {'fields': {'title': {'old': 'First', 'new': 'Renamed'}}}
    assert history(c, renamed_url)['items'][0]['data']['action'] == 'rename'
    # Removed rows are hidden under a row scope, since they can no longer be checked against it.
    draft, draft_url = new_invoice(c)
    lines = {row['qty']: row['id'] for row in draft['children']['lines']}
    edit(c, draft_url, {'children': {'lines': {'delete': [lines['2.0000']]}}})
    change = history(viewer, draft_url)['items'][0]
    assert change['partial'] and 'rows' not in change['data']
    assert history(c, draft_url)['items'][0]['data']['rows']['lines']['removed'][0]['id'] == lines['2.0000']
    # Paging, newest first.
    page = history(c, url, '?limit=2&offset=1')
    assert [item['kind'] for item in page['items']] == ['moved', 'created'] and (page['limit'], page['offset'], page['total']) == (2, 1, 3)
    for query in ('?limit=0', '?limit=101', '?offset=-1', '?page=1', '?limit=1&limit=2'):
        assert c('GET', f'{url}/journal{query}')[0] == 422, query
    assert c('GET', '/api/invoice/999/journal')[0] == 404


def test_reading_removes_values_rows_and_states_the_caller_cannot_read(app):
    check_reading(app)


# User entries

def check_user_entries(app):
    c, editor, viewer = client(app), client(app, 'editor'), client(app, 'viewer')
    _, url = new_invoice(c)
    assert editor('GET', url)[1]['_journal'] == {'count': 1, 'open_follow_ups': 0}
    write = lambda body, who=editor: who('POST', url + '/journal', body)
    status, note, _ = write({'kind': 'note', 'text': '  Called back  '})
    assert status == 201 and (note['kind'], note['actor'], note['data']) == ('note', 2, {'text': 'Called back'})
    assert write({'kind': 'message', 'text': 'Sent prices', 'subject': 'Prices', 'party': 'Acme'})[1]['data'] == {
        'text': 'Sent prices', 'subject': 'Prices', 'party': 'Acme', 'direction': 'out'}
    assert write({'kind': 'call', 'text': 'Asked for terms', 'direction': 'in', 'minutes': 12})[1]['data'] == {
        'text': 'Asked for terms', 'party': None, 'direction': 'in', 'minutes': 12}
    status, follow_up, _ = write({'kind': 'follow_up', 'text': 'Send the contract', 'due': '2026-11-10', 'assignee': 3})
    assert status == 201 and follow_up['data'] == {'text': 'Send the contract', 'due': '2026-11-10', 'assignee': 3}
    assert follow_up['done'] is None
    for body, key in (({'kind': 'note'}, 'text'), ({'kind': 'note', 'text': '   '}, 'text'), ({'kind': 'note', 'text': 'x' * 10001}, 'text'),
                      ({'kind': 'note', 'text': 'Hi', 'due': '2026-01-01'}, '_form'), ({'kind': 'created', 'text': 'Hi'}, 'kind'),
                      ({'text': 'Hi'}, 'kind'), ({'kind': 'message', 'text': 'Hi', 'direction': 'sideways'}, 'direction'),
                      ({'kind': 'message', 'text': 'Hi', 'subject': 's' * 201}, 'subject'),
                      ({'kind': 'call', 'text': 'Hi', 'minutes': -1}, 'minutes'), ({'kind': 'call', 'text': 'Hi', 'minutes': True}, 'minutes'),
                      ({'kind': 'follow_up', 'text': 'Hi', 'due': '10-11-2026'}, 'due'),
                      ({'kind': 'follow_up', 'text': 'Hi', 'due': '2026-02-30'}, 'due'),
                      ({'kind': 'follow_up', 'text': 'Hi', 'assignee': 999}, 'assignee'),
                      ({'kind': 'follow_up', 'text': 'Hi', 'assignee': '2'}, 'assignee'),
                      ({'kind': 'follow_up_done', 'follow_up': note['id']}, 'follow_up'),
                      ({'kind': 'follow_up_done', 'follow_up': 999}, 'follow_up')):
        status, error, _ = write(body)
        assert status == 422 and key in error['fields'], (body, error)
    # The assignee must be able to read the record.
    _, theirs = new_invoice(c, owner=1)
    assert c('POST', theirs + '/journal', {'kind': 'follow_up', 'text': 'Hi', 'assignee': 2})[1]['fields'] == {
        'assignee': 'This user cannot read this record'}
    assert editor('GET', url)[1]['_journal'] == {'count': 5, 'open_follow_ups': 1}
    # A follow up is completed once, by a role that may write the journal.
    assert write({'kind': 'follow_up_done', 'follow_up': follow_up['id']}, viewer)[0] == 403
    status, done, _ = write({'kind': 'follow_up_done', 'follow_up': follow_up['id'], 'text': 'Signed'})
    assert status == 201 and done['data'] == {'follow_up': follow_up['id'], 'text': 'Signed'}
    assert write({'kind': 'follow_up_done', 'follow_up': follow_up['id']})[0] == 409
    page = history(viewer, url)
    assert [item['kind'] for item in page['items']] == ['follow_up_done', 'follow_up', 'call', 'message', 'note', 'created']
    assert page['items'][1]['done'] == done['id'] and page['users'] == {'1': 'admin', '2': 'editor', '3': 'viewer'}
    assert viewer('GET', url)[1]['_journal'] == {'count': 6, 'open_follow_ups': 0}
    # Viewers cannot post; entries can be added on a locked or void record; nothing edits or deletes them.
    assert write({'kind': 'note', 'text': 'Hi'}, viewer)[0] == 403
    post(c, url, 'moves/issued')
    post(c, url, 'void', {'reason': 'Duplicate'})
    assert write({'kind': 'note', 'text': 'Kept for the audit'})[0] == 201
    for method in ('PATCH', 'DELETE', 'PUT'):
        assert c(method, url + '/journal', {'kind': 'note', 'text': 'x'})[0] == 405, method
    assert c('DELETE', f"{url}/journal/{note['id']}")[0] in (404, 405)
    assert c('POST', '/api/plain/1/journal', {'kind': 'note', 'text': 'x'})[0] == 404
    schema = {role: next(model for model in who('GET', '/api/schema')[1]['models'] if model['name'] == 'invoice')['journal']
              for role, who in (('editor', editor), ('viewer', viewer))}
    assert schema['editor']['kinds'] == ['note', 'message', 'call', 'follow_up', 'follow_up_done'] and schema['viewer']['kinds'] == []
    assert schema['viewer']['tamper_evident'] is True
    # A Rule naming 'journal' adds roles; an editor that only reads may not post.
    ticket = c('POST', '/api/ticket', {'title': 'T'})[1]
    ticket_url = f"/api/ticket/{ticket['id']}"
    assert viewer('POST', ticket_url + '/journal', {'kind': 'note', 'text': 'Seen'})[0] == 201
    assert editor('POST', ticket_url + '/journal', {'kind': 'note', 'text': 'Seen'})[0] == 201
    notice = c('POST', '/api/notice', {'title': 'N'})[1]
    notice_url = f"/api/notice/{notice['id']}"
    assert editor('POST', notice_url + '/journal', {'kind': 'note', 'text': 'Seen'})[0] == 403
    assert c('POST', notice_url + '/journal', {'kind': 'note', 'text': 'Seen'})[0] == 201
    assert viewer('POST', notice_url + '/journal', {'kind': 'note', 'text': 'Seen'})[0] == 201
    # Text must be valid Unicode, like record fields.
    for body in ({'kind': 'note', 'text': 'a\ud800b'}, {'kind': 'message', 'text': 'x', 'party': '\udfff'}):
        status, error, _ = write(body)
        assert status == 422 and list(error['fields'].values()) == ['Expected valid Unicode text'], error


def test_user_entries_are_validated_and_append_only(app):
    check_user_entries(app)


def test_python_api_posts_and_reads_with_the_same_checks(app):
    c = client(app)
    _, url = new_invoice(c)
    record_id = RecordId(url.rsplit('/', 1)[1])
    with app.business.context(User(3, 'viewer', 'viewer')) as context:
        with pytest.raises(Exception, match='cannot write'):
            journal_module.post(context, Invoice, record_id, 'note', text='Hi')
        assert journal_module.entries(context, 'invoice', record_id)['total'] == 1
    with app.business.context(User(2, 'editor', 'editor')) as context:
        entry = journal_module.post(context, Invoice, record_id, 'note', text='From code')
    assert history(c, url)['items'][0]['id'] == entry['id']


def test_journal_entries_are_published_as_events(tmp_path):
    database = Database(tmp_path / 'events.sqlite3', Registry(*MODELS))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      events=EventStore(database))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    c = client(app)
    memo = c('POST', '/api/memo', {'title': 'Memo'})[1]
    cursor = c('GET', '/api/_events')[1]['cursor']
    assert c('POST', f"/api/memo/{memo['id']}/journal", {'kind': 'note', 'text': 'Hi'})[0] == 201
    assert c('GET', f'/api/_events?cursor={cursor}')[1]['items'] == [{'model': 'memo', 'id': memo['id'], 'deleted': False, 'journal': True}]


# Storage

def check_append_only_and_chain(app):
    c = client(app)
    _, url = new_invoice(c)
    post(c, url, 'moves/issued')
    for text in ('One', 'Two', 'Three'):
        c('POST', url + '/journal', {'kind': 'note', 'text': text})
    memo = c('POST', '/api/memo', {'title': 'Memo'})[1]
    entries = stored(app)
    invoice = [row for row in entries if row['model'] == 'invoice']
    # Memo entries are not hashed; invoice entries form one chain per record.
    assert all(row['hash'] is None for row in entries if row['model'] == 'memo')
    assert invoice[0]['prev_hash'] is None and all(row['prev_hash'] == before['hash'] for before, row in zip(invoice, invoice[1:]))
    assert journal_module.verify(app.database) == (len(invoice), [])
    for sql in ('UPDATE evlet_journal SET data=? WHERE id=?', 'DELETE FROM evlet_journal WHERE id=?'):
        # The append-only triggers refuse the change.
        with pytest.raises((ConflictError, sqlite3.OperationalError)):
            with app.database.transaction() as tx:
                tx.connection.execute(sql, ('{}', invoice[1]['id']) if 'UPDATE' in sql else (invoice[1]['id'],))
    assert stored(app) == entries
    # Someone with direct database access removes the guard and edits or deletes entries.
    with app.database.transaction() as tx:
        tx.connection.execute('DROP TRIGGER evlet_journal_no_update')
        tx.connection.execute('DROP TRIGGER evlet_journal_no_delete')
        # Entries: created, moved, number and three notes.
        tx.connection.execute('DELETE FROM evlet_journal WHERE id=?', (invoice[1]['id'],))
        tx.connection.execute("UPDATE evlet_journal SET data='{\"text\":\"Changed\"}' WHERE id=?", (invoice[3]['id'],))
        tx.connection.execute('UPDATE evlet_journal SET hash=NULL, prev_hash=NULL WHERE id=?', (invoice[5]['id'],))
    checked, breaks = journal_module.verify(app.database)
    assert checked == len(invoice) - 2
    assert [(entry, message) for entry, _, _, message in breaks] == [
        (invoice[2]['id'], 'the previous hash does not match the entry before it'),
        (invoice[3]['id'], 'the hash does not match the entry'),
        (invoice[5]['id'], 'this entry has no hash, but its model is tamper evident')]
    assert {(model, record_id) for _, model, record_id, _ in breaks} == {('invoice', invoice[0]['record_id'])}
    # Stripping every hash of a record of a tamper-evident model is reported too.
    _, other = new_invoice(c)
    other_id = RecordId(other.rsplit('/', 1)[1])
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_journal SET hash=NULL, prev_hash=NULL WHERE model=? AND record_id=?', ('invoice', other_id))
    stripped = [entry for entry in journal_module.verify(app.database)[1] if entry[2] == other_id]
    assert [message for *_, message in stripped] == ['this entry has no hash, but its model is tamper evident']
    assert memo['id']


def test_entries_are_append_only_and_tampering_is_detected(app):
    check_append_only_and_chain(app)


def test_insert_or_replace_cannot_overwrite_an_entry_and_old_databases_get_the_guard(app):
    c = client(app)
    _, url = new_invoice(c)
    first = stored(app)[0]
    with pytest.raises(ConflictError):
        with app.database.transaction() as tx:
            tx.connection.execute("INSERT OR REPLACE INTO evlet_journal (id, model, record_id, kind, actor, at, data) "
                                  "VALUES (?, 'invoice', 1, 'note', '1', 'x', '{}')", (first['id'],))
    assert stored(app)[0] == first
    # A database created before the guard existed gets it on the next start.
    with app.database.transaction() as tx:
        tx.connection.execute('DROP TRIGGER evlet_journal_no_replace')
    app.initialize()
    with app.database.transaction() as tx:
        assert tx.connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_journal_no_replace'").fetchone()
    assert journal_module.verify(app.database)[1] == []


def test_post_runs_in_its_own_savepoint_with_the_recursion_guard(app, monkeypatch):
    c = client(app)
    record, url = new_invoice(c)
    real = journal_module.shown
    def fail(*args):
        raise RuntimeError('reading the entry back failed')
    with app.business.context(User(2, 'editor', 'editor')) as context:
        monkeypatch.setattr(journal_module, 'shown', fail)
        with pytest.raises(RuntimeError):
            journal_module.post(context, Invoice, record['id'], 'note', text='Lost')
        monkeypatch.setattr(journal_module, 'shown', real)
        journal_module.post(context, Invoice, record['id'], 'note', text='Kept')
        # Posting to the record an operation is changing is refused, like any recursive change.
        context._active.append((Invoice, RecordId(record['id'])))
        with pytest.raises(Exception, match='Recursive'):
            journal_module.post(context, Invoice, record['id'], 'note', text='Inner')
        context._active.pop()
    assert [item['data'].get('text') for item in history(c, url)['items']] == ['Kept', None]


def test_verify_command_reports_breaks(app, tmp_path, capsys):
    from evlet.cli.main import main
    c = client(app)
    record, url = new_invoice(c)
    post(c, url, 'moves/issued')
    root = tmp_path / 'project'
    (root / 'apps').mkdir(parents=True)
    instance = root / 'instances' / 'acme'
    (instance / 'data').mkdir(parents=True)
    (instance / 'config.py').write_text("from evlet import Instance\n\ninstance = Instance(name='acme', domain=None)\n")
    app.database.close()  # the last connection to close folds the write-ahead log into the file it copies
    shutil.copy(app.database.path, instance / 'data' / 'database.sqlite3')
    main(['journal', 'verify', 'acme', '--project', str(root)])
    assert capsys.readouterr().out == 'Journal verified: 3 hashed entries, no breaks\n'
    with sqlite3.connect(instance / 'data' / 'database.sqlite3') as connection:
        connection.execute('DROP TRIGGER evlet_journal_no_update')
        connection.execute("UPDATE evlet_journal SET actor='3' WHERE id=2")
    with pytest.raises(SystemExit) as exit:
        main(['journal', 'verify', 'acme', '--project', str(root)])
    assert exit.value.code == 1
    output = capsys.readouterr()
    assert output.out == f"Entry 2 (invoice {record['id']}): the hash does not match the entry\n"
    assert output.err == '1 break in 3 hashed entries\n'
    with pytest.raises(SystemExit):
        main(['journal', 'verify', 'other', '--project', str(root)])


def check_rollback(app, monkeypatch):
    c = client(app)
    record, url = new_invoice(c)
    def fail(*args):
        raise RuntimeError('number entry failed')
    with app.business.context(User(1, 'admin', 'admin')) as context:
        monkeypatch.setattr(journal_module, 'number', fail)
        with pytest.raises(RuntimeError):
            context.move(Invoice, record['id'], 'issued')
        monkeypatch.undo()
        context.update(Invoice, record['id'], title='After')
    saved = c('GET', url)[1]
    assert saved['status'] == 'draft' and saved['number'] is None and saved['title'] == 'After'
    assert [item['kind'] for item in history(c, url)['items']] == ['changed', 'created']
    assert journal_module.verify(app.database)[1] == []
    assert post(c, url, 'moves/issued')[1]['number'] == 'INV-001'


def test_a_failed_operation_leaves_no_journal_entry(app, monkeypatch):
    check_rollback(app, monkeypatch)


# Statement budget

class BigLine(Child):
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))


class QuietMeta(FiniteMeta):
    journal = None


class HashedMeta(FiniteMeta):
    journal = Journal(tamper_evident=True)


class BigQuiet(Model):
    Meta = QuietMeta
    permissions = ALL
    title = Text(default='')
    lines = Table(BigLine, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued',))


class BigHashed(Model):
    Meta = HashedMeta
    permissions = ALL
    title = Text(default='')
    lines = Table(BigLine, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued',))


def test_a_journaled_patch_of_a_1000_row_document_costs_a_bounded_number_of_statements(tmp_path):
    from test_lines import Counting, counted
    app = start(Counting(tmp_path / 'big.sqlite3', Registry(BigQuiet, BigHashed)))
    c = client(app)
    rows = [{'_key': f'l{index}', 'qty': '1', 'rate': '2.50'} for index in range(1000)]
    counts = {}
    for name in ('bigquiet', 'bighashed'):
        record = c('POST', f'/api/{name}', {'children': {'lines': rows}})[1]
        url = f"/api/{name}/{record['id']}"
        ids = [row['id'] for offset in range(0, 1000, 100) for row in c('GET', f'{url}/lines/lines?offset={offset}')[1]['items']]
        body = {'title': 'All', 'children': {'lines': {'update': [{'id': row_id, 'qty': '2'} for row_id in ids]}}}
        headers = etag(c, url)
        (status, saved, _), counts[name] = counted(c, 'PATCH', url, body, headers=headers)
        assert status == 200 and saved['total'] == '5000.00'
    # The previous hash, the entry, the SQLite guard trigger that runs with the insert (traced as its
    # own statement, one primary key lookup), and the `_journal` summary of the response.
    assert counts['bighashed'] <= counts['bigquiet'] + 4, counts
    entry = history(c, url)['items'][0]
    assert len(entry['data']['rows']['lines']['updated']) == 1000 and entry['data']['fields']['total']['new'] == '5000.00'
    assert journal_module.verify(app.database)[1] == []
