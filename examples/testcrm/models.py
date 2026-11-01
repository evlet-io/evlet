"""testCRM: organizations, contacts, leads, deals with lines, products, tasks and bookings.

Everything the app shows comes from these models: lists, boards, record views, line tables,
moves, actions, related records, files and measures. Amounts are AED with UAE VAT at 5%.

Roles: admin manages everything. Sales people use the editor role: they see and change only
the deals they own and never see a deal's internal notes. Viewers read, without internal notes.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from evlet import (Attachments, Boolean, Child, Count, Currency, Date, Decimal, Duration, Effect, Email, Integer, Link,
                   LongText, Model, Move, Negate, Percent, Phone, Reference, Related, Rule, Select, Sequence, State, Sum,
                   Table, Tags, Text, Time, URL, action)
from evlet.calc import D, F, coalesce, days, parent, round, row, today, when

EVERYTHING = Rule(('read', 'create', 'update', 'delete'))
READ = Rule(('read',))

INDUSTRIES = ('Trading', 'Manufacturing', 'Logistics', 'Fit-out', 'Healthcare', 'Construction', 'FMCG', 'Retail', 'Services')
EMIRATES = ('Abu Dhabi', 'Dubai', 'Sharjah', 'Ajman', 'Umm Al Quwain', 'Ras Al Khaimah', 'Fujairah')
SOURCES = ('Referral', 'Website', 'LinkedIn', 'Event', 'Google Ads', 'Cold call', 'Partner')


class Organization(Model):
    """A company we sell to. Winning its first deal makes it a customer."""
    permissions = {'admin': EVERYTHING, 'editor': Rule(('read', 'create', 'update')), 'viewer': READ}
    name = Text(required=True, max_length=160)
    industry = Select(INDUSTRIES)
    emirate = Select(EMIRATES, default='Dubai')
    website = URL()
    phone = Phone()
    # The 15-digit UAE VAT registration number (TRN).
    trn = Text(max_length=15, label='TRN')
    payment_days = Integer(default=30, minimum=0, maximum=180)
    status = Select(('prospect', 'customer'), default='prospect')
    customer_since = Date()
    tags = Tags()

    class Meta:
        title_field = 'name'
        list_fields = ('name', 'industry', 'emirate', 'status', 'customer_since', 'phone')
        search_fields = ('name', 'trn')
        key_fields = ('status', 'emirate', 'industry', 'payment_days')
        layout = (('Company', ('name', 'industry', 'emirate', 'website', 'phone', 'trn')),
                  ('Account', ('status', 'customer_since', 'payment_days', 'tags')))
        relations = (Related('deal', 'organization', label='Deals', limit=10, order=('-id',)),
                     Related('contact', 'organization', label='People', limit=10, order=('name',)))
        measures = {
            'deals': Count('deal', link='organization'),
            'won_value': Sum('total', model='deal', link='organization', where=F('stage') == 'won'),
            'customers': Count(where=F('status') == 'customer'),
        }
        attachments = Attachments(max_files=10)


class Contact(Model):
    """A person at an organization."""
    permissions = {'admin': EVERYTHING, 'editor': Rule(('read', 'create', 'update')), 'viewer': READ}
    name = Text(required=True, max_length=120)
    job_title = Text(max_length=120)
    organization = Link(Organization, index=True)
    email = Email()
    phone = Phone()
    # Read through the organization link, so the list shows where the person sits.
    emirate = Text(compute=F('organization.emirate'))

    class Meta:
        title_field = 'name'
        list_fields = ('name', 'job_title', 'organization', 'email', 'phone')
        search_fields = ('name', 'email')
        key_fields = ('organization', 'job_title', 'email', 'phone')


class Product(Model):
    """What we sell. Only an administrator changes prices."""
    permissions = {'admin': EVERYTHING, 'editor': READ, 'viewer': READ}
    name = Text(required=True, max_length=120)
    code = Text(required=True, unique=True, max_length=20)
    kind = Select(('licence', 'service', 'subscription'), default='service')
    price = Currency(default=0, minimum=0)
    taxable = Boolean(default=True)
    active = Boolean(default=True)

    class Meta:
        title_field = 'name'
        list_fields = ('code', 'name', 'kind', 'price', 'active')
        search_fields = ('name', 'code')


class Booking(Model):
    """Won business: one row per won deal, written only by the deal when it is won. Voiding the deal
    writes a reversing row, so the bookings always add up to the business we still have."""
    permissions = {'admin': READ, 'editor': READ, 'viewer': READ}
    organization = Link(Organization)
    owner = Text(default='')
    amount = Currency(required=True)
    booked_on = Date()
    memo = Text(default='')
    source = Reference()
    reversal = Boolean(default=False)

    class Meta:
        list_fields = ('booked_on', 'organization', 'owner', 'amount', 'memo', 'reversal')
        measures = {'booked': Sum('amount')}


class Deal(Model):
    """A sales opportunity, from first contact to won or lost."""
    permissions = {
        'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise')),
        # Sales people own their deals: they see only those, and never the internal notes.
        'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'owner': user.username},
                       read_fields=('number', 'title', 'organization', 'contact', 'owner', 'lead_source', 'expected_close',
                                    'stage', 'probability', 'payment_days', 'vat_rate', 'lost_reason', 'next_step',
                                    'follow_up_on', 'won_on', 'tags', 'subtotal', 'vat_total', 'total',
                                    'recurring_total', 'line_count', 'weighted', 'payment_expected', 'days_to_close'),
                       write_fields=('title', 'organization', 'contact', 'owner', 'lead_source', 'expected_close',
                                     'payment_days', 'vat_rate', 'lost_reason', 'next_step', 'tags')),
        'viewer': Rule(('read',), read_fields=('number', 'title', 'organization', 'contact', 'owner', 'lead_source',
                                               'expected_close', 'stage', 'probability', 'vat_rate', 'subtotal', 'vat_total',
                                               'total', 'weighted', 'won_on', 'lost_reason')),
    }

    # DL-2026-0001, counted per year from the day the deal is created. Only the engine writes it.
    number = Sequence('DL-{year}-{n:04}')
    title = Text(required=True, max_length=160)
    organization = Link(Organization, required=True)
    contact = Link(Contact)
    # The username of the sales person who owns the deal.
    owner = Text(required=True, max_length=60)
    lead_source = Select(SOURCES, label='Source')
    expected_close = Date()
    stage = State(('lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost', 'void'), moves=(
        Move('lead', 'qualified', 'Qualify'),
        Move('qualified', 'proposal', 'Send proposal', when=F('total') > 0),
        Move('proposal', 'negotiation', 'Negotiate'),
        Move(('proposal', 'negotiation'), 'won', 'Mark won',
             when=(F('total') > 0) & (F('contact') != None), run='on_won'),  # noqa: E711
        Move(('lead', 'qualified', 'proposal', 'negotiation'), 'lost', 'Mark lost'),
        Move('lost', 'lead', 'Reopen', roles=('admin',)),
    ), locked=('won', 'lost', 'void'), final=('void',), voidable=('won',))
    # A won deal that has to change is revised: it is voided and a new copy starts at lead.
    probability = Percent(compute=when(F('stage') == 'won', D('100'),
                                       when(F('stage') == 'negotiation', D('75'),
                                            when(F('stage') == 'proposal', D('50'),
                                                 when(F('stage') == 'qualified', D('25'),
                                                      when(F('stage') == 'lead', D('10'), D('0')))))))
    payment_days = Integer(initial=F('organization.payment_days'), minimum=0, maximum=180)
    vat_rate = Percent(default=5)
    # Shown once the deal is lost, required then and still editable after it is locked. Mark lost asks for it.
    lost_reason = Text(applies_when=F('stage') == 'lost', required_when=F('stage') == 'lost', editable_when_locked=True,
                       max_length=200)
    next_step = Text(max_length=200, editable_when_locked=True)
    follow_up_on = Date()
    won_on = Date()
    tags = Tags()
    internal_notes = LongText()

    class Line(Child):
        product = Link(Product)
        # Suggested from the product; the user may change all three.
        description = Text(initial=F('product.name'), max_length=200)
        rate = Currency(initial=F('product.price'), minimum=0)
        taxable = Boolean(initial=F('product.taxable'))
        qty = Decimal(scale=2, default=1, minimum=0)
        discount = Percent(default=0)
        recurring = Boolean(default=False)
        # Rounded half up to fils by the Currency field; no round() needed.
        amount = Currency(compute=qty * rate * (1 - discount / 100))
        vat = Currency(compute=when(taxable == True, amount * parent.vat_rate / 100, D('0')))  # noqa: E712

    # Up to 1,000 lines. Clients send only what changed: {"create": [...], "update": [...], "delete": [...], "order": [...]}.
    lines = Table(Line, max_rows=1000)
    subtotal = Currency(compute=lines.sum('amount'))
    vat_total = Currency(compute=lines.sum('vat'))
    total = Currency(compute=subtotal + vat_total)
    recurring_total = Currency(compute=lines.where(row.recurring == True).sum('amount'))  # noqa: E712
    line_count = Integer(compute=lines.count())
    # The forecast in whole dirhams.
    weighted = Currency(compute=round(total * probability / 100, 0), label='Weighted value')
    # When the customer should pay, once the deal is won.
    payment_expected = Date(compute=when(F('won_on') == None, None, F('won_on') + days(coalesce(F('payment_days'), 0))))  # noqa: E711
    # Not stored: always counted from today.
    days_to_close = Integer(stored=False, compute=when(F('expected_close') == None, None,  # noqa: E711
                                                       F('expected_close') - today()))

    class Meta:
        calculation_language = 'finite-v1'
        numeric_profile = 'finite40-hu18-v1'
        revisable = True
        title_field = 'title'
        list_fields = ('number', 'title', 'organization', 'owner', 'stage', 'total', 'expected_close')
        search_fields = ('title', 'number')
        key_fields = ('organization', 'contact', 'total', 'expected_close', 'owner')
        layout = (('Deal', ('number', 'title', 'organization', 'contact', 'owner', 'lead_source', 'tags')),
                  ('Forecast', ('stage', 'probability', 'expected_close', 'days_to_close', 'weighted', 'lost_reason')),
                  ('Amounts', ('subtotal', 'vat_total', 'total', 'recurring_total', 'line_count')),
                  ('Terms', ('payment_days', 'vat_rate', 'next_step', 'follow_up_on', 'won_on', 'payment_expected',
                             'revises')),
                  ('Internal', ('internal_notes',)))
        relations = (Related('task', 'deal', label='Tasks', limit=10, order=('due_on',)),)
        measures = {
            'pipeline': Sum('total', where=(F('stage') != 'won') & (F('stage') != 'lost') & (F('stage') != 'void')),
            'won': Sum('total', where=F('stage') == 'won'),
            'deals': Count(),
            'open_tasks': Count('task', link='deal', where=F('done') == False),  # noqa: E712
        }
        effects = (Effect(Booking, rows='booking_rows', on='won', reverse=Negate('amount')),)
        attachments = Attachments(max_files=10, max_size=1_048_576)

    def on_won(self):
        """Winning a deal dates it, makes the organization a customer and books a kick-off."""
        won_on = today_in_dubai()
        self.won_on = won_on
        self.next_step = 'Kick-off meeting'
        organization = self.context.get(Organization, self.organization)
        if organization.status != 'customer':
            self.context.update(Organization, self.organization, status='customer', customer_since=won_on)
        self.context.create(Task, title=f'Kick-off: {self.title}', kind='meeting', deal=self.id, contact=self.contact,
                            assignee=self.owner, due_on=won_on + timedelta(days=7))

    @action('Schedule follow up', inputs={'due_on': Date(required=True), 'note': Text(required=True, max_length=200)},
            available_when=(F('stage') != 'won') & (F('stage') != 'lost') & (F('stage') != 'void'))
    def schedule_follow_up(self, due_on, note):
        """Sets the next step and adds a call task for the owner."""
        self.next_step = note
        self.follow_up_on = due_on
        self.context.create(Task, title=note, kind='call', deal=self.id, contact=self.contact, assignee=self.owner,
                            due_on=due_on)

    def booking_rows(self):
        return [{'organization': self.organization, 'owner': self.owner, 'amount': self.total,
                 'booked_on': self.won_on, 'memo': f'{self.number} {self.title}'}]


class Task(Model):
    """Something to do, usually for a deal."""
    permissions = {'admin': EVERYTHING, 'editor': EVERYTHING, 'viewer': READ}
    title = Text(required=True, max_length=200)
    kind = Select(('call', 'email', 'meeting', 'todo'), default='todo')
    assignee = Text(max_length=60)
    due_on = Date()
    at = Time(label='Time')
    length = Duration(applies_when=F('kind') == 'meeting')
    done = Boolean(default=False)
    deal = Link(Deal, index=True)
    contact = Link(Contact)
    overdue = Boolean(stored=False, compute=when(F('done') == True, False,  # noqa: E712
                                                 coalesce(F('due_on') < today(), False)))

    class Meta:
        title_field = 'title'
        list_fields = ('title', 'kind', 'assignee', 'due_on', 'done', 'deal')
        search_fields = ('title', 'assignee')
        key_fields = ('deal', 'due_on', 'assignee', 'done')


class Lead(Model):
    """Someone who might buy. A qualified lead is converted into an organization, a contact and a deal."""
    permissions = {'admin': EVERYTHING, 'editor': Rule(('read', 'create', 'update')), 'viewer': READ}
    name = Text(required=True, max_length=120)
    company = Text(required=True, max_length=160)
    email = Email()
    phone = Phone()
    lead_source = Select(SOURCES, label='Source')
    score = Integer(default=50, minimum=0, maximum=100)
    owner = Text(max_length=60)
    status = State(('new', 'qualified', 'lost'), moves=(
        Move('new', 'qualified', 'Qualify', when=F('score') >= 40),
        Move(('new', 'qualified'), 'lost', 'Mark lost'),
        Move('lost', 'new', 'Reopen'),
    ), locked=('lost',))
    # Mark lost asks for the reason: the move carries it and checks it in the same transaction.
    lost_reason = Text(applies_when=F('status') == 'lost', required_when=F('status') == 'lost',
                       editable_when_locked=True, max_length=200)
    deal = Link(Deal)

    class Meta:
        title_field = 'name'
        list_fields = ('name', 'company', 'lead_source', 'score', 'owner', 'status')
        search_fields = ('name', 'company', 'email')
        key_fields = ('company', 'score', 'owner', 'lead_source')

    @action('Convert to deal', inputs={'title': Text(required=True, max_length=160), 'expected_close': Date()},
            available_when=(F('status') == 'qualified') & (F('deal') == None))  # noqa: E711
    def convert(self, title, expected_close):
        """Creates the organization, the contact and a deal at qualified, and links the deal here."""
        organization = self.context.create(Organization, name=self.company, phone=self.phone, tags=['From lead'])
        contact = self.context.create(Contact, name=self.name, organization=organization['id'], email=self.email,
                                      phone=self.phone)
        deal = self.context.create(Deal, title=title, organization=organization['id'], contact=contact['id'],
                                   owner=self.owner or self.context.user.username, lead_source=self.lead_source,
                                   expected_close=expected_close)
        self.context.move(Deal, deal['id'], 'qualified')
        self.deal = deal['id']


def today_in_dubai():
    return datetime.now(ZoneInfo('Asia/Dubai')).date()

