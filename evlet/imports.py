"""CSV import: create records of a model with Meta.importable = True from a CSV, through the same create as
POST /api/<model>, so every permission, field write rule, validation and Controls rule applies.

    GET  /api/<model>/import          the columns a CSV can fill and the person's saved mapping
    POST /api/<model>/import/check    a dry run: every record is created and checked, then rolled back
    POST /api/<model>/import          create every record in one transaction (all or nothing by default)

A CSV column maps to a field ("customer") or a Table row field ("children.lines.item"). With a key column, rows
with the same key are one record and each row adds its Table rows; a blank key continues the record above.
See docs/model-engine.md#csv-import.
"""

import csv
import io
import json
import re
from datetime import date

from . import qb
from .auth import AccessError, allow
from .database import ConflictError, NotFoundError
from .fields import Link
from .identity import RecordId, jsonable
from .models import ValidationError, checked_body

# The CSV text a request may carry (characters), its rows and the records it may create.
MAX_TEXT = 2_000_000
MAX_ROWS = 5000
MAX_RECORDS = 1000
MAX_COLUMNS = 200
# Field kinds a CSV cell cannot fill.
SKIPPED = ('sequence', 'state', 'image', 'reference', 'richtext')
ARGUMENTS = {'csv', 'mapping', 'key', 'save_mapping', 'all_or_nothing'}
TRUE, FALSE = ('true', 'yes', 'y', '1'), ('false', 'no', 'n', '0')


def importable(business, model):
    """Whether a model takes CSV imports: Meta.importable = True on a model callers fully reach."""
    meta = getattr(model, 'Meta', None)
    value = getattr(meta, 'importable', False) if meta is not None else False
    if type(value) is not bool:
        raise ValueError(f'{model.__name__}: Meta.importable is True or False')
    return value and business.registry.api.get(model, 'full') == 'full' and not getattr(model, '__ledger__', False) \
        and model not in business.child_models


def fillable(business, user, model):
    """{path: (field, table or None)} of what a CSV column can fill for this person."""
    policy = business.policy
    result = {}
    for name in sorted(policy.fields(user, model, write=True), key=list(model.__declared_fields__).index):
        field = model.__declared_fields__[name]
        if field.kind not in SKIPPED and field.stored and not field.hidden:
            result[name] = (field, None)
    for child in business.children.values():
        if child.parent is not model or not policy.allows(user, child.model, 'create'):
            continue
        writable = policy.fields(user, child.model, write=True)
        for name, field in child.model.__declared_fields__.items():
            if name in writable and name != child.link and field.kind not in SKIPPED and field.stored and not field.hidden:
                result[f'children.{child.name}.{name}'] = (field, child.name)
    return result


def describe(business, user, model, connection):
    resource = business.registry.name_for(model)
    columns = []
    for path, (field, table) in fillable(business, user, model).items():
        entry = {'path': path, 'label': field.label or field.name.replace('_', ' ').capitalize(), 'type': field.kind, 'required': bool(field.required)}
        if table is not None:
            entry['table'] = table
        if isinstance(field, Link):
            entry['target'] = business.registry.name_for(business.registry.resolve(field.target))
        if field.kind in ('select', 'multiselect'):
            entry['options'] = list(getattr(field, 'choices', ()))
        columns.append(entry)
    saved = None
    if connection.table_exists('evlet_user_prefs'):
        row = connection.run(qb.select('evlet_user_prefs', 'value').where(user_id=user.id, scope='import', key=resource)).one()
        saved = None if row is None else json.loads(row['value'])
    return {'model': resource, 'label': getattr(getattr(model, 'Meta', None), 'plural', None) or model.__name__, 'columns': columns,
            'saved': saved, 'limits': {'characters': MAX_TEXT, 'rows': MAX_ROWS, 'records': MAX_RECORDS}}


def route(business, scope, body, user, model, *, dry_run):
    if not importable(business, model):
        raise AccessError(404, 'This model does not take imports')
    business.policy.require(user, model, 'create')
    if scope['method'] == 'GET' and not dry_run:
        with business.database.transaction() as tx:
            return 200, describe(business, user, model, tx.connection), []
    allow(scope['method'], 'POST')
    checked_body(body, ARGUMENTS, 'Send csv, mapping and optionally key, save_mapping and all_or_nothing')
    plan = Plan(business, user, model, body)
    if body.get('save_mapping') is True:
        save_mapping(business, user, model, plan)
    return plan.run(dry_run=dry_run, all_or_nothing=body.get('all_or_nothing', True))


def save_mapping(business, user, model, plan):
    """Keep the mapping for this person and model, in its own transaction so a dry run keeps it too."""
    with business.database.transaction() as tx:
        if not tx.connection.table_exists('evlet_user_prefs'):
            return
        value = json.dumps({'mapping': plan.mapping, 'key': plan.key})
        tx.connection.run(qb.insert('evlet_user_prefs', user_id=user.id, scope='import', key=business.registry.name_for(model), value=value)
                          .on_conflict('user_id', 'scope', 'key').do_update(value=qb.col('excluded.value')))


