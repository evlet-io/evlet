"""Meta.focus on models and the focus option on slips: checked at registration, shown in the schema."""

import pytest

from evlet import (Application, Child, Currency, Database, Link, LocalAuth, Model, Registry, Rule, Sequence, Slip, Table, Text)
from test_business import PASSWORD, client, http
from test_rules_types import add_user

ALL = Rule(('read', 'create', 'update', 'delete'))


class Line(Child):
    item = Text(default='')


def probe(focus):
    """A model with one field of each kind Meta.focus refuses, and Meta.focus set to `focus`."""
    class Probe(Model):
        customer = Text(default='')
        total = Currency(compute='1')
        locked = Text(read_only=True)
        secret = Text(hidden=True)
        scratch = Text(stored=False)
        number = Sequence('P-{n:03}')
        lines = Table(Line)
        Meta = type('Meta', (), {'focus': focus})
    return Registry(Probe)


def test_meta_focus_is_checked_at_registration():
    assert probe('customer')
    cases = [
        ('missing', "Probe.Meta.focus: Probe has no field 'missing'"),
        ('total', 'Probe.Meta.focus: total is calculated; name a field people fill in'),
        ('locked', 'Probe.Meta.focus: locked is read only'),
        ('secret', 'Probe.Meta.focus: secret is hidden'),
        ('scratch', 'Probe.Meta.focus: scratch is not stored'),
        ('number', 'Probe.Meta.focus: number is numbered by a Sequence'),
        ('lines', 'Probe.Meta.focus: lines is a table; name a field'),
        ('', 'Probe.Meta.focus: name one field'),
        (('customer',), 'Probe.Meta.focus: name one field'),
    ]
    for focus, message in cases:
        with pytest.raises(ValueError) as error:
            probe(focus)
        assert str(error.value).startswith(message), (focus, error.value)


class Ticket(Model):
    permissions = {'admin': ALL, 'editor': Rule(('read', 'create'), read_fields=('subject',)), 'viewer': Rule(('read',))}
    subject = Text(default='')
    owner = Text(default='')

    class Meta:
        focus = 'owner'


class Note(Model):
    permissions = {'admin': ALL}
    body = Text(default='')


class OpenTicket(Slip):
    title = 'Open a ticket'
    menu = True
    focus = 'subject'
    subject = Text(required=True)
    tag = Text(read_only=True)

    def hand_in(self):
        return None


class PlainSlip(Slip):
    menu = True
    subject = Text()

    def hand_in(self):
        return None


@pytest.fixture
def app(tmp_path):
    database = Database(tmp_path / 'focus.sqlite3', Registry(Ticket, Note))
    application = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False,
                                                        allow_setup=True), slips=(OpenTicket, PlainSlip))
    application.initialize()
    assert http(application, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    add_user(application, 'editor')
    add_user(application, 'viewer')
    return application


def model_entry(app, role, name):
    return next(model for model in client(app, role)('GET', '/api/schema')[1]['models'] if model['name'] == name)


def test_schema_shows_focus_only_when_set_and_readable(app):
    assert model_entry(app, 'admin', 'ticket')['focus'] == 'owner'
    assert 'focus' not in model_entry(app, 'admin', 'note')
    # The editor cannot read owner, so it is left out, as key_fields leave out unreadable fields.
    assert 'focus' not in model_entry(app, 'editor', 'ticket')
    # The viewer can read owner but cannot write it: display only, so it stays and the browser falls back.
    assert model_entry(app, 'viewer', 'ticket')['focus'] == 'owner'


def test_slip_focus_is_checked_and_shown_on_the_surface(app, tmp_path):
    c = client(app)
    status, surface, _ = c('POST', '/api/_slips/open_ticket/present', {})
    assert status == 200, surface
    assert surface['focus'] == 'subject'
    status, surface, _ = c('POST', '/api/_slips/plain_slip/present', {})
    assert status == 200 and 'focus' not in surface
    database = Database(tmp_path / 'slip-focus.sqlite3', Registry(Ticket))

    def register(focus, **fields):
        attributes = {'subject': Text(), 'hand_in': lambda self: None, 'focus': focus, **fields}
        return Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)),
                           slips=(type('Probe', (Slip,), attributes),))

    assert register('subject')
    cases = [
        (('missing', {}), "Probe.focus: Probe has no field 'missing'"),
        (('tag', {'tag': Text(read_only=True)}), 'Probe.focus: tag is read only'),
        (('total', {'total': Currency(compute='1', stored=False)}), 'Probe.focus: total is calculated'),
        (('secret', {'secret': Text(hidden=True)}), 'Probe.focus: secret is hidden'),
        (('lines', {'lines': Table(Line)}), 'Probe.focus: lines is a table'),
        (('customer', {'customer': Link(Ticket, read_only=True)}), 'Probe.focus: customer is read only'),
        ((3, {}), 'Probe.focus: name one field'),
    ]
    for (focus, fields), message in cases:
        with pytest.raises(ValueError) as error:
            register(focus, **fields)
        assert str(error.value).startswith(message), (focus, error.value)
