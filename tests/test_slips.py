import pytest

from evlet import (Application, Child, Currency, Database, Date, Decimal, Link, LocalAuth, Model, Outcome, Registry,
                   Rule, Select, Slip, Table, Text, ValidationError, action, present, validate)
from evlet.auth import password_hash
from test_business import PASSWORD, client, http, secured, hashed  # noqa: F401 (fixtures)


class Client(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'update')),
                   'viewer': Rule(('read',))}
    title = Text(required=True)

    @action('Plan job')
    def plan_job(self):
        return present(StartJob, source=self, values={'name': self.title + ' job', 'billing': 'time'})


class Job(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    class Meta:
        title_field = 'job_name'

    job_name = Text(required=True)
    client = Link(Client, required=True)
    starts_on = Date()
    budget = Currency(minimum='0')

    class Step(Child):
        title = Text(required=True)
        hours = Decimal(minimum='0')

    steps = Table(Step)


class StartJob(Slip):
    title = 'Start a job'
    hand_in_label = 'Start job'
    source = Client
    layout = (('Job', ('name', 'client', 'starts_on', 'billing', 'rate')), ('Steps', ('steps',)))
    name = Text(required=True, max_length=60)
    client = Link(Client, read_only=True)
    starts_on = Date(required=True)
    billing = Select(['fixed', 'time'], default='fixed')
    rate = Currency(visible_if="billing == 'time'", required_if="billing == 'time'")

    class Step(Child):
        title = Text(required=True)
        hours = Decimal(minimum='0', default='0')

    steps = Table(Step, required=True)
    total = Decimal(compute='sum(steps.hours)', stored=False)

    def load(self, source):
        self.client = source.id

    @validate

    def validate(self):
        if self.starts_on is not None and self.starts_on.year < 2000:
            raise ValidationError({'starts_on': 'Pick a date from 2000 on'})

    def hand_in(self):
        job = self.context.create(Job, job_name=self.name, client=self.client, starts_on=self.starts_on,
                                  children={'steps': [{'title': step.title, 'hours': step.hours} for step in self.steps]})
        return Outcome(f'Job {job["id"]} started', open=(Job, job['id']), data={'hours': str(self.total)})


class QuickJob(Slip):
    title = 'Quick job'
    menu = True
    becomes = Job
    name = Text(required=True)
    client = Link(Client, required=True)
    hours = Decimal(minimum='0', default='0')
    rate = Currency(default='100.00')

    class Step(Child):
        title = Text(required=True)
        hours = Decimal()

    steps = Table(Step)

    def document(self, source):
        return {'job_name': self.name, 'client': self.client, 'budget': self.hours * self.rate,
                'steps': [{'title': step.title, 'hours': step.hours} for step in self.steps]}


class DirectJob(Slip):
    title = 'Direct job'
    becomes = Job
    review = False
    job_name = Text(required=True)
    client = Link(Client, required=True)


@pytest.fixture
def app(tmp_path):
    db = Database(tmp_path / 'slips.sqlite3', Registry(Client, Job))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth, slips=(StartJob, QuickJob, DirectJob))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    hashed = password_hash(PASSWORD)
    with db.transaction() as tx:
        for role in ('editor', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password', (role, hashed, role))
    return app


def acme(c):
    status, record, _ = c('POST', '/api/client', {'title': 'Acme'})
    assert status == 201, record
    return record['id'], {b'if-match': c('GET', f'/api/client/{record["id"]}')[2][b'etag']}


def tables(app):
    with app.database._connection() as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_present_returns_the_neutral_schema_and_saves_nothing(app):
    c = client(app)
    client_id, _ = acme(c)
    status, surface, _ = c('POST', '/api/_slips/start_job/present', {'source': {'model': 'client', 'id': client_id}})
    assert status == 200, surface
    assert surface['slip'] == 'start_job' and surface['label'] == 'Start a job' and surface['hand_in_label'] == 'Start job'
    assert surface['source']['title'] == 'Acme' and surface['source']['revision']
    assert surface['outcome'] == {'kind': 'handler'}
    assert surface['layout'][0] == {'label': 'Job', 'fields': ['name', 'client', 'starts_on', 'billing', 'rate']}
    assert surface['values']['client'] == client_id and surface['values']['billing'] == 'fixed'
    assert surface['rules']['rate'] == {'visible': False, 'required': False}
    assert surface['definition']['children'][0]['allow_add'] is True
    assert surface['states']['name'] == 'required-input'
    assert not tables(app) & {'startjob', 'start_job', 'step'}
    assert app.database.count(Job) == 0


def test_check_runs_calculations_and_conditions(app):
    c = client(app)
    client_id, _ = acme(c)
    values = {'billing': 'time', 'children': {'steps': [{'_key': 'row-1', 'title': 'Survey', 'hours': '2.5'}, {'_key': 'row-2', 'title': 'Build', 'hours': '4'}]}}
    status, surface, _ = c('POST', '/api/_slips/start_job/check', {'source': {'model': 'client', 'id': client_id}, 'values': values, 'revision': 3})
    assert status == 200, surface
    assert surface['revision'] == 3
    assert surface['rules']['rate'] == {'visible': True, 'required': True}
    assert surface['states']['rate'] == 'required-input'
    assert surface['values']['total'] == '6.5000'
    assert [row['_key'] for row in surface['values']['children']['steps']] == ['row-1', 'row-2']
    status, surface, _ = c('POST', '/api/_slips/start_job/check', {'source': {'model': 'client', 'id': client_id},
                                                                  'values': {'children': {'steps': [{'_key': 'row-1', 'hours': '-1'}]}}})
    assert status == 200 and 'steps.row-1.hours' in surface['errors']


def test_hand_in_validates_on_the_server_and_keeps_nothing_on_errors(app):
    c = client(app)
    client_id, revision = acme(c)
    body = {'source': {'model': 'client', 'id': client_id}, 'key': 'hand-in-0001',
            'values': {'billing': 'time', 'starts_on': '1999-01-01', 'children': {'steps': [{'_key': 'a', 'hours': '1'}]}}}
    status, result, _ = c('POST', '/api/_slips/start_job/hand-in', body, headers=revision)
    assert status == 422, result
    assert {'name', 'rate', 'steps.a.title'} <= set(result['fields'])
    assert app.database.count(Job) == 0
    body['values'] = {'name': 'Fit-out', 'starts_on': '1999-01-01', 'children': {'steps': [{'title': 'Survey'}]}}
    status, result, _ = c('POST', '/api/_slips/start_job/hand-in', body, headers=revision)
    assert status == 422 and result['fields'] == {'starts_on': 'Pick a date from 2000 on'}


def test_hand_in_runs_the_handler_once_against_the_current_source(app):
    c = client(app)
    client_id, revision = acme(c)
    body = {'source': {'model': 'client', 'id': client_id}, 'key': 'hand-in-0002',
            'values': {'name': 'Fit-out', 'starts_on': '2026-11-16', 'children': {'steps': [{'title': 'Survey', 'hours': '2'}, {'title': 'Build', 'hours': '3.5'}]}}}
    assert c('POST', '/api/_slips/start_job/hand-in', body)[0] == 428
    status, outcome, _ = c('POST', '/api/_slips/start_job/hand-in', body, headers=revision)
    assert status == 200, outcome
    job_id = c('GET', '/api/job')[1]['items'][0]['id']
    assert outcome == {'message': f'Job {job_id} started', 'open': {'model': 'job', 'id': job_id}, 'data': {'hours': '5.5000'}}
    job = c('GET', f'/api/job/{job_id}')[1]
    assert job['client'] == client_id and [row['title'] for row in job['children']['steps']] == ['Survey', 'Build']
    # A retry with the same key returns the same outcome without running again.
    assert c('POST', '/api/_slips/start_job/hand-in', body, headers=revision)[:2] == (200, outcome)
    assert app.database.count(Job) == 1
    assert c('POST', '/api/_slips/start_job/hand-in', {**body, 'values': {**body['values'], 'name': 'Other'}}, headers=revision)[0] == 409
    # A slip filled against an older revision of its source is refused.
    assert c('PATCH', f'/api/client/{client_id}', {'title': 'Acme Trading'}, headers=revision)[0] == 200
    assert c('POST', '/api/_slips/start_job/hand-in', {**body, 'key': 'hand-in-0003'}, headers=revision)[0] in (409, 412)
    assert app.database.count(Job) == 1


def test_inputs_and_sources_are_checked(app):
    c = client(app)
    client_id, _ = acme(c)
    source = {'model': 'client', 'id': client_id}
    assert c('POST', '/api/_slips/start_job/present', {'source': source, 'values': {'client': 99}})[0] == 403
    assert c('POST', '/api/_slips/start_job/present', {'source': source, 'values': {'total': '9'}})[0] == 403
    assert c('POST', '/api/_slips/start_job/present', {'source': source, 'values': {'unknown': 1}})[0] == 422
    assert c('POST', '/api/_slips/start_job/present', {})[0] == 422
    assert c('POST', '/api/_slips/start_job/present', {'source': {'model': 'job', 'id': 1}})[0] == 422
    assert c('POST', '/api/_slips/start_job/present', {'source': {'model': 'client', 'id': 404}})[0] == 404
    assert c('POST', '/api/_slips/quick_job/present', {'source': source})[0] == 422
    assert c('POST', '/api/_slips/start_job/present', {'source': source, 'extra': 1})[0] == 422
    assert c('POST', '/api/_slips/missing/present', {})[0] == 404
    assert c('POST', '/api/_slips/start_job/open', {'source': source})[0] == 404
    # A Link must name a record the caller can read.
    status, result, _ = c('POST', '/api/_slips/quick_job/hand-in', {'key': 'missing-link', 'values': {'name': 'Kitchen', 'client': 77}})
    assert status in (403, 404, 422), result
    assert app.database.count(Job) == 0


def test_roles_and_target_permissions_decide_who_may_present(app):
    admin = client(app)
    client_id, revision = acme(admin)
    source = {'model': 'client', 'id': client_id}
    viewer, editor = client(app, 'viewer'), client(app, 'editor')
    assert viewer('POST', '/api/_slips/start_job/present', {'source': source})[0] == 403
    assert editor('POST', '/api/_slips/start_job/present', {'source': source})[0] == 200
    # The editor cannot create jobs, so slips that become a job are refused and not listed.
    assert editor('POST', '/api/_slips/quick_job/present', {})[0] == 403
    schema = {role: c('GET', '/api/schema')[1] for role, c in (('admin', admin), ('editor', editor), ('viewer', viewer))}
    entries = {role: next(model for model in value['models'] if model['name'] == 'client') for role, value in schema.items()}
    assert entries['admin']['slips'] == [{'name': 'start_job', 'label': 'Start a job', 'hand_in_label': 'Start job'}]
    assert entries['editor']['slips'] == entries['admin']['slips'] and 'slips' not in entries['viewer']
    assert schema['admin']['slips'] == [{'name': 'quick_job', 'label': 'Quick job', 'hand_in_label': 'Quick job'}]
    assert schema['editor']['slips'] == [] and schema['viewer']['slips'] == []
    # The handler writes with the caller's permissions: an editor's hand-in cannot create a job.
    body = {'source': source, 'key': 'editor-hand-in', 'values': {'name': 'Fit-out', 'starts_on': '2026-11-16', 'children': {'steps': [{'title': 'Survey'}]}}}
    assert editor('POST', '/api/_slips/start_job/hand-in', body, headers=revision)[0] == 403
    assert app.database.count(Job) == 0


def test_becomes_review_returns_values_and_creates_nothing(app):
    c = client(app)
    client_id, _ = acme(c)
    status, surface, _ = c('POST', '/api/_slips/quick_job/present', {})
    assert status == 200 and surface['outcome'] == {'kind': 'document', 'model': 'job', 'review': True}
    assert surface['source'] is None
    body = {'key': 'review-0001', 'values': {'name': 'Kitchen', 'client': client_id, 'hours': '3', 'children': {'steps': [{'title': 'Measure', 'hours': '1'}]}}}
    status, outcome, _ = c('POST', '/api/_slips/quick_job/hand-in', body)
    assert status == 200, outcome
    assert outcome == {'document': {'model': 'job', 'review': True, 'values': {
        'job_name': 'Kitchen', 'client': client_id, 'budget': '300.00', 'children': {'steps': [{'title': 'Measure', 'hours': '1.0000'}]}}}}
    assert app.database.count(Job) == 0


def test_becomes_direct_creates_through_the_normal_create_path(app):
    c = client(app)
    client_id, _ = acme(c)
    status, outcome, _ = c('POST', '/api/_slips/direct_job/hand-in', {'key': 'direct-0001', 'values': {'job_name': 'Garage', 'client': client_id}})
    assert status == 200, outcome
    job_id = c('GET', '/api/job')[1]['items'][0]['id']
    assert outcome == {'document': {'model': 'job', 'id': job_id}, 'open': {'model': 'job', 'id': job_id}}
    assert c('GET', f'/api/job/{job_id}')[1]['job_name'] == 'Garage'
    status, result, _ = c('POST', '/api/_slips/direct_job/hand-in', {'key': 'direct-0002', 'values': {'job_name': 'Shed', 'client': 999}})
    assert status in (403, 404, 422) and app.database.count(Job) == 1


def test_an_action_can_present_a_slip(app):
    c = client(app)
    client_id, revision = acme(c)
    status, result, _ = c('POST', f'/api/client/{client_id}/actions/plan_job', {}, headers=revision)
    assert status == 200, result
    assert result['_outcome'] == {'present': {'slip': 'start_job', 'source': {'model': 'client', 'id': client_id},
                                              'values': {'name': 'Acme job', 'billing': 'time'}}}
    status, surface, _ = c('POST', '/api/_slips/start_job/present', {'source': result['_outcome']['present']['source'],
                                                                    'values': result['_outcome']['present']['values']})
    assert status == 200 and surface['values']['name'] == 'Acme job' and surface['rules']['rate']['visible'] is True


def test_definitions_are_checked_when_registered(tmp_path):
    db = Database(tmp_path / 'checks.sqlite3', Registry(Client, Job))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)

    def register(**attributes):
        return Application(db, auth=auth, slips=(type('Probe', (Slip,), {'name': Text(), **attributes}),))

    with pytest.raises(ValueError, match='define hand_in'):
        register()
    with pytest.raises(ValueError, match='choose one outcome'):
        register(becomes=Job, hand_in=lambda self: None)
    with pytest.raises(ValueError, match='not as a menu slip'):
        register(source=Client, menu=True, becomes=Job)
    with pytest.raises(ValueError, match='layout'):
        register(becomes=Job, layout=(('Main', ('missing',)),))
    with pytest.raises(ValueError, match='roles'):
        register(becomes=Job, roles=('owner',))
    with pytest.raises(ValueError, match='Duplicate'):
        Application(db, auth=auth, slips=(QuickJob, QuickJob))
    with pytest.raises(ValueError, match='present'):
        present(Client)
    with pytest.raises(ValueError, match='no field'):
        present(StartJob, values={'missing': 1})
    with pytest.raises(ValueError, match='authentication'):
        Application(db, slips=(QuickJob,))


