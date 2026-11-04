"""Slips: forms presented from code that save nothing until they are handed in."""

from datetime import date

from evlet import Child, Currency, Date, Decimal, Link, Outcome, Select, Slip, Table, Text, ValidationError, present

from .models import Customer, Invoice, Project


class Task(Child):
    title = Text(required=True, max_length=160)
    hours = Decimal(scale=1, minimum='0', default='0')


class PlanProject(Slip):
    """A button on each customer. Handing it in creates the project and its tasks through the normal
    create path, with the person's permissions."""
    title = 'Plan a project'
    hand_in_label = 'Create project'
    source = Customer
    becomes = Project
    review = False
    layout = (('Project', ('name', 'customer', 'starts_on', 'billing', 'hourly_rate', 'budget')), ('Tasks', ('tasks',)))

    name = Text(required=True, max_length=120)
    customer = Link(Customer, read_only=True)
    starts_on = Date(required=True)
    billing = Select(['fixed', 'time'], default='fixed')
    hourly_rate = Currency(minimum='0', visible_if="billing == 'time'", required_if="billing == 'time'")
    budget = Currency(minimum='0', default='0.00', visible_if="billing == 'fixed'")
    tasks = Table(Task, required=True)
    total_hours = Decimal(scale=1, compute='sum(tasks.hours)', stored=False)

    def load(self, source):
        self.customer = source.id
        self.name = f'{source.customer_name} project'

    def validate(self):
        if self.starts_on is not None and self.starts_on < date.today():
            raise ValidationError({'starts_on': 'Pick a date from today on'})

    def document(self, source):
        budget = (self.total_hours or 0) * self.hourly_rate if self.billing == 'time' else self.budget
        return {'title': self.name, 'customer': self.customer, 'starts_on': self.starts_on, 'budget': budget,
                'tasks': [{'title': task.title} for task in self.tasks]}


class EstimateProject(Slip):
    """In the New menu. Its handler only calculates; it saves nothing and offers the next slip."""
    title = 'Estimate a project'
    hand_in_label = 'Estimate'
    menu = True

    customer = Link(Customer, required=True, filters={'active': True})
    hourly_rate = Currency(minimum='0.01', default='350.00', required=True)
    tasks = Table(Task, required=True)
    total_hours = Decimal(scale=1, compute='sum(tasks.hours)', stored=False)

    def hand_in(self):
        estimate = round((self.total_hours or 0) * self.hourly_rate, 2)
        return Outcome(f'Estimate: AED {estimate:,.2f} for {self.total_hours} hours', data={'estimate': str(estimate)},
                       present=present(PlanProject, source=(Customer, self.customer), values={
                           'billing': 'time', 'hourly_rate': self.hourly_rate,
                           'tasks': [{'title': task.title, 'hours': task.hours} for task in self.tasks]}))


class QuickInvoice(Slip):
    """In the New menu. Handing it in opens a new invoice filled from it, to review and create."""
    title = 'Quick invoice'
    hand_in_label = 'Review invoice'
    menu = True
    becomes = Invoice

    customer = Link(Customer, required=True, filters={'active': True})
    reference = Text(required=True, max_length=40)
    hours = Decimal(scale=1, minimum='0.1', required=True)
    rate = Currency(minimum='0.01', default='350.00')
    posting_date = Date(default=date.today)

    def document(self, source):
        return {'customer': self.customer, 'reference': self.reference, 'posting_date': self.posting_date,
                'total': self.hours * self.rate}


SLIPS = (PlanProject, EstimateProject, QuickInvoice)
