"""The standard `name` and `title` of every record (docs/internal/record-identity.md).

`id` never changes and is what Links point to. `name` is the human identifier, such as INV-2026-00001:
optional, unique per model, filled by a naming series, an `autoname(self, context)` method or by hand.
`title` is the display text: a declared `title` field, `Meta.title_field`, a `Meta.title` template of
short fields, or else the name, then the text id. The framework keeps it in sync on every write.

Row tables (Table children) have neither.
"""

import re

NAME_LENGTH = 140
TITLE_LENGTH = 255
TEMPLATE = re.compile(r'\{([A-Za-z][A-Za-z0-9_]*)\}')


def name_field():
    from .fields import Text
    field = Text(max_length=NAME_LENGTH, unique=True, label='Name', read_only=True)
    field.standard = 'name'
    return field


def title_field():
    from .fields import Text
    field = Text(max_length=TITLE_LENGTH, label='Title', read_only=True)
    field.standard = 'title'
    return field


def standard(field):
    """'name' or 'title' for a framework column, else None."""
    return getattr(field, 'standard', None)


def is_row_table(namespace, bases, fields):
    """Table children (Child), transient forms and frozen row tables that declare evlet_position."""
    flags = ('__row__', '__transient__')
    return any(namespace.get(flag) or any(getattr(base, flag, False) for base in bases) for flag in flags) \
        or 'evlet_position' in fields


def add_standard(fields):
    """Give a record model its standard columns. A declared `title` field is the title itself. A declared
    `name` field marks a model from before records had standard columns (a frozen class in migration
    history, a legacy registry): it gets neither, as its table had neither.
    Returns the fields it added, for the class namespace."""
    added = {}
    if 'name' in fields and not standard(fields['name']):
        return added
    if 'name' not in fields:
        added['name'] = name_field()
    if 'title' not in fields:
        added['title'] = title_field()
    fields.update(added)
    return added


def form_hidden(field):
    """Fields forms leave out although responses carry them: the standard title, and a standard name
    that no naming fills (it stays read only)."""
    return standard(field) == 'title' or (standard(field) == 'name' and field.read_only)


def has_name(model):
    return standard(model.__fields__.get('name')) == 'name'


def has_title(model):
    """Whether the framework fills `title` (a declared title field is the model's own)."""
    return standard(model.__fields__.get('title')) == 'title'


def declared_name(model):
    """A root model whose own `name` field takes the standard column's place."""
    field = model.__declared_fields__.get('name')
    return field is not None and standard(field) != 'name' and 'evlet_position' not in model.__fields__


def configure(registry, child_models):
    """Check title declarations, and let people change the standard name of models that declare naming
    (once it is assigned, for a series or autoname). Without naming it stays read only. Forms show the
    name only with naming and never show the title (evlet/ui/metadata.py)."""
    for model in registry.models.values():
        if model in child_models:
            continue
        title = model.__declared_fields__.get('title')
        if title is not None and standard(title) is None and (title.kind != 'text' or not title.stored):
            raise ValueError(f'{model.__name__}.title: a declared title is the record title, so it must be a stored Text field')
        compile_title(model)
        if has_name(model):
            rules = registry.naming.get(model)
            named = rules is not None and rules.field == 'name'
            model.__fields__['name'].read_only = not named


def check_declarations(registry, child_models):
    """Applications refuse a model that declares its own `name`: it is the standard record name now.
    (Frozen classes in migration history may keep one, so this is checked here and not on the class.)"""
    for model in registry.models.values():
        if model not in child_models and declared_name(model):
            raise ValueError(f'{model.__name__}.name: "name" is the standard record name (id, name, title). Rename your '
                             f'field, for example to {model.__name__.lower()}_name or full_name, with '
                             f"RenameField('{registry.name_for(model)}', 'name', '<new name>') in an app migration.")


