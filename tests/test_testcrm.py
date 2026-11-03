"""End-to-end flows of testCRM (examples/testcrm) over the real ASGI app and a real SQLite database.

The app is installed into a disposable project exactly as scripts/testcrm.sh does, seeded with the
fictional data, and driven only through the authenticated HTTP routes the browser uses.
"""
from datetime import date, timedelta
from decimal import Decimal
import json
import sys
from urllib.parse import quote

import pytest

from evlet.instances import activate_generation
from scripts import testcrm
from test_business import http

PASSWORD = 'a long local test password'


def dubai_today():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Asia/Dubai')).date()


@pytest.fixture(scope='module')
def crm(tmp_path_factory):
    app = testcrm.build(tmp_path_factory.mktemp('testcrm') / 'project', frontend_port=8000)
    with activate_generation(app):
        app.initialize()
        testcrm.seed(app, PASSWORD, big_lines=150)
        yield app
        app.database.close()
    for name in list(sys.modules):
        if name.split('.')[0] == 'testcrm':
            del sys.modules[name]


def login(app, username):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': username, 'password': PASSWORD})
    if status == 200 and data.get('two_step'):
        # Admin needs two-step sign-in on an instance.
        status, data, headers = testcrm.finish_sign_in(app, username, data, lambda body: http(app, 'POST', '/api/auth/login', body))
    assert status == 200, data
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']

    def call(method, path, body=None, **kwargs):
        return http(app, method, path, body, cookie=cookie, csrf=csrf, **kwargs)

    def write(method, path, body=None):
        # Document writes send the ETag of the last read, as the browser does.
        return call(method, path, body, headers={b'if-match': call('GET', path.split('/moves/')[0].split('/actions/')[0]
                                                                       .removesuffix('/void').removesuffix('/revise')
                                                                       .removesuffix('/unpost').removesuffix('/repost'))[2][b'etag']})
    call.write = write
    return call


def find(call, model, **filters):
    query = quote(json.dumps(filters))
    items = call('GET', f'/api/{model}?limit=100&offset=0&filters={query}')[1]['items']
    assert len(items) == 1, items
    return items[0]


def deal_url(call, title):
    return '/api/deal/' + str(call('GET', '/api/deal?limit=5&offset=0&q=' + quote(title))[1]['items'][0]['id'])


def test_sign_in_and_navigation_schema(crm):
    assert http(crm, 'GET', '/api/schema')[0] == 401
    assert http(crm, 'POST', '/api/auth/login', {'username': 'sara', 'password': 'wrong password here'})[0] == 401
    sara = login(crm, 'sara')
    assert sara('GET', '/api/auth/session')[1]['user']['username'] == 'sara'
    models = {model['name']: model for model in sara('GET', '/api/schema')[1]['models']}
    assert {'organization', 'contact', 'product', 'deal', 'task', 'lead', 'booking'} <= set(models)
    deal = models['deal']
    # Sales people never see the internal notes; State, moves, key fields and relations drive the screens.
    assert 'internal_notes' not in {field['name'] for field in deal['fields']}
    assert deal['state']['field'] == 'stage' and [move['to'] for move in deal['state']['moves']][:3] == ['qualified', 'proposal', 'negotiation']
    assert deal['key_fields'] == ['organization', 'contact', 'total', 'expected_close', 'owner']
    assert [relation['name'] for relation in deal['relations']][0] == 'task.deal'
    assert deal['children'][0]['max_rows'] == 1000
    admin = login(crm, 'admin')
    assert 'internal_notes' in {field['name'] for field in next(m for m in admin('GET', '/api/schema')[1]['models'] if m['name'] == 'deal')['fields']}


