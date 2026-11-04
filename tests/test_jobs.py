from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

import pytest

from evlet import EventStore, JobQueue, Task, User, ValidationError
from examples.playground.models import Customer, Project, ProjectTask
from test_business import secured, hashed, client


def queue(secured, handler, **kwargs):
    result = JobQueue(secured.database, [Task('work', handler, retry_delay=2)], events=secured.business.events, **kwargs)
    result.initialize()
    return result


def submit(jobs, **kwargs):
    with jobs.database.transaction() as tx:
        return jobs.enqueue(tx, 'work', {'value': 1}, **kwargs)


def raw(jobs, job_id):
    with jobs.database._connection() as db:
        return dict(db.execute('SELECT * FROM evlet_jobs WHERE id=?', (job_id,)).fetchone())


def test_enqueue_and_business_write_commit_or_roll_back_together(secured):
    jobs = queue(secured, lambda tx, context, payload: None)
    with pytest.raises(RuntimeError):
        with secured.database.transaction() as tx:
            project = tx.create(Project, title='rollback')
            jobs.enqueue(tx, 'work', {}, target=(Project, project.id))
            raise RuntimeError('abort')
    assert secured.database.count(Project) == 0 and jobs.claim() is None
    with secured.database.transaction() as tx:
        project = tx.create(Project, title='commit')
        receipt = jobs.enqueue(tx, 'work', {}, target=(Project, project.id))
    assert raw(jobs, receipt.id)['state'] == 'queued'


def test_dedupe_matches_payload_and_survives_completion(secured):
    jobs = queue(secured, lambda tx, context, payload: tx.create(Customer, customer_name=context.tenant))
    first = submit(jobs, key='same')
    assert submit(jobs, key='same').id == first.id
    with secured.database.transaction() as tx:
        with pytest.raises(ValueError): jobs.enqueue(tx, 'work', {'different': True}, key='same')
    assert jobs.run_one()
    assert submit(jobs, key='same').state == 'succeeded'
    assert not jobs.run_one() and secured.database.count(Customer) == 1


def test_competing_workers_claim_once(secured):
    jobs = queue(secured, lambda tx, context, payload: tx.create(Customer, customer_name='once'))
    receipt = submit(jobs)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: jobs.claim(), range(4)))
    assert len([claim for claim in claims if claim]) == 1
    assert jobs.execute(next(claim for claim in claims if claim))
    assert raw(jobs, receipt.id)['attempts'] == 1
    assert secured.database.count(Customer) == 1


def test_restart_recovers_lease_and_fences_old_worker(secured):
    jobs = queue(secured, lambda tx, context, payload: tx.create(Customer, customer_name='once'))
    now = int(time.time())
    receipt = submit(jobs, run_at=now)
    old = jobs.claim(now=now, lease_seconds=1)
    restarted = queue(secured, jobs.tasks['work'].handler)
    assert restarted.claim(now=now) is None
    replacement = restarted.claim(now=now + 2)
    assert jobs.execute(old, now=now + 2) is False
    assert restarted.execute(replacement, now=now + 2)
    assert raw(jobs, receipt.id)['attempts'] == 2 and secured.database.count(Customer) == 1


def test_failed_handler_rolls_back_then_retries_with_backoff(secured):
    def handler(tx, context, payload):
        tx.create(Customer, customer_name='committed only once')
        if context.attempt == 1:
            raise RuntimeError('private error details')
    jobs = queue(secured, handler)
    now = int(time.time()); receipt = submit(jobs, run_at=now)
    assert jobs.run_one(now=now)
    state = raw(jobs, receipt.id)
    assert state['state'] == 'queued' and state['run_at'] == now + 2 and state['error'] == 'RuntimeError'
    assert secured.database.count(Customer) == 0
    assert not jobs.run_one(now=now + 1)
    assert jobs.run_one(now=now + 2) and secured.database.count(Customer) == 1


def test_permanent_failure_and_expired_final_claim(secured):
    def invalid(tx, context, payload): raise ValidationError({'field': 'invalid'})
    jobs = queue(secured, invalid)
    receipt = submit(jobs)
    jobs.run_one()
    assert raw(jobs, receipt.id)['state'] == 'failed'
    jobs.retry(receipt.id)
    assert raw(jobs, receipt.id)['attempts'] == 0
    now = int(time.time())
    for index in range(3): assert jobs.claim(now=now + index * 2, lease_seconds=1)
    assert jobs.claim(now=now + 6, lease_seconds=1) is None
    assert raw(jobs, receipt.id)['state'] == 'failed'


def test_schedules_coalesce_missed_runs_and_disable(secured):
    jobs = queue(secured, lambda tx, context, payload: None)
    jobs.schedule('hourly', 'work', {}, every=3600, start_at=100)
    assert jobs.tick(now=99) == 0
    assert jobs.tick(now=7300) == 1
    assert jobs.tick(now=7300) == 0
    with jobs.database._connection() as db:
        assert db.execute('SELECT count(*) FROM evlet_jobs').fetchone()[0] == 1
        assert db.execute("SELECT next_run FROM evlet_schedules WHERE name='hourly'").fetchone()[0] == 10900
    jobs.set_schedule_active('hourly', False)
    assert jobs.tick(now=20000) == 0


def test_actor_permission_rechecked_and_status_hidden(secured):
    c = client(secured, 'editor')
    project = c('POST', '/api/project', {'title': 'Job', 'children': {'tasks': [{'title': 'Done', 'done': True}]}})[1]
    status, response, _ = c('POST', f"/api/project/{project['id']}/actions/refresh_progress", {})
    assert status == 202
    job_id = response['job']['id']
    assert c('GET', f'/api/_jobs/{job_id}')[1]['state'] == 'queued'
    viewer = client(secured, 'viewer')
    assert viewer('GET', f'/api/_jobs/{job_id}')[0] == 404
    with secured.database.transaction() as tx: tx.connection.execute("UPDATE evlet_users SET role='viewer' WHERE username='editor'")
    secured.business.jobs.run_one()
    assert raw(secured.business.jobs, job_id)['state'] == 'failed'
    assert secured.database.get(Project, project['id']).progress == 0


def test_project_job_updates_and_publishes_only_after_success(secured):
    c = client(secured)
    project = c('POST', '/api/project', {'title': 'Job', 'children': {'tasks': [{'title': 'Done', 'done': True}, {'title': 'Pending'}]}})[1]
    response = c('POST', f"/api/project/{project['id']}/actions/refresh_progress", {})[1]
    assert secured.business.jobs.run_one()
    assert secured.database.get(Project, project['id']).progress == 50
    job = c('GET', f"/api/_jobs/{response['job']['id']}")[1]
    assert job['state'] == 'succeeded' and 'payload' not in job and 'actor' not in job


def test_queue_refuses_other_tenant_namespace_and_pauses_disabled_work(secured):
    jobs = queue(secured, lambda tx, context, payload: tx.create(Customer, customer_name='work'))
    receipt = submit(jobs)
    with pytest.raises(ValueError): queue(secured, lambda *args: None, tenant='other')
    assert not jobs.run_one(active=lambda: False)
    assert raw(jobs, receipt.id)['state'] == 'queued'
    claim = jobs.claim()
    assert not jobs.execute(claim, active=lambda: False)
    assert secured.database.count(Customer) == 0