class Rollback(Exception):
    """Ends a dry run: the transaction rolls back and the answer it carries is returned."""
    def __init__(self, answer):
        super().__init__('dry run')
        self.answer = answer


class Plan:
    """A checked CSV and mapping: its header, rows and the records they group into."""

    def __init__(self, business, user, model, body):
        self.business, self.user, self.model = business, user, model
        text = body.get('csv')
        if not isinstance(text, str) or not text.strip():
            raise ValidationError({'csv': 'Send the CSV file as text'})
        if len(text) > MAX_TEXT:
            raise ValidationError({'csv': f'Use a CSV of at most {MAX_TEXT // 1_000_000} MB'})
        try:
            rows = list(csv.reader(io.StringIO(text.lstrip('﻿'), newline='')))
        except csv.Error as exc:
            raise ValidationError({'csv': f'The CSV could not be read: {exc}'}) from None
        rows = [row for row in rows if any(cell.strip() for cell in row)]
        if not rows:
            raise ValidationError({'csv': 'The CSV is empty'})
        self.header, self.rows = [cell.strip() for cell in rows[0]], rows[1:]
        if len(self.header) > MAX_COLUMNS or len(set(self.header)) != len(self.header) or '' in self.header:
            raise ValidationError({'csv': f'The first row names each column once, at most {MAX_COLUMNS} columns'})
        if not self.rows:
            raise ValidationError({'csv': 'The CSV has a header and no rows'})
        if len(self.rows) > MAX_ROWS:
            raise ValidationError({'csv': f'Use at most {MAX_ROWS} rows; split the file'})
        self.fillable = fillable(business, user, model)
        mapping = body.get('mapping')
        if not isinstance(mapping, dict) or not all(isinstance(key, str) and (value is None or isinstance(value, str)) for key, value in mapping.items()):
            raise ValidationError({'mapping': 'Map CSV columns to fields: {"Customer": "customer", "Item": "children.lines.item"}'})
        self.mapping = {column: path for column, path in mapping.items() if path}
        unknown = sorted(set(self.mapping) - set(self.header))
        if unknown:
            raise ValidationError({'mapping': f'The CSV has no column {unknown[0]}'})
        wrong = sorted(path for path in self.mapping.values() if path not in self.fillable)
        if wrong:
            raise ValidationError({'mapping': f'{wrong[0]} is not a field you can import into'})
        if len(set(self.mapping.values())) != len(self.mapping):
            raise ValidationError({'mapping': 'Map each field from one column'})
        if not self.mapping:
            raise ValidationError({'mapping': 'Map at least one column to a field'})
        self.key = body.get('key')
        if self.key is not None and (not isinstance(self.key, str) or self.key not in self.header):
            raise ValidationError({'key': 'The key names a column of the CSV'})
        self.records = self.grouped()
        if len(self.records) > MAX_RECORDS:
            raise ValidationError({'csv': f'Use at most {MAX_RECORDS} records; split the file'})
        self.links = {}

    def grouped(self):
        """[(first CSV line, [(line, cells)])]: the rows of each record. Line 1 is the header."""
        records, by_key = [], {}
        index = None if self.key is None else self.header.index(self.key)
        for number, row in enumerate(self.rows, start=2):
            cells = dict(zip(self.header, (cell.strip() for cell in row)))
            key = None if index is None else cells.get(self.key, '')
            if index is not None and key == '' and records:
                records[-1][1].append((number, cells))
            elif index is not None and key in by_key:
                by_key[key][1].append((number, cells))
            else:
                records.append((number, [(number, cells)]))
                if key:
                    by_key[key] = records[-1]
        return records

    # ---------- one record ----------

    def body(self, context, rows):
        """The POST /api/<model> body for one record's rows, and the errors met building it ({column: message})."""
        values, children, errors = {}, {}, {}
        first = {}
        for line, cells in rows:
            child_rows = {}
            for column, path in self.mapping.items():
                text = cells.get(column, '')
                field, table = self.fillable[path]
                if table is None:
                    if text == '':
                        continue
                    if column in first and first[column] != text:
                        errors[column] = f'Line {line} has {text!r}; the first row of this record has {first[column]!r}'
                        continue
                    if column in first:
                        continue
                    first[column] = text
                    try:
                        values[path] = self.value(context, field, text)
                    except ValueError as exc:
                        errors[column] = str(exc)
                elif text != '':
                    try:
                        child_rows.setdefault(table, {})[path.split('.', 2)[2]] = self.value(context, field, text)
                    except ValueError as exc:
                        errors[column] = f'Line {line}: {exc}'
            for table, row in child_rows.items():
                children.setdefault(table, []).append({'_key': f'import-{line}', **row})
        if children:
            values['children'] = children
        return values, errors

    def value(self, context, field, text):
        """A CSV cell as the JSON value the create route takes."""
        kind = field.kind
        if kind in ('integer', 'duration'):
            try:
                return int(text.replace(',', ''))
            except ValueError:
                raise ValueError('Expected a whole number') from None
        if kind == 'boolean':
            if text.lower() in TRUE:
                return True
            if text.lower() in FALSE:
                return False
            raise ValueError('Expected yes or no')
        if kind == 'date':
            found = re.fullmatch(r'([0-9]{1,2})[-/.]([0-9]{1,2})[-/.]([0-9]{4})', text)
            if found:
                try:
                    return date(int(found[3]), int(found[2]), int(found[1])).isoformat()
                except ValueError:
                    raise ValueError('Expected a date such as 31-03-2026 or 2026-03-31') from None
            return text
        if isinstance(field, Link) and not field.ledger_target:
            return self.link(context, field, text)
        return text

    def link(self, context, field, text):
        """A Link cell: a record id, or the name or title of exactly one record this person can read."""
        registry = self.business.registry
        target = registry.resolve(field.target)
        cached = self.links.get((target, text))
        if cached is not None:
            return cached
        found = None
        try:
            record_id = RecordId(text)
        except (TypeError, ValueError):
            record_id = None
        if record_id is not None and self.business.policy.visible(context._tx, self.user, target, [record_id]):
            found = str(record_id)
        else:
            for column in ('name', 'title'):
                if column not in target.__fields__:
                    continue
                try:
                    page = context.list(target, filters={column: text}, limit=2)
                except (AccessError, ValidationError):
                    continue
                if page['total'] > 1:
                    raise ValueError(f'More than one {target.__name__} is called {text}')
                if page['total'] == 1:
                    found = str(page['rows'][0].id)
                    break
        if found is None:
            raise ValueError(f'No {target.__name__} is called {text}')
        self.links[(target, text)] = found
        return found

    def create(self, context, values):
        """Create one record as POST /api/<model> does; returns its id."""
        business, model = self.business, self.model
        if getattr(model, '__declaration_mode__', False):
            context._person = (model, None)
            return context._write(model, None, values)['id']
        return business.create_plain(context._tx, self.user, model, values).id

    # ---------- the whole file ----------

    def run(self, *, dry_run, all_or_nothing=True):
        if type(all_or_nothing) is not bool:
            raise ValidationError({'all_or_nothing': 'Use true or false'})
        try:
            with self.business.context(self.user) as context:
                answer = self.attempt(context, dry_run=dry_run, all_or_nothing=all_or_nothing)
                if dry_run or answer[1]['errors'] and all_or_nothing:
                    raise Rollback(answer)
                return answer
        except Rollback as rollback:
            return rollback.answer

    def attempt(self, context, *, dry_run, all_or_nothing):
        errors, ids = [], []
        for line, rows in self.records:
            values, problems = self.body(context, rows)
            if not problems:
                try:
                    with context._savepoint():
                        ids.append(self.create(context, values))
                except ValidationError as exc:
                    problems = self.columns_of(exc.errors)
                except (ConflictError, NotFoundError, AccessError) as exc:
                    problems = {'': str(exc.args[0]) if exc.args else str(exc)}
            for column, message in problems.items():
                errors.append({'line': line, 'column': column or None, 'message': message})
        failed = {item['line'] for item in errors}
        created = 0 if dry_run or errors and all_or_nothing else len(ids)
        result = {'dry_run': dry_run, 'records': len(self.records), 'rows': len(self.rows), 'valid': len(self.records) - len(failed),
                  'created': created, 'ids': [str(item) for item in ids] if created else [], 'errors': errors,
                  'error_csv': self.error_csv(errors) if errors else None}
        # A file that was read and checked answers 200 with its errors, so the result (and the error CSV) reaches the
        # browser; 201 when records were created. A body the route cannot read at all answers 422.
        return (201 if created else 200), jsonable(result), []

    def columns_of(self, errors):
        """Field errors keyed by the CSV column that filled the field; others by their own name."""
        by_field = {path: column for column, path in self.mapping.items()}
        result = {}
        for name, message in errors.items():
            column = by_field.get(name)
            text = message if isinstance(message, str) else '; '.join(map(str, message)) if isinstance(message, list) else json.dumps(message)
            result[column if column is not None else '' if name == '_form' else name] = text
        return result

    def error_csv(self, errors):
        """The rows of every record that failed, with an Error column, ready to fix and import again."""
        messages = {}
        for item in errors:
            text = f"{item['column']}: {item['message']}" if item['column'] else item['message']
            messages.setdefault(item['line'], []).append(text)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\r\n')
        writer.writerow([*self.header, 'Error'])
        for line, rows in self.records:
            if line not in messages:
                continue
            for number, cells in rows:
                writer.writerow([cells.get(column, '') for column in self.header] + ['; '.join(messages[line]) if number == line else ''])
        return out.getvalue()
