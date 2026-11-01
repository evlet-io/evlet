"""Phase 9: measures and the aggregate API on real SQLite and, when configured, PostgreSQL."""

from datetime import date
from decimal import Decimal as D_
import json
import time
from urllib.parse import quote

import pytest

from evlet import (Boolean, Child, Count, CountDistinct, Currency, Database, Date, Day, Decimal, Integer, Link, LongText, Max, Min,
                   Model, Month, Move, NotFoundError, PostgreSQLDatabase, Registry, Rule, Select, Sequence, State, Sum, Tags, Text,
                   Table, ValidationError, Year)
from evlet.auth import AccessError, User
from evlet.calc import D, F, days, month_start, today, when
from test_business import client, http
from test_postgres import pg_dsn  # noqa: F401 - fixture
from test_rules_types import add_user, start

ALL = Rule(('read', 'create', 'update', 'delete'))


class Customer(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read',), scope=lambda user: {'name': user.username}),
                   'viewer': Rule(('read',))}
    name = Text(default='')


class Invoice(Model):
    permissions = {
        'admin': ALL,
        'editor': Rule(('read', 'create', 'update'), read_fields=(
            'customer', 'status', 'posting_date', 'paid_on', 'total', 'outstanding', 'qty', 'units', 'weight', 'approved', 'owner'),
            scope=lambda user: {'owner': user.username}),
        'viewer': Rule(('read',), read_fields=('status', 'posting_date', 'total')),
    }
    customer = Link(Customer)
    status = Select(('draft', 'issued', 'paid'), default='draft')
    posting_date = Date()
    paid_on = Date()
    total = Currency(default='0')
    outstanding = Currency(default='0')
    qty = Decimal(scale=3, default='0')
    units = Integer()
    weight = Integer(default=0)
    approved = Boolean()
    owner = Text(default='')
    secret = Currency(default='0')
    notes = LongText(default='')
    labels = Tags()

    class Meta:
        measures = {
            'outstanding': Sum('outstanding', where=F('status') == 'issued'),
            'count': Count(),
            'hidden_total': Sum('secret'),
            'customers': CountDistinct('customer'),
            'payments': Count('payment', link='invoice'),
            'paid': Sum('amount', model='payment', link='invoice'),
            'last_payment': Max('paid_on', model='payment', link='invoice'),
        }


class Payment(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read', 'create'), scope=lambda user: {'by': user.username}),
                   'viewer': Rule(('read',), read_fields=('amount',))}
    invoice = Link(Invoice)
    amount = Currency(default='0')
    paid_on = Date()
    by = Text(default='')


MODELS = (Customer, Invoice, Payment)


@pytest.fixture(params=['sqlite', 'postgresql'])
def app(request, tmp_path):
    registry = Registry(*MODELS)
    if request.param == 'sqlite':
        database = Database(tmp_path / 'measures.sqlite3', registry)
    else:
        database = PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry)
    application = start(database)
    add_user(application, 'editor')
    add_user(application, 'viewer')
    return application


def insert(app, model, rows):
    with app.database.transaction() as tx:
        return [tx.create(model, **row).id for row in rows]


def total(app, **query):
    return app.database.aggregate(Invoice, **query)


# Exact sums and overflow

def test_exact_sums_over_many_rows_and_large_values(app):
    insert(app, Invoice, [{'total': '0.10', 'qty': '0.001', 'units': 3, 'owner': 'many'} for _ in range(1000)])
    rows = total(app, measures={'sum': Sum('total'), 'qty': Sum('qty'), 'units': Sum('units'), 'n': Count()}, filters={'owner': 'many'})
    # 1000 x 0.10 is exactly 100.00; a float sum would give 99.9999999999986.
    assert rows == [{'sum': D_('100.00'), 'qty': D_('1.000'), 'units': 3000, 'n': 1000}]
    assert str(rows[0]['sum']) == '100.00'
    half = 2**62
    insert(app, Invoice, [{'total': str(D_(half) / 100), 'owner': 'big'}, {'total': str(D_(half - 1) / 100), 'owner': 'big'},
                          {'total': '-0.01', 'owner': 'big'}])
    assert total(app, measures={'sum': Sum('total')}, filters={'owner': 'big', 'total__gt': '0'}) == [{'sum': D_('92233720368547758.07')}]
    assert total(app, measures={'sum': Sum('total')}, filters={'owner': 'big'}) == [{'sum': D_('92233720368547758.06')}]
    insert(app, Invoice, [{'total': '0.01', 'owner': 'big'}, {'total': '0.01', 'owner': 'big'}])
    with pytest.raises(ValidationError) as error:
        total(app, measures={'sum': Sum('total')}, filters={'owner': 'big', 'total__gt': '0'})
    assert error.value.errors == {'measures': 'A sum is outside the exact range of stored numbers; narrow the filters'}
    # Count, Min and Max are unaffected by the large values.
    assert total(app, measures={'n': Count(), 'low': Min('total'), 'high': Max('total')}, filters={'owner': 'big'}) == [
        {'n': 5, 'low': D_('-0.01'), 'high': D_('46116860184273879.04')}]


