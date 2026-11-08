"""Progress and resumable steps for long jobs, on real SQLite (docs/jobs-realtime.md)."""

import time

import pytest

from evlet import JobQueue, Task
from evlet.jobs import LEASE, encode_checkpoint, progress_text, table
from examples.playground.models import Customer, PointBalance, PointMove
from test_business import secured, hashed, client


def queue(secured, handler, **kwargs):
    jobs = JobQueue(secured.database, [Task('rebuild', handler, retry_delay=2, **kwargs)], events=secured.business.events)
    jobs.initialize()
    return jobs


def submit(jobs, **kwargs):
    with jobs.database.transaction() as tx:
        return jobs.enqueue(tx, 'rebuild', {'batch': 2}, **kwargs)


def raw(jobs, job_id, table='evlet_jobs', key='id'):
    with jobs.database._connection() as db:
        row = db.execute(f'SELECT * FROM {table} WHERE {key}=?', (job_id,)).fetchone()
        return None if row is None else dict(row)


def customers(secured, count):
    with secured.database.transaction() as tx:
        return [tx.create(Customer, customer_name=f'Customer {index}').id for index in range(count)]


def stepper(log, fail=None):
    """Write one Customer per item in batches of payload['batch'], reporting progress; the checkpoint is the next index."""
    def handler(tx, job, payload):
        start = job.checkpoint or 0
        log.append((job.attempt, start))
        end = min(start + payload['batch'], 6)
        for index in range(start, end):
            tx.create(Customer, customer_name=f'Row {index}')
        job.progress(end, 6, f'Rows {start} to {end - 1}')
        if fail and fail(job, start):
            raise RuntimeError('Stopped mid step')
        return end if end < 6 else None
    return handler


def names(secured):
    return sorted(customer.customer_name for customer in secured.database.list(Customer, limit=100))


def test_resumable_task_commits_each_step_with_its_progress(secured):
    log = []
    jobs = queue(secured, stepper(log), resumable=True)
    receipt = submit(jobs)
    assert jobs.run_one()
    assert log == [(1, 0), (1, 2), (1, 4)]
    assert names(secured) == [f'Row {index}' for index in range(6)]
    assert raw(jobs, receipt.id)['state'] == 'succeeded'
    progress = raw(jobs, receipt.id, 'evlet_job_progress', 'job_id')
    assert (progress['done'], progress['total'], progress['message'], progress['checkpoint']) == (6, 6, 'Rows 4 to 5', None)
    [row] = jobs.recent()
    assert (row['done'], row['total'], row['message']) == (6, 6, 'Rows 4 to 5')
    assert table(jobs.recent()).splitlines()[1].split() == [f'#{receipt.id}', 'rebuild', 'succeeded', '1', '6', 'of', '6', '(100%)', 'Rows', '4', 'to', '5']


def test_stopped_job_resumes_from_its_last_committed_checkpoint(secured):
    """The worker stops after two steps; a restarted worker carries on from the third without repeating work."""
    log = []
    jobs = queue(secured, stepper(log), resumable=True)
    receipt = submit(jobs)
    checks = iter([True, True, True, False])  # run_one's own check, then one per step
    assert jobs.run_one(active=lambda: next(checks, False)) is True
    stopped = raw(jobs, receipt.id)
    assert (stopped['state'], stopped['attempts'], stopped['lease']) == ('queued', 0, None)
    assert names(secured) == ['Row 0', 'Row 1', 'Row 2', 'Row 3']
    saved = raw(jobs, receipt.id, 'evlet_job_progress', 'job_id')
    assert (saved['done'], saved['total'], saved['checkpoint']) == (4, 6, '4')
    restarted = queue(secured, stepper(log), resumable=True)
    assert restarted.run_one()
    assert log == [(1, 0), (1, 2), (1, 4)]
    assert names(secured) == [f'Row {index}' for index in range(6)]
    assert raw(jobs, receipt.id)['state'] == 'succeeded'


