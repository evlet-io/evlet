"""Run from the checkout with uvicorn examples.playground.app:create_app --factory."""

import os
from pathlib import Path

from evlet import Action, Application, ChildTable, Database, LocalAuth, ValidationError
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
    return Application(database, auth=auth, children=(ChildTable(Project, 'tasks', ProjectTask, 'project', 'Tasks'),),
                       actions=(Action(Project, 'start', 'Start project', start_project),
                                Action(Project, 'complete', 'Complete project', complete_project)),
                       attachments=(Project,))
