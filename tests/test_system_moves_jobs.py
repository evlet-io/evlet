"""System moves (Move(..., system=True)), a job's business context (JobContext.business) and jobs an
action queues to run once its transaction commits (Context.enqueue). Real SQLite, through the ASGI routes."""

import pytest

from evlet import (Application, Boolean, Database, JobQueue, Model, Move, Registry, Rule, State, Task, Text,
                   ValidationError, action)
from evlet.calc import F
from evlet.identity import RecordId
from evlet.auth import AccessError, KeyAccess, LocalAuth, User
from evlet.connector import ConnectorError
from evlet.tools import Dispatch
from test_business import PASSWORD, client, http

ROLES = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
         'viewer': Rule(('read',))}
# Viewers may add a note to a build, but cannot see its status.
BUILD_ROLES = {**ROLES, 'viewer': Rule(('read', 'update'), read_fields=('title', 'ready', 'note'), write_fields=('note',))}
SEEN = []


class Build(Model):
    """A build people queue and only the application starts and finishes."""
    permissions = BUILD_ROLES
    title = Text(required=True, max_length=60)
    ready = Boolean(default=True)
    note = Text(default='', max_length=200)
    status = State(('new', 'queued', 'building', 'done'), default='new', moves=(
        Move('new', 'queued', 'Queue'),
        Move('queued', 'building', 'Start', system=True, when=F('ready'), run='started'),
        Move('building', 'done', 'Finish', system=True),
    ))

    def started(self):
        self.note = 'Started'

    @action('Build now', roles=('admin', 'editor'), available_when=F('status') == 'new')
    def build_now(self):
        """Queue the record and the job that builds it; the job runs once this action commits."""
        self.note = 'Waiting for the worker'
        return self.context.enqueue('build', {'build': str(self.id)})

    @action('Build and fail', roles=('admin',))
    def build_and_fail(self):
        self.context.enqueue('build', {'build': str(self.id)})
        raise ValidationError({'_form': 'Refused after queueing'})


class Starter(Model):
    """Another record whose action starts a build: trusted app code making a system move."""
    permissions = ROLES
    build_id = Text(max_length=40, default='')

    @action('Start the build', roles=('admin', 'editor'))
    def start_build(self):
        self.context.move(Build, self.build_id, 'building')


def build_job(tx, job, payload):
    """Start and finish the build as the system, through the document context."""
    context = job.business(tx, system='Build worker', role='admin')
    SEEN.append((context.user.username, context.user.role))
    context.move(Build, payload['build'], 'queued') if tx.get(Build, RecordId(payload['build'])).status == 'new' else None
    context.move(Build, payload['build'], 'building')
    context.move(Build, payload['build'], 'done')


def as_user(tx, job, payload):
    SEEN.append(job.business(tx).user)


@pytest.fixture
def app(tmp_path):
    SEEN.clear()
    database = Database(tmp_path / 'system.sqlite3', Registry(Build, Starter))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    jobs = JobQueue(database, [Task('build', build_job, max_attempts=1), Task('as_user', as_user, max_attempts=1)])
    app = Application(database, auth=auth, jobs=jobs)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    for name in ('editor', 'viewer'):
        auth.create_user(username=name, password=PASSWORD, role=name)
    return app


def create(admin, **values):
    status, body, _ = admin('POST', '/api/build', {'title': 'Spring catalogue', **values})
    assert status == 201, body
    return body['id']


def post(caller, record_id, what, body=None, model='build'):
    url = f'/api/{model}/{record_id}'
    return caller('POST', f'{url}/{what}', body or {}, headers={b'if-match': caller('GET', url)[2][b'etag']})


def status_of(app, record_id):
    with app.database.transaction() as tx:
        return tx.get(Build, RecordId(record_id)).status


def queue(app, record_id, user=User(1, 'admin', 'admin')):
    with app.business.context(user) as context:
        context.move(Build, record_id, 'queued')


# Declaring