def test_list_sort_search_filter_and_paging(crm):
    admin = login(crm, 'admin')
    everything = admin('GET', '/api/deal?limit=100&offset=0')[1]
    assert everything['total'] == 17
    by_total = admin('GET', '/api/deal?limit=5&offset=0&order_by=-total')[1]['items']
    totals = [Decimal(item['total']) for item in by_total]
    assert totals == sorted(totals, reverse=True) and by_total[0]['title'] == 'Group rollout, 150 sites'
    assert [item['title'] for item in admin('GET', '/api/deal?limit=50&offset=0&q=rollout')[1]['items']] == ['Group rollout, 150 sites', 'ERP rollout, 3 entities']
    proposals = admin('GET', '/api/deal?limit=50&offset=0&filters=' + quote(json.dumps({'stage': 'proposal'})))[1]
    assert proposals['total'] == 3 and {item['stage'] for item in proposals['items']} == {'proposal'}
    first, second = (admin('GET', f'/api/deal?limit=10&offset={offset}&order_by=number')[1] for offset in (0, 10))
    assert len(first['items']) == 10 and len(second['items']) == 7
    assert not {item['id'] for item in first['items']} & {item['id'] for item in second['items']}
    # The pipeline by stage and the declared measures behind the list strip and board totals.
    measures = admin('GET', '/api/deal/measures')[1]['measures']
    assert measures['deals'] == 17 and Decimal(measures['won']) > 0
    by_stage = admin('GET', '/api/deal/aggregate?measures=value:sum:total,deals:count&group_by=stage')[1]['items']
    assert {row['stage'] for row in by_stage} >= {'lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost'}
    assert sum(Decimal(row['value']) for row in by_stage if row['stage'] not in ('won', 'lost', 'void')) == Decimal(measures['pipeline'])
    # The measures and the aggregate follow the list's search box, with the list's readable search fields.
    rollout = admin('GET', '/api/deal?limit=50&offset=0&q=rollout')[1]
    searched = admin('GET', '/api/deal/measures?q=rollout')[1]['measures']
    assert searched['deals'] == rollout['total'] == 2
    assert Decimal(searched['pipeline']) + Decimal(searched['won']) == sum(Decimal(item['total']) for item in rollout['items']
                                                                          if item['stage'] not in ('lost', 'void'))
    stages = admin('GET', '/api/deal/aggregate?measures=n:count&group_by=stage&q=rollout')[1]['items']
    assert sum(row['n'] for row in stages) == 2
    assert login(crm, 'vera')('GET', '/api/deal/measures?q=' + 'x' * 201)[0] == 422
    # Won this month is a declared measure: won_on from the first of this month in Dubai, decided at query time.
    month = dubai_today().strftime('%Y-%m')
    won = admin('GET', '/api/deal/aggregate?measures=won:sum:total&group_by=won_on:month&filters=' + quote(json.dumps({'stage': 'won'})))[1]['items']
    assert sum(Decimal(row['won']) for row in won) == Decimal(measures['won'])
    assert Decimal(measures['won_this_month']) == sum(Decimal(row['won']) for row in won if row['won_on__month'] == month) > 0


def test_open_a_deal_and_edit_fields(crm):
    sara = login(crm, 'sara')
    url = deal_url(sara, 'HR and WPS payroll')
    deal = sara('GET', url)[1]
    assert deal['number'].startswith('DL-') and deal['stage'] == 'qualified' and deal['probability'] == '25.00'
    # The organization's payment terms were suggested on create; the user may change them.
    assert deal['payment_days'] == 45
    edited = sara.write('PATCH', url, {'title': 'HR, WPS payroll and leave', 'payment_days': 60, 'tags': ['Payroll']})
    assert edited[0] == 200, edited
    assert (edited[1]['title'], edited[1]['payment_days'], edited[1]['tags']) == ('HR, WPS payroll and leave', 60, ['Payroll'])
    assert sara.write('PATCH', url, {'stage': 'won'})[1]['fields'] == {'stage': 'This changes only through its moves'}
    assert sara.write('PATCH', url, {'number': 'X'})[1]['fields'] == {'number': 'This number is assigned by the system'}


