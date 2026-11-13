"""Lifecycle Playground: uvicorn examples.playground.lifecycle.app:create_app --factory --host 127.0.0.1.

An order confirmed as a release (v0, v1, ...), reopened with a remark and cancelled as a final state, on the
state manager (docs/states-actions.md). This uses a separate demonstration database. It does not post
accounting or issue legal invoices.
"""
from pathlib import Path

from evlet import (Application, Child, Currency, Database, Integer, LocalAuth, Model, Registry,
                   Rule, State, Table, Text, ValidationError, state, validate)


class Order(Model):
    number = Text(required=True, unique=True)
    customer = Text(required=True)
    remarks = Text(versioned=False)
    status = State(
        State.Step('draft', 'Draft'),
        State.Step('confirmed', 'Confirmed', color='green', lock=True, release=True),
        State.Step('cancelled', 'Cancelled', color='rose', lock=True, final=True),
        rules=(
            State.Rule('draft', 'confirmed', 'Confirm', primary=True),
            State.Rule('confirmed', 'draft', 'Reopen', remark='required'),
            State.Rule(('draft', 'confirmed'), 'cancelled', 'Cancel', remark='required'),
        ), show='bar')

    class Line(Child):
        description = Text(required=True)
        quantity = Integer(default=1, minimum=1)
        rate = Currency(default='0')
    lines = Table(Line, required=True)
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read', 'create', 'update', 'journal')),
                   'viewer': Rule(('read',))}

    @validate
    def positive_rates(self):
        if self.status == 'confirmed' and any(line.rate <= 0 for line in self.lines):
            raise ValidationError({'lines': 'Enter positive rates before confirmation'})

    @state('confirmed')
    def record_confirmation(self):
        # Applications post their own records through self.context in the move's transaction.
        self.remarks = 'Confirmed by ' + (self.context.user.username if self.context.user else 'the engine')


def create_app(path=None):
    path = Path(path or Path(__file__).parent / '.data' / 'lifecycle.sqlite3')
    database = Database(path, Registry(Order))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    return Application(database, auth=auth, name='Document Lifecycle Playground')
