"""Record search timings (docs/performance.md#record-search): fuzzy search over Meta.search_fields and the
plain list search, through GET /api/{model}/search on a disposable SQLite file. Usage:

    .venv/bin/python scripts/benchmark_search.py [records, default 100000]
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


class Product(Model):
    class Meta:
        search_fields = ('code', 'barcode')
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    title = Text(required=True)
    code = Text(default='')
    barcode = Text(default='')

class Plain(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    title = Text(required=True)
    code = Text(default='')

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
words = ['box', 'tape', 'cable', 'steel', 'bolt', 'nut', 'pipe', 'valve', 'red', 'blue', 'large', 'small', 'carton', 'pack']
random.seed(1)
path = Path(tempfile.mkdtemp()) / 'db.sqlite3'  # disposable
db = Database(path, Registry(Product, Plain))
app = start(db)
t = time.perf_counter()
with db.transaction() as tx:
    for i in range(N):
        title = ' '.join(random.choice(words) for _ in range(3)) + f' {i}'
        tx.create(Product, title=title, code=f'P-{i:06d}', barcode=f'{4006381000000 + i}')
        tx.create(Plain, title=title, code=f'P-{i:06d}')
print(f'seeded {N} in {time.perf_counter() - t:.1f}s')
admin = client(app)
def run(url, times=20):
    samples = []
    for _ in range(times):
        t = time.perf_counter(); status, body, _ = admin('GET', url); samples.append((time.perf_counter() - t) * 1000)
        assert status == 200, body
    samples.sort()
    return f'p50 {statistics.median(samples):.1f} ms, p95 {samples[int(len(samples) * .95) - 1]:.1f} ms'
for label, url in (('fuzzy, common ("box")', '/api/product/search?q=box'), ('fuzzy, in order ("bxtp")', '/api/product/search?q=bxtp'),
                   ('fuzzy, rare ("qzx", no match)', '/api/product/search?q=qzx'), ('fuzzy, barcode', '/api/product/search?q=4006381012345'),
                   ('plain contains ("box")', '/api/plain/search?q=box'), ('empty text', '/api/product/search')):
    print(f'{label}: {run(url)}')
