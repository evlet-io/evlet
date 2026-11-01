"""Human document numbers: the Sequence field, its counters and where the engine assigns them.

A Sequence value is written only by the engine, inside the write transaction that assigns it.
Counters live in `evlet_sequence(model, field, scope_key, period, last)`. Writers are serialized
(SQLite `BEGIN IMMEDIATE`, a PostgreSQL transaction lock), the counter row is updated in the same
transaction as the record, so committed records get gapless numbers and a rollback consumes none.
"""

from datetime import date
import json
import re
from types import MappingProxyType

from .fields import Boolean, Date, Integer, LongText, MISSING, State, Tags, Text
from .models import ValidationError, identifier

TOKEN = re.compile(r'\{([^{}]*)\}')
PERIOD_TOKENS = ('year', 'yy', 'month')
MAX_FORMAT = 120
COUNTER_TABLE = ('CREATE TABLE IF NOT EXISTS evlet_sequence (model TEXT NOT NULL, field TEXT NOT NULL, '
                 'scope_key TEXT NOT NULL, period TEXT NOT NULL, "last" INTEGER NOT NULL, last_name TEXT, how TEXT, '
                 'note TEXT, changed_by TEXT, changed_at INTEGER, PRIMARY KEY (model, field, scope_key, period))')
NEXT = ('INSERT INTO evlet_sequence (model, field, scope_key, period, "last") VALUES (?, ?, ?, ?, 1) '
        'ON CONFLICT (model, field, scope_key, period) DO UPDATE SET "last" = evlet_sequence."last" + 1 RETURNING "last"')
ASSIGNED_BY_ENGINE = 'This number is assigned by the system'
# Numbers already taken in a scope (planted by a repair, or an older format) are skipped, at most this many in a row.
MAX_SKIPPED = 10000


def parse_format(text):
    """The parts of a number format: literal text and (token, width) pairs. Raises ValueError."""
    if not isinstance(text, str) or not text or len(text) > MAX_FORMAT:
        raise ValueError(f'A Sequence format is text of 1 to {MAX_FORMAT} characters, such as "INV-{{year}}-{{n:05}}"')
    if any(not character.isprintable() for character in text):
        raise ValueError('A Sequence format cannot contain control characters')
    parts, seen, position = [], set(), 0
    for match in TOKEN.finditer(text):
        literal = text[position:match.start()]
        if '{' in literal or '}' in literal:
            raise ValueError('A Sequence format has an unmatched brace')
        if literal:
            parts.append(literal)
        token = match.group(1)
        padded = re.fullmatch(r'n:0([1-9][0-9]?)', token)
        name, width = ('n', int(padded.group(1))) if padded else (token, 0)
        if name not in ('n', *PERIOD_TOKENS):
            raise ValueError(f'Unknown Sequence token {{{token}}}; use {{year}}, {{yy}}, {{month}}, {{n}} or {{n:05}}')
        if width > 18:
            raise ValueError('Pad {n} to at most 18 digits')
        if name in seen:
            raise ValueError(f'A Sequence format uses {{{name}}} once')
        seen.add(name)
        parts.append((name, width))
        position = match.end()
    rest = text[position:]
    if '{' in rest or '}' in rest:
        raise ValueError('A Sequence format has an unmatched brace')
    if rest:
        parts.append(rest)
    if 'n' not in seen:
        raise ValueError('A Sequence format needs the counter {n}')
    if 'year' in seen and 'yy' in seen:
        raise ValueError('Use {year} or {yy}, not both')
    if 'month' in seen and not seen & {'year', 'yy'}:
        raise ValueError('{month} needs {year} or {yy}, or numbers would repeat every year')
    return tuple(parts)


