"""Backend model, field, and row rules. Python database sessions remain trusted."""

from dataclasses import dataclass
from types import MappingProxyType

from .auth import AccessError, ROLES
from .fields import Link, Reference
from .models import Model, ValidationError, identifier
from .query import SqlQuery, OPERATORS


# IDs per batched permission or row statement; well under SQLite's bound-parameter limit.
CHUNK = 500
# Engine corrections: admin has them by default and a Rule may grant them to other roles too.
CORRECTIONS = ('void', 'revise')


def corrects(model, operation):
    """Whether a model supports a correction: void needs voidable states, revise also Meta.revisable."""
    state = getattr(model, '__state__', None)
    voidable = state is not None and bool(model.__declared_fields__[state].voidable)
    return voidable and (operation == 'void' or getattr(model, '__revisable__', False))


@dataclass(frozen=True)
class Rule:
    operations: tuple = ('read',)
    read_fields: tuple | None = None
    write_fields: tuple | None = None
    scope: object = None


def default_rule(base):
    """The framework default for a base role: admin and editor everything, viewer read, any other base nothing."""
    return Rule(('read', 'create', 'update', 'delete') if base in ('admin', 'editor') else ('read',) if base == 'viewer' else ())


def child_operations(operations):
    result = {'read'} & set(operations)
    if result and {'create', 'update'} & set(operations):
        result.add('create')
    if result and 'update' in operations:
        result |= {'update', 'delete'}
    return tuple(name for name in ('read', 'create', 'update', 'delete') if name in result)


