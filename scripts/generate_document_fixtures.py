"""Generate the declarative document contract from authenticated ASGI responses."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_documents import Order
from test_business import http, client, PASSWORD
from evlet import Registry, Database, LocalAuth, Application
from evlet.identity import sequential_ids
from evlet.audit import fixed_times


def collect(app):
    c = client(app)
    models = c('GET', '/api/schema')[1]['models']
    values = {'title': 'Contract', 'children': {'items': [{'_key': 'first', 'qty': '3', 'rate': '0.10'}, {'_key': 'second', 'qty': '2', 'rate': '1.25'}]}}
    status, result, _ = c('POST', '/api/_draft/order', {'values': values, 'revision': 4})
    assert status == 200
    result['values'].pop('_key')
    status, created, _ = c('POST', '/api/order', values)
    assert status == 201
    _, saved, headers = c('GET', f"/api/order/{created['id']}")
    return {'models': models, 'input': values, 'preview': result,
            'saved': saved, 'etag': headers[b'etag'].decode()}


def main():
    # Ids count up per table, so the recording only changes when the responses do.
    with TemporaryDirectory() as directory, sequential_ids(), fixed_times():
        db = Database(Path(directory) / 'fixture.sqlite3', Registry(Order))
        app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
        app.initialize()
        http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
        path = Path(__file__).resolve().parents[1] / 'tests/fixtures/document-contract.json'
        path.write_text(json.dumps(collect(app), indent=2) + '\n')


if __name__ == '__main__': main()
