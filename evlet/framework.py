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

from . import qb
from .adapter import Column, TableDefinition
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
    from .tools import ensure_calls
    ensure_calls(connection)
    if connection.table_exists('evlet_mcp_calls'):
        if connection.table_exists('evlet_mcp_clients'):
            calls = (qb.select(qb.table('evlet_mcp_calls', 'k'), 'k.at', 'm.user_id', qb.value('mcp'), 'm.client_name', 'k.client_id',
                               'k.tool', 'k.model', 'k.kind', 'k.outcome')
                     .join('evlet_mcp_clients', qb.eq('m.id', qb.col('k.client_id')), alias='m').order_by('k.id'))
            connection.run(qb.insert_rows_from('evlet_tool_calls', ('at', 'user_id', 'origin', 'client', 'client_id', 'tool', 'model',
                                                                    'kind', 'outcome'), calls))
        connection.drop_table('evlet_mcp_calls')


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


def _state_manager(connection, registry):
    """0.4.6: give the journal the state manager's columns (empty on old entries), create evlet_releases, turn
    confirmable revisions into releases with their snapshots and lifecycle states into State values, then give
    every record that is in a release step and has no release a v0 release (no snapshot)."""
    import json
    from .database import Session
    from .journal import SCALAR_COLUMNS
    from .statemanager import digest, ensure_releases, releasing, store, tree, versioned
    if connection.table_exists('evlet_journal'):
        present = connection.columns('evlet_journal')
        for column in SCALAR_COLUMNS:
            if column.name not in present:
                connection.add_column('evlet_journal', column)
    ensure_releases(connection)
    lost = set()
    if connection.table_exists('evlet_document_lifecycle'):
        for row in connection.run(qb.select('evlet_document_lifecycle', 'model', 'record_id', 'state')).all():
            model = registry.models.get(row['model'])
            if model is None or model.__state__ is None or row['state'] not in model.__declared_fields__[model.__state__].choices:
                lost.add(row['model'])
            else:
                connection.run(qb.update(registry.name_for(model), **{model.__state__: row['state']}).where(id=row['record_id']))
    if lost:
        raise SchemaError('These models have confirmable lifecycle rows but no State with those steps (draft, confirmed, '
                          'cancelled): ' + ', '.join(sorted(lost)) + '. Declare the State first, then migrate.')
    if connection.table_exists('evlet_document_revisions'):
        for row in connection.run(qb.select('evlet_document_revisions').order_by('model', 'record_id', 'revision')).all():
            model = registry.models[row['model']]
            document = versioned(model, json.loads(row['data'])['document'], registry)
            current = connection.run(qb.select(registry.name_for(model), model.__state__).where(id=row['record_id'])).value() or 'draft'
            for snapshot in (False, True):
                store(connection, registry, model, row['record_id'], row['revision'] - 1, snapshot=snapshot, state=current, data=document
                      if snapshot else {'approvals': [], 'remark': None}, hash=digest(model, document, registry), actor=row['actor'])
        connection.drop_table('evlet_document_revisions')
    if connection.table_exists('evlet_document_lifecycle'):
        connection.drop_table('evlet_document_lifecycle')
    session = Session(connection, registry)
    for model in [model for model in registry.models.values() if releasing(model) and connection.table_exists(registry.name_for(model))]:
        name, steps = registry.name_for(model), model.__declared_fields__[model.__state__].releases
        released = {row[0] for row in connection.run(qb.select('evlet_releases', 'record_id').where(model=name)).all()}
        for (record_id,) in connection.run(qb.select(name, 'id').where(qb.in_(model.__state__, steps)).order_by('id')).all():
            if record_id not in released:
                document = tree(_saved(session, registry, model, record_id))
                store(connection, registry, model, record_id, 0, snapshot=False, state=session.get(model, record_id)._values[model.__state__],
                      data={'approvals': [], 'remark': None}, hash=digest(model, document, registry), actor='engine')


def _saved(session, registry, model, record_id):
    """A saved record with its owned rows at every depth in `_rows`, for its content hash."""
    record = session.get(model, record_id)
    from .database import ROW_LIMIT
    order = lambda child: ('evlet_position', 'id') if 'evlet_position' in child.model.__fields__ else ('id',)
    record._rows = {}
    for child in (child for child in registry.children if child.parent is model):
        rows, page = [], True
        while page:
            page = session.list(child.model, filters={child.link: record_id}, order_by=order(child), limit=ROW_LIMIT, offset=len(rows))
            rows += page
        record._rows[child.name] = [_saved(session, registry, child.model, row.id) for row in rows]
    return record


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
    FrameworkStep('0011_state_manager', "Add the state manager's journal columns and releases; convert confirmable lifecycle "
                  'and revision rows; give records already in a release step a v0 release.', _state_manager, registry=True),
)

LEDGER = TableDefinition('evlet_framework_migrations', (
    Column('position', 'INTEGER', primary=True),
    Column('name', 'TEXT', required=True),
    Column('checksum', 'TEXT', required=True),
    Column('evlet_version', 'TEXT', required=True),
    Column('applied_at', 'INTEGER', required=True),
), unique=(('name',),))


def version():
    try:
        return metadata.version('evlet')
    except metadata.PackageNotFoundError:
        return '0+unknown'


def state():
    """What apps.lock and the installed manifest record about the framework."""
    return {'version': version(), 'schema': STEPS[-1].name}


def _applied(connection):
    rows = connection.run(qb.select('evlet_framework_migrations', 'name', 'checksum').order_by('position')).all()
    if len(rows) > len(STEPS) or any(row[0] != step.name or row[1] != step.checksum() for row, step in zip(rows, STEPS)):
        raise SchemaError('This database was migrated by a different or newer Evlet release; install that release of Evlet')
    return len(rows)


def pending(database):
    """The steps `upgrade` would run, for `evlet migrate --plan`. Call it on a copy: it creates the ledger table."""
    with database.transaction() as tx:
        tx.connection.ensure_table(LEDGER)
        return STEPS[_applied(tx.connection):]


def upgrade(database, *, apply=True):
    """Run pending steps in one transaction and record each one, or refuse when apply is false."""
    if apply:
        # Before the steps' transaction and before any is recorded: a failure leaves 0010 pending.
        database.upgrade_storage()
    # Processes starting together take turns: one write transaction at a time, so later ones find the steps applied.
    with database.transaction() as tx:
        connection = tx.connection
        connection.ensure_table(LEDGER)
        waiting = STEPS[_applied(connection):]
        if waiting and not apply:
            raise SchemaError('Evlet was upgraded and framework migrations are pending (' + ', '.join(step.name for step in waiting)
                              + '); stop serving and run evlet migrate')
        for position, step in enumerate(waiting, len(STEPS) - len(waiting) + 1):
            if step.run is not None:
                step.run(connection, *((database.registry,) if step.registry else ()))
            connection.run(qb.insert('evlet_framework_migrations', position=position, name=step.name, checksum=step.checksum(),
                                     evlet_version=version(), applied_at=int(time.time())))
    return [step.name for step in waiting]
