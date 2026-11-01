"""Keep the browser's fixtures equal to real Python HTTP responses."""

import json
from pathlib import Path

from scripts.generate_ui_fixtures import generate


def test_shared_ui_and_record_fixtures_match_http_contracts():
    fixtures = Path(__file__).resolve().parents[1] / 'frontend/src/tests'
    for name, actual in generate().items():
        assert json.loads((fixtures / name).read_text()) == actual, name


def test_metadata_imports_remain_compatible():
    from evlet import metadata
    from evlet.ui import metadata as current
    for name in ('field_schema', 'label_for', 'ui_schema', 'view_for'):
        assert getattr(metadata, name) is getattr(current, name)


def test_default_plural_labels_follow_simple_english_rules():
    from evlet import Model, Text
    from evlet.ui.metadata import plural_for, view_for

    assert [plural_for(label) for label in ('Ledger entry', 'Key', 'Day', 'Address', 'Box', 'Buzz', 'Branch', 'Wish', 'Invoice', 'Order line')] == [
        'Ledger entries', 'Keys', 'Days', 'Addresses', 'Boxes', 'Buzzes', 'Branches', 'Wishes', 'Invoices', 'Order lines']

    class LedgerEntry(Model):
        name = Text()

    class Branch(Model):
        name = Text()

        class Meta:
            plural = 'Offices'

    assert view_for(LedgerEntry)['plural'] == 'Ledger entries'
    assert view_for(Branch)['plural'] == 'Offices'
