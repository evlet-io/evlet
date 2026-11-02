"""Lifecycle Playground: uvicorn examples.playground.lifecycle.app:create_app --factory --host 127.0.0.1.

This uses a separate demonstration database. It does not post accounting or issue legal invoices.
"""
from pathlib import Path

from evlet import (Application, Child, Currency, Database, Integer, LocalAuth, Model, Registry,
                   Rule, Table, Text, ValidationError, action, validate)


class Order(Model):
    confirmable = True
    track_changes = True
    number = Text(required=True, unique=True)
    customer = Text(required=True)
    remarks = Text(editable_when_locked=True)

    class Line(Child):
        description = Text(required=True)
        quantity = Integer(default=1, minimum=1)
        rate = Currency(default='0')
    lines = Table(Line, required=True)
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'confirm', 'cancel', 'reopen')),
                   'editor': Rule(('read', 'create', 'update', 'confirm', 'cancel', 'reopen', 'journal')),
                   'viewer': Rule(('read',))}

    @validate.confirm
    def positive_rates(self):
        if any(line.rate <= 0 for line in self.lines):
            raise ValidationError({'lines': 'Enter positive rates before confirmation'})

    @action.confirm
    def record_confirmation(self):
        # Applications can post their own records through self.context and register references with
        # self.context.reference_effect(TargetModel, id). The execution id is also an outbox job key.
        self.remarks = 'Confirmed revision ' + str(self.context.lifecycle_execution.revision)


def create_app(path=None):
    path = Path(path or Path(__file__).parent / '.data' / 'lifecycle.sqlite3')
    database = Database(path, Registry(Order))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    return Application(database, auth=auth, name='Document Lifecycle Playground')
