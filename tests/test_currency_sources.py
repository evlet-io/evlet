"""Currency fields: where the currency code comes from, checked when declared and resolved per person in /api/schema."""

import pytest

from evlet import Currency, Link, Model, Percent, Registry, Select, Text
from test_business import client, hashed, secured  # noqa: F401


class Company(Model):
    name = Text(default='')
    company_currency = Select(['AED', 'USD'], default='AED')
    founded = Text(default='')


class Bill(Model):
    company = Link(Company)
    code = Text(default='USD')
    fixed = Currency(currency='AED')
    own = Currency(currency='code')
    fetched = Currency(currency='company.company_currency')
    shared = Currency(currency='settings.billing.currency')
    plain = Currency()


def test_currency_takes_four_explicit_sources():
    assert Currency(currency='AED').currency_source() == {'code': 'AED'}
    assert Currency(currency='code').currency_source() == {'field': 'code'}
    assert Currency(currency='company.company_currency').currency_source() == {'link': 'company', 'field': 'company_currency'}
    assert Currency(currency='settings.billing.currency').currency_source() == {'setting': {'app': 'billing', 'key': 'currency'}}
    assert Currency().currency_source() is None
    for bad in ('aed!', 'a.b.c', 'settings.Billing.currency', 'company.', 7):
        with pytest.raises(ValueError, match='currency'):
            Currency(currency=bad)


def test_registry_checks_that_a_currency_source_names_a_text_or_select_field():
    Registry(Company, Bill)

    class NotALink(Model):
        code = Text(default='')
        amount = Currency(currency='code.name')
    with pytest.raises(ValueError, match='must start with a Link field'):
        Registry(NotALink)

    class Missing(Model):
        company = Link(Company)
        amount = Currency(currency='company.nothing')
    with pytest.raises(ValueError, match='company.nothing is not one on Company'):
        Registry(Company, Missing)

    class Number(Model):
        rate = Percent()
        amount = Currency(currency='rate')
    with pytest.raises(ValueError, match='Text or Select'):
        Registry(Number)


def test_ui_schema_sends_a_code_or_the_source():
    fields = {field['name']: field for field in Registry(Company, Bill).ui_schema()['models'][1]['fields']}
    assert fields['fixed']['currency'] == 'AED' and 'currency_source' not in fields['fixed']
    assert fields['own']['currency_source'] == {'field': 'code'}
    assert fields['fetched']['currency_source'] == {'link': 'company', 'field': 'company_currency'}
    assert fields['shared']['currency_source'] == {'setting': {'app': 'billing', 'key': 'currency'}}
    assert 'currency' not in fields['plain'] and 'currency_source' not in fields['plain']


def fields_of(c, model):
    schema = c('GET', '/api/schema')[1]
    return {field['name']: field for field in next(entry for entry in schema['models'] if entry['name'] == model)['fields']}


def test_the_playground_schema_resolves_each_source_for_the_person(secured):
    editor = client(secured, 'editor')
    invoice, payment, project = fields_of(editor, 'invoice'), fields_of(editor, 'payment'), fields_of(editor, 'project')
    # Fetched through a Link: the target model is named so the browser can read the linked record.
    assert invoice['total']['currency_source'] == {'link': 'customer', 'field': 'currency', 'model': 'customer'}
    assert invoice['tax_rate']['suffix'] == '%' and invoice['tax_rate']['label'] == 'Tax rate'
    assert payment['amount']['currency_source'] == {'field': 'currency'}
    # A setting is read on the server and sent as the code; it follows the setting when it changes.
    assert project['budget']['currency'] == 'AED' and 'currency_source' not in project['budget']
    assert editor('PATCH', '/api/_settings/app/projects', {'values': {'currency': 'USD'}})[0] == 200
    assert fields_of(editor, 'project')['budget']['currency'] == 'USD'
    assert fields_of(client(secured, 'viewer'), 'project')['budget']['currency'] == 'USD'


def test_a_source_the_person_cannot_read_is_left_out(secured):
    business = secured.business
    original = business.policy.fields

    def fields(user, model, write=False):
        names = original(user, model, write=write)
        return {name for name in names if not (model.__name__ == 'Customer' and name == 'currency')}
    business.policy.fields = fields
    try:
        invoice = fields_of(client(secured, 'editor'), 'invoice')
    finally:
        business.policy.fields = original
    assert 'currency_source' not in invoice['total'] and 'currency' not in invoice['total']


def test_an_unknown_setting_falls_back_without_a_code(secured, caplog):
    project = secured.business.registry.models['project']
    budget = project.__declared_fields__['budget']
    budget.currency = 'settings.projects.missing'
    try:
        fields = fields_of(client(secured, 'editor'), 'project')
    finally:
        budget.currency = 'settings.projects.currency'
    assert 'currency' not in fields['budget'] and 'no setting projects.missing' in caplog.text