def test_lines_add_edit_remove_reorder_and_totals(crm):
    sara = login(crm, 'sara')
    url = deal_url(sara, 'Distributor portal')
    deal = sara('GET', url)[1]
    lines = deal['children']['lines']
    assert len(lines) == 2 and deal['subtotal'] == '82000.00' and deal['total'] == '86100.00'
    products = {item['code']: item['id'] for item in sara('GET', '/api/product?limit=50&offset=0')[1]['items']}
    # Add a line from a product: description, rate and taxable come from the product. 15% off 2 x 2,400.
    added = sara.write('PATCH', url, {'children': {'lines': {'create': [{'_key': 'n1', 'product': products['TRAIN-DAY'], 'qty': '2', 'discount': '15'}],
                                                             'order': ['n1', *[line['id'] for line in lines]]}}})
    assert added[0] == 200, added
    body = added[1]
    new = body['children']['lines'][0]
    assert new['id'] == body['_row_ids']['n1'] and (new['description'], new['rate'], new['amount'], new['vat']) == ('Training day', '2400.00', '4080.00', '204.00')
    assert (body['subtotal'], body['vat_total'], body['total']) == ('86080.00', '4304.00', '90384.00')
    # Edit: a 33.33% discount on 1 x 3,200 is 2,133.44 after rounding half up to fils.
    edited = sara.write('PATCH', url, {'children': {'lines': {'update': [{'id': lines[0]['id'], 'qty': '1', 'discount': '33.33'}]}}})[1]
    assert next(line for line in edited['children']['lines'] if line['id'] == lines[0]['id'])['amount'] == '2133.44'
    # Reorder, then remove the added line; the totals follow.
    reordered = sara.write('PATCH', url, {'children': {'lines': {'order': [lines[1]['id'], lines[0]['id'], new['id']]}}})[1]
    assert [line['id'] for line in reordered['children']['lines']] == [lines[1]['id'], lines[0]['id'], new['id']]
    removed = sara.write('PATCH', url, {'children': {'lines': {'delete': [new['id']]}}})[1]
    assert removed['line_count'] == 2 and removed['subtotal'] == str(Decimal('18000.00') + Decimal('2133.44'))
    # Calculated values cannot be written.
    assert sara.write('PATCH', url, {'children': {'lines': {'update': [{'id': lines[0]['id'], 'amount': '1'}]}}})[0] == 422


def test_a_deal_with_150_lines_pages_and_recalculates(crm):
    sara = login(crm, 'sara')
    url = deal_url(sara, 'Group rollout')
    deal = sara('GET', url)[1]
    assert len(deal['children']['lines']) == 100 and deal['children_pages'] == {'lines': {'total': 150, 'limit': 100, 'partial': True}}
    page = sara('GET', url + '/lines/lines?limit=100&offset=100')[1]
    assert page['total'] == 150 and len(page['items']) == 50 and page['items'][-1]['description'] == 'Site 0150'
    last = page['items'][-1]
    before = Decimal(deal['subtotal'])
    after = sara.write('PATCH', url, {'children': {'lines': {'update': [{'id': last['id'], 'qty': str(Decimal(last['qty']) + 1)}]}}})[1]
    assert Decimal(after['subtotal']) - before == Decimal(last['rate']) * (1 - Decimal(last['discount']) / 100)
    # A full list is refused for a collection over 100 rows: it would drop the rows it does not list.
    assert sara.write('PATCH', url, {'children': {'lines': deal['children']['lines']}})[0] == 422


def test_rules_lost_reason(crm):
    sara = login(crm, 'sara')
    url = deal_url(sara, 'Subcontractor management')
    assert sara('GET', url)[1]['lost_reason'] is None
    # Before the deal is lost the reason does not apply, so a value sent now is cleared.
    assert sara.write('PATCH', url, {'lost_reason': 'Too early'})[1]['lost_reason'] is None
    assert {move['to']: move.get('asks') for move in sara('GET', url)[1]['_moves']}['lost'] == ['lost_reason']
    assert sara.write('POST', url + '/moves/lost', {})[1]['fields'] == {'lost_reason': 'This field is required'}
    lost = sara.write('POST', url + '/moves/lost', {'lost_reason': 'Budget moved to next year'})
    assert lost[0] == 200 and lost[1]['stage'] == 'lost' and lost[1]['_locked'] is True
    assert lost[1]['lost_reason'] == 'Budget moved to next year'
    # Locked, but the reason stays editable.
    assert sara.write('PATCH', url, {'title': 'x'})[1]['fields'] == {'title': 'This record is lost, so it is locked'}
    assert sara.write('PATCH', url, {'lost_reason': 'Chose another vendor'})[1]['lost_reason'] == 'Chose another vendor'
    # Only an administrator may reopen it.
    assert [(move['to'], move['allowed'], move['reason']) for move in sara('GET', url)[1]['_moves']] == [('lead', False, 'Your role cannot use this move')]
    reopened = login(crm, 'admin').write('POST', url + '/moves/lead', {})[1]
    assert reopened['stage'] == 'lead' and reopened['lost_reason'] is None