class Sequence(Text):
    """A human document number such as INV-2026-00042, separate from the record id.

    `scope` names plain fields whose values each have their own counter. With {year}, {yy} or {month}
    the counter restarts each period, decided by the Date field named by `date=` (or, without it, the
    day the number is assigned in the instance timezone). `assign_on` is "create" or a state of the
    model's State; the value stays null until then and only the engine writes it.
    """
    kind = 'sequence'
    OPTIONS = ('label', 'help_text', 'placeholder', 'hidden')

    def __init__(self, format, *, scope=(), assign_on='create', date=None, **kwargs):
        if set(kwargs) - set(self.OPTIONS):
            raise ValueError('Sequence takes format, scope, assign_on, date and ' + ', '.join(self.OPTIONS))
        super().__init__(max_length=255, default=None, read_only=True, **kwargs)
        self.parts = parse_format(format)
        self.format = format
        if isinstance(scope, str) or not isinstance(scope, (list, tuple)) or len(set(scope)) != len(scope) \
                or any(not isinstance(name, str) for name in scope):
            raise ValueError('Sequence scope is a list of distinct field names, such as ("company",)')
        for name in scope:
            identifier(name)
        self.scope = tuple(scope)
        if not isinstance(assign_on, str) or not assign_on:
            raise ValueError('Sequence assign_on is "create" or a state name')
        self.assign_on = assign_on
        tokens = {part[0] for part in self.parts if isinstance(part, tuple)}
        self.period = 'month' if 'month' in tokens else 'year' if tokens & {'year', 'yy'} else None
        if date is not None:
            if not isinstance(date, str):
                raise ValueError('Sequence date names a Date field')
            identifier(date)
            if self.period is None:
                raise ValueError('Sequence date is used only by a format with {year}, {yy} or {month}')
        self.date = date

    def metadata(self):
        # The storage schema: the scope shapes the unique index. The format, assign_on and date
        # can change without a migration; numbers already assigned keep their values.
        return {**super().metadata(), 'scope': list(self.scope)}

    def schema(self):
        return {'format': self.format, 'scope': list(self.scope), 'assign_on': self.assign_on, 'date': self.date}

    def render(self, number, day):
        values = {'year': f'{day.year:04d}', 'yy': f'{day.year % 100:02d}', 'month': f'{day.month:02d}'} if day else {}
        return ''.join(part if isinstance(part, str) else str(number).zfill(part[1]) if part[0] == 'n' else values[part[0]]
                       for part in self.parts)

    def period_key(self, day):
        if self.period is None:
            return ''
        return f'{day.year:04d}' if self.period == 'year' else f'{day.year:04d}-{day.month:02d}'


def fields_of(model):
    return {name: field for name, field in model.__declared_fields__.items() if isinstance(field, Sequence)}


def compile_sequences(registry, models, plans, child_models):
    """{model: {field name: Sequence}} after checking each declaration against its model."""
    result = {}
    for model in models:
        declared = fields_of(model)
        if not declared:
            continue
        if model in child_models:
            raise ValueError(f'{model.__name__}: a Sequence belongs on a root document, not on Table rows')
        fields = model.__declared_fields__
        for name, sequence in declared.items():
            where = f'{model.__name__}.{name}'
            for scoped in sequence.scope:
                field = fields.get(scoped)
                plain = field is not None and field.stored and field.compute is None and field.query is None \
                    and field.fetch is None and isinstance(field, (Text, Integer, Date, Boolean)) \
                    and not isinstance(field, (LongText, Sequence, Tags, State))
                if not plain:
                    raise ValueError(f'{where}: scope field {scoped!r} must be a stored, not calculated, text, number, '
                                     'Link, date or Boolean field of the model, and not its State')
                if not field.required:
                    raise ValueError(f'{where}: scope field {scoped!r} must be required, so every number has a scope')
            if sequence.date is not None:
                field = fields.get(sequence.date)
                if not isinstance(field, Date) or not field.stored:
                    raise ValueError(f'{where}: date names {sequence.date!r}, which is not a stored Date field of the model')
            if sequence.assign_on != 'create':
                state = model.__state__
                if state is None or sequence.assign_on not in fields[state].choices:
                    raise ValueError(f'{where}: assign_on is "create" or a state of the model, not {sequence.assign_on!r}')
            plan = plans.get(model)
            for entry in (plan or {}).get('fields', ()):
                for scope, _, read in entry['reads']:
                    if read == name and (scope == 'self' and not entry['path'] or scope == 'parent' and len(entry['path']) == 1):
                        raise ValueError(f'{where}: calculations and field rules cannot read a Sequence, because the engine '
                                         'assigns it after the document settles; read it in a move guard or an action instead')
        result[model] = MappingProxyType(declared)
    return MappingProxyType(result)


