"""The workflow engine: stored definitions, runs, attempts, stages, tasks and decisions.

Definitions are saved as drafts and published as immutable versions. A run is one pass of a record
through one published version for one protected operation; it starts with the first Send and stays
pinned to that version. An attempt is one approval try inside a run: Request changes ends it and the
next Send starts a linked new one, so earlier approvals never count again.

When a stage starts, the engine reads the facts its conditions need from the saved record, decides
its requirement, its action conditions, its routes and which reviews apply, and resolves the people
assigned to each task. All of that is frozen in the stage execution's `routing`, so later decisions
follow the same branches whatever changes outside the attempt. Decisions are append-only rows,
guarded against UPDATE and DELETE by triggers; runs, attempts, executions and tasks are projections
that the engine keeps in step with them in the same transaction.

Everything here runs inside the caller's transaction and takes no network calls. The checks that
write paths, receipts and the journal add are a later phase (docs/internal/workflow.md section 11).
"""

import hashlib
import json
import re
import secrets
import unicodedata

from .auth import AccessError, User
from .database import ConflictError
from .identity import RecordId
from .journal import now
from .models import ValidationError, identifier
from .workflow import protect_key, protect_object
from .workflow_definition import (LIMITS, VOTES, Known, Undecided, canonical, check, condition_facts, evaluate, facts_read,
                                  resolve_route, split)

APPEND_ONLY = 'Workflow history is append only'
OPEN = ('draft', 'active', 'blocked', 'held')
RUN_STATUSES = ('draft', 'active', 'blocked', 'held', 'approved', 'rejected', 'closed', 'ended')
ATTEMPT_STATUSES = ('draft', 'active', 'approved', 'rejected', 'closed', 'changes', 'withdrawn', 'ended')
TASK_STATUSES = ('pending', 'approved', 'rejected', 'changes', 'done', 'skipped', 'closed')
DECISION_KINDS = ('submit', 'approve', 'reject', 'request_changes', 'move', 'withdraw', 'refresh', 'hold', 'resume',
                  'reassign', 'override', 'clarify')
# The most people one role assignment resolves to; more is a configuration to repair, not a task to open.
ROLE_LIMIT = 500

