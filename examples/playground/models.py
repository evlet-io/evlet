"""A small consumer of Evlet's public API, not an accounting system."""

from datetime import date

from evlet import (Boolean, Chart, Child, Count, Currency, Date, DateTime, Decimal, Hierarchy, Html, Integer, Link, LongText, Main, Model, Naming,
                   Ref, Registry, Related, RichText, Rule, Section, Select, Series, Sum, Tab, Table, Text, ValidationError, action)
from evlet.calc import F

# Currencies the Playground records may use. A Currency field only shows the code; it never converts.
# KWD has 3 decimal places (ISO 4217); XCR is a client's own credit unit the demo gives 5 in Controls, Currencies.
CURRENCIES = ['AED', 'SAR', 'USD', 'EUR', 'INR', 'KWD', 'XCR']


class Customer(Model):
    class Meta:
        description = "Good work starts with good relationships."
        # The record title (Link pickers, lists, search) is the customer's name.
        title_field = 'customer_name'
        list_fields = ('customer_name', 'email', 'active')
        search_fields = ('customer_name', 'email')
        # flow='columns': down the left column (name, email, active), then the right (currency); Tab goes the
        # same way, and notes, full width, sits below both columns.
        layout = (Section('Details', ('customer_name', 'email', 'active', 'currency', 'notes'), collapsible=False, flow='columns'),)

    customer_name = Text(required=True, max_length=120, label='Customer name')
    email = Text(max_length=254)
    active = Boolean(required=True, default=True)
    currency = Select(CURRENCIES, required=True, default='AED', help_text='Invoices for this customer show amounts in it.')
    notes = LongText()


class Invoice(Model):
    class Meta:
        description = "Keep each piece of work connected to a customer."
        list_fields = ('name', 'reference', 'customer', 'total', 'payment_status', 'status', 'posting_date')
        search_fields = ('name', 'reference', 'status')
        # The server names each new invoice INV-2026-00001, INV-2026-00002, ... in its create transaction:
        # the standard name, shown as the document number. The year comes from the posting date, or the
        # creation day without one. Controls, Naming can add series and set the counter. With no title
        # source the title is the name.
        # label names the standard name field for invoices on the form, list columns, filters and search.
        naming = Naming(Series('INV-{YYYY}-{#####}', label='Invoices'), date_field='posting_date', label='Invoice number')
        # The list opens an invoice in a side panel beside it. People can switch back to the full page.
        open_in = 'panel'
        # The posting date places an invoice in a period. Once Controls, Periods locks that period, nobody
        # adds, changes or deletes invoices dated in it, app code included.
        period = 'posting_date'
        # The header shows the payment status as a badge; it is calculated, so nobody edits it.
        status = 'payment_status'
        # A new invoice's form starts with the cursor in Customer, the field people fill in first.
        focus = 'customer'

    customer = Link(Customer, required=True)
    # Only projects of the invoice's customer. Changing the customer clears a project of another one,
    # with a note under the field; the server names the mismatch if one is saved anyway.
    project = Link('project', filters={'customer': Ref('customer')}, help_text="One of this customer's projects.")
    # help_text reads under the input; tooltip sits behind a ? icon at its right end.
    reference = Text(required=True, unique=True, max_length=40, label='Customer reference',
                     help_text="The customer's reference for this invoice.",
                     tooltip="Use the customer's purchase order number if they ask for one.",
                     # copy=False: Duplicate leaves the reference empty, since it is unique per invoice.
                     copy=False)
    # The currency comes from the customer the invoice is for.
    total = Currency(required=True, minimum="0.01", currency='customer.currency')
    posting_date = Date()
    status = Select(["draft", "sent"], required=True, default="draft")
    # suffix='%' puts a % box after the input, as Percent fields have.
    # panel=False: the list's side panel leaves the tax rate out; the full page shows it.
    tax_rate = Decimal(scale=4, minimum="0", maximum="100", default="0.0000", label="Tax rate", suffix="%", help_text="Recorded separately; this does not calculate the total.",
                       panel=False)
    amount_paid = Currency(minimum="0", default="0.00", currency='customer.currency', label='Amount paid')
    # Calculated from the amount paid against the total, never set by hand. colors picks each badge colour.
    payment_status = Select(['unpaid', 'partly_paid', 'paid'], label='Payment status',
                            colors={'unpaid': 'rose', 'partly_paid': 'amber', 'paid': 'green'},
                            compute="'paid' if amount_paid >= total else ('partly_paid' if amount_paid > 0 else 'unpaid')")


