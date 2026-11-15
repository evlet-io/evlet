"""Flat apps declare trusted background work in services/: module-level Task and Schedule objects.

They register when the app is installed or reloaded, like @cron, and run with the job context and the
database session that tasks passed to JobQueue get."""

import json
import sqlite3
import sys
import time

import pytest

from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.instances import load_runtime


BACKGROUND = '''"""Finance background work: a resumable worker that drains a queue and a nightly check."""
from evlet import Schedule, Task
from finance.models.item import FinanceItem


def drain(tx, job, payload):
    # One queued row per step; the checkpoint counts the rows done so far.
    rows = tx.list(FinanceItem, filters={'title__contains': 'queued:'}, order_by='id', limit=1)
    if not rows:
        return None
    tx.update(FinanceItem, rows[0].id, title='done:' + rows[0].title.removeprefix('queued:'))
    job.progress((job.checkpoint or 0) + 1)
    return (job.checkpoint or 0) + 1


def check(tx, job, payload):
    tx.create(FinanceItem, title=f"checked:{payload['books']}:{tx.count(FinanceItem)}")


drain_queue = Task('finance_drain', drain, resumable=True)
nightly_check = Task('finance_nightly', check)
nightly = Schedule('finance_nightly', 'finance_nightly', every=86400, payload={'books': 'main'})
'''


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    (root / 'apps').mkdir(parents=True)
    (root / 'instances').mkdir()
    yield root
    for name in list(sys.modules):
        if name.split('.')[0] in ('finance', 'payroll'):
            del sys.modules[name]


def finance(project, source=BACKGROUND):
    path = create_app(project, 'finance')
    (path / 'services').mkdir(exist_ok=True)
    (path / 'services/__init__.py').write_text('')
    (path / 'services/background.py').write_text(source)
    return path


def schedule_row(project, name='finance_nightly'):
    with sqlite3.connect(project / 'instances/acme/data/database.sqlite3') as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute('SELECT * FROM evlet_schedules WHERE name = ?', (name,)).fetchone()
        mine = connection.execute('SELECT * FROM evlet_app_schedules WHERE name = ?', (name,)).fetchone()
        return (dict(row) if row else None), (dict(mine) if mine else None)


def titles(app):
    model = app.database.registry.resolve('finance.FinanceItem')
    with app.database.transaction() as tx:
        return sorted(row.title for row in tx.list(model, limit=100))


def test_a_flat_app_installs_runs_reloads_and_removes_its_task_and_schedule(project):
    path = finance(project)
    create_instance(project, 'acme')
    questions = []
    install(project, 'acme', app_name='finance', confirm=lambda question: questions.append(question) or True)
    # The trust prompt names the background work and says it has full database access.
    assert questions and 'finance (task finance_drain, task finance_nightly, schedule finance_nightly)' in questions[0]
    assert 'full database access' in questions[0]
    row, mine = schedule_row(project)
    assert row['task'] == 'finance_nightly' and row['interval'] == 86400 and row['enabled'] == 1
    assert json.loads(row['payload']) == {'books': 'main'} and mine == {'name': 'finance_nightly', 'app': 'finance', 'retired': 0}
    first_due = row['next_run']

    app = load_runtime(project, 'acme')
    app.initialize()
    try:
        jobs = app.business.jobs
        with app.database.transaction() as tx:
            for name in ('a', 'b', 'c'):
                tx.create(app.database.registry.resolve('finance.FinanceItem'), title='queued:' + name)
            jobs.enqueue(tx, 'finance_drain', {})
        # A resumable task: one committed step per run, carrying its checkpoint, until it returns None.
        while jobs.run_one():
            pass
        drained = next(job for job in jobs.recent() if job['task'] == 'finance_drain')
        assert drained['state'] == 'succeeded'
        assert titles(app) == ['done:a', 'done:b', 'done:c']
        # The schedule queues its task when due, and the handler gets the real session and the payload.
        assert jobs.run_one(now=first_due)
        while jobs.run_one(now=first_due):
            pass
        assert next(job for job in jobs.recent() if job['task'] == 'finance_nightly')['state'] == 'succeeded'
        assert 'checked:main:3' in titles(app)
        jobs.set_schedule_active('finance_nightly', False)
    finally:
        app.database.close()
    after_run, _ = schedule_row(project)
    assert after_run['next_run'] > first_due and after_run['enabled'] == 0

    # Reload with a changed source: next-run state and the operator's disabled state are kept.
    (path / 'services/background.py').write_text(BACKGROUND + '\n# reviewed\n')
    install(project, 'acme', trust=True)
    assert schedule_row(project)[0] == after_run
    # A changed interval reconciles forward without re-enabling it.
    (path / 'services/background.py').write_text(BACKGROUND.replace('every=86400', 'every=43200'))
    install(project, 'acme', trust=True)
    changed, mine = schedule_row(project)
    assert changed['interval'] == 43200 and changed['enabled'] == 0 and mine['retired'] == 0

    # Removing the task and schedule disables the schedule and keeps its row and the job history.
    app = load_runtime(project, 'acme')
    app.initialize()
    try:
        app.business.jobs.set_schedule_active('finance_nightly', True)
        history = len(app.business.jobs.recent())
    finally:
        app.database.close()
    (path / 'services/background.py').write_text('"""No background work any more."""\n')
    install(project, 'acme', trust=True)
    removed, mine = schedule_row(project)
    assert removed is not None and removed['enabled'] == 0 and mine['retired'] == 1
    app = load_runtime(project, 'acme')
    app.initialize()
    try:
        assert 'finance_drain' not in app.business.jobs.tasks and len(app.business.jobs.recent()) == history
    finally:
        app.database.close()

    # Declared again, a schedule the app removed comes back enabled with a fresh due time.
    (path / 'services/background.py').write_text(BACKGROUND)
    before = int(time.time())
    install(project, 'acme', trust=True)
    restored, mine = schedule_row(project)
    assert restored['enabled'] == 1 and restored['next_run'] >= before + 86400 and mine['retired'] == 0


