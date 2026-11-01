"""Keep recorded UI contracts equal to real Python HTTP responses.

The UI framework's browser tests replay frontend/src/evlet/tests/api-fixture.json and
screens-fixture.json. tests/fixtures holds the Python page (/api/ui) and record contracts,
which the browser UI no longer renders but which stay part of the HTTP API.
"""

import json
from pathlib import Path
import re

from scripts.generate_ui_fixtures import generate


def test_page_and_record_fixtures_match_http_contracts():
    fixtures = Path(__file__).resolve().parents[1] / 'tests/fixtures'
    for name, actual in generate().items():
        assert json.loads((fixtures / name).read_text()) == actual, name


JOURNAL_TIME = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z')


def without_journal_times(value):
    """Journal entries carry the moment they were written; everything else must match exactly."""
    if isinstance(value, dict):
        return {key: 'at' if key == 'at' and isinstance(item, str) and JOURNAL_TIME.fullmatch(item) else without_journal_times(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [without_journal_times(item) for item in value]
    return value


def test_ui_framework_fixtures_match_http_contracts():
    """The production UI's tests replay these recorded responses; they must stay what the server sends."""
    from scripts.generate_ui_framework_fixtures import generate as framework
    fixtures = Path(__file__).resolve().parents[1] / 'frontend/src/evlet/tests'
    for name, actual in framework().items():
        assert without_journal_times(json.loads((fixtures / name).read_text())) == without_journal_times(actual), name


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
