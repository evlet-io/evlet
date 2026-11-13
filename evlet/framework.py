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

    `run(connection)` receives the open migration transaction. Steps run before the
    additive startup tables exist, so a step creates any table it writes to. Leave `run` out for a
    step that only records schema the additive startup code already creates. The checksum covers
    the name, summary and the function's qualified name, not its source: release review keeps a
    released step's code unchanged.
    """

    name: str
    summary: str
    run: object = None
    # A step that reads the app's models gets the registry as a second argument.
    registry: bool = False

    def checksum(self):
        target = None if self.run is None else f'{self.run.__module__}.{self.run.__qualname__}'
        return sha256(json.dumps({'name': self.name, 'summary': self.summary, 'run': target}, sort_keys=True).encode()).hexdigest()


def _tool_calls(connection):
    from .tools import CALLS_TABLE
    for statement in CALLS_TABLE:
        connection.execute(statement)
    if connection.table_exists('evlet_mcp_calls'):
        if connection.table_exists('evlet_mcp_clients'):
            connection.execute("INSERT INTO evlet_tool_calls(at,user_id,origin,client,client_id,tool,model,kind,outcome) "
                               "SELECT k.at, m.user_id, 'mcp', m.client_name, k.client_id, k.tool, k.model, k.kind, k.outcome "
                               'FROM evlet_mcp_calls k JOIN evlet_mcp_clients m ON m.id=k.client_id ORDER BY k.id')
        connection.execute('DROP TABLE evlet_mcp_calls')


def _record_names(connection, registry):
    """Give every record table the standard name and title columns (evlet/naming.py) and fill empty titles.

    New tables have them from the start, and every app migration batch adds them too; this step covers
    an instance with no app migration to run. `name` stays empty for existing rows: an app that wants
    names for them fills them itself (evlet.sequences.backfill or a RunData migration).
    """
    from .naming import add_columns, fill_titles
    for table, model in registry.models.items():
        add_columns(connection, table, model)
        fill_titles(connection, table, model)


def _record_times(connection, registry):
    """Give every record table the created and modified columns (evlet/audit.py) and fill them: from the
    record's Journal where it has one, otherwise from the time in its id. New tables have them from the
    start, and every app migration batch adds them too; this step covers an instance with nothing to migrate."""
    from .audit import add_columns
    for table, model in registry.models.items():
        if not getattr(model, '__ledger__', False):  # Ledger tables have no record times (evlet/ledger.py).
            add_columns(connection, table)


def _unsaved_changes(connection):
    """The table of unsaved changes people leave on saved records (evlet/unsaved.py)."""
    from .unsaved import ensure
    # It refers to evlet_users; on a new database startup creates both, users first.
    if connection.table_exists('evlet_users'):
        ensure(connection)


def _fixed_ids(connection, registry):
    """Make the database refuse an UPDATE that changes a model table's id (evlet/identity.py). New tables
    have the trigger from the start, and every app migration batch adds it again; this step covers an
    instance with nothing to migrate."""
    from .identity import add_id_guard
    for table in registry.models:
        if connection.table_exists(table):
            add_id_guard(connection, table)


def _administrator(connection):
    """Add the built-in Administrator (evlet/auth.py) to a database from before it existed, with no password.
    A new database has no users table yet; startup creates the Administrator there. Also renames the 0.4.2
    Administrator to admin; refuses when another account named admin is not an active admin."""
    from .auth import ensure_administrator
    if connection.table_exists('evlet_users'):
        ensure_administrator(connection)


STEPS = (
    FrameworkStep('0001_startup_tables', 'Adopt the framework tables that Evlet 0.2.0 and earlier create and extend at startup.'),
    FrameworkStep('0002_tool_calls', 'Move the MCP call log into evlet_tool_calls and drop evlet_mcp_calls.', _tool_calls),
    FrameworkStep('0003_record_names', 'Add the standard name and title columns to record tables and fill each title.',
                  _record_names, registry=True),
    FrameworkStep('0004_record_times', 'Add the created and modified time and user columns to record tables and fill them.',
                  _record_times, registry=True),
    FrameworkStep('0005_unsaved_changes', 'Add evlet_unsaved, the unsaved changes people leave on saved records.', _unsaved_changes),
    FrameworkStep('0006_fixed_ids', 'Make the database refuse a change to the id of any model row.', _fixed_ids, registry=True),
    # Recorded only: migrate() moves the amounts before any app migration runs (evlet.migrations.money_scale).
    FrameworkStep('0007_money_scale', 'Store every Currency amount at 6 decimal places, so each currency can have its own decimals.'),
    FrameworkStep('0008_administrator', 'Add the built-in Administrator account, with no password until one is set.', _administrator),
    FrameworkStep('0009_admin_username', 'Rename the built-in Administrator from administrator to admin.', _administrator),
    # Recorded only: upgrade() moves the tables before the steps' transaction (Database.upgrade_storage, which
    # runs evlet.writes.split_files), since a file cannot be attached inside a transaction.
    FrameworkStep('0010_split_files', 'Move sign-in sessions, the tool-call log and Eva into their own SQLite files next to the database.'),
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


def pending(database):
    """The steps `upgrade` would run, for `evlet migrate --plan`. Call it on a copy: it creates the ledger table."""
    with database.transaction() as tx:
        tx.connection.execute(LEDGER)
        return STEPS[_applied(tx.connection):]


def upgrade(database, *, apply=True):
    """Run pending steps in one transaction and record each one, or refuse when apply is false."""
    if apply:
        # Before the steps' transaction and before any is recorded: a failure leaves 0010 pending.
        database.upgrade_storage()
    # Processes starting together take turns: one write transaction at a time, so later ones find the steps applied.
    with database.transaction() as tx:
        connection = tx.connection
        connection.execute(LEDGER)
        waiting = STEPS[_applied(connection):]
        if waiting and not apply:
            raise SchemaError('Evlet was upgraded and framework migrations are pending (' + ', '.join(step.name for step in waiting)
                              + '); stop serving and run evlet migrate')
        for position, step in enumerate(waiting, len(STEPS) - len(waiting) + 1):
            if step.run is not None:
                step.run(connection, *((database.registry,) if step.registry else ()))
            connection.execute('INSERT INTO evlet_framework_migrations (position, name, checksum, evlet_version, applied_at) VALUES (?, ?, ?, ?, ?)',
                               (position, step.name, step.checksum(), version(), int(time.time())))
    return [step.name for step in waiting]
