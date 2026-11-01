"""Measure the finite-v1 engine without a database: build, single edits and parent fan-out."""
from pathlib import Path
import json
import platform
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from finite_fixtures import Invoice, Project, MODELS
from evlet import Registry
from evlet.calc import Document


def invoice(rows):
    return {'inclusive': True, 'children': {'items': [{'_key': f'r{i}', 'qty': str(i % 7 + 1), 'rate': '12.35'} for i in range(rows)]}}


def project(phases, tasks):
    return {'children': {'phases': [{'_key': f'p{p}', 'children': {'tasks': [{'_key': f't{t}', 'hours': str(t % 5 + 1)} for t in range(tasks)]}}
                                    for p in range(phases)]}}


def measure(plan, data, edit, label, repeats=50):
    tracemalloc.start()
    start = time.perf_counter()
    document = Document(plan, data)
    cold = time.perf_counter() - start
    memory = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    timings = []
    for index in range(repeats):
        start = time.perf_counter()
        edit(document, index)
        timings.append(time.perf_counter() - start)
    timings.sort()
    return {'case': label, 'rows': len(document.rows) - 1, 'nodes': len(document.deps), 'cold_ms': round(cold * 1000, 2),
            'p50_ms': round(statistics.median(timings) * 1000, 3), 'p95_ms': round(timings[int(len(timings) * 0.95) - 1] * 1000, 3),
            'evaluated_last': document.trace['evaluated'], 'steps_last': document.trace['steps'], 'peak_kib': memory // 1024}


def main():
    registry = Registry(*MODELS.values())
    invoice_plan, project_plan = registry.plans[Invoice], registry.plans[Project]
    results = [
        measure(invoice_plan, invoice(100), lambda d, i: d.set(d.find(f'items.r{i % 100}'), 'qty', str(i % 9 + 1)), 'invoice-100 row edit'),
        measure(invoice_plan, invoice(100), lambda d, i: d.set(d.root.handle, 'inclusive', i % 2 == 0), 'invoice-100 parent toggle'),
        measure(project_plan, project(10, 99), lambda d, i: d.set(d.find(f'phases.p{i % 10}.tasks.t{i % 99}'), 'hours', str(i % 9 + 1)), 'project-1000 task edit'),
        measure(project_plan, project(10, 99), lambda d, i: d.set(d.root.handle, 'hourly_rate', str(100 + i)), 'project-1000 rate fan-out', 20),
    ]
    print(json.dumps({'python': platform.python_version(), 'machine': platform.machine(), 'system': platform.system(), 'results': results}, indent=1))


if __name__ == '__main__': main()
