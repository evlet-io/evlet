"""Backend model, field, and row rules. Python database sessions remain trusted."""

from dataclasses import dataclass

from .auth import AccessError, ROLES
from .fields import Link
from .models import ValidationError, identifier
from .query import Query, OPERATORS


@dataclass(frozen=True)
class Rule:
    operations: tuple = ('read',)
    read_fields: tuple | None = None
    write_fields: tuple | None = None
    scope: object = None


class Policy:
    def __init__(self, registry, *, rules=None):
        self.registry = registry
        self.rules = dict(rules or {})
        for (role, model), rule in self.rules.items():
            registry.name_for(model)
            if role not in ROLES or not isinstance(rule, Rule) or set(rule.operations) - {'read', 'create', 'update', 'delete'}:
                raise ValueError('Invalid permission rule')
            if rule.operations and 'read' not in rule.operations:
                raise ValueError('Write permissions require read access')
            for fields in (rule.read_fields, rule.write_fields):
                if fields is not None and (not isinstance(fields, tuple) or set(fields) - set(model.__fields__)):
                    raise ValueError('Permission fields must name model fields')
            if rule.scope is not None and not callable(rule.scope):
                raise ValueError('Row scope must be a callable returning query filters')

    def rule(self, user, model):
        return self.rules.get((user.role, model), Rule(('read',) if user.role == 'viewer' else ('read', 'create', 'update', 'delete')))

    def allows(self, user, model, operation):
        return operation in self.rule(user, model).operations

    def require(self, user, model, operation):
        if not self.allows(user, model, operation):
            raise AccessError(403, 'You do not have permission for this operation')

    def fields(self, user, model, *, write=False):
        rule = self.rule(user, model)
        readable = set(model.__fields__ if rule.read_fields is None else rule.read_fields)
        # Links to models that cannot be read must not expose their identifiers.
        readable = {name for name in readable if not isinstance(model.__fields__[name], Link) or self.allows(user, model.__fields__[name].target, 'read')}
        if not write:
            return readable
        writable = readable & set(model.__fields__ if rule.write_fields is None else rule.write_fields)
        return {name for name in writable if not model.__fields__[name].read_only}

    def scope_sql(self, user, model):
        rule = self.rule(user, model)
        filters = {} if rule.scope is None else rule.scope(user)
        if not isinstance(filters, dict):
            raise ValueError('Row scope must return a filter object')
        return Query(self.registry, model).where(filters)

    def record(self, tx, user, model, record_id, operation='read'):
        self.require(user, model, operation)
        where, values = self.scope_sql(user, model)
        table = identifier(self.registry.name_for(model))
        sql = f'SELECT 1 FROM {table} AS record{where} {"AND" if where else "WHERE"} record.id=?'
        if not tx.connection.execute(sql, [*values, record_id]).fetchone():
            raise AccessError(404, 'Record not found')
        return tx.get(model, record_id)

    def check_values(self, tx, user, model, values):
        if set(values) - self.fields(user, model, write=True):
            raise AccessError(403, 'One or more fields cannot be written')
        for name, value in values.items():
            field = model.__fields__[name]
            if isinstance(field, Link) and value is not None:
                try:
                    record_id = field.clean(value)
                except ValueError as exc:
                    raise ValidationError({name: str(exc)}) from exc
                self.record(tx, user, field.target, record_id)

    def serialize(self, tx, user, record):
        model = type(record)
        result = {key: value for key, value in record.to_dict().items() if key == 'id' or key in self.fields(user, model)}
        for name, field in model.__fields__.items():
            if isinstance(field, Link) and result.get(name) is not None:
                try:
                    self.record(tx, user, field.target, result[name])
                except AccessError:
                    result[name] = None
        return result

    def query_fields(self, user, model, filters, order_by):
        readable = {'id', *self.fields(user, model)}
        keys = list(filters or {}) + list(order_by or ())
        for key in keys:
            if not isinstance(key, str):
                raise AccessError(403, 'Query field is unavailable')
            parts = key.lstrip('-').split('__')
            if parts[-1] in OPERATORS:
                parts.pop()
            # Cross-model query paths could disclose restricted rows or fields.
            if len(parts) != 1 or parts[0] not in readable:
                raise AccessError(403, 'Query field is unavailable')
            field = model.__fields__.get(parts[0])
            if isinstance(field, Link) and self.rule(user, field.target).scope is not None:
                raise AccessError(403, 'Queries on this restricted relationship are unavailable')
