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
            for child in business.children.values():
                if child.parent is not model: continue
                rows = before._rows.get(child.name, [])
                for row in rows: business.policy.record(tx, user, child.model, row.id, 'delete')
                child_changes[child.name] = {'added': [], 'updated': [], 'removed': [row.id for row in rows], 'reordered': False}
                for row in rows: tx.delete(child.model, row.id)
            tx.delete(model, record.id)
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
    business.policy.check_values(tx, user, model, scalars)
    if set(collections) - {child.name for child in business.children.values() if child.parent is model}:
        raise ValidationError({'children': 'Unknown child collection'})
    draft = Draft(model, {**(dict(current._values) if current else {}), **scalars}, context=context, record_id=record_id)
    object.__setattr__(draft, '_submitted', frozenset(scalars))
    old = Draft(model, dict(current._values), context=context, record_id=record_id) if current else None
    for child in business.children.values():
        if child.parent is not model: continue
        existing = tx.list(child.model, filters={child.link: record_id}, limit=100, order_by=('evlet_position', 'id') if 'evlet_position' in child.model.__fields__ else ('id',)) if record_id else []
        if record_id and tx.count(child.model, filters={child.link: record_id}) > 100:
            raise ValidationError({'children': 'This collection exceeds the 100-row editor limit'})
        originals = {row.id: row for row in existing}
        hidden = set()
        for row in existing:
            try: business.policy.record(tx, user, child.model, row.id)
            except AccessError: hidden.add(row.id)
        if old is not None:
            old._rows[child.name] = [Draft(child.model, row._values, context=context, parent=old, record_id=row.id) for row in existing]
        if child.name not in collections:
            draft._rows[child.name] = [Draft(child.model, row._values, context=context, parent=draft, record_id=row.id) for row in existing]
            continue
        rows = collections[child.name]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValidationError({child.name: 'Use at most 100 rows'})
        kept, keys, merged = set(), set(), []
        for index, value in enumerate(rows):
            if not isinstance(value, dict):
                raise ValidationError({child.name: 'Each row must be an object'})
            value = dict(value)
            row_id = value.pop('id', None)
            temporary = value.pop('_key', None)
            if temporary is not None and (not isinstance(temporary, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', temporary) or temporary in keys):
                raise ValidationError({child.name: 'Row keys must be distinct strings of at most 64 letters, digits, hyphens or underscores'})
            keys.add(temporary) if temporary is not None else None
            business.policy.check_values(tx, user, child.model, value)
            if row_id is not None:
                if type(row_id) is not int or row_id not in originals or row_id in kept or row_id in hidden:
                    raise ValidationError({child.name: 'Child ID must belong to this parent and appear once'})
                business.policy.record(tx, user, child.model, row_id, 'read' if preview or not value else 'update')
                kept.add(row_id)
            elif not preview:
                business.policy.require(user, child.model, 'create')
            row = Draft(child.model, {**(dict(originals[row_id]._values) if row_id else {}), **value}, context=context, parent=draft, record_id=row_id)
            object.__setattr__(row, '_submitted', frozenset(value))
            if 'evlet_position' in child.model.__fields__: row._values['evlet_position'] = index
            if temporary is not None: object.__setattr__(row, 'key', temporary)
            merged.append(row)
        for row_id in originals.keys() - kept - hidden:
            if not preview: business.policy.record(tx, user, child.model, row_id, 'delete')
        merged.extend(Draft(child.model, originals[row_id]._values, context=context, parent=draft, record_id=row_id) for row_id in hidden)
        if len(merged) > 100: raise ValidationError({child.name: 'Use at most 100 rows'})
        draft._rows[child.name] = merged
    return draft, old


def settle(context, draft, *, complete, previous=None):
    registry = context._business.registry
    nodes, order = graph(registry, draft._definition)
    errors, states = {}, {}
    source_cache = {}
    all_drafts = [('', draft)] + [(name + '.' + row.key + '.', row) for name, rows in draft._rows.items() for row in rows]
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
            old = previous if item is draft else next((row for rows in (previous._rows.values() if previous else ()) for row in rows if row.id is not None and row.id == item.id and row._definition is item._definition), None)
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
    for key in order:
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
                    old = previous if item is draft else next((row for rows in (previous._rows.values() if previous else ()) for row in rows if row.id is not None and row.id == item.id and row._definition is item._definition), None)
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
        result = business.detail(tx, user, tx.get(model, record_id))
        if outcome is not None: result['_outcome'] = outcome
        return result
    record = write_snapshot(tx, draft)
    context._active[-1] = (model, record.id)
    business.policy.record(tx, user, model, record.id, 'update' if before else 'create')
    child_changes, keys = {}, {}
    for child in business.children.values():
        if child.parent is not model: continue
        old_rows = before._rows.get(child.name, []) if before else []
        old_ids = {row.id for row in old_rows}
        rows = draft._rows.get(child.name, [])
        retained = {row.id for row in rows if row.id}
        for row_id in old_ids - retained: tx.delete(child.model, row_id)
        added, updated = [], []
        old_values = {row.id: row.stored_values() for row in old_rows}
        for row in rows:
            row._values[child.link] = record.id
            if row.id is None:
                write_snapshot(tx, row)
                business.policy.record(tx, user, child.model, row.id, 'create')
                added.append(row.id)
                keys[row.key] = row.id
            elif row.stored_values() != old_values[row.id]:
                business.policy.record(tx, user, child.model, row.id, 'update')
                write_snapshot(tx, row)
                updated.append(row.id)
        child_changes[child.name] = {'added': added, 'updated': updated, 'removed': sorted(old_ids - retained),
                                     'reordered': [row.id for row in old_rows] != [row.id for row in rows]}
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
