"""Record testCRM responses for the browser end-to-end tests (frontend/src/evlet/tests/testcrm-*.test.ts).

testCRM is installed and seeded in a disposable project (scripts/testcrm.py), then each scenario is
played against the real ASGI app as a signed-in user. A scenario is a list of phases: the reads the
screens make, then one write, then the reads after it. The browser tests replay a scenario through a
fetch stub that answers each read from the latest phase reached and moves on at each write, so the
screens see exactly what the engine sent at every step.

Run from the repository root: .venv/bin/python -m scripts.generate_testcrm_fixtures
"""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qsl, quote, urlsplit

from evlet.instances import activate_generation
from scripts import testcrm

PASSWORD = 'a long local test password'
SCHEMAS = {}
OUTPUT = Path(__file__).resolve().parent.parent / 'frontend/src/evlet/tests/testcrm-fixture.json'


def key(method, url):
    """The same normalization as the replay stub: decoded query parameters in order."""
    parts = urlsplit(url)
    query = '&'.join(f'{name}={value}' for name, value in sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return f'{method} {parts.path}' + (f'?{query}' if query else '')


def http(app, method, path, data=None, cookie='', csrf='', headers=None):
    async def run():
        route, _, query = path.partition('?')
        events = [{'type': 'http.request', 'body': json.dumps({} if data is None else data).encode()}]
        sent = []

        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}

        async def send(event):
            sent.append(event)
        request_headers = {b'host': b'127.0.0.1:8000', b'content-type': b'application/json', b'x-evlet-request': b'1',
                           b'cookie': cookie.encode(), b'x-csrf-token': csrf.encode(), **(headers or {})}
        await app({'type': 'http', 'method': method, 'path': route, 'query_string': query.encode(),
                   'headers': list(request_headers.items()), 'client': ('127.0.0.1', 1234)}, receive, send)
        response_headers, content = dict(sent[0]['headers']), sent[1]['body']
        payload = json.loads(content) if content and response_headers.get(b'content-type', b'').startswith(b'application/json') else None
        return sent[0]['status'], payload, response_headers
    return asyncio.run(run())


class Scenario:
    """Records one user's requests in phases."""

    def __init__(self, app, username, *, signed_in=True):
        self.app, self.phases, self.username, self.cookie, self.csrf = app, [{}], username, '', ''
        if not signed_in:
            # The login screen: the anonymous session, a refused password, then the real sign-in.
            self.get('/api/auth/session')
            self.phases[-1][key('POST', '/api/auth/login')] = self.call('POST', '/api/auth/login', {'username': username, 'password': 'not the password'})
            self.phases.append({})
        status, data, headers = http(app, 'POST', '/api/auth/login', {'username': username, 'password': PASSWORD})
        assert status == 200, data
        self.cookie, self.csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
        if not signed_in:
            self.phases[-1][key('POST', '/api/auth/login')] = {'status': status, 'body': {**data, 'csrf': 'fixture-csrf-token'}, 'etag': None}
            self.phases.append({})
        self.schema = self.get('/api/auth/session', '/api/schema')

    def call(self, method, url, body=None, headers=None):
        status, payload, response_headers = http(self.app, method, url, body, cookie=self.cookie, csrf=self.csrf, headers=headers)
        if isinstance(payload, dict) and isinstance(payload.get('csrf'), str):
            payload['csrf'] = 'fixture-csrf-token'
        return {'status': status, 'body': payload, 'etag': response_headers.get(b'etag', b'').decode() or None}

    def get(self, *urls):
        for url in urls:
            self.phases[-1][key('GET', url)] = self.call('GET', url)
        return self.phases[-1][key('GET', urls[-1])]['body']

    def write(self, method, url, body=None, read=None):
        """A write the screen sends with the ETag of its last read; the reads after it start a new phase."""
        etag = self.call('GET', read or url)['etag']
        reply = self.call(method, url, body, headers={b'if-match': etag.encode()} if etag else None)
        self.phases[-1][key(method, url)] = reply
        self.phases.append({})
        return reply

    def record_page(self, model, record_id, *, links=True):
        """What the record view reads: the detail, its line pages, linked titles, measures, related counts and files."""
        detail = self.get(f'/api/{model}/{record_id}')
        pages = detail.get('children_pages', {})
        for name, page in pages.items():
            for offset in range(100, page['total'], 100):
                self.get(f'/api/{model}/{record_id}/lines/{name}?limit=100&offset={offset}')
        if links:
            self.links(model, [detail])
            for name in detail.get('children', {}):
                rows = detail['children'][name] + [row for offset in range(100, pages.get(name, {}).get('total', 0), 100)
                                                   for row in self.get(f'/api/{model}/{record_id}/lines/{name}?limit=100&offset={offset}')['items']]
                self.links(f'{model}_{name}', rows)
        schema = next(entry for entry in self.schema['models'] if entry['name'] == model)
        if model == 'deal':
            self.get(f'/api/{model}/{record_id}/measures')
        for relation in schema.get('relations', []):
            self.get(f"/api/{model}/{record_id}/related/{relation['name']}?limit=1",
                     f"/api/{model}/{record_id}/related/{relation['name']}?limit={relation['limit']}&offset=0")
        if schema.get('attachments'):
            self.get(f'/api/{model}/{record_id}/attachments')
        if schema.get('journal'):
            self.get(f'/api/{model}/{record_id}/journal?limit=50')
        return detail

    def links(self, model, records):
        """Titles of the records a list or record links to."""
        schema = {entry['name']: entry for entry in self.schema['models']}
        entry = schema.get(model)
        if not entry:
            return
        for field in entry['fields']:
            if field['type'] == 'link' and field.get('target') in schema:
                for record in records:
                    if record.get(field['name']):
                        self.get(f"/api/{field['target']}/{record[field['name']]}")

    def list_page(self, model, query=''):
        """What the list page reads: the page, the measures strip, counts per state and linked titles."""
        page = self.get(f'/api/{model}?limit=50&offset=0' + query)
        self.get(f'/api/{model}/measures' + (('?' + query.lstrip('&')) if 'filters=' in query else ''))
        self.links(model, page['items'])
        return page

    def dump(self):
        # One schema per user is kept under "schemas"; scenarios point to it.
        phases = [dict(phase) for phase in self.phases]
        for phase in phases:
            if key('GET', '/api/schema') in phase:
                SCHEMAS[self.username] = phase.pop(key('GET', '/api/schema'))
                phase[key('GET', '/api/schema')] = {'schema': self.username}
        return phases


