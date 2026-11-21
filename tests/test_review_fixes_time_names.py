"""Review fixes: print and Sequence dates in the instance timezone, job backoff from the failure,
attachment names without bidi or control characters, and clean CLI errors. Real SQLite throughout."""

import base64
import getpass
import time
from types import SimpleNamespace

import pytest

from evlet import Database, Model, Registry, Sequence, Text
from evlet.jobs import JobQueue, Task
from evlet.printing import Formats
from examples.playground.models import Customer
from support import client
from test_rules_types import ALL
from test_cli_lifecycle import environment_stub, project  # noqa: F401


DESIGN = SimpleNamespace(date_format='%d-%m-%Y', currency='AED')


def test_printed_datetimes_use_the_instance_timezone():
    utc = Formats(DESIGN, lambda text: text)
    dubai = Formats(DESIGN, lambda text: text, zone='Asia/Dubai')
    assert utc.date('2026-11-20T21:30:00.000000Z') == '<bdi dir="ltr">20-11-2026 21:30</bdi>'
    assert dubai.date('2026-11-20T21:30:00.000000Z') == '<bdi dir="ltr">21-11-2026 01:30</bdi>'
    # A Date has no time of day and never moves.
    assert dubai.date('2026-11-20') == '<bdi dir="ltr">20-11-2026</bdi>'


class Docket(Model):
    permissions = ALL
    code = Sequence('D{year}{month}-{n:03}')
    title = Text(default='')


def test_raw_session_create_dates_a_sequence_in_the_session_timezone(tmp_path, monkeypatch):
    from evlet import database
    database_ = Database(tmp_path / 'db.sqlite3', Registry(Docket))
    database_.initialize()
    database_.timezone = 'Pacific/Kiritimati'
    zones = []
    monkeypatch.setattr(database, 'today', lambda zone: zones.append(zone) or '2027-01-01')
    with database_.transaction() as tx:
        assert tx.create(Docket, title='raw').code == 'D202701-001'
    assert zones and set(zones) == {'Pacific/Kiritimati'}


def test_retry_backoff_counts_from_the_failure_not_the_run_start(secured, monkeypatch):  # noqa: F811
    clock = {'offset': 0}
    real = time.monotonic
    monkeypatch.setattr(time, 'monotonic', lambda: real() + clock['offset'])

    def slow_then_fail(tx, context, payload):
        clock['offset'] += 100  # the run took 100 seconds before it failed
        raise RuntimeError('remote timed out')

    jobs = JobQueue(secured.database, [Task('work', slow_then_fail, retry_delay=2)], events=secured.business.events)
    jobs.initialize()
    now = int(time.time())
    with jobs.database.transaction() as tx:
        receipt = jobs.enqueue(tx, 'work', {}, run_at=now)
    assert jobs.run_one(now=now)
    with jobs.database._connection() as db:
        row = db.execute('SELECT state, run_at FROM evlet_jobs WHERE id=?', (receipt.id,)).fetchone()
    assert row[0] == 'queued' and row[1] == now + 100 + 2
    assert secured.database.count(Customer) == 0


@pytest.mark.parametrize('name', ['invoice‮fdp.exe', 'a⁦b.pdf', 'zero​width.txt', 'del\x7f.txt', 'c1\x85.txt'])
def test_attachment_names_refuse_bidi_format_and_control_characters(secured, name):  # noqa: F811
    c = client(secured)
    project = c('POST', '/api/project', {'title': 'Files'})[1]
    path = f'/api/project/{project["id"]}/attachments'
    content = base64.b64encode(b'x').decode()
    assert c('POST', path, {'name': name, 'content': content})[0] == 422
    assert c('POST', path, {'name': 'Rechnung März.pdf', 'content': content})[0] == 201


def test_set_password_without_input_fails_cleanly(project, monkeypatch, capsys):
    from evlet.cli.install import install
    from evlet.cli.main import main
    install(project, 'acme', app_name='notes', trust=True)

    def ended(prompt=''):
        raise EOFError
    monkeypatch.setattr(getpass, 'getpass', ended)
    with pytest.raises(SystemExit) as exit_:
        main(['users', 'set-password', 'acme', 'admin', '--project', str(project)])
    assert exit_.value.code == 1
    assert 'Input ended before the command finished. Nothing was changed.' in capsys.readouterr().err


def test_dev_watch_is_accepted_and_documented_as_unneeded():
    from evlet.cli.main import parser
    args = parser().parse_args(['dev', '--watch'])
    assert args.watch is True
    help_text = parser()._subparsers._group_actions[0].choices['dev'].format_help()
    assert 'always reloads' in help_text