class Policy:
    def __init__(self, registry, *, rules=None, roles=None, overrides=None):
        """rules are the app files' rules; overrides (Controls) replace a role's rule on a model.

        roles maps each role to its base framework role; a custom role without its own rule on a
        model inherits the base role's rule from the files.
        """
        self.registry = registry
        self.roles = MappingProxyType(dict(roles or {role: role for role in ROLES}))
        if any(self.roles.get(role) != role for role in ROLES) or set(self.roles.values()) - set(ROLES) or 'admin' in [base for role, base in self.roles.items() if role != 'admin']:
            raise ValueError('Roles must include the framework roles, and custom roles must be based on editor or viewer')
        self.declared = MappingProxyType(dict(rules or {}))
        self.rules = dict(rules or {})
        # Declared permissions, build resources, tables and sources deny by default;
        # computed fields or actions alone keep legacy permission defaults.
        owners = {child.model: child.parent for child in registry.children}
        def declared(model):
            # Registry wraps each Table child in a generated subclass of the author's class.
            base = model.__bases__[0] if model in owners else model
            return base, any('permissions' in vars(cls) for cls in base.__mro__ if cls.__module__ not in (Model.__module__, 'builtins'))
        def governed(model):
            if declared(model)[1]: return True
            if model in owners: return governed(owners[model])
            return bool(getattr(model, '__resource_id__', None) or model.__tables__ or model.__sources__)
        def default(role, model):
            if (role, model) not in self.rules:
                base, own = declared(model)
                if model in owners and not own:
                    # Editing a document's lines is an update of that document.
                    self.rules[role, model] = Rule(child_operations(default(role, owners[model]).operations))
                elif role not in ROLES and role not in getattr(base, 'permissions', {}):
                    self.rules[role, model] = default(self.roles.get(role, 'viewer'), model)
                else:
                    self.rules[role, model] = getattr(base, 'permissions', {}).get(role, Rule(()))
            return self.rules[role, model]
        self.governed = frozenset(model for model in registry.models.values() if governed(model))
        for model in self.governed:
            for role in self.roles:
                default(role, model)
        for model in registry.models.values():
            for role, base in self.roles.items():
                if role != base and (role, model) not in self.rules and (base, model) in self.rules:
                    self.rules[role, model] = self.rules[base, model]
        self.file_rules = MappingProxyType(dict(self.rules))
        for (role, model), rule in dict(overrides or {}).items():
            if role == 'admin':
                raise ValueError('Admin rules cannot be overridden')
            self.rules[role, model] = rule
        # Rules are fixed per policy, so cached finite derivation checks cannot go stale.
        self.rules = MappingProxyType(self.rules)
        for (role, model), rule in self.rules.items():
            registry.name_for(model)
            if role not in self.roles or not isinstance(rule, Rule) or set(rule.operations) - {'read', 'create', 'update', 'delete', *CORRECTIONS}:
                raise ValueError('Invalid permission rule')
            if 'revise' in rule.operations and 'create' not in rule.operations:
                raise ValueError(f'{model.__name__}: Rule names \'revise\' without \'create\'; a revise creates a draft copy')
            for operation in CORRECTIONS:
                if operation in rule.operations and not corrects(model, operation):
                    raise ValueError(f'{model.__name__}: Rule names {operation!r}, but the model cannot be '
                                     + ('voided (declare voidable states on its State)' if operation == 'void' else 'revised (set Meta.revisable = True)'))
            if rule.operations and 'read' not in rule.operations:
                raise ValueError('Write permissions require read access')
            for fields in (rule.read_fields, rule.write_fields):
                if fields is not None and (not isinstance(fields, tuple) or set(fields) - set(model.__declared_fields__)):
                    raise ValueError('Permission fields must name model fields')
            if rule.scope is not None and not callable(rule.scope):
                raise ValueError('Row scope must be a callable returning query filters')
        # Void and revise: admin, when its Rule allows reading and updating the model (and creating, for
        # revise), plus every role whose Rule names the operation. The rules table itself is not changed.
        corrections = {}
        for model in registry.models.values():
            for operation in CORRECTIONS:
                if not corrects(model, operation):
                    continue
                named = {role for role in self.roles if operation in self.rules.get((role, model), Rule(())).operations}
                admin = set(self.rules.get(('admin', model), Rule(('read', 'create', 'update', 'delete'))).operations)
                if {'read', 'update', *(('create',) if operation == 'revise' else ())} <= admin:
                    named.add('admin')
                corrections[model, operation] = frozenset(named)
        self.corrections = MappingProxyType(corrections)

    def rule(self, user, model):
        found = self.rules.get((user.role, model))
        if found is None:
            found = default_rule(self.roles.get(user.role))
        access = getattr(user, 'access', None)
        if access is None:
            return found
        # An API key narrows its user's rule: its models only, and reads only unless it may write.
        if access.models is not None and model not in access.models:
            return Rule(())
        if not access.write:
            return Rule(tuple(op for op in found.operations if op == 'read'), found.read_fields, None, found.scope)
        return found

    def allows(self, user, model, operation):
        if operation in ('create', 'update', 'delete') and model in getattr(self.registry, 'effect_targets', ()):
            # Effect rows are written only by the engine, so no role may change them.
            return False
        if operation in CORRECTIONS:
            access = getattr(user, 'access', None)
            if access is not None and (not access.write or access.models is not None and model not in access.models):
                return False
            return user.role in self.corrections.get((model, operation), ())
        return operation in self.rule(user, model).operations

    def require(self, user, model, operation):
        if not self.allows(user, model, operation):
            raise AccessError(403, 'You do not have permission for this operation')

    def presented(self, user, model):
        """Fields this role may see before derivation checks."""
        rule = self.rule(user, model)
        readable = set(model.__declared_fields__ if rule.read_fields is None else rule.read_fields)
        # Links to models that cannot be read must not expose their identifiers.
        readable = {name for name in readable if not isinstance(model.__declared_fields__[name], Link) or self.allows(user, model.__declared_fields__[name].target, 'read')}
        if model in self.governed:
            readable = {name for name in readable if not model.__declared_fields__[name].hidden}
        return readable

    def fields(self, user, model, *, write=False, _visiting=frozenset()):
        rule = self.rule(user, model)
        readable = self.presented(user, model)
        if getattr(model, '__declaration_mode__', False):
            readable = {name for name in readable if self.derivation_allowed(user, model, name, _visiting)}
        if not write:
            return readable
        writable = readable & set(model.__declared_fields__ if rule.write_fields is None else rule.write_fields)
        return {name for name in writable if not model.__declared_fields__[name].read_only and not name.startswith('evlet_')}

    def derivation_allowed(self, user, model, name, visiting=frozenset()):
        from .reactivity import plans, references
        from .calc import is_finite
        field = model.__declared_fields__[name]
        if is_finite(field) or getattr(field, 'initial', None) is not None:
            # A finite result (or initial suggestion) is readable only when every input and whole collection
            # it reads is, and every field on a Link path is readable on every record (no row scope on a
            # looked-up model). Otherwise the value would reveal what the role cannot read.
            return self.expression_allowed(user, model, name, None if is_finite(field) else 'initial')
        paths = set(getattr(field.query, '__depends__', ()))
        paths |= references(plans(model)[name].get('compute'))
        if not paths: return True
        if (model, name) in visiting: return False
        visiting = visiting | {(model, name)}
        for path in paths:
            parts = path.replace('.*.', '.').split('.')
            target = model
            source = model.__sources__.get(parts[0]) if len(parts) == 2 else None
            if source is not None:
                if parts[1] not in source.fields: return False
                if isinstance(source.target, type) and (not self.allows(user, source.target, 'read') or parts[1] not in self.fields(user, source.target, _visiting=visiting)):
                    return False
                from .declarations import Ref
                for dependency in [value.path for value in source.filters.values() if isinstance(value, Ref)] + list(getattr(source.target, '__depends__', ())):
                    dep_parts = dependency.split('.')
                    owner = next((child.parent for child in self.registry.children if child.model is model), None) if dep_parts[0] == 'parent' else model
                    if owner is None or len(dep_parts) > (2 if dep_parts[0] == 'parent' else 1) or dep_parts[-1] not in self.fields(user, owner, _visiting=visiting): return False
                continue
            if len(parts) > 1:
                if parts[0] == 'parent':
                    target = next((child.parent for child in self.registry.children if child.model is model), None)
                else:
                    target = next((child.model for child in self.registry.children if child.parent is model and child.name == parts[0]), None)
                    if target is not None and self.rule(user, target).scope is not None:
                        return False
                if target is None: return False
            if not self.allows(user, target, 'read') or parts[-1] not in self.fields(user, target, _visiting=visiting):
                return False
        return True

    def expression_allowed(self, user, model, name, kind=None):
        """Whether this role may read every input of a field's finite calculation, initial or rule.

        Finite plans are acyclic and rules depend only on the role, so results are cached.
        """
        cache = self.__dict__.setdefault('_derived', {})
        key = (user.role, getattr(user, 'access', None), model, name, kind)
        if key not in cache:
            cache[key] = self.reads_allowed(user, self.registry.finite_reads(model, name, kind))
        return cache[key]

    def reads_allowed(self, user, reads):
        """Whether this role may read every (scope, model, field) input of a finite expression.

        Collections and Link lookups must be complete for the role: a row scope on them hides rows.
        """
        return all(
            self.allows(user, target, 'read')
            and not (scope in ('member', 'membership', 'lookup') and self.rule(user, target).scope is not None)
            and (read is None or read in self.presented(user, target) and self.derivation_allowed(user, target, read))
            for scope, target, read in reads)

    def scope_sql(self, user, model):
        rule = self.rule(user, model)
        filters = {} if rule.scope is None else rule.scope(user)
        if not isinstance(filters, dict):
            raise ValueError('Row scope must return a filter object')
        return SqlQuery(self.registry, model).where(filters)

    def record(self, tx, user, model, record_id, operation='read'):
        self.require(user, model, operation)
        where, values = self.scope_sql(user, model)
        table = identifier(self.registry.name_for(model))
        sql = f'SELECT 1 FROM {table} AS record{where} {"AND" if where else "WHERE"} record.id=?'
        if not tx.connection.execute(sql, [*values, record_id]).fetchone():
            raise AccessError(404, 'Record not found')
        return tx.get(model, record_id)

    def records(self, tx, user, model, record_ids, operation='read'):
        """Batched record(): {id: record} for the IDs this user may access, one statement per chunk.

        Same rules as record() (operation permission and row scope); IDs that fail are left out
        instead of raising, and an operation the role lacks returns nothing.
        """
        return self._scoped(tx, user, model, record_ids, operation, 'record.*')

    def visible(self, tx, user, model, record_ids, operation='read'):
        """The subset of record_ids that record() would accept; one statement per chunk."""
        return set(self._scoped(tx, user, model, record_ids, operation, 'record.id'))

    def _scoped(self, tx, user, model, record_ids, operation, columns):
        if not self.allows(user, model, operation):
            return {}
        ids = list(dict.fromkeys(record_ids))
        where, values = self.scope_sql(user, model)
        table = identifier(self.registry.name_for(model))
        found = {}
        for start in range(0, len(ids), CHUNK):
            part = ids[start:start + CHUNK]
            sql = f'SELECT {columns} FROM {table} AS record{where} {"AND" if where else "WHERE"} record.id IN ({", ".join("?" for _ in part)})'
            for row in tx.connection.execute(sql, [*values, *part]).fetchall():
                if columns == 'record.id': found[row[0]] = None
                else: found[row['id']] = model._from_row(row)
        return found

    def check_values(self, tx, user, model, values, *, targets=None):
        if set(values) - self.fields(user, model, write=True):
            raise AccessError(403, 'One or more fields cannot be written')
        for name, value in values.items():
            field = model.__declared_fields__[name]
            if isinstance(field, Link) and value is not None:
                try:
                    record_id = field.clean(value)
                except ValueError as exc:
                    raise ValidationError({name: str(exc)}) from exc
                # targets: a batched reader with the same checks as record() (documents.LinkTargets).
                targets.get(field.target, record_id) if targets is not None else self.record(tx, user, field.target, record_id)
            elif isinstance(field, Reference) and value is not None:
                # The referenced record must exist and be readable by this user.
                try:
                    target, record_id = field.clean(value)
                    target = self.registry.resolve(target)
                except ValueError as exc:
                    raise ValidationError({name: str(exc)}) from exc
                self.record(tx, user, target, record_id)

    def serialize(self, tx, user, record):
        return self.serialize_many(tx, user, [record])[0]

    def serialize_many(self, tx, user, records):
        """Readable fields of records of one model; Links and References the user cannot follow show as empty.

        Linked records are checked in batches (one statement per target model and chunk), not per row.
        """
        if not records:
            return []
        model = type(records[0])
        if any(type(record) is not model for record in records):
            raise ValueError('Serialize records of one model at a time')
        readable = self.fields(user, model)
        results = [{key: value for key, value in record.to_dict().items() if key == 'id' or key in readable} for record in records]
        for name, field in model.__fields__.items():
            if isinstance(field, Link):
                wanted = [result[name] for result in results if result.get(name) is not None]
                if not wanted:
                    continue
                shown = self.visible(tx, user, field.target, wanted)
                for result in results:
                    if result.get(name) is not None and result[name] not in shown:
                        result[name] = None
            elif isinstance(field, Reference):
                # A reference this user cannot follow is shown as empty, including the model it names.
                wanted = {}
                for result in results:
                    if result.get(name) is not None:
                        wanted.setdefault(result[name]['model'], []).append(result[name]['id'])
                shown = {target: self.visible(tx, user, self.registry.models[target], ids) if target in self.registry.models else set()
                         for target, ids in wanted.items()}
                for result in results:
                    if result.get(name) is not None and result[name]['id'] not in shown[result[name]['model']]:
                        result[name] = None
        return results

    def query_fields(self, user, model, filters, order_by):
        readable = {'id', *self.fields(user, model)}
        keys = list(filters or {}) + list(order_by or ())
        for key in keys:
            if not isinstance(key, str):
                raise AccessError(403, 'Query field is unavailable')
            parts = key.lstrip('-').split('__')
            if len(parts) > 1 and parts[-1] in OPERATORS:
                parts.pop()
            # Cross-model query paths could disclose restricted rows or fields.
            if len(parts) != 1 or parts[0] not in readable:
                raise AccessError(403, 'Query field is unavailable')
            field = model.__fields__.get(parts[0])
            if isinstance(field, Link) and self.rule(user, field.target).scope is not None:
                raise AccessError(403, 'Queries on this restricted relationship are unavailable')
            if isinstance(field, Reference):
                # Matching a reference could reveal one this role cannot follow. Equality needs a target
                # model readable without row scope; other filters need that for every model.
                value = (filters or {}).get(key) if key in (parts[0], parts[0] + '__eq') else None
                named = value.get('model') if isinstance(value, dict) else None
                targets = [self.registry.models[named]] if named in self.registry.models else self.registry.models.values()
                if any(not self.allows(user, target, 'read') or self.rule(user, target).scope is not None for target in targets):
                    raise AccessError(403, 'Queries on this restricted relationship are unavailable')