def test_lead_mark_lost_carries_the_reason(crm):
    """Lead declares lost_reason with applies_when and required_when on lost. Mark lost asks for it."""
    sara = login(crm, 'sara')
    url = '/api/lead/' + str(find(sara, 'lead', name='Majid Obaid')['id'])
    refused = sara.write('POST', url + '/moves/lost', {})
    assert refused[0] == 422 and refused[1]['fields'] == {'lost_reason': 'This field is required'}
    assert sara('GET', url)[1]['status'] == 'new'
    lost = sara.write('POST', url + '/moves/lost', {'lost_reason': 'No budget this year'})
    assert lost[0] == 200 and (lost[1]['status'], lost[1]['lost_reason']) == ('lost', 'No budget this year')


def test_moves_guards_and_blocked_reasons(crm):
    omar = login(crm, 'omar')
    url = deal_url(omar, 'Shipment tracking integration')
    deal = omar('GET', url)[1]
    assert deal['line_count'] == 0 and [(move['to'], move['allowed']) for move in deal['_moves']] == [('qualified', True), ('lost', True)]
    qualified = omar.write('POST', url + '/moves/qualified', {})[1]
    blocked = {move['to']: move for move in qualified['_moves']}['proposal']
    assert blocked == {'to': 'proposal', 'label': 'Send proposal', 'allowed': False, 'reason': 'The condition for this move is not met'}
    refused = omar.write('POST', url + '/moves/proposal', {})
    assert refused[0] == 409 and refused[1]['error'] == 'Send proposal is not available: the condition for this move is not met'
    assert omar.write('POST', url + '/moves/won', {})[1]['error'] == 'This record cannot move from qualified to won'
    # A move without the record's ETag is refused.
    assert omar('POST', url + '/moves/proposal', {})[0] == 428


def test_winning_a_deal_creates_a_task_books_it_and_makes_a_customer(crm):
    rahul = login(crm, 'rahul')
    url = deal_url(rahul, 'Batch and expiry tracking')
    deal = rahul('GET', url)[1]
    organization = rahul('GET', f"/api/organization/{deal['organization']}")[1]
    tasks_before = rahul('GET', url + '/related/task.deal?limit=10&offset=0')[1]['total']
    won = rahul.write('POST', url + '/moves/won', {})
    assert won[0] == 200, won
    won = won[1]
    assert won['stage'] == 'won' and won['probability'] == '100.00' and won['won_on'] is not None
    assert won['payment_expected'] == (date.fromisoformat(won['won_on']) + timedelta(days=won['payment_days'])).isoformat()
    assert won['weighted'] == str(Decimal(won['total']).quantize(Decimal('1'))) + '.00'
    tasks = rahul('GET', url + '/related/task.deal?limit=10&offset=0')[1]
    assert tasks['total'] == tasks_before + 1
    kickoff = next(task for task in tasks['items'] if task['title'].startswith('Kick-off'))
    assert (kickoff['kind'], kickoff['assignee'], kickoff['contact']) == ('meeting', 'rahul', deal['contact'])
    customer = rahul('GET', f"/api/organization/{deal['organization']}")[1]
    assert organization['status'] == 'prospect' and customer['status'] == 'customer' and customer['customer_since'] == won['won_on']
    admin = login(crm, 'admin')
    bookings = admin('GET', '/api/booking?limit=100&offset=0&filters=' + quote(json.dumps({'organization': deal['organization']})))[1]['items']
    assert [(row['amount'], row['owner'], row['reversal']) for row in bookings] == [(won['total'], 'rahul', False)]
    # Voiding the won deal writes the reversing booking; revising starts a new copy at lead.
    revision = admin.write('POST', url + '/revise', {'reason': 'Customer added a second warehouse'})
    assert revision[0] == 201, revision
    assert revision[1]['stage'] == 'lead' and revision[1]['revises'] == url.rsplit('/', 1)[1] and revision[1]['number'] != won['number']
    bookings = admin('GET', '/api/booking?limit=100&offset=0&filters=' + quote(json.dumps({'organization': deal['organization']})))[1]['items']
    assert sorted(row['amount'] for row in bookings) == sorted([won['total'], '-' + won['total']])
    assert admin('GET', url)[1]['stage'] == 'void'
    # Sales people cannot void or revise.
    assert rahul.write('POST', '/api/deal/' + str(revision[1]['id']) + '/void', {'reason': 'x'})[0] in (403, 404)