# Storage

def index_sql(name, table, field_name, sequence):
    """A unique index on the scope fields and the number, for assigned numbers only."""
    columns = ', '.join(identifier(column) for column in (*sequence.scope, field_name))
    return (f'CREATE UNIQUE INDEX {identifier(name)} ON {identifier(table)} ({columns}) '
            f'WHERE {identifier(field_name)} IS NOT NULL')


def ensure_counters(connection):
    connection.execute(COUNTER_TABLE)


def scope_key(model, sequence, values):
    if not sequence.scope:
        return ''
    fields = model.__declared_fields__
    return json.dumps([fields[name].serialize(values.get(name)) for name in sequence.scope], separators=(',', ':'))


def number_day(sequence, values, today):
    """The day that decides the period: the date field's value, or today in the instance timezone."""
    if sequence.period is None:
        return None
    if sequence.date is None:
        return date.fromisoformat(today)
    day = values.get(sequence.date)
    if day is None:
        raise ValidationError({sequence.date: 'Fill in this date before the number is assigned'})
    return day


def next_number(connection, resource, name, model, sequence, values, today):
    """Take the next counter value for this record's scope and period and render the number.

    A number that a record of the same scope already holds is skipped (the counter moves past it), so a
    clash planted by a repair, a format changed back or a {yy} century wrap does not block the series.
    In normal operation nothing is skipped and numbers stay gapless.
    """
    day = number_day(sequence, values, today)
    fields = model.__declared_fields__
    key = (resource, name, scope_key(model, sequence, values), sequence.period_key(day))
    columns = ' AND '.join(f'{identifier(column)} = ?' for column in (*sequence.scope, name))
    scope = [fields[column].to_storage(values.get(column)) for column in sequence.scope]
    for _ in range(MAX_SKIPPED):
        number = sequence.render(connection.execute(NEXT, key).fetchone()[0], day)
        if connection.execute(f'SELECT 1 FROM {identifier(resource)} WHERE {columns}', [*scope, number]).fetchone() is None:
            return number
    raise ValidationError({name: 'No free number was found in this series; check the stored numbers and the format'})


# The document pipeline

def sequence_write(model, scalars, current, readable):
    """Ordinary writes never set a Sequence. Sending the saved value back is not a change.

    `readable` are the fields this role may read. A value for a Sequence the role cannot read is left
    for the permission check, which refuses it whatever it is, so the answer reveals nothing.
    """
    for name in fields_of(model):
        if name not in scalars or name not in readable:
            continue
        if scalars[name] == (None if current is None else current._values.get(name)):
            del scalars[name]
        else:
            raise ValidationError({name: ASSIGNED_BY_ENGINE})


def assign(context, model, draft, before, move):
    """Assign due numbers on the settled draft, in the write transaction. Returns [(field, number)].

    A number is due on create (assign_on="create", or a new record starting in the assign_on state)
    and when a move enters the assign_on state. Once assigned, the scope fields and the period of the
    date field are fixed, because the number was counted in them.
    """
    business = context._business
    declared = business.registry.sequences.get(model)
    if not declared:
        return []
    from .documents import host_today
    readable = business.policy.fields(context.user, model)
    state = model.__state__
    assigned = []
    for name, sequence in declared.items():
        if before is not None and before._values.get(name) is not None:
            fixed(sequence, draft._values, before._values, name in readable)
            continue
        if draft._values.get(name) is not None:
            continue
        if sequence.assign_on == 'create':
            due = before is None
        else:
            due = (before is None and draft._values.get(state) == sequence.assign_on) or \
                  (move is not None and move.to == sequence.assign_on)
        if not due:
            continue
        number = next_number(context._tx.connection, business.registry.name_for(model), name, model, sequence,
                             draft._values, host_today(business))
        draft._values[name] = number
        assigned.append((name, number))
    return assigned


