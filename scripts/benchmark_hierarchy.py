"""Measure team row scopes (Hierarchy.team) over deep and wide reporting trees through the HTTP routes.

Builds 10,000 employees with 10 leave rows each: a manager over a 500-level chain, a manager over
5,000 reports in a ten-way tree, and 4,498 others. Reports wall time and statement plans for a
list page, a count and an aggregate. Run from the checkout:

    .venv/bin/python scripts/benchmark_hierarchy.py
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
import argparse
import json
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from evlet import Application, Database, Hierarchy, Integer, Link, LocalAuth, Model, Registry, Rule, Text
from test_business import PASSWORD, client, http

TEAM = Hierarchy('employee', parent='reports_to')
ALL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Employee(Model):
    permissions = {**ALL, 'viewer': Rule(('read',), scope=TEAM.team())}
    name = Text(default='')
    reports_to = Link('employee', index=True)


class Leave(Model):
    permissions = {**ALL, 'viewer': Rule(('read',), scope=TEAM.team('employee'))}
    employee = Link(Employee, index=True)
    days = Integer(default=1)


DEEP, WIDE, TOTAL, LEAVES = 500, 5000, 10_000, 10


def organisation():
    """(id, reports_to) rows: 1 is the chief, 2 heads the deep chain, 3 the wide tree; the rest report to 1."""
    rows = [(1, None), (2, 1), (3, 1)]
    rows += [(4 + index, 2 if index == 0 else 3 + index) for index in range(DEEP)]
    start = 4 + DEEP
    rows += [(start + index, 3 if index < 10 else start + index // 10 - 1) for index in range(WIDE)]
    rows += [(identity, 1) for identity in range(len(rows) + 1, TOTAL + 1)]
    return rows


def load(db):
    employees = organisation()
    with db.transaction() as tx:
        tx.connection.executemany('INSERT INTO employee (id, name, reports_to) VALUES (?, ?, ?)',
                                  [(identity, f'Employee {identity}', parent) for identity, parent in employees])
        tx.connection.executemany('INSERT INTO leave (employee, days) VALUES (?, ?)',
                                  [(identity, 1 + day % 3) for identity, _ in employees for day in range(LEAVES)])
    return employees


def measure(label, call, repeats):
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        status, body = call()
        timings.append(time.perf_counter() - start)
        if status != 200:
            raise RuntimeError(f'{label}: HTTP {status} {body}')
    timings.sort()
    return {'case': label, 'p50_ms': float(f'{statistics.median(timings) * 1000:.2f}'),
            'p95_ms': float(f'{timings[max(0, int(len(timings) * 0.95) - 1)] * 1000:.2f}'), 'runs': repeats, 'result': body}


def run(db, repeats):
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    load(db)
    admin, results = client(app), []
    for name, employee, team in (('deep', 2, DEEP + 1), ('wide', 3, WIDE + 1), ('leaf', 4 + DEEP - 1, 1)):
        user = admin('POST', '/api/auth/users', {'username': name, 'password': PASSWORD, 'role': 'viewer'})[1]
        app.business.auth.link_user(user['id'], Employee, employee)
        c = client(app, name)
        page = lambda: (lambda r: (r[0], r[1].get('total')))(c('GET', '/api/leave?limit=50'))
        total = lambda: (lambda r: (r[0], r[1].get('items')))(c('GET', '/api/leave/aggregate?measures=' + quote('n:count,d:sum:days')))
        people = lambda: (lambda r: (r[0], r[1].get('total')))(c('GET', '/api/employee?limit=50'))
        for label, call, expected in ((f'{name}: leave list page and count', page, team * LEAVES),
                                      (f'{name}: leave aggregate', total, None), (f'{name}: employee list page', people, team)):
            result = measure(label, call, repeats)
            if expected is not None and result['result'] != expected:
                raise RuntimeError(f'{label}: expected {expected}, got {result["result"]}')
            results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args()
    registry = Registry(Employee, Leave)
    with TemporaryDirectory() as directory:
        import sqlite3
        db = Database(Path(directory) / 'hierarchy.sqlite3', registry)
        version = sqlite3.sqlite_version
        results = run(db, args.repeats)
    print(json.dumps({'database': 'sqlite', 'version': version, 'python': platform.python_version(),
                      'machine': platform.machine(), 'system': platform.system(), 'employees': TOTAL, 'leave_rows': TOTAL * LEAVES,
                      'results': results}, indent=1))


if __name__ == '__main__':
    main()