def test_a_system_move_has_no_roles_and_a_move_without_roles_must_say_so():
    move = Move('queued', 'building', 'Start', system=True)
    assert move.system and move.roles == ()
    assert Move('new', 'queued').system is False and Move('new', 'queued').roles == ('admin', 'editor')
    with pytest.raises(ValueError, match='A system move has no roles'):
        Move('queued', 'building', system=True, roles=('admin',))
    with pytest.raises(ValueError, match=r'declare Move\(\.\.\., system=True\)'):
        Move('queued', 'building', roles=())
    with pytest.raises(ValueError, match='system must be True or False'):
        Move('queued', 'building', system='yes')


# Never offered, refused to every caller

def test_system_moves_are_never_offered(app):
    admin = client(app)
    record_id = create(admin)
    state = next(item for item in admin('GET', '/api/schema')[1]['models'] if item['name'] == 'build')['state']
    assert [move['to'] for move in state['moves']] == ['queued']
    queue(app, record_id)
    detail = admin('GET', f'/api/build/{record_id}')[1]
    assert status_of(app, record_id) == 'queued' and detail['_moves'] == []


def test_every_http_caller_is_refused_a_system_move(app):
    admin = client(app)
    record_id = create(admin)
    queue(app, record_id)
    # A signed in person, even an admin.
    status, body, _ = post(admin, record_id, 'moves/building')
    assert (status, body['error']) == (403, 'Start is made by the application only')
    # An API key.
    _, token = app.business.auth.create_key(1, 'Script', {'access': 'write', 'models': None})
    bearer = {b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''}
    etag = http(app, 'GET', f'/api/build/{record_id}', headers=bearer)[2][b'etag']
    status, body, _ = http(app, 'POST', f'/api/build/{record_id}/moves/building', {}, headers={**bearer, b'if-match': etag})
    assert (status, body['error']) == (403, 'Start is made by the application only')
    # An MCP client, the local connector and Eva reach the same routes in process.
    for access in (KeyAccess(True, None, None, 7), KeyAccess(True, None, None, 0), KeyAccess(True, None, None, None, 'eva')):
        request = Dispatch(app.business, None, User(1, 'admin', 'admin', access=access))
        _, headers = request('GET', f'/api/build/{record_id}')
        with pytest.raises(ConnectorError, match='made by the application only'):
            request('POST', f'/api/build/{record_id}/moves/building', {}, headers=[(b'if-match', headers[b'etag'])])
    assert status_of(app, record_id) == 'queued'


def test_a_role_that_cannot_read_the_state_learns_nothing(app):
    admin = client(app)
    record_id = create(admin)
    queue(app, record_id)
    viewer = client(app, 'viewer')
    status, body, _ = post(viewer, record_id, 'moves/building')
    assert (status, body['error']) == (409, 'This record has no such move now')


# Made by trusted Python

def test_trusted_python_makes_a_system_move_with_all_its_rules(app):
    admin = client(app)
    record_id = create(admin, ready=False)
    queue(app, record_id)
    with app.business.context(User(1, 'admin', 'admin')) as context:
        # The guard still decides, and so do update permission and the row.
        with pytest.raises(AccessError, match='condition for this move is not met'):
            context.move(Build, record_id, 'building')
        context.update(Build, record_id, ready=True)
        assert context.move(Build, record_id, 'building')['note'] == 'Started'
    # A user without update access to the record is refused, system move or not.
    with app.business.context(User(4, 'buyer', 'portal')) as context:
        with pytest.raises(AccessError):
            context.move(Build, record_id, 'done')
    assert status_of(app, record_id) == 'building'
    entries = admin('GET', f'/api/build/{record_id}/journal')[1]['items']
    assert entries[0]['kind'] == 'moved' and entries[0]['data']['to'] == 'building' and entries[0]['actor'] == 1


