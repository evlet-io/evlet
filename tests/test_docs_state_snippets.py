"""The State Python shown in Docs (frontend/src/evlet/app/docs.ts) runs on the real API: the State display examples and
the Screens and routes invoice. No Docs snippet uses the State API 0.4.6 removed."""
import re
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'evlet' / 'app' / 'docs.ts'


def snippets():
    block = SOURCE.read_text().split('const STATE_CODE = {', 1)[1].split('\n};', 1)[0]
    return dict(re.findall(r'^  (\w+): `(.*?)`,$', block, re.S | re.M))


def test_every_snippet_is_found():
    assert set(snippets()) == {'deal', 'bar', 'none', 'invoice', 'expense'}


@pytest.mark.parametrize('name', ['deal', 'invoice', 'expense'])
def test_model_snippet_declares_its_state(name):
    scope = {}
    exec(compile(snippets()[name], f'docs:{name}', 'exec'), scope)
    model = next(value for value in scope.values() if isinstance(value, type) and getattr(value, '__state__', None))
    field = model.__declared_fields__[model.__state__]
    assert field.show in ('badge', 'bar')


@pytest.mark.parametrize('show', ['bar', 'none'])
def test_show_snippets_are_the_deal_with_another_show(show):
    assert f'show="{show}"' in snippets()[show]
    scope = {}
    exec(compile(snippets()['deal'].replace('show="badge"', f'show="{show}"'), f'docs:{show}', 'exec'), scope)
    assert scope['Deal'].__declared_fields__['stage'].show == show


def test_screens_snippet_declares_its_state():
    from evlet import Child, Currency, Model, Text

    class Customer(Model):
        name = Text(required=True)

    class InvoiceLine(Child):
        amount = Currency(default=0)

    code = re.search(r'^const screensCode = `(.*?)`;$', SOURCE.read_text(), re.S | re.M).group(1)
    scope = {'Customer': Customer, 'InvoiceLine': InvoiceLine}
    exec(compile(code, 'docs:screens', 'exec'), scope)
    field = scope['Invoice'].__declared_fields__['status']
    assert list(field.steps) == ['draft', 'issued', 'paid']


REMOVED = ('Move(', 'confirmable', 'locked=', 'voidable=', 'before_close', 'changed(', 'moves=', 'final=(')


def test_no_docs_snippet_uses_the_removed_state_api():
    for code in re.findall(r'^const \w+Code = `(.*?)`;$', SOURCE.read_text(), re.S | re.M) + list(snippets().values()):
        assert not [name for name in REMOVED if name in code], code[:80]
