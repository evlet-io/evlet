"""Insert rows into an append-only ledger model with Session.insert_many on a temporary SQLite file.

Writes one transaction per batch, then reads a grouped Sum over every row. Prints JSON; the result of
1,000,000 rows is kept in docs/benchmarks/. A measurement, not a pass/fail threshold.

    .venv/bin/python scripts/benchmark_ledger.py [rows] [batch]
"""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import platform
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from evlet import Database, Date, Decimal, Link, Model, Registry, Sum, Text


class Member(Model):
    full_name = Text(required=True)


class PointMove(Model):
    member = Link(Member, required=True)
    on_date = Date(required=True)
    reason = Text(default='')
    points = Decimal(scale=2, required=True)

    class Meta:
        ledger = True
        append_only = True
        timestamps = 'created'
        indexes = [('member', 'on_date')]


def main(rows=1_000_000, batch=10_000):
    with TemporaryDirectory() as directory:
        database = Database(Path(directory) / 'ledger.sqlite3', Registry(Member, PointMove))
        database.initialize()
        with database.transaction() as tx:
            members = [tx.create(Member, full_name=f'Member {index}').id for index in range(100)]
        start, day = time.perf_counter(), date(2026, 1, 1)
        written, batches = 0, []
        while written < rows:
            size = min(batch, rows - written)
            values = [{'member': members[(written + index) % 100], 'on_date': day + timedelta(days=(written + index) % 365),
                       'reason': 'earned', 'points': '1.25'} for index in range(size)]
            began = time.perf_counter()
            with database.transaction() as tx:
                tx.insert_many(PointMove, values)
            batches.append(time.perf_counter() - began)
            written += size
        inserted = time.perf_counter() - start
        began = time.perf_counter()
        groups = database.aggregate(PointMove, measures={'points': Sum('points')}, group_by=('member',))
        summed = time.perf_counter() - began
        assert len(groups) == 100 and database.count(PointMove) == rows
        size = (Path(directory) / 'ledger.sqlite3').stat().st_size
        batches.sort()
        return {
            'adapter': 'sqlite', 'database_version': sqlite3.sqlite_version, 'platform': platform.platform(),
            'machine': platform.machine(), 'python': platform.python_version(),
            'model': 'append-only ledger: Link, Date, Text, Decimal(scale=2), created_at, one composite index',
            'rows': rows, 'batch': batch, 'transactions': len(batches),
            'insert_seconds': round(inserted, 3), 'rows_per_second': round(rows / inserted),
            'batch_median_ms': round(batches[len(batches) // 2] * 1000, 3), 'batch_max_ms': round(batches[-1] * 1000, 3),
            'grouped_sum_seconds': round(summed, 3), 'database_bytes': size,
        }


if __name__ == '__main__':
    arguments = [int(value) for value in sys.argv[1:3]]
    print(json.dumps(main(*arguments), indent=2))