def test_an_action_makes_a_system_move_on_another_record(app):
    admin, editor = client(app), client(app, 'editor')
    record_id = create(admin)
    queue(app, record_id)
    status, starter, _ = editor('POST', '/api/starter', {'build_id': record_id})
    assert status == 201, starter
    status, body, _ = post(editor, starter['id'], 'actions/start_build', model='starter')
    assert status == 200, body
    assert status_of(app, record_id) == 'building'


# A job's business context

def test_a_job_acts_as_the_system_under_a_name_the_journal_records(app):
    admin = client(app)
    record_id = create(admin)
    with app.database.transaction() as tx:
        app.business.jobs.enqueue(tx, 'build', {'build': record_id})
    assert app.business.jobs.run_one()
    assert status_of(app, record_id) == 'done' and SEEN == [('Build worker', 'admin')]
    entries = admin('GET', f'/api/build/{record_id}/journal')[1]['items']
    moved = [entry for entry in entries if entry['kind'] == 'moved']
    assert [entry['data']['to'] for entry in moved] == ['done', 'building', 'queued']
    assert all(entry['actor'] is None and entry['via'] == 'Build worker' for entry in moved)
    with app.database._connection() as db:
        assert db.execute('SELECT evlet_modified_by FROM build').fetchone()[0] is None


def test_a_job_acts_as_its_user_or_a_stated_account_read_again(app):
    jobs = app.business.jobs
    with app.database._connection() as db:
        editor = db.execute("SELECT id FROM evlet_users WHERE username='editor'").fetchone()[0]
    with app.database.transaction() as tx:
        jobs.enqueue(tx, 'as_user', {}, user=User(editor, 'editor', 'editor'))
    assert jobs.run_one() and SEEN.pop() == User(editor, 'editor', 'editor')

    def stated(tx, job, payload):
        SEEN.append(job.business(tx, user=payload['user']).user)
    jobs.tasks['stated'] = Task('stated', stated, max_attempts=1)
    with app.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET role='viewer' WHERE id=?", (editor,))
        jobs.enqueue(tx, 'stated', {'user': editor})
    assert jobs.run_one() and SEEN.pop() == User(editor, 'editor', 'viewer')
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_users SET active=0 WHERE id=?', (editor,))
        receipt = jobs.enqueue(tx, 'stated', {'user': editor})
    assert jobs.run_one() and SEEN == []
    with app.database._connection() as db:
        row = db.execute('SELECT state, error FROM evlet_jobs WHERE id=?', (receipt.id,)).fetchone()
    assert tuple(row) == ('failed', 'The account this job acts as is not active')


def test_a_job_context_needs_an_explicit_identity(app):
    jobs, problems = app.business.jobs, []

    def check(tx, job, payload):
        for options in ({}, {'system': 'Import'}, {'system': 'Import', 'role': 'owner'}, {'role': 'admin'},
                        {'system': '', 'role': 'admin'}, {'user': 1, 'system': 'Import', 'role': 'admin'}, {'user': 'admin'}):
            try:
                job.business(tx, **options)
            except ValueError as exc:
                problems.append(str(exc))
    jobs.tasks['check'] = Task('check', check, max_attempts=1)
    with app.database.transaction() as tx:
        jobs.enqueue(tx, 'check', {})
    assert jobs.run_one()
    assert problems == [
        'This job was queued without a user. Pass user=account_id, or system="Name" with role=',
        'A system context needs role=, one of: admin, editor, viewer, portal',
        'A system context needs role=, one of: admin, editor, viewer, portal',
        'role= goes with system=; a user acts with its own role',
        'Name the system in 1 to 60 characters, such as system="Nightly import"',
        'Act as a user or as the system, not both',
        'Pass user= an account id or a User with one',
    ]


