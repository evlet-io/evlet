"""Human document numbers: the Sequence field, its counters and where the engine assigns them.

A Sequence value is written only by the engine, inside the write transaction that assigns it.
Counters live in `evlet_sequence(model, field, scope_key, period, last)`. Writers are serialized
(one write transaction at a time), the counter row is updated in the same
transaction as the record, so committed records get gapless numbers and a rollback consumes none.
"""

from datetime import date
import json
import re
from types import MappingProxyType

from .fields import Boolean, Date, Integer, LongText, MISSING, State, Tags, Text
from .models import ValidationError, identifier
from . import qb
from .adapter import Column, TableDefinition

TOKEN = re.compile(r'\{([^{}]*)\}')
PERIOD_TOKENS = ('year', 'yy', 'month')
MAX_FORMAT = 120
COUNTER_TABLE = TableDefinition('evlet_sequence', (
    Column('model', 'TEXT', required=True), Column('field', 'TEXT', required=True), Column('scope_key', 'TEXT', required=True),
    Column('period', 'TEXT', required=True), Column('last', 'INTEGER', required=True), Column('last_name', 'TEXT'),
    Column('how', 'TEXT'), Column('note', 'TEXT'), Column('changed_by', 'TEXT'), Column('changed_at', 'INTEGER')),
    primary_key=('model', 'field', 'scope_key', 'period'))
# A counter row is identified by these columns.
COUNTER_KEY = ('model', 'field', 'scope_key', 'period')


def next_counter(connection, model, field, scope_key, period):
    """Add one to a counter, starting it at 1, in the write transaction; returns the new value."""
    counter = qb.insert('evlet_sequence', model=model, field=field, scope_key=scope_key, period=period, last=1)
    return connection.run(counter.on_conflict(*COUNTER_KEY).do_update(last=qb.col('evlet_sequence.last') + 1).returning('last')).id
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
    OPTIONS = ('label', 'help_text', 'tooltip', 'placeholder', 'hidden', 'copy')

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

def ensure_counters(connection):
    connection.ensure_table(COUNTER_TABLE)


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
    scope = {column: fields[column].to_storage(values.get(column)) for column in sequence.scope}
    for _ in range(MAX_SKIPPED):
        number = sequence.render(next_counter(connection, *key), day)
        if connection.run(qb.select(resource, 'id').where(**scope, **{name: number})).one() is None:
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
            session.connection.run(qb.update(resource, **{name: number}).where(id=record.id))
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
MAX_TRIES = 1000