def test_sums_depend_only_on_the_final_total(app):
    big = 2**62
    # The running total passes 2^63 in one order and not in the other; both give the same exact result.
    insert(app, Invoice, [{'units': big, 'owner': 'up'}, {'units': big, 'owner': 'up'}, {'units': -big, 'owner': 'up'},
                          {'units': -big, 'owner': 'down'}, {'units': big, 'owner': 'down'}, {'units': big, 'owner': 'down'}])
    for owner in ('up', 'down'):
        assert total(app, measures={'s': Sum('units')}, filters={'owner': owner}) == [{'s': big}]
    assert total(app, measures={'s': Sum('units')}, group_by='owner') == [{'owner': 'down', 's': big}, {'owner': 'up', 's': big}]
    # The smallest stored integer sums, compares and bounds like any other value.
    insert(app, Invoice, [{'units': -2**63, 'owner': 'low'}])
    assert total(app, measures={'s': Sum('units'), 'lo': Min('units'), 'n': Count(where=F('units') == D(str(-2**63)))},
                 filters={'owner': 'low'}) == [{'s': -2**63, 'lo': -2**63, 'n': 1}]
    with pytest.raises(ValidationError, match='outside the range'):
        total(app, measures={'n': Count(where=F('units') < D(str(-2**63 - 1)))})
    insert(app, Invoice, [{'units': -1, 'owner': 'low'}])
    with pytest.raises(ValidationError) as error:
        total(app, measures={'s': Sum('units')}, filters={'owner': 'low'})
    assert error.value.errors == {'measures': 'A sum is outside the exact range of stored numbers; narrow the filters'}
    # One group out of range fails the whole request.
    with pytest.raises(ValidationError):
        total(app, measures={'s': Sum('units')}, group_by='owner')


def test_empty_results_and_nulls(app):
    # No rows: one result with zero counts and sums and empty minimums.
    assert total(app, measures={'n': Count(), 's': Sum('total'), 'u': Sum('units'), 'lo': Min('posting_date'),
                                'd': CountDistinct('status')}) == [{'n': 0, 's': D_('0.00'), 'u': 0, 'lo': None, 'd': 0}]
    assert total(app, measures={'n': Count()}, group_by=('status',)) == []
    insert(app, Invoice, [{'units': None, 'status': None}, {'units': 4, 'status': 'draft'}, {'units': None, 'status': 'draft'}])
    assert total(app, measures={'n': Count(), 'u': Sum('units'), 'hi': Max('units'), 'd': CountDistinct('units')}) == [
        {'n': 3, 'u': 4, 'hi': 4, 'd': 1}]
    # Empty values form their own group, sorted first.
    assert total(app, measures={'n': Count(), 'u': Sum('units')}, group_by='status') == [
        {'status': None, 'n': 1, 'u': 0}, {'status': 'draft', 'n': 2, 'u': 4}]


# Grouping

def test_grouping_by_day_month_and_year_across_years_and_timezones(app, monkeypatch):
    dates = ['2024-12-31', '2025-01-01', '2025-01-31', '2025-02-01', '2026-01-01', '2026-01-01', None]
    insert(app, Invoice, [{'posting_date': value, 'total': '1.50', 'status': 'issued' if index % 2 else 'draft'}
                          for index, value in enumerate(dates)])
    expected_years = [{'posting_date__year': None, 'n': 1}, {'posting_date__year': 2024, 'n': 1},
                      {'posting_date__year': 2025, 'n': 3}, {'posting_date__year': 2026, 'n': 2}]
    expected_months = [{'posting_date__month': None, 'n': 1, 's': D_('1.50')}, {'posting_date__month': '2024-12', 'n': 1, 's': D_('1.50')},
                       {'posting_date__month': '2025-01', 'n': 2, 's': D_('3.00')}, {'posting_date__month': '2025-02', 'n': 1, 's': D_('1.50')},
                       {'posting_date__month': '2026-01', 'n': 2, 's': D_('3.00')}]
    for zone in ('UTC', 'Pacific/Kiritimati', 'America/Adak'):
        # Dates are plain calendar dates, so the process timezone never moves a record between groups.
        monkeypatch.setenv('TZ', zone)
        time.tzset()
        assert total(app, measures={'n': Count()}, group_by=Year('posting_date')) == expected_years
        assert total(app, measures={'n': Count(), 's': Sum('total')}, group_by=(Month('posting_date'),)) == expected_months
        days = total(app, measures={'n': Count()}, group_by=[Day('posting_date')])
        assert [(row['posting_date__day'], row['n']) for row in days] == [
            (None, 1), (date(2024, 12, 31), 1), (date(2025, 1, 1), 1), (date(2025, 1, 31), 1), (date(2025, 2, 1), 1), (date(2026, 1, 1), 2)]
    monkeypatch.delenv('TZ')
    time.tzset()
    combined = total(app, measures={'n': Count()}, group_by=('status', Year('posting_date')))
    assert combined == [
        {'status': 'draft', 'posting_date__year': None, 'n': 1}, {'status': 'draft', 'posting_date__year': 2024, 'n': 1},
        {'status': 'draft', 'posting_date__year': 2025, 'n': 1}, {'status': 'draft', 'posting_date__year': 2026, 'n': 1},
        {'status': 'issued', 'posting_date__year': 2025, 'n': 2}, {'status': 'issued', 'posting_date__year': 2026, 'n': 1}]
    # A field and a part of it may be grouped together.
    both = total(app, measures={'n': Count()}, group_by=('posting_date', Year('posting_date')), filters={'posting_date__isnull': False})
    assert both[0] == {'posting_date': date(2024, 12, 31), 'posting_date__year': 2024, 'n': 1}


