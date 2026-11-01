"""Authoritative document operations sharing the existing transaction and policy."""

from dataclasses import dataclass
from types import MappingProxyType, MethodType
from uuid import uuid4
from contextlib import contextmanager
import inspect
import re

from .auth import AccessError
from .declarations import Ref
from .fields import MISSING, Link
from .models import ValidationError, identifier
from .query import Query
from .reactivity import evaluate, graph, round_field, plans
from .calc import CalcError, Document, LIMITS, is_finite, to_decimal


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
        object.__setattr__(self, '_sources', {})
        object.__setattr__(self, 'context', context)
        object.__setattr__(self, 'parent', parent)
        object.__setattr__(self, 'source', source)
        object.__setattr__(self, 'id', record_id)
        object.__setattr__(self, 'key', str(record_id) if record_id else uuid4().hex)
        object.__setattr__(self, '_readonly', False)
        object.__setattr__(self, '_submitted', frozenset())

    def __getattr__(self, name):
        if name in self._definition.__declared_fields__:
            return self._values.get(name)
        if name in self._definition.__tables__:
            return self._rows.get(name, [])
        if name in self._definition.__sources__:
            return self._sources.get(name)
        value = getattr(self._definition, name)
        return MethodType(value, self) if inspect.isfunction(value) else value

    def __setattr__(self, name, value):
        if self._readonly:
            raise AttributeError('Evaluation drafts are read only')
        if name in self._definition.__declared_fields__:
            if is_finite(self._definition.__declared_fields__[name]):
                raise ValidationError({name: 'COMPUTED_FIELD_WRITE: this value is calculated'})
            self._values[name] = value
        elif name in self._definition.__tables__:
            if not isinstance(value, (list, tuple)) or len(value) > 100:
                raise ValidationError({name: 'Use at most 100 rows'})
            child = self._definition.__tables__[name].child
            self._rows[name] = [row if isinstance(row, Draft) else Draft(child, row, context=self.context, parent=self, source=self.source) for row in value]
        else:
            raise AttributeError('Unknown draft field: ' + name)

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


@dataclass(frozen=True)
class Change:
    operation: str
    actor: object
    before: object
    after: object
    fields: tuple
    children: object