def parse_series(text):
    """The parts of a series format: literal text, ('date', token), ('n', width) or ('field', name). Raises ValueError."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Enter a format.')
    if len(text) > MAX_FORMAT:
        raise ValueError(f'Keep the format under {MAX_FORMAT} characters.')
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
    # Names are at most 40 characters after expansion; field values are checked when a name is made.
    if sum(len(part) if isinstance(part, str) else 4 if part == ('date', 'YYYY') else 2 if part[0] == 'date' else
           part[1] if part[0] == 'n' else 0 for part in parts) > MAX_NAME:
        raise ValueError(f'Names from this format would be longer than {MAX_NAME} characters.')
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
    """How records get their standard `name` (evlet/naming.py).

    Naming(Series(...), ...): the first series whose condition matches, from the top.
    Naming(): the model's `autoname(self, context) -> str` method, run in the write transaction.
    Naming(manual=True): people type the name.

    `assign_on` is "create" (the default), "confirm" for a confirmable model (the name is taken in the
    confirm transaction, so drafts never use a number) or a state of the model. Until then the name is
    empty. `date_field` is an optional Date field for the date tokens; without it they use the day the
    name is assigned, in the instance timezone. A trusted Session may pass a name (imports); it is kept.

    The older form Naming("field", Series(...)) names a unique, optional Text field of the model instead,
    on create, and that field stays read only.

    `label` names the standard `name` field for the model's domain, such as "Project number", on the
    form, in list columns, filters, search and the schema. Without it the field is "Name".
    """

    def __init__(self, *series, date_field=None, assign_on='create', manual=False, label=None):
        field = 'name'
        if series and isinstance(series[0], str):
            field, series = series[0], series[1:]
            identifier(field)
            if not series:
                raise ValueError('Naming takes one or more Series after the field name')
        if any(not isinstance(item, Series) for item in series):
            raise ValueError('Naming takes Series, such as Naming(Series("INV-{YYYY}-{#####}"))')
        if manual is not True and manual is not False:
            raise ValueError('Naming manual is True or False')
        if manual and series:
            raise ValueError('Naming(manual=True) takes no series: people type the name')
        if not isinstance(assign_on, str) or not assign_on:
            raise ValueError('Naming assign_on is "create", "confirm" or a state name')
        if field != 'name' and assign_on != 'create':
            raise ValueError('Naming of a field other than name assigns on create')
        if date_field is not None:
            if not isinstance(date_field, str):
                raise ValueError('Naming date_field names a Date field')
            identifier(date_field)
        if label is not None:
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80 or '\n' in label:
                raise ValueError('Naming label is a short line of text, such as "Project number"')
            if field != 'name':
                raise ValueError(f'Naming label names the standard name field; label {field} with label= on the field itself')
            label = label.strip()
        self.field, self.date_field, self.assign_on, self.manual = field, date_field, assign_on, manual
        self.label = label
        last = len(series) - 1
        self.series = tuple({'id': item.id or ('default' if index == last else f'series_{index + 1}'),
                             'label': item.label or item.format, 'format': item.format, 'when': item.when}
                            for index, item in enumerate(series))
        if self.series:
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

    def __init__(self, model, field, date_field, series, assign_on='create', manual=False):
        from .expressions import UnknownField, parse
        fields = model.__declared_fields__
        self.model, self.field, self.date_field = model, field, date_field
        self.assign_on, self.manual = assign_on, manual
        # Without series and not manual, the model's autoname(self, context) method names records.
        self.autoname = not series and not manual
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

    @property
    def system(self):
        """Whether the server assigns the name (a series or autoname), so people do not type it."""
        return not self.manual

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
    from .naming import has_name
    fields = model.__declared_fields__
    where = f'{model.__name__}.Meta.naming'
    target = fields.get(naming.field)
    if naming.field == 'name' and not has_name(model):
        raise ValueError(f'{where}: {model.__name__} declares its own name field; rename it so the standard name can be used')
    if not isinstance(target, Text) or isinstance(target, (LongText, Sequence)) or not target.stored \
            or not target.unique or target.required or target.compute is not None:
        raise ValueError(f'{where}: {naming.field} must be a stored Text field with unique=True and not required; '
                         'the server fills it on create')
    if naming.date_field is not None and not isinstance(fields.get(naming.date_field), Date):
        raise ValueError(f'{where}: date_field names {naming.date_field!r}, which is not a Date field of the model')
    if naming.assign_on == 'confirm' and not getattr(model, 'confirmable', False):
        raise ValueError(f'{where}: assign_on="confirm" needs a confirmable model')
    if naming.assign_on not in ('create', 'confirm'):
        state = model.__state__
        if state is None or naming.assign_on not in fields[state].choices:
            raise ValueError(f'{where}: assign_on is "create", "confirm" or a state of the model, not {naming.assign_on!r}')
    rules = naming.series if series is None else series
    if not rules and not naming.manual and not callable(getattr(model, 'autoname', None)):
        raise ValueError(f'{where}: give Series, manual=True, or an autoname(self, context) method on the model')
    try:
        return NamingRules(model, naming.field, naming.date_field, rules, naming.assign_on, naming.manual)
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
    connection.ensure_table(COUNTER_TABLE)
    present = set(connection.columns('evlet_sequence'))
    for name, kind in COUNTER_COLUMNS:
        if name not in present:
            connection.add_column('evlet_sequence', Column(name, kind))


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
    for _ in range(MAX_TRIES):
        now = int(time.time())
        counter = qb.insert('evlet_sequence', model=resource, field=rules.field, scope_key=prefix, period='', last=1, how='issued',
                            changed_by=actor, changed_at=now, note=None)
        counter = counter.on_conflict(*COUNTER_KEY).do_update(last=qb.col('evlet_sequence.last') + 1, how='issued', changed_by=actor,
                                                               changed_at=now, note=None)
        number = connection.run(counter.returning('last')).id
        name = prefix + str(number).zfill(width) + suffix
        if len(name) > MAX_NAME:
            raise ValidationError({rules.field: f'This name would be longer than {MAX_NAME} characters'})
        if connection.run(qb.select(resource, 'id').where(**{rules.field: name})).one() is None:
            connection.run(qb.update('evlet_sequence', last_name=name).where(model=resource, field=rules.field, scope_key=prefix, period=''))
            return name
    raise ValidationError({rules.field: f'No free name was found in {MAX_TRIES} tries. Set the counter past the names already used.'})


def preview_name(connection, resource, rules, parts, values, today):
    """The name a series would give a record with these values now. No counter changes; `connection` may
    be None before any counter exists."""
    day = rules.day(values, today)
    head, width, tail = split(parts)
    prefix = render(head, values, day)
    row = connection.run(qb.select('evlet_sequence', 'last').where(model=resource, field=rules.field, scope_key=prefix, period='')).one() \
        if connection is not None else None
    return prefix + str((row[0] if row else 0) + 1).zfill(width) + render(tail, values, day)


def naming_rules(registry, model):
    from .fields import active_layer
    return active_layer().naming.get(model) or registry.naming.get(model)


def name_due(rules, values, *, created=False, lifecycle=None, move=None):
    """Whether a record without a name gets one in this write (see Naming.assign_on)."""
    if rules is None or rules.manual or values.get(rules.field) not in (None, ''):
        return False
    if rules.assign_on == 'create':
        return created
    if rules.assign_on == 'confirm':
        return lifecycle == 'confirm'
    state = rules.model.__state__
    return (created and values.get(state) == rules.assign_on) or (move is not None and move.to == rules.assign_on)


def issue(connection, registry, model, rules, values, today, actor=None, *, record=None, context=None, record_id=None):
    """A name from the series or the model's autoname(self, context), checked to be free."""
    from .naming import check_name
    table = registry.name_for(model)
    if rules.autoname:
        name = model.autoname(record, context)
        check_name(connection, table, model, name, record_id)
        return name
    connection.ensure_table(COUNTER_TABLE)
    return issue_name(connection, table, rules, values, today, actor)


def name_new(connection, registry, model, values, today, actor=None, *, record=None, context=None):
    """The name for a new record without one, or None when none is due on create or a name was given."""
    rules = naming_rules(registry, model)
    if not name_due(rules, values, created=True):
        return None
    return issue(connection, registry, model, rules, values, today, actor, record=record, context=context)