def test_limits_and_invalid_requests(app):
    insert(app, Invoice, [{'weight': index} for index in range(501)])
    assert len(total(app, measures={'n': Count()}, group_by='weight', filters={'weight__lt': 500})) == 500
    with pytest.raises(ValidationError, match='more than 500 groups'):
        total(app, measures={'n': Count()}, group_by='weight')
    nine = {f'm{index}': Count() for index in range(9)}
    cases = [
        ({'measures': nine}, 'measures'),
        ({'measures': {}}, 'measures'),
        ({'measures': {'n': Count()}, 'group_by': ('status', 'owner', 'units', 'weight', 'approved')}, 'group_by'),
        ({'measures': {'Bad': Count()}}, 'measures'),
        ({'measures': {'n': Sum('owner')}}, 'n'),
        ({'measures': {'n': Min('status')}}, 'n'),
        ({'measures': {'n': Sum('customer')}}, 'n'),
        ({'measures': {'n': CountDistinct('labels')}}, 'n'),
        ({'measures': {'n': Sum('missing')}}, 'n'),
        ({'measures': {'n': 'not_declared'}}, 'n'),
        ({'measures': {'n': 'payments'}}, 'n'),
        ({'measures': {'n': Count('payment', link='invoice')}}, 'n'),
        ({'measures': {'n': 'count'}, 'group_by': Month('owner')}, 'group_by'),
        ({'measures': {'n': Count()}, 'group_by': 'notes'}, 'group_by'),
        ({'measures': {'n': Count()}, 'group_by': 'labels'}, 'group_by'),
        ({'measures': {'n': Count()}, 'group_by': ('status', 'status')}, 'group_by'),
        ({'measures': {'n': Count()}, 'group_by': 'nothing'}, 'group_by'),
        ({'measures': {'status': Count()}, 'group_by': 'status'}, 'measures'),
        ({'measures': {'n': Count()}, 'filters': {'nothing': 1}}, 'nothing'),
        ({'measures': {'n': Count()}, 'filters': {'total__gt': 'x'}}, 'total__gt'),
        ({'measures': {'n': Count()}, 'group_by': 5}, 'group_by'),
        ({'measures': {'n': Count()}, 'group_by': ('status', 5)}, 'group_by'),
        ({'measures': [('n', Count())]}, 'measures'),
    ]
    for query, key in cases:
        with pytest.raises(ValidationError) as error:
            total(app, **query)
        assert key in error.value.errors, (query, error.value.errors)
    with pytest.raises(ValidationError) as error:
        total(app, measures={'n': Count()}, group_by=5)
    assert error.value.errors == {'group_by': 'Use a field name, Day(), Month() or Year(), or a tuple or list of them'}
    with pytest.raises(ValueError, match='CountDistinct'):
        Count('payment')
    with pytest.raises(ValueError, match='both model= and link='):
        Sum('amount', model=Payment)


def test_filters_use_the_list_filter_syntax(app):
    insert(app, Invoice, [{'status': 'issued', 'total': '10.00', 'owner': 'a'}, {'status': 'issued', 'total': '2.50', 'owner': 'b'},
                          {'status': 'draft', 'total': '7.00', 'owner': 'a'}])
    assert total(app, measures={'s': Sum('total')}, filters={'status': 'issued'}) == [{'s': D_('12.50')}]
    assert total(app, measures={'s': Sum('total')}, filters={'total__gte': '7', 'owner__in': ['a']}) == [{'s': D_('17.00')}]
    assert total(app, measures={'s': Sum('total'), 'n': Count()}, group_by='owner', filters={'status__ne': 'draft'}) == [
        {'owner': 'a', 's': D_('10.00'), 'n': 1}, {'owner': 'b', 's': D_('2.50'), 'n': 1}]


# where= expressions