def test_admin_unposts_a_won_deal_and_reposts_it(crm):
    admin, rahul = login(crm, 'admin'), login(crm, 'rahul')
    deals = admin('GET', '/api/deal?limit=100&offset=0&filters=' + quote(json.dumps({'stage': 'negotiation'})))[1]['items']
    deal = next(item for item in deals if item['contact'] and Decimal(item['total']) > 0)
    url = f"/api/deal/{deal['id']}"
    won = admin.write('POST', url + '/moves/won', {})[1]
    assert won['stage'] == 'won' and won['_unpost'] == {'allowed': True}
    # Sales people cannot unpost; the admin gives a reason and the deal goes back to negotiation with its number.
    assert rahul('POST', url + '/unpost', {'reason': 'x'})[0] in (403, 404)
    status, body, _ = admin.write('POST', url + '/unpost', {'reason': 'Signed with the wrong entity'})
    assert status == 200 and (body['stage'], body['number']) == ('negotiation', won['number']), body
    entry = admin('GET', url + '/journal?limit=5')[1]['items'][1]
    assert entry['kind'] == 'unposted' and entry['data'] == {'from': 'won', 'to': 'negotiation', 'reason': 'Signed with the wrong entity'}
    status, body, _ = admin.write('POST', url + '/repost', {})
    assert status == 200 and (body['stage'], body['number']) == ('won', won['number']), body
    filters = quote(json.dumps({'memo': f"{won['number']} {won['title']}"}))
    bookings = admin('GET', f'/api/booking?limit=100&offset=0&filters={filters}')[1]['items']
    assert [(row['amount'], row['reversal']) for row in bookings] == [(won['total'], False), ('-' + won['total'], True), (body['total'], False)]


def test_actions_schedule_follow_up_and_convert_a_lead(crm):
    sara = login(crm, 'sara')
    url = deal_url(sara, 'Project costing and BOQ')
    due = (date.today() + timedelta(days=3)).isoformat()
    assert sara.write('POST', url + '/actions/schedule_follow_up', {'due_on': due})[1]['fields'] == {'note': 'This field is required'}
    done = sara.write('POST', url + '/actions/schedule_follow_up', {'due_on': due, 'note': 'Send the BOQ import demo'})
    assert done[0] == 200, done
    assert (done[1]['next_step'], done[1]['follow_up_on']) == ('Send the BOQ import demo', due)
    assert 'Send the BOQ import demo' in [task['title'] for task in sara('GET', url + '/related/task.deal?limit=10&offset=0')[1]['items']]
    assert sara('GET', url + '/measures')[1]['measures']['open_tasks'] >= 2
    won = sara('GET', deal_url(sara, 'AMC renewal 2027'))[1]
    assert {action['name']: action['allowed'] for action in won['_actions']} == {'schedule_follow_up': False}
    # Converting a qualified lead creates its organization, contact and deal, and links the deal.
    lead = find(sara, 'lead', name='Anjali Varma')
    url = f"/api/lead/{lead['id']}"
    converted = sara.write('POST', url + '/actions/convert', {'title': 'Pharmacy chain ERP', 'expected_close': '2026-12-15'})
    assert converted[0] == 200, converted
    deal = sara('GET', f"/api/deal/{converted[1]['deal']}")[1]
    assert (deal['title'], deal['stage'], deal['owner'], deal['lead_source'], deal['expected_close']) == ('Pharmacy chain ERP', 'qualified', 'sara', 'LinkedIn', '2026-12-15')
    organization = sara('GET', f"/api/organization/{deal['organization']}")[1]
    contact = sara('GET', f"/api/contact/{deal['contact']}")[1]
    assert (organization['name'], organization['tags'], contact['name'], contact['emirate']) == ('Bluewave Pharmacy Group', ['From lead'], 'Anjali Varma', 'Dubai')
    assert sara.write('POST', url + '/actions/convert', {'title': 'Again'})[0] == 409


