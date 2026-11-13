"""Record the real lifecycle API contract shared by backend and browser tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evlet.identity import sequential_ids
from evlet.audit import fixed_times

from examples.playground.lifecycle.app import create_app
from tests.test_business import client, http, PASSWORD

DESTINATION = Path(__file__).resolve().parents[1] / 'frontend/src/evlet/tests/lifecycle-fixture.json'


def normalize(value, key=None):
    if key == '_row_ids':
        return {'row-' + str(index): item for index, item in enumerate(value.values())}
    if key in ('csrf', 'at', 'timestamp', 'hash', 'prev_hash', 'execution_id', 'event_id', 'etag') and value is not None:
        return 1 if isinstance(value, int) else '"fixture-etag"' if key == 'etag' else 'fixture-' + key
    if isinstance(value, dict):
        return {key: normalize(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def record():
    with TemporaryDirectory() as folder, sequential_ids(), fixed_times():
        app = create_app(Path(folder) / 'test.sqlite3')
        app.initialize()
        http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
        c = client(app)
        result = {}
        def capture(name, method, path, body=None, etag=None):
            status, data, headers = c(method, path, body, headers={b'if-match': etag} if etag else {})
            assert status in (200, 201), data
            result[name] = {'status': status, 'body': data, 'etag': headers.get(b'etag', b'').decode() or None}
            return headers.get(b'etag')
        capture('session', 'GET', '/api/auth/session')
        capture('schema', 'GET', '/api/schema')
        tag = capture('draft', 'POST', '/api/order', {'number': 'ORD-001', 'customer': 'A', 'children': {'lines': [{'description': 'First', 'rate': '10'}, {'description': 'Second', 'rate': '20'}]}})
        o = '/api/order/0000000000001'
        tag = capture('confirmed', 'POST', o + '/moves/draft_to_confirmed', {}, tag)
        tag = capture('reopened', 'POST', o + '/moves/confirmed_to_draft', {'remark': 'Customer and lines changed'}, tag)
        lines = result['reopened']['body']['children']['lines']
        tag = capture('edited', 'PATCH', o, {'customer': 'B', 'children': {'lines': {'update': [{'id': lines[0]['id'], 'quantity': 2}], 'create': [{'description': 'Third', 'rate': '30'}]}}}, tag)
        tag = capture('reconfirmed', 'POST', o + '/moves/draft_to_confirmed', {}, tag)
        capture('cancelled', 'POST', o + '/moves/draft_confirmed_to_cancelled', {'remark': 'Customer withdrew the order'}, tag)
        capture('history', 'GET', o + '/history')
        return result


if __name__ == '__main__':
    DESTINATION.write_text(json.dumps(normalize(record()), indent=2) + '\n')
