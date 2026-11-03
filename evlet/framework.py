"""Versioned framework schema: ordered, checksummed steps that `evlet migrate` runs.

Framework tables (users, sessions, jobs, journal and the rest) are still created and extended
at startup with idempotent, additive DDL. Any change that can lose or rewrite data (moving rows,
dropping or rebuilding a table) is a step here instead. An instance runs pending steps only
inside `evlet migrate`, on a staged copy after a backup; serving refuses until they are applied.
See docs/app-upgrades.md.
"""

from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
import json
import time

from .migrations import SchemaError


@dataclass(frozen=True)
class FrameworkStep:
    """One framework schema change. A released step never changes; a correction is a new step.

    `run(connection, postgres)` receives the open migration transaction. Leave it out for a step
    that only records schema the additive startup code already creates.
    """

    name: str
    summary: str
    run: object = None

    def checksum(self):
        target = None if self.run is None else f'{self.run.__module__}.{self.run.__qualname__}'
        return sha256(json.dumps({'name': self.name, 'summary': self.summary, 'run': target}, sort_keys=True).encode()).hexdigest()


STEPS = (
    FrameworkStep('0001_startup_tables', 'Adopt the framework tables that Evlet 0.2.0 and earlier create and extend at startup.'),
)

LEDGER = ('CREATE TABLE IF NOT EXISTS evlet_framework_migrations (position INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, '
          'checksum TEXT NOT NULL, evlet_version TEXT NOT NULL, applied_at INTEGER NOT NULL)')


def version():
    try:
        return metadata.version('evlet')
    except metadata.PackageNotFoundError:
        return '0+unknown'


def state():
    """What apps.lock and the installed manifest record about the framework."""
    return {'version': version(), 'schema': STEPS[-1].name}


def _applied(connection):
    rows = connection.execute('SELECT name, checksum FROM evlet_framework_migrations ORDER BY position').fetchall()
    if len(rows) > len(STEPS) or any(row[0] != step.name or row[1] != step.checksum() for row, step in zip(rows, STEPS)):
        raise SchemaError('This database was migrated by a different or newer Evlet release; install that release of Evlet')
    return len(rows)


def upgrade(database, *, apply=True):
    """Run pending steps in one transaction and record each one, or refuse when apply is false."""
    postgres = getattr(database, 'driver', None) is not None
    with database.transaction() as tx:
        connection = tx.connection
        connection.execute(LEDGER)
        waiting = STEPS[_applied(connection):]
        if waiting and not apply:
            raise SchemaError('Evlet was upgraded and framework migrations are pending (' + ', '.join(step.name for step in waiting)
                              + '); stop serving and run evlet migrate')
        for position, step in enumerate(waiting, len(STEPS) - len(waiting) + 1):
            if step.run is not None:
                step.run(connection, postgres)
            connection.execute('INSERT INTO evlet_framework_migrations (position, name, checksum, evlet_version, applied_at) VALUES (?, ?, ?, ?, ?)',
                               (position, step.name, step.checksum(), version(), int(time.time())))
    return [step.name for step in waiting]
