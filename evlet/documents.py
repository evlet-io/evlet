"""Authoritative document operations sharing the existing transaction and policy."""

from types import MappingProxyType, MethodType
from uuid import uuid4
from contextlib import contextmanager
import inspect
import json
import re

from . import qb
from .auth import AccessError, has_role
from .database import ConflictError, ROW_LIMIT, conflict
from .declarations import Ref
from .fields import CurrentUser, MISSING, Link
from .identity import RecordId
from .naming import drop_echoes
from .permissions import trusted
from .models import ASSIGNED_ID, ValidationError, refuse_id
from .query import SqlQuery
from .reactivity import evaluate, graph, round_field, plans
from .calc import CalcError, Document, LIMITS, MAX_HOPS, is_finite, to_decimal
from . import sections


class Snapshot:
    def __init__(self, values):
        object.__setattr__(self, '_data', MappingProxyType({key: freeze(value) for key, value in values.items()}))

    def __getattr__(self, name):
        try: return self._data[name]
        except KeyError: raise AttributeError(name) from None

    def __setattr__(self, name, value):
        raise AttributeError('Snapshots are immutable')


def freeze(value):
    if isinstance(value, dict): return Snapshot(value)
    if isinstance(value, (list, tuple)): return tuple(freeze(item) for item in value)
    return value


class Draft:
    def __init__(self, definition, values=None, *, context=None, parent=None, source=None, record_id=None):
        object.__setattr__(self, '_definition', definition)
        object.__setattr__(self, '_values', dict(values or {}))
        object.__setattr__(self, '_rows', {})
        # Section rows of Tables declared with sections=True: {table: [SectionRow]} (evlet/sections.py).
        object.__setattr__(self, '_sections', {})
        object.__setattr__(self, '_sources', {})
        object.__setattr__(self, 'context', context)
        object.__setattr__(self, 'parent', parent)
        # The model class, for hooks such as derive() that run on a draft (a field named `model` wins).
        if not hasattr(definition, 'model') and not {'model'} & {*definition.__declared_fields__, *definition.__tables__, *definition.__sources__}:
            object.__setattr__(self, 'model', definition)
        if 'source' not in definition.__declared_fields__:
            # A flat record may name a field `source` (an effect target's Reference); the field wins.
            object.__setattr__(self, 'source', source)
        object.__setattr__(self, 'id', record_id)
        object.__setattr__(self, 'key', str(record_id) if record_id else uuid4().hex)
        object.__setattr__(self, '_readonly', False)
        object.__setattr__(self, '_submitted', frozenset())
        # Whether the caller placed this row (a full row list, or `order` in a change set).
        object.__setattr__(self, '_placed', False)
        # Field rule states from the last settlement, and source values as the caller left them (lock checks).
        object.__setattr__(self, '_rules', {})
        object.__setattr__(self, '_caller', {})
        # While derive() runs: the names it may assign on this draft (None is no limit).
        object.__setattr__(self, '_derives', None)

    def __getattr__(self, name):
        if name in self._definition.__declared_fields__:
            return self._values.get(name)
        if name in self._definition.__tables__:
            rows = self._rows.get(name, [])
            if sections.enabled(self._definition, name) and not isinstance(rows, sections.TableRows):
                # The real rows, with sections() for the grouping; kept in place so list changes still apply.
                rows = sections.TableRows(rows, self._sections.setdefault(name, []) if isinstance(self._sections, dict) else ())
                if isinstance(self._rows, dict) and name in self._rows:
                    self._rows[name] = rows
            return rows
        if name in self._definition.__sources__:
            return self._sources.get(name)
        value = getattr(self._definition, name)
        return MethodType(value, self) if inspect.isfunction(value) else value

    def __setattr__(self, name, value):
        if self._readonly:
            raise AttributeError('Evaluation drafts are read only')
        if name == 'id':
            raise ValidationError({'id': ASSIGNED_ID})
        if self._derives is not None and name not in self._derives:
            raise AttributeError(f'derive() sets only what Meta.derives names, and the rows of those Tables; '
                                 f'{self._definition.__name__}.{name} is not one of them')
        if name in self._definition.__declared_fields__:
            if is_finite(self._definition.__declared_fields__[name]):
                raise ValidationError({name: 'COMPUTED_FIELD_WRITE: this value is calculated'})
            if name == self._definition.__state__:
                raise ValidationError({name: 'This changes only through its moves'})
            if self._definition.__declared_fields__[name].kind == 'sequence':
                from .sequences import ASSIGNED_BY_ENGINE
                raise ValidationError({name: ASSIGNED_BY_ENGINE})
            self._values[name] = value
        elif name in self._definition.__tables__:
            limit = self._definition.__tables__[name].max_rows
            sectioned = sections.enabled(self._definition, name)
            if not isinstance(value, (list, tuple)):
                raise ValidationError({name: f'Use at most {limit} rows'})
            marked = [isinstance(item, sections.SectionRow) or sectioned and sections.is_entry(item) for item in value]
            if sum(1 for flag in marked if not flag) > limit:
                raise ValidationError({name: f'Use at most {limit} rows'})
            # The registry's row model (declared Child classes are copied with an owner link and position).
            owned = self.context._business.children.get((self._definition, name)) if self.context is not None else None
            child = owned.model if owned is not None else self._definition.__tables__[name].child
            old_rows = list(self._rows.get(name, []))
            items = [item if flag else item if isinstance(item, Draft) else Draft(child, item, context=self.context, parent=self, source=self.source)
                     for item, flag in zip(value, marked)]
            if any(marked):
                if not sectioned:
                    raise ValidationError({name: f'{name} has no section rows; declare Table(..., sections=True)'})
                items = [self._section_item(name, item) if flag else item for item, flag in zip(items, marked)]
                rows, found = sections.split(items)
                if len(found) > sections.MAX_SECTIONS:
                    raise ValidationError({name: f'Use at most {sections.MAX_SECTIONS} section rows'})
            else:
                rows = items
                found = sections.reanchor(old_rows, rows, self._sections.get(name, [])) if sectioned else []
            ordered = sections.combined(rows, found) if sectioned else rows
            if 'evlet_position' in child.__fields__:
                for row, position in zip(ordered, positions(ordered, set())):
                    row._values['evlet_position'] = position
            if sectioned:
                self._sections[name] = found
                rows = sections.TableRows(rows, found)
            self._rows[name] = rows
        else:
            raise AttributeError('Unknown draft field: ' + name)

    def _section_item(self, name, item):
        """A SectionRow from {"section": label} (a new one) or {"id": ..., "section": label} (one this Table holds)."""
        if isinstance(item, sections.SectionRow):
            return item
        sections.check_entry(item, name)
        label = sections.label_of(item['section'], name)
        if item.get('id') is None:
            return sections.SectionRow(label)
        known = {section.id: section for section in self._sections.get(name, [])}
        section_id = item_id(item['id'])
        if section_id not in known:
            raise ValidationError({name: CHILD_ID})
        found = known[section_id].copy()
        found.label = label
        return found

    def read(self, path):
        parts = path.split('.')
        current = self
        if parts[0] in ('parent', 'source'):
            current = getattr(self, parts.pop(0))
        for part in parts:
            if current is None: return None
            if isinstance(current, (list, tuple)):
                current = [getattr(row, part, None) if not isinstance(row, dict) else row.get(part) for row in current]
            else:
                current = getattr(current, part, None)
        return current

    def values(self):
        return {'id': self.id, **self._values, **{name: [row.values() for row in rows] for name, rows in self._rows.items()}}

    def stored_values(self):
        return {'id': self.id, **{name: self._values.get(name) for name in self._definition.__fields__},
                **{name: [row.stored_values() for row in rows] for name, rows in self._rows.items()}}


# Where a change came from, recorded with it.
SOURCES = ('User', 'API', 'Automation', 'Scheduler', 'System', 'Integration')


