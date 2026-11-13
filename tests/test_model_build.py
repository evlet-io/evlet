"""Model build steps: higher layers register the parts of Model and Registry construction they own."""

import subprocess
import sys
import textwrap

import pytest

from evlet import Integer, Journal, Model, Registry, Text, models
from test_effects import Account, Invoice, LedgerEntry, StockMove

ORDER = ('ledger_declared', 'ledger_class', 'statemanager', 'journaled',
         'meaning', 'guards', 'kept_plan', 'sequences', 'namings', 'measures', 'cards', 'effects', 'posted_reads',
         'journals', 'api', 'ledger_schema')


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for name, function in list(models.BUILD_STEPS.items()):
        def recorded(*args, name=name, function=function, **kwargs):
            seen.append(name)
            return function(*args, **kwargs)
        monkeypatch.setitem(models.BUILD_STEPS, name, recorded)
    return seen


def first_calls(names):
    return list(dict.fromkeys(names))


def test_the_build_order_is_fixed_and_every_step_is_registered():
    assert models.BUILD_ORDER == ORDER
    assert set(models.BUILD_STEPS) == set(ORDER)


def test_class_steps_run_in_order(calls):
    class Entry(Model):
        class Meta:
            ledger = True
            timestamps = 'created'
        amount = Integer()

    assert calls == ['ledger_declared', 'ledger_class', 'statemanager']
    assert set(Entry.__fields__) == {'amount', 'created_at'}
    calls.clear()

    class Noted(Model):
        class Meta:
            journal = Journal()
        title = Text()

    assert calls == ['ledger_declared', 'statemanager', 'journaled']
    assert Noted.__declaration_mode__ is True


def test_registry_steps_run_in_order(calls):
    registry = Registry(Account, LedgerEntry, StockMove, Invoice)
    registry.schema()
    # Generated Table row classes run the class steps too; the Registry's own steps follow ORDER.
    assert [name for name in first_calls(calls) if name in ORDER[4:]] == list(ORDER[4:])
    assert registry.effects and registry.posted_reads and registry.kept_plans and registry.journals is not None


def test_an_unregistered_step_says_what_to_do(monkeypatch):
    monkeypatch.delitem(models.BUILD_STEPS, 'meaning')
    with pytest.raises(RuntimeError, match='meaning is not registered: import evlet'):
        Registry(Account)
    with pytest.raises(ValueError, match='Unknown model build step'):
        models.build_step('unknown')


def test_a_model_defined_straight_after_import_evlet_gets_every_step():
    script = textwrap.dedent('''
        import evlet
        from evlet import models
        assert set(models.BUILD_STEPS) == set(models.BUILD_ORDER), set(models.BUILD_ORDER) - set(models.BUILD_STEPS)
        seen = []
        for name, function in list(models.BUILD_STEPS.items()):
            models.BUILD_STEPS[name] = lambda *args, name=name, function=function: (seen.append(name), function(*args))[1]

        class Entry(evlet.Model):
            class Meta:
                ledger = True
            amount = evlet.Integer()

        class Note(evlet.Model):
            class Meta:
                journal = evlet.Journal()
            title = evlet.Text()
            status = evlet.State(evlet.State.Step('draft'), evlet.State.Step('done'),
                                 rules=(evlet.State.Rule('draft', 'done', 'Done'),))

        evlet.Registry(Entry, Note).schema()
        missing = set(models.BUILD_ORDER) - set(seen) - {'kept_plan', 'posted_reads'}  # need a plan or effects
        assert not missing, missing
        print('ok')
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == 'ok', result.stderr
