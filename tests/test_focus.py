"""Meta.focus on models and the focus option on slips: checked at registration, shown in the schema."""

import pytest

from evlet import (Application, Child, Currency, Database, Link, LocalAuth, Model, Registry, Rule, Prompt, Sequence, Table, Text)
from evlet.prompts import check, schema
from support import PASSWORD, client, http
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


class OpenTicket(Prompt):
    class Meta:
        focus = 'subject'
    subject = Text(required=True)
    tag = Text(read_only=True)


class PlainPrompt(Prompt):
    subject = Text()


@pytest.fixture
def app(tmp_path):
    database = Database(tmp_path / 'focus.sqlite3', Registry(Ticket, Note))
    application = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False,
                                                        allow_setup=True))
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


def test_prompt_focus_is_checked_and_shown_on_the_surface(app):
    assert schema(app.registry, OpenTicket, 'open')[0]['focus'] == 'subject'
    assert 'focus' not in schema(app.registry, PlainPrompt, 'plain')[0]

    def register(focus, **fields):
        return check(type('Probe', (Prompt,), {'subject': Text(), 'Meta': type('Meta', (), {'focus': focus}), **fields}))

    assert register('subject')
    cases = [
        (('missing', {}), "Probe.Meta.focus: Probe has no field 'missing'"),
        (('tag', {'tag': Text(read_only=True)}), 'Probe.Meta.focus: tag is read only'),
        (('total', {'total': Currency(compute='1', stored=False)}), 'Probe.Meta.focus: total is calculated'),
        (('secret', {'secret': Text(hidden=True)}), 'Probe.Meta.focus: secret is hidden'),
        (('lines', {'lines': Table(Line)}), 'Probe.Meta.focus: lines is a table'),
        (('customer', {'customer': Link(Ticket, read_only=True)}), 'Probe.Meta.focus: customer is read only'),
        ((3, {}), 'Probe.Meta.focus: name one field'),
    ]
    for (focus, fields), message in cases:
        with pytest.raises(ValueError) as error:
            register(focus, **fields)
        assert str(error.value).startswith(message), (focus, error.value)