class Context:
    """Permission-aware reads and document writes in one unit of work."""
    def __init__(self, business, tx, user, *, readonly=False, active=None):
        if not business.database.owns(tx):
            raise ValueError('Document context belongs to another database')
        self._business, self._tx, self.user = business, tx, user
        self.readonly = readonly
        self._active = [] if active is None else active
        self._recalculate = False  # Explicit opt-in to recalculate a document saved under another plan.

    def read_context(self):
        return Context(self._business, self._tx, self.user, readonly=True, active=self._active)

    def _model(self, value):
        model = self._business.registry.resolve(value)
        if model in self._business.child_models:
            raise AccessError(403, 'Access child rows through their parent document')
        return model

    def get(self, model, record_id):
        model = self._model(model)
        record = self._business.policy.record(self._tx, self.user, model, record_id)
        value = self._business.detail(self._tx, self.user, record)
        children = value.pop('children', {})
        return Snapshot({**value, **children})

    def list(self, model, *, filters=None, order_by=('id',), limit=50, offset=0):
        model = self._model(model)
        business, tx = self._business, self._tx
        business.policy.require(self.user, model, 'read')
        business.policy.query_fields(self.user, model, filters, order_by)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 2**63-1:
            raise ValidationError({'page': 'Use a page of 1 to 100 rows and a nonnegative offset'})
        query = Query(business.registry, model)
        where, values = query.where(filters)
        scoped, scope_values = business.policy.scope_sql(self.user, model)
        if scoped:
            where += (' AND ' if where else ' WHERE ') + scoped.removeprefix(' WHERE ')
            values.extend(scope_values)
        table = identifier(business.registry.name_for(model))
        rows = tx.connection.execute(f'SELECT record.* FROM {table} AS record{where}{query.ordering(order_by)} LIMIT ? OFFSET ?', [*values, limit, offset]).fetchall()
        total = tx.connection.execute(f'SELECT count(*) FROM {table} AS record{where}', values).fetchone()[0]
        return {'rows': [business.policy.serialize(tx, self.user, model._from_row(row)) for row in rows],
                'total': total, 'limit': limit, 'offset': offset}

    def count(self, model, *, filters=None):
        return self.list(model, filters=filters, limit=1)['total']

    def create(self, model, **values):
        return self._write(model, None, values)

    def update(self, model, record_id, **values):
        return self._write(model, record_id, values)

    def action(self, model, record_id, name, **arguments):
        return self._write(model, record_id, {}, action=name, arguments=arguments)

    def delete(self, model, record_id):
        if self.readonly: raise AccessError(403, 'This context is read only')
        model = self._model(model)
        key = (model, record_id)
        if key in self._active or len(self._active) >= 16:
            raise ValidationError({'_form': 'Recursive document change detected'})
        self._active.append(key)
        try:
            business, tx, user = self._business, self._tx, self.user
            record = business.policy.record(tx, user, model, record_id, 'delete')
            before, _ = load_draft(self, model, record_id, {}, preview=True)
            child_changes = {}
            for prefix, item in walk(before):
                if item is not before: business.policy.record(tx, user, item._definition, item.id, 'delete')
            for child in business.children.values():
                if child.parent is not model: continue
                rows = before._rows.get(child.name, [])
                child_changes[child.name] = {'added': [], 'updated': [], 'removed': [row.id for row in rows], 'reordered': False}
                for row in rows: delete_tree(business, tx, child.model, row.id)
            with tx.maintenance():
                tx.delete(model, record.id)
            if business.registry.plan_for(model) is not None:
                tx.connection.execute('DELETE FROM evlet_calculation_versions WHERE resource=? AND record_id=?', (business.registry.name_for(model), record.id))
            tx.connection.execute('DELETE FROM evlet_attachments WHERE model=? AND record_id=?', (business.registry.name_for(model), record_id))
            reaction = getattr(model, 'changed', None)
            if reaction:
                freeze_draft(before)
                reaction(before, Change('delete', user, freeze(before.stored_values()), None, (), freeze(child_changes)))
            business.changed(tx, model, record_id, deleted=True)
        finally:
            self._active.pop()

    def _write(self, model, record_id, values, *, action=None, arguments=None):
        if self.readonly:
            raise AccessError(403, 'This context is read only')
        model = self._model(model)
        if not getattr(model, '__declaration_mode__', False):
            raise ValueError('Contextual writes require declarative models')
        key = (model, record_id)
        if key in self._active or len(self._active) >= 16:
            raise ValidationError({'_form': 'Recursive document change detected'})
        self._active.append(key)
        try:
            return operate(self, model, record_id, values, action=action, arguments=arguments)
        except ValidationError as exc:
            raise ValidationError(self._business.visible_errors(self.user, model, exc.errors, tx=self._tx)) from None
        finally:
            self._active.pop()

    def enqueue(self, task, payload, *, key=None, target=None):
        if self.readonly:
            raise AccessError(403, 'This context is read only')
        if self._business.jobs is None:
            raise ValueError('Jobs are unavailable')
        if target is not None: self._business.policy.record(self._tx, self.user, target[0], target[1], 'update')
        return self._business.jobs.enqueue(self._tx, task, payload, user=self.user if self.user.id else None,
                                          system_role=self.user.role if not self.user.id else None, key=key, target=target)


def load_draft(context, model, record_id, values, *, preview=False):
    business, tx, user = context._business, context._tx, context.user
    operation = 'update' if record_id else 'create'
    business.policy.require(user, model, 'read' if preview and record_id else operation)
    current = business.policy.record(tx, user, model, record_id, 'read' if preview else operation) if record_id else None
    scalars, collections = business.split_body(values)
    computed_write(business, model, scalars, '')
    business.policy.check_values(tx, user, model, scalars)
    draft = Draft(model, {**(dict(current._values) if current else {}), **scalars}, context=context, record_id=record_id)
    object.__setattr__(draft, '_submitted', frozenset(scalars))
    old = Draft(model, dict(current._values), context=context, record_id=record_id) if current else None
    load_rows(context, model, draft, old, record_id, collections, preview, '', set(), [0])
    if business.registry.plan_for(model) is not None and sum(1 for _ in walk(draft)) - 1 > LIMITS['document_rows']:
        raise ValidationError({'children': 'This document exceeds the owned row limit'})
    return draft, old