def test_failed_step_rolls_back_alone_and_the_retry_resumes(secured):
    log = []
    jobs = queue(secured, stepper(log, fail=lambda job, start: job.attempt == 1 and start == 4), resumable=True)
    receipt = submit(jobs)
    now = int(time.time())
    assert jobs.run_one(now=now)
    failed = raw(jobs, receipt.id)
    assert (failed['state'], failed['error']) == ('queued', 'RuntimeError')
    # The third step's rows and progress rolled back; the first two steps stay.
    assert names(secured) == ['Row 0', 'Row 1', 'Row 2', 'Row 3']
    assert raw(jobs, receipt.id, 'evlet_job_progress', 'job_id')['done'] == 4
    assert jobs.run_one(now=failed['run_at'])
    assert log == [(1, 0), (1, 2), (1, 4), (2, 4)]
    assert names(secured) == [f'Row {index}' for index in range(6)]
    assert raw(jobs, receipt.id)['state'] == 'succeeded'


def test_manual_retry_of_a_failed_job_resumes_too(secured):
    log = []
    jobs = queue(secured, stepper(log, fail=lambda job, start: start == 2 and job.attempt < 3), resumable=True)
    jobs.tasks['rebuild'] = Task('rebuild', jobs.tasks['rebuild'].handler, max_attempts=1, resumable=True)
    receipt = submit(jobs)
    assert jobs.run_one()
    assert raw(jobs, receipt.id)['state'] == 'failed'
    jobs.retry(receipt.id)
    with jobs.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_jobs SET max_attempts=3, attempts=2 WHERE id=?', (receipt.id,))
    assert jobs.run_one()
    assert log[-2:] == [(3, 2), (3, 4)] and log[0] == (1, 0)
    assert names(secured) == [f'Row {index}' for index in range(6)]


def test_each_step_renews_the_lease_and_a_crashed_worker_is_resumed(secured):
    """A worker that dies mid step loses that step only: the next worker reclaims after the lease and resumes."""
    seen = []

    def handler(tx, job, payload):
        seen.append(tx.connection.execute('SELECT lease_until FROM evlet_jobs WHERE id=?', (job.id,)).fetchone()[0])
        start = job.checkpoint or 0
        tx.create(Customer, customer_name=f'Row {start}')
        job.progress(start + 1, 3)
        if start == 1 and job.attempt == 1:
            raise SystemExit  # Stands in for the process dying: not handled as a job failure.
        return start + 1 if start < 2 else None

    jobs = queue(secured, handler, resumable=True)
    receipt = submit(jobs)
    now = int(time.time())
    claim = jobs.claim(now=now, lease_seconds=5)
    with pytest.raises(SystemExit):
        jobs.execute(claim, now=now)
    assert seen[0] == now + 5 and seen[1] >= now + LEASE
    crashed = raw(jobs, receipt.id)
    assert crashed['state'] == 'running' and names(secured) == ['Row 0']
    assert jobs.claim(now=now + 6) is None  # The renewed lease still holds.
    assert jobs.run_one(now=seen[1])
    assert names(secured) == ['Row 0', 'Row 1', 'Row 2']
    assert raw(jobs, receipt.id)['attempts'] == 2


def test_plain_and_external_tasks_report_progress(secured):
    def plain(tx, job, payload):
        job.progress(1, 2, 'Half way')
        job.progress(2, 2)
    jobs = queue(secured, plain)
    receipt = submit(jobs)
    assert jobs.run_one()
    progress = raw(jobs, receipt.id, 'evlet_job_progress', 'job_id')
    assert (progress['done'], progress['total'], progress['message']) == (2, 2, '')

    seen = []
    def external(job, payload):
        job.progress(3, None, 'Sent 3')
        # Saved at once, outside any transaction the task holds.
        seen.append(raw(jobs, job.id, 'evlet_job_progress', 'job_id')['done'])
    jobs = queue(secured, external, external=True)
    receipt = submit(jobs)
    assert jobs.run_one() and seen == [3]
    assert progress_text(3, None, 'Sent 3') == '3 Sent 3' and progress_text(None, None) == ''