def compile_title(model):
    """The source of a model's title: ('field', name), ('template', parts) or None (name, then id)."""
    meta = getattr(model, 'Meta', None)
    template, field = getattr(meta, 'title', None), getattr(meta, 'title_field', None)
    fields = model.__fields__
    if template is not None:
        if not isinstance(template, str) or not TEMPLATE.search(template):
            raise ValueError(f'{model.__name__}.Meta.title is text with fields in braces, such as "{{first_name}} {{last_name}}"')
        for part in TEMPLATE.findall(template):
            if part not in fields or part in ('title',) or fields[part].kind not in TEXTUAL:
                raise ValueError(f'{model.__name__}.Meta.title: {{{part}}} must be a short field of the model')
        return ('template', template)
    if field not in (None, 'id', 'name', 'title') and field in fields and has_title(model):
        return ('field', field)
    return None


TEXTUAL = ('text', 'email', 'phone', 'url', 'select', 'sequence', 'date', 'integer', 'decimal', 'currency')


def title_sources(model):
    """The fields a standard title shows: its source field or template fields, else the name it repeats."""
    if not has_title(model):
        return set()
    source = compile_title(model)
    if source:
        return {source[1]} if source[0] == 'field' else set(TEMPLATE.findall(source[1]))
    return {'name'} if has_name(model) else set()


def title_of(model, values, record_id=None):
    """The title a record with these stored values shows; None until there is anything to show."""
    if not has_title(model):
        return values.get('title')
    source = compile_title(model)
    text = None
    if source and source[0] == 'field':
        value = values.get(source[1])
        text = None if value is None else str(value)
    elif source:
        def part(match):
            value = values.get(match.group(1))
            return '' if value is None else str(value)
        text = re.sub(r'\s+', ' ', TEMPLATE.sub(part, source[1])).strip()
    text = text or (values.get('name') if has_name(model) else None) or (None if record_id is None else str(record_id))
    return None if text is None else text[:TITLE_LENGTH]


def check_name(connection, table, model, value, record_id=None):
    """A manual or computed name: not blank, at most 140 characters and not taken by another record."""
    from .models import ValidationError, identifier
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({'name': 'Use a name'})
    if len(value) > NAME_LENGTH:
        raise ValidationError({'name': f'Use at most {NAME_LENGTH} characters'})
    found = connection.execute(f'SELECT id FROM {identifier(table)} WHERE "name" = ?', (value,)).fetchone()
    if found is not None and found[0] != record_id:
        raise ValidationError({'name': 'This name is already taken'})


def add_columns(connection, postgres, table, model):
    """Add the standard columns a record table lacks, with the unique index over names. Idempotent."""
    from .models import identifier
    framework = [key for key, field in model.__app_stored__.items() if standard(field)]
    if not framework:
        return
    if postgres:
        present = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?", (table,))}
    else:
        present = {row[1] for row in connection.execute(f'PRAGMA table_info({identifier(table)})')}
    for key in framework:
        if key not in present:
            connection.execute(f'ALTER TABLE {identifier(table)} ADD COLUMN {identifier(key)} TEXT')
    if 'name' in framework and 'name' not in present:
        if postgres:
            connection.execute(f'CREATE UNIQUE INDEX {identifier(f"evlet_{table}_name_unique")} ON {identifier(table)} ("name")')
        else:
            from .migrations import _index
            _index(connection, table, 'name', unique=True)


def fill_titles(connection, table, model):
    """Fill the standard title of records that have none, in batches."""
    from .models import identifier
    if not has_title(model):
        return
    last = 0
    while True:
        rows = connection.execute(f'SELECT * FROM {identifier(table)} WHERE "title" IS NULL AND "id" > ? ORDER BY "id" LIMIT 500',
                                  (last,)).fetchall()
        if not rows:
            return
        last = rows[-1]['id']
        records = [model._from_row(row) for row in rows]
        connection.executemany(f'UPDATE {identifier(table)} SET "title" = ? WHERE "id" = ?',
                               [(title_of(model, record._values, record.id), record.id) for record in records])


def drop_echoes(model, values, current=None):
    """Leave out a standard name or title a client sent back as it read it (a full row returned for
    saving): unchanged, or empty on a new record. Any other value stays and is checked as a write."""
    for key in ('name', 'title'):
        if key in values and standard(model.__fields__.get(key)):
            before = None if current is None else current.get(key)
            if values[key] == before or values[key] in (None, '') and before in (None, ''):
                del values[key]
    return values
