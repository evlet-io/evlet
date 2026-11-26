"""Prompts: forms an action or an @api function returns. They save nothing until submit() runs (docs/prompts.md)."""

from datetime import date

from evlet import Child, Currency, Date, Decimal, Js, Link, Main, Outcome, Percent, Prompt, Select, Table, Text, ValidationError, validate
from evlet.calc import F

from .items import UNITS
from .models import Customer, Invoice, Item


class PlanProject(Prompt):
    """What Plan project (an action on a planned project) opens: a start date, a rate and the tasks with their
    hours. The budget is calculated as people type; submit() saves it on the project and adds the tasks."""

    class Meta:
        title = 'Plan a project'
        primary = 'Plan project'
        layout = (Main(('starts_on', 'hourly_rate', 'hours', 'budget')), Main('tasks'))
        focus = 'starts_on'
        # A model script works in a prompt as on a record (docs/model-scripts.md): it marks a long task here.
        script = 'plan_project.js'

    starts_on = Date(required=True)
    hourly_rate = Currency(currency='AED', minimum='0', default='350.00', required=True)

    class Task(Child):
        title = Text(required=True, max_length=160)
        hours = Decimal(scale=1, minimum='0', default='0', look=Js('hours_look'), tooltip=Js('hours_tip'))

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

    class Line(Child):
        item = Link(Item, required=True)
        uom = Select(UNITS, required=True, default='nos', label='Unit')
        qty = Decimal(scale=2, minimum='0.01', default='1', label='Quantity')
        # Help text that names a field reads under the rate in each row ("per Box"), filled in as the unit changes.
        # {parent.reference} would name a field of the quick invoice itself.
        rate = Currency(currency='AED', minimum='0', default='0.00', help_text='per {uom}')
        amount = Currency(currency='AED', compute=F('qty') * F('rate'))

    # totals= puts these fields under the lines, in this order: the discount is typed in its line and the
    # lines below it follow at once. The last line reads as the grand total.
    lines = Table(Line, required=True, totals=('total', 'discount_percent', 'discount', 'grand_total'))
    # The same help text template works on a form field: it follows the customer picked above.
    total = Currency(currency='AED', compute=lines.sum('amount'), label='Net total', help_text='Invoiced to {customer}')
    discount_percent = Percent(default='0', label='Discount %')
    discount = Currency(currency='AED', compute=F('total') * F('discount_percent') / 100)
    grand_total = Currency(currency='AED', compute=F('total') - F('discount'), label='Grand total')

    def submit(self):
        return Outcome(new=(Invoice, {'customer': self.customer, 'reference': self.reference,
                                      'posting_date': date.today(), 'total': self.grand_total}))


def quick_invoice(context):
    """Open Quick invoice: an @api function that returns a prompt has no record."""
    return QuickInvoice


class ClosePeriod(Prompt):
    """Close invoices up to a date (Meta.period on Invoice) from trusted code: evlet.periods.set_lock writes the same
    lock and audit row as Controls, Periods. Moving the lock earlier, or opening it, is for an admin."""

    class Meta:
        title = 'Close invoices'
        primary = 'Close'

    up_to = Date(required=True, label='Close up to')
    reason = Text(required=True, max_length=200)

    def load(self, source):
        from evlet import periods
        current = periods.lock_date(self.context, Invoice)
        self.up_to = current

    def submit(self):
        from evlet import periods
        periods.set_lock(self.context, Invoice, None, self.up_to, self.reason)
        return Outcome(f'Invoices are closed up to {self.up_to:%d-%m-%Y}')


def close_period(context):
    """Open Close invoices: a prompt that sets the invoice period lock."""
    return ClosePeriod