class Context:
    """Permission-aware reads and document writes in one unit of work."""
    def __init__(self, business, tx, user, *, readonly=False, active=None, source=None, correlation_id=None):
        if not business.database.owns(tx):
            raise ValueError('Document context belongs to another database')
        source = getattr(tx, '_change_source', 'API' if user is not None and user.access is not None else 'User') if source is None else source
        correlation_id = getattr(tx, '_change_correlation', None) if correlation_id is None else correlation_id
        if source not in SOURCES:
            raise ValueError('Unknown change source')
        if correlation_id is not None and (not isinstance(correlation_id, str) or len(correlation_id) > 200):
            raise ValueError('correlation_id must be text of at most 200 characters')
        if not readonly:
            from .audit import actor_id
            tx._change_source, tx._change_correlation = source, correlation_id
            # Writes are recorded under this user; the engine's own contexts (effects) keep the person's.
            if actor_id(user) is not None:
                tx.actor = actor_id(user)
        self.source, self.correlation_id = source, correlation_id
        self._business, self._tx, self.user = business, tx, user
        self.readonly = readonly
        self._active = [] if active is None else active
        self._recalculate = None  # (model, record_id) explicitly allowed to recalculate under the active plan.
        self._person = None  # (model, record_id) of the write a person sent over HTTP; Controls rules apply to it.
        # Set only on the private copy a state manager hook or an @action handler gets while it runs (handler_context),
        # or 'read' on the read-only copy derive() and before_close() get (hook_context).
        self._session_open = False

    @property
    def session(self):
        """The transaction session (evlet.Session) of the running move or action: trusted app code only.

        Open only on the context a @state, @state.exit or @release hook or an @action handler gets as self.context while it
        runs. Writes through it bypass row and field permissions and join the move or action's
        transaction, so a failure anywhere rolls back the State, its Sequence number and every row written.
        Every other context refuses it: business.context(), services, reports, jobs, and everything Eva,
        assistants, tools, MCP and the connector reach.

        derive() and before_close() get it read only (evlet.database.ReadSession): get, list, count,
        related and aggregate inside the write's transaction, and no writes."""
        if self._session_open == 'read':
            from .database import ReadSession
            return ReadSession(self._tx)
        if not self._session_open:
            raise AccessError(403, 'The transaction session is available only to a state manager hook or an '
                                   '@action handler, as self.context.session while it runs')
        return self._tx

    def read_context(self):
        return Context(self._business, self._tx, self.user, readonly=True, active=self._active, source=self.source, correlation_id=self.correlation_id)

    def _model(self, value):
        if self.user is None:
            raise ValueError('A document context needs a User: pass the account whose permissions apply, '
                             'such as User(1, "admin", "admin"), to business.context()')
        model = self._business.registry.resolve(value)
        if model in self._business.child_models:
            raise AccessError(403, 'Access child rows through their parent document')
        return model

    def get(self, model, record_id):
        model = self._model(model)
        record = self._business.policy.record(self._tx, self.user, model, record_id)
        # Python code sees every readable row, not the first page an HTTP detail embeds.
        value = self._business.detail(self._tx, self.user, record, row_limit=None)
        children = value.pop('children', {})
        return Snapshot({**value, **children})

    def print(self, design, record_id):
        """PDF bytes of a record printed with a PrintDesign, with this context's user and permissions."""
        from .printing import INSTALL, available, document_context, render
        model = self._model(design.model)
        if design not in self._business.prints.current().values():
            raise ValueError('Register the print design with Application(prints=...)')
        if not available():
            raise AccessError(503, INSTALL)
        record = self._business.policy.record(self._tx, self.user, model, record_id)
        self._business.policy.require(self.user, model, 'print')
        return render(design, document_context(self._business, self._tx, self.user, model, record), self._business)

    def list(self, model, *, filters=None, order_by=('id',), limit=50, offset=0):
        model = self._model(model)
        business, tx = self._business, self._tx
        business.policy.require(self.user, model, 'read')
        business.policy.query_fields(self.user, model, filters, order_by, children=business.children.values())
        if type(limit) is not int or not 1 <= limit <= ROW_LIMIT or type(offset) is not int or not 0 <= offset <= 2**63-1:
            raise ValidationError({'page': f'Use a page of 1 to {ROW_LIMIT} rows and a nonnegative offset'})
        query = SqlQuery(business.registry, model, children=business.children.values())
        where = qb.and_(query.where(filters), business.policy.scope(self.user, model))
        source = qb.table(business.registry.name_for(model), 'record')
        page = qb.select(source).where(where).order_by(*query.ordering(order_by)).limit(limit).offset(offset)
        rows = tx.connection.run(page).all()
        total = tx.connection.run(qb.select(source, qb.count()).where(where)).value()
        return {'rows': business.policy.serialize_many(tx, self.user, [model._from_row(row) for row in rows]),
                'total': total, 'limit': limit, 'offset': offset}

    def count(self, model, *, filters=None):
        return self.list(model, filters=filters, limit=1)['total']

    def aggregate(self, model, *, measures, group_by=(), filters=None):
        """Database.aggregate with this user's row scope and field permissions."""
        from .measures import scoped_aggregate
        return scoped_aggregate(self._business, self._tx, self.user, self._model(model), measures, group_by, filters)

    def measures(self, model, record_id=None, *, filters=None):
        """Declared measures this user may read, with row scopes applied."""
        from .measures import scoped_measures
        return scoped_measures(self._business, self._tx, self.user, self._model(model), None if record_id is None else known_id(record_id), filters)

    def history(self, model, record_id, **page):
        from .journal import entries
        return entries(self, model, known_id(record_id), **page)

    def create(self, model, **values):
        return self._write(model, None, values)

    def unlocked_periods(self, reason):
        """For migrations and data repairs in trusted code only: write records dated in closed periods inside
        this block. The reason is required; it is logged and audited (Session.unlocked_periods)."""
        if self.readonly:
            raise AccessError(403, 'This context is read only')
        return self._tx.unlocked_periods(reason)

    def update(self, model, record_id, **values):
        return self._write(model, record_id, values)

    def action(self, model, record_id, name, **arguments):
        return self._write(model, record_id, {}, action=name, arguments=arguments)

    def move(self, model, record_id, key, *, remark=None, **values):
        """Move a record by its rule `key`, with the same checks as POST /api/{model}/{id}/moves/{key}. `values`
        fill the fields the move asks for (statemanager.move_asks), checked with the move."""
        return self._write(model, record_id, values, move=key, remark=remark)

    def delete(self, model, record_id):
        if self.readonly: raise AccessError(403, 'This context is read only')
        model, record_id = self._model(model), known_id(record_id)
        key = (model, record_id)
        if key in self._active or len(self._active) >= 16:
            raise ValidationError({'_form': 'Recursive document change detected'})
        self._active.append(key)
        try:
            with self._savepoint():
                self._delete(model, record_id)
        finally:
            self._active.pop()

    def _delete(self, model, record_id):
        business, tx, user = self._business, self._tx, self.user
        record = business.policy.record(tx, user, model, record_id, 'delete')
        from .statemanager import check_delete
        check_delete(self, model, record)
        before, _ = load_draft(self, model, record_id, {}, preview=True)
        subtree = {}
        for prefix, item in walk(before):
            if item is not before:
                business.policy.require(user, item._definition, 'delete')
                subtree.setdefault(item._definition, []).append(item.id)
        for definition, ids in subtree.items():
            if set(ids) - business.policy.visible(tx, user, definition, ids, 'delete'):
                raise AccessError(404, 'Record not found')
        for child in business.children.values():
            if child.parent is not model: continue
            rows = before._rows.get(child.name, [])
            delete_rows(business, tx, child.model, [row.id for row in rows])
        with tx.maintenance():
            tx.delete(model, record.id)
        if business.registry.plan_for(model) is not None:
            tx.connection.run(qb.delete('evlet_calculation_versions').where(resource=business.registry.name_for(model), record_id=record.id))
        tx.connection.run(qb.delete('evlet_attachments').where(model=business.registry.name_for(model), record_id=record_id))
        from .unsaved import Unsaved
        Unsaved.record_deleted(tx.connection, business.registry.name_for(model), record_id)
        from . import journal
        journal.deleted(self, model, record.id)
        business.changed(tx, model, record_id, deleted=True)

    def release(self, model, record_id, *, remark=None):
        """Release the record as it is (the Release button): @release runs; the same checks as POST .../release."""
        return self._write(model, record_id, {}, release=True, remark=remark)

    def versions(self, model, record_id):
        from .statemanager import versions
        return versions(self, self._model(model), known_id(record_id))

    def version(self, model, record_id, number):
        from .statemanager import version
        return version(self, self._model(model), known_id(record_id), number)

    def version_diff(self, model, record_id, first, second):
        """Compare two versions; `second` may be "current"."""
        from .statemanager import version_diff
        return version_diff(self, self._model(model), known_id(record_id), first, second)

    def _write(self, model, record_id, values, *, action=None, arguments=None, move=None, remark=None, release=False):
        if self.readonly:
            raise AccessError(403, 'This context is read only')
        model = self._model(model)
        if not getattr(model, '__declaration_mode__', False):
            raise ValueError('Contextual writes require declarative models')
        refuse_id(values)
        record_id = None if record_id is None else known_id(record_id)
        key = (model, record_id)
        if key in self._active or len(self._active) >= 16:
            raise ValidationError({'_form': 'Recursive document change detected'})
        self._active.append(key)
        try:
            with self._savepoint():
                return operate(self, model, record_id, values, action=action, arguments=arguments, move=move, remark=remark, release=release)
        except ValidationError as exc:
            raise ValidationError(self._business.visible_errors(self.user, model, exc.errors, tx=self._tx)) from None
        finally:
            self._active.pop()

    @contextmanager
    def _savepoint(self):
        """One document operation is all or nothing even when trusted code catches its failure and the
        outer transaction commits: any exception rolls back to a savepoint taken before it. A unique or
        foreign key violation surfaces as ConflictError, a unique Meta.indexes entry as ValidationError."""
        connection = self._tx.connection
        try:
            with connection.savepoint(f'evlet_operation_{len(self._active)}'):
                yield
        except BaseException as exc:
            failure = connection.integrity_failure(exc)
            if failure is not None:
                raise conflict(self._business.registry, failure) from exc
            raise

    def enqueue(self, task, payload, *, key=None, target=None, run_at=None):
        """Queue a registered Task in this context's transaction; returns its JobReceipt.

        The job is part of the transaction: it is queued only if the transaction commits, and once it
        does, the worker in this process is woken to run it at once. It runs as this context's user (read
        again when it starts), or with this context's role when the context acts as the system. `target`
        (Model, id) needs update access now and again when the job runs. `run_at` (UTC seconds) delays it;
        until then the job waits in the queue. In an @action, use self.context.enqueue(...); return the
        receipt to answer 202 with the job. A running job queues its own next run with JobContext.follow_up."""
        if self.readonly:
            raise AccessError(403, 'This context is read only')
        jobs = self._business.jobs
        if jobs is None:
            raise ValueError('Jobs are unavailable')
        if target is not None: self._business.policy.record(self._tx, self.user, target[0], target[1], 'update')
        receipt = jobs.enqueue(self._tx, task, payload, user=self.user if self.user.id else None,
                               system_role=self.user.role if not self.user.id else None, key=key, target=target, run_at=run_at)
        if jobs.wake not in self._tx._after_commit:
            self._tx._after_commit.append(jobs.wake)
        return receipt


def load_draft(context, model, record_id, values, *, preview=False):
    business, tx, user = context._business, context._tx, context.user
    operation = 'update' if record_id else 'create'
    business.policy.require(user, model, 'read' if preview and record_id else operation)
    current = business.policy.record(tx, user, model, record_id, 'read' if preview else operation) if record_id else None
    scalars, collections = business.split_body(values)
    if current is None:
        drop_empty_name(business.registry, model, scalars)
    from .statemanager import state_write
    from .sequences import sequence_write
    state_write(model, scalars, current)
    sequence_write(model, scalars, current, business.policy.fields(user, model))
    computed_write(business, model, scalars, '')
    derived_write(model, scalars, collections, '')
    drop_echoes(model, scalars, current._values if current else None)
    business.policy.check_values(tx, user, model, scalars)
    # A new record takes the defaults Controls set on this instance for fields the caller left out.
    values = business.controls.custom.prepare(model, scalars) if current is None else scalars
    draft = Draft(model, {**(dict(current._values) if current else {}), **values}, context=context, record_id=record_id)
    object.__setattr__(draft, '_submitted', frozenset(scalars))
    old = Draft(model, dict(current._values), context=context, record_id=record_id) if current else None
    # Finite-v1 roots bound the rows they load; legacy roots keep the per-collection limit only.
    bounded = [0, 0] if business.registry.plan_for(model) is not None else None
    saved = SavedRows(context, model, record_id) if record_id else None
    targets = LinkTargets(context)
    targets.prefetch(submitted_links(business, model, collections))
    load_rows(context, model, draft, old, record_id, collections, preview, '', set(), bounded, saved, targets)
    if business.registry.plan_for(model) is not None and sum(1 for _ in walk(draft)) - 1 > LIMITS['document_rows']:
        raise ValidationError({'children': 'This document exceeds the owned row limit'})
    return draft, old


def drop_empty_name(registry, model, values):
    """A null naming field on create is no value: the server names the record."""
    rules = registry.naming.get(model)
    if rules is not None and rules.field in values and values[rules.field] is None:
        del values[rules.field]


def computed_write(business, model, values, prefix):
    """Finite-v1 calculated fields have one writer: clients submit sources only."""
    if business.registry.plan_for(model) is None:
        return
    written = [name for name in values if name in model.__declared_fields__ and is_finite(model.__declared_fields__[name])]
    if written:
        raise ValidationError({prefix + name: 'COMPUTED_FIELD_WRITE: this value is calculated' for name in written})


DERIVED = 'DERIVED_FIELD_WRITE: this value is set by the record'


def derived_write(model, scalars, collections, prefix):
    """Fields, Tables and row fields that derive() sets (Meta.derives) have one writer: callers cannot send them."""
    derives = getattr(model, '__derives__', ())
    written = [name for name in (*scalars, *collections) if name in derives]
    if written:
        raise ValidationError({prefix + name: DERIVED for name in written})


def row_bounds(business, child):
    """(min_rows, max_rows, cap) of an owned collection: declared bounds, and the most stored rows it may load.

    Table declarations carry min_rows and max_rows. Legacy ChildTable registrations and tables under
    legacy roots keep the 100-row limit; finite-v1 tables may load up to the engine limit so that a
    collection saved before max_rows was lowered can still be edited down.
    """
    from .declarations import Table
    table = child.parent.__tables__.get(child.name)
    if not isinstance(table, Table) or table.child not in child.model.__mro__:
        return 0, ROW_LIMIT, ROW_LIMIT
    finite = business.registry.plan_for(child.model) is not None
    return table.min_rows, table.max_rows, LIMITS['collection_rows'] if finite else ROW_LIMIT