def ids(scenario, model, **filters):
    found = scenario.call('GET', f'/api/{model}?limit=100&offset=0&filters=' + quote(json.dumps(filters)))['body']['items']
    assert len(found) == 1, (model, filters, found)
    return found[0]['id']


def main():
    folder = Path(tempfile.mkdtemp(prefix='evlet-testcrm-fixtures-'))
    app = testcrm.build(folder / 'project', frontend_port=8000)
    out = {}
    with activate_generation(app):
        app.initialize()
        testcrm.seed(app, PASSWORD, big_lines=150)
        try:
            record(app, out)
        finally:
            app.database.close()
    for name in list(sys.modules):
        if name.split('.')[0] == 'testcrm':
            del sys.modules[name]
    # Dates set from today (won_on, the kick-off task) and the year in numbers follow the day of recording.
    out['schemas'] = SCHEMAS
    OUTPUT.write_text(json.dumps(out, sort_keys=True, separators=(',', ':')) + '\n')


def record(app, out):
    sara = Scenario(app, 'sara', signed_in=False)
    sara.get('/api/organization?limit=50&offset=0')
    # The list: page, search, a saved view per state, sort, the board and the palette search.
    sara.list_page('deal')
    sara.get('/api/deal/aggregate?measures=n:count,pipeline&group_by=stage')
    sara.list_page('deal', '&q=planning')
    sara.list_page('deal', '&filters=' + quote(json.dumps({'stage': 'proposal'}, separators=(',', ':'))))
    sara.list_page('deal', '&order_by=total')
    sara.list_page('deal', '&order_by=-total')
    board = sara.get('/api/deal?limit=100&offset=0')
    sara.links('deal', board['items'])
    for model in ('deal', 'organization', 'contact', 'lead', 'task', 'product'):
        sara.get(f'/api/{model}?limit=5&offset=0&q=rollout')
    sara.get('/api/product?limit=8&offset=0', '/api/product?limit=8&offset=0&q=Training')
    # On the board, Distributor portal moves from qualified to proposal.
    portal = ids(sara, 'deal', title='Distributor portal')
    sara.get(f'/api/deal/{portal}')
    assert sara.write('POST', f'/api/deal/{portal}/moves/proposal', {}, read=f'/api/deal/{portal}')['status'] == 200
    sara.get(f'/api/deal/{portal}', '/api/deal?limit=100&offset=0', '/api/deal/measures', '/api/deal/aggregate?measures=n:count,pipeline&group_by=stage')
    sara.list_page('deal')
    out['sara_list'] = sara.dump()

    # A proposal: edit a line, add one from a product, reorder, save; then negotiate and win.
    sara = Scenario(app, 'sara')
    deal = ids(sara, 'deal', title='Production planning module')
    url = f'/api/deal/{deal}'
    detail = sara.record_page('deal', deal)
    sara.get('/api/product?limit=8&offset=0', '/api/product?limit=8&offset=0&q=Training')
    products = {item['code']: item['id'] for item in sara.call('GET', '/api/product?limit=50&offset=0')['body']['items']}
    sara.get(f"/api/product/{products['TRAIN-DAY']}")
    lines = detail['children']['lines']
    saved = sara.write('PATCH', url, {'children': {'lines': {
        'create': [{'_key': 'n1', 'product': products['TRAIN-DAY'], 'description': 'Training day', 'rate': '2400.00', 'taxable': True}],
        'update': [{'id': lines[1]['id'], 'qty': '15.00'}],
        'order': ['n1', lines[0]['id'], lines[1]['id']]}}})
    assert saved['status'] == 200, saved
    sara.record_page('deal', deal)
    assert sara.write('POST', f'{url}/moves/negotiation', {}, read=url)['status'] == 200
    sara.record_page('deal', deal)
    won = sara.write('POST', f'{url}/moves/won', {}, read=url)
    assert won['status'] == 200, won
    # In another session sara plans a follow up on the won deal and completes it.
    me = sara.call('GET', '/api/auth/session')['body']['user']['id']
    planned = sara.call('POST', f'{url}/journal', {'kind': 'follow_up', 'text': 'Send the project plan', 'due': '2026-11-20', 'assignee': me})
    assert planned['status'] == 201, planned
    assert sara.call('POST', f'{url}/journal', {'kind': 'follow_up_done', 'follow_up': planned['body']['id'], 'text': 'Plan sent'})['status'] == 201
    sara.record_page('deal', deal)
    sara.get(f'/api/deal/{deal}/related/task.deal?limit=10&offset=0')
    sara.get(f"/api/organization/{detail['organization']}")
    # A note from the Activity tab; the history is read again.
    sara.phases[-1][key('POST', f'{url}/journal')] = sara.call('POST', f'{url}/journal', {'kind': 'note', 'text': 'Kick-off agreed for next week'})
    sara.phases.append({})
    sara.get(f'{url}/journal?limit=50')
    out['sara_win'] = sara.dump()

    # A qualified deal with no lines: Send proposal is blocked; then it is lost and given a reason.
    omar = Scenario(app, 'omar')
    deal = ids(omar, 'deal', title='Shipment tracking integration')
    url = f'/api/deal/{deal}'
    assert omar.write('POST', f'{url}/moves/qualified', {}, read=url)['status'] == 200
    omar.record_page('deal', deal)
    refused = omar.write('POST', f'{url}/moves/proposal', {}, read=url)
    assert refused['status'] == 409, refused
    omar.record_page('deal', deal)
    # Mark lost asks for the reason; then the reason is edited in place on the locked deal.
    assert omar.write('POST', f'{url}/moves/lost', {'lost_reason': 'Budget moved'}, read=url)['status'] == 200
    omar.record_page('deal', deal)
    assert omar.write('PATCH', url, {'lost_reason': 'Budget moved to next year'})['status'] == 200
    omar.record_page('deal', deal)
    out['omar_lost'] = omar.dump()

    # A lead's Mark lost asks for the reason too: refused without it, done with it.
    sara = Scenario(app, 'sara')
    lead = ids(sara, 'lead', name='Majid Obaid')
    sara.record_page('lead', lead)
    assert sara.write('POST', f'/api/lead/{lead}/moves/lost', {}, read=f'/api/lead/{lead}')['status'] == 422
    assert sara.write('POST', f'/api/lead/{lead}/moves/lost', {'lost_reason': 'No budget this year'}, read=f'/api/lead/{lead}')['status'] == 200
    sara.record_page('lead', lead)
    out['sara_lead_lost'] = sara.dump()

    # A deal with 150 lines: 100 embedded, the rest paged in; one edit on the last line.
    sara = Scenario(app, 'sara')
    deal = ids(sara, 'deal', title='Group rollout, 150 sites')
    detail = sara.record_page('deal', deal)
    last = sara.get(f'/api/deal/{deal}/lines/lines?limit=100&offset=100')['items'][-1]
    assert sara.write('PATCH', f'/api/deal/{deal}', {'children': {'lines': {'update': [{'id': last['id'], 'qty': '10.00'}]}}})['status'] == 200
    sara.record_page('deal', deal)
    out['sara_big'] = sara.dump()

    # Two sessions: the other one saved first, so this save is a conflict; Reload reads it again.
    sara, other = Scenario(app, 'sara'), Scenario(app, 'sara')
    deal = ids(sara, 'deal', title='ERP rollout, 3 entities')
    url = f'/api/deal/{deal}'
    sara.record_page('deal', deal)
    stale = sara.call('GET', url)['etag']
    assert other.write('PATCH', url, {'next_step': 'Changed elsewhere'})['status'] == 200
    sara.phases[-1][key('PATCH', url)] = sara.call('PATCH', url, {'title': 'ERP rollout, 3 entities and Oman'}, headers={b'if-match': stale.encode()})
    assert sara.phases[-1][key('PATCH', url)]['status'] == 409
    sara.phases.append({})
    sara.record_page('deal', deal)
    assert sara.write('PATCH', url, {'title': 'ERP rollout, 3 entities and Oman'})['status'] == 200
    sara.record_page('deal', deal)
    out['sara_conflict'] = sara.dump()

    # Scope and hidden fields: a deal of another sales person, and the same deal for a viewer and the admin.
    sara.phases = [sara.phases[0]]
    theirs = ids(omar, 'deal', title='Fleet and warehouse ERP')
    sara.get(f'/api/deal/{theirs}')
    out['sara_scope'] = sara.dump()
    for username in ('vera', 'admin'):
        viewer = Scenario(app, username)
        viewer.record_page('deal', deal)
        viewer.list_page('deal')
        out[f'{username}_deal'] = viewer.dump()
    out['ids'] = {'deal': deal, 'theirs': theirs}


if __name__ == '__main__':
    main()
