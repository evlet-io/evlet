"""Values a role must not see stay hidden on every read path: the draft preview, formulas over Query
sources with a row rule, and cached derivation checks filled by trusted code."""

from evlet import Boolean, Currency, Database, Link, Model, Query, Ref, Reference, Registry, Rule, Text, User
from evlet.calc import F
from evlet.permissions import calling
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import add_user, start

ALL = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update'))}
# The editor reads only accounts marked public.
PUBLIC_ONLY = {**ALL, 'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'public': True})}


# 1. The draft preview blanks Links and References the user cannot follow

class Account(Model):
    permissions = PUBLIC_ONLY
    label = Text(required=True)
    public = Boolean(default=False)


class Ticket(Model):
    Meta = FiniteMeta
    permissions = ALL
    memo = Text(default='')
    account = Link(Account)
    about = Reference()


def test_draft_preview_hides_links_the_user_cannot_follow(tmp_path):
    app = start(Database(tmp_path / 'tickets.sqlite3', Registry(Account, Ticket)))
    admin = client(app)
    private = admin('POST', '/api/account', {'label': 'Private'})[1]
    status, ticket, _ = admin('POST', '/api/ticket', {'memo': 'x', 'account': private['id'],
                                                      'about': {'model': 'account', 'id': private['id']}})
    assert status == 201, ticket
    editor = add_user(app, 'editor')
    # A normal read blanks both, since the editor's row rule hides the account.
    shown = editor('GET', f"/api/ticket/{ticket['id']}")[1]
    assert shown['account'] is None and shown['about'] is None
    # The draft preview of the same saved record must not reveal them either.
    status, draft, _ = editor('POST', '/api/_draft/ticket', {'id': ticket['id'], 'values': {'memo': 'y'}})
    assert status == 200, draft
    values = draft.get('values', draft)
    assert values['account'] is None and values['about'] is None
    # The admin, who can read the account, still sees both in the preview.
    values = admin('POST', '/api/_draft/ticket', {'id': ticket['id'], 'values': {}})[1]
    values = values.get('values', values)
    assert values['account'] == private['id'] and values['about'] == {'model': 'account', 'id': private['id']}


# 2. A formula over a Query source respects the source's row rule

class Plan(Model):
    permissions = PUBLIC_ONLY
    label = Text(required=True)
    public = Boolean(default=False)
    limit = Currency(default='100.00')


class Booking(Model):
    permissions = ALL
    plan = Link(Plan)
    terms = Query(Plan, filters={'id': Ref('plan')}, fields=['limit'])
    limit_copy = Currency(compute='terms.limit', stored=False)


def test_a_formula_over_a_row_scoped_query_source_is_not_shown(tmp_path):
    app = start(Database(tmp_path / 'bookings.sqlite3', Registry(Plan, Booking)))
    admin = client(app)
    plan = admin('POST', '/api/plan', {'label': 'Gold', 'limit': '900'})[1]
    booking = admin('POST', '/api/booking', {'plan': plan['id']})[1]
    assert booking['limit_copy'] == '900.00'
    editor = add_user(app, 'editor')
    # The editor's row rule hides the plan, so the copy of its limit would reveal it.
    shown = editor('GET', f"/api/booking/{booking['id']}")[1]
    assert 'limit_copy' not in shown


# 3. Derivation checks cached by trusted code do not leak to callers

# Neither model declares permissions, so the hidden Link stays readable to trusted code.
class Vault(Model):
    class Meta:
        api = None
    balance = Currency(default='0')


class Statement(Model):
    Meta = FiniteMeta
    vault = Link(Vault, hidden=True)
    shown_balance = Currency(compute=F('vault.balance'), stored=False)


def test_trusted_code_does_not_fill_the_cache_for_callers(tmp_path):
    app = start(Database(tmp_path / 'statements.sqlite3', Registry(Vault, Statement)))
    policy, editor = app.business.policy, User(2, 'editor', 'editor')
    # Trusted code (a job, a schedule, start-up work) may read the vault through the formula.
    assert 'shown_balance' in policy.fields(editor, Statement)
    # A caller may not reach a Meta.api = None model, so the formula over it stays hidden.
    with calling():
        assert 'shown_balance' not in policy.fields(editor, Statement)
    # And the trusted answer is still the trusted one afterwards.
    assert 'shown_balance' in policy.fields(editor, Statement)