def fixed(sequence, values, saved, shown=True):
    """Refuse a change of the scope fields, or of the date field's period, of an assigned number.

    `shown` is whether the caller may read the number; otherwise the message does not reveal one exists.
    """
    errors = {}
    generic = 'This cannot change now'
    for name in sequence.scope:
        if values.get(name) != saved.get(name):
            errors[name] = 'This is fixed once the number is assigned' if shown else generic
    if sequence.date is not None:
        old, new = saved.get(sequence.date), values.get(sequence.date)
        if new is None or old is not None and sequence.period_key(old) != sequence.period_key(new):
            errors[sequence.date] = 'This stays in the period of the assigned number' if shown else generic
    if errors:
        raise ValidationError(errors)


def assigned(context, model, record_id, name, number):
    """Called once per assigned number after the record is saved, in its transaction.

    Writes the Journal's `number` entry; numbers never appear in `created` or `changed` entries.
    """
    from . import journal
    journal.number(context, model, record_id, name, number)


def clear_for_copy(model, values):
    """Values for a copy of a record (such as a revision): every Sequence is null, so the copy gets
    its own number when it reaches assign_on. Returns a new dict; `values` is not changed."""
    cleared = dict(values)
    for name in fields_of(model):
        if name in cleared:
            cleared[name] = None
    return cleared


# Existing rows

def backfill(session, model, name, *, filters=None, today=None):
    """Number existing rows whose `name` is null, in id order, from the same counters.

    For a Sequence added by a migration. Run it in one transaction; `filters` selects the rows (for
    example only issued records) and `today` (YYYY-MM-DD) is the period day for formats without a date
    field. Returns the number of rows numbered. This is trusted maintenance code: it writes the field
    directly and does not run document settlement, which a Sequence never feeds.
    """
    sequence = model.__declared_fields__.get(name)
    if not isinstance(sequence, Sequence):
        raise ValueError(f'{model.__name__}.{name} is not a Sequence field')
    if sequence.period is not None and sequence.date is None and today is None:
        raise ValueError('Pass today="YYYY-MM-DD" to number rows by a format period without a date field')
    resource = session.registry.name_for(model)
    ensure_counters(session.connection)
    count = 0
    while True:
        rows = session.list(model, filters={**(filters or {}), name: None}, order_by=('id',), limit=100)
        if not rows:
            return count
        for record in rows:
            for scoped in sequence.scope:
                if record._values.get(scoped) is None:
                    raise ValidationError({scoped: f'Record {record.id} has no value for this scope field'})
            number = next_number(session.connection, resource, name, model, sequence, record._values, today)
            session.connection.execute(f'UPDATE {identifier(resource)} SET {identifier(name)} = ? WHERE "id" = ?', (number, record.id))
            count += 1


def raw_write(session, model, values, current, cleaned):
    """Raw Session writes: refuse setting a Sequence or moving an assigned number's scope or period,
    except inside maintenance(). Returns the numbers a raw create assigns, {field: number}.

    `cleaned` are the record's validated values after the write. A raw create numbers a Sequence that is
    due on create (assign_on="create", or the default state is assign_on); without a date field its
    period is today in UTC, because a Session has no instance timezone.
    """
    declared = fields_of(model)
    if not declared:
        return {}
    if not session._maintenance:
        for name, sequence in declared.items():
            value = values.get(name, MISSING)
            saved = None if current is None else current._values.get(name)
            if value is not MISSING and value != saved:
                raise ValueError(f'{model.__name__}.{name} is assigned by the engine; use sequences.backfill() '
                                 'or session.maintenance() for an audited repair')
            if saved is not None:
                fixed(sequence, cleaned, current._values)
    if current is not None:
        return {}
    from datetime import datetime, timezone
    numbers = {}
    for name, sequence in declared.items():
        due = sequence.assign_on == 'create' or cleaned.get(model.__state__) == sequence.assign_on
        if due and cleaned.get(name) is None:
            ensure_counters(session.connection)
            numbers[name] = next_number(session.connection, session.registry.name_for(model), name, model, sequence,
                                        cleaned, datetime.now(timezone.utc).date().isoformat())
    return numbers


