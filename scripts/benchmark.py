"""Reproducible SQLite benchmark. Writes only a new disposable SQLite database set."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
from pathlib import Path
import platform
import resource
import sqlite3
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evlet import Application, Currency, Database, Integer, LocalAuth, Model, Registry, Text
from evlet import qb
from evlet.runtime import runtime


class GLEntry(Model):
    account = Integer(required=True, index=True)
    posting_day = Integer(required=True)
    amount = Currency(required=True)


class StockEntry(Model):
    item = Integer(required=True, index=True)
    quantity = Integer(required=True)


class Invoice(Model):
    reference = Text(required=True, unique=True)
    total = Currency(required=True)


def timing(function):
    start = time.perf_counter()
    value = function()
    return (time.perf_counter() - start) * 1000, value


def stats(values):
    if not values:
        return {'samples': 0, 'median_ms': None, 'p95_ms': None, 'p99_ms': None, 'max_ms': None}
    ordered = sorted(values)
    return {'samples': len(values), 'median_ms': round(statistics.median(values), 3),
            'p95_ms': round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3),
            'p99_ms': round(ordered[min(len(ordered)-1, int(len(ordered)*.99))], 3),
            'max_ms': round(max(values), 3)}


def request(app, cookie):
    async def run():
        sent = []
        async def receive(): return {'type': 'http.request', 'body': b''}
        async def send(event): sent.append(event)
        await app({'type': 'http', 'method': 'GET', 'path': '/api/glentry',
                   'query_string': b'limit=20&filters=%7B%22account%22%3A42%7D',
                   'headers': [(b'host', b'127.0.0.1:8000'), (b'cookie', cookie.encode())]}, receive, send)
        status = sent[0]['status']
        if status not in (200, 503): raise RuntimeError(f'Authenticated ASGI request failed: {status}')
        if status == 200:
            assert len(json.loads(sent[1]['body'])['items']) == 20
        return status
    return asyncio.run(run())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', type=Path, required=True)
    parser.add_argument('--gl', type=int, default=2_000_000)
    parser.add_argument('--stock', type=int, default=500_000)
    parser.add_argument('--users', type=int, default=50)
    parser.add_argument('--requests', type=int, default=500)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.gl,args.stock,args.users,args.requests,args.seconds) < 1 or args.gl < 20000:
        parser.error('Choose positive sizes; GL needs at least 20000 rows')
    registry = Registry(GLEntry, StockEntry, Invoice)
    args.sqlite.parent.mkdir(parents=True, exist_ok=True)
    with args.sqlite.open('xb'): pass
    db = Database(args.sqlite, registry)
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    app = Application(db, auth=auth); app.initialize()
    # Dedicated synthetic benchmark session. Password hashing is outside measured requests.
    import hashlib
    import secrets
    token = secrets.token_hex(32)
    with db.transaction() as tx:
        uid = tx.connection.run(qb.insert('evlet_users', username='benchmark', password='disabled-password', role='viewer').returning_id()).id
    with db.transaction(store='sessions') as tx:
        tx.connection.run(qb.insert('evlet_sessions', token=hashlib.sha256(token.encode()).hexdigest(), user_id=uid, csrf='benchmark', expires=int(time.time()) + 3600, realm='desk', started=int(time.time())))
    def load():
        datasets = [('glentry', ('account','posting_day','amount'), ((i%1000, i%365, 100 if i%2 == 0 else -100) for i in range(args.gl))),
                    ('stockentry', ('item','quantity'), ((i%1000, 1 if i%2 == 0 else -1) for i in range(args.stock)))]
        with db.transaction() as tx:
            for table, columns, rows in datasets:
                tx.connection.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', rows)
    load_ms, _ = timing(load)
    with db._connection() as c:
        c.execute('ANALYZE')
        version = c.execute('SELECT sqlite_version()').fetchone()[0]
        settings = {name: c.execute('PRAGMA '+name).fetchone()[0] for name in ('journal_mode','synchronous','busy_timeout')}
    hardware = {}
    if sys.platform == 'darwin':
        for key in ('machdep.cpu.brand_string', 'hw.memsize'):
            hardware[key] = subprocess.check_output(['sysctl','-n',key], text=True).strip()
    def report(sql, values=()):
        with db._connection() as c: return c.execute(sql, values).fetchall()
    def day(): return report('SELECT SUM(amount) FROM glentry WHERE posting_day=?', (42,))
    before = [timing(day)[0] for _ in range(5)]
    with db.transaction() as tx:
        tx.connection.execute('CREATE INDEX benchmark_posting_day ON glentry(posting_day)')
    after = [timing(day)[0] for _ in range(5)]
    trial = [timing(lambda: report('SELECT account,SUM(amount) FROM glentry GROUP BY account'))[0] for _ in range(3)]
    stock = [timing(lambda: report('SELECT item,SUM(quantity) FROM stockentry GROUP BY item'))[0] for _ in range(3)]
    assert report('SELECT COUNT(*),SUM(amount) FROM glentry')[0][0] == args.gl
    assert report('SELECT SUM(amount) FROM glentry')[0][0] == (100 if args.gl%2 else 0)
    assert report('SELECT SUM(quantity) FROM stockentry')[0][0] == (1 if args.stock%2 else 0)
    writes, errors = [], []
    def write_invoices():
        for i in range((args.seconds+2)//3):
            if i: time.sleep(3)
            def write():
                with db.transaction() as tx:
                    invoice = tx.create(Invoice, reference=f'load-{i}', total='10.00')
                    tx.create(GLEntry, account=1, posting_day=364, amount='10.00')
                    tx.create(GLEntry, account=2, posting_day=364, amount='-10.00')
                    tx.create(StockEntry, item=1, quantity=-1)
                return invoice
            try: writes.append(timing(write)[0])
            except Exception as exc: errors.append(type(exc).__name__)
    cpu_start = time.process_time(); start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.users+1) as pool:
        writer = pool.submit(write_invoices)
        deadline = start + args.seconds
        def read(_):
            samples = []
            while time.perf_counter() < deadline or len(samples) < (args.requests+args.users-1)//args.users:
                try:
                    elapsed_ms, status = timing(lambda: request(app, 'evlet_session='+token))
                    samples.append((elapsed_ms, status))
                    if status == 503:
                        time.sleep(1)  # Honor Retry-After; do not turn overload into a retry storm.
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    break
            return samples
        attempts = [v for samples in pool.map(read, range(args.users)) for v in samples]
        latencies = [elapsed_ms for elapsed_ms, status in attempts if status == 200]
        read_elapsed = time.perf_counter()-start
        writer.result()
    elapsed = time.perf_counter()-start
    if errors: raise RuntimeError(f'Benchmark errors: {errors}')
    assert db.count(Invoice) == len(writes)
    assert report('SELECT SUM(amount) FROM glentry')[0][0] == (100 if args.gl%2 else 0)
    with db._connection() as c:
        size = sum(p.stat().st_size for p in args.sqlite.parent.glob(args.sqlite.name+'*'))
    assets = list((Path(__file__).resolve().parents[1]/'frontend/dist/assets').glob('*'))
    result = {'adapter': 'sqlite', 'database_version': version,
              'platform': platform.platform(), 'machine': platform.machine(), 'cpu_count': os.cpu_count(),
              'python': platform.python_version(), 'hardware': hardware, 'database_settings': settings, 'dataset': {'gl': args.gl, 'stock': args.stock},
              'users': args.users, 'requests': stats(latencies),
              'attempts': stats([elapsed_ms for elapsed_ms, _ in attempts]),
              'http_statuses': {str(status): sum(code == status for _, code in attempts) for status in (200, 503)},
              'http_limits': {name: lane.limit for name, lane in runtime().lanes.items()},
              'http_workers': {name: lane.workers for name, lane in runtime().lanes.items()}, 'request_phase_seconds': round(read_elapsed,3),
              'requests_per_second': round(len(latencies)/read_elapsed,3), 'elapsed_seconds': round(elapsed,3),
              'invoice_transactions': stats(writes), 'invoice_interval_seconds': 3, 'errors': errors,
              'trial_balance': stats(trial), 'stock_balance': stats(stock),
              'posting_day_without_index': stats(before), 'posting_day_with_index': stats(after),
              'dataset_load_ms': round(load_ms,3), 'database_bytes': size,
              'client_cpu_seconds': round(time.process_time()-cpu_start,3),
              'client_peak_rss_native_units': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'frontend_asset_bytes': sum(p.stat().st_size for p in assets),
              'frontend_asset_gzip_bytes': sum(len(gzip.compress(p.read_bytes())) for p in assets),
              'method': 'Local authenticated ASGI calls, no network/TLS. SQL reports use trusted adapter connections. Bulk load bypasses model construction. Writes serialize per database.'}
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    db.close()
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
