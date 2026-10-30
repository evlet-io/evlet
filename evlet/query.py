"""Bounded, parameterized queries over registered fields and Link paths."""

from copy import copy

from .fields import Integer, Link, Text
from .models import ValidationError, identifier

OPERATORS = {'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'isnull', 'contains', 'icontains', 'startswith'}


class Query:
    def __init__(self, registry, model):
        self.registry = registry
        self.model = model
        self.alias = 0
        registry.name_for(model)

    def reference(self, path, model=None, alias='record'):
        model = self.model if model is None else model
        parts = path.split('__')
        if not 1 <= len(parts) <= 4:
            raise ValueError('Use at most three relationship hops')
        name = parts[0]
        field = Integer(minimum=1) if name == 'id' else model.__fields__.get(name)
        if field is None:
            raise ValueError(f'Unknown field: {name}')
        column = f'{identifier(alias)}.{identifier(name)}'
        if len(parts) == 1:
            return column, field
        if not isinstance(field, Link):
            raise ValueError(f'{name} is not a Link')
        self.alias += 1
        target_alias = f'link{self.alias}'
        target_column, target_field = self.reference('__'.join(parts[1:]), field.target, target_alias)
        table = identifier(self.registry.name_for(field.target))
        sql = f'(SELECT {target_column} FROM {table} AS {identifier(target_alias)} WHERE {identifier(target_alias)}."id" = {column})'
        return sql, target_field

    @staticmethod
    def value(field, value):
        # Query bounds need only type validation, not a model's business limits.
        field = copy(field)
        field.required = False
        field.validator = None
        if hasattr(field, 'minimum'):
            field.minimum = None
        if hasattr(field, 'maximum'):
            field.maximum = None
        return field.to_storage(field.clean(value))

    def where(self, filters, search="", *, search_fields=None):
        if filters is None:
            filters = {}
        if not isinstance(filters, dict) or len(filters) > 20:
            raise ValidationError({'filters': 'Use an object with at most 20 filters'})
        clauses, values = [], []
        for key, value in filters.items():
            try:
                if not isinstance(key, str):
                    raise ValueError('Filter names must be strings')
                parts = key.split('__')
                operator = parts.pop() if len(parts) > 1 and parts[-1] in OPERATORS else 'eq'
                column, field = self.reference('__'.join(parts))
                if operator == 'isnull':
                    if type(value) is not bool:
                        raise ValueError('isnull expects true or false')
                    clauses.append(f'{column} IS {"" if value else "NOT "}NULL')
                elif value is None and operator in ('eq', 'ne'):
                    clauses.append(f'{column} IS {"" if operator == "eq" else "NOT "}NULL')
                elif operator == 'in':
                    if not isinstance(value, (list, tuple)) or len(value) > 100:
                        raise ValueError('in expects at most 100 values')
                    if any(item is None for item in value):
                        raise ValueError('Use isnull to query null values')
                    clauses.append(f'{column} IN ({", ".join("?" for _ in value)})' if value else '1=0')
                    values.extend(self.value(field, item) for item in value)
                elif operator in ('contains', 'icontains', 'startswith'):
                    if not isinstance(field, Text) or not isinstance(value, str):
                        raise ValueError('Text matching requires a text field and string')
                    value.encode('utf-8')
                    if operator == 'startswith':
                        clauses.append(f'substr({column}, 1, length(?)) = ?')
                        values.extend([value, value])
                    else:
                        clauses.append(f'instr({column}, ?) > 0' if operator == 'contains' else f'instr(lower({column}), lower(?)) > 0')
                        values.append(value)
                else:
                    if value is None:
                        raise ValueError('Use isnull to query null values')
                    symbol = {'eq': '=', 'ne': '!=', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[operator]
                    clauses.append(f'{column} {symbol} ?')
                    values.append(self.value(field, value))
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValidationError({str(key): str(exc)}) from exc
        try:
            if not isinstance(search, str) or len(search) > 200:
                raise ValueError('Use search text of at most 200 characters')
            search.encode('utf-8')
            search = search.strip()
            if search:
                from .metadata import view_for
                matches = ['CAST("record"."id" AS TEXT) = ?']
                values.append(search.removeprefix('#'))
                for name in (view_for(self.model)['search_fields'] if search_fields is None else search_fields):
                    column, _ = self.reference(name)
                    matches.append(f'instr(lower({column}), lower(?)) > 0')
                    values.append(search)
                clauses.append('(' + ' OR '.join(matches) + ')')
        except (ValueError, UnicodeError) as exc:
            raise ValidationError({'q': str(exc)}) from exc
        return (' WHERE ' + ' AND '.join(clauses)) if clauses else '', values

    def ordering(self, order_by):
        if order_by is None:
            order_by = ('-id',)
        if isinstance(order_by, str):
            order_by = (order_by,)
        if not isinstance(order_by, (tuple, list)) or not 1 <= len(order_by) <= 4:
            raise ValidationError({'order_by': 'Use between one and four field names'})
        clauses = []
        try:
            for key in order_by:
                if not isinstance(key, str):
                    raise ValueError('Order fields must be strings')
                descending = key.startswith('-')
                column, _ = self.reference(key[1:] if descending else key)
                clauses.append(column + (' DESC NULLS LAST' if descending else ' ASC NULLS FIRST'))
            if not any(key.lstrip('-') == 'id' for key in order_by):
                clauses.append('"record"."id" ASC')
        except ValueError as exc:
            raise ValidationError({'order_by': str(exc)}) from exc
        return ' ORDER BY ' + ', '.join(clauses)
