"""Composite and unique indexes declared in Meta.indexes, on normal and ledger models.

    class Rate(Model):
        unit = Text(max_length=10, required=True)
        on_date = Date(required=True)
        kind = Select(('spot', 'average'), required=True)

        class Meta:
            indexes = [(('unit', 'on_date', 'kind'), {'unique': True}), ('on_date',)]

An entry is a tuple of field names, or (field names, {"unique": True}). The database enforces a unique
entry; a write that breaks one raises ValidationError naming the fields. The
saved schema records the entries (a ledger model under "ledger", a normal model under "indexes"), the
schema check compares them with the database, and AddIndex(table, (fields...), unique=...) adds one.
"""


from .models import ValidationError



def normalize(name, value, fields):
    """Meta.indexes as a tuple of (field names, unique). `fields` are the model's stored fields; the
    row `id` may be named too, such as ("serial", "id") to read the latest row per serial."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{name}: Meta.indexes is a list such as [("member", "on_date"), (("member",), {{"unique": True}})]')
    result = []
    for entry in value:
        unique = False
        if isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[0], (list, tuple)) and isinstance(entry[1], dict):
            entry, extra = entry
            if set(extra) - {'unique'} or type(extra.get('unique', False)) is not bool:
                raise ValueError(f'{name}: Meta.indexes options are {{"unique": True or False}}')
            unique = extra.get('unique', False)
        if not isinstance(entry, (list, tuple)) or not entry or any(not isinstance(key, str) for key in entry):
            raise ValueError(f'{name}: each Meta.indexes entry names one or more fields, such as ("member", "on_date")')
        if len(set(entry)) != len(entry):
            raise ValueError(f'{name}: Meta.indexes entry {tuple(entry)} names a field twice')
        for key in entry:
            if key == 'id':
                continue
            if key not in fields:
                raise ValueError(f'{name}: Meta.indexes names {key}, which is not a field of the model')
            field = fields[key]
            if not field.stored or field.kind == 'richtext' or len(field.columns(key)) != 1:
                raise ValueError(f'{name}: Meta.indexes names {key}, a {type(field).__name__} field, which has no single '
                                 'stored column to index')
        item = (tuple(entry), unique)
        if any(other[0] == item[0] for other in result):
            raise ValueError(f'{name}: Meta.indexes lists {tuple(entry)} twice')
        result.append(item)
    return tuple(result)


def declared(model, *, unique=None):
    """The column tuples of a model's Meta.indexes; with unique=True only the unique ones."""
    return {covered for covered, is_unique in getattr(model, '__indexes__', ()) if unique is None or is_unique}


def schema(model):
    """Meta.indexes as the saved schema records them."""
    return [{'fields': list(fields), 'unique': unique} for fields, unique in getattr(model, '__indexes__', ())]


def saved(entry):
    """The index list of a saved schema's model entry: under "ledger" for a ledger model, else "indexes"."""
    return entry['ledger']['indexes'] if 'ledger' in entry else entry.get('indexes', [])


def of(entry):
    """(fields, unique) pairs from a saved index list."""
    return {(tuple(item['fields']), item['unique']) for item in entry or ()}


def violated(registry, table, columns):
    """ValidationError for a unique violation of `columns` of `table` (from the connection's integrity_failure)
    that broke a unique Meta.indexes entry, else None. The values are never repeated."""
    model = registry.models.get(table) if table else None
    if model is None or columns not in declared(model, unique=True):
        return None
    # A Child row's owner field is the framework's; the message speaks of the record that owns the rows.
    internal = set(getattr(model, '__internal_fields__', ()))
    named = [key for key in columns if key != 'id' and key not in internal] or ['id']
    fields = getattr(model, '__declared_fields__', {})
    labels = [getattr(fields.get(key), 'label', None) or key.replace('_', ' ') for key in named]
    together = labels[0] if len(labels) == 1 else ', '.join(labels[:-1]) + ' and ' + labels[-1]
    owner = 'Another row of this record' if internal & set(columns) else 'Another record'
    message = f'{owner} already has the same {together}'
    return ValidationError({key: message for key in named})


def entry(model):
    """What a normal model's saved schema entry adds for Meta.indexes: nothing when it declares none, so
    definitions saved before Meta.indexes worked on normal models stay the same."""
    if getattr(model, '__ledger__', False) or not getattr(model, '__indexes__', ()):
        return {}
    return {'indexes': schema(model)}
