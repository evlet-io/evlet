"""Explicitly upgrade the Playground database, preserving a backup first."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from evlet import (AddField, Boolean, Currency, Database, Date, DateTime, Decimal,
                   Link, LongText, Migration, Model, Registry, Select, Text)
from .models import registry

migrations = [Migration('0001_model_engine',
    AddField('customer', 'active', Boolean(required=True, default=True)),
    AddField('customer', 'notes', LongText()),
    AddField('invoice', 'posting_date', Date()),
    AddField('invoice', 'status', Select(['draft', 'sent'], required=True, default='draft')),
    AddField('invoice', 'tax_rate', Decimal(scale=4, minimum='0', maximum='100', default='0.0000')),
    AddField('payment', 'received_at', DateTime()),
)]


def legacy_registry():
    """Frozen Milestone 1 definitions used only to validate a legacy baseline."""
    class Customer(Model):
        name = Text(required=True, max_length=120)
        email = Text(max_length=254)
    class Invoice(Model):
        customer = Link(Customer, required=True)
        reference = Text(required=True, unique=True, max_length=40)
        total = Currency(required=True, minimum='0.01')
    class Payment(Model):
        invoice = Link(Invoice, required=True)
        reference = Text(required=True, unique=True, max_length=40)
        amount = Currency(required=True, minimum='0.01')
    return Registry(Customer, Invoice, Payment)


def upgrade(path):
    path = Path(path)
    if path.exists():
        backup = path.with_name(path.name + '.' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.backup')
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
            has_metadata = source.execute("SELECT 1 FROM sqlite_master WHERE name = 'evlet_schema'").fetchone()
            has_records = source.execute("SELECT 1 FROM sqlite_master WHERE name = 'customer'").fetchone()
        print(f'Backup: {backup}')
        if has_records and not has_metadata:
            Database(path, legacy_registry()).initialize()
    applied = Database(path, registry).migrate(migrations)
    print('Applied: ' + ', '.join(applied) if applied else 'Schema is current.')


if __name__ == '__main__':
    upgrade(os.environ.get('EVLET_DATABASE', str(Path(__file__).parent / '.data' / 'playground.sqlite3')))