TABLES = (
    "CREATE TABLE IF NOT EXISTS evlet_workflows (id TEXT PRIMARY KEY, model TEXT NOT NULL, protects TEXT NOT NULL, "
    "name TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','published','paused','archived')), "
    "published_version INTEGER, draft TEXT, draft_revision INTEGER NOT NULL DEFAULT 0, "
    "changed_by INTEGER REFERENCES evlet_users(id), changed_at TEXT NOT NULL)",
    'CREATE INDEX IF NOT EXISTS evlet_workflows_scope ON evlet_workflows (model, protects, status)',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_versions (workflow_id TEXT NOT NULL REFERENCES evlet_workflows(id), '
    'version INTEGER NOT NULL CHECK(version>0), definition TEXT NOT NULL, hash TEXT NOT NULL, '
    "published_by INTEGER REFERENCES evlet_users(id), published_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', "
    'PRIMARY KEY (workflow_id, version))',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, '
    'record_id INTEGER NOT NULL, workflow_id TEXT NOT NULL, version INTEGER NOT NULL, protects TEXT NOT NULL, '
    'requester INTEGER NOT NULL REFERENCES evlet_users(id), '
    f"status TEXT NOT NULL CHECK(status IN ({', '.join(repr(item) for item in RUN_STATUSES)})), "
    'stage_id TEXT NOT NULL, attempt_id INTEGER, counter INTEGER NOT NULL DEFAULT 0, blocked TEXT, '
    'started_at TEXT NOT NULL, ended_at TEXT, '
    'FOREIGN KEY (workflow_id, version) REFERENCES evlet_workflow_versions(workflow_id, version))',
    # One open run per record and protected operation (an operational workflow protects '').
    'CREATE UNIQUE INDEX IF NOT EXISTS evlet_workflow_runs_open ON evlet_workflow_runs (model, record_id, protects) '
    f"WHERE status IN ({', '.join(repr(item) for item in OPEN)})",
    'CREATE INDEX IF NOT EXISTS evlet_workflow_runs_record ON evlet_workflow_runs (model, record_id)',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'run_id INTEGER NOT NULL REFERENCES evlet_workflow_runs(id), n INTEGER NOT NULL CHECK(n>0), '
    f"status TEXT NOT NULL CHECK(status IN ({', '.join(repr(item) for item in ATTEMPT_STATUSES)})), "
    'generation INTEGER NOT NULL DEFAULT 0, fingerprint TEXT, previous_id INTEGER REFERENCES evlet_workflow_attempts(id), '
    'started_at TEXT, ended_at TEXT, end_reason TEXT, evidence_used_at TEXT, UNIQUE (run_id, n))',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_record_data (model TEXT NOT NULL, record_id INTEGER NOT NULL, '
    'generation INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (model, record_id))',
    # parent_id and review_id place the stages of a nested review; reserved until nested reviews are built.
    'CREATE TABLE IF NOT EXISTS evlet_workflow_executions (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'run_id INTEGER NOT NULL REFERENCES evlet_workflow_runs(id), attempt_id INTEGER NOT NULL REFERENCES evlet_workflow_attempts(id), '
    'stage_id TEXT NOT NULL, parent_id INTEGER REFERENCES evlet_workflow_executions(id), review_id TEXT, '
    "occurrence INTEGER NOT NULL CHECK(occurrence>0), status TEXT NOT NULL CHECK(status IN ('active','blocked','done','closed')), "
    'outcome TEXT, routing TEXT NOT NULL, entered_at TEXT NOT NULL, left_at TEXT)',
    'CREATE INDEX IF NOT EXISTS evlet_workflow_executions_run ON evlet_workflow_executions (run_id, attempt_id)',
    'CREATE INDEX IF NOT EXISTS evlet_workflow_executions_open ON evlet_workflow_executions (attempt_id, status)',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'execution_id INTEGER NOT NULL REFERENCES evlet_workflow_executions(id), review_id TEXT, required TEXT NOT NULL, '
    f"status TEXT NOT NULL CHECK(status IN ({', '.join(repr(item) for item in TASK_STATUSES)})), due_at TEXT, closed_reason TEXT)",
    'CREATE INDEX IF NOT EXISTS evlet_workflow_tasks_execution ON evlet_workflow_tasks (execution_id)',
    'CREATE INDEX IF NOT EXISTS evlet_workflow_tasks_due ON evlet_workflow_tasks (status, due_at)',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_assignees (task_id INTEGER NOT NULL REFERENCES evlet_workflow_tasks(id), '
    "user_id INTEGER NOT NULL REFERENCES evlet_users(id), via TEXT NOT NULL CHECK(via IN ('requester','role','user','fact','field','delegate')), "
    'principal_id INTEGER REFERENCES evlet_users(id), active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (task_id, user_id))',
    'CREATE INDEX IF NOT EXISTS evlet_workflow_assignees_user ON evlet_workflow_assignees (user_id, active, task_id)',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_delegations (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'from_user INTEGER NOT NULL REFERENCES evlet_users(id), to_user INTEGER NOT NULL REFERENCES evlet_users(id), '
    'scope TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT, authorized_by INTEGER REFERENCES evlet_users(id), '
    'created_at TEXT NOT NULL, revoked_at TEXT, revoked_by INTEGER REFERENCES evlet_users(id))',
    'CREATE INDEX IF NOT EXISTS evlet_workflow_delegations_to ON evlet_workflow_delegations (to_user, ends_at)',
    'CREATE TABLE IF NOT EXISTS evlet_workflow_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'run_id INTEGER NOT NULL REFERENCES evlet_workflow_runs(id), attempt_id INTEGER NOT NULL REFERENCES evlet_workflow_attempts(id), '
    'execution_id INTEGER REFERENCES evlet_workflow_executions(id), task_id INTEGER REFERENCES evlet_workflow_tasks(id), '
    f"action_id TEXT, kind TEXT NOT NULL CHECK(kind IN ({', '.join(repr(item) for item in DECISION_KINDS)})), "
    'actor INTEGER NOT NULL REFERENCES evlet_users(id), principal INTEGER NOT NULL REFERENCES evlet_users(id), '
    'delegation_id INTEGER REFERENCES evlet_workflow_delegations(id), role TEXT, feedback TEXT, '
    'clarifies INTEGER REFERENCES evlet_workflow_decisions(id), at TEXT NOT NULL, version INTEGER NOT NULL, '
    'generation INTEGER NOT NULL, correlation_id TEXT, source TEXT, client TEXT, journal_id INTEGER)',
    'CREATE INDEX IF NOT EXISTS evlet_workflow_decisions_run ON evlet_workflow_decisions (run_id, id)',
    # A person decides a task once, and one stage execution once: deciding as two roles never gives two votes.
    'CREATE UNIQUE INDEX IF NOT EXISTS evlet_workflow_decisions_vote ON evlet_workflow_decisions (task_id, principal) '
    "WHERE clarifies IS NULL AND kind IN ('approve','reject','request_changes')",
    'CREATE UNIQUE INDEX IF NOT EXISTS evlet_workflow_decisions_once ON evlet_workflow_decisions (execution_id, principal) '
    "WHERE clarifies IS NULL AND kind IN ('approve','reject','request_changes')",
)
GUARDED = ('evlet_workflow_decisions', 'evlet_workflow_versions')


def ensure_tables(connection, *, postgres=False):
    """Create the tables, indexes and append-only guards. Every statement is idempotent."""
    for statement in TABLES:
        connection.execute(statement)
    if postgres:
        connection.execute('CREATE OR REPLACE FUNCTION evlet_workflow_append_only() RETURNS trigger LANGUAGE plpgsql AS '
                           f"$$ BEGIN RAISE EXCEPTION '{APPEND_ONLY}'; END $$")
        for table in GUARDED:
            if connection.execute('SELECT 1 FROM pg_trigger WHERE tgname=?', (f'{table}_append_only',)).fetchone() is None:
                connection.execute(f'CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} '
                                   'FOR EACH ROW EXECUTE FUNCTION evlet_workflow_append_only()')
                connection.execute(f'CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} '
                                   'FOR EACH STATEMENT EXECUTE FUNCTION evlet_workflow_append_only()')
        return
    keys = {'evlet_workflow_decisions': 'id = NEW.id', 'evlet_workflow_versions': 'workflow_id = NEW.workflow_id AND version = NEW.version'}
    for table in GUARDED:
        for event in ('UPDATE', 'DELETE'):
            connection.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_no_{event.lower()} BEFORE {event} ON {table} '
                               f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END")
        # REPLACE deletes the old row without firing DELETE triggers, so an insert may not reuse a key.
        connection.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} '
                           f'WHEN EXISTS (SELECT 1 FROM {table} WHERE {keys[table]}) '
                           f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END")


class WorkflowError(ConflictError):
    """A workflow conflict with a stable code: WORKFLOW_BLOCKED, WORKFLOW_CHANGED, WORKFLOW_NONE or WORKFLOW_PAUSED."""

    def __init__(self, code, message, causes=()):
        self.code, self.message, self.causes = code, message, tuple(causes)
        super().__init__(message)


def blocked_error(message, causes=()):
    return WorkflowError('WORKFLOW_BLOCKED', message, causes)


CHANGED = 'This request changed. Review the latest status.'


class Blocked(Exception):
    """Raised inside stage entry; the run is marked blocked with this reason."""

    def __init__(self, message, causes=()):
        self.message, self.causes = message, tuple(causes)


def loads(value):
    return None if value is None else json.loads(value)


class Workflows:
    """business.workflows: the engine for the models that declare Meta.workflow."""

    def __init__(self, business):
        self.business, self.registry = business, business.registry
        self.postgres = getattr(business.database, 'driver', None) is not None

    def initialize(self):
        with self.business.database.transaction() as tx:
            ensure_tables(tx.connection, postgres=self.postgres)
            # Idempotency receipts of workflow decisions, shared with dialogs and slips.
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_operations (key TEXT PRIMARY KEY, actor INTEGER NOT NULL, '
                                  'request TEXT NOT NULL, result TEXT NOT NULL)')

    def _model(self, model):
        model = self.registry.resolve(model)
        if model not in self.registry.workflows:
            raise AccessError(404, 'This model has no workflow')
        return model

    # ---------- definitions ----------

    def known(self, tx, model):
        policy = self.business.policy
        reviewers = policy.reviewers.get(model, frozenset())
        users = {row[0] for row in tx.connection.execute('SELECT id FROM evlet_users WHERE active=1').fetchall()}
        return Known({role: role in reviewers for role in policy.roles}, users)

    def check(self, tx, model, definition):
        """The normalized definition, or DefinitionError listing every problem by id."""
        model = self._model(model)
        return check(self.registry.workflows[model], self.known(tx, model), self.registry.name_for(model), definition)

    def save_draft(self, tx, user, model, definition, *, workflow_id=None, revision=None):
        """Save a draft definition; {"id", "revision"}. A draft is checked when published, not when saved,
        but it must be an object of at most the definition size and name the model."""
        model = self._model(model)
        name = self.registry.name_for(model)
        if not isinstance(definition, dict) or definition.get('model') != name:
            raise ValidationError({'definition': f'Send a definition for {name}'})
        text = canonical(definition)
        if len(text.encode()) > LIMITS['bytes']:
            raise ValidationError({'definition': f'A definition is at most {LIMITS["bytes"] // 1024} KB'})
        protects = protect_key(definition.get('protects'))
        label = definition.get('name') if isinstance(definition.get('name'), str) else ''
        connection, at = tx.connection, now()
        if workflow_id is None:
            workflow_id = 'wfl_' + secrets.token_hex(8)
            connection.execute('INSERT INTO evlet_workflows (id, model, protects, name, status, draft, draft_revision, changed_by, changed_at) '
                               "VALUES (?, ?, ?, ?, 'draft', ?, 1, ?, ?)",
                               (workflow_id, name, protects or '', label[:LIMITS['name']], text, user.id or None, at))
            return {'id': workflow_id, 'revision': 1}
        row = connection.execute('SELECT model, draft_revision FROM evlet_workflows WHERE id=?', (workflow_id,)).fetchone()
        if row is None or row['model'] != name:
            raise AccessError(404, 'Workflow not found')
        if revision != row['draft_revision']:
            raise WorkflowError('WORKFLOW_CHANGED', 'Someone else saved this workflow. Reload it to see their changes.')
        connection.execute('UPDATE evlet_workflows SET draft=?, draft_revision=draft_revision+1, name=?, changed_by=?, changed_at=? WHERE id=?',
                           (text, label[:LIMITS['name']], user.id or None, at, workflow_id))
        return {'id': workflow_id, 'revision': revision + 1}

    def publish(self, tx, user, workflow_id, *, revision, notes=None):
        """Publish the draft as the next immutable version; returns the version number."""
        connection = tx.connection
        row = connection.execute('SELECT * FROM evlet_workflows WHERE id=?', (workflow_id,)).fetchone()
        if row is None:
            raise AccessError(404, 'Workflow not found')
        if row['draft_revision'] != revision or row['draft'] is None:
            raise WorkflowError('WORKFLOW_CHANGED', 'Someone else saved this workflow. Reload it to see their changes.')
        model = self._model(row['model'])
        draft = json.loads(row['draft'])
        version = (row['published_version'] or 0) + 1
        draft = {**draft, 'id': workflow_id, 'version': version, **({} if notes is None else {'notes': notes})}
        definition = self.check(tx, model, draft)
        # Running an operation from a workflow (execute) comes in a later phase; until then publishing one is
        # refused rather than accepted and silently ignored.
        unbuilt = [{'id': action['id'], 'message': 'Running an operation from a workflow action is not available yet'}
                   for stage in definition['stages'] for action in stage['actions'] if action['execute'] is not None]
        if definition['settings']['completion']['mode'] == 'execute':
            unbuilt.append({'id': 'settings', 'message': 'Completion that runs the operation is not available yet; use enable'})
        if unbuilt:
            from .workflow_definition import DefinitionError
            raise DefinitionError(unbuilt)
        protects = protect_key(definition['protects'])
        self._check_overlap(tx, model, workflow_id, protects, definition)
        text = canonical(definition)
        at = now()
        connection.execute('INSERT INTO evlet_workflow_versions (workflow_id, version, definition, hash, published_by, published_at, notes) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (workflow_id, version, text, hashlib.sha256(text.encode()).hexdigest(), user.id or None, at, definition['notes']))
        status = 'paused' if definition['settings']['paused'] else 'published'
        connection.execute('UPDATE evlet_workflows SET status=?, published_version=?, protects=?, name=?, draft=NULL, '
                           'draft_revision=draft_revision+1, changed_by=?, changed_at=? WHERE id=?',
                           (status, version, protects, definition['name'], user.id or None, at, workflow_id))
        return version

    def _check_overlap(self, tx, model, workflow_id, protects, definition):
        """A second published workflow for the same model and operation is allowed only when both choose
        records by the same choice fact with no value in common (contract question 9)."""
        others = tx.connection.execute("SELECT w.id, w.name, v.definition FROM evlet_workflows w JOIN evlet_workflow_versions v "
                                       "ON v.workflow_id=w.id AND v.version=w.published_version WHERE w.model=? AND w.protects=? "
                                       "AND w.status IN ('published','paused') AND w.id<>?",
                                       (self.registry.name_for(model), protects, workflow_id)).fetchall()
        if not others:
            return
        mine = split(definition)
        compiled = self.registry.workflows[model]
        for other in others:
            theirs = split(json.loads(other['definition']))
            if mine is None or theirs is None or mine[0] != theirs[0] or compiled.facts[mine[0]].type != 'enum' or mine[1] & theirs[1]:
                from .workflow_definition import DefinitionError
                raise DefinitionError([{'id': 'definition', 'message': f'The workflow {other["name"]!r} already applies to these records. '
                                        'Two workflows for the same operation must each choose records by the same choice field, '
                                        'with no value in common.'}])

    def set_paused(self, tx, user, workflow_id, paused):
        """Pause or resume new starts; running requests carry on."""
        row = tx.connection.execute('SELECT status FROM evlet_workflows WHERE id=?', (workflow_id,)).fetchone()
        if row is None:
            raise AccessError(404, 'Workflow not found')
        if row['status'] not in ('published', 'paused'):
            raise WorkflowError('WORKFLOW_CHANGED', 'Only a published workflow can be paused or resumed')
        tx.connection.execute('UPDATE evlet_workflows SET status=?, changed_by=?, changed_at=? WHERE id=?',
                              ('paused' if paused else 'published', user.id or None, now(), workflow_id))

    def definition(self, tx, workflow_id, version):
        row = tx.connection.execute('SELECT definition FROM evlet_workflow_versions WHERE workflow_id=? AND version=?',
                                    (workflow_id, version)).fetchone()
        if row is None:
            raise AccessError(404, 'Workflow version not found')
        return json.loads(row['definition'])

    # ---------- selection ----------

    def select(self, tx, model, record_id, protects, requester):
        """(workflow id, version, definition) of the one published workflow that applies, or None when none
        applies and the model allows that. Overlap, an undecided condition and pausing raise WorkflowError."""
        model = self._model(model)
        compiled = self.registry.workflows[model]
        rows = tx.connection.execute("SELECT w.id, w.status, w.published_version, v.definition FROM evlet_workflows w "
                                     "JOIN evlet_workflow_versions v ON v.workflow_id=w.id AND v.version=w.published_version "
                                     "WHERE w.model=? AND w.protects=? AND w.status IN ('published','paused') ORDER BY w.id",
                                     (self.registry.name_for(model), protects)).fetchall()
        matched = []
        for row in rows:
            definition = json.loads(row['definition'])
            facts, unknown = self.facts(tx, model, record_id, requester, condition_facts(definition['applies_when']))
            found = evaluate(definition['applies_when'], facts, self.types(model))
            if isinstance(found, Undecided):
                raise blocked_error(self.undecided_message(model, found, unknown, 'Which workflow applies'), found.causes)
            if found:
                matched.append((row, definition))
        if len(matched) > 1:
            raise blocked_error('More than one workflow applies to this record. An administrator must repair the workflows.')
        if not matched:
            if compiled.unmatched == 'allow':
                return None
            raise WorkflowError('WORKFLOW_NONE', 'No workflow applies to this record, so it cannot be sent.')
        row, definition = matched[0]
        if row['status'] == 'paused' or definition['settings']['paused']:
            raise WorkflowError('WORKFLOW_PAUSED', 'New requests are paused for this workflow.')
        return row['id'], row['published_version'], definition

    def applies(self, tx, model, record_id, protects, requester):
        """Whether a published or paused workflow for the operation matches the record. An undecided
        condition or two matches raise WORKFLOW_BLOCKED: they never count as no match."""
        rows = tx.connection.execute("SELECT v.definition FROM evlet_workflows w JOIN evlet_workflow_versions v "
                                     "ON v.workflow_id=w.id AND v.version=w.published_version "
                                     "WHERE w.model=? AND w.protects=? AND w.status IN ('published','paused')",
                                     (self.registry.name_for(model), protects)).fetchall()
        matched = 0
        for row in rows:
            definition = json.loads(row['definition'])
            facts, unknown = self.facts(tx, model, record_id, requester, condition_facts(definition['applies_when']))
            found = evaluate(definition['applies_when'], facts, self.types(model))
            if isinstance(found, Undecided):
                raise blocked_error(self.undecided_message(model, found, unknown, 'Which workflow applies'), found.causes)
            matched += bool(found)
        if matched > 1:
            raise blocked_error('More than one workflow applies to this record. An administrator must repair the workflows.')
        return matched == 1

    # ---------- facts ----------

    def types(self, model):
        return {name: spec.type for name, spec in self.registry.workflows[model].facts.items()}

    def facts(self, tx, model, record_id, requester, names):
        """({name: wire value}, {name: reason}) for the facts named; facts that cannot be read are left out."""
        compiled = self.registry.workflows[model]
        found, unknown = {}, {}
        record = None
        for name in sorted(names):
            spec = compiled.facts.get(name)
            if spec is None:
                unknown[name] = 'not a fact'
                continue
            try:
                if spec.source in ('field', 'lookup'):
                    if record is None:
                        record = tx.get(model, record_id)
                    if spec.source == 'field':
                        found[name] = wire(record._values.get(name))
                    else:
                        found[name] = wire(spec.field.clean(getattr(record, spec.fact.read)()))
                elif spec.source == 'requester':
                    found[name] = self.requester_fact(tx, compiled, name, requester)
                else:
                    found[name] = self.row_fact(tx, spec, record_id)
            except Undecidable as exc:
                unknown[name] = exc.reason
            except Exception as exc:  # noqa: BLE001 - a fact that fails to read is undecided, never false
                unknown[name] = type(exc).__name__
        return found, unknown

    def requester_fact(self, tx, compiled, name, requester):
        if name == 'requester':
            return requester
        if name == 'requester.role':
            row = tx.connection.execute('SELECT role FROM evlet_users WHERE id=?', (requester,)).fetchone()
            return None if row is None else row['role']
        target = self.registry.resolve(compiled.people.model)
        records = self.linked(tx, requester, target)
        values = {wire(tx.get(target, record_id)._values.get('department')) for record_id in records}
        if not values:
            raise Undecidable('the requester is linked to no record')
        if len(values) > 1:
            raise Undecidable('the requester is linked to records of different departments')
        return values.pop()

    def row_fact(self, tx, spec, record_id):
        child, fact = spec.child, spec.fact
        table, owner = identifier(self.registry.name_for(child.model)), identifier(child.link)
        if fact.aggregate == 'any':
            from .measures import Today, compile_where
            _, _, sql, values = compile_where(self.registry, child.model, fact.where)
            values = [value.value(self.business.database.timezone or 'UTC') if isinstance(value, Today) else value for value in values]
            row = tx.connection.execute(f'SELECT COALESCE(SUM(CASE WHEN {sql} = 1 THEN 1 ELSE 0 END), 0), '
                                        f'COALESCE(SUM(CASE WHEN {sql} IS NULL THEN 1 ELSE 0 END), 0) '
                                        f'FROM {table} AS "record" WHERE "record".{owner} = ?', (*values, *values, record_id)).fetchone()
            if row[0]:
                return True
            if row[1]:
                raise Undecidable('a row cannot be decided')
            return False
        rows = [child.model._from_row(row) for row in tx.connection.execute(f'SELECT * FROM {table} WHERE {owner} = ?', (record_id,)).fetchall()]
        if fact.aggregate == 'count':
            return len(rows)
        from . import calc
        values = [row._values.get(fact.column) for row in rows]
        if any(value is None for value in values):
            raise Undecidable(f'a row has no {fact.column}')
        if not values:
            if fact.aggregate == 'sum':
                return '0' if spec.type == 'money' else 0
            return None
        if spec.type == 'date':
            return (min if fact.aggregate == 'min' else max)(value.isoformat() for value in values)
        numbers = [calc.parse(value) for value in values]
        if fact.aggregate == 'sum':
            result = calc.total(numbers)
        else:
            result = numbers[0]
            for number in numbers[1:]:
                if (calc.compare(number, result) < 0) == (fact.aggregate == 'min'):
                    result = number
        return wire(result)

    def linked(self, tx, user_id, target):
        return [RecordId(row[0]) for row in tx.connection.execute(
            'SELECT record_id FROM evlet_user_links WHERE user_id=? AND model=? ORDER BY record_id',
            (user_id, self.registry.name_for(target))).fetchall()]

    def accounts_of(self, tx, target, record_ids):
        if not record_ids:
            return []
        marks = ', '.join('?' for _ in record_ids)
        return [row[0] for row in tx.connection.execute(
            f'SELECT DISTINCT user_id FROM evlet_user_links WHERE model=? AND record_id IN ({marks}) ORDER BY user_id',
            (self.registry.name_for(target), *record_ids)).fetchall()]

    def undecided_message(self, model, found, unknown, what):
        compiled = self.registry.workflows[model]
        labels = [compiled.facts[name].label if name in compiled.facts else name for name in found.causes]
        return f'{what} cannot be decided: {", ".join(labels)} has no value or cannot be read.'

    # ---------- runtime ----------

    def open_runs(self, tx, model, record_id):
        marks = ', '.join('?' for _ in OPEN)
        return tx.connection.execute(f'SELECT * FROM evlet_workflow_runs WHERE model=? AND record_id=? AND status IN ({marks}) ORDER BY id',
                                     (self.registry.name_for(model), RecordId(record_id), *OPEN)).fetchall()

    def run_for(self, tx, model, record_id, protects):
        runs = self.open_runs(tx, model, record_id)
        if protects is not None:
            runs = [run for run in runs if run['protects'] == protects]
        if len(runs) > 1:
            raise ValidationError({'protects': 'Choose the operation this decision is for'})
        return runs[0] if runs else None

    def decide(self, tx, user, model, record_id, action_id=None, *, kind=None, review=None, protects=None, feedback=None, meta=None):
        """Record one decision (or the first Send, which starts a run) and settle what it completes.
        Returns the run's status as status() does. `meta` holds source, client and correlation_id for the
        decision row."""
        model = self._model(model)
        compiled = self.registry.workflows[model]
        record_id = RecordId(record_id)
        self.business.policy.record(tx, user, model, record_id)
        if protects is not None and protects not in (*compiled.protects, ''):
            raise ValidationError({'protects': 'This model has no such protected operation'})
        run = self.run_for(tx, model, record_id, protects)
        if run is None:
            if kind not in (None, 'submit'):
                # Only a Send starts a run; a decision on a run that already ended is a late one.
                ended = tx.connection.execute('SELECT 1 FROM evlet_workflow_runs WHERE model=? AND record_id=?'
                                              + (' AND protects=?' if protects is not None else ''),
                                              (self.registry.name_for(model), record_id, *(() if protects is None else (protects,)))).fetchone()
                if ended:
                    raise WorkflowError('WORKFLOW_CHANGED', CHANGED)
                raise AccessError(404, 'This action is not available')
            if protects is None:
                if len(compiled.protects) != 1:
                    raise ValidationError({'protects': 'Choose the operation to send this record for'})
                protects = compiled.protects[0]
            run = self.start(tx, user, model, record_id, protects)
        state = Run(self, tx, model, run)
        state.decide(user, action_id, kind, review, feedback, meta)
        return self.status(tx, user, model, record_id, protects=state.run['protects'])

    def withdraw(self, tx, user, model, record_id, *, protects=None, feedback=None, meta=None):
        """The requester withdraws the open request; returns the status as status() does."""
        model = self._model(model)
        record_id = RecordId(record_id)
        self.business.policy.record(tx, user, model, record_id)
        run = self.run_for(tx, model, record_id, protects)
        if run is None:
            raise WorkflowError('WORKFLOW_CHANGED', 'There is no open request to withdraw.')
        state = Run(self, tx, model, run)
        state.withdraw(user, feedback, meta)
        return self.status(tx, user, model, record_id, protects=state.run['protects'])

    # ---------- approval validity ----------

    def fingerprint(self, tx, model, record_id):
        """SHA-256 of the record's canonical saved data: root fields, rows at every depth with their positions
        and the attachment list. Harmless fields are left out; masked and excluded fields contribute only
        whether they have a value, never the value (their changes raise the data generation instead)."""
        business = self.business
        compiled = self.registry.workflows[model]

        def values(definition, row, skip=()):
            result = {}
            internal = set(getattr(definition, '__internal_fields__', ())) - {'evlet_position'}
            for name, field in definition.__fields__.items():
                if name in skip or name in internal:
                    continue
                value = row._values.get(name)
                if field.tracking in ('mask', 'exclude'):
                    result[name] = value is not None
                else:
                    try:
                        result[name] = field.serialize(value)
                    except Exception:  # noqa: BLE001 - a value that cannot be serialized still counts by its text
                        result[name] = str(value)
            return result

        def tree(definition, row, skip=()):
            result = {'id': int(row.id), 'values': values(definition, row, skip), 'rows': {}}
            for child in business.children.values():
                if child.parent is not definition:
                    continue
                table, link = identifier(self.registry.name_for(child.model)), identifier(child.link)
                rows = [child.model._from_row(item) for item in tx.connection.execute(f'SELECT * FROM {table} WHERE {link}=? ORDER BY id',
                                                                                     (row.id,)).fetchall()]
                result['rows'][child.name] = [tree(child.model, item, (child.link,)) for item in rows]
            return result

        data = tree(model, tx.get(model, record_id), compiled.harmless)
        if tx.connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_attachments'" if not self.postgres else
                                 "SELECT 1 FROM information_schema.tables WHERE table_name='evlet_attachments'").fetchone():
            data['attachments'] = [row[0] for row in tx.connection.execute(
                "SELECT id FROM evlet_attachments WHERE model=? AND record_id=? AND (purpose IS NULL OR purpose <> 'image') ORDER BY id",
                (self.registry.name_for(model), record_id)).fetchall()]
        return hashlib.sha256(canonical(data).encode()).hexdigest()

    def latest(self, tx, model, record_id, protects):
        return tx.connection.execute('SELECT * FROM evlet_workflow_runs WHERE model=? AND record_id=? AND protects=? ORDER BY id DESC LIMIT 1',
                                     (self.registry.name_for(model), RecordId(record_id), protects)).fetchone()

    def token(self, tx, model, record_id, protects):
        """The concurrency token of a record's workflow for one operation: "<run id>.<counter>" of its latest
        run, or "new" when it never had one."""
        run = self.latest(tx, model, record_id, protects)
        return 'new' if run is None else f'{run["id"]}.{run["counter"]}'

    def evidence(self, tx, model, record_id, protects):
        """(run, attempt) when the latest run for the operation reached an approved end whose evidence is unused
        and still matches the saved data (generation and fingerprint), else None."""
        run = self.latest(tx, model, record_id, protects)
        if run is None or run['status'] != 'approved':
            return None
        attempt = tx.connection.execute('SELECT * FROM evlet_workflow_attempts WHERE id=?', (run['attempt_id'],)).fetchone()
        if attempt['status'] != 'approved' or attempt['evidence_used_at'] is not None:
            return None
        if attempt['generation'] != self.generation(tx, model, record_id) or attempt['fingerprint'] != self.fingerprint(tx, model, record_id):
            return None
        return run, attempt

    def consume(self, tx, run, attempt):
        """Use approval evidence once; the run's token changes. Returns what the confirmation snapshot records."""
        tx.connection.execute('UPDATE evlet_workflow_attempts SET evidence_used_at=? WHERE id=?', (now(), attempt['id']))
        tx.connection.execute('UPDATE evlet_workflow_runs SET counter=counter+1 WHERE id=?', (run['id'],))
        decisions = [row[0] for row in tx.connection.execute(
            "SELECT id FROM evlet_workflow_decisions WHERE attempt_id=? ORDER BY id", (attempt['id'],)).fetchall()]
        return {'run': run['id'], 'workflow': run['workflow_id'], 'version': run['version'], 'attempt': attempt['n'], 'decisions': decisions}

    def record_changed(self, tx, user, model, record_id, reason):
        """A committed write changed non-harmless data: raise the record's data generation, end every approval
        attempt in progress (its open tasks close and the run returns to its start stage in a new attempt) and
        void approval evidence not yet used. Runs in the writer's transaction, so it commits or rolls back with it."""
        name, record_id = self.registry.name_for(model), RecordId(record_id)
        tx.connection.execute('INSERT INTO evlet_workflow_record_data (model, record_id, generation) VALUES (?, ?, 1) '
                              'ON CONFLICT (model, record_id) DO UPDATE SET generation=evlet_workflow_record_data.generation+1',
                              (name, record_id))
        for run in self.open_runs(tx, model, record_id):
            state = Run(self, tx, model, run)
            if state.attempt()['status'] != 'active':
                continue
            before = (run['stage_id'], run['status'])
            state.restart('ended', reason)
            state.bump()
            state.note_stage(user, before, reason)
        for protects in self.registry.workflows[model].protects:
            run = self.latest(tx, model, record_id, protects)
            if run is None or run['status'] != 'approved':
                continue
            attempt = tx.connection.execute('SELECT * FROM evlet_workflow_attempts WHERE id=?', (run['attempt_id'],)).fetchone()
            if attempt['evidence_used_at'] is not None:
                continue
            at = now()
            tx.connection.execute("UPDATE evlet_workflow_attempts SET status='ended', ended_at=?, end_reason=? WHERE id=?",
                                  (at, reason, attempt['id']))
            tx.connection.execute("UPDATE evlet_workflow_runs SET status='ended', counter=counter+1 WHERE id=?", (run['id'],))
            state = Run(self, tx, model, tx.connection.execute('SELECT * FROM evlet_workflow_runs WHERE id=?', (run['id'],)).fetchone())
            state.journal(user, 'stage', {'attempt': attempt['n'], 'from': {'id': run['stage_id'], 'label': state.stages[run['stage_id']]['label']},
                                          'to': {'id': run['stage_id'], 'label': state.stages[run['stage_id']]['label']},
                                          'status': 'ended', 'reason': reason})

    def record_deleted(self, tx, model, record_id):
        """End the record's open runs when it is deleted (the caller checked that the stage allowed it)."""
        for run in self.open_runs(tx, model, record_id):
            Run(self, tx, model, run).end('Record deleted')

    def start(self, tx, user, model, record_id, protects):
        selected = self.select(tx, model, record_id, protects, user.id)
        if selected is None:
            raise WorkflowError('WORKFLOW_NONE', 'No workflow applies to this record; it needs no approval.')
        workflow_id, version, definition = selected
        connection, at = tx.connection, now()
        run_id = connection.execute('INSERT INTO evlet_workflow_runs (model, record_id, workflow_id, version, protects, requester, status, '
                                    "stage_id, counter, started_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, 0, ?)",
                                    (self.registry.name_for(model), record_id, workflow_id, version, protects, user.id,
                                     definition['start'], at)).lastrowid
        attempt_id = connection.execute("INSERT INTO evlet_workflow_attempts (run_id, n, status, generation) VALUES (?, 1, 'draft', ?)",
                                        (run_id, self.generation(tx, model, record_id))).lastrowid
        connection.execute('UPDATE evlet_workflow_runs SET attempt_id=? WHERE id=?', (attempt_id, run_id))
        run = connection.execute('SELECT * FROM evlet_workflow_runs WHERE id=?', (run_id,)).fetchone()
        state = Run(self, tx, model, run)
        state.enter(definition['start'])
        if state.run['status'] == 'blocked':
            reason = json.loads(state.run['blocked'])
            raise blocked_error(reason['message'], reason.get('causes', ()))
        return state.run

    def generation(self, tx, model, record_id):
        row = tx.connection.execute('SELECT generation FROM evlet_workflow_record_data WHERE model=? AND record_id=?',
                                    (self.registry.name_for(model), record_id)).fetchone()
        return 0 if row is None else row[0]

    def status(self, tx, user, model, record_id, *, protects=None):
        """The run of a record as the engine sees it: stage, tasks, people and decisions. The detail metadata
        each person sees, filtered by their permissions, is built on this in a later phase."""
        model = self._model(model)
        record_id = RecordId(record_id)
        self.business.policy.record(tx, user, model, record_id)
        connection = tx.connection
        where = ' AND protects=?' if protects is not None else ''
        run = connection.execute(f'SELECT * FROM evlet_workflow_runs WHERE model=? AND record_id=?{where} ORDER BY id DESC LIMIT 1',
                                 (self.registry.name_for(model), record_id, *(() if protects is None else (protects,)))).fetchone()
        if run is None:
            return None
        definition = self.definition(tx, run['workflow_id'], run['version'])
        stages = {stage['id']: stage for stage in definition['stages']}
        attempt = connection.execute('SELECT * FROM evlet_workflow_attempts WHERE id=?', (run['attempt_id'],)).fetchone()
        executions = connection.execute('SELECT * FROM evlet_workflow_executions WHERE run_id=? ORDER BY id', (run['id'],)).fetchall()
        tasks = []
        for task in connection.execute('SELECT t.* FROM evlet_workflow_tasks t JOIN evlet_workflow_executions e ON e.id=t.execution_id '
                                       'WHERE e.attempt_id=? ORDER BY t.id', (run['attempt_id'],)).fetchall():
            people = connection.execute('SELECT user_id, via, active FROM evlet_workflow_assignees WHERE task_id=? ORDER BY user_id',
                                        (task['id'],)).fetchall()
            tasks.append({'id': task['id'], 'execution': task['execution_id'], 'review': task['review_id'], 'status': task['status'],
                          'required': json.loads(task['required']), 'closed_reason': task['closed_reason'],
                          'people': [row['user_id'] for row in people if row['active']]})
        decisions = [{'id': row['id'], 'attempt': row['attempt_id'], 'execution': row['execution_id'], 'task': row['task_id'],
                      'action': row['action_id'], 'kind': row['kind'], 'actor': row['actor'], 'feedback': row['feedback'], 'at': row['at']}
                     for row in connection.execute('SELECT * FROM evlet_workflow_decisions WHERE run_id=? ORDER BY id', (run['id'],)).fetchall()]
        stage = stages[run['stage_id']]
        return {'run': run['id'], 'workflow': run['workflow_id'], 'version': run['version'], 'protects': protect_object(run['protects']),
                'token': f'{run["id"]}.{run["counter"]}', 'status': run['status'], 'requester': run['requester'],
                'stage': {'id': stage['id'], 'label': stage['label'], 'kind': stage['kind']},
                'attempt': attempt['n'], 'attempt_status': attempt['status'], 'blocked': loads(run['blocked']),
                'executions': [{'id': row['id'], 'stage': row['stage_id'], 'attempt': row['attempt_id'], 'occurrence': row['occurrence'],
                                'status': row['status'], 'outcome': row['outcome'], 'routing': json.loads(row['routing'])} for row in executions],
                'tasks': tasks, 'decisions': decisions}


class Undecidable(Exception):
    def __init__(self, reason):
        self.reason = reason


def wire(value):
    """A fact value as JSON keeps it: exact decimal text, ISO dates, public ids."""
    from datetime import date, datetime
    from decimal import Decimal
    from . import calc
    if value is None or type(value) in (bool, int, str):
        return value
    if isinstance(value, RecordId):
        return str(value)
    if isinstance(value, (Decimal, calc.Num)):
        return calc.text(calc.parse(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, int):
        return int(value)
    raise Undecidable(f'{type(value).__name__} values cannot be compared')


class Run:
    """One run inside one transaction: stage entry, decisions and settlement."""

    def __init__(self, engine, tx, model, run):
        self.engine, self.tx, self.model = engine, tx, model
        self.connection = tx.connection
        self.run = run
        self.definition = engine.definition(tx, run['workflow_id'], run['version'])
        self.stages = {stage['id']: stage for stage in self.definition['stages']}
        self.compiled = engine.registry.workflows[model]
        self.types = engine.types(model)
        self.steps = 0

    # Projections

    def reload(self):
        self.run = self.connection.execute('SELECT * FROM evlet_workflow_runs WHERE id=?', (self.run['id'],)).fetchone()

    def set_run(self, **values):
        names = ', '.join(f'{name}=?' for name in values)
        self.connection.execute(f'UPDATE evlet_workflow_runs SET {names} WHERE id=?', (*values.values(), self.run['id']))
        self.reload()

    def attempt(self):
        return self.connection.execute('SELECT * FROM evlet_workflow_attempts WHERE id=?', (self.run['attempt_id'],)).fetchone()

    def execution(self):
        """The current stage's open execution in the current attempt."""
        return self.connection.execute("SELECT * FROM evlet_workflow_executions WHERE attempt_id=? AND parent_id IS NULL "
                                       "AND status IN ('active','blocked') ORDER BY id DESC LIMIT 1", (self.run['attempt_id'],)).fetchone()

    def tasks(self, execution_id, status=None):
        sql = 'SELECT * FROM evlet_workflow_tasks WHERE execution_id=?' + (' AND status=?' if status else '') + ' ORDER BY id'
        return self.connection.execute(sql, (execution_id, *((status,) if status else ()))).fetchall()

    def close_tasks(self, execution_id, reason):
        self.connection.execute("UPDATE evlet_workflow_tasks SET status='closed', closed_reason=? WHERE execution_id=? AND status='pending'",
                                (reason, execution_id))
        self.connection.execute("UPDATE evlet_workflow_assignees SET active=0 WHERE task_id IN "
                                "(SELECT id FROM evlet_workflow_tasks WHERE execution_id=? AND status='closed')", (execution_id,))

    def leave(self, execution_id, outcome, status='done'):
        self.connection.execute('UPDATE evlet_workflow_executions SET status=?, outcome=?, left_at=? WHERE id=?',
                                (status, outcome, now(), execution_id))

    # Stage entry

    def enter(self, stage_id):
        """Start a stage: decide its conditions and routes over frozen facts, then open its tasks.
        Automatic steps (a group with no review that continues) are bounded per transaction."""
        self.steps += 1
        if self.steps > LIMITS['automatic_steps']:
            raise blocked_error('This request passed through too many stages at once. An administrator must repair the workflow.')
        stage = self.stages[stage_id]
        occurrence = 1 + self.connection.execute('SELECT count(*) FROM evlet_workflow_executions WHERE run_id=? AND stage_id=?',
                                                 (self.run['id'], stage_id)).fetchone()[0]
        facts, unknown = self.engine.facts(self.tx, self.model, self.run['record_id'], self.run['requester'],
                                           facts_read(self.definition, stage))
        routing = {'facts': facts, 'unknown': unknown, 'when': {}, 'routes': {}, 'activated': [], 'skipped': []}
        execution_id = self.connection.execute('INSERT INTO evlet_workflow_executions (run_id, attempt_id, stage_id, occurrence, status, '
                                               "routing, entered_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                                               (self.run['id'], self.run['attempt_id'], stage_id, occurrence, canonical(routing), now())).lastrowid
        self.set_run(stage_id=stage_id, blocked=None)
        if stage['kind'] == 'end':
            self.finish(execution_id, stage['outcome'])
            return
        try:
            next_stage = self.decide_routing(stage, routing, facts)
            self.save_routing(execution_id, routing)
            if next_stage is not None:
                self.leave(execution_id, 'skipped')
                self.enter(next_stage)
                return
            group = stage['reviews']
            if group is None:
                self.open_task(execution_id, None, stage['assign'], {'kind': 'any'}, stage)
            else:
                activated = [item for item in group['items'] if item['id'] in routing['activated']]
                for item in activated if group['run'] == 'together' else activated[:1]:
                    self.open_task(execution_id, item, item['assign'], item['required'], stage)
        except Blocked as exc:
            self.block(execution_id, stage, exc.message, exc.causes)
            return
        self.set_run(status='draft' if stage['kind'] == 'work' else 'active')

    def decide_routing(self, stage, routing, facts):
        """Fill routing (action conditions, routes, activated reviews); the next stage when nothing applies and
        the group continues, else None. Raises Blocked for an undecided condition or a group that cannot run."""
        types = self.types
        if stage['require'] is not None:
            found = evaluate(stage['require']['when'], facts, types)
            if isinstance(found, Undecided):
                raise Blocked(self.engine.undecided_message(self.model, found, routing['unknown'], f'Whether {stage["label"]} may start'), found.causes)
            if not found:
                raise Blocked(stage['require']['message'])
        for action in stage['actions']:
            found = evaluate(action['when'], facts, types)
            if isinstance(found, Undecided):
                raise Blocked(self.engine.undecided_message(self.model, found, routing['unknown'], f'Whether {action["label"]} is offered'), found.causes)
            routing['when'][action['id']] = found
            if action['go_to'] is not None:
                routing['routes'][action['id']] = self.route(action['go_to'], facts, routing, f'Where {action["label"]} goes')
        group = stage['reviews']
        if group is None:
            return None
        for key in ('on_complete', 'on_reject', 'on_changes'):
            if group[key] is not None:
                routing['routes'][key] = self.route(group[key], facts, routing, f'Where {stage["label"]} goes')
        for item in group['items']:
            found = evaluate(item['when'], facts, types)
            if isinstance(found, Undecided):
                raise Blocked(self.engine.undecided_message(self.model, found, routing['unknown'], f'Whether the {item["label"]} review applies'),
                              found.causes)
            routing['activated' if found else 'skipped'].append(item['id'])
        if not routing['activated']:
            if group['when_none'] == 'continue':
                return routing['routes']['on_complete']
            raise Blocked(f'No review of {stage["label"]} applies to this request. An administrator must repair the workflow.')
        needed = self.needed(group['complete'], len(routing['activated']))
        if needed > len(routing['activated']):
            raise Blocked(f'{stage["label"]} needs {needed} reviews to approve, but only {len(routing["activated"])} apply.')
        return None

    def route(self, route, facts, routing, what):
        target, undecided = resolve_route(route, facts, self.types)
        if undecided is not None:
            raise Blocked(self.engine.undecided_message(self.model, undecided, routing['unknown'], what), undecided.causes)
        return target

    @staticmethod
    def needed(complete, available):
        return {'all': available, 'any': 1}.get(complete['kind']) or complete['count']

    def save_routing(self, execution_id, routing):
        self.connection.execute('UPDATE evlet_workflow_executions SET routing=? WHERE id=?', (canonical(routing), execution_id))

    def routing(self, execution):
        return json.loads(execution['routing'])

    def block(self, execution_id, stage, message, causes=()):
        self.connection.execute("UPDATE evlet_workflow_executions SET status='blocked' WHERE id=?", (execution_id,))
        self.set_run(status='blocked', blocked=canonical({'stage': stage['id'], 'message': message, 'causes': list(causes)}))

    def finish(self, execution_id, outcome):
        at = now()
        self.leave(execution_id, outcome)
        self.connection.execute('UPDATE evlet_workflow_attempts SET status=?, ended_at=?, end_reason=? WHERE id=?',
                                (outcome, at, outcome.capitalize(), self.run['attempt_id']))
        self.set_run(status=outcome, ended_at=at)

    # Assignment

    def open_task(self, execution_id, item, assign, required, stage):
        """Open a task for the eligible people of an assignment, or raise Blocked when too few are eligible."""
        label = item['label'] if item is not None else stage['label']
        review = stage['kind'] == 'review'
        people = self.eligible(execution_id, self.resolve(assign, label), review)
        if not people:
            raise Blocked(f'Nobody is eligible for {label}. An administrator must repair the assignment.')
        if required['kind'] == 'count' and required['count'] > len(people):
            raise Blocked(f'{label} needs {required["count"]} people to approve, but only {len(people)} are eligible. '
                          'An administrator must repair the assignment.')
        need = len(people) if required['kind'] == 'all' else required.get('count', 1)
        task_id = self.connection.execute("INSERT INTO evlet_workflow_tasks (execution_id, review_id, required, status) VALUES (?, ?, ?, 'pending')",
                                          (execution_id, item['id'] if item else None, canonical({**required, 'need': need}))).lastrowid
        for user_id, via in people:
            self.connection.execute('INSERT INTO evlet_workflow_assignees (task_id, user_id, via, principal_id, active) VALUES (?, ?, ?, ?, 1)',
                                    (task_id, user_id, via, user_id))
        return task_id

    def resolve(self, assign, label):
        """[(account id, via)] an assignment names, before eligibility checks."""
        kind, engine, tx = assign['kind'], self.engine, self.tx
        if kind == 'requester':
            return [(self.run['requester'], 'requester')]
        if kind == 'user':
            return [(assign['user'], 'user')]
        if kind == 'role':
            rows = self.connection.execute('SELECT id FROM evlet_users WHERE role=? AND active=1 ORDER BY id LIMIT ?',
                                           (assign['role'], ROLE_LIMIT + 1)).fetchall()
            if len(rows) > ROLE_LIMIT:
                raise Blocked(f'More than {ROLE_LIMIT} people have the role for {label}. Assign a smaller group.')
            return [(row[0], 'role') for row in rows]
        target = engine.registry.resolve(self.compiled.people.model) if self.compiled.people is not None else None
        if kind == 'fact':
            parent = self.compiled.people.parent
            managers = []
            for record_id in engine.linked(tx, self.run['requester'], target):
                value = tx.get(target, record_id)._values.get(parent)
                if value is not None:
                    managers.append(RecordId(value))
            people = engine.accounts_of(tx, target, managers)
            if not people:
                raise Blocked(f'The requester has no manager with an account, so nobody is eligible for {label}. '
                              'An administrator must repair the assignment.')
            return [(user_id, 'fact') for user_id in people]
        value = tx.get(self.model, self.run['record_id'])._values.get(assign['field'])
        return [(user_id, 'field') for user_id in engine.accounts_of(tx, target, [RecordId(value)] if value is not None else [])]

    def eligible(self, execution_id, people, review):
        """The people who may decide: active accounts that can read the record and, for reviews, hold `review`,
        are not the requester when self-approval is blocked and have not decided elsewhere in this stage."""
        ids = list(dict.fromkeys(user_id for user_id, _ in people))
        if not ids:
            return []
        via = {}
        for user_id, how in people:
            via.setdefault(user_id, how)
        marks = ', '.join('?' for _ in ids)
        accounts = {row['id']: row for row in self.connection.execute(
            f'SELECT id, username, role FROM evlet_users WHERE active=1 AND id IN ({marks})', ids).fetchall()}
        links = {}
        for row in self.connection.execute(f'SELECT user_id, model, record_id FROM evlet_user_links WHERE user_id IN ({marks}) '
                                           'ORDER BY model, record_id', ids).fetchall():
            links.setdefault(row['user_id'], []).append((row['model'], RecordId(row['record_id'])))
        decided = {row[0] for row in self.connection.execute(
            "SELECT principal FROM evlet_workflow_decisions WHERE execution_id=? AND kind IN ('approve','reject','request_changes')",
            (execution_id,)).fetchall()}
        policy, result = self.engine.business.policy, []
        block_self = review and self.definition['settings']['self_approval'] == 'block'
        for user_id in ids:
            account = accounts.get(user_id)
            if account is None or user_id in decided or block_self and user_id == self.run['requester']:
                continue
            user = User(account['id'], account['username'], account['role'], tuple(links.get(user_id, ())))
            if review and not policy.allows(user, self.model, 'review'):
                continue
            if not policy.visible(self.tx, user, self.model, [self.run['record_id']]):
                continue
            result.append((user_id, via[user_id]))
        return result

    # Decisions

    def decide(self, user, action_id, kind, review, feedback, meta=None):
        """Record one decision and settle what it completes; returns the decision id. The journal entry is
        written first, in the same transaction, so the decision row can name it (both or neither persist)."""
        run = self.run
        self.check_open()
        execution = self.execution()
        stage = self.stages[run['stage_id']]
        routing = self.routing(execution)
        offered = [action for action in stage['actions'] if routing['when'].get(action['id'])]
        if action_id is not None:
            action = next((item for item in offered if item['id'] == action_id), None)
            if action is None or kind is not None and action['kind'] != kind:
                raise AccessError(404, 'This action is not available')
        else:
            matches = [item for item in offered if item['kind'] == kind]
            if not matches:
                raise AccessError(404, 'This action is not available')
            if len(matches) > 1:
                raise ValidationError({'action': 'Choose the action'})
            action = matches[0]
        text = clean_feedback(feedback, action['feedback'])
        if action['kind'] == 'submit':
            # Sending a record for review changes what may happen to it, so it needs update access to it.
            self.engine.business.policy.record(self.tx, user, self.model, run['record_id'], 'update')
        task = self.task_for(user, execution, stage, action, review)
        decision = action['kind']
        before = (run['stage_id'], run['status'])
        attempt = self.attempt()
        labels = {item['id']: item['label'] for item in (stage['reviews'] or {}).get('items', ())}
        journal_id = self.journal(user, 'submitted' if decision == 'submit' else 'decided', {
            'attempt': attempt['n'], 'action': action['id'], 'label': action['label'], 'kind': decision,
            'stage': {'id': stage['id'], 'label': stage['label']},
            'review': None if task['review_id'] is None else {'id': task['review_id'], 'label': labels.get(task['review_id'])},
            'feedback': text})
        meta = meta or {}
        decision_id = self.connection.execute(
            'INSERT INTO evlet_workflow_decisions (run_id, attempt_id, execution_id, task_id, action_id, kind, actor, principal, role, '
            'feedback, at, version, generation, correlation_id, source, client, journal_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (run['id'], run['attempt_id'], execution['id'], task['id'], action['id'], decision, user.id, user.id, user.role, text, now(),
             run['version'], attempt['generation'], meta.get('correlation_id'), meta.get('source'), meta.get('client'), journal_id)).lastrowid
        if decision in VOTES:
            # A person counts once per stage: they leave every other review of it.
            self.connection.execute('UPDATE evlet_workflow_assignees SET active=0 WHERE user_id=? AND task_id IN '
                                    '(SELECT id FROM evlet_workflow_tasks WHERE execution_id=? AND id<>?)', (user.id, execution['id'], task['id']))
        try:
            self.settle(execution, stage, routing, task, action)
        except Blocked as exc:
            self.block(execution['id'], stage, exc.message, exc.causes)
        self.bump()
        self.note_stage(user, before)
        return decision_id

    def check_open(self):
        run = self.run
        if run['status'] == 'blocked':
            reason = json.loads(run['blocked'])
            raise blocked_error(reason['message'], reason.get('causes', ()))
        if run['status'] == 'held':
            raise WorkflowError('WORKFLOW_CHANGED', 'This request is on hold.')
        if run['status'] not in ('draft', 'active'):
            raise WorkflowError('WORKFLOW_CHANGED', CHANGED)

    def bump(self):
        """Raise the run's counter: its concurrency token changes."""
        self.connection.execute('UPDATE evlet_workflow_runs SET counter=counter+1 WHERE id=?', (self.run['id'],))
        self.reload()

    def journal(self, user, kind, data):
        """Append a journal entry about this run in the current transaction; returns its id."""
        from . import journal
        return journal.append(self.engine.business, self.tx, user, self.model, self.run['record_id'], kind,
                              {'workflow': {'run': self.run['id'], 'version': self.run['version'], **data}})

    def note_stage(self, user, before, reason=None):
        """A `stage` journal entry when the run moved to another stage or status since `before`."""
        stage_id, status = before
        if (self.run['stage_id'], self.run['status']) == (stage_id, status):
            return
        source, target = self.stages[stage_id], self.stages[self.run['stage_id']]
        blocked = loads(self.run['blocked'])
        self.journal(user, 'stage', {'attempt': self.attempt()['n'], 'from': {'id': source['id'], 'label': source['label']},
                                     'to': {'id': target['id'], 'label': target['label']}, 'status': self.run['status'],
                                     **({'reason': reason} if reason else {}),
                                     **({'blocked': blocked['message']} if blocked else {})})

    def withdraw(self, user, feedback, meta=None):
        """The requester takes the request back: open tasks close, the attempt ends as withdrawn and the run
        returns to its start stage. The next Send starts a linked new attempt."""
        run = self.run
        if run['status'] not in ('active', 'blocked') or self.attempt()['status'] != 'active':
            raise WorkflowError('WORKFLOW_CHANGED', 'Only a request awaiting review can be withdrawn.' if run['status'] == 'draft' else CHANGED)
        if not self.definition['settings']['withdraw']['allowed']:
            raise AccessError(403, 'This workflow does not allow withdrawing a request')
        self.engine.business.policy.record(self.tx, user, self.model, run['record_id'], 'update')
        if user.id != run['requester']:
            raise AccessError(403, 'Only the requester can withdraw this request')
        text = clean_feedback(feedback, {'mode': 'optional', 'max_length': 2000})
        execution = self.execution()
        before = (run['stage_id'], run['status'])
        attempt = self.attempt()
        stage = self.stages[run['stage_id']]
        journal_id = self.journal(user, 'withdrawn', {'attempt': attempt['n'], 'stage': {'id': stage['id'], 'label': stage['label']},
                                                      'feedback': text})
        meta = meta or {}
        decision_id = self.connection.execute(
            'INSERT INTO evlet_workflow_decisions (run_id, attempt_id, execution_id, task_id, action_id, kind, actor, principal, role, '
            "feedback, at, version, generation, correlation_id, source, client, journal_id) VALUES (?, ?, ?, NULL, NULL, 'withdraw', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run['id'], run['attempt_id'], None if execution is None else execution['id'], user.id, user.id, user.role, text, now(),
             run['version'], attempt['generation'], meta.get('correlation_id'), meta.get('source'), meta.get('client'), journal_id)).lastrowid
        self.restart('withdrawn', 'Withdrawn by the requester')
        self.bump()
        self.note_stage(user, before, 'Withdrawn by the requester')
        return decision_id

    def restart(self, status, reason):
        """End the current attempt with `status` and send the run back to its start stage in a new attempt."""
        execution = self.execution()
        if execution is not None:
            self.close_tasks(execution['id'], reason)
            self.leave(execution['id'], 'closed', status='closed')
        self.new_attempt(status, reason)
        self.steps = 0
        self.enter(self.definition['start'])

    def task_for(self, user, execution, stage, action, review):
        """The open task this person decides with the action; 403 when they are not assigned, 409 when their
        task was already decided or closed (a late vote)."""
        active = self.connection.execute('SELECT 1 FROM evlet_users WHERE id=? AND active=1', (user.id,)).fetchone()
        if active is None:
            # Eligibility is checked again at each decision: an account disabled after assignment decides nothing.
            raise AccessError(403, 'You cannot decide on this request')
        if action['kind'] in VOTES:
            if not self.engine.business.policy.allows(user, self.model, 'review'):
                raise AccessError(403, 'You cannot decide on this request')
            if self.definition['settings']['self_approval'] == 'block' and user.id == self.run['requester']:
                raise AccessError(403, 'You cannot decide on your own request')
        if action['who'] == {'kind': 'requester'} and user.id != self.run['requester']:
            raise AccessError(403, 'Only the requester can do this')
        rows = self.connection.execute('SELECT t.*, a.active FROM evlet_workflow_tasks t JOIN evlet_workflow_assignees a ON a.task_id=t.id '
                                       'WHERE t.execution_id=? AND a.user_id=? ORDER BY t.id', (execution['id'], user.id)).fetchall()
        if review is not None:
            rows = [row for row in rows if row['review_id'] == review]
        decided = self.connection.execute('SELECT 1 FROM evlet_workflow_decisions WHERE execution_id=? AND principal=? AND clarifies IS NULL '
                                          "AND kind IN ('approve','reject','request_changes')", (execution['id'], user.id)).fetchone()
        pending = [row for row in rows if row['status'] == 'pending' and row['active'] and not (decided and action['kind'] in VOTES)]
        if action['who'].get('kind') == 'people':
            pending = [row for row in self.tasks(execution['id'], 'pending')]
            allowed = {user_id for item in action['who']['people'] for user_id, _ in self.eligible(execution['id'], self.resolve(item, action['label']), False)}
            if user.id not in allowed:
                raise AccessError(403, 'You cannot do this at this stage')
        if not pending:
            if rows:
                raise WorkflowError('WORKFLOW_CHANGED', CHANGED)
            raise AccessError(403, 'You cannot decide on this request')
        if len(pending) > 1:
            raise ValidationError({'review': 'Choose the review you are deciding'})
        return pending[0]

    def settle(self, execution, stage, routing, task, action):
        kind, connection = action['kind'], self.connection
        if kind in ('submit', 'move'):
            connection.execute("UPDATE evlet_workflow_tasks SET status='done' WHERE id=?", (task['id'],))
            self.close_tasks(execution['id'], 'Done by someone else')
            self.leave(execution['id'], kind)
            if kind == 'submit':
                connection.execute("UPDATE evlet_workflow_attempts SET status='active', started_at=?, generation=?, fingerprint=? WHERE id=?",
                                   (now(), self.engine.generation(self.tx, self.model, self.run['record_id']),
                                    self.engine.fingerprint(self.tx, self.model, self.run['record_id']), self.run['attempt_id']))
            self.enter(routing['routes'][action['id']])
            return
        if kind == 'request_changes':
            connection.execute("UPDATE evlet_workflow_tasks SET status='changes' WHERE id=?", (task['id'],))
            self.close_tasks(execution['id'], 'Changes were requested')
            self.leave(execution['id'], 'changes')
            target = routing['routes']['on_changes'] if stage['reviews'] is not None else routing['routes'][action['id']]
            self.new_attempt('changes', 'Changes were requested')
            self.enter(target)
            return
        if kind == 'reject':
            connection.execute("UPDATE evlet_workflow_tasks SET status='rejected' WHERE id=?", (task['id'],))
        else:
            need = json.loads(task['required'])['need']
            votes = connection.execute("SELECT count(DISTINCT principal) FROM evlet_workflow_decisions WHERE task_id=? AND kind='approve' "
                                       'AND clarifies IS NULL', (task['id'],)).fetchone()[0]
            if votes < need:
                self.check_attainable(execution, stage)
                return
            connection.execute("UPDATE evlet_workflow_tasks SET status='approved' WHERE id=?", (task['id'],))
        group = stage['reviews']
        if group is None:
            self.close_tasks(execution['id'], 'No longer required')
            self.leave(execution['id'], 'approved' if kind == 'approve' else 'rejected')
            self.enter(routing['routes'][action['id']])
            return
        self.settle_group(execution, stage, group, routing)

    def settle_group(self, execution, stage, group, routing):
        tasks = {row['review_id']: row for row in self.tasks(execution['id'])}
        activated = routing['activated']
        approved = [rid for rid in activated if rid in tasks and tasks[rid]['status'] == 'approved']
        rejected = [rid for rid in activated if rid in tasks and tasks[rid]['status'] == 'rejected']
        waiting = [rid for rid in activated if rid not in tasks or tasks[rid]['status'] == 'pending']
        needed = self.needed(group['complete'], len(activated))
        if rejected and group['rejection'] == 'veto' or len(approved) + len(waiting) < needed:
            self.close_tasks(execution['id'], 'No longer required')
            self.leave(execution['id'], 'rejected')
            self.enter(routing['routes']['on_reject'])
            return
        if len(approved) >= needed:
            self.close_tasks(execution['id'], 'No longer required')
            self.leave(execution['id'], 'approved')
            self.enter(routing['routes']['on_complete'])
            return
        if group['run'] == 'sequence' and not any(rid in tasks and tasks[rid]['status'] == 'pending' for rid in activated):
            following = next(rid for rid in activated if rid not in tasks)
            item = next(item for item in group['items'] if item['id'] == following)
            self.open_task(execution['id'], item, item['assign'], item['required'], stage)
        self.check_attainable(execution, stage)

    def check_attainable(self, execution, stage):
        """Block when a pending review can no longer reach its quorum because its people decided elsewhere."""
        labels = {item['id']: item['label'] for item in (stage['reviews'] or {}).get('items', ())}
        for task in self.tasks(execution['id'], 'pending'):
            need = json.loads(task['required'])['need']
            votes = self.connection.execute("SELECT count(*) FROM evlet_workflow_decisions WHERE task_id=? AND kind='approve'",
                                            (task['id'],)).fetchone()[0]
            people = self.connection.execute('SELECT count(*) FROM evlet_workflow_assignees a WHERE a.task_id=? AND a.active=1 AND NOT EXISTS '
                                             '(SELECT 1 FROM evlet_workflow_decisions d WHERE d.task_id=a.task_id AND d.principal=a.user_id)',
                                             (task['id'],)).fetchone()[0]
            if votes + people < need:
                label = labels.get(task['review_id'], stage['label'])
                raise Blocked(f'Nobody else is eligible to decide {label}: everyone assigned has decided another review of '
                              f'{stage["label"]}. An administrator must repair the assignment.')

    def end(self, reason):
        execution = self.execution()
        if execution is not None:
            self.close_tasks(execution['id'], reason)
            self.leave(execution['id'], 'closed', status='closed')
        at = now()
        self.connection.execute("UPDATE evlet_workflow_attempts SET status='ended', ended_at=?, end_reason=? WHERE id=?",
                                (at, reason, self.run['attempt_id']))
        self.set_run(status='ended', ended_at=at, blocked=None)
        self.bump()

    def new_attempt(self, status, reason):
        at, current = now(), self.attempt()
        self.connection.execute('UPDATE evlet_workflow_attempts SET status=?, ended_at=?, end_reason=? WHERE id=?',
                                (status, at, reason, current['id']))
        attempt_id = self.connection.execute("INSERT INTO evlet_workflow_attempts (run_id, n, status, generation, previous_id) "
                                             "VALUES (?, ?, 'draft', ?, ?)",
                                             (self.run['id'], current['n'] + 1, self.engine.generation(self.tx, self.model, self.run['record_id']),
                                              current['id'])).lastrowid
        self.set_run(attempt_id=attempt_id)


# Control characters other than tab, line feed and carriage return (C0, DEL and C1).
CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')


def clean_feedback(value, rules):
    """Trimmed feedback text, or None. Feedback is plain text: valid Unicode (no lone surrogates), normalized
    to NFC, without NUL or other control characters except tab and line breaks, and at most max_length
    characters after trimming. Required feedback must have text; whitespace alone is none."""
    if value is not None and not isinstance(value, str):
        raise ValidationError({'feedback': 'Feedback is text'})
    text = value or ''
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        raise ValidationError({'feedback': 'Feedback must be valid text'}) from None
    text = unicodedata.normalize('NFC', text).strip()
    if CONTROL.search(text):
        raise ValidationError({'feedback': 'Feedback cannot contain control characters'})
    if rules['mode'] == 'hidden' and text:
        raise ValidationError({'feedback': 'This action takes no feedback'})
    if rules['mode'] == 'required' and not text:
        raise ValidationError({'feedback': 'Enter feedback'})
    if len(text) > rules['max_length']:
        raise ValidationError({'feedback': f'Feedback is at most {rules["max_length"]} characters'})
    return text or None