WHERE_ROWS = [
    {'status': 'issued', 'total': '10.00', 'outstanding': '4.00', 'approved': True, 'posting_date': '2025-01-01',
     'paid_on': '2025-01-05', 'units': 1, 'qty': '0.001', 'weight': 1},
    {'status': 'paid', 'total': None, 'outstanding': '0', 'approved': None, 'posting_date': '2025-02-01',
     'paid_on': None, 'units': None, 'qty': '0', 'weight': 2},
    {'status': None, 'total': '1.01', 'outstanding': '2.00', 'approved': False, 'posting_date': None,
     'paid_on': '2025-01-01', 'units': 5, 'qty': '0', 'weight': 4},
    {'status': 'draft', 'total': '1.00', 'outstanding': '1.00', 'approved': True, 'posting_date': '2025-03-01',
     'paid_on': '2025-02-01', 'units': 2, 'qty': '1.5', 'weight': 8},
]
# Rows (by index) each condition includes. An undecided condition (an ordered comparison or a
# Boolean with an empty value) includes nothing, exactly as finite-v1 leaves it pending.
WHERE_CASES = [
    (F('status') == 'issued', {0}),
    (F('status') != 'issued', {1, 2, 3}),
    (F('status') == None, {2}),  # noqa: E711
    (F('status') != None, {0, 1, 3}),  # noqa: E711
    (F('total') > D('1.005'), {0, 2}),
    (F('total') <= D('1.005'), {3}),
    (F('total') == D('1.005'), set()),
    (F('total') != D('1.005'), {0, 1, 2, 3}),
    (F('total') >= 10, {0}),
    (10 <= F('total'), {0}),
    (F('total') > F('outstanding'), {0}),
    (F('total') == F('outstanding'), {3}),
    (F('total') != F('outstanding'), {0, 1, 2}),
    (~(F('total') > 5), {2, 3}),
    ((F('total') > 5) | (F('status') == 'paid'), {0}),
    ((F('status') == 'paid') | (F('units') == None), {1}),  # noqa: E711
    ((F('status') != 'draft') & F('approved'), {0}),
    (F('approved'), {0, 3}),
    (~F('approved'), {2}),
    (F('approved') == True, {0, 3}),  # noqa: E712
    (F('approved') != True, {1, 2}),  # noqa: E712
    (F('posting_date') < F('paid_on'), {0}),
    (F('posting_date') == F('paid_on'), set()),
    (F('qty') > D('0.0005'), {0, 3}),
    (F('qty') < D('0.0005'), {1, 2}),
    (F('units') >= 2, {2, 3}),
    (F('units') == None, {1}),  # noqa: E711
    (True, {0, 1, 2, 3}),
]


def test_where_expressions_match_finite_semantics(app):
    insert(app, Invoice, WHERE_ROWS)
    for condition, expected in WHERE_CASES:
        weights = sum(WHERE_ROWS[index]['weight'] for index in expected)
        result = total(app, measures={'n': Count(where=condition), 'w': Sum('weight', where=condition)})
        assert result == [{'n': len(expected), 'w': weights}], (condition, result)
    # Several conditional measures in one query, grouped.
    grouped = total(app, measures={'issued': Sum('total', where=F('status') == 'issued'), 'big': Count(where=F('total') > 5),
                                   'approved': CountDistinct('status', where=F('approved'))}, group_by=Year('posting_date'))
    assert grouped == [{'posting_date__year': None, 'issued': D_('0.00'), 'big': 0, 'approved': 0},
                       {'posting_date__year': 2025, 'issued': D_('10.00'), 'big': 1, 'approved': 2}]


def test_where_compares_dates_with_today_days_and_the_month_start(app, monkeypatch):
    import evlet.measures as measures
    zones = []
    monkeypatch.setattr(measures, 'current_date', lambda zone: zones.append(zone) or date(2026, 3, 15))
    dates = ['2026-02-28', '2026-03-01', '2026-03-14', '2026-03-15', '2026-03-16', None]
    insert(app, Invoice, [{'posting_date': value, 'total': '2.00'} for value in dates])
    cases = [
        (F('posting_date') < today(), 3),
        (today() > F('posting_date'), 3),
        (F('posting_date') == today(), 1),
        (F('posting_date') != today(), 5),
        (F('posting_date') >= today() - days(14), 4),
        (F('posting_date') <= today() + days(1), 5),
        (F('posting_date') >= month_start(), 4),
        ((F('posting_date') >= month_start()) & (F('posting_date') < today()), 2),
    ]
    for condition, expected in cases:
        assert total(app, measures={'n': Count(where=condition)}) == [{'n': expected}], condition
    # Trusted database calls use UTC; Context and HTTP use the instance timezone, at query time.
    assert set(zones) == {'UTC'}
    app.business.timezone = 'Asia/Dubai'
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.aggregate(Invoice, measures={'n': Sum('total', where=F('posting_date') >= month_start())}) == [{'n': D_('8.00')}]
    assert zones[-1] == 'Asia/Dubai'


def test_unsupported_where_expressions_are_rejected(app):
    rejected = [
        (F('total') + 1 > 2, 'not calculations'),
        (F('customer.name') == 'x', 'Link paths'),
        (F('nope') == 1, 'unknown field nope'),
        (F('total'), 'Boolean condition'),
        (F('posting_date') < today() - days(F('weight')), 'not calculations'),
        (F('total') < today(), 'ordered comparisons'),
        (F('qty') == F('total'), 'same scale'),
        (D(1) == 1, 'compare a field'),
        (when(F('approved'), True, False), 'when is not supported'),
        (F('status') > 'a', 'ordered comparisons'),
        (F('labels') == 'x', 'tags field'),
        (F('total') == D('99999999999999999999'), 'outside the range'),
    ]
    for condition, message in rejected:
        with pytest.raises(ValidationError) as error:
            total(app, measures={'n': Count(where=condition)})
        assert message in error.value.errors['n'], (condition, error.value.errors)