def computed_write(business, model, values, prefix):
    """Finite-v1 calculated fields have one writer: clients submit sources only."""
    if business.registry.plan_for(model) is None:
        return
    written = [name for name in values if name in model.__declared_fields__ and is_finite(model.__declared_fields__[name])]
    if written:
        raise ValidationError({prefix + name: 'COMPUTED_FIELD_WRITE: this value is calculated' for name in written})


def load_rows(context, model, draft, old, record_id, collections, preview, prefix, keys_seen, loaded=None):
    """Merge submitted child collections at every depth, keeping hidden rows."""
    business, tx, user = context._business, context._tx, context.user
    loaded = [0] if loaded is None else loaded

    def count(rows):
        loaded[0] += rows
        if loaded[0] > 2 * LIMITS['document_rows']:
            raise ValidationError({'children': 'This document exceeds the owned row limit'})
    if set(collections) - {child.name for child in business.children.values() if child.parent is model}:
        raise ValidationError({prefix + 'children': 'Unknown child collection'})
    for child in business.children.values():
        if child.parent is not model: continue
        existing = tx.list(child.model, filters={child.link: record_id}, limit=100, order_by=('evlet_position', 'id') if 'evlet_position' in child.model.__fields__ else ('id',)) if record_id else []
        if record_id and tx.count(child.model, filters={child.link: record_id}) > 100:
            raise ValidationError({prefix + 'children': 'This collection exceeds the 100-row editor limit'})
        count(len(existing))
        originals = {row.id: row for row in existing}
        hidden = set()
        for row in existing:
            try: business.policy.record(tx, user, child.model, row.id)
            except AccessError: hidden.add(row.id)

        def stored(row, owner):
            item = Draft(child.model, row._values, context=context, parent=owner, record_id=row.id)
            load_rows(context, child.model, item, None, row.id, {}, True, prefix + child.name + '.' + item.key + '.', set(), loaded)
            return item
        if old is not None:
            old._rows[child.name] = [stored(row, old) for row in existing]
        if child.name not in collections:
            draft._rows[child.name] = [stored(row, draft) for row in existing]
            continue
        rows = collections[child.name]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValidationError({prefix + child.name: 'Use at most 100 rows'})
        count(len(rows))
        kept, keys, merged = set(), set(), []
        for index, value in enumerate(rows):
            if not isinstance(value, dict):
                raise ValidationError({prefix + child.name: 'Each row must be an object'})
            value = dict(value)
            row_id = value.pop('id', None)
            temporary = value.pop('_key', None)
            # New rows' keys map to IDs in one flat _row_ids object, so they are unique document-wide.
            fresh = row_id is None
            if temporary is not None and (not isinstance(temporary, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', temporary) or temporary in keys or fresh and temporary in keys_seen):
                raise ValidationError({prefix + child.name: 'Row keys must be distinct (new rows: within the document) and use at most 64 letters, digits, hyphens or underscores'})
            keys.add(temporary) if temporary is not None else None
            keys_seen.add(temporary) if temporary is not None and fresh else None
            value, nested = business.split_body(value)
            computed_write(business, child.model, value, prefix + child.name + '.')
            business.policy.check_values(tx, user, child.model, value)
            if row_id is not None:
                if type(row_id) is not int or row_id not in originals or row_id in kept or row_id in hidden:
                    raise ValidationError({prefix + child.name: 'Child ID must belong to this parent and appear once'})
                business.policy.record(tx, user, child.model, row_id, 'read' if preview or not value else 'update')
                kept.add(row_id)
            elif not preview:
                business.policy.require(user, child.model, 'create')
            row = Draft(child.model, {**(dict(originals[row_id]._values) if row_id else {}), **value}, context=context, parent=draft, record_id=row_id)
            object.__setattr__(row, '_submitted', frozenset(value))
            if 'evlet_position' in child.model.__fields__: row._values['evlet_position'] = index
            if temporary is not None: object.__setattr__(row, 'key', temporary)
            load_rows(context, child.model, row, None, row_id, nested, preview, prefix + child.name + '.' + row.key + '.', keys_seen, loaded)
            merged.append(row)
        for row_id in originals.keys() - kept - hidden:
            if not preview:
                # Removing a row removes its whole subtree: every descendant must be deletable too.
                for _, item in walk(stored(originals[row_id], draft)):
                    try: business.policy.record(tx, user, item._definition, item.id, 'delete')
                    except AccessError: raise AccessError(403, 'You do not have permission for this operation') from None
        merged.extend(stored(originals[row_id], draft) for row_id in sorted(hidden))
        if len(merged) > 100: raise ValidationError({prefix + child.name: 'Use at most 100 rows'})
        draft._rows[child.name] = merged


def walk(draft, prefix=''):
    """Pre-order (prefix, draft) pairs: a document, then each row and its descendants."""
    yield prefix, draft
    for name, rows in draft._rows.items():
        for row in rows:
            yield from walk(row, prefix + name + '.' + row.key + '.')


def matching(previous, item, draft):
    """The prior version of a row (same model and ID) anywhere in the previous tree."""
    if item is draft: return previous
    if previous is None or item.id is None: return None
    return next((row for _, row in walk(previous) if row is not previous and row.id == item.id and row._definition is item._definition), None)


def settle(context, draft, *, complete, previous=None):
    registry = context._business.registry
    nodes, order = graph(registry, draft._definition)
    errors, states = {}, {}
    source_cache = {}
    all_drafts = list(walk(draft))
    # Owner links of legacy child tables are set from the saved parent at write time.
    owner_links = {child.model: child.link for child in context._business.children.values() if child.parent is draft._definition}
    def internal(item):
        names = set(getattr(item._definition, '__internal_fields__', ()))
        return names | {owner_links[item._definition]} if item is not draft and item._definition in owner_links else names
    for prefix, item in all_drafts:
        for name, field in item._definition.__declared_fields__.items():
            if name in internal(item): continue
            value = item._values.get(name, MISSING)
            if value is MISSING and item.id is None:
                if isinstance(field.default, Ref): value = item.read(field.default.path)
                elif field.default is not MISSING: value = field.default() if callable(field.default) else field.default
            if field.compute is not None or field.query is not None: continue
            try:
                item._values[name] = None if value is MISSING or value is None else field.validate(value)
                if field.kind == 'integer' and item._values[name] is not None and abs(item._values[name]) > 2**53-1:
                    raise ValueError('Document integers must fit the browser safe range')
            except ValueError as exc:
                errors[prefix + name] = str(exc)
        for name, field in item._definition.__declared_fields__.items():
            if not field.fetch: continue
            link_name, target_name = field.fetch.split('.')
            link = item._definition.__declared_fields__.get(link_name)
            if not isinstance(link, Link) or target_name not in link.target.__declared_fields__:
                raise ValueError('Fetch must reference a declared Link target field')
            old = matching(previous, item, draft)
            if not field.read_only and name in item._submitted and item._values.get(name) is not None:
                if old is None or old._values.get(name) != item._values.get(name) or old._values.get(link_name) == item._values.get(link_name): continue
            if not field.read_only and old is not None and old._values.get(link_name) == item._values.get(link_name): continue
            try:
                value = None
                if item._values.get(link_name) is not None:
                    record = context._business.policy.record(context._tx, context.user, link.target, item._values[link_name])
                    if target_name not in context._business.policy.fields(context.user, link.target): raise AccessError(403, 'Fetched field is unavailable')
                    value = getattr(record, target_name)
                item._values[name] = field.clean(value)
            except ValueError as exc: errors[prefix + name] = str(exc)
    plan = registry.plan_for(draft._definition)
    if plan is not None:
        finite_settle(plan, draft, errors, states, complete)
    for key in ([] if plan is not None else order):
        parts = key.split('.')
        items = [(draft, '')] if len(parts) == 1 else [(row, parts[0] + '.' + row.key + '.') for row in draft._rows.get(parts[0], [])]
        name = parts[-1]
        for item, prefix in items:
            if name in item._definition.__sources__:
                source = item._definition.__sources__[name]
                inputs = {key: item.read(value.path) if isinstance(value, Ref) else value for key, value in source.filters.items()}
                inputs.update({path: item.read(path.replace('.*.', '.')) for path in getattr(source.target, '__depends__', ())})
                if any(value is None or value == '' for value in inputs.values()):
                    item._sources[name] = None
                    states[prefix + name] = 'missing-input'
                    continue
                import json
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
                continue
            field = item._definition.__declared_fields__[name]
            try:
                if field.compute is not None:
                    value = round_field(field, evaluate(nodes[key]['compute'], item.read))
                    item._values[name] = field.validate(value) if value is not None else None
                    if value is None: states[prefix + name] = 'missing-input'
                if field.query is not None:
                    dependencies = getattr(field.query, '__depends__')
                    if any(item.read(path.replace('.*.', '.')) is None for path in dependencies):
                        item._values[name] = None
                        states[prefix + name] = 'missing-input'
                        continue
                    import json
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
    if not complete:
        for prefix, item in all_drafts:
            for name, field in item._definition.__declared_fields__.items():
                if name in internal(item): continue
                try:
                    required = field.required or field.required_if is not None and evaluate(plans(item._definition)[name]['required_if'], item.read)
                    if required and item._values.get(name) in (None, ''): states[prefix + name] = 'required-input'
                    if isinstance(field, Link) and item._values.get(name) is not None and field.filters:
                        target = context._business.policy.record(context._tx, context.user, field.target, item._values[name])
                        for filter_name, expected in field.filters.items():
                            if filter_name not in context._business.policy.fields(context.user, field.target): raise AccessError(403, 'A selection constraint is unavailable')
                            expected = item.read(expected.path) if isinstance(expected, Ref) else expected
                            if expected is None or getattr(target, filter_name) != expected: errors[prefix + name] = 'The selected record does not match the current filters'
                except (ValueError, TypeError, ArithmeticError) as exc: errors[prefix + name] = str(exc)
    if complete:
        for prefix, item in reversed(all_drafts):
            for name, field in item._definition.__declared_fields__.items():
                if name in internal(item): continue
                try:
                    old = matching(previous, item, draft)
                    if name in item._submitted and field.read_only_if is not None and evaluate(plans(item._definition)[name]['read_only_if'], item.read):
                        if old is None or old._values.get(name) != item._values.get(name):
                            raise ValueError('This field is read only in the current state')
                    if field.required_if is not None and evaluate(plans(item._definition)[name]['required_if'], item.read) and item._values.get(name) in (None, ''):
                        raise ValueError('This field is required')
                    item._values[name] = field.clean(item._values.get(name))
                    if isinstance(field, Link) and item._values[name] is not None:
                        target = context._business.policy.record(context._tx, context.user, field.target, item._values[name])
                        for filter_name, expected in field.filters.items():
                            if filter_name not in context._business.policy.fields(context.user, field.target):
                                raise AccessError(403, 'A selection constraint is unavailable')
                            expected = item.read(expected.path) if isinstance(expected, Ref) else expected
                            if expected is None or getattr(target, filter_name) != expected:
                                raise ValueError('The selected record does not match the current filters')
                except ValueError as exc:
                    errors[prefix + name] = str(exc)
            for name, table in item._definition.__tables__.items():
                if table.required and not item._rows.get(name): errors[prefix + name] = 'Add at least one row'
            if not errors:
                try: item.validate()
                except ValidationError as exc: errors.update({prefix + key: value for key, value in exc.errors.items()})
        if errors: raise ValidationError(errors)
    return errors, states


MESSAGES = {'COMPUTE_INPUT_MISSING': 'A required calculation input is missing',
            'COMPUTE_DIVISION_BY_ZERO': 'Division by zero', 'COMPUTE_RANGE_ERROR': 'Calculation exceeds the supported range',
            'COMPUTE_SCALE_ERROR': 'The result needs an explicit round() to fit this field',
            'COMPUTE_TYPE_ERROR': 'An input has the wrong type', 'PARENT_CONTEXT_REQUIRED': 'The owning document is unavailable'}


def finite_settle(plan, draft, errors, states, complete):
    """Authoritative finite-v1 evaluation over the whole draft tree in this transaction."""
    def data(item):
        values = {name: item._values.get(name) for name, field in item._definition.__declared_fields__.items()
                  if not is_finite(field) and name not in getattr(item._definition, '__internal_fields__', ())}
        values['_key'], values['id'] = item.key, item.id
        if item._rows:
            values['children'] = {name: [data(row) for row in rows] for name, rows in item._rows.items()}
        return values
    try:
        document = Document(plan, data(draft))
    except CalcError as exc:
        raise ValidationError({'_form': f'{exc.code}: {exc.message}'}) from None
    fields = {}
    for field in plan['fields']:
        fields.setdefault(tuple(field['path']), []).append(field)

    def apply(item, row, prefix):
        for field in fields.get(row.path, ()):
            name = field['field']
            result = document.results[row.handle, name]
            definition = item._definition.__declared_fields__[name]
            value = None
            if result.state == 'ready' and result.value is not None:
                value = int(to_decimal(result.value)) if field['target_type'] == 'integer' else to_decimal(result.value) if field['result_type'] == 'decimal' else result.value
                value = definition.validate(value)
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


def write_snapshot(tx, draft):
    model = draft._definition
    names = list(model.__fields__)
    values = [model.__fields__[name].to_storage(draft._values.get(name)) for name in names]
    table = identifier(tx.registry.name_for(model))
    if draft.id is None:
        sql = f'INSERT INTO {table} ({", ".join(identifier(name) for name in names)}) VALUES ({", ".join("?" for _ in names)})' if names else f'INSERT INTO {table} DEFAULT VALUES'
        result = tx.connection.execute(sql, values)
        object.__setattr__(draft, 'id', result.lastrowid)
    elif names:
        tx.connection.execute(f'UPDATE {table} SET {", ".join(identifier(name) + "=?" for name in names)} WHERE id=?', [*values, draft.id])
    return tx.get(model, draft.id)


def operate(context, model, record_id, values, *, action=None, arguments=None):
    business, tx, user = context._business, context._tx, context.user
    draft, before = load_draft(context, model, record_id, values)
    plan = business.registry.plan_for(model)
    if plan is not None and record_id:
        row = tx.connection.execute('SELECT plan_hash FROM evlet_calculation_versions WHERE resource=? AND record_id=?',
                                    (business.registry.name_for(model), record_id)).fetchone()
        if (row is None or row[0] != plan['model_schema_hash']) and not context._recalculate:
            raise AccessError(409, 'SCHEMA_VERSION_MISMATCH: this record was calculated with another plan; '
                                   'resubmit with "_calculation": {"model_schema_hash": <active>, "recalculate": true} to recalculate it')
    outcome = None
    if action is not None:
        method = getattr(model, action, None)
        declaration = getattr(method, '__action__', None)
        if declaration is None or user.role not in declaration['roles']:
            raise AccessError(403, 'Action is unavailable')
        outcome = method(draft, **typed_arguments(method, arguments or {}, skip=1))
    settle(context, draft, complete=True, previous=before)
    prior = before.stored_values() if before else None
    after = draft.stored_values()
    if prior == after:
        if plan is not None and context._recalculate:
            record_plan(tx, business.registry.name_for(model), record_id, plan)
        result = business.detail(tx, user, tx.get(model, record_id))
        if outcome is not None: result['_outcome'] = outcome
        return result
    record = write_snapshot(tx, draft)
    if plan is not None:
        record_plan(tx, business.registry.name_for(model), record.id, plan)
    context._active[-1] = (model, record.id)
    business.policy.record(tx, user, model, record.id, 'update' if before else 'create')
    keys = {}
    child_changes = persist_rows(context, model, draft, before, record.id, keys)
    after = draft.stored_values()
    change = Change('create' if before is None else 'action' if action else 'update', user,
                    freeze(prior) if prior is not None else None, freeze(after),
                    tuple(name for name in model.__fields__ if before is None or before._values.get(name) != draft._values.get(name)),
                    freeze(child_changes))
    reaction = getattr(model, 'changed', None)
    if reaction is not None:
        freeze_draft(draft)
        reaction(draft, change)
    business.changed(tx, model, record.id)
    result = business.detail(tx, user, tx.get(model, record.id))
    if keys: result['_row_ids'] = keys
    if outcome is not None: result['_outcome'] = outcome
    return result


def record_plan(tx, resource, record_id, plan):
    tx.connection.execute('INSERT INTO evlet_calculation_versions(resource,record_id,plan_hash) VALUES (?,?,?) '
                          'ON CONFLICT(resource, record_id) DO UPDATE SET plan_hash=excluded.plan_hash',
                          (resource, record_id, plan['model_schema_hash']))


def persist_rows(context, model, draft, before, owner_id, keys):
    """Write owned rows parent-before-child; delete removed subtrees child-before-parent."""
    business, tx, user = context._business, context._tx, context.user
    changes = {}
    for child in business.children.values():
        if child.parent is not model: continue
        old_rows = before._rows.get(child.name, []) if before else []
        old_by_id = {row.id: row for row in old_rows}
        rows = draft._rows.get(child.name, [])
        retained = {row.id for row in rows if row.id}
        for row_id in set(old_by_id) - retained: delete_tree(business, tx, child.model, row_id)
        added, updated, nested = [], [], {}
        for row in rows:
            row._values[child.link] = owner_id
            if row.id is None:
                write_snapshot(tx, row)
                business.policy.record(tx, user, child.model, row.id, 'create')
                added.append(row.id)
                keys[row.key] = row.id
            elif row.stored_values() != old_by_id[row.id].stored_values():
                business.policy.record(tx, user, child.model, row.id, 'update')
                write_snapshot(tx, row)
                updated.append(row.id)
            inner = persist_rows(context, child.model, row, old_by_id.get(row.id), row.id, keys)
            if inner: nested[row.id] = inner
        changes[child.name] = {'added': added, 'updated': updated, 'removed': sorted(set(old_by_id) - retained),
                               'reordered': [row.id for row in old_rows] != [row.id for row in rows]}
        if nested: changes[child.name]['rows'] = nested
    return changes


def delete_tree(business, tx, model, record_id):
    """Document-pipeline deletion; callers have checked permissions for the whole subtree."""
    for child in business.children.values():
        if child.parent is model:
            for row in tx.list(child.model, filters={child.link: record_id}, limit=100):
                delete_tree(business, tx, child.model, row.id)
    with tx.maintenance():
        tx.delete(model, record_id)


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
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY) or kind not in (str, int, bool, float):
            raise ValueError('Action/API inputs require explicit scalar type annotations')
        if kind is float and type(value) is int and abs(value) <= 2**53:
            value = float(value)  # JSON cannot tell 1 from 1.0
        if value is inspect.Parameter.empty or type(value) is not kind:
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
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY) or kind not in (str, int, bool, float):
            raise ValueError('Action/API inputs require explicit scalar type annotations')
        item = {'name': parameter.name, 'type': kind.__name__, 'required': parameter.default is inspect.Parameter.empty}
        if not item['required']:
            if type(parameter.default) is not kind: raise ValueError('Argument default must match its declared type')
            item['default'] = parameter.default
        result.append(item)
    return result