def owned_rows(tx, child, owner_ids, *, scope=None, limit=None):
    """Rows of one owned collection for many owners, in position order: {owner_id: (records, total)}.

    One statement per chunk of owner IDs. `scope` is a row-scope condition (Policy.scope) from the
    policy. With a limit each owner keeps its first `limit` rows while `total` counts them all.
    """
    from .permissions import CHUNK
    model = child.model
    link = f'record.{child.link}'
    order = ('record.evlet_position', 'record.id') if 'evlet_position' in model.__fields__ else ('record.id',)
    scope = qb.TRUE if scope is None else scope
    source = qb.table(tx.registry.name_for(model), 'record')
    ids, result = list(dict.fromkeys(owner_ids)), {}
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        if limit is None:
            query = qb.select(source).where(scope, qb.in_(link, part)).order_by(link, *order)
        else:
            # Window functions (SQLite 3.25+) keep the first rows per owner and count them all.
            ranked = qb.select(source, qb.all_columns('record'),
                               qb.named(qb.over(qb.row_number(), partition_by=[link], order_by=order), 'evlet_rank'),
                               qb.named(qb.over(qb.count(), partition_by=[link]), 'evlet_total')).where(scope, qb.in_(link, part))
            query = (qb.select(qb.from_query(ranked, 'ranked')).where(qb.le('ranked.evlet_rank', limit))
                     .order_by(f'ranked.{child.link}', 'ranked.evlet_rank'))
        for row in tx.connection.run(query).all():
            record = model._from_row(row)
            records, total = result.setdefault(getattr(record, child.link), ([], [0]))
            records.append(record)
            total[0] = row['evlet_total'] if limit is not None else total[0] + 1
    return {owner: (records, total[0]) for owner, (records, total) in result.items()}


class SavedRows:
    """A saved document's owned rows, and the ones this user cannot see, loaded one table level at a time.

    Each level costs one statement per chunk of owner IDs for the rows and one for their row scope,
    instead of statements per row.
    """
    def __init__(self, context, model, record_id):
        business, tx, user = context._business, context._tx, context.user
        bounded = business.registry.plan_for(model) is not None
        self.rows, self.hidden, self.sections = {}, set(), {}
        level, loaded = {model: [record_id]}, 0
        while level:
            following = {}
            for owner, ids in level.items():
                for child in business.children.values():
                    if child.parent is not owner:
                        continue
                    cap = row_bounds(business, child)[2]
                    found = owned_rows(tx, child, ids, limit=cap + 1)
                    if any(total > cap for _, total in found.values()):
                        raise ValidationError({'children': f'{child.name} exceeds the {cap}-row editor limit'})
                    found_ids = []
                    for owner_id, (records, _) in found.items():
                        self.rows[child, owner_id] = records
                        found_ids.extend(record.id for record in records)
                    for owner_id, items in sections.load(tx, child, ids).items():
                        self.sections[child, owner_id] = items
                    loaded += len(found_ids)
                    if bounded and loaded > LIMITS['document_rows']:
                        raise ValidationError({'children': 'This document exceeds the owned row limit'})
                    shown = business.policy.visible(tx, user, child.model, found_ids)
                    self.hidden.update((child.model, row_id) for row_id in found_ids if row_id not in shown)
                    if found_ids:
                        following.setdefault(child.model, []).extend(found_ids)
            level = following

    def of(self, child, owner_id):
        return self.rows.get((child, owner_id), [])

    def sections_of(self, child, owner_id):
        """Copies of a collection's saved section rows, placed among its saved rows."""
        saved = [item.copy() for item in self.sections.get((child, owner_id), [])]
        return sections.anchored(self.of(child, owner_id), saved)


CHANGES = ('create', 'update', 'delete', 'order')
CHILD_ID = 'Child ID must belong to this parent and appear once'


def known_id(value):
    """A record id given to a Context method (a RecordId, an int or the text form), or a 404."""
    try:
        return RecordId(value)
    except ValueError:
        raise AccessError(404, 'Record not found') from None


def item_id(value):
    """A RecordId from a saved row id in a request (text, or an int from older clients), else None."""
    try:
        return RecordId(value)
    except ValueError:
        return None


def row_changes(changes, existing, hidden, positioned, where):
    """Entries ('keep', id, False) or ('row', value, placed) for a {create, update, delete, order} change set.

    Rows the change set does not mention keep their values and place. Created rows append unless
    `order` places them. `order` lists saved row IDs and the `_key` of created rows; the listed rows
    take that order within the positions they already hold, so unlisted and hidden rows stay put.
    """
    def fail(message):
        raise ValidationError({where: message})
    if set(changes) - set(CHANGES):
        fail('Row changes use only create, update, delete and order')
    parts = {key: changes.get(key, []) for key in CHANGES}
    if any(not isinstance(value, list) or len(value) > LIMITS['collection_rows'] for value in parts.values()):
        fail(f'create, update, delete and order are lists of at most {LIMITS["collection_rows"]} items')
    if any(not isinstance(value, dict) for value in parts['create'] + parts['update']):
        fail('Each row must be an object')
    if any('id' in value for value in parts['create']):
        fail('Created rows take no id; change saved rows with update')
    saved = {row.id for row in existing}

    def saved_id(value):
        try:
            value = RecordId(value)
        except ValueError:
            fail(CHILD_ID)
        if value not in saved or value in hidden:
            fail(CHILD_ID)
        return value
    deleted = [saved_id(value) for value in parts['delete']]
    updates = {}
    for value in parts['update']:
        row_id = saved_id(value.get('id'))
        if row_id in updates:
            fail(CHILD_ID)
        updates[row_id] = value
    removed = set(deleted)
    if len(removed) != len(deleted) or removed & set(updates):
        fail(CHILD_ID)
    sequence = [('saved', row.id) for row in existing if row.id not in removed]
    sequence += [('new', index) for index in range(len(parts['create']))]
    listed, placed = [], set()
    if parts['order']:
        if not positioned:
            fail('This collection has no stored order')
        created = {}
        for index, value in enumerate(parts['create']):
            if isinstance(value.get('_key'), str):
                created.setdefault(value['_key'], index)
        for item in parts['order']:
            # The _key of a created row wins over a saved row id written the same way.
            if isinstance(item, str) and item in created:
                entry = ('new', created[item])
            elif item_id(item) is not None:
                if item_id(item) in removed:
                    fail('A deleted row cannot be ordered')
                entry = ('saved', saved_id(item))
            else:
                fail('Order lists saved row IDs and the _key of rows created in this request')
            if entry in placed:
                fail('Each row appears once in order')
            listed.append(entry)
            placed.add(entry)
        index = {entry: position for position, entry in enumerate(sequence)}
        for slot, entry in zip(sorted(index[entry] for entry in listed), listed):
            sequence[slot] = entry
    entries = []
    for kind, ref in sequence:
        if kind == 'new':
            entries.append(('row', parts['create'][ref], (kind, ref) in placed))
        elif ref in updates:
            entries.append(('row', updates[ref], (kind, ref) in placed))
        elif (kind, ref) in placed:
            entries.append(('row', {'id': ref}, True))
        else:
            entries.append(('keep', ref, False))
    return entries


def full_list(submitted, existing, hidden):
    """Entries for a full row list: hidden rows keep their places and the submitted rows fill the others in order."""
    rows, entries = iter(submitted), []
    for row in existing:
        if row.id in hidden:
            entries.append(('keep', row.id, False))
            continue
        value = next(rows, None)
        if value is not None:
            entries.append(('row', value, True))
    return entries + [('row', value, True) for value in rows]


def positions(rows, hidden):
    """Stored positions for rows in their new order without moving hidden rows.

    Rows keep their stored position when it still fits, otherwise take the next free number before
    the following hidden row. Only when no numbers are left (rows saved with equal positions) is the
    whole collection renumbered.
    """
    current = [row._values.get('evlet_position') for row in rows]
    fixed = [row.id in hidden for row in rows]
    following, upper = [None] * len(rows), None
    for index in range(len(rows) - 1, -1, -1):
        following[index] = upper
        if fixed[index]: upper = current[index]
    first = next((value for value, keep in zip(current, fixed) if keep), None)

    def attempt(reuse):
        result = []
        last = -1 if first is None else min(-1, first - len(rows) - 1)
        for index, value in enumerate(current):
            limit = following[index]
            if not fixed[index]:
                fits = reuse and rows[index].id is not None and type(value) is int and value > last
                value = value if fits and (limit is None or value < limit) else last + 1
            if value <= last or not fixed[index] and limit is not None and value >= limit:
                return None
            result.append(value)
            last = value
        return result
    return attempt(True) or attempt(False) or list(range(len(rows)))


