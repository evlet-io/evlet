"""Busy writing on SQLite: a resumable job holds the database --step seconds per step while six people save records.

    .venv/bin/python scripts/benchmark_busy_writes.py --step 1.0

Prints one JSON line: saves, failed saves (with their errors), the median, 95th percentile and longest wait for a
save, and how long the job took. Pass --source to run it against another checkout, such as an older release.
Each run uses a new temporary database. See docs/performance.md, Busy writing.
"""
import argparse
import json
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

options = argparse.ArgumentParser(description=__doc__.splitlines()[0])
options.add_argument('--step', type=float, default=1.0, help='Seconds each job step holds the database')
options.add_argument('--source', default=str(Path(__file__).resolve().parents[1]), help='The Evlet checkout to measure')
arguments = options.parse_args()
sys.path.insert(0, arguments.source)
from evlet import Database, JobQueue, Task  # noqa: E402
from examples.playground.models import Customer, registry  # noqa: E402

STEP, STEPS, PEOPLE = arguments.step, 20, 6
directory = Path(tempfile.mkdtemp())
database = Database(directory / 'database.sqlite3', registry)
database.initialize()

def handler(tx, context, payload):
    done = context.checkpoint or 0
    if done == STEPS:
        return None
    for index in range(50):
        tx.create(Customer, customer_name=f'Imported {done}-{index}')
    time.sleep(STEP)
    return done + 1

jobs = JobQueue(database, [Task('import', handler, resumable=True)])
jobs.initialize()
with database.transaction() as tx:
    jobs.enqueue(tx, 'import', {}, system_role='editor')
waits, errors, stop = [], [], threading.Event()

def person(index):
    count = 0
    while not stop.is_set():
        began = time.monotonic()
        try:
            database.create(Customer, customer_name=f'Person {index} {count}')
            waits.append(time.monotonic() - began)
        except Exception as exc:
            errors.append(str(exc))
        count += 1
        time.sleep(0.01)

threads = [threading.Thread(target=person, args=(i,)) for i in range(PEOPLE)]
for thread in threads:
    thread.start()
began = time.monotonic()
jobs.run_one()
took = time.monotonic() - began
stop.set()
for thread in threads:
    thread.join()
waits.sort()
print(json.dumps({'step_s': STEP, 'saves': len(waits), 'failed': len(errors), 'errors': sorted(set(errors)),
                  'median_ms': round(1000 * statistics.median(waits), 1), 'p95_ms': round(1000 * waits[int(0.95 * len(waits))], 1),
                  'max_ms': round(1000 * waits[-1], 1), 'job_s': round(took, 2)}))
shutil.rmtree(directory)