def test_names_are_app_qualified_and_a_clash_fails_before_activation(project):
    path = finance(project, BACKGROUND.replace("Task('finance_drain'", "Task('drain'"))
    create_instance(project, 'acme')
    with pytest.raises(ValueError, match="Task 'drain' in app finance must be named after its app, such as finance_drain"):
        install(project, 'acme', app_name='finance', trust=True)
    (path / 'services/background.py').write_text(BACKGROUND.replace("'finance_nightly', 'finance_nightly'", "'finance_nightly', 'finance_other'"))
    with pytest.raises(ValueError, match="runs task 'finance_other', which is not a Task declared in finance/services"):
        install(project, 'acme', app_name='finance', trust=True)
    (path / 'services/background.py').write_text(BACKGROUND + "\nagain = Task('finance_drain', drain)\n")
    with pytest.raises(ValueError, match="Task name 'finance_drain' is declared twice, in app finance"):
        install(project, 'acme', app_name='finance', trust=True)
    # Nothing was activated by the refused installs.
    assert not (project / 'instances/acme/data/database.sqlite3').exists()


def test_two_apps_cannot_register_the_same_task_name(project):
    from evlet.builds import clash
    from evlet.jobs import Task
    from types import SimpleNamespace
    handler = lambda tx, job, payload: None
    first = SimpleNamespace(tasks=(Task('finance_sync', handler),), schedules=())
    second = SimpleNamespace(tasks=(Task('finance_sync', handler),), schedules=())
    with pytest.raises(ValueError, match="Task name 'finance_sync' is declared twice, in apps finance and payroll"):
        clash([('finance', first), ('payroll', second)], 'tasks', 'Task')


def test_apps_without_background_work_keep_their_trust_prompt_and_cron(project):
    path = create_app(project, 'finance')
    (path / 'services').mkdir(exist_ok=True)
    (path / 'services/__init__.py').write_text('')
    (path / 'services/daily.py').write_text('from evlet import cron\n\n\n@cron("0 2 * * *", role="viewer")\ndef tidy(context):\n    return None\n')
    create_instance(project, 'acme')
    questions = []
    install(project, 'acme', app_name='finance', confirm=lambda question: questions.append(question) or True)
    assert questions == ['Trust and execute these local Python apps: finance?']
    with sqlite3.connect(project / 'instances/acme/data/database.sqlite3') as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM evlet_schedules WHERE name LIKE 'cron_%'")]
        assert len(names) == 1
        assert connection.execute('SELECT count(*) FROM evlet_app_schedules').fetchone()[0] == 0
