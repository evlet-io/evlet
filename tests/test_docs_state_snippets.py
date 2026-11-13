"""The Python shown next to the State display examples in Docs (frontend/src/evlet/app/docs.ts) runs on the real API."""
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
