"""Background Playground: a price list that people ask to build and the application builds in a job.

    .venv/bin/python -m uvicorn examples.playground.background.app:create_app --factory --host 127.0.0.1
    .venv/bin/python -m examples.playground.background.app      # the job worker, in a second terminal

It uses a separate demonstration database. It shows three public APIs (docs/jobs-realtime.md):

* `Move(..., system=True)`: Start, Finish and Fail are made by the application only. People never see them
  as buttons, and every HTTP caller is refused them.
* `self.context.enqueue(...)` in an action: Build queues the job in the action's transaction, so it is
  queued only if the action commits, and a worker in the same process is woken at once.
* `job.business(tx, system=..., role=...)`: the job moves the price list through its normal checks, and its
  Journal says the Price list builder did it.
"""
from pathlib import Path

from evlet import (Application, Boolean, Database, Integer, JobQueue, LocalAuth, Model, Registry, Rule, State, Task,
                   Text, action, state)
from evlet.calc import F

# Steps of a build: each is one committed step of the resumable job, so the record shows its progress.
STEPS = 5


class PriceList(Model):
    """A price list people ask to build; only the application starts, finishes or fails the build."""
    title = Text(required=True, max_length=120)
    items = Integer(default=100, minimum=1, maximum=10000, label='Items to price')
    requested = Boolean(default=False, label='Build requested')
    progress = Text(default='', max_length=200)
    status = State(
        State.Step('draft'),
        State.Step('building'),
        State.Step('ready'),
        State.Step('failed'),
        rules=(
            State.Rule('draft', 'building', 'Start', key='building', by=()),
            State.Rule('building', 'ready', 'Finish', key='ready', by=()),
            State.Rule('building', 'failed', 'Fail', key='failed', by=()),
            State.Rule(('ready', 'failed'), 'draft', 'Reopen', key='draft'),
        ), default='draft', show='bar')
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
                   'viewer': Rule(('read',))}

    @action('Build', available_when=(F('status') == 'draft') & (F('requested') == False))  # noqa: E712
    def build(self):
        """Queue the build. The job runs once this action commits; nothing is queued if it fails."""
        self.requested = True
        self.progress = 'Waiting for the worker'
        return self.context.enqueue('build_price_list', {'price_list': str(self.id)})

    @state('ready')
    def finished(self):
        self.requested = False
        self.progress = f'Priced {self.items} items'


def build_price_list(tx, job, payload):
    """One step of a build, acting as the system: the first step starts it, the last one finishes it."""
    context = job.business(tx, system='Price list builder', role='admin')
    price_list = context.get(PriceList, payload['price_list'])
    if price_list.status == 'draft':
        context.move(PriceList, price_list.id, 'building')
        return 0
    if price_list.status != 'building':
        return None
    done = job.checkpoint + 1
    job.progress(done, STEPS, f'Step {done} of {STEPS}')
    if done < STEPS:
        context.update(PriceList, price_list.id, progress=f'Step {done} of {STEPS}')
        return done
    context.move(PriceList, price_list.id, 'ready')
    return None


def create_app(path=None):
    path = Path(path or Path(__file__).parent / '.data' / 'background.sqlite3')
    database = Database(path, Registry(PriceList))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    jobs = JobQueue(database, [Task('build_price_list', build_price_list, max_attempts=1, resumable=True)])
    return Application(database, auth=auth, jobs=jobs, name='Background Playground')


def work(app):
    """Run jobs until Control-C: half a second between passes, less when this process queued one."""
    while True:
        if not app.business.jobs.run_one():
            app.business.jobs.wait(0.5)


if __name__ == '__main__':
    application = create_app()
    application.initialize()
    try:
        work(application)
    except KeyboardInterrupt:
        pass