# Naming series: Meta.naming = Naming("reference", Series("INV-{YYYY}-{####}"), date_field="posting_date")
#
# A naming series names a record on create from the same counters as a Sequence. A counter row is keyed
# (model, target field, resolved prefix, ''): the prefix is the expanded text before the counter token, so
# a new year or month gives a new prefix that starts at 1. Controls can replace the series list (override
# kind "naming") and set a counter; the target field and the date field come from the app file.

MAX_NAME = 40
DATE_TOKENS = {'YYYY': '%Y', 'YY': '%y', 'MM': '%m', 'DD': '%d'}
SERIES_ID = re.compile(r'[a-z][a-z0-9_]{0,39}')
COUNTER_COLUMNS = (('last_name', 'TEXT'), ('how', 'TEXT'), ('note', 'TEXT'), ('changed_by', 'TEXT'), ('changed_at', 'INTEGER'))
ISSUE = ('INSERT INTO evlet_sequence (model, field, scope_key, period, "last", how, changed_by, changed_at, note) '
         "VALUES (?, ?, ?, '', 1, 'issued', ?, ?, NULL) ON CONFLICT (model, field, scope_key, period) DO UPDATE SET "
         '"last" = evlet_sequence."last" + 1, how = \'issued\', changed_by = excluded.changed_by, '
         'changed_at = excluded.changed_at, note = NULL RETURNING "last"')
MAX_TRIES = 1000