def test_meta_measures_are_checked_at_registration():
    def declare(measures, *extra):
        class Bad(Model):
            name = Text(default='')
            amount = Currency(default='0')
            Meta = type('Meta', (), {'measures': measures})
        return Registry(Bad, *extra)
    cases = [
        ({'n': Sum('name')}, 'needs a number field'),
        ({'Bad': Count()}, 'lowercase'),
        ({'n': Count(where=F('amount') * 2 > 1)}, 'not calculations'),
        ({'n': 'count'}, 'Use Count, CountDistinct'),
        ({}, 'one to 8'),
        ({f'm{index}': Count() for index in range(9)}, 'one to 8'),
        ({'n': Count(Customer, link='name')}, 'must be a Link'),
    ]
    for measures, message in cases:
        with pytest.raises(ValueError, match=message):
            declare(measures, Customer)
    with pytest.raises(ValueError, match='does not belong'):
        declare({'n': Count(Payment, link='invoice')})
    assert set(Registry(*MODELS).measures[Invoice]) == {'outstanding', 'count', 'hidden_total', 'customers', 'payments', 'paid', 'last_payment'}


# Permissions, Context and per-record measures

def seed(app):
    customers = insert(app, Customer, [{'name': 'editor'}, {'name': 'other'}])
    invoices = insert(app, Invoice, [
        {'customer': customers[0], 'status': 'issued', 'total': '100.00', 'outstanding': '60.00', 'owner': 'editor', 'secret': '1.00', 'posting_date': '2025-01-10'},
        {'customer': customers[1], 'status': 'issued', 'total': '50.00', 'outstanding': '50.00', 'owner': 'other', 'secret': '2.00', 'posting_date': '2025-02-10'},
        {'customer': customers[0], 'status': 'draft', 'total': '20.00', 'outstanding': '20.00', 'owner': 'editor', 'secret': '3.00', 'posting_date': '2026-01-10'},
        {'customer': None, 'status': 'paid', 'total': '5.00', 'outstanding': '0', 'owner': 'other'},
    ])
    insert(app, Payment, [{'invoice': invoices[0], 'amount': '25.00', 'paid_on': '2025-01-20', 'by': 'editor'},
                          {'invoice': invoices[0], 'amount': '15.00', 'paid_on': '2025-03-01', 'by': 'admin'},
                          {'invoice': invoices[0], 'amount': '0.10', 'paid_on': '2025-02-01', 'by': 'editor'},
                          {'invoice': invoices[1], 'amount': '1.00', 'paid_on': '2025-02-11', 'by': 'editor'}])
    return invoices


def test_permission_aware_aggregates_apply_row_scope_and_field_rules(app):
    invoices = seed(app)
    admin, editor, viewer = User(1, 'admin', 'admin'), User(2, 'editor', 'editor'), User(3, 'viewer', 'viewer')
    query = {'measures': {'s': Sum('total'), 'n': Count(), 'open': 'outstanding'}, 'group_by': ('status',)}
    with app.business.context(admin, readonly=True) as context:
        assert context.aggregate(Invoice, **query) == [
            {'status': 'draft', 's': D_('20.00'), 'n': 1, 'open': D_('0.00')},
            {'status': 'issued', 's': D_('150.00'), 'n': 2, 'open': D_('110.00')},
            {'status': 'paid', 's': D_('5.00'), 'n': 1, 'open': D_('0.00')}]
        assert context.measures(Invoice) == {'outstanding': D_('110.00'), 'count': 4, 'hidden_total': D_('6.00'), 'customers': 2}
        assert context.measures('invoice', invoices[0]) == {'payments': 3, 'paid': D_('40.10'), 'last_payment': date(2025, 3, 1)}
    with app.business.context(editor, readonly=True) as context:
        # The editor sees only its own invoices and its own payments.
        assert context.aggregate(Invoice, **query) == [
            {'status': 'draft', 's': D_('20.00'), 'n': 1, 'open': D_('0.00')},
            {'status': 'issued', 's': D_('100.00'), 'n': 1, 'open': D_('60.00')}]
        assert context.measures(Invoice) == {'outstanding': D_('60.00'), 'count': 2}
        assert context.measures(Invoice, invoices[0]) == {'payments': 2, 'paid': D_('25.10'), 'last_payment': date(2025, 2, 1)}
        denied = [{'measures': {'s': Sum('secret')}}, {'measures': {'s': 'hidden_total'}}, {'measures': {'s': CountDistinct('notes')}},
                  {'measures': {'n': Count(where=F('secret') > 0)}}, {'measures': {'n': Count()}, 'group_by': 'secret'},
                  {'measures': {'n': Count()}, 'group_by': 'customer'}, {'measures': {'n': Count()}, 'filters': {'secret__gt': 0}},
                  # Customers are row scoped for the editor: no measure may count or compare their links.
                  {'measures': {'n': CountDistinct('customer')}}, {'measures': {'n': Count(where=F('customer') == None)}},  # noqa: E711
                  {'measures': {'n': Count(where=F('customer.name') == 'x')}}, {'measures': {'n': 'customers'}},
                  # Unknown fields and declared measures look exactly like unreadable ones.
                  {'measures': {'n': Count(where=F('nothing') > 0)}}, {'measures': {'n': 'not_declared'}},
                  {'measures': {'n': 'payments'}}, {'measures': {'n': Sum('nothing')}}, {'measures': {'n': Count()}, 'group_by': 'nothing'}]
        for request in denied:
            with pytest.raises(AccessError) as error:
                context.aggregate(Invoice, **request)
            assert (error.value.status, error.value.message) == (403, 'Measure field is unavailable') or (
                'filters' in request and error.value.status == 403), request
        with pytest.raises(AccessError) as error:
            context.measures(Invoice, invoices[1])
        assert error.value.status == 404
    with app.business.context(viewer, readonly=True) as context:
        assert context.aggregate(Invoice, measures={'s': Sum('total'), 'last': Max('posting_date')}) == [
            {'s': D_('175.00'), 'last': date(2026, 1, 10)}]
        assert context.measures(Invoice) == {'count': 4}
        # The viewer cannot read Payment.invoice, so no per-record measure is available.
        assert context.measures(Invoice, invoices[0]) == {}
        with pytest.raises(AccessError):
            context.aggregate(Invoice, measures={'o': 'outstanding'})
    # Trusted database calls see everything.
    assert app.database.measures(Invoice, invoices[3]) == {'payments': 0, 'paid': D_('0.00'), 'last_payment': None}
    assert app.database.measures(Invoice, filters={'owner': 'other'}) == {'outstanding': D_('50.00'), 'count': 2, 'hidden_total': D_('2.00'),
                                                                          'customers': 1}
    with pytest.raises(NotFoundError):
        app.database.measures(Invoice, 999)
    with pytest.raises(ValidationError):
        app.database.measures(Invoice, invoices[0], filters={'owner': 'x'})