def test_playground_slips_estimate_then_plan_a_project_and_review_an_invoice(secured):
    from examples.playground.models import Invoice, Project
    c = client(secured)
    status, customer, _ = c('POST', '/api/customer', {'customer_name': 'Acme', 'active': True})
    assert status == 201
    customer = customer['id']
    revision = {b'if-match': c('GET', f'/api/customer/{customer}')[2][b'etag']}
    tasks = {'tasks': [{'title': 'Survey', 'hours': '2'}, {'title': 'Build', 'hours': '3.5'}]}
    status, outcome, _ = c('POST', '/api/_slips/estimate_project/hand-in', {'key': 'estimate-0001', 'values': {'customer': customer, 'hourly_rate': '100', 'children': tasks}})
    assert status == 200, outcome
    assert outcome['message'] == 'Estimate: AED 550.00 for 5.5 hours' and outcome['data'] == {'estimate': '550.00'}
    following = outcome['present']
    assert following['slip'] == 'plan_project' and following['source'] == {'model': 'customer', 'id': customer}
    status, surface, _ = c('POST', '/api/_slips/plan_project/present', {'source': following['source'], 'values': following['values']})
    assert status == 200 and surface['values']['name'] == 'Acme project' and surface['values']['total_hours'] == '5.5'
    assert surface['rules']['budget'] == {'visible': False}
    body = {'source': following['source'], 'key': 'plan-0001', 'values': {**following['values'], 'starts_on': '2099-01-01'}}
    status, outcome, _ = c('POST', '/api/_slips/plan_project/hand-in', body, headers=revision)
    project_id = c('GET', '/api/project')[1]['items'][0]['id']
    assert status == 200 and outcome == {'document': {'model': 'project', 'id': project_id}, 'open': {'model': 'project', 'id': project_id}}, outcome
    project = c('GET', f'/api/project/{project_id}')[1]
    assert project['budget'] == '550.00' and [task['title'] for task in project['children']['tasks']] == ['Survey', 'Build']
    status, outcome, _ = c('POST', '/api/_slips/quick_invoice/hand-in', {'key': 'invoice-0001', 'values': {'customer': customer, 'reference': 'INV-1', 'hours': '2'}})
    assert status == 200 and outcome['document']['values']['total'] == '700.00'
    assert secured.database.count(Invoice) == 0 and secured.database.count(Project) == 1
    # A viewer cannot create projects or invoices: no slip is offered and the routes refuse.
    viewer = client(secured, 'viewer')
    schema = viewer('GET', '/api/schema')[1]
    assert schema['slips'] == [] and 'slips' not in next(model for model in schema['models'] if model['name'] == 'customer')
    assert viewer('POST', '/api/_slips/plan_project/present', {'source': {'model': 'customer', 'id': customer}})[0] == 403