def parse_series(text):
    """The parts of a series format: literal text, ('date', token), ('n', width) or ('field', name). Raises ValueError."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Enter a format.')
    if len(text) > MAX_NAME:
        raise ValueError(f'Keep it under {MAX_NAME} characters.')
    if any(not character.isprintable() for character in text):
        raise ValueError('A format cannot contain control characters.')
    parts, position, counters = [], 0, 0
    for match in TOKEN.finditer(text):
        literal = text[position:match.start()]
        if '{' in literal or '}' in literal:
            raise ValueError('A brace is not closed.')
        if literal:
            parts.append(literal)
        token = match.group(1)
        if token in DATE_TOKENS:
            parts.append(('date', token))
        elif re.fullmatch('#+', token):
            if not 3 <= len(token) <= 8:
                raise ValueError('A counter has 3 to 8 #, like {####}.')
            counters += 1
            parts.append(('n', len(token)))
        elif re.fullmatch('[a-z][a-z0-9_]*', token):
            parts.append(('field', token))
        else:
            raise ValueError(f"{{{token}}} isn't a token. Use {{YYYY}}, {{YY}}, {{MM}}, {{DD}}, {{####}} or a field name.")
        position = match.end()
    if '{' in text[position:] or '}' in text[position:]:
        raise ValueError('A brace is not closed.')
    if text[position:]:
        parts.append(text[position:])
    if counters != 1:
        raise ValueError('Use exactly one counter, like {####}.')
    return tuple(parts)


class Series:
    """One naming series: a format and, except for the last series, a condition (see evlet/expressions.py)."""

    def __init__(self, format, *, when='', label=None, id=None):
        self.parts = parse_series(format)
        if not isinstance(when, str):
            raise ValueError("Series when is condition text, such as 'total >= 250000'")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ValueError('Series label is nonempty text')
        if id is not None and (not isinstance(id, str) or not SERIES_ID.fullmatch(id)):
            raise ValueError('Series id uses lowercase letters, digits and underscores, starting with a letter')
        self.format, self.when, self.label, self.id = format, when.strip(), label, id


class Naming:
    """Name records on create with the first series whose condition matches, from the top.

    `field` is a unique, optional Text field of the model that only the server fills on create (a trusted
    Session may pass a name, for imports). `date_field` is an optional Date field for the date tokens;
    without it they use the creation day in the instance timezone.
    """

    def __init__(self, field, *series, date_field=None):
        if not isinstance(field, str):
            raise ValueError('Naming field names a unique Text field')
        identifier(field)
        if not series or any(not isinstance(item, Series) for item in series):
            raise ValueError('Naming takes one or more Series after the field name')
        if date_field is not None:
            if not isinstance(date_field, str):
                raise ValueError('Naming date_field names a Date field')
            identifier(date_field)
        self.field, self.date_field = field, date_field
        last = len(series) - 1
        self.series = tuple({'id': item.id or ('default' if index == last else f'series_{index + 1}'),
                             'label': item.label or item.format, 'format': item.format, 'when': item.when}
                            for index, item in enumerate(series))
        check_order(self.series)


def check_order(series):
    """Every series but the last has a condition; the last has none, so every record gets a name."""
    if series[-1]['when']:
        raise ValueError('The last series has no condition, so every record gets a name.')
    if any(not item['when'] for item in series[:-1]):
        raise ValueError('Only the last series can be without a condition.')
    if len({item['id'] for item in series}) != len(series):
        raise ValueError('Give each series its own id.')


class NamingRules:
    """A model's compiled naming: the target field, the date field and the series in order.

    `series` holds (public dict, parts, Condition or None). `skipped` lists series left out because their
    condition names a field the model no longer has.
    """

    def __init__(self, model, field, date_field, series):
        from .expressions import UnknownField, parse
        fields = model.__declared_fields__
        self.model, self.field, self.date_field = model, field, date_field
        self.series, self.skipped = [], []
        for item in series:
            parts = parse_series(item['format'])
            for part in parts:
                if isinstance(part, tuple) and part[0] == 'field':
                    found = fields.get(part[1])
                    if found is None:
                        raise ValueError(f"{{{part[1]}}} isn't a token or a field on {model.__name__}.")
                    if not isinstance(found, Text) or isinstance(found, LongText) or part[1] == field:
                        raise ValueError(f'{{{part[1]}}} must be a short text field other than {field}.')
            try:
                condition = parse(item['when'], fields) if item['when'] else None
            except UnknownField as exc:
                self.skipped.append({'id': item['id'], 'error': str(exc), 'field': exc.name})
                continue
            self.series.append((dict(item), parts, condition))

    def public(self):
        return [item for item, _, _ in self.series]

    def match(self, values):
        for item, parts, condition in self.series:
            if condition is None or condition.matches(values):
                return item, parts
        return self.series[-1][:2]

    def day(self, values, today):
        value = values.get(self.date_field) if self.date_field else None
        return value or date.fromisoformat(today)


def compile_naming(model, naming, series=None):
    """Check Meta.naming against its model; `series` replaces the file series (a Controls override)."""
    fields = model.__declared_fields__
    where = f'{model.__name__}.Meta.naming'
    target = fields.get(naming.field)
    if not isinstance(target, Text) or isinstance(target, (LongText, Sequence)) or not target.stored \
            or not target.unique or target.required or target.compute is not None:
        raise ValueError(f'{where}: {naming.field} must be a stored Text field with unique=True and not required; '
                         'the server fills it on create')
    if naming.date_field is not None and not isinstance(fields.get(naming.date_field), Date):
        raise ValueError(f'{where}: date_field names {naming.date_field!r}, which is not a Date field of the model')
    try:
        return NamingRules(model, naming.field, naming.date_field, naming.series if series is None else series)
    except ValueError as exc:
        raise ValueError(f'{where}: {exc}') from None


def compile_namings(models, child_models):
    result = {}
    for model in models:
        naming = getattr(getattr(model, 'Meta', None), 'naming', None)
        if naming is None:
            continue
        if not isinstance(naming, Naming):
            raise ValueError(f'{model.__name__}.Meta.naming is Naming("field", Series("..."), ...)')
        if model in child_models:
            raise ValueError(f'{model.__name__}: naming belongs on a root document, not on Table rows')
        result[model] = compile_naming(model, naming)
    return MappingProxyType(result)


def upgrade_counters(connection):
    """The counter table with the columns naming series use: the last name and the last change."""
    import sqlite3
    connection.execute(COUNTER_TABLE)
    if isinstance(connection, sqlite3.Connection):
        present = {row[1] for row in connection.execute('PRAGMA table_info(evlet_sequence)')}
    else:
        present = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='evlet_sequence'")}
    for name, kind in COUNTER_COLUMNS:
        if name not in present:
            connection.execute(f'ALTER TABLE evlet_sequence ADD COLUMN {name} {kind}')


def render(parts, values, day, number=None):
    """The text of parts; a field token uses the cleaned value with / and spaces as -."""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif part[0] == 'date':
            out.append(day.strftime(DATE_TOKENS[part[1]]))
        elif part[0] == 'field':
            out.append(re.sub(r'[/\s]+', '-', str(values.get(part[1]) or '')))
        else:
            out.append(str(number).zfill(part[1]))
    return ''.join(out)


def split(parts):
    """(parts before the counter, counter width, parts after it)."""
    index = next(i for i, part in enumerate(parts) if isinstance(part, tuple) and part[0] == 'n')
    return parts[:index], parts[index][1], parts[index + 1:]


def issue_name(connection, resource, rules, values, today, actor):
    """Issue the next name for a new record, in its write transaction. Names already held are skipped."""
    import time
    _, parts = rules.match(values)
    day = rules.day(values, today)
    head, width, tail = split(parts)
    prefix, suffix = render(head, values, day), render(tail, values, day)
    target = identifier(rules.field)
    for _ in range(MAX_TRIES):
        number = connection.execute(ISSUE, (resource, rules.field, prefix, actor, int(time.time()))).fetchone()[0]
        name = prefix + str(number).zfill(width) + suffix
        if len(name) > MAX_NAME:
            raise ValidationError({rules.field: f'This name would be longer than {MAX_NAME} characters'})
        if connection.execute(f'SELECT 1 FROM {identifier(resource)} WHERE {target} = ?', (name,)).fetchone() is None:
            connection.execute("UPDATE evlet_sequence SET last_name = ? WHERE model = ? AND field = ? AND scope_key = ? AND period = ''",
                               (name, resource, rules.field, prefix))
            return name
    raise ValidationError({rules.field: f'No free name was found in {MAX_TRIES} tries. Set the counter past the names already used.'})


def preview_name(connection, resource, rules, parts, values, today):
    """The name a series would give a record with these values now. No counter changes; `connection` may
    be None before any counter exists."""
    day = rules.day(values, today)
    head, width, tail = split(parts)
    prefix = render(head, values, day)
    row = connection.execute("SELECT \"last\" FROM evlet_sequence WHERE model = ? AND field = ? AND scope_key = ? AND period = ''",
                             (resource, rules.field, prefix)).fetchone() if connection is not None else None
    return prefix + str((row[0] if row else 0) + 1).zfill(width) + render(tail, values, day)


def name_new(connection, registry, model, values, today, actor=None):
    """The name for a new record without one, or None when the model has no naming or a name was given."""
    rules = registry.naming.get(model)
    if rules is None or values.get(rules.field) not in (None, ''):
        return None
    connection.execute(COUNTER_TABLE)
    return issue_name(connection, registry.name_for(model), rules, values, today, actor)