def test_progress_status_hides_the_checkpoint(secured):
    jobs = queue(secured, stepper([]), resumable=True)
    secured.business.jobs = jobs
    admin = client(secured)
    with secured.database._connection() as db:
        user_id = db.execute("SELECT id FROM evlet_users WHERE username='admin'").fetchone()[0]
    from evlet import User
    receipt = submit(jobs, user=User(user_id, 'admin', 'admin'))
    status, data, _ = admin('GET', f'/api/_jobs/{receipt.id}')
    assert status == 200 and data['progress'] is None
    checks = iter([True, True, False])
    jobs.run_one(active=lambda: next(checks, False))
    status, data, _ = admin('GET', f'/api/_jobs/{receipt.id}')
    assert data['state'] == 'queued' and data['progress'] == {'done': 2, 'total': 6, 'message': 'Rows 0 to 1'}
    assert 'checkpoint' not in str(data)


def test_progress_and_task_options_are_validated(secured):
    def bad(tx, job, payload):
        job.progress(*payload['args'])
    jobs = queue(secured, bad)
    for args in ([-1], [3, 2], [1.5], [1, None, 'x' * 201], [True]):
        with jobs.database.transaction() as tx:
            receipt = jobs.enqueue(tx, 'rebuild', {'args': args})
        assert jobs.run_one()
        assert raw(jobs, receipt.id)['error'] == 'ValueError'
    with pytest.raises(ValueError):
        JobQueue(secured.database, [Task('both', lambda job, payload: None, external=True, resumable=True)])
    with pytest.raises(ValueError):
        JobQueue(secured.database, [Task('flag', lambda tx, job, payload: None, resumable=1)])
    with pytest.raises(ValueError):
        encode_checkpoint('x' * 16384)
    with pytest.raises(ValueError):
        encode_checkpoint(float('nan'))


def test_playground_rebuilds_points_balances_in_steps(secured, monkeypatch):
    """The Playground's Rebuild points balances job over the ledger models, stopped half way and resumed."""
    from examples.playground import app as playground
    monkeypatch.setattr(playground, 'REBUILD_BATCH', 2)
    ids = customers(secured, 5)
    with secured.database.transaction() as tx:
        for customer, points in zip(ids, (10, 20, 0, 5, 7)):
            if points:
                tx.create(PointMove, customer=customer, on_date='2026-11-08', reason='Opening', points=points)
                tx.create(PointMove, customer=customer, on_date='2026-11-08', reason='Spent', points=-1)
        tx.create(PointBalance, customer=ids[0], points=999)  # A wrong cached total the rebuild corrects.
    jobs = secured.business.jobs
    with secured.database.transaction() as tx:
        receipt = jobs.enqueue(tx, 'rebuild_points', {})
    checks = iter([True, True, True, False])  # run_one's own check, then one per step
    jobs.run_one(active=lambda: next(checks, False))
    assert raw(jobs, receipt.id)['state'] == 'queued'
    assert raw(jobs, receipt.id, 'evlet_job_progress', 'job_id')['done'] == 4
    jobs.run_one()
    balances = {row.customer: row.points for row in secured.database.list(PointBalance, limit=100)}
    assert balances == {ids[0]: 9, ids[1]: 19, ids[3]: 4, ids[4]: 6}
    progress = raw(jobs, receipt.id, 'evlet_job_progress', 'job_id')
    assert (raw(jobs, receipt.id)['state'], progress['done'], progress['total']) == ('succeeded', 5, 5)


def test_jobs_list_command_prints_progress(secured, tmp_path, monkeypatch, capsys):
    from evlet.cli import jobs as command
    from evlet.cli.main import main
    jobs = queue(secured, stepper([]), resumable=True)
    secured.business.jobs = jobs
    receipt = submit(jobs)
    checks = iter([True, True, False])
    jobs.run_one(active=lambda: next(checks, False))
    root = tmp_path / 'project'
    for name in ('apps', 'instances/acme'):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(command, 'load_runtime', lambda project, name: secured if (project, name) == (root, 'acme') else None)
    main(['jobs', 'list', '--instance', 'acme', '--project', str(root)])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ['Job', 'Task', 'State', 'Attempts', 'Progress']
    assert lines[1].split() == [f'#{receipt.id}', 'rebuild', 'queued', '0', '2', 'of', '6', '(33%)', 'Rows', '0', 'to', '1']
    assert table([]) == 'No jobs'