class Payment(Model):
    class Meta:
        description = "Record payments and the invoices they belong to."
        # A payment is entered in one go, so leaving one with unsaved edits asks to discard them
        # instead of keeping them for later (docs/ui.md, Unsaved changes).
        autosave = False

    invoice = Link(Invoice, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    # The currency is a field on the payment itself.
    currency = Select(CURRENCIES, required=True, default='AED')
    amount = Currency(required=True, minimum="0.01", currency='currency')
    received_at = DateTime()


class LedgerEntry(Model):
    class Meta:
        description = "What posted invoices owe. Only Post to ledger on an invoice writes entries."
        label = 'Ledger entry'
        plural = 'Ledger entries'
        list_fields = ('posting_date', 'customer', 'invoice', 'amount')
        # Every role reads entries and none writes them, admin included: the Post to ledger action
        # (app.py) is trusted Python and the only way in. Closed periods apply to entries too.
        api = 'read'
        period = 'posting_date'

    invoice = Link(Invoice, required=True, index=True)
    customer = Link(Customer, required=True)
    posting_date = Date(required=True)
    amount = Currency(required=True, currency='customer.currency')
    memo = Text(max_length=120)


class Project(Model):
    class Meta:
        description = "Track a small project and its customer."
        list_fields = ('title', 'customer', 'status', 'budget')
        # A compact record page: Main is the primary area, with no heading; every Section after it is
        # "more information", which a person can open and close. Budget starts open, Schedule starts
        # closed and Notes starts closed when it is empty. A layout with Main has no record tabs.
        layout = (Main(('title', 'customer', 'status')),
                  Section('Budget', ('budget', 'progress'), description='What the project may spend and how far the work has come.'),
                  Section('Schedule', ('starts_on',), collapsed=True, description='When the work starts.'),
                  Section('Notes', ('brief', 'notes'), collapsed='empty', description='Goals, scope and anything else worth keeping.'))
        # Cards at the top of the record page, up to five: derived values only. Here they are per-record
        # measures (below): what the project's invoices total and have been paid, and its tasks. A field
        # with compute=, fetch= or query= can be a card too.
        cards = ('invoiced', 'paid', 'tasks', 'open_tasks')
        # The full page's side column: related tasks first, then the record's times, then key fields.
        side_blocks = ('related', 'info', 'key_fields')
        open_in = 'panel'
        # What the list's side panel shows, in this order: the Main fields (named by the model's label,
        # Project) and Budget, then the Related tab and Record info. Schedule, Notes, Activity and the
        # summary stay on the full page, one click away (Open full page). Controls, Models, Form layout
        # can change it per instance.
        panel = ('Project', 'Budget', 'related', 'info')
        # The list's tabs: All, Planned, Active and Done, with counts. It opens on Active.
        list_tabs = 'status'
        list_tab = 'active'
        # Totals over the listed projects. trend= lets the browser chart the budget by start month (the last
        # 6 months), read from GET /api/project/aggregate with the list's filters; color= picks its colour.
        # Per record (model= and link=): the project's invoices, its tasks and those not done yet.
        measures = {'budget': Sum('budget', trend='starts_on', color='teal'), 'projects': Count(),
                    'invoiced': Sum('total', model='invoice', link='project'),
                    'paid': Sum('amount_paid', model='invoice', link='project'),
                    'tasks': Count('projecttask', link='project'),
                    'open_tasks': Count('projecttask', link='project', where=~F('done'))}

    # A declared title is the record title itself.
    title = Text(required=True, max_length=120, label='Project')
    customer = Link(Customer)
    status = Select(['planned', 'active', 'done'], required=True, default='planned', read_only=True)
    # The currency is the Projects app setting "Project currency" (Settings, Projects).
    budget = Currency(minimum='0', default='0.00', currency='settings.projects.currency')
    progress = Integer(minimum=0, maximum=100, default=0, label='Progress', suffix='%')
    starts_on = Date()
    # Formatted text: headings, lists, links (evlet/richtext.py sanitises every write).
    brief = RichText(label='Brief', help_text='Goals, scope and links, with formatting.')
    notes = LongText()


class ProjectTask(Model):
    class Meta:
        label = 'Project task'
        plural = 'Project tasks'
        list_fields = ('title', 'done')
        # A composite index for "open tasks of a project" (docs/model-engine.md#composite-and-unique-indexes);
        # (fields, {"unique": True}) would make the pair unique.
        indexes = [('project', 'done')]

    project = Link(Project, required=True, index=True)
    title = Text(required=True, max_length=160)
    done = Boolean(required=True, default=False)


# Who reports to whom. A viewer linked to an employee (LocalAuth.link_user) reads that employee and
# everyone below them, at any depth; editors and admins (HR) read everyone. See seed_team in team.py.
TEAM = Hierarchy('employee', parent='reports_to')


class Skill(Child):
    skill = Text(required=True, max_length=80)
    level = Select(['learning', 'practised', 'expert'], required=True, default='practised')


class SkillLevel(Child):
    """One row per level used in an employee's skills, built by Employee.derive on every save."""
    level = Select(['learning', 'practised', 'expert'])
    skills = Integer(default=0)


DEPARTMENTS = ['sales', 'finance', 'operations', 'people']


class Employee(Model):
    class Meta:
        description = "People and who they report to."
        title_field = 'full_name'
        list_fields = ('full_name', 'email', 'department', 'reports_to')
        search_fields = ('full_name', 'email')
        # The record page, declared block by block (docs/ui.md, Record layouts): two tabs instead of Details,
        # the Skills table inside the Profile tab, text from the app, charts over the measure below, and two
        # lists of direct reports, one narrowed to this employee's department. Listing related records here
        # turns the automatic Related tab and side block off for employees.
        layout = (
            Tab('Profile', (
                Section('Employee', ('full_name', 'email', 'department', 'reports_to', 'skill_count'), collapsible=False),
                Html('<p>Skills of <strong>{full_name}</strong> are kept with the record. Their team is on the Team tab.</p>'),
                'skills',
                'skill_levels',
            )),
            Tab('Team', (
                Chart('stat', y='people', model='employee', link='reports_to', label='Direct reports'),
                Chart('bar', x='department', y='people', model='employee', link='reports_to', label='Direct reports by department'),
                Related('employee', 'reports_to', label='Direct reports', fields=('full_name', 'email', 'department'), order='full_name'),
                Related('employee', 'reports_to', label='Reports in the same department', filters={'department': Ref('department')},
                        fields=('full_name', 'email')),
            )),
        )
        # A Tab of the layout can be where records open, as any record tab can.
        default_tab = 'profile'
        # People counted by the charts above; the list shows the count too.
        measures = {'people': Count()}
        # derive() below sets these on every save and preview; nobody writes them, and the Skill levels
        # table is read only on the form (docs/model-engine.md#derived-values).
        derives = ('skill_count', 'skill_levels')

    # Moving someone changes every team above them, so each save keeps who changed reports_to and when.
    track_changes = True
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update', 'delete')),
                   'viewer': Rule(('read',), scope=TEAM.team())}
    full_name = Text(required=True, max_length=120, label='Full name')
    email = Text(max_length=254)
    department = Select(DEPARTMENTS)
    reports_to = Link('employee', index=True, label='Reports to')
    # Section rows group skills under headings such as "Languages" or "Tools" (docs/model-engine.md#section-rows).
    # They are stored apart from the rows, so turning this on needed no migration, and derive() below still
    # counts real skills only.
    skills = Table(Skill, sections=True)
    skill_count = Integer(label='Skills')
    skill_levels = Table(SkillLevel, label='Skill levels')

    def derive(self):
        """How many skills, and one row per level they use: values a formula cannot build."""
        counts = {}
        for skill in self.skills:
            counts[skill.level] = counts.get(skill.level, 0) + 1
        self.skill_count = len(self.skills)
        self.skill_levels = [{'level': level, 'skills': counts[level]} for level in ('learning', 'practised', 'expert') if level in counts]

    def before_close(self, to):
        """Refuse to delete someone while people still report to them, naming who (to is None for a delete).

        It reads through self.context.session, read only, inside the delete's own transaction."""
        # self.model is the model class (Employee) on the draft a hook gets.
        reports = self.context.session.list(self.model, filters={'reports_to': self.id}, order_by=('full_name',), limit=6)
        if reports:
            names = ', '.join(report.full_name for report in reports[:5]) + (' and others' if len(reports) > 5 else '')
            raise ValidationError({'_form': f'Move their reports to another manager first: {names}'})

    @action('Recognise', inputs={'points': Integer(required=True, minimum=1, maximum=50),
                                 'reason': Text(required=True, max_length=120)})
    def recognise(self, points, reason):
        """Give recognition points: one row in the Recognition ledger, at most RECOGNITION_LIMIT a month each.

        self.context.session is this action's own transaction, open only while the action runs. The lock
        keeps two awards at once from both reading the same monthly total, the total counts rows written
        earlier in this transaction, and a refusal rolls back everything the action wrote."""
        tx = self.context.session
        tx.lock(('recognition', self.id))
        today = date.today()
        given = tx.aggregate(Recognition, measures={'points': Sum('points')},
                             filters={'employee': self.id, 'on_date__gte': today.replace(day=1)})
        so_far = (given[0]['points'] if given else None) or 0
        if so_far + points > RECOGNITION_LIMIT:
            raise ValidationError({'points': f'Only {RECOGNITION_LIMIT - so_far} points are left this month'})
        tx.insert_many(Recognition, [{'employee': self.id, 'on_date': today, 'reason': reason, 'points': points}])
        return {'points_this_month': so_far + points}


