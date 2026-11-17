"""Prompts: forms an action or an @api function returns. They save nothing until submit() runs (docs/prompts.md)."""

from datetime import date

from evlet import Child, Currency, Date, Decimal, Link, Main, Outcome, Prompt, Table, Text, ValidationError, validate
from evlet.calc import F

from .models import Customer, Invoice


class PlanProject(Prompt):
    """What Plan project (an action on a planned project) opens: a start date, a rate and the tasks with their
    hours. The budget is calculated as people type; submit() saves it on the project and adds the tasks."""

    class Meta:
        title = 'Plan a project'
        primary = 'Plan project'
        layout = (Main(('starts_on', 'hourly_rate', 'hours', 'budget')), Main('tasks'))
        focus = 'starts_on'

    starts_on = Date(required=True)
    hourly_rate = Currency(currency='AED', minimum='0', default='350.00', required=True)

    class Task(Child):
        title = Text(required=True, max_length=160)
        hours = Decimal(scale=1, minimum='0', default='0')

    tasks = Table(Task, required=True)
    hours = Decimal(scale=1, compute=tasks.sum('hours'))
    budget = Currency(currency='AED', compute=F('hours') * F('hourly_rate'))

    def load(self, project):
        self.starts_on = project.starts_on or date.today()

    @validate
    def not_in_the_past(self):
        if self.starts_on is not None and self.starts_on < date.today():
            raise ValidationError({'starts_on': 'Pick a date from today on'})

    def submit(self):
        from .models import ProjectTask
        project = self.source
        project.starts_on, project.budget = self.starts_on, self.budget
        project.save()
        for task in self.tasks:
            self.context.session.create(ProjectTask, project=project.id, title=task.title)
        return Outcome(f'{len(self.tasks)} tasks planned', open=project)


class QuickInvoice(Prompt):
    """Opened from the sidebar (an @api shortcut). Submitting opens a new invoice filled from it, to review and create."""

    class Meta:
        title = 'Quick invoice'
        primary = 'Review invoice'

    customer = Link(Customer, required=True, filters={'active': True})
    reference = Text(required=True, max_length=40)
    hours = Decimal(scale=1, minimum='0.1', required=True)
    rate = Currency(currency='AED', minimum='0.01', default='350.00')
    total = Currency(currency='AED', compute=F('hours') * F('rate'))

    def submit(self):
        return Outcome(new=(Invoice, {'customer': self.customer, 'reference': self.reference,
                                      'posting_date': date.today(), 'total': self.total}))


def quick_invoice(context):
    """Open Quick invoice: an @api function that returns a prompt has no record."""
    return QuickInvoice
