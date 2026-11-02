"""The lifecycle contract against a real PostgreSQL database, with no driver mocks."""
import pytest
import sqlite3
from evlet import Application, LocalAuth, PostgreSQLDatabase, Registry
from test_postgres import pg_dsn
import test_lifecycle as lifecycle


@pytest.fixture
def app(pg_dsn):
    database = PostgreSQLDatabase(pg_dsn, Registry(lifecycle.Invoice, lifecycle.Posting))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    return app


@pytest.mark.parametrize('check', [
    lifecycle.test_identity_revisions_snapshots_and_history,
    lifecycle.test_stable_child_ids_and_diff,
    lifecycle.test_concurrent_confirmation_executes_once,
    lifecycle.test_locking_delete_and_raw_write_guards,
    lifecycle.test_application_guards_and_approval_separation,
])
def test_postgres_lifecycle_contract(app, check):
    check(app)


@pytest.mark.parametrize('gate,operation', [('fail_confirm', 'confirm'), ('fail_cancel', 'cancel')])
def test_postgres_rollback(app, gate, operation):
    lifecycle.test_handler_failure_rollback_even_when_caught(app, gate, operation)


@pytest.mark.parametrize('statement', ["UPDATE evlet_document_revisions SET data='{}'", 'DELETE FROM evlet_document_revisions',
                                       'TRUNCATE evlet_document_revisions'])
def test_postgres_immutable_snapshot_guards(app, statement):
    with app.business.context(lifecycle.ADMIN) as context:
        record_id = lifecycle.create(context)
        context.confirm(lifecycle.Invoice, record_id)
    with pytest.raises(sqlite3.OperationalError, match='PostgreSQL operation failed'):
        with app.database.transaction() as tx:
            tx.connection.execute(statement)
    with app.business.context(lifecycle.ADMIN) as context:
        assert context.snapshot(lifecycle.Invoice, record_id, revision=1)['document']['customer'] == 'A'
