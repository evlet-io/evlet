"""List page timings (docs/performance.md#list-counts): GET /api/{model} with and without search on a
disposable SQLite file, showing what the page query and its capped count cost. Usage:

    .venv/bin/python scripts/benchmark_list.py [records, default 100000]
"""

import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from evlet import Database, Model, Registry, Rule, Text  # noqa: E402
from test_business import client  # noqa: E402
from test_rules_types import start  # noqa: E402


class Item(Model):
    class Meta:
        search_fields = ('title', 'code')
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    title = Text(required=True)
    code = Text(default='')


N = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
words = ['box', 'tape', 'cable', 'steel', 'bolt', 'nut', 'pipe', 'valve', 'red', 'blue', 'large', 'small', 'carton', 'pack']
random.seed(1)
path = Path(tempfile.mkdtemp()) / 'db.sqlite3'  # disposable
db = Database(path, Registry(Item))
app = start(db)
t = time.perf_counter()
with db.transaction() as tx:
    for i in range(N):
        tx.create(Item, title=' '.join(random.choice(words) for _ in range(3)) + f' {i}', code=f'P-{i:06d}')
print(f'seeded {N} in {time.perf_counter() - t:.1f}s')
admin = client(app)


def run(url, times=20):
    samples = []
    for _ in range(times):
        t = time.perf_counter(); status, body, _ = admin('GET', url); samples.append((time.perf_counter() - t) * 1000)
        assert status == 200, body
    samples.sort()
    return f"p50 {statistics.median(samples):.1f} ms, p95 {samples[int(len(samples) * .95) - 1]:.1f} ms (total {body['total']}{'+' if body.get('total_capped') else ''})"


for label, url in (('no search', '/api/item'), ('search, common ("box")', '/api/item?q=box'),
                   ('search, rare ("P-012345")', '/api/item?q=P-012345'), ('search, no match ("zzz")', '/api/item?q=zzz'),
                   ('sorted by title, page 3', '/api/item?order_by=title&offset=100')):
    print(f'{label}: {run(url)}')
