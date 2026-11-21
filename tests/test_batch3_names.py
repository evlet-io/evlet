"""Names renamed in 0.4.x (docs/api-stability.md): the new name is the class, the old one warns once and is the same object."""

from decimal import Decimal as D
import subprocess
import sys
import warnings

import pytest

import evlet
from evlet import AnyLink, Application, Currency, Database, Decimal, DevApp, Model, Registry, Rule, State
from evlet.auth import LocalAuth
from evlet.calc import F, Document
from support import PASSWORD, client, http

RENAMED = (('App', 'DevApp'), ('Reference', 'AnyLink'))


def test_new_names_are_exported_and_old_names_are_not_listed():
    for old, new in RENAMED:
        assert new in evlet.__all__ and old not in evlet.__all__
    assert State.Move is evlet.declarations.Move
    assert isinstance(AnyLink(), evlet.fields.Field)
    assert issubclass(DevApp, Application)


def test_new_names_raise_no_warning():
    code = '''
import warnings
warnings.simplefilter("error")
from evlet import AnyLink, DevApp, State
from evlet.ui.application import DevApp as Same
assert Same is DevApp and State.Move("draft", "done").to == "done"
'''
    subprocess.run([sys.executable, '-c', code], check=True, timeout=30)


@pytest.mark.parametrize('expression', ['evlet.App', 'evlet.Reference', 'evlet.State.Rule', 'evlet.ui.application.App'])
def test_old_names_warn_once_and_are_the_same_object(expression):
    new = {'evlet.App': 'evlet.DevApp', 'evlet.Reference': 'evlet.AnyLink', 'evlet.State.Rule': 'evlet.State.Move',
           'evlet.ui.application.App': 'evlet.DevApp'}[expression]
    code = f'''
import warnings
import evlet, evlet.ui.application
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    first, second = {expression}, {expression}
assert first is second is {new}, (first, {new})
found = [w for w in caught if issubclass(w.category, DeprecationWarning)]
assert len(found) == 1, [str(w.message) for w in caught]
assert "{new.rsplit('.', 1)[-1]}" in str(found[0].message) and found[0].filename == "<string>", found[0]
'''
    subprocess.run([sys.executable, '-c', code], check=True, timeout=30)


def test_from_import_of_an_old_name_still_works():
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        from evlet import App, Reference
        rule = State.Rule('draft', 'done')
    assert App is DevApp and Reference is AnyLink and isinstance(rule, State.Move)
    assert isinstance(AnyLink(), Reference)


def test_pytest_does_not_turn_deprecation_warnings_into_errors():
    # Tests keep using the old names; a filter that errors on DeprecationWarning would break them.
    assert not any(action == 'error' and issubclass(DeprecationWarning, category)
                   for action, _message, category, _module, _line in warnings.filters)


def test_makemigration_writes_the_new_field_name():
    from evlet.migrations import field_code
    field = AnyLink(index=True)
    field.name = 'source'
    code, names = field_code(field)
    assert code.startswith('AnyLink(') and names == {'AnyLink'}


# DX-13: the two formula languages round a tie differently (docs/finite-calculations.md, Two formula languages).

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class OldFormula(Model):
    permissions = FULL
    qty = Decimal(scale=3, default='1')
    rate = Currency(default='0')
    total = Currency(compute='qty * rate')


class NewFormula(Model):
    permissions = FULL
    qty = Decimal(scale=3, default='1')
    rate = Currency(default='0')
    total = Currency(compute=F('qty') * F('rate'))


def test_string_formulas_round_half_even_and_finite_expressions_half_up(tmp_path):
    database = Database(tmp_path / 'rounding.sqlite3', Registry(OldFormula, NewFormula))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False,
                                               allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app, 'admin')
    totals = []
    for model in ('oldformula', 'newformula'):
        status, record, _ = admin('POST', f'/api/{model}', {'qty': '1.005', 'rate': '1'})
        assert status == 201, record
        totals.append(D(record['total']))
    assert totals == [D('1.00'), D('1.01')]
    assert Document(database.registry.plans[NewFormula], {'qty': '1.005', 'rate': '1'}).snapshot()['total'] == '1.01'
