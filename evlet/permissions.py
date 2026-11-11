"""Backend model, field, and row rules. Python database sessions remain trusted."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType, SimpleNamespace

from .auth import AccessError, PORTAL, PUBLIC, ROLES, is_administrator
from .identity import RecordId
from .naming import title_sources
from .fields import Link, Reference
from .models import Model, ValidationError, identifier, refuse_id
from .query import Condition, SqlQuery, child_tables, conditions


# IDs per batched permission or row statement; well under SQLite's bound-parameter limit.
CHUNK = 500
# Engine corrections: admin has them by default and a Rule may grant them to other roles too.
CORRECTIONS = ('void', 'revise', 'unpost', 'repost', 'confirm', 'cancel', 'reopen')
# Operations that change a record or what is kept with it; Meta.api = "read" refuses them to callers.
WRITES = frozenset(('create', 'update', 'delete', 'journal', *CORRECTIONS))
# Meta.api values: what callers (HTTP routes, MCP, Eva, connectors) reach of a model.
API = ('full', 'read', None)
# True while a request is served. The app code it runs (actions, moves, reactions, services) is trusted again.
CALLER = ContextVar('evlet_caller', default=False)


@contextmanager
def calling(value=True):
    """Serve a request as a caller (value=True), or run trusted app code inside one (value=False)."""
    token = CALLER.set(value)
    try:
        yield
    finally:
        CALLER.reset(token)


def trusted():
    """Run app code a request calls, such as an action handler, as trusted Python: Meta.api does not limit it."""
    return calling(False)


def surface(registry, model, operation):
    """The one rule over what may be done to a model before any role is asked, so no Rule or Controls
    override can grant it back. Effect rows are written only by the engine, so nobody changes them. While a
    caller is served, Meta.api = "read" refuses every write and Meta.api = None refuses everything."""
    if operation in ('create', 'update', 'delete') and model in getattr(registry, 'effect_targets', ()):
        return False
    if not CALLER.get():
        return True
    api = getattr(registry, 'api', {}).get(model, 'full')
    return api == 'full' or api == 'read' and operation not in WRITES


def read_only(model):
    """The refusal for a write route of a Meta.api = "read" model."""
    return f'{model.__name__} records are read only here. The application changes them.'


def compile_api(registry):
    """{model: Meta.api} for every registered model; owned rows follow their document. A Link from a model
    callers reach to an api=None model must be hidden, and so must an effect target's source when the
    posting document is api=None, so ids and titles of such records never reach a caller."""
    result = {}
    for model in registry.models.values():
        root = registry.root_of(model)
        # A ledger model has no public surface (evlet/ledger.py); its class refuses any other value.
        value = None if getattr(root, '__ledger__', False) else getattr(root.Meta, 'api', 'full')
        if value not in API:
            raise ValueError(f'{root.__name__}: Meta.api is "full", "read" or None')
        result[model] = value
    for model, value in result.items():
        if value is None:
            continue
        for name, field in model.__declared_fields__.items():
            # A Link to a ledger row shows only its integer id, read only, so it need not be hidden.
            if isinstance(field, Link) and result.get(field.target) is None and not field.hidden and not field.ledger_target:
                raise ValueError(f'{model.__name__}.{name} links to {field.target.__name__}, which has Meta.api = None. '
                                 'Declare the Link hidden=True so its ids and titles stay private')
    for source, bounds in getattr(registry, 'effects', {}).items():
        for bound in bounds:
            field = bound.target.__declared_fields__.get('source')
            if result.get(source) is None and result.get(bound.target) is not None and field is not None and not field.hidden:
                raise ValueError(f'{bound.target.__name__}.source names {source.__name__} records, which has Meta.api = None. '
                                 'Declare it source = Reference(hidden=True)')
    return MappingProxyType(result)


def corrects(model, operation):
    """Whether a model supports a correction: void needs voidable states, revise also Meta.revisable, and
    unpost and repost need effects (Meta.effects), each with a `reverse`, so cancelling them is possible."""
    if operation in ('confirm', 'cancel', 'reopen'):
        return getattr(model, 'confirmable', False)
    state = getattr(model, '__state__', None)
    if operation in ('unpost', 'repost'):
        declared = getattr(getattr(model, 'Meta', None), 'effects', ()) or ()
        return state is not None and bool(declared) and all(getattr(effect, 'reverse', None) is not None for effect in declared)
    voidable = state is not None and bool(model.__declared_fields__[state].voidable)
    return voidable and (operation == 'void' or getattr(model, '__revisable__', False))


@dataclass(frozen=True)
class Rule:
    operations: tuple = ('read',)
    read_fields: tuple | None = None
    write_fields: tuple | None = None
    scope: object = None


def own_customer(field='customer', model='customer'):
    """Row scope for portal users: rows whose field holds a record of model the user is linked to.

    Links come from LocalAuth.link_user. Use field='id' on the linked model itself.
    """
    def scope(user):
        return {field + '__in': [record_id for name, record_id in user.links if name == model]}
    return scope


class Hierarchy:
    """A tree of records of one model through a Link to the same model, such as employees by reports_to.

    team() is a row scope over the tree below the user's linked records of the model (LocalAuth.link_user),
    resolved inside SQL with a recursive query, so a team has no size limit. Declare the parent Link with
    index=True so each level is an index lookup.
    """

    def __init__(self, model, *, parent):
        if not isinstance(parent, str) or not parent:
            raise ValueError('Hierarchy needs the name of its parent Link field')
        self.model, self.parent = model, parent

    def team(self, field='id', *, include_self=True):
        """Row scope: rows whose field names the user's linked record or any record below it.

        field is 'id' on the hierarchy model itself, or a Link (path) to it such as 'employee'.
        include_self=False leaves out the user's own record and keeps only the records below it.
        """
        def scope(user):
            return Team(self, field, include_self, tuple(user.links))
        scope.teams = ((self, field),)
        return scope


@dataclass(frozen=True)
class Team:
    """What a Hierarchy.team scope returns for one user; compiled by scope_condition."""
    hierarchy: Hierarchy
    field: str
    include_self: bool
    links: tuple


@dataclass(frozen=True)
class AnyOf:
    """What an any_of scope returns for one user: rows that meet any of the conditions."""
    conditions: tuple


def any_of(*scopes):
    """Row scope that allows a row when any of scopes allows it, such as own records or a team's.

    Each scope is a row scope callable or a filter object; an empty filter object allows every row.
    """
    if not scopes or any(not callable(scope) and not isinstance(scope, dict) for scope in scopes):
        raise ValueError('any_of takes row scopes or filter objects')
    def scope(user):
        return AnyOf(tuple(item(user) if callable(item) else item for item in scopes))
    scope.teams = tuple(team for item in scopes for team in getattr(item, 'teams', ()))
    return scope


def team_target(registry, model, team):
    """The hierarchy model of team, after checking its parent Link and that field leads to it from model."""
    hierarchy, field = team
    target = registry.resolve(hierarchy.model)
    parent = target.__fields__.get(hierarchy.parent)
    if not isinstance(parent, Link) or registry.resolve(parent.target) is not target:
        raise ValueError(f'Hierarchy {registry.name_for(target)}: {hierarchy.parent} must be a Link to the same model')
    parts = field.split('__') if isinstance(field, str) and field else []
    owner = model
    for index, name in enumerate(parts):
        last = index == len(parts) - 1
        if name == 'id' and last:
            if owner is target:
                return target
            break
        found = owner.__fields__.get(name)
        if not isinstance(found, Link):
            break
        owner = registry.resolve(found.target)
        if last and owner is target:
            return target
    raise ValueError(f'{model.__name__}: team field {field!r} must be id on {target.__name__} or a Link to it')


def scope_condition(registry, model, condition):
    """SQL for what a row scope returned, over the alias "record": (clause, values), or (None, []) for every row.

    A filter object combines its filters with AND, AnyOf its conditions with OR, and Team selects the
    hierarchy with a recursive query from the linked records, which UNION stops at a cycle.
    """
    if isinstance(condition, dict):
        where, values = SqlQuery(registry, model).where(condition)
        return (f'({where.removeprefix(" WHERE ")})' if where else None), values
    if isinstance(condition, AnyOf):
        clauses, values = [], []
        for item in condition.conditions:
            clause, more = scope_condition(registry, model, item)
            if clause is None:
                return None, []
            clauses.append(clause)
            values += more
        return ('(' + ' OR '.join(clauses) + ')') if clauses else '1=0', values
    if isinstance(condition, Team):
        target = team_target(registry, model, (condition.hierarchy, condition.field))
        name = registry.name_for(target)
        roots = sorted({record_id for linked, record_id in condition.links if linked == name})
        if not roots:
            return '1=0', []
        column, _ = SqlQuery(registry, model).reference(condition.field)
        table, parent = identifier(name), identifier(condition.hierarchy.parent)
        marks = ', '.join('?' for _ in roots)
        start = f'SELECT id FROM {table} WHERE id IN ({marks})' if condition.include_self else f'SELECT id FROM {table} WHERE {parent} IN ({marks})'
        # The range join cannot be hashed, so PostgreSQL walks the parent index level by level instead of
        # scanning the table at each level (it guesses ten rows per level); SQLite plans both forms alike.
        return (f'{column} IN (WITH RECURSIVE evlet_team(id) AS ({start} UNION SELECT below.id FROM evlet_team '
                f'JOIN {table} AS below ON below.{parent} >= evlet_team.id AND below.{parent} <= evlet_team.id) '
                f'SELECT id FROM evlet_team)'), roots
    raise ValueError('Row scope must return a filter object')


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


def _is_id(value):
    try:
        RecordId(value)
    except ValueError:
        return False
    return True


class Policy:
    def __init__(self, registry, *, rules=None, roles=None, overrides=None, read_only=None):
        """rules are the app files' rules; overrides (Controls) replace a role's rule on a model.

        roles maps each role to its base framework role; a custom role without its own rule on a
        model inherits the base role's rule from the files. read_only ({model: names}) are fields no
        role may write, from Controls; a naming field is always among them, because the server fills it.
        """
        self.registry = registry
        # The field an older Naming("field", ...) fills stays read only; the standard name is writable once
        # assigned, which the document pipeline checks (evlet/naming.py).
        locked = {model: {rules.field} for model, rules in getattr(registry, 'naming', {}).items() if rules.field != 'name'}
        for model, names in (read_only or {}).items():
            locked.setdefault(model, set()).update(names)
        self.read_only = MappingProxyType({model: frozenset(names) for model, names in locked.items()})
        self.roles = MappingProxyType({**{role: role for role in (*ROLES, PORTAL)}, **dict(roles or {})})
        if any(self.roles.get(role) != role for role in (*ROLES, PORTAL)) or set(self.roles.values()) - {*ROLES, PORTAL} or PUBLIC in self.roles \
                or 'admin' in [base for role, base in self.roles.items() if role != 'admin']:
            raise ValueError('Roles must include the framework roles, and custom roles must be based on editor, viewer or portal')
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
                elif self.roles.get(role, role) != role and role not in getattr(base, 'permissions', {}):
                    self.rules[role, model] = default(self.roles[role], model)
                else:
                    self.rules[role, model] = getattr(base, 'permissions', {}).get(role, Rule(()))
            return self.rules[role, model]
        self.governed = frozenset(model for model in registry.models.values() if governed(model))
        for model in self.governed:
            for role in (*self.roles, PUBLIC):
                default(role, model)
        for model in registry.models.values():
            for role, base in self.roles.items():
                if role != base and (role, model) not in self.rules and (base, model) in self.rules:
                    self.rules[role, model] = self.rules[base, model]
        # Instance fields (x_, added in Controls) follow the record for staff: a desk role that reads it reads
        # them and one that creates or updates it writes them, also when the app lists the fields a role may read
        # or write. Portal and public roles get them only through a Controls override. Overrides narrow them per
        # role like any other field.
        for (role, model), rule in list(self.rules.items()):
            added = tuple(name for name, field in model.__declared_fields__.items() if getattr(field, 'instance', None))
            if not added or 'read' not in rule.operations or self.roles.get(role) not in ROLES:
                continue
            read, write = rule.read_fields, rule.write_fields
            if read is not None:
                read += tuple(name for name in added if name not in read)
            if write is not None and {'create', 'update'} & set(rule.operations):
                write += tuple(name for name in added if name not in write)
            if (read, write) != (rule.read_fields, rule.write_fields):
                self.rules[role, model] = Rule(rule.operations, read, write, rule.scope)
        self.file_rules = MappingProxyType(dict(self.rules))
        for (role, model), rule in dict(overrides or {}).items():
            if role == 'admin':
                raise ValueError('Admin rules cannot be overridden')
            self.rules[role, model] = rule
        # Rules are fixed per policy, so cached finite derivation checks cannot go stale.
        self.rules = MappingProxyType(self.rules)
        for (role, model), rule in self.rules.items():
            registry.name_for(model)
            if role not in (*self.roles, PUBLIC) or not isinstance(rule, Rule) or set(rule.operations) - {'read', 'create', 'update', 'delete', 'journal', 'print', *CORRECTIONS}:
                raise ValueError('Invalid permission rule')
            if 'journal' in rule.operations and model not in registry.journals:
                raise ValueError(f'{model.__name__}: Rule names \'journal\', but the model has no journal '
                                 '(declare Meta.journal = Journal() or a State)')
            if 'revise' in rule.operations and 'create' not in rule.operations:
                raise ValueError(f'{model.__name__}: Rule names \'revise\' without \'create\'; a revise creates a draft copy')
            if 'repost' in rule.operations and 'update' not in rule.operations:
                raise ValueError(f'{model.__name__}: Rule names \'repost\' without \'update\'; a repost runs the posting move')
            for operation in CORRECTIONS:
                if operation in rule.operations and not corrects(model, operation):
                    raise ValueError(f'{model.__name__}: Rule names {operation!r}, but the model cannot be '
                                     + {'void': 'voided (declare voidable states on its State)', 'revise': 'revised (set Meta.revisable = True)'}.get(
                                         operation, f'{operation}ed (declare Meta.effects, each with reverse=)'))
            if rule.operations and 'read' not in rule.operations:
                raise ValueError('Write permissions require read access')
            for fields in (rule.read_fields, rule.write_fields):
                if fields is not None and (not isinstance(fields, tuple) or set(fields) - set(model.__declared_fields__)):
                    raise ValueError('Permission fields must name model fields')
            if rule.scope is not None and not callable(rule.scope):
                raise ValueError('Row scope must be a callable returning query filters')
            for team in getattr(rule.scope, 'teams', ()):
                team_target(registry, model, team)
        # Void, revise, unpost and repost: admin, when its Rule allows reading and updating the model (and
        # creating, for revise), plus every role whose Rule names the operation. The rules table is not changed.
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
        # Journal entries (notes, messages, calls, follow ups): admin when its Rule reads the model, the
        # editor and roles based on it when their Rule reads and updates it, and every role whose Rule
        # names 'journal'. Naming a role only adds it, like void and revise.
        journalers = {}
        for model in registry.journals:
            operations = {role: self.rules.get((role, model), default_rule(base)).operations for role, base in self.roles.items()}
            named = {role for role in self.roles if 'journal' in operations[role]}
            if 'read' in operations['admin']:
                named.add('admin')
            named |= {role for role, base in self.roles.items() if base == 'editor' and {'read', 'update'} <= set(operations[role])}
            journalers[model] = frozenset(named)
        self.journalers = MappingProxyType(journalers)
        # Printing is its own operation: admin when its Rule reads the model, and every role whose Rule names
        # 'print'. Read access is still required when a record prints.
        printers = {}
        for model in registry.models.values():
            named = {role for role in self.roles if 'print' in self.rules.get((role, model), Rule(())).operations}
            if 'read' in self.rules.get(('admin', model), default_rule('admin')).operations:
                named.add('admin')
            printers[model] = frozenset(named)
        self.printers = MappingProxyType(printers)

    def administrator_rule(self, model):
        """Every operation the model supports, on every row and field: the built-in Administrator's rule.
        Validation, read-only fields, state rules, period locks and foreign keys still apply."""
        cache = self.__dict__.setdefault('_administrator', {})
        if model not in cache:
            operations = ['read', 'create', 'update', 'delete', 'print']
            if model in self.registry.journals:
                operations.append('journal')
            operations += [operation for operation in CORRECTIONS if corrects(model, operation)]
            cache[model] = Rule(tuple(operations))
        return cache[model]

    def rule(self, user, model):
        found = self.administrator_rule(model) if is_administrator(user) else self.rules.get((user.role, model))
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
        if not surface(self.registry, model, operation):
            return False
        if is_administrator(user):
            return operation in self.rule(user, model).operations
        if operation in CORRECTIONS:
            access = getattr(user, 'access', None)
            if access is not None and (not access.write or access.models is not None and model not in access.models):
                return False
            return user.role in self.corrections.get((model, operation), ())
        if operation == 'journal':
            return user.role in self.journalers.get(model, ())
        if operation == 'print':
            return user.role in self.printers.get(model, ()) and 'read' in self.rule(user, model).operations
        return operation in self.rule(user, model).operations

    def require(self, user, model, operation):
        if not self.allows(user, model, operation):
            raise AccessError(403, 'You do not have permission for this operation')

    def presented(self, user, model):
        """Fields this role may see before derivation checks."""
        rule = self.rule(user, model)
        readable = set(model.__declared_fields__ if rule.read_fields is None else rule.read_fields)
        # Links to models that cannot be read must not expose their identifiers.
        readable = {name for name in readable if not isinstance(model.__declared_fields__[name], Link)
                    or model.__declared_fields__[name].ledger_target or self.allows(user, model.__declared_fields__[name].target, 'read')}
        if model in self.governed:
            readable = {name for name in readable if not model.__declared_fields__[name].hidden}
        return readable

    def field_readable(self, user, model, name, visiting=frozenset()):
        """Whether one field is in fields(user, model), without working out the rest of the model's fields."""
        if name not in self.presented(user, model):
            return False
        if getattr(model, '__declaration_mode__', False) and not self.derivation_allowed(user, model, name, visiting):
            return False
        if name == 'title':
            return all(source == 'title' or self.field_readable(user, model, source, visiting) for source in title_sources(model))
        return True

    def fields(self, user, model, *, write=False):
        rule = self.rule(user, model)
        readable = self.presented(user, model)
        if getattr(model, '__declaration_mode__', False):
            readable = {name for name in readable if self.derivation_allowed(user, model, name)}
        # The standard title repeats its source fields, so it is readable only with them (evlet/naming.py).
        if 'title' in readable and not title_sources(model) <= readable:
            readable = readable - {'title'}
        if not write:
            return readable
        writable = readable & set(model.__declared_fields__ if rule.write_fields is None else rule.write_fields)
        locked = self.read_only.get(model, ())
        # Fields derive() sets (Meta.derives) have one writer, like calculated ones.
        derived = getattr(model, '__derives__', ())
        return {name for name in writable if not model.__declared_fields__[name].read_only and not name.startswith('evlet_') and name not in locked
                and name not in derived
                and not getattr(model.__declared_fields__[name], 'ledger_target', False)}

    def derivation_allowed(self, user, model, name, visiting=frozenset()):
        """Whether this role may read a derived field: every path its formula or query reads is readable.

        The answer depends only on the role (and API key), the registry and these fixed rules, so it is
        memoised per policy (Controls builds a new policy when permissions change). Each formula is walked once
        per role; formulas that read other formulas reuse the stored answers instead of walking every path
        again, which grew exponentially with chained formulas.
        """
        cache = self.__dict__.setdefault('_derivations', {})
        key = (user.role, is_administrator(user), getattr(user, 'access', None), model, name)
        if key in cache:
            return cache[key]
        if (model, name) in visiting:
            return False  # A cycle is never readable; registration rejects value cycles anyway.
        result = self._derivation_allowed(user, model, name, visiting | {(model, name)})
        cache[key] = result
        return result

    def _derivation_allowed(self, user, model, name, visiting):
        from .reactivity import plans, references
        from .calc import is_finite
        # Counted so a test can check that each formula is walked once per role (test_formula_and_record_values.py).
        self.__dict__['_derivation_walks'] = self.__dict__.get('_derivation_walks', 0) + 1
        field = model.__declared_fields__[name]
        if is_finite(field) or getattr(field, 'initial', None) is not None:
            # A finite result (or initial suggestion) is readable only when every input and whole collection
            # it reads is, and every field on a Link path is readable on every record (no row scope on a
            # looked-up model). Otherwise the value would reveal what the role cannot read.
            return self.expression_allowed(user, model, name, None if is_finite(field) else 'initial')
        paths = set(getattr(field.query, '__depends__', ()))
        paths |= references(plans(model)[name].get('compute'))
        if not paths: return True
        for path in paths:
            parts = path.replace('.*.', '.').split('.')
            target = model
            source = model.__sources__.get(parts[0]) if len(parts) == 2 else None
            if source is not None:
                if parts[1] not in source.fields: return False
                if isinstance(source.target, type) and (not self.allows(user, source.target, 'read') or not self.field_readable(user, source.target, parts[1], visiting)):
                    return False
                from .declarations import Ref
                for dependency in [value.path for value in source.filters.values() if isinstance(value, Ref)] + list(getattr(source.target, '__depends__', ())):
                    dep_parts = dependency.split('.')
                    owner = next((child.parent for child in self.registry.children if child.model is model), None) if dep_parts[0] == 'parent' else model
                    if owner is None or len(dep_parts) > (2 if dep_parts[0] == 'parent' else 1) or not self.field_readable(user, owner, dep_parts[-1], visiting): return False
                continue
            if len(parts) > 1:
                if parts[0] == 'parent':
                    target = next((child.parent for child in self.registry.children if child.model is model), None)
                else:
                    target = next((child.model for child in self.registry.children if child.parent is model and child.name == parts[0]), None)
                    if target is not None and self.rule(user, target).scope is not None:
                        return False
                if target is None: return False
            if not self.allows(user, target, 'read') or not self.field_readable(user, target, parts[-1], visiting):
                return False
        return True

    def expression_allowed(self, user, model, name, kind=None):
        """Whether this role may read every input of a field's finite calculation, initial or rule.

        Finite plans are acyclic and rules depend only on the role, so results are cached.
        """
        cache = self.__dict__.setdefault('_derived', {})
        key = (user.role, is_administrator(user), getattr(user, 'access', None), model, name, kind)
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
            and (read is None or read in self.presented(user, target) and self.derivation_allowed(user, target, read)
                 or self.intermediate(user, target, read))
            for scope, target, read in reads)

    def intermediate(self, user, model, name):
        """Whether a field is a hidden intermediate for this role: hidden=True, unstored and finite-calculated,
        on a governed model, in the role's read fields, and every input it reads readable. It stays out of
        records, lists and the schema, but formulas that read it are readable, and the browser computes it
        from the shipped plan (its formula reads only what the role can read already)."""
        from .calc import is_finite
        field = model.__declared_fields__.get(name)
        if field is None or not field.hidden or field.stored or not is_finite(field) or model not in self.governed:
            return False
        rule = self.rule(user, model)
        if rule.read_fields is not None and name not in rule.read_fields:
            return False
        return self.expression_allowed(user, model, name)

    def scope_sql(self, user, model):
        rule = self.rule(user, model)
        if rule.scope is None:
            return '', []
        clause, values = scope_condition(self.registry, model, rule.scope(user))
        return (' WHERE ' + clause if clause else ''), values

    def record(self, tx, user, model, record_id, operation='read'):
        self.require(user, model, operation)
        where, values = self.scope_sql(user, model)
        table = identifier(self.registry.name_for(model))
        sql = f'SELECT 1 FROM {table} AS record{where} {"AND" if where else "WHERE"} record.id=?'
        try:
            record_id = RecordId(record_id)
        except ValueError:
            raise AccessError(404, 'Record not found') from None
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
        ids = list(dict.fromkeys(RecordId(record_id) for record_id in record_ids if _is_id(record_id)))
        where, values = self.scope_sql(user, model)
        table = identifier(self.registry.name_for(model))
        found = {}
        for start in range(0, len(ids), CHUNK):
            part = ids[start:start + CHUNK]
            sql = f'SELECT {columns} FROM {table} AS record{where} {"AND" if where else "WHERE"} record.id IN ({", ".join("?" for _ in part)})'
            for row in tx.connection.execute(sql, [*values, *part]).fetchall():
                if columns == 'record.id': found[RecordId(row[0])] = None
                else: found[RecordId(row['id'])] = model._from_row(row)
        return found

    def check_values(self, tx, user, model, values, *, targets=None):
        refuse_id(values)
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
            if isinstance(field, Link) and field.ledger_target:
                continue  # A ledger row id is shown as it is: there is no record to read behind it.
            if isinstance(field, Link):
                wanted = [result[name] for result in results if result.get(name) is not None]
                if not wanted:
                    continue
                shown = self.visible(tx, user, field.target, wanted)
                for result in results:
                    if result.get(name) is not None and RecordId(result[name]) not in shown:
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
                    if result.get(name) is not None and RecordId(result[name]['id']) not in shown[result[name]['model']]:
                        result[name] = None
        return results

    def _readable_ids(self, database, user, target, operator, value):
        """Whether an eq or in filter on a row-scoped Link names only records this user can read. `database` is a
        Database, or an open connection to read with (a route already inside its transaction)."""
        if database is None or operator not in ('eq', 'in'):
            return False
        ids = value if operator == 'in' else [value]
        if not isinstance(ids, list) or not ids or len(ids) > 100:
            return False
        try:
            ids = [RecordId(item) for item in ids]
        except ValueError:
            return False
        if not hasattr(database, '_connection'):
            return set(ids) <= self.visible(SimpleNamespace(connection=database), user, target, ids)
        with database._connection() as connection:
            return set(ids) <= self.visible(SimpleNamespace(connection=connection), user, target, ids)

    def query_fields(self, user, model, filters, order_by, *, database=None, children=()):
        """Refuse query fields that could disclose rows or fields: the same 403 for unknown and unreadable
        fields and child tables, so a filter never answers about what the role cannot read. With database,
        eq and in filters on a row-scoped Link are allowed when every id is a record the user can read,
        such as a portal user's customer (database may also be an open connection). children: ChildTables beyond the
        registry's Table declarations."""
        items = conditions(filters)
        readable = {'id', *self.fields(user, model)}
        for key in order_by or ():
            if not isinstance(key, str):
                raise AccessError(403, 'Query field is unavailable')
            self._query_field(user, model, readable, key.lstrip('-'), None, None, database)
        tables = None
        for item in items:
            if isinstance(item, Condition):
                self._query_field(user, model, readable, item.path, item.operator, item.value, database)
                continue
            tables = child_tables(self.registry, children) if tables is None else tables
            child = tables.get((model, item.child))
            # A child table's rows are read through their parent; one with its own row scope is not queried.
            if child is None or not self.allows(user, child.model, 'read') or self.rule(user, child.model).scope is not None:
                raise AccessError(403, 'Query field is unavailable')
            child_readable = {'id', *self.fields(user, child.model)}
            for inner in item.where:
                self._query_field(user, child.model, child_readable, inner.path, inner.operator, inner.value, database)

    def _query_field(self, user, model, readable, path, operator, value, database):
        # Cross-model query paths could disclose restricted rows or fields.
        if '__' in path or path not in readable:
            raise AccessError(403, 'Query field is unavailable')
        field = model.__fields__.get(path)
        if isinstance(field, Link) and self.rule(user, field.target).scope is not None:
            if operator is None or not self._readable_ids(database, user, field.target, operator, value):
                raise AccessError(403, 'Queries on this restricted relationship are unavailable')
        if isinstance(field, Reference):
            # Matching a reference could reveal one this role cannot follow. Equality needs a target
            # model readable without row scope; other filters need that for every model.
            named = value.get('model') if operator == 'eq' and isinstance(value, dict) else None
            targets = [self.registry.models[named]] if named in self.registry.models else self.registry.models.values()
            if any(not self.allows(user, target, 'read') or self.rule(user, target).scope is not None for target in targets):
                raise AccessError(403, 'Queries on this restricted relationship are unavailable')