def aggregate_url(measures, group_by=None, filters=None):
    url = '/api/invoice/aggregate?measures=' + quote(measures)
    if group_by is not None:
        url += '&group_by=' + quote(group_by)
    if filters is not None:
        url += '&filters=' + quote(json.dumps(filters))
    return url


def test_http_endpoints(app):
    invoices = seed(app)
    admin, editor, viewer = client(app), client(app, 'editor'), client(app, 'viewer')
    status, body, _ = admin('GET', aggregate_url('total:sum:total,n:count,outstanding,last:max:posting_date', 'status,posting_date:year'))
    assert status == 200, body
    assert body == {'group_by': ['status', 'posting_date__year'], 'measures': ['total', 'n', 'outstanding', 'last'], 'items': [
        {'status': 'draft', 'posting_date__year': 2026, 'total': '20.00', 'n': 1, 'outstanding': '0.00', 'last': '2026-01-10'},
        {'status': 'issued', 'posting_date__year': 2025, 'total': '150.00', 'n': 2, 'outstanding': '110.00', 'last': '2025-02-10'},
        {'status': 'paid', 'posting_date__year': None, 'total': '5.00', 'n': 1, 'outstanding': '0.00', 'last': None}]}
    assert admin('GET', aggregate_url('n:count', 'posting_date:month,posting_date:day', {'status': 'issued'}))[1]['items'] == [
        {'posting_date__month': '2025-01', 'posting_date__day': '2025-01-10', 'n': 1},
        {'posting_date__month': '2025-02', 'posting_date__day': '2025-02-10', 'n': 1}]
    assert admin('GET', aggregate_url('c:count_distinct:customer,lo:min:total'))[1]['items'] == [{'c': 2, 'lo': '5.00'}]
    assert editor('GET', aggregate_url('total:sum:total'))[1]['items'] == [{'total': '120.00'}]
    assert viewer('GET', aggregate_url('n:count', 'status'))[1]['items'] == [
        {'status': 'draft', 'n': 1}, {'status': 'issued', 'n': 2}, {'status': 'paid', 'n': 1}]
    # Model-level declared measures for a list page, with the list filters.
    assert admin('GET', '/api/invoice/measures')[1] == {'measures': {'outstanding': '110.00', 'count': 4, 'hidden_total': '6.00', 'customers': 2}}
    assert admin('GET', '/api/invoice/measures?filters=' + quote(json.dumps({'status': 'draft'})))[1] == {
        'measures': {'outstanding': '0.00', 'count': 1, 'hidden_total': '3.00', 'customers': 1}}
    assert editor('GET', '/api/invoice/measures')[1] == {'measures': {'outstanding': '60.00', 'count': 2}}
    assert viewer('GET', '/api/invoice/measures')[1] == {'measures': {'count': 4}}
    # Per-record measures over the related payments.
    assert admin('GET', f'/api/invoice/{invoices[0]}/measures')[1] == {
        'measures': {'payments': 3, 'paid': '40.10', 'last_payment': '2025-03-01'}}
    assert editor('GET', f'/api/invoice/{invoices[0]}/measures')[1] == {
        'measures': {'payments': 2, 'paid': '25.10', 'last_payment': '2025-02-01'}}
    assert viewer('GET', f'/api/invoice/{invoices[0]}/measures')[1] == {'measures': {}}
    assert admin('GET', f'/api/invoice/{invoices[3]}/measures')[1] == {'measures': {'payments': 0, 'paid': '0.00', 'last_payment': None}}
    assert editor('GET', f'/api/invoice/{invoices[1]}/measures')[0] == 404
    assert admin('GET', '/api/invoice/999/measures')[0] == 404
    # Unreadable fields are rejected with one generic message, whether or not they exist.
    for url in (aggregate_url('s:sum:secret'), aggregate_url('s:sum:nothing'), aggregate_url('hidden_total'),
                aggregate_url('c:count_distinct:customer'), aggregate_url('customers'), aggregate_url('nothing'), aggregate_url('payments'),
                aggregate_url('n:count', 'secret'), aggregate_url('n:count', 'customer'), aggregate_url('n:count', None, {'secret': 1})):
        status, body, _ = editor('GET', url)
        assert status == 403 and body['error'] in ('Measure field is unavailable', 'Query field is unavailable'), (url, body)
    assert viewer('GET', aggregate_url('n:count', 'owner')) [1] == {'error': 'Measure field is unavailable'}
    assert viewer('GET', aggregate_url('outstanding'))[0] == 403
    # Declared measures leave out what the role cannot use, including Links to row-scoped models.
    editor_schema = next(model for model in editor('GET', '/api/schema')[1]['models'] if model['name'] == 'invoice')
    assert 'customers' not in [item['name'] for item in editor_schema['measures']]
    # Filters are checked even when the model declares no measure the role may read.
    assert admin('GET', '/api/payment/measures?filters=' + quote(json.dumps({'amount__gt': 'x'})))[0] == 422
    assert admin('GET', '/api/payment/measures')[1] == {'measures': {}}


