"""Does a busy background job affect people using the app? (docs/performance.md, Busy writing on SQLite)

    PYTHONPATH=. .venv/bin/python scripts/busy_queue_benchmark.py --step 0.1

Eight simulated people each open a list of customers and save a customer, then pause 0.2 s, for 20 s.

* Run A: the people alone.
* Run B: the same while one background job writes customers in back-to-back transactions of --step seconds,
  like a data generator. Its writes are marked as a job's (evlet.writes.job_writes), as a JobQueue marks them.
* Run C: run B, while sign-in attempts, the tool-call log and Eva write their own files
  (sessions.sqlite3, logs.sqlite3, eva.sqlite3) on three more threads. They must not wait on the job.

Prints each run's tries, failures and waits, whether a slow-write warning named the job, and a last JSON line.
A measurement on a temporary Playground database, not a pass/fail check; tests/test_sqlite_files.py has the check.
"""
import argparse
import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

options = argparse.ArgumentParser(description=__doc__.splitlines()[0])
options.add_argument('--step', type=float, default=1.0, help='Seconds each job transaction holds the database')
options.add_argument('--seconds', type=float, default=20, help='How long each run lasts')
options.add_argument('--people', type=int, default=8)
arguments = options.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['EVLET_DATABASE'] = os.path.join(tempfile.mkdtemp(), 'database.sqlite3')
from examples.playground.app import create_app  # noqa: E402
from examples.playground.models import Customer  # noqa: E402
from evlet.writes import job_writes  # noqa: E402

app = create_app()
app.database.initialize()
app.business.initialize()
db = app.database
with db.transaction() as tx:
    tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_eva_lock (id INTEGER PRIMARY KEY CHECK(id=1), n INTEGER NOT NULL)')
    tx.connection.execute('INSERT OR IGNORE INTO evlet_eva_lock(id,n) VALUES (1,0)')


class Warnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


warnings = Warnings()
logging.getLogger('evlet.writes').addHandler(warnings)


def timed(results, kind, action):
    started = time.perf_counter()
    try:
        action()
        results.append((kind, time.perf_counter() - started, None))
    except Exception as exc:  # noqa: BLE001  (every failure is counted and shown)
        results.append((kind, time.perf_counter() - started, f'{type(exc).__name__}: {str(exc)[:60]}'))


def person(index, stop, results):
    count = 0
    while not stop.is_set():
        timed(results, 'read', lambda: db.list(Customer, limit=50))

        def save():
            with db.transaction() as tx:
                tx.create(Customer, customer_name=f'Person {index}-{count}')
        timed(results, 'save', save)
        count += 1
        time.sleep(0.2)


def side(stop, results):
    count = 0
    while not stop.is_set():
        def sign_in():
            with db.transaction(store='sessions') as tx:
                tx.connection.execute('INSERT INTO evlet_login_limits VALUES (?,1,?) ON CONFLICT(client) DO UPDATE SET attempts=attempts+1',
                                      (f'client {count % 50}', int(time.time()) + 900))

        def tool_log():
            with db.transaction(store='logs') as tx:
                tx.connection.execute("INSERT INTO evlet_tool_calls(at,user_id,origin,tool,kind,outcome) VALUES (?,1,'api','list_records','read','ok')",
                                      (int(time.time()),))

        def eva():
            with db.transaction(store='eva') as tx:
                tx.connection.execute('UPDATE evlet_eva_lock SET n=n+1 WHERE id=1')
        for kind, action in (('sign-in', sign_in), ('tool log', tool_log), ('eva', eva)):
            timed(results, kind, action)
        count += 1
        time.sleep(0.05)


def busy_job(stop, rows):
    written = 0
    with job_writes('job #1 data generator'):
        while not stop.is_set():
            try:
                with db.transaction() as tx:
                    until = time.perf_counter() + arguments.step
                    while time.perf_counter() < until:
                        tx.create(Customer, customer_name=f'Generated {written}')
                        written += 1
            except Exception:  # noqa: BLE001
                pass
    rows.append(written)


def run(name, job, sides):
    stop, results, rows = threading.Event(), [], []
    threads = [threading.Thread(target=person, args=(index, stop, results)) for index in range(arguments.people)]
    if job:
        threads.append(threading.Thread(target=busy_job, args=(stop, rows)))
    if sides:
        threads.append(threading.Thread(target=side, args=(stop, results)))
    before = len(warnings.messages)
    for thread in threads:
        thread.start()
    time.sleep(arguments.seconds)
    stop.set()
    for thread in threads:
        thread.join()
    summary = {'run': name, 'job_rows': rows[0] if rows else None,
               'slow_write_warnings': [message for message in warnings.messages[before:] if 'data generator' in message][:1]}
    print(f'\n{name} ({arguments.people} people, {arguments.seconds:g} s' + (f', job steps of {arguments.step:g} s, job wrote {rows[0]} rows)' if rows else ')'))
    for kind in ('read', 'save', 'sign-in', 'tool log', 'eva'):
        found = [item for item in results if item[0] == kind]
        if not found:
            continue
        ok = sorted(item[1] for item in found if item[2] is None)
        errors = [item[2] for item in found if item[2] is not None]

        def point(q):
            return round(ok[min(len(ok) - 1, int(q * len(ok)))] * 1000, 1) if ok else 0
        summary[kind] = {'tries': len(found), 'failed': len(errors), 'median_ms': point(0.5), 'p95_ms': point(0.95),
                         'worst_ms': round(max(ok) * 1000, 1) if ok else 0}
        print(f'  {kind:8}: {len(found):5} tries, {len(errors):4} failed, wait median {point(0.5):7.1f} ms, '
              f'p95 {point(0.95):7.1f} ms, worst {summary[kind]["worst_ms"]:7.1f} ms')
        if errors:
            print('            first error:', errors[0])
    if summary['slow_write_warnings']:
        print('  warning:', summary['slow_write_warnings'][0])
    return summary


results = [run('A: people only', False, False), run('B: people + busy job', True, False),
           run('C: B + sign-in, tool log and Eva writes', True, True)]
db.close()
print(json.dumps({'step_s': arguments.step, 'runs': results}))