# Recognition points one employee may get in a calendar month.
RECOGNITION_LIMIT = 100


class Recognition(Model):
    """Recognition points, one row per award. A ledger model: only the Recognise action on an employee
    writes it, through its own transaction session, and the database refuses to change a row once written."""
    class Meta:
        ledger = True
        append_only = True
        timestamps = 'created'
        indexes = [('employee', 'on_date')]

    employee = Link(Employee, required=True)
    on_date = Date(required=True)
    reason = Text(default='', max_length=120)
    points = Integer(required=True)


class PointMove(Model):
    """Customer points, one row per award or spend. A ledger model: lean rows, no screens or routes, and the
    database refuses to change a row once written. Only the Award points action (app.py) writes them."""
    class Meta:
        ledger = True
        append_only = True
        timestamps = 'created'
        indexes = [('customer', 'on_date')]

    customer = Link(Customer, required=True)
    on_date = Date(required=True)
    reason = Text(default='', max_length=120)
    points = Integer(required=True)


class PointBalance(Model):
    """Each customer's points total: a cache the Award points action updates with each movement."""
    class Meta:
        ledger = True
        timestamps = 'modified'
        indexes = [(('customer',), {'unique': True})]

    customer = Link(Customer, required=True)
    points = Integer(required=True, default=0)


# Items have about 200 fields and two child tables: the list filters at scale. Imported here so the
# registry lists every Playground model; items.py links to customers by name, not by import.
from .items import Item  # noqa: E402

registry = Registry(Customer, Invoice, Payment, LedgerEntry, Project, ProjectTask, Item, Employee, PointMove, PointBalance, Recognition)
