from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import sqlite3

import pytest

from evlet import ConflictError, Database, Model, NotFoundError, Registry, ValidationError
from evlet.identity import RecordId
from examples.playground.models import Customer, Invoice, Payment, registry


def test_playground_round_trip_survives_reopening(database):
    customer = database.create(Customer, customer_name="Acme", email="hello@example.com")
    invoice = database.create(Invoice, customer=customer.id, reference="INV-1", total="100.10")
    payment = database.create(Payment, invoice=invoice.id, reference="PAY-1", amount="100.10")
    reopened = Database(database.path, registry)
    reopened.initialize()  # repeatable initialization preserves data
    assert reopened.get(Customer, customer.id).customer_name == "Acme"
    assert reopened.get(Invoice, invoice.id).total == Decimal("100.10")
    assert reopened.get(Payment, payment.id).to_dict() == {
        "id": payment.id, "invoice": invoice.id, "reference": "PAY-1", "currency": "AED", "amount": "100.10", "received_at": None,
        "name": None, "title": str(payment.id),
    }
    with sqlite3.connect(database.path) as conn:
        assert conn.execute("SELECT total, typeof(total) FROM invoice").fetchone() == (10010, "integer")
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_transactions_commit_and_rollback_as_a_unit(database):
    with database.transaction() as tx:
        customer = tx.create(Customer, customer_name="Committed")
        tx.create(Invoice, customer=customer.id, reference="COMMITTED", total="1.00")
    with pytest.raises(RuntimeError):
        with database.transaction() as tx:
            customer = tx.create(Customer, customer_name="Rolled back")
            tx.create(Invoice, customer=customer.id, reference="ROLLED-BACK", total="1.00")
            raise RuntimeError("Simulated business failure")
    assert [record.customer_name for record in database.list(Customer)] == ["Committed"]
    assert [record.reference for record in database.list(Invoice)] == ["COMMITTED"]


def test_foreign_keys_uniqueness_and_failure_rollback(database):
    with pytest.raises(ConflictError):
        database.create(Invoice, customer=999, reference="ORPHAN", total="1.00")
    customer = database.create(Customer, customer_name="Acme")
    database.create(Invoice, customer=customer.id, reference="DUPLICATE", total="1.00")
    with pytest.raises(ConflictError):
        with database.transaction() as tx:
            tx.create(Customer, customer_name="Must roll back")
            tx.create(Invoice, customer=customer.id, reference="DUPLICATE", total="2.00")
    assert len(database.list(Customer)) == 1
    assert len(database.list(Invoice)) == 1


def test_sql_values_are_parameterized_and_pagination_is_bounded(database):
    malicious = "Robert'); DROP TABLE customer;--"
    first = database.create(Customer, customer_name=malicious)
    second = database.create(Customer, customer_name="Second")
    assert database.get(Customer, first.id).customer_name == malicious
    assert database.list(Customer, limit=1)[0].id == second.id
    assert database.list(Customer, limit=1, offset=1)[0].id == first.id
    assert database.list(Customer, offset=2) == []
    for kwargs in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": 2**63}):
        with pytest.raises(ValidationError):
            database.list(Customer, **kwargs)
    with pytest.raises(NotFoundError):
        database.get(Customer, 999)
    with pytest.raises(ValidationError):
        database.get(Customer, True)


def test_short_concurrent_transactions_preserve_records(database):
    def write(index):
        return database.create(Customer, customer_name=f"Customer {index}").id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(write, range(12)))
    assert len(set(ids)) == 12
    assert len(database.list(Customer)) == 12


def test_empty_model_and_unregistered_model(tmp_path):
    class Empty(Model):
        pass
    db = Database(tmp_path / "empty.sqlite3", Registry(Empty))
    db.initialize()
    empty = db.create(Empty)
    assert empty.to_dict() == {"id": empty.id, "name": None, "title": str(empty.id)} and isinstance(empty.id, RecordId)
    with pytest.raises(ValueError, match="registry"):
        db.create(Customer, customer_name="No")
    with pytest.raises(ValueError, match="temporary file"):
        Database(":memory:", registry)


def test_field_named_storage_name_is_data_not_the_table_name(tmp_path):
    from evlet import Text
    from evlet.ui.expressions import model_key
    class Bin(Model):
        storage_name = Text()
    db = Database(tmp_path / "bin.sqlite3", Registry(Bin))
    db.initialize()
    assert model_key(Bin) == db.registry.name_for(Bin) == "bin"
    created = db.create(Bin, storage_name="cold")
    assert db.get(Bin, created.id).storage_name == "cold"
