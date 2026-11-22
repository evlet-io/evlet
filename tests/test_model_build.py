"""Model build steps: evlet/__init__.py gives core the parts of Model and Registry construction higher layers own."""

import dataclasses
import subprocess
import sys
import textwrap

import pytest

from evlet import Activity, Integer, Model, Registry, Text, models
from test_release_postings import Account, Invoice, LedgerEntry, StockMove

ORDER = ('ledger_declared', 'ledger_class', 'statemanager', 'activity_declared',
         'meaning', 'guards', 'kept_plan', 'sequences', 'namings', 'measures', 'cards', 'posted_reads',
         'activities', 'api', 'ledger_schema')


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def recorded(name, function):
        def call(*args, **kwargs):
            seen.append(name)
            return function(*args, **kwargs)
        return call
    wrapped = {name: recorded(name, getattr(models.STEPS, name)) for name in ORDER}
    monkeypatch.setattr(models, 'STEPS', dataclasses.replace(models.STEPS, **wrapped))
    return seen


def first_calls(names):
    return list(dict.fromkeys(names))


def test_the_build_order_is_fixed_and_every_step_is_set():
    assert tuple(field.name for field in dataclasses.fields(models.BuildSteps)) == ORDER
    assert all(callable(getattr(models.STEPS, name)) for name in ORDER)
    with pytest.raises(TypeError):
        models.BuildSteps(**{name: print for name in ORDER[:-1]})  # every step is named


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
            activity = Activity()
        title = Text()

    assert calls == ['ledger_declared', 'statemanager', 'activity_declared']
    assert Noted.__declaration_mode__ is True


def test_registry_steps_run_in_order(calls):
    registry = Registry(Account, LedgerEntry, StockMove, Invoice)
    registry.schema()
    # Generated Table row classes run the class steps too; the Registry's own steps follow ORDER.
    assert [name for name in first_calls(calls) if name in ORDER[4:]] == list(ORDER[4:])
    assert registry.posted_reads and registry.kept_plans and registry.activities is not None


@pytest.mark.parametrize('first_import', ['import evlet', 'import evlet.models'])
def test_a_model_defined_straight_after_import_gets_every_step(first_import):
    # Importing a submodule runs evlet/__init__.py first, so the steps are set whichever import comes first.
    script = first_import + textwrap.dedent('''
        import dataclasses
        import evlet
        from evlet import models
        seen = []
        wrap = lambda name, function: lambda *args: (seen.append(name), function(*args))[1]
        models.STEPS = dataclasses.replace(models.STEPS, **{field.name: wrap(field.name, getattr(models.STEPS, field.name))
                                                            for field in dataclasses.fields(models.STEPS)})

        class Entry(evlet.Model):
            class Meta:
                ledger = True
            amount = evlet.Integer()

        class Note(evlet.Model):
            class Meta:
                activity = evlet.Activity()
            title = evlet.Text()
            docstate = evlet.State(evlet.State.Step('draft'), evlet.State.Step('done'),
                                 rules=(evlet.State.Rule('draft', 'done', 'Done'),))

        evlet.Registry(Entry, Note).schema()
        missing = {field.name for field in dataclasses.fields(models.STEPS)} - set(seen) - {'kept_plan', 'posted_reads'}  # need a plan or a release step
        assert not missing, missing
        print('ok')
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == 'ok', result.stderr