def test_sales_see_only_their_deals_and_no_hidden_fields(crm):
    sara, omar, vera = login(crm, 'sara'), login(crm, 'omar'), login(crm, 'vera')
    mine = sara('GET', '/api/deal?limit=100&offset=0')[1]['items']
    assert mine and {item['owner'] for item in mine} == {'sara'}
    assert all('internal_notes' not in item for item in mine)
    theirs = deal_url(omar, 'Fleet and warehouse ERP')
    assert sara('GET', theirs)[0] == 404
    assert sara('GET', '/api/deal?limit=50&offset=0&q=Fleet')[1]['total'] == 0
    assert sara.write('PATCH', deal_url(sara, 'ERP rollout'), {'internal_notes': 'x'})[0] in (403, 422)
    # A deal created for someone else leaves the row scope and is refused.
    assert sara('POST', '/api/deal', {'title': 'Not mine', 'organization': mine[0]['organization'], 'owner': 'omar'})[0] in (403, 404, 422)
    # Viewers read every deal without internal notes and cannot write.
    seen = vera('GET', '/api/deal?limit=100&offset=0')[1]
    assert seen['total'] >= 17 and all('internal_notes' not in item and 'owner' in item for item in seen['items'])
    detail = vera('GET', deal_url(vera, 'ERP rollout'))[1]
    assert 'internal_notes' not in detail and detail['total'] is not None
    assert vera.write('PATCH', deal_url(vera, 'ERP rollout'), {'title': 'x'})[0] == 403
    admin = login(crm, 'admin')
    assert admin('GET', deal_url(admin, 'ERP rollout'))[1]['internal_notes'].startswith('Partner sign-off')
    # Measures follow the row scope.
    assert sara('GET', '/api/deal/measures')[1]['measures']['deals'] == len(mine)
    # A new deal or lead belongs to whoever creates it, without typing a username.
    created = sara('POST', '/api/deal', {'title': 'Mine without typing', 'organization': mine[0]['organization']})
    assert created[0] == 201 and created[1]['owner'] == 'sara'
    assert sara('POST', '/api/lead', {'name': 'Hamad', 'company': 'Hamad Trading'})[1]['owner'] == 'sara'


def test_two_sessions_editing_the_same_deal_conflict(crm):
    first, second = login(crm, 'sara'), login(crm, 'sara')
    url = deal_url(first, 'Production planning module')
    etag = first('GET', url)[2][b'etag']
    assert second('GET', url)[2][b'etag'] == etag
    assert first('PATCH', url, {'next_step': 'Book the demo'}, headers={b'if-match': etag})[0] == 200
    stale = second('PATCH', url, {'title': 'Changed in the second tab'}, headers={b'if-match': etag})
    assert stale[0] == 409
    fresh = second('GET', url)
    assert fresh[1]['next_step'] == 'Book the demo'
    assert second('PATCH', url, {'title': 'Production planning and costing'}, headers={b'if-match': fresh[2][b'etag']})[1]['next_step'] == 'Book the demo'