def test_http_rejects_malformed_parameters(app):
    seed(app)
    admin = client(app)
    malformed = [
        '/api/invoice/aggregate',
        '/api/invoice/aggregate?measures=',
        '/api/invoice/aggregate?measures=n:average:total',
        '/api/invoice/aggregate?measures=n:count:total',
        '/api/invoice/aggregate?measures=n:sum',
        '/api/invoice/aggregate?measures=n:count,n:count',
        '/api/invoice/aggregate?measures=N:count',
        '/api/invoice/aggregate?measures=n:sum:total:extra',
        '/api/invoice/aggregate?measures=n:sum:owner',
        '/api/invoice/aggregate?measures=n:count&group_by=posting_date:week',
        '/api/invoice/aggregate?measures=n:count&group_by=owner:month',
        '/api/invoice/aggregate?measures=n:count&group_by=a,b,c,d,e',
        '/api/invoice/aggregate?measures=n:count&group_by=status,',
        '/api/invoice/aggregate?measures=n:count&filters=[]',
        '/api/invoice/aggregate?measures=n:count&filters={',
        '/api/invoice/aggregate?measures=n:count&measures=m:count',
        '/api/invoice/aggregate?measures=n:count&order_by=n',
        '/api/invoice/aggregate?measures=' + ','.join(f'm{index}:count' for index in range(9)),
        '/api/invoice/aggregate?measures=n:count&filters=' + quote(json.dumps({'total__gt': 'x'})),
        '/api/invoice/measures?q=x',
        '/api/invoice/1/measures?filters=%7B%7D',
        '/api/invoice/aggregate?measures=n%FF:count',
        '/api/invoice/aggregate?measures=n:count&filters=' + 'x' * 9000,
    ]
    for url in malformed:
        status, body, _ = admin('GET', url)
        assert status == 422, (url, status, body)
    # Document models answer writes to a non-numeric record path with 404, others with 405.
    assert admin('POST', '/api/invoice/aggregate', {})[0] in (404, 405)
    assert admin('DELETE', '/api/invoice/1/measures')[0] == 405
    assert admin('GET', '/api/invoice/abc/measures')[0] == 404
    assert admin('GET', '/api/invoice/0/measures')[0] == 404
    assert admin('GET', '/api/invoice/aggregate/extra')[0] == 404
    assert http(app, 'GET', '/api/invoice/aggregate?measures=n:count')[0] == 401


def test_schema_lists_readable_declared_measures(app):
    admin, viewer = client(app), client(app, 'viewer')
    entry = next(model for model in admin('GET', '/api/schema')[1]['models'] if model['name'] == 'invoice')
    assert entry['measures'][0] == {'name': 'outstanding', 'function': 'sum', 'field': 'outstanding', 'per_record': False,
                                    'model': None, 'link': None, 'where': {'$expr': 'eq', 'args': [
                                        {'$expr': 'ref', 'scope': 'self', 'field': 'status'},
                                        {'$expr': 'literal', 'type': 'text', 'value': 'issued'}]}}
    assert [item['name'] for item in entry['measures']] == ['outstanding', 'count', 'hidden_total', 'customers', 'payments', 'paid',
                                                           'last_payment']
    assert entry['measures'][4] == {'name': 'payments', 'function': 'count', 'field': None, 'where': None, 'per_record': True,
                                    'model': 'payment', 'link': 'invoice'}
    shown = next(model for model in viewer('GET', '/api/schema')[1]['models'] if model['name'] == 'invoice')
    assert [item['name'] for item in shown['measures']] == ['count']
    assert 'measures' not in next(model for model in admin('GET', '/api/schema')[1]['models'] if model['name'] == 'payment')


