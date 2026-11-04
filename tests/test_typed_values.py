"""The API takes only canonical dates, date-times, times and durations. The forgiving typed forms
(120627, 1.30, 12.35pm) are a browser convenience: over HTTP they are refused, never coerced."""

import pytest

from evlet import Date, DateTime, Duration, Model, Text, Time
from test_business import client
from test_rules_types import ALL, application


class Slot(Model):
    permissions = ALL
    title = Text(default='')
    day = Date()
    starts = DateTime()
    at = Time()
    length = Duration()


@pytest.fixture
def api(tmp_path):
    return client(application(tmp_path, Slot))


def test_canonical_values_are_accepted(api):
    status, slot, _ = api('POST', '/api/slot', {'day': '2027-06-12', 'starts': '2027-06-12T09:30:00+04:00', 'at': '09:30', 'length': 'PT1H30M'})
    assert status == 201, slot
    assert (slot['day'], slot['starts'], slot['at'], slot['length']) == ('2027-06-12', '2027-06-12T05:30:00.000000Z', '09:30:00', 'PT1H30M')
    # Whole seconds are the duration's stored form, so they are canonical too.
    assert api('POST', '/api/slot', {'length': 5400})[1]['length'] == 'PT1H30M'


@pytest.mark.parametrize('field, value', [
    ('day', '2027-02-31'), ('day', '120627'), ('day', '12/06/2027'), ('day', '12-06-2027'), ('day', 'today'), ('day', '2027-6-12'), ('day', 20270612),
    ('starts', '2027-06-12 09:30'), ('starts', '2027-06-12T09:30'), ('starts', '12/06/2027 09:30'), ('starts', '120627 1235'), ('starts', '2027-02-31T09:30:00Z'),
    ('at', '25:00'), ('at', '9am'), ('at', '935'), ('at', '12.35'), ('at', '9:30'), ('at', '12:60'), ('at', '09:30:00+04:00'),
    ('length', '1.62'), ('length', 1.5), ('length', '1.30'), ('length', '1h 30m'), ('length', '90'), ('length', 'PT1.5H'), ('length', -60), ('length', True),
])
def test_other_forms_are_validation_errors(api, field, value):
    status, payload, _ = api('POST', '/api/slot', {field: value})
    assert status == 422 and field in payload['fields'], (field, value, payload)