def test_slips_reports_and_dialogs_refuse_finite_expressions_until_they_have_plans():
    # Slips, Report filter forms and Dialogs still run the string rules. A finite expression on them used to be
    # ignored (applies_when) or fail when the form was checked (compute=F(...)), so they are refused up front.
    from evlet import Dialog, Report
    from evlet.calc import F

    for base in (Slip, Report, Dialog):
        for field in (Text(applies_when=F('kind') == 'y'), Text(required_when=F('kind') == 'y'),
                      Text(locked_when=F('kind') == 'y'), Text(initial=F('kind')), Text(compute=F('kind'))):
            with pytest.raises(ValueError, match=r"Probe\.note: .*visible_if.*required_if.*read_only_if.*compute='\.\.\.'"):
                type('Probe', (base,), {'kind': Text(), 'note': field})

    # A finite expression on a row of the form's table is refused too, naming the row field.
    class Row(Child):
        hours = Decimal()
        doubled = Decimal(compute=F('hours') * 2, stored=False)
    with pytest.raises(ValueError, match=r'Probe\.rows\.doubled: '):
        type('Probe', (Slip,), {'rows': Table(Row)})

    # The string rules keep working.
    type('Probe', (Slip,), {'kind': Text(), 'note': Text(visible_if="kind == 'y'", required_if="kind == 'y'"),
                            'shown': Text(compute='kind', stored=False)})