# Measures beside void, revise, moves, Sequence numbers, line pages and related records on one model

class NumberedMeta:
    calculation_language = 'finite-v1'
    numeric_profile = 'finite40-hu18-v1'
    revisable = True
    measures = {
        # Declared conditions decide whether void records count.
        'live_total': Sum('total', where=(F('status') == 'draft') | (F('status') == 'issued')),
        'void_total': Sum('total', where=F('status') == 'void'),
        'quotes': Count(),
        'numbered': CountDistinct('number'),
        # Per record: the same count as the total of the Revisions relation.
        'revisions': Count('numberedquote', link='revises'),
    }


class NumberedLine(Child):
    note = Text(default='')


class NumberedQuote(Model):
    Meta = NumberedMeta
    permissions = {'admin': ALL}
    name = Text(default='')
    lines = Table(NumberedLine, max_rows=10)
    total = Currency(default='0')
    number = Sequence('NQ-{n:03}', assign_on='issued')
    status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),),
                   locked=('issued', 'void'), final=('void',), voidable=('issued',))


@pytest.fixture(params=['sqlite', 'postgresql'])
def numbered(request, tmp_path):
    registry = Registry(NumberedQuote)
    if request.param == 'sqlite':
        database = Database(tmp_path / 'numbered.sqlite3', registry)
    else:
        database = PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry)
    return start(database)


def test_measures_with_void_revise_moves_and_sequences(numbered):
    c = client(numbered)
    base = '/api/numberedquote'
    tag = lambda url: {b'if-match': c('GET', url)[2][b'etag']}
    urls = []
    for name, total in (('A', '10.00'), ('B', '20.00'), ('C', '5.00')):
        status, record, _ = c('POST', base, {'name': name, 'total': total, 'children': {'lines': [{'note': name}]}})
        assert status == 201, record
        urls.append(f"{base}/{record['id']}")
    # Each route on one model: moves, void, revise, measures, per-record measures, aggregate, lines and related.
    for url in urls[:2]:
        status, moved, _ = c('POST', url + '/moves/issued', {}, headers=tag(url))
        assert status == 200 and moved['number'] is not None, moved
    status, voided, _ = c('POST', urls[0] + '/void', {'reason': 'Duplicate'}, headers=tag(urls[0]))
    assert status == 200 and voided['status'] == 'void' and voided['number'] == 'NQ-001', voided
    status, copy, _ = c('POST', urls[1] + '/revise', {'reason': 'New price'}, headers=tag(urls[1]))
    assert status == 201 and copy['number'] is None and copy['status'] == 'draft', copy
    assert c('GET', base + '/measures')[1] == {
        'measures': {'live_total': '25.00', 'void_total': '30.00', 'quotes': 4, 'numbered': 2}}
    by_status = c('GET', f'{base}/aggregate?measures=n:count,t:sum:total&group_by=status')[1]
    assert by_status['items'] == [{'status': 'draft', 'n': 2, 't': '25.00'}, {'status': 'void', 'n': 2, 't': '30.00'}]
    # The revision link is an ordinary field to filter and group on.
    revised = c('GET', f"{base}/aggregate?measures=n:count&filters={quote(json.dumps({'revises': copy['revises']}))}")[1]
    assert revised['items'] == [{'n': 1}]
    assert c('GET', f"{urls[0]}/measures")[1] == {'measures': {'revisions': 0}}
    # Measure routes are GET only; void and revise stay POST only.
    assert c('POST', base + '/measures', {})[0] in (404, 405) and c('POST', base + '/aggregate', {})[0] in (404, 405)
    assert c('GET', urls[0] + '/void')[0] in (404, 405) and c('GET', urls[0] + '/revise')[0] in (404, 405)
    # Line pages and related records are GET only and do not shadow the other routes.
    status, lines, _ = c('GET', urls[1] + '/lines/lines')
    assert status == 200 and [row['note'] for row in lines['items']] == ['B'], lines
    status, revisions, _ = c('GET', urls[1] + '/related/numberedquote.revises')
    assert status == 200 and revisions['total'] == 1 and revisions['items'][0]['id'] == copy['id'], revisions
    assert c('GET', urls[2] + '/related/numberedquote.revises')[1]['total'] == 0
    assert c('GET', urls[1] + '/measures')[1] == {'measures': {'revisions': 1}}
    assert c('POST', urls[1] + '/related/numberedquote.revises', {}, headers=tag(urls[1]))[0] == 405
    assert c('POST', urls[1] + '/lines/lines', {}, headers=tag(urls[1]))[0] == 405
    assert c('GET', urls[1] + '/related/measures')[0] == 404 and c('GET', urls[1] + '/related/void')[0] == 404
    schema = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'numberedquote')
    assert [item['name'] for item in schema['relations']] == ['numberedquote.revises'] and len(schema['measures']) == 5
    # Numbering the revision is reflected in the next read.
    copy_url = f"{base}/{copy['id']}"
    assert c('POST', copy_url + '/moves/issued', {}, headers=tag(copy_url))[1]['number'] == 'NQ-003'
    assert c('GET', base + '/measures')[1]['measures']['numbered'] == 3
