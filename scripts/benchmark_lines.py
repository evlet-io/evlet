"""Measure owned-table documents through the authenticated HTTP routes on a temporary SQLite file.

Reports SQL statement counts and wall time for loading, paging and saving a 1,000-row document
and a 100 x 9 nested document. Run from the checkout: .venv/bin/python scripts/benchmark_lines.py
"""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import platform
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from evlet import Application, Database, LocalAuth, Registry, Model, Child, Table, Decimal, Currency, Integer, Text, Link, Rule
from evlet.calc import round
from finite_fixtures import FiniteMeta
from test_business import http, client, PASSWORD

ALL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Product(Model):
    permissions = ALL
    name = Text(default='product')


class Part(Child):
    hours = Decimal(default=0)


class Line(Child):
    product = Link(Product)
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=round(qty * rate, 2))
    parts = Table(Part, max_rows=9)


class Order(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='order')
    lines = Table(Line, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    line_count = Integer(compute=lines.count())


class Counting(Database):
    statements = 0

    @contextmanager
    def _connection(self):
        with super()._connection() as connection:
            connection.set_trace_callback(self._count)
            yield connection

    def _count(self, statement):
        type(self).statements += 1


def measure(label, call, repeats, prepare=lambda: None):
    """Run call(prepared) `repeats` times; prepare() (for example reading the current ETag) is not timed."""
    timings, statements = [], None
    for _ in range(repeats):
        prepared = prepare()
        Counting.statements = 0
        start = time.perf_counter()
        status = call(prepared)
        timings.append(time.perf_counter() - start)
        if status not in (200, 201):
            raise RuntimeError(f'{label}: HTTP {status}')
        statements = Counting.statements
    timings.sort()
    return {'case': label, 'statements': statements, 'p50_ms': round_ms(statistics.median(timings)),
            'p95_ms': round_ms(timings[max(0, int(len(timings) * 0.95) - 1)]), 'runs': repeats}


def round_ms(seconds):
    return float(f'{seconds * 1000:.2f}')


def row(product, parts, **values):
    # Request bodies are limited to 64 KiB, so rows stay short.
    return {'product': product, 'qty': '1', 'rate': '2.5', **values, **({'children': {'parts': [{'hours': '1'}] * parts}} if parts else {})}


def document(c, product, rows, parts):
    body = [row(product, parts, _key=f'l{index}') for index in range(rows)]
    status, order, _ = c('POST', '/api/order', {'children': {'lines': body}})
    assert status == 201, order
    return order


def cases(c, product, rows, parts, repeats):
    label = f'{rows} rows' + (f' x {parts} nested' if parts else '')
    body = {'children': {'lines': [row(product, parts) for _ in range(rows)]}}
    results = [measure(f'create, {label}', lambda _: c('POST', '/api/order', body)[0], max(3, repeats // 5))]
    order = document(c, product, rows, parts)
    url = f"/api/order/{order['id']}"
    results.append(measure(f'GET detail, {label}', lambda _: c('GET', url)[0], repeats))
    results.append(measure(f'GET one page of lines, {label}', lambda _: c('GET', url + '/lines/lines?limit=100&offset=0')[0], repeats))
    etag = lambda: {b'if-match': c('GET', url)[2][b'etag']}
    counter = iter(range(10**6))
    results.append(measure(f'PATCH title, {label}', lambda headers: c('PATCH', url, {'title': f't{next(counter)}'}, headers=headers)[0], repeats, etag))
    last = order['_row_ids'][f'l{rows - 1}']
    change = lambda headers: c('PATCH', url, {'children': {'lines': {'update': [{'id': last, 'qty': str(next(counter) % 9 + 1)}]}}}, headers=headers)[0]
    results.append(measure(f'PATCH one row (row changes), {label}', change, repeats, etag))
    return results


def main(repeats=20):
    with TemporaryDirectory() as directory:
        db = Counting(Path(directory) / 'lines.sqlite3', Registry(Order, Product))
        app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
        app.initialize()
        http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
        c = client(app)
        product = c('POST', '/api/product', {})[1]['id']
        results = cases(c, product, 1000, 0, repeats) + cases(c, product, 100, 9, repeats)
    print(json.dumps({'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version, 'machine': platform.machine(),
                      'system': platform.system(), 'results': results}, indent=1))


if __name__ == '__main__': main()