def load_rows(context, model, draft, old, record_id, collections, preview, prefix, keys_seen, loaded=None, saved=None, targets=None):
    """Merge submitted child collections at every depth, keeping hidden rows.

    A collection is either a full list of rows (rows left out are removed) or a change set
    {create, update, delete, order} (rows left out are kept).
    """
    business, tx, user = context._business, context._tx, context.user
    def count(rows, slot):
        # Stored rows (each once, draft side) and new submitted rows are bounded separately so
        # a full document can still swap rows; load_draft checks the merged total.
        if loaded is None: return
        loaded[slot] += rows
        if loaded[slot] > LIMITS['document_rows']:
            raise ValidationError({'children': 'This document exceeds the owned row limit'})
    if set(collections) - {child.name for child in business.children.values() if child.parent is model}:
        raise ValidationError({prefix + 'children': 'Unknown child collection'})
    for child in business.children.values():
        if child.parent is not model: continue
        max_rows = row_bounds(business, child)[1]
        existing = saved.of(child, record_id) if record_id else []
        count(len(existing), 0)
        originals = {row.id: row for row in existing}
        hidden = {row.id for row in existing if (child.model, row.id) in saved.hidden}
        positioned = 'evlet_position' in child.model.__fields__
        sectioned = sections.enabled(model, child.name)
        headings = saved.sections_of(child, record_id) if record_id and sectioned else []
        known = {item.id: item for item in headings}

        def stored(row, owner, counter):
            item = Draft(child.model, row._values, context=context, parent=owner, record_id=row.id)
            load_rows(context, child.model, item, None, row.id, {}, True, prefix + child.name + '.' + item.key + '.', set(), counter, saved)
            return item
        if old is not None:
            old._rows[child.name] = [stored(row, old, None if loaded is None else [0, 0]) for row in existing]
            if sectioned: old._sections[child.name] = [item.copy() for item in headings]
        if child.name not in collections:
            draft._rows[child.name] = [stored(row, draft, loaded) for row in existing]
            if sectioned: draft._sections[child.name] = [item.copy() for item in headings]
            continue
        submitted = collections[child.name]
        listed = submitted if isinstance(submitted, list) else [*submitted.get('create', []), *submitted.get('update', [])] \
            if isinstance(submitted, dict) and all(isinstance(submitted.get(key, []), list) for key in ('create', 'update')) else []
        if not sectioned and 'section' not in child.model.__declared_fields__ and any(sections.is_entry(value) for value in listed):
            raise ValidationError({prefix + child.name: f'{child.label} has no section rows'})
        # A Table with sections orders its rows and section rows together; section rows are never hidden.
        everything = sections.combined(existing, headings) if sectioned else existing
        if isinstance(submitted, dict):
            entries = row_changes(submitted, everything, hidden, positioned, prefix + child.name)
        elif isinstance(submitted, list) and sum(1 for value in submitted if not (sectioned and sections.is_entry(value))) <= max_rows:
            if len(existing) - len(hidden) > ROW_LIMIT:
                # A detail embeds only the first ROW_LIMIT rows, so a full list from it would drop the rest.
                raise ValidationError({prefix + child.name: f'This collection has more than {ROW_LIMIT} rows; send a change set '
                                                             '{"create", "update", "delete", "order"} instead of the full list'})
            entries = full_list(submitted, everything, hidden)
        else:
            raise ValidationError({prefix + child.name: f'Use at most {max_rows} rows'})
        count(sum(1 for kind, value, _ in entries if kind == 'row' and isinstance(value, dict) and value.get('id') is None
                  and not (sectioned and sections.is_entry(value))), 1)
        kept, keys, merged, kept_sections = set(), set(), [], set()
        for kind, value, placed in entries:
            if kind == 'keep':
                if value in known:
                    kept_sections.add(value)
                    merged.append(known[value].copy())
                    continue
                if value not in hidden: kept.add(value)
                merged.append(stored(originals[value], draft, loaded))
                continue
            if not isinstance(value, dict):
                raise ValidationError({prefix + child.name: 'Each row must be an object'})
            if sectioned and (sections.is_entry(value) or item_id(value.get('id')) in known):
                merged.append(section_entry(business, user, child, value, known, kept_sections, originals, placed, preview,
                                            prefix + child.name, keys_seen))
                continue
            value = dict(value)
            row_id = value.pop('id', None)
            if row_id is not None:
                row_id = item_id(row_id) or row_id
            temporary = value.pop('_key', None)
            # New rows' keys map to IDs in one flat _row_ids object, so they are unique document-wide.
            fresh = row_id is None
            if temporary is not None and (not isinstance(temporary, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', temporary) or temporary in keys or fresh and temporary in keys_seen):
                raise ValidationError({prefix + child.name: 'Row keys must be distinct (new rows: within the document) and use at most 64 letters, digits, hyphens or underscores'})
            keys.add(temporary) if temporary is not None else None
            keys_seen.add(temporary) if temporary is not None and fresh else None
            value, nested = business.split_body(value)
            computed_write(business, child.model, value, prefix + child.name + '.')
            derived_write(child.model, value, nested, prefix + child.name + '.')
            original = originals.get(row_id) if row_id is not None else None
            drop_echoes(child.model, value, getattr(original, '_values', None))
            business.policy.check_values(tx, user, child.model, value, targets=targets)
            if row_id is not None:
                if not isinstance(row_id, RecordId) or row_id not in originals or row_id in kept or row_id in hidden:
                    raise ValidationError({prefix + child.name: CHILD_ID})
                # A row that is not hidden is inside this user's row scope, so the operation check is enough.
                business.policy.require(user, child.model, 'read' if preview or not value else 'update')
                kept.add(row_id)
            elif not preview:
                business.policy.require(user, child.model, 'create')
            row = Draft(child.model, {**(dict(originals[row_id]._values) if row_id else {}), **value}, context=context, parent=draft, record_id=row_id)
            object.__setattr__(row, '_submitted', frozenset(value))
            object.__setattr__(row, '_placed', placed)
            if temporary is not None: object.__setattr__(row, 'key', temporary)
            load_rows(context, child.model, row, None, row_id, nested, preview, prefix + child.name + '.' + row.key + '.', keys_seen, loaded, saved, targets)
            merged.append(row)
        for row_id in originals.keys() - kept - hidden:
            if not preview:
                # Removing a row removes its whole subtree: every descendant must be deletable too.
                for _, item in walk(stored(originals[row_id], draft, None if loaded is None else [0, 0])):
                    if not business.policy.allows(user, item._definition, 'delete') or (item._definition, item.id) in saved.hidden:
                        raise AccessError(403, 'You do not have permission for this operation')
        if known.keys() - kept_sections and not preview:
            business.policy.require(user, child.model, 'delete')
        if sum(1 for row in merged if not isinstance(row, sections.SectionRow)) > max_rows:
            raise ValidationError({prefix + child.name: f'Use at most {max_rows} rows'})
        if positioned:
            for row, position in zip(merged, positions(merged, hidden)):
                row._values['evlet_position'] = position
        if sectioned:
            merged, found = sections.split(merged)
            if len(found) > sections.MAX_SECTIONS:
                raise ValidationError({prefix + child.name: f'Use at most {sections.MAX_SECTIONS} section rows'})
            draft._sections[child.name] = found
        draft._rows[child.name] = merged


def section_entry(business, user, child, value, known, kept, originals, placed, preview, where, keys_seen):
    """A SectionRow from a submitted section row: {"section": label} creates one, {"id": ..., "section": label}
    renames a saved one, and {"id": ...} keeps or places it. A row never turns into a section or back."""
    sections.check_entry(value, where)
    row_id = value.get('id')
    temporary = value.get('_key')
    if temporary is not None and (not isinstance(temporary, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', temporary)
                                  or row_id is None and temporary in keys_seen):
        raise ValidationError({where: 'Row keys must be distinct (new rows: within the document) and use at most 64 letters, digits, hyphens or underscores'})
    if row_id is None:
        if not preview:
            business.policy.require(user, child.model, 'create')
        if temporary is not None:
            keys_seen.add(temporary)
        return sections.SectionRow(sections.label_of(value['section'], where), key=temporary, placed=placed)
    row_id = item_id(row_id)
    if row_id in originals:
        raise ValidationError({where: 'A row cannot become a section row; remove it and add a section'})
    if row_id not in known or row_id in kept:
        raise ValidationError({where: CHILD_ID})
    kept.add(row_id)
    item = known[row_id].copy()
    item._placed = placed
    if 'section' in value:
        label = sections.label_of(value['section'], where)
        if label != item.label and not preview:
            business.policy.require(user, child.model, 'update')
        item.label = label
    return item


def walk(draft, prefix=''):
    """Pre-order (prefix, draft) pairs: a document, then each row and its descendants."""
    yield prefix, draft
    for name, rows in draft._rows.items():
        for row in rows:
            yield from walk(row, prefix + name + '.' + row.key + '.')


def prior_versions(previous, draft):
    """A lookup for the prior version of each row (same model and ID) anywhere in the previous tree."""
    rows = {}
    if previous is not None:
        for _, row in walk(previous):
            if row is not previous: rows.setdefault((row._definition, row.id), row)

    def prior(item):
        if item is draft: return previous
        return None if item.id is None else rows.get((item._definition, item.id))
    return prior


class LinkTargets:
    """Linked records read for one write or preview, in batches; the same checks as Policy.record.

    prefetch() reads every (model, id) not yet asked with one statement per model and chunk;
    get() raises AccessError exactly like Policy.record for a record the user cannot read.
    """
    def __init__(self, context):
        self.context, self.found, self.asked = context, {}, set()

    def prefetch(self, pairs):
        wanted = {}
        for model, record_id in pairs:
            if (model, record_id) not in self.asked:
                self.asked.add((model, record_id))
                wanted.setdefault(model, []).append(record_id)
        policy, tx, user = self.context._business.policy, self.context._tx, self.context.user
        for model, ids in wanted.items():
            self.found.update(((model, record_id), record) for record_id, record in policy.records(tx, user, model, ids).items())

    def get(self, model, record_id):
        self.context._business.policy.require(self.context.user, model, 'read')
        if not isinstance(record_id, int):
            return self.context._business.policy.record(self.context._tx, self.context.user, model, record_id)
        self.prefetch([(model, record_id)])
        record = self.found.get((model, record_id))
        if record is None:
            raise AccessError(404, 'Record not found')
        return record


def link_values(items, *, fetches_only=False):
    """(target model, id) of every valid Link value on the given drafts (or the Links their fetches read)."""
    pairs = []
    for item in items:
        definition = item._definition
        names = {field.fetch.split('.')[0] for field in definition.__declared_fields__.values() if field.fetch} if fetches_only \
            else definition.__declared_fields__
        for name in names:
            field = definition.__declared_fields__.get(name)
            value = item._values.get(name)
            if isinstance(field, Link) and value is not None and isinstance(field.target, type):
                try: pairs.append((field.target, field.clean(value)))
                except (ValueError, TypeError): pass
    return pairs


def submitted_links(business, model, collections):
    """(target model, id) of Link values submitted in child rows at any depth, in either collection format."""
    pairs = []
    for child in business.children.values():
        if child.parent is not model or child.name not in collections: continue
        rows = collections[child.name]
        if isinstance(rows, dict):
            rows = [value for key in ('create', 'update') for value in rows.get(key, []) if isinstance(rows.get(key), list)]
        if not isinstance(rows, list): continue
        for row in rows:
            if not isinstance(row, dict): continue
            for name, value in row.items():
                field = child.model.__declared_fields__.get(name)
                if isinstance(field, Link) and value is not None:
                    try: pairs.append((field.target, field.clean(value)))
                    except (ValueError, TypeError): pass
            if isinstance(row.get('children'), dict):
                pairs.extend(submitted_links(business, child.model, row['children']))
    return pairs


def link_mismatch(context, item, name, field, value, targets=None):
    """None when the Link's selected record satisfies its filters, else the message for the field.

    Reading the record checks access. The message names both records only when the Link has one
    filter, it compares a Link of the target with a Link on this form, and the role may read both
    titles; otherwise it stays general, so it never reveals a record the role cannot read.
    """
    business = context._business
    target = targets.get(field.target, value) if targets is not None else business.policy.record(context._tx, context.user, field.target, value)
    visible = business.policy.fields(context.user, field.target)
    if set(field.filters) - visible: raise AccessError(403, 'A selection constraint is unavailable')
    failed = []
    for filter_name, expected in field.filters.items():
        path = expected.path if isinstance(expected, Ref) else None
        expected = item.read(path) if path else expected
        if expected is None or getattr(target, filter_name) != expected: failed.append((filter_name, path, expected))
    if not failed: return None
    general = f"This {field_label(item._definition, name) or 'selection'} doesn't fit the other values on this form."
    if len(field.filters) != 1 or failed[0][1] is None: return general
    filter_name, path, expected = failed[0]
    if expected is None:
        label = path_label(item, path)
        return f'Choose {label} first.' if label else general
    # Both sides must be records the role may read, shown by their titles.
    compared = field.target.__declared_fields__.get(filter_name)
    if not isinstance(compared, Link): return general
    try:
        owner = targets.get(compared.target, expected) if targets is not None else business.policy.record(context._tx, context.user, compared.target, expected)
    except (AccessError, ValueError, TypeError):
        return general
    titles = [getattr(record, 'title', None) if 'title' in business.policy.fields(context.user, model) else None
              for record, model in ((target, field.target), (owner, compared.target))]
    if not all(isinstance(title, str) and title.strip() for title in titles): return general
    return f'{titles[0]} is not in {titles[1]}.'


def field_label(definition, name):
    from .ui.metadata import label_for
    field = definition.__declared_fields__.get(name)
    return None if field is None else field.label or label_for(name)


def path_label(item, path):
    """The label of the field a Ref path names on this form: `company` or `parent.company`."""
    parts = path.split('.')
    if len(parts) == 1: return field_label(item._definition, parts[0])
    if len(parts) == 2 and parts[0] == 'parent' and getattr(item, 'parent', None) is not None:
        return field_label(item.parent._definition, parts[1])
    return None


def settle(context, draft, *, complete, previous=None, kept=False, checks=True):
    """Bring a document tree to a consistent state: inputs, fetches, calculations, then checks.

    `checks=False` stops after the calculations, for reading a saved document's virtual results
    (Business.add_virtual_values): its Link and field checks are a save's, not a read's.

    A preview (complete=False) returns errors and field states; a save (complete=True) also runs
    the full validation and raises ValidationError on any error. With `kept` (an ordinary write on a
    locked record, any write on a posted one, and previews of both) stored results and fetched copies keep their saved values, and rules, virtual
    results and validation read those saved values.
    """
    registry = context._business.registry
    errors, states = {}, {}
    all_drafts = list(walk(draft))
    prior = prior_versions(previous, draft)
    # Owner links of legacy child tables are set from the saved parent at write time.
    owner_links = {child.model: child.link for child in context._business.children.values() if child.parent is draft._definition}
    def internal(item):
        names = set(getattr(item._definition, '__internal_fields__', ()))
        return names | {owner_links[item._definition]} if item is not draft and item._definition in owner_links else names
    targets = LinkTargets(context)
    targets.prefetch(link_values([item for _, item in all_drafts], fetches_only=True))
    for prefix, item in all_drafts:
        apply_inputs(item, prefix, internal(item), errors)
        object.__setattr__(item, '_caller', dict(item._values))
        apply_fetches(context, item, prefix, prior(item), errors, targets, virtual_only=kept)
    # derive() sets its values before calculations; a locked or posted record keeps them, like its results.
    if getattr(draft._definition, '__derives__', ()) and draft.parent is None and not kept:
        all_drafts = derive(context, draft, internal, errors, targets)
    plan = registry.plan_for(draft._definition)
    if plan is not None:
        if kept:
            plan = registry.kept_plans[registry.root_of(draft._definition)]
        finite_settle(context, plan, draft, errors, states, complete, previous, kept)
    else:
        compute_legacy(context, draft, all_drafts, errors, states, complete, kept)
    if not checks:
        return errors, states
    # Every Link target is read once, in batches, before the per-field checks.
    targets.prefetch(link_values([item for _, item in all_drafts]))
    if complete:
        validate_tree(context, all_drafts, internal, prior, errors, targets)
    else:
        check_preview(context, all_drafts, internal, errors, states, targets)
    return errors, states


def derive(context, draft, internal, errors, targets):
    """Run the document's derive() and treat what it set like caller input: cleaned, with defaults and
    fetches on the rows it built. Returns the document's drafts again, since it may replace rows.

    Rows it builds for a Table take the ids of the saved rows they replace, in order, so a derived
    Table that comes out the same is not rewritten. A ValidationError it raises is reported like a
    field error."""
    model = draft._definition
    names = model.__derives__
    tables = {name for name in names if name in model.__tables__}
    fields = {name for name in names if name in model.__declared_fields__}
    # "table.field": fields derive() sets on the rows of a Table callers edit; it keeps those rows.
    row_fields = {}
    for name in names:
        if '.' in name:
            table, field = name.split('.')
            row_fields.setdefault(table, set()).add(field)
    saved = {name: [row for row in draft._rows.get(name, []) if row.id] for name in tables}
    edited = {name: list(rows) for name, rows in draft._rows.items() if name not in tables}
    before = list(walk(draft))
    for prefix, item in before:
        top = prefix.split('.')[0]
        allowed = frozenset(names) if item is draft else None if top in tables else \
            frozenset(row_fields.get(top, ())) if item.parent is draft else frozenset()
        object.__setattr__(item, '_derives', allowed)
    try:
        with trusted(), hook_context(context, draft):
            draft.derive()
    except ValidationError as exc:
        errors.update(exc.errors)
    finally:
        for _, item in [*before, *walk(draft)]:
            object.__setattr__(item, '_derives', None)
    for name, rows in edited.items():
        if [id(row) for row in draft._rows.get(name, [])] != [id(row) for row in rows]:
            raise AttributeError(f'derive() cannot add, remove or reorder the rows of {model.__name__}.{name}, which '
                                 'callers edit; name the Table itself in Meta.derives to build its rows')
    all_drafts = list(walk(draft))
    targets.prefetch(link_values([item for _, item in all_drafts if item.id is None], fetches_only=True))
    for prefix, item in all_drafts:
        if item is draft:
            apply_inputs(item, prefix, internal(item) | (set(model.__declared_fields__) - fields), errors, clear=True)
            item._caller.update({name: item._values.get(name) for name in fields})
        elif item.parent is draft and prefix.split('.')[0] in row_fields:
            names_here = row_fields[prefix.split('.')[0]]
            apply_inputs(item, prefix, internal(item) | (set(item._definition.__declared_fields__) - names_here), errors, clear=True)
            item._caller.update({name: item._values.get(name) for name in names_here})
        elif prefix.split('.')[0] in tables:
            unknown = set(item._values) - set(item._definition.__declared_fields__)
            if unknown:
                raise ValueError(f'derive() built a {item._definition.__name__} row with unknown fields: {", ".join(sorted(map(str, unknown)))}')
            apply_inputs(item, prefix, internal(item), errors, clear=True)
            object.__setattr__(item, '_caller', dict(item._values))
            apply_fetches(context, item, prefix, None, errors, targets)
    for name in tables:
        rows = draft._rows.get(name, [])
        kept = {row.id for row in rows if row.id}
        free = [row for row in saved[name] if row.id not in kept]
        for row in rows:
            if row.id is None and free:
                old = free.pop(0)
                # An error on a rebuilt row follows it to the saved id the preview and response show.
                before_key, after_key = f'{name}.{row.key}.', f'{name}.{old.id}.'
                for key in [key for key in errors if key.startswith(before_key)]:
                    errors[after_key + key[len(before_key):]] = errors.pop(key)
                object.__setattr__(row, 'id', old.id)
                object.__setattr__(row, 'key', str(old.id))
    return list(walk(draft))


@contextmanager
def hook_context(context, draft):
    """Give derive() a read context with the session open read only, for as long as it runs."""
    inner = context.read_context()
    inner._session_open = 'read'
    previous = draft.context
    object.__setattr__(draft, 'context', inner)
    try:
        yield
    finally:
        inner._session_open = False
        for _, part in walk(draft):
            if part.context is inner or part is draft:
                object.__setattr__(part, 'context', previous)


def current_user(item, field):
    """The signed-in user's username (Text) or account id (Integer), from the root draft's context."""
    root = item
    while root.parent is not None:
        root = root.parent
    user = root.context.user if root.context is not None else None
    if user is None:
        return None
    return user.username if field.kind == 'text' else user.id


def apply_inputs(item, prefix, internal, errors, *, clear=False):
    """Creation defaults, then type validation of every source (non-calculated) field not in `internal`.
    With `clear` (values derive() set), a value that fails is emptied after its error is noted, so
    calculations, previews and responses never read a value the field refuses."""
    for name, field in item._definition.__declared_fields__.items():
        if name in internal: continue
        value = item._values.get(name, MISSING)
        if value is MISSING and item.id is None:
            if isinstance(field.default, Ref): value = item.read(field.default.path)
            elif field.default is CurrentUser: value = current_user(item, field)
            elif field.default is not MISSING: value = field.default() if callable(field.default) else field.default
        if field.compute is not None or field.query is not None: continue
        try:
            item._values[name] = None if value is MISSING or value is None else field.validate(value)
            if field.kind == 'integer' and item._values[name] is not None and abs(item._values[name]) > 2**53-1:
                raise ValueError('Document integers must fit the browser safe range')
        except ValueError as exc:
            errors[prefix + name] = str(exc)
            if clear:
                item._values[name] = None


def apply_fetches(context, item, prefix, old, errors, targets=None, *, virtual_only=False):
    """Copy `fetch='link.field'` values from the linked record when the link changed.

    A virtual fetch (stored=False) has no saved copy, so it is always read from the linked record.
    `virtual_only` reads only those, for a record that keeps its saved copies (a locked or posted one).
    """
    for name, field in item._definition.__declared_fields__.items():
        if not field.fetch or virtual_only and field.stored: continue
        link_name, target_name = field.fetch.split('.')
        link = item._definition.__declared_fields__.get(link_name)
        if not isinstance(link, Link) or target_name not in link.target.__declared_fields__:
            raise ValueError('Fetch must reference a declared Link target field')
        if not field.read_only and name in item._submitted and item._values.get(name) is not None:
            if old is None or old._values.get(name) != item._values.get(name) or old._values.get(link_name) == item._values.get(link_name): continue
        if field.stored and not field.read_only and old is not None and old._values.get(link_name) == item._values.get(link_name): continue
        try:
            value = None
            if item._values.get(link_name) is not None:
                record = targets.get(link.target, item._values[link_name]) if targets is not None else \
                    context._business.policy.record(context._tx, context.user, link.target, item._values[link_name])
                if target_name not in context._business.policy.fields(context.user, link.target): raise AccessError(403, 'Fetched field is unavailable')
                value = getattr(record, target_name)
            item._values[name] = field.clean(value)
        except ValueError as exc: errors[prefix + name] = str(exc)
        except AccessError:
            if field.stored: raise
            item._values[name] = None  # A virtual copy of a record this user cannot read stays empty.


def compute_legacy(context, draft, all_drafts, errors, states, complete, kept=False):
    """String-formula computes, Query sources and query fields, in dependency order.

    With `kept`, stored results keep their saved values and only virtual results are calculated.
    """
    nodes, order = graph(context._business.registry, draft._definition)
    source_cache = {}
    for key in order:
        parts = key.split('.')
        items = [(draft, '')] if len(parts) == 1 else [(row, parts[0] + '.' + row.key + '.') for row in draft._rows.get(parts[0], [])]
        name = parts[-1]
        for item, prefix in items:
            if name in item._definition.__sources__:
                load_source(context, item, prefix, name, all_drafts, source_cache, errors, states)
                continue
            field = item._definition.__declared_fields__[name]
            if kept and field.stored:
                continue
            try:
                if field.compute is not None:
                    value = round_field(field, evaluate(nodes[key]['compute'], item.read), item.read)
                    item._values[name] = field.validate(value) if value is not None else None
                    if value is None: states[prefix + name] = 'missing-input'
                if field.query is not None:
                    dependencies = getattr(field.query, '__depends__')
                    if any(item.read(path.replace('.*.', '.')) is None for path in dependencies):
                        item._values[name] = None
                        states[prefix + name] = 'missing-input'
                        continue
                    cache_key = (id(field.query), json.dumps([item.read(path.replace('.*.', '.')) for path in dependencies], default=str))
                    if cache_key not in source_cache:
                        with readonly_drafts(context, all_drafts): result = field.query(item)
                        source_cache[cache_key] = field.clean(result)
                    item._values[name] = source_cache[cache_key]
                    states[prefix + name] = 'ready' if item._values[name] is not None else 'empty'
            except (ValueError, ArithmeticError, TypeError) as exc:
                item._values[name] = None
                missing = str(exc) == 'A required calculation input is missing'
                if not missing or complete and field.stored: errors[prefix + name] = str(exc)
                states[prefix + name] = 'missing-input' if missing else 'error'


def load_source(context, item, prefix, name, all_drafts, source_cache, errors, states):
    """Resolve one `Query` source into a read-only snapshot, cached by its inputs."""
    source = item._definition.__sources__[name]
    inputs = {key: item.read(value.path) if isinstance(value, Ref) else value for key, value in source.filters.items()}
    inputs.update({path: item.read(path.replace('.*.', '.')) for path in getattr(source.target, '__depends__', ())})
    if any(value is None or value == '' for value in inputs.values()):
        item._sources[name] = None
        states[prefix + name] = 'missing-input'
        return
    cache_key = (id(source), json.dumps(inputs, sort_keys=True, default=str))
    try:
        if cache_key not in source_cache:
            if isinstance(source.target, type):
                allowed = context._business.policy.fields(context.user, source.target)
                if set(source.fields) - allowed: raise AccessError(403, 'Query outputs are unavailable')
                result = context.read_context().list(source.target, filters=inputs, limit=2)
                if result['total'] > 1: raise ValueError('A scalar query must match at most one record')
                result = {key: result['rows'][0].get(key) for key in source.fields} if result['rows'] else None
            else:
                with readonly_drafts(context, all_drafts): result = source.target(item)
            if result is not None:
                if not isinstance(result, dict) or set(result) != set(source.fields): raise ValueError('Query result must match its declared shape')
                result = {key: field.clean(result[key]) for key, field in source.fields.items()}
            source_cache[cache_key] = result
        result = source_cache[cache_key]
        item._sources[name] = Snapshot(result) if result is not None else None
        states[prefix + name] = 'ready' if result is not None else 'empty'
    except AccessError:
        item._sources[name] = None
        states[prefix + name] = 'denied'
    except (ValueError, TypeError, ArithmeticError) as exc:
        item._sources[name] = None
        states[prefix + name] = 'error'
        errors[prefix + name] = str(exc)


def check_preview(context, all_drafts, internal, errors, states, targets=None):
    """Preview-only hints: missing required inputs and Link selections that no longer match."""
    for prefix, item in all_drafts:
        for name, field in item._definition.__declared_fields__.items():
            if name in internal(item): continue
            try:
                required = field.required or field.required_if is not None and evaluate(plans(item._definition)[name]['required_if'], item.read)
                required = required or item._rules.get(name, {}).get('required', False)
                if required and item._values.get(name) in EMPTY: states[prefix + name] = 'required-input'
                if isinstance(field, Link) and item._values.get(name) is not None and field.filters:
                    message = link_mismatch(context, item, name, field, item._values[name], targets)
                    if message: errors[prefix + name] = message
            except (ValueError, TypeError, ArithmeticError) as exc: errors[prefix + name] = str(exc)


def validate_tree(context, all_drafts, internal, prior, errors, targets=None):
    """Full save validation, children before parents; raises ValidationError on any error."""
    for prefix, item in reversed(all_drafts):
        old = prior(item)
        for name, field in item._definition.__declared_fields__.items():
            if name in internal(item): continue
            try:
                rules = item._rules.get(name)
                if rules and rules['locked'] and locked_change(item, old, name):
                    raise ValueError(rules.get('message') or LOCKED)
                if name in item._submitted and field.read_only_if is not None and evaluate(plans(item._definition)[name]['read_only_if'], item.read):
                    if old is None or old._values.get(name) != item._values.get(name):
                        raise ValueError('This field is read only in the current state')
                if rules and not rules['applies'] and rules['locked'] and old is not None and old._values.get(name) not in EMPTY:
                    raise ValueError(LOCKED_CLEAR)
                if rules and not rules['applies']:
                    # A field that does not apply is not required, skips its validator and is stored empty.
                    item._values[name] = None
                    continue
                if rules and rules['required'] and item._values.get(name) in EMPTY:
                    raise ValueError('This field is required')
                if field.required_if is not None and evaluate(plans(item._definition)[name]['required_if'], item.read) and item._values.get(name) in (None, ''):
                    raise ValueError('This field is required')
                item._values[name] = field.clean(item._values.get(name))
                # A ledger row has no public surface and callers never write a Link to one, so only trusted code
                # sets it: there is no caller access to check, and nothing to compare without filters.
                if isinstance(field, Link) and item._values[name] is not None and not (field.ledger_target and not field.filters):
                    message = link_mismatch(context, item, name, field, item._values[name], targets)
                    if message: raise ValueError(message)
            except ValueError as exc:
                errors[prefix + name] = str(exc)
        for name, table in item._definition.__tables__.items():
            if len(item._rows.get(name, ())) < table.min_rows:
                errors[prefix + name] = 'Add at least one row' if table.min_rows == 1 else f'Add at least {table.min_rows} rows'
        if not errors:
            from .statemanager import check_item
            errors.update({prefix + key: value for key, value in check_item(item).items()})
    if errors: raise ValidationError(errors)


EMPTY = (None, '', ())
LOCKED = 'This field is locked and cannot be changed now'
LOCKED_CLEAR = 'This field is locked, so a change that would clear it is not allowed'
LOCKED_PENDING = 'This field is locked until the values its lock rule reads are filled in'


def locked_change(item, old, name):
    """Whether the caller (request body, Context values or an action) changed a locked field.

    Values the engine writes (initial suggestions, cleared fields) are not caller changes. A new record
    or row has no saved value to keep, and its locks are never set (Document.locked).
    """
    if old is None:
        return False
    return item._caller.get(name, item._values.get(name)) != old._values.get(name)


def from_wire(field, value):
    """A value the finite engine wrote (initial or cleared), as the field's Python value."""
    from .fields import Integer
    if isinstance(field, Integer) and not isinstance(field, Link) and isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value):
        value = int(value)
    return None if value is None else field.validate(value)


MESSAGES = {'COMPUTE_INPUT_MISSING': 'A required calculation input is missing',
            'COMPUTE_DIVISION_BY_ZERO': 'Division by zero', 'COMPUTE_RANGE_ERROR': 'Calculation exceeds the supported range',
            'COMPUTE_SCALE_ERROR': 'The result needs an explicit round() to fit this field',
            'COMPUTE_TYPE_ERROR': 'An input has the wrong type', 'PARENT_CONTEXT_REQUIRED': 'The owning document is unavailable'}


def host_today(business):
    """Today's date in the instance timezone (UTC when none is configured)."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    zone = getattr(business, 'timezone', None) or 'UTC'
    return datetime.now(timezone.utc if zone == 'UTC' else ZoneInfo(zone)).date().isoformat()


def resolve_lookups(context, document):
    """Supply Link path values: one query per looked-up model per hop, at most MAX_HOPS rounds.

    The server reads stored values with full access, like hidden child rows, so stored results do not
    depend on who saves. Readability is enforced on output: derivation_allowed withholds any result
    whose path the user cannot read.
    """
    fill_lookups(context._business.registry, context._tx, document)


def fill_lookups(registry, tx, document):
    """Supply a document's Link path values from this transaction (see resolve_lookups)."""
    from .database import ROW_LIMIT
    asked = set()  # Each record is queried at most once per settle, found or not.
    for _ in range(MAX_HOPS):
        wanted = {}
        for model, record_id, fields in document.lookup_requests():
            if (model, record_id) not in asked:
                asked.add((model, record_id))
                wanted.setdefault(model, (fields, []))[1].append(record_id)
        if not wanted:
            return
        found = []
        for name, (fields, ids) in wanted.items():
            model = registry.models[name]
            for start in range(0, len(ids), ROW_LIMIT):
                for record in tx.list(model, filters={'id__in': ids[start:start + ROW_LIMIT]}, limit=ROW_LIMIT):
                    found.append((name, record.id, {field: model.__declared_fields__[field].serialize(getattr(record, field)) for field in fields}))
        if not found:
            return
        document.batch(lambda session: [session.set_lookups(*item) for item in found])


def finite_settle(context, plan, draft, errors, states, complete, previous=None, kept=False):
    """Authoritative finite-v1 evaluation over the whole draft tree in this transaction.

    With `kept` the plan is the root's kept plan, which reads stored results as saved values.

    With initial suggestions on an update, the session starts from the saved document and receives
    this write's changes as one batch, exactly as a browser session would, so an initial fires only
    when its inputs change and never over a value the caller supplied.
    """
    initials = {(tuple(field['path']), field['field']) for field in plan['fields'] if field.get('kind') == 'initial'}
    # Locks are decided on the saved document, so an update starts from it whenever the plan has locks.
    from_saved = any(field.get('kind') in ('initial', 'locked_when') for field in plan['fields'])

    def data(item, path=()):
        # A new row leaves out initial fields the caller did not supply, so their suggestions apply.
        # Instance fields (x_) are plain values no formula can read, so the plan never sees them.
        values = {name: item._values.get(name) for name, field in item._definition.__declared_fields__.items()
                  if (not is_finite(field) or kept and field.stored) and name not in getattr(item._definition, '__internal_fields__', ())
                  and not name.startswith('x_')
                  and not (item.id is None and (path, name) in initials and name not in item._submitted)}
        # Engine keys are internal: saved rows by ID and new rows by draft, so a client _key never meets a saved ID.
        values['_key'], values['id'] = ('s' + str(item.id)) if item.id else 'n' + str(id(item)), item.id
        if item._rows:
            values['children'] = {name: [data(row, path + (name,)) for row in rows] for name, rows in item._rows.items()}
        return values

    def sync(session, row, item, old, path):
        """This write's changes to one saved row, then its collections, as session commands."""
        for name, field in item._definition.__declared_fields__.items():
            if is_finite(field) or name in getattr(item._definition, '__internal_fields__', ()) or name.startswith('x_'):
                continue
            value, before = item._values.get(name), old._values.get(name)
            if name in item._submitted or value != before or type(value) is not type(before):
                session.set(row.handle, name, value)
        for table, rows in item._rows.items():
            saved = old._rows.get(table, [])
            if [child.id for child in rows] != [child.id for child in saved]:
                session.replace_rows(row.handle, table, [{'id': child.id} if child.id else data(child, path + (table,)) for child in rows])
            current, olds = {child.id: child for child in row.children[table] if child.id}, {child.id: child for child in saved}
            for child in rows:
                if child.id:
                    sync(session, current[child.id], child, olds[child.id], path + (table,))

    def host_inputs(document):
        if any(read[0] == 'today' for field in plan['fields'] for read in field['reads']):
            document.set_today(host_today(context._business))
        resolve_lookups(context, document)
    try:
        if from_saved and previous is not None:
            document = Document(plan, data(previous), checked=True)
            host_inputs(document)
            document.batch(lambda session: sync(session, document.root, draft, previous, ()))
            resolve_lookups(context, document)
        else:
            document = Document(plan, data(draft), checked=True)
            host_inputs(document)
    except CalcError as exc:
        raise ValidationError({'_form': f'{exc.code}: {exc.message}'}) from None
    fields = {}
    for field in plan['fields']:
        fields.setdefault(tuple(field['path']), []).append(field)
    policy, user = context._business.policy, context.user

    def apply(item, row, prefix):
        rules = document.rule_states(row.handle)
        saved = document.locks.get(row.handle, {})
        for name, state in rules.items():
            if not state['locked']:
                continue
            decided = saved[name] if name in saved else document.decided(row, name, 'locked_when', None)
            if decided is None and policy.expression_allowed(user, item._definition, name, 'locked_when'):
                state['message'] = LOCKED_PENDING
        object.__setattr__(item, '_rules', rules)
        for field in fields.get(row.path, ()):
            name = field['field']
            if field.get('kind') in ('initial', 'applies_when'):
                # Engine-written values: an initial suggestion or a field cleared because it does not apply.
                value, current = row.values.get(name), item._values.get(name)
                if value != current or type(value) is not type(current):
                    try:
                        item._values[name] = from_wire(item._definition.__declared_fields__[name], value)
                    except ValueError as exc:
                        errors[prefix + name] = str(exc)
                continue
            if field.get('kind'):
                continue
            result = document.results[row.handle, name]
            definition = item._definition.__declared_fields__[name]
            value = None
            if result.state == 'ready' and result.value is not None:
                if field['result_type'] == 'decimal':
                    from .fields import Integer
                    value = int(to_decimal(result.value)) if isinstance(definition, Integer) else to_decimal(result.value)
                else:
                    value = result.value
                try:
                    value = definition.validate(value)
                except ValueError as exc:
                    # Declared bounds on a result are a calculation error, reported like any other.
                    item._values[name] = None
                    states[prefix + name] = 'error'
                    errors[prefix + name] = f'COMPUTE_RANGE_ERROR: {exc}'
                    continue
            item._values[name] = value
            if result.state == 'ready':
                continue
            states[prefix + name] = 'missing-input' if result.state == 'pending' else 'error'
            # Pending optional virtual results may stay pending; stored results and errors block saves.
            if result.state == 'error' or complete and field['stored']:
                errors[prefix + name] = f"{result.code}: {MESSAGES.get(result.code, 'The calculation failed')}"
        for name, rows in item._rows.items():
            for child, child_row in zip(rows, row.children.get(name, [])):
                apply(child, child_row, prefix + name + '.' + child.key + '.')
    apply(draft, document.root, '')


@contextmanager
def handler_context(context, draft):
    """Give a state manager hook or an @action handler its own copy of the context, with the transaction
    session open, for as long as it runs. The caller's context (an HTTP route, a service, a tool) never
    gets an open session, and the copy closes when the handler returns, even if the handler kept it."""
    from copy import copy
    inner = copy(context)  # Shares the transaction, the user and the recursion guard (_active).
    inner._session_open = True
    previous = draft.context
    object.__setattr__(draft, 'context', inner)
    try:
        yield
    finally:
        inner._session_open = False
        object.__setattr__(draft, 'context', previous)
        for _, part in walk(draft):
            if part.context is inner:
                object.__setattr__(part, 'context', previous)


def add_outcome(result, outcome):
    """What a run method or action returned, on its detail: a queued job as `_job` ({id, state}), a
    presented slip as `_outcome`."""
    from .jobs import JobReceipt
    if isinstance(outcome, JobReceipt):
        result['_job'] = outcome.public()
    elif outcome is not None:
        result['_outcome'] = outcome


@contextmanager
def readonly_drafts(context, all_drafts):
    originals = [(part, part.context, part._readonly, part._values, part._rows) for _, part in all_drafts]
    for part, _, _, values, rows in originals:
        object.__setattr__(part, 'context', context.read_context())
        object.__setattr__(part, '_readonly', True)
        object.__setattr__(part, '_values', MappingProxyType(values))
        object.__setattr__(part, '_rows', MappingProxyType({name: tuple(items) for name, items in rows.items()}))
    try: yield
    finally:
        for part, old_context, readonly, values, rows in originals:
            object.__setattr__(part, 'context', old_context)
            object.__setattr__(part, '_readonly', readonly)
            object.__setattr__(part, '_values', values)
            object.__setattr__(part, '_rows', rows)


def freeze_draft(draft):
    for rows in draft._rows.values():
        for row in rows: freeze_draft(row)
    object.__setattr__(draft, '_values', MappingProxyType(dict(draft._values)))
    object.__setattr__(draft, '_rows', MappingProxyType({name: tuple(rows) for name, rows in draft._rows.items()}))
    object.__setattr__(draft, '_readonly', True)


def source_values(draft, allowed=None, behaviors=('compute',)):
    from .reactivity import references
    sources = draft._definition.__sources__
    used = {name: set(source.fields) for name, source in sources.items()}
    if allowed is not None:
        # Return only the outputs that readable fields use; others may feed hidden fields.
        used = {}
        for name in allowed:
            entry = plans(draft._definition)[name]
            # Record schemas expose only computes over sources; dialogs also use conditions and filters.
            expressions = [entry.get(key) for key in behaviors if key != 'filters']
            expressions += list(entry.get('filters', {}).values()) if 'filters' in behaviors else []
            for path in set().union(*(references(expression) for expression in expressions)):
                parts = path.split('.')
                if len(parts) == 2 and parts[0] in sources and parts[1] in sources[parts[0]].fields:
                    used.setdefault(parts[0], set()).add(parts[1])
    return {name: {key: field.serialize(getattr(draft._sources[name], key, None)) for key, field in source.fields.items() if key in used[name]}
            if draft._sources.get(name) is not None else None
            for name, source in sources.items() if name in used}


def write_snapshot(tx, draft, before=None, *, reload=True):
    from .database import insert, storage_columns, stored_values
    from .naming import has_title
    model = draft._definition
    # References are checked when they change; a saved one is not rechecked on every write.
    tx.check_references(model, {name: draft._values.get(name) for name in model.__fields__
                                 if before is None or before._values.get(name) != draft._values.get(name)})
    # Amounts carry at most their currency's places (calculated ones were rounded to them already).
    from .currencies import check_amounts
    check_amounts(tx, model, draft._values)
    # The standard title follows its source on every write (evlet/naming.py).
    if has_title(model):
        draft._values['title'] = stored_values(model, draft._values, draft.id)['title']
    names, values = storage_columns(model, draft._values, model.__fields__)
    table = tx.registry.name_for(model)
    if draft.id is None:
        object.__setattr__(draft, 'id', insert(tx.connection, table, names, values, id_title=has_title(model), actor=tx.actor))
    else:
        from .audit import modified
        stamp_names, stamp_values = modified(tx.actor)
        names, values = [*names, *stamp_names], [*values, *stamp_values]
        tx.connection.run(qb.update(table, **dict(zip(names, values))).where(id=draft.id))
    # Rich text and Image fields: link held images, refuse other records' and delete the ones no longer used.
    from . import images
    holders = images.holders(model)
    if holders and (before is None or any(before._values.get(name) != draft._values.get(name) for name in holders)):
        images.settle(tx, model, draft.id, draft._values)
    return tx.get(model, draft.id) if reload else None


def operate(context, model, record_id, values, *, action=None, arguments=None, move=None, remark=None, release=False):
    """Create, update, run an action or make a move through the document pipeline, in one transaction.

    Ordinary writes respect a locked State. Actions and moves are declared, role-checked code: they may
    change a record in a locked state but still obey each field's locked_when and every other rule.
    A record with posted effects keeps its stored calculated values on every write, and its actions and
    moves may not change what those values read. A move runs in the state manager's order: the rule,
    @state.exit, the new state, @state, settle (with @validate), then the write and its journal entry.
    """
    from . import guards, statemanager
    business, tx, user = context._business, context._tx, context.user
    # Two Contexts for different users may share one transaction: this write is recorded under this one.
    from .audit import actor_id
    if actor_id(user) is not None:
        tx.actor = actor_id(user)
    # Required fields and retired options set in Controls apply to what people send, not to app code.
    person, context._person = context._person == (model, record_id) and action is None and move is None, None
    if (action is not None or move is not None or release) and not record_id:
        raise AccessError(404, 'Record not found')
    if release and not (statemanager.releasing(model) and business.policy.allows(user, model, 'release')):
        raise AccessError(403, 'Your role cannot release this record')
    rule = level = None
    if move is not None:
        # The rule is checked on the saved record before its values, so values never reveal which rules exist.
        draft, before = load_draft(context, model, record_id, {})
        rule, remark, level = statemanager.begin_move(context, model, draft, before, move, remark)
        if not statemanager.last_level(context, model, before, rule, level):
            # An approval level that is not the last is logged; the record stays as it is.
            return statemanager.approve(context, model, before, rule, level, remark, values)
        if values:
            statemanager.check_move_values(context, model, rule, values)
            draft, before = load_draft(context, model, record_id, values)
    else:
        draft, before = load_draft(context, model, record_id, values)
    statemanager.bind(draft, before)
    ordinary = action is None and move is None
    kept_by = keeps_saved_results(context, model, before, ordinary=ordinary)
    kept = bool(kept_by)
    # A record with postings keeps its stored calculated values for every writer.
    posted = 'posted' in kept_by
    # An ordinary write on a locked record changes only versioned=False fields and keeps saved results.
    locked_write = ordinary and 'locked' in kept_by
    if context._recalculate == (model, record_id) and before is not None:
        if posted:
            raise AccessError(409, 'This record has postings. To change its totals, move it out of its posted state.')
        if locked_write:
            statemanager.check_recalculate(context, model, before)
    plan = business.registry.plan_for(model)
    version = business.registry.formula_hashes.get(business.registry.root_of(model))
    # A write that keeps saved results does not recalculate, so it neither checks nor updates the recorded formula version.
    if kept:
        plan = None
    if plan is not None and record_id:
        row = tx.connection.run(qb.select('evlet_calculation_versions', 'plan_hash')
                                .where(resource=business.registry.name_for(model), record_id=record_id)).one()
        # No recorded version (saved outside the pipeline or before tracking) adopts the active plan.
        if row is not None and row[0] != version and context._recalculate != (model, record_id):
            raise AccessError(409, 'SCHEMA_VERSION_MISMATCH: this record was calculated with another plan; '
                                   'resubmit with "_calculation": {"model_schema_hash": <active>, "recalculate": true} to recalculate it')
    outcome, reversed_rows = None, []
    if rule is not None:
        previous = before._values.get(model.__state__)
        if previous in model.__declared_fields__[model.__state__].releases:
            # Leaving a release keeps its content first (the snapshot is taken once, lazily).
            statemanager.diverge(context, model, before, draft, leaving=True)
        if any(bound.on == previous for bound in business.registry.effects.get(model, ())):
            # Leaving a posting state reverses what it posted, before any hook runs.
            from .effects import reverse
            reversed_rows = reverse(context, model, record_id)
        statemanager.run_hooks(context, draft, 'exit', previous)
        draft._values[model.__state__] = rule.to
        statemanager.run_hooks(context, draft, 'enter', rule.to)
    elif action is not None:
        method = getattr(model, action, None)
        declaration = getattr(method, '__action__', None)
        if declaration is None or not has_role(user, declaration['roles']):
            raise AccessError(403, 'Action is unavailable')
        arguments = guards.action_arguments(context, model, method, declaration, arguments, before)
        with trusted(), handler_context(context, draft):
            outcome = method(draft, **arguments)
    elif before is not None:
        statemanager.check_state_lock(context, draft, before)
    # Settling runs @validate on the document and each row (validate_tree), after every value is final.
    settle(context, draft, complete=True, previous=before, kept=kept)
    if posted and not locked_write:
        guards.check_posted(context, draft, before)
    if locked_write:
        statemanager.keep_saved(draft, before)
    if person:
        business.controls.custom.enforce(model, draft._values, before._values if before else None)
    # Document numbers and names are assigned on the settled draft, in this transaction.
    from . import sequences
    numbers = sequences.assign(context, model, draft, before, rule)
    rules = sequences.naming_rules(business.registry, model)
    if rules is not None and rules.field == 'name' and rules.system and (before is None or before._values.get('name') in (None, '')) \
            and draft._values.get('name') not in (None, ''):
        # A series or autoname assigns the name; people may change it once it has one.
        raise ValidationError({'name': 'Assigned on save'})
    if sequences.name_due(rules, draft._values, created=before is None, move=rule):
        draft._values[rules.field] = sequences.issue(tx.connection, business.registry, model, rules, draft._values, host_today(business),
                                                     user.username, record=draft, context=context, record_id=draft.id)
    # Entering a release step (or a Release) whose content changed since the last release releases it: @release,
    # then calculations and @validate again. The write that changes a released content snapshots that release first.
    released = None
    if release or rule is not None and rule.to in model.__declared_fields__[model.__state__].releases:
        released = statemanager.prepare_release(context, model, draft)
        if released is not None:
            settle(context, draft, complete=True, previous=before, kept=kept)
        elif release:
            raise AccessError(409, 'Nothing changed since the last release')
    if rule is None or rule.sources and before._values.get(model.__state__) not in model.__declared_fields__[model.__state__].releases:
        statemanager.diverge(context, model, before, draft)
    prior = before.stored_values() if before else None
    after = draft.stored_values()
    if prior == after and rule is None and released is None and (before is None or sections.signature(before) == sections.signature(draft)):
        if plan is not None:
            record_plan(tx, business.registry.name_for(model), record_id, version)
        # Every readable row: the HTTP route derives its ETag from this and embeds the first page.
        result = business.detail(tx, user, tx.get(model, record_id), row_limit=None)
        add_outcome(result, outcome)
        return result
    # A closed period (Meta.period) refuses the write whether the saved or the new date is in it.
    from . import periods
    periods.check(tx, model, before=before._values if before is not None else None, after=draft._values)
    record = write_snapshot(tx, draft, before)
    if plan is not None:
        record_plan(tx, business.registry.name_for(model), record.id, version)
    context._active[-1] = (model, record.id)
    business.policy.record(tx, user, model, record.id, 'update' if before else 'create')
    keys = {}
    persist_rows(context, model, draft, before, record.id, keys)
    if released is not None:
        statemanager.finish_release(context, model, record, draft, released, rule, remark)
    # The Journal entry of this write: `moved` for a move, otherwise `created` or `changed`.
    from . import journal
    if rule is not None:
        from .effects import post
        posted_rows = post(context, model, record.id, rule.to)
        statemanager.moved(context, model, record, before._values.get(model.__state__), rule, remark, level, before, draft, released=released)
        journal.effects_posted(context, model, record.id, reversed_rows + posted_rows)
    elif release:
        pass
    else:
        journal.written(context, model, record.id, before, draft, action=action,
                        entering=statemanager.entering(context, model, record) if before is None else None)
    for name, number in numbers:
        sequences.assigned(context, model, record.id, name, number)
    business.changed(tx, model, record.id)
    result = business.detail(tx, user, tx.get(model, record.id), row_limit=None)
    if keys: result['_row_ids'] = keys
    add_outcome(result, outcome)
    return result


def keeps_saved_results(context, model, before, *, ordinary=True):
    """Why a write to a saved document keeps its stored results instead of recalculating them.

    This is the one rule for the save, the draft preview, a record's detail and a trusted settle. It returns
    the reasons that apply, and an empty set when the write recalculates:
    - 'posted': the record has posted effects. Every writer keeps its results.
    - 'locked': the record is in a locked State. Only an `ordinary` write keeps them; a move or an
      action settles normally.
    A new record (`before` is None) keeps nothing.
    """
    if before is None or before.id is None:
        return frozenset()
    from .effects import has_postings
    from .statemanager import locked_state
    reasons = set()
    if has_postings(context._tx.connection, context._business.registry, model, before):
        reasons.add('posted')
    if ordinary and locked_state(model, before._values) is not None:
        reasons.add('locked')
    return frozenset(reasons)


def owner_of(tx, model, record_id):
    """The id of the document a saved Table row belongs to, through every level of rows; None when it is gone."""
    owners = {child.model: child for child in tx.registry.children}
    while model in owners:
        child = owners[model]
        row = tx.connection.run(qb.select(tx.registry.name_for(model), child.link).where(id=record_id)).one()
        if row is None or row[0] is None:
            return None
        model, record_id = child.parent, row[0]
    return record_id


def settle_saved(tx, model, record_id, children=None):
    """Settle a saved document after a trusted Session write to it or to one of its rows (evlet/database.py), for a
    model that declares derive(): derive, clean, calculate and validate, as on any write, then store what changed.

    It runs as the engine, without roles or row scopes, like the trusted write that called it. A record with posted
    effects keeps its stored results, as on every other write. derive() reads through a read-only session, so it
    cannot write and start another settlement. `children` are the document's Table rows the write sends with it,
    merged like the rows of an HTTP write."""
    from .effects import EngineBusiness, ENGINE, SessionPolicy
    business = EngineBusiness(tx._business)
    business.policy = SessionPolicy(tx._business.policy)
    context = Context(business, tx, ENGINE)
    current = tx.get(model, record_id)
    # The shared rule, read for trusted code: only postings keep the saved results here. A locked State does
    # not, because hooks and app code write to a document while it is locked and its derived values must follow.
    kept_by = keeps_saved_results(context, model, Draft(model, dict(current._values), context=context, record_id=current.id),
                                  ordinary=False)
    if 'posted' in kept_by:
        if children:
            raise ValidationError({'children': 'This record has postings. To change its rows, move it out of its posted state.'})
        return
    derived_write(model, {}, children or {}, '')
    draft = Draft(model, dict(current._values), context=context, record_id=current.id)
    before = Draft(model, dict(current._values), context=context, record_id=current.id)
    load_rows(context, model, draft, before, current.id, children or {}, False, '', set(), None,
              SavedRows(context, model, current.id), LinkTargets(context))
    settle(context, draft, complete=True, previous=before)
    if before.stored_values() != draft.stored_values():
        write_snapshot(tx, draft, before, reload=False)
    persist_rows(context, model, draft, before, current.id, {})


def record_plan(tx, resource, record_id, version):
    tx.connection.run(qb.insert('evlet_calculation_versions', resource=resource, record_id=record_id, plan_hash=version)
                      .on_conflict('resource', 'record_id').do_update(plan_hash=qb.col('excluded.plan_hash')))


def persist_rows(context, model, draft, before, owner_id, keys):
    """Write owned rows parent-before-child; delete removed subtrees child-before-parent.

    Permission checks run once per collection: the operation for the role, then one row-scope
    statement for the rows this write created or changed, so they stay inside the user's scope.
    """
    business, tx, user = context._business, context._tx, context.user
    policy = business.policy
    changes = {}
    for child in business.children.values():
        if child.parent is not model: continue
        old_rows = before._rows.get(child.name, []) if before else []
        old_by_id = {row.id: row for row in old_rows}
        rows = draft._rows.get(child.name, [])
        sectioned = sections.enabled(model, child.name)
        if sectioned:
            # Rows and section rows share one order; a handler that changed the rows in place may leave gaps or ties.
            ordered = sections.combined(rows, draft._sections.get(child.name, []))
            if not sections.ordered_positions(ordered):
                for item, position in zip(ordered, positions(ordered, set())):
                    item._values['evlet_position'] = position
        retained = {row.id for row in rows if row.id}
        delete_rows(business, tx, child.model, sorted(set(old_by_id) - retained))
        added, updated, moved, nested = [], [], {}, {}
        for row in rows:
            row._values[child.link] = owner_id
            if row.id is None:
                policy.require(user, child.model, 'create')
                write_snapshot(tx, row, reload=False)
                added.append(row.id)
                keys[row.key] = row.id
            else:
                previous, current = old_by_id[row.id].stored_values(), row.stored_values()
                if previous != current:
                    position = previous.pop('evlet_position', None) != current.pop('evlet_position', None)
                    if previous != current:
                        # A changed row, or one whose owned rows changed, is an update of that row.
                        policy.require(user, child.model, 'update')
                        own = old_by_id[row.id]._values
                        if position or any(own.get(name) != row._values.get(name) for name in child.model.__fields__):
                            write_snapshot(tx, row, old_by_id[row.id], reload=False)
                        updated.append(row.id)
                    elif position:
                        # Rows the caller placed need update permission; rows shifted by others' changes do not.
                        if row._placed: policy.require(user, child.model, 'update')
                        moved[row.id] = row._values['evlet_position']
            inner = persist_rows(context, child.model, row, old_by_id.get(row.id), row.id, keys)
            if inner: nested[row.id] = inner
        write_positions(tx, child.model, moved)
        # The written rows must stay inside this user's row scope.
        for ids, operation in ((added, 'create'), (updated, 'update')):
            if ids and set(ids) - policy.visible(tx, user, child.model, ids, operation):
                raise AccessError(404, 'Record not found')
        changes[child.name] = {'added': added, 'updated': updated, 'removed': sorted(set(old_by_id) - retained),
                               'reordered': [row.id for row in old_rows] != [row.id for row in rows]}
        if nested: changes[child.name]['rows'] = nested
        if sectioned:
            headings = sections.persist(context, child, owner_id, before._sections.get(child.name, []) if before else [],
                                        draft._sections.get(child.name, []), keys, old_rows, rows)
            if headings: changes[child.name]['sections'] = headings
    return changes


def write_positions(tx, model, positions):
    """Store new row positions, one statement per chunk of rows."""
    from .permissions import CHUNK
    items, table = list(positions.items()), tx.registry.name_for(model)
    for start in range(0, len(items), CHUNK):
        part = dict(items[start:start + CHUNK])
        tx.connection.run(qb.update(table, evlet_position=qb.case('id', part)).where(qb.in_('id', list(part))))


def delete_rows(business, tx, model, record_ids):
    """Document-pipeline deletion of rows and their owned subtrees, children first.

    Callers have checked permissions for the whole subtree. One statement per table level and chunk.
    """
    from .permissions import CHUNK
    ids = list(dict.fromkeys(record_ids))
    if not ids:
        return
    sections.forget(tx.connection, business.registry, model, ids)
    for child in business.children.values():
        if child.parent is model:
            table = business.registry.name_for(child.model)
            below = []
            for start in range(0, len(ids), CHUNK):
                part = ids[start:start + CHUNK]
                below.extend(row[0] for row in tx.connection.run(qb.select(table, 'id').where(qb.in_(child.link, part))))
            delete_rows(business, tx, child.model, below)
    table = business.registry.name_for(model)
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        tx.connection.run(qb.delete(table).where(qb.in_('id', part)))


def typed_arguments(function, values, *, skip=0):
    from typing import get_type_hints
    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())[skip:]
    if not isinstance(values, dict) or set(values) - {parameter.name for parameter in parameters}:
        raise ValidationError({'arguments': 'Unknown input fields'})
    annotations = get_type_hints(function)
    result = {}
    for parameter in parameters:
        value = values.get(parameter.name, parameter.default)
        kind = annotations.get(parameter.name)
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY) or kind not in (str, int, bool, float, RecordId):
            raise ValueError('Action/API inputs require explicit scalar type annotations')
        if kind is float and type(value) is int and abs(value) <= 2**53:
            value = float(value)  # JSON cannot tell 1 from 1.0
        if kind is RecordId and value is not inspect.Parameter.empty:
            # A record id argument: the text id (or an int from Python) names a record of any model.
            try:
                value = RecordId(value)
            except ValueError:
                raise ValidationError({parameter.name: 'Expected a record id'}) from None
        if value is inspect.Parameter.empty or not (type(value) is kind or kind is RecordId):
            raise ValidationError({parameter.name: 'Expected ' + kind.__name__})
        import math
        if kind is int and abs(value) > 2**53-1 or kind is float and not math.isfinite(value) or kind is str and len(value) > 65536:
            raise ValidationError({parameter.name: 'Input exceeds the supported bounds'})
        result[parameter.name] = value
    return result


def argument_schema(function, *, skip=0):
    from typing import get_type_hints
    annotations = get_type_hints(function)
    result = []
    for parameter in list(inspect.signature(function).parameters.values())[skip:]:
        kind = annotations.get(parameter.name)
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY) or kind not in (str, int, bool, float, RecordId):
            raise ValueError('Action/API inputs require explicit scalar type annotations')
        item = {'name': parameter.name, 'type': 'record_id' if kind is RecordId else kind.__name__, 'required': parameter.default is inspect.Parameter.empty}
        if not item['required']:
            if type(parameter.default) is not kind: raise ValueError('Argument default must match its declared type')
            item['default'] = parameter.default
        result.append(item)
    return result