def test_activity_journal_records_moves_the_win_and_what_people_add(crm):
    sara = login(crm, 'sara')
    sara_id = sara('GET', '/api/auth/session')[1]['user']['id']
    url = deal_url(sara, 'Production planning')
    title = sara('GET', url)[1]['title']
    assert sara.write('POST', url + '/moves/negotiation', {})[0] == 200
    assert sara.write('POST', url + '/moves/won', {})[0] == 200
    page = sara('GET', url + '/journal?limit=50&offset=0')[1]
    entries = page['items']
    assert page['total'] == len(entries) and page['users'][str(sara_id)] == 'sara'
    # Newest first: the Booking posting follows the win in the same operation.
    effects, won, negotiated = entries[:3]
    assert (effects['kind'], effects['data']['targets']) == ('effects', [{'target': 'booking', 'rows': 1, 'reversal': False}])
    assert (won['kind'], won['data']['from'], won['data']['to'], won['data']['move']) == ('moved', 'negotiation', 'won', 'Mark won')
    assert (negotiated['kind'], negotiated['data']['move']) == ('moved', 'Negotiate')
    # on_won dated the deal and set the kick-off as the next step; the kick-off task itself is a Task (no journal).
    assert won['data']['fields']['next_step']['new'] == 'Kick-off meeting'
    assert won['data']['fields']['won_on']['old'] is None and won['data']['fields']['won_on']['new'] == sara('GET', url)[1]['won_on']
    assert 'stage' not in won['data']['fields'] and {entry['actor'] for entry in entries[:3]} == {sara_id}
    assert f'Kick-off: {title}' in [task['title'] for task in sara('GET', url + '/related/task.deal?limit=10&offset=0')[1]['items']]
    # The earlier edits, the moves made when seeding, the number and the create, oldest last.
    kinds = [entry['kind'] for entry in entries]
    assert kinds[-2:] == ['number', 'created'] and kinds.count('moved') == 4 and 'changed' in kinds
    assert any(entry['data'].get('fields', {}).get('next_step', {}).get('new') == 'Book the demo' for entry in entries if entry['kind'] == 'changed')
    # People add a note and a follow up, then complete it; entries can be added to the locked deal.
    note = sara('POST', url + '/journal', {'kind': 'note', 'text': 'Kick-off agreed for next week'})
    assert note[0] == 201 and note[1]['data'] == {'text': 'Kick-off agreed for next week'}
    due = (date.today() + timedelta(days=7)).isoformat()
    follow_up = sara('POST', url + '/journal', {'kind': 'follow_up', 'text': 'Send the project plan', 'due': due, 'assignee': sara_id})
    assert follow_up[0] == 201 and follow_up[1]['done'] is None
    done = sara('POST', url + '/journal', {'kind': 'follow_up_done', 'follow_up': follow_up[1]['id'], 'text': 'Plan sent'})
    assert done[0] == 201
    assert sara('POST', url + '/journal', {'kind': 'follow_up_done', 'follow_up': follow_up[1]['id']})[0] == 409
    assert sara('POST', url + '/journal', {'kind': 'follow_up_done', 'follow_up': won['id']})[0] == 422
    assert sara('POST', url + '/journal', {'kind': 'note', 'text': ' '})[0] == 422
    latest = sara('GET', url + '/journal?limit=3&offset=0')[1]
    assert [entry['kind'] for entry in latest['items']] == ['follow_up_done', 'follow_up', 'note']
    assert latest['items'][1]['done'] == done[1]['id'] and latest['total'] == page['total'] + 3
    # A viewer reads every entry but not the fields it cannot read, and cannot add to the journal.
    vera = login(crm, 'vera')
    seen = vera('GET', url + '/journal?limit=50&offset=0')[1]
    assert seen['total'] == latest['total']
    viewer_won = next(entry for entry in seen['items'] if entry['id'] == won['id'])
    assert viewer_won['partial'] is True and 'next_step' not in viewer_won['data']['fields'] and viewer_won['data']['to'] == 'won'
    assert vera('POST', url + '/journal', {'kind': 'note', 'text': 'Seen'})[0] == 403
    # Another sales person cannot read the deal or its history.
    assert login(crm, 'omar')('GET', url + '/journal?limit=50&offset=0')[0] == 404


def test_an_email_from_a_contact_appears_in_its_activity(crm):
    """Meta.mail on Contact and Lead: a salesperson's own mailbox puts a matching message in the record's Activity."""
    from test_mailboxes import _IMAP, _SMTP, APP_PASSWORD, mailbox, message, serve
    imap, smtp = serve(_IMAP), serve(_SMTP)
    try:
        for server in (imap, smtp):
            server.logins, server.tokens = {('sara@acme.test', APP_PASSWORD)}, set()
        imap.mail, imap.log, imap.bodies, imap.validity, smtp.messages = [], [], [], 7, []
        admin, sara = login(crm, 'admin'), login(crm, 'sara')
        assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'mail.personal', 'value': True}]})[0] == 200
        contact = find(sara, 'contact', name='Khalid Al Mansoori')
        imap.mail += [message(1, contact['email'], 'Payment terms for Q3', to='sara@acme.test', body='Can we move to 45 days?'),
                      message(2, 'someone@unknown.test', 'Not a customer', to='sara@acme.test')]
        status, payload, _ = sara('POST', '/api/_settings/me/mailbox', mailbox(imap, smtp, address='sara@acme.test'))
        assert status == 200, payload
        while crm.business.jobs.run_one():
            pass
        page = sara('GET', f"/api/contact/{contact['id']}/journal")[1]
        found = [item for item in page['items'] if item['kind'] == 'message']
        assert len(found) == 1 and found[0]['data']['subject'] == 'Payment terms for Q3' and found[0]['data']['direction'] == 'in'
        assert page['users'][str(found[0]['actor'])] == 'sara' and found[0]['data']['party'] == contact['email']
        assert 'Not a customer' not in json.dumps(page)
        # Viewers read the record, so they see the message too.
        assert login(crm, 'vera')('GET', f"/api/contact/{contact['id']}/journal?kind=message")[1]['total'] == 1
        sara('DELETE', '/api/_settings/me/mailbox')
    finally:
        for server in (imap, smtp):
            server.shutdown()
            server.server_close()
