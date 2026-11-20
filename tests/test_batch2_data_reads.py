"""Read-only contexts read a snapshot without the write turn, so a long report, export, preview or print
never holds up a save (business.context(readonly=True), Database.read_transaction(), docs/performance.md)."""

import sqlite3
import threading
import time

import pytest

from evlet import Database, Model, Registry, Rule, Text, User
from evlet.auth import AccessError
from evlet import qb
from test_rules_types import application

ALL = ('read', 'create', 'update', 'delete')


class Memo(Model):
    permissions = {'admin': Rule(ALL), 'viewer': Rule(('read',))}
    title = Text(required=True)


@pytest.fixture
def app(tmp_path):
    return application(tmp_path, Memo)


ADMIN = User(1, 'admin', 'admin')


def test_a_long_read_only_context_does_not_block_a_concurrent_save(app):
    business = app.business
    business.database.write_wait = 2
    with business.context(ADMIN) as context:
        context.create(Memo, title='before')
    reading, release = threading.Event(), threading.Event()
    seen = {}

    def report():
        with business.context(ADMIN, readonly=True) as context:
            seen['first'] = [memo['title'] for memo in context.list(Memo)['rows']]
            reading.set()
            # The "long report": still inside its read transaction while someone else saves.
            release.wait(10)
            seen['second'] = [memo['title'] for memo in context.list(Memo)['rows']]

    thread = threading.Thread(target=report)
    thread.start()
    try:
        assert reading.wait(10)
        started = time.monotonic()
        with business.context(ADMIN) as context:
            context.create(Memo, title='during')
        assert time.monotonic() - started < 1.5
    finally:
        release.set()
        thread.join(10)
    # One consistent snapshot for the whole read; the save is there afterwards.
    assert seen['first'] == seen['second'] == ['before']
    with business.context(ADMIN, readonly=True) as context:
        assert sorted(memo['title'] for memo in context.list(Memo)['rows']) == ['before', 'during']


def test_a_read_only_context_refuses_writes_and_keeps_permissions(app):
    business = app.business
    with business.context(ADMIN, readonly=True) as context:
        with pytest.raises(AccessError):
            context.create(Memo, title='x')
        # Even trusted code holding the session cannot write through a read transaction.
        with pytest.raises(sqlite3.OperationalError):
            context._tx.connection.run(qb.insert('memo', title='x'))
    viewer = User(2, 'viewer', 'viewer')
    with business.context(viewer, readonly=True) as context:
        with pytest.raises(AccessError):
            context.delete(Memo, 1)
    # The pooled connection is writable again for the next writer.
    with business.context(ADMIN) as context:
        assert context.create(Memo, title='after').title == 'after'


def test_read_transaction_on_a_bare_database(tmp_path):
    database = Database(tmp_path / 'db.sqlite3', Registry(Memo))
    database.initialize()
    database.create(Memo, title='one')
    with database.read_transaction() as session:
        assert [memo.title for memo in session.list(Memo)] == ['one']
        assert database.create(Memo, title='two').title == 'two'
        assert [memo.title for memo in session.list(Memo)] == ['one']
