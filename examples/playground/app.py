"""Run from the checkout with uvicorn examples.playground.app:create_app --factory."""

import os
from pathlib import Path

from evlet import Action, Application, ChildTable, Database, EventStore, JobQueue, LocalAuth, Task, ValidationError
from .models import Project, ProjectTask, registry
from .migrate import migrations


def start_project(tx, user, project):
    if project.status != 'planned':
        raise ValidationError({'_form': 'Only a planned project can be started'})
    tx.update(Project, project.id, status='active')


def complete_project(tx, user, project):
    if project.status != 'active':
        raise ValidationError({'_form': 'Start the project before completing it'})
    tasks = tx.list(ProjectTask, filters={'project': project.id}, limit=100)
    if not tasks or any(not task.done for task in tasks):
        raise ValidationError({'_form': 'Complete every task before completing the project'})
    tx.update(Project, project.id, status='done', progress=100)


def create_app():
    path = os.environ.get('EVLET_DATABASE', str(Path(__file__).parent / '.data' / 'playground.sqlite3'))
    database = Database(path, registry, migrations=migrations)
    auth = LocalAuth(database, origins=('http://127.0.0.1:5173', 'http://localhost:5173',
                                       'http://127.0.0.1:8000', 'http://localhost:8000',
                                       'http://127.0.0.1:4173', 'http://localhost:4173'),
                     secure_cookies=False, allow_setup=True)
    return build_app(database, auth)


def refresh_progress(tx, job, payload):
    project = tx.get(Project, payload['project_id'])
    tasks = tx.list(ProjectTask, filters={'project': project.id}, limit=100)
    progress = round(100 * sum(task.done for task in tasks) / len(tasks)) if tasks else 0
    tx.update(Project, project.id, progress=progress)


def build_app(database, auth, *, storage=None, tenant='local'):
    events = EventStore(database)
    jobs = JobQueue(database, (Task('project_progress', refresh_progress),), tenant=tenant, events=events)
    def queue_progress(tx, user, project):
        return jobs.enqueue(tx, 'project_progress', {'project_id': project.id}, user=user, target=(Project, project.id))
    return Application(database, auth=auth, storage=storage, jobs=jobs, events=events, children=(ChildTable(Project, 'tasks', ProjectTask, 'project', 'Tasks'),),
                       actions=(Action(Project, 'start', 'Start project', start_project),
                                Action(Project, 'complete', 'Complete project', complete_project),
                                Action(Project, 'refresh_progress', 'Recalculate progress', queue_progress)),
                       attachments=(Project,))