def test_a_system_context_has_only_its_roles_permissions(app):
    admin = client(app)
    record_id = create(admin)
    outcome = []

    def as_viewer(tx, job, payload):
        context = job.business(tx, system='Report robot', role='viewer')
        outcome.append(context.get(Build, record_id).title)
        try:
            context.update(Build, record_id, title='Changed')
        except AccessError as exc:
            outcome.append(exc.status)
    app.business.jobs.tasks['as_viewer'] = Task('as_viewer', as_viewer, max_attempts=1)
    with app.database.transaction() as tx:
        app.business.jobs.enqueue(tx, 'as_viewer', {})
    assert app.business.jobs.run_one()
    assert outcome == ['Spring catalogue', 403]


# Queued by an action, run once it commits

def test_an_action_queues_a_job_that_runs_once_the_action_commits(app):
    admin = client(app)
    record_id = create(admin)
    jobs = app.business.jobs
    jobs.wait(0)
    status, body, _ = post(admin, record_id, 'actions/build_now')
    assert status == 200, body
    assert body['_job']['state'] == 'queued' and body['note'] == 'Waiting for the worker'
    # The committed action woke the worker in this process, which runs the job as the system.
    assert jobs.wait(0) is True
    assert jobs.run_one() and status_of(app, record_id) == 'done'
    assert app.business.jobs.status(body['_job']['id'], User(1, 'admin', 'admin'))['state'] == 'succeeded'


def test_a_job_queued_by_a_failed_action_is_never_queued(app):
    admin = client(app)
    record_id = create(admin)
    jobs = app.business.jobs
    jobs.wait(0)
    status, body, _ = post(admin, record_id, 'actions/build_and_fail')
    assert status == 422, body
    assert jobs.wait(0) is False
    with app.database._connection() as db:
        assert db.execute('SELECT count(*) FROM evlet_jobs WHERE task=?', ('build',)).fetchone()[0] == 0
    assert status_of(app, record_id) == 'new'


def test_python_contexts_queue_after_commit_too(app):
    admin = client(app)
    record_id = create(admin)
    jobs = app.business.jobs
    jobs.wait(0)
    with pytest.raises(RuntimeError):
        with app.business.context(User(1, 'admin', 'admin')) as context:
            context.enqueue('build', {'build': record_id})
            raise RuntimeError('Stop')
    assert jobs.wait(0) is False and jobs.claim() is None
    with app.business.context(User(1, 'admin', 'admin')) as context:
        context.enqueue('build', {'build': record_id})
        assert jobs.wait(0) is False
    assert jobs.wait(0) is True and jobs.run_one()


# The Background Playground

def test_the_background_playground_builds_a_price_list_in_a_job(tmp_path):
    from examples.playground.background.app import STEPS, create_app
    app = create_app(tmp_path / 'background.sqlite3')
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    status, body, _ = admin('POST', '/api/pricelist', {'title': 'Winter prices', 'items': 40})
    assert status == 201, body
    record_id = body['id']
    assert admin('GET', f'/api/pricelist/{record_id}')[1]['_moves'] == []
    status, body, _ = post(admin, record_id, 'actions/build', model='pricelist')
    assert status == 200 and body['_job']['state'] == 'queued' and body['progress'] == 'Waiting for the worker'
    job_id = body['_job']['id']
    # People cannot start it themselves, even with a hand-written request.
    status, body, _ = post(admin, record_id, 'moves/building', model='pricelist')
    assert (status, body['error']) == (403, 'Start is made by the application only')
    jobs = app.business.jobs
    assert jobs.wait(0)
    while jobs.run_one():
        pass
    detail = admin('GET', f'/api/pricelist/{record_id}')[1]
    assert (detail['status'], detail['progress'], detail['requested']) == ('ready', 'Priced 40 items', False)
    assert [move['to'] for move in detail['_moves']] == ['draft']
    assert jobs.status(job_id, User(1, 'admin', 'admin'))['progress']['done'] == STEPS
    moved = [entry for entry in admin('GET', f'/api/pricelist/{record_id}/journal')[1]['items'] if entry['kind'] == 'moved']
    assert [(entry['data']['to'], entry['actor'], entry['via']) for entry in moved] == [
        ('ready', None, 'Price list builder'), ('building', None, 'Price list builder')]
