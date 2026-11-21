"""Explicit application registries and validated record snapshots."""

import re
from dataclasses import dataclass
from types import MappingProxyType

from .fields import ACTIVE, Field, Image, Link, MISSING, RULES, Reference, active_layer, check_help_references, help_references
from .declarations import STATE_FIELD, Table, ReactiveQuery, DEFAULT_TABLE_ROWS, Ref
from .identity import RecordId
from .naming import standard
from .audit import COLUMNS as AUDIT

# Steps of building a model class (the first four) and a Registry (the rest) that higher layers own,
# in the order they run. Each module registers its own with @build_step when it is imported, and
# evlet/__init__.py imports them all, so they are in place before any app model is defined.
BUILD_ORDER = ('ledger_declared', 'ledger_class', 'statemanager', 'activity_declared',
               'meaning', 'guards', 'kept_plan', 'sequences', 'namings', 'measures', 'cards', 'posted_reads',
               'activities', 'api', 'ledger_schema')
BUILD_STEPS = {}


def build_step(name):
    """Register the decorated function as the build step `name` of BUILD_ORDER."""
    if name not in BUILD_ORDER:
        raise ValueError(f'Unknown model build step: {name}')

    def register(function):
        BUILD_STEPS[name] = function
        return function
    return register


def step(name):
    if name not in BUILD_STEPS:
        raise RuntimeError(f'The model build step {name} is not registered: import evlet before defining models')
    return BUILD_STEPS[name]


def identifier(name):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name.lower().startswith("sqlite_"):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return '"' + name + '"'


def storage_name(model):
    # A field may itself be called storage_name, so only a str class attribute names the table.
    for cls in model.__mro__:
        value = vars(cls).get('storage_name')
        if isinstance(value, str):
            return value
    return model.__name__.lower()


class ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{key}: {value}" for key, value in errors.items()))


# Record ids are made by the framework on every create path and never change (docs/model-engine.md#record-ids).
ASSIGNED_ID = 'The id is assigned by Evlet when the record is created and never changes; leave it out'


def refuse_id(values):
    """Refuse a caller-supplied `id` on any create or update path, with one clear message."""
    if 'id' in values:
        raise ValidationError({'id': ASSIGNED_ID})


def checked_body(body, allowed, message, *, exact=False, nonempty=False, field='_form'):
    """A JSON request body that is an object with only the allowed keys (exactly those when exact)."""
    keys = set(body) if isinstance(body, dict) else None
    if keys is None or (keys != set(allowed) if exact else keys - set(allowed)) or nonempty and not keys:
        raise ValidationError({field: message})
    return body


def declared_hooks(cls, name, meta, fields, tables, row):
    """Check derive() with what is declared derived=True; returns the names derive() writes: "field",
    "table" (it builds the rows) and "table.field" (it fills a field on the rows of a Table callers edit).

    derive(self) builds stored values that formulas cannot express. It runs on every write and preview of
    the document, after the formulas that do not read its values and before those that do; callers cannot
    write what it sets (docs/model-engine.md#derived-values)."""
    import inspect
    from .calc import is_finite
    from .declarations import State

    def source(owner, key, field):
        if not field.stored or field.compute is not None or field.query is not None or is_finite(field) or \
                isinstance(field, State) or field.kind == 'sequence' or standard(field):
            raise ValueError(f'{owner}.{key}: derive() sets stored source fields; calculated, State, Sequence and '
                             'standard fields have their own writers, so they cannot be derived=True')

    def row_fields(child):
        return [key for key, field in child.__app_fields__.items() if field.derived] if isinstance(child, type) else []
    derives = []
    for key, field in fields.items():
        if field.derived:
            source(name, key, field)
            derives.append(key)
    for key, table in tables.items():
        derived_rows = row_fields(table.child)
        if row and (table.derived or derived_rows):
            raise ValueError(f'{name}.{key}: derive() reaches one level of Table, so a row\'s own Table and its '
                             'fields cannot be derived=True')
        if table.derived:
            if derived_rows:
                raise ValueError(f'{name}.{key} is derived=True, so derive() builds its rows whole; '
                                 f'{table.child.__name__}.{derived_rows[0]} cannot also be derived=True')
            if table.sections:
                raise ValueError(f'{name}: {key} has sections=True, so derive() cannot build it; '
                                 'section rows belong to Tables people edit')
            derives.append(key)
        for field_name in derived_rows:
            source(table.child.__name__, field_name, table.child.__app_fields__[field_name])
            derives.append(key + '.' + field_name)
    derive = getattr(cls, 'derive', None)
    if row:
        # A row's derived fields are filled by its document's derive().
        if inspect.isfunction(derive):
            raise ValueError(f'{name}: derive() belongs to the document; a Table row cannot declare it')
        derives = []
    elif not inspect.isfunction(derive):
        if derives:
            raise ValueError(f'{name}.{derives[0]} is derived=True: declare a derive(self) method on {name} that sets it')
    elif not derives:
        raise ValueError(f'{name}.derive: declare the fields and Tables it sets with derived=True')
    elif len(inspect.signature(derive).parameters) != 1:
        raise ValueError(f'{name}.derive takes only self')
    for removed in ('before_close', 'changed', 'check_lifecycle'):
        if inspect.isfunction(getattr(cls, removed, None)):
            raise ValueError(f'{name}.{removed}() was removed in 0.4.6; use @validate, @state(...), @state.exit(...) or '
                             '@release (docs/states-actions.md#hooks)')
    if inspect.isfunction(vars(cls).get('validate')) and not hasattr(vars(cls)['validate'], '__evlet_hook__'):
        raise ValueError(f'{name}.validate() was removed in 0.4.6; decorate a check with @validate instead')
    return tuple(derives)


def check_search_meta(name, meta, fields):
    """Meta.search_fields and Meta.option, checked when the class is defined. view_for() checks the rest when the
    app builds its schema (each search field must be a visible short text field)."""
    if meta is None:
        return
    search = getattr(meta, 'search_fields', None)
    if search is not None:
        if not isinstance(search, (tuple, list)) or len(search) > 8 or len(set(search)) != len(search) \
                or any(type(item) is not str or item not in fields for item in search):
            raise ValueError(f'{name}.Meta.search_fields: name up to eight distinct fields of {name}, such as ("code", "email"), '
                             f'not {search!r}')
    option = getattr(meta, 'option', None)
    if option is not None:
        from .declarations import Option
        if not isinstance(option, Option):
            raise ValueError(f'{name}.Meta.option takes an Option(title=, subtext=, hook=)')
        if option.refs():
            raise ValueError(f'{name}.Meta.option: Ref() reads the form a Link is on; declare it in that Link\'s option=')


def check_totals(name, fields, tables):
    """Table(totals=...): each name is a shown field of the owning model, listed once, under one Table."""
    owners = {}
    for table_name, table in tables.items():
        for field in getattr(table, 'totals', ()):
            where = f'{name}.{table_name}: totals names {field!r}'
            if field in tables:
                raise ValueError(f'{where}, which is a Table; name fields of {name}')
            if field not in fields:
                raise ValueError(f'{where}, which is not a field of {name}')
            if fields[field].hidden:
                raise ValueError(f'{where}, which is hidden=True; a total shows on the form')
            if field in owners:
                raise ValueError(f'{where} twice' if owners[field] == table_name
                                 else f'{where}, which is already in the totals of {owners[field]}')
            owners[field] = table_name


class ModelType(type):
    def __new__(mcls, name, bases, namespace):
        fields = {}
        tables = {}
        sources = {}
        for base in bases:
            fields.update(getattr(base, "__app_fields__", {}))
            tables.update(getattr(base, '__tables__', {}))
            sources.update(getattr(base, '__sources__', {}))
        meta = namespace['Meta'] if 'Meta' in namespace else next((base.Meta for base in bases if hasattr(base, 'Meta')), None)
        # Record pages have no tabs since 0.4: the old options fail here, with where to go instead.
        if 'Meta' in namespace:
            from .ui.metadata import refuse_removed
            refuse_removed(name, namespace['Meta'])
        # Meta.ledger = True: a lean table trusted code writes (evlet/ledger.py).
        # Model and Child are built before the steps register; they declare none of what the steps check.
        steps = step if namespace.get('__module__') != __name__ else lambda name: lambda *args: False
        ledger = steps('ledger_declared')(name, namespace, bases)
        for key, value in namespace.items():
            if isinstance(value, Field):
                identifier(key)
                if key.startswith('x_'):
                    raise ValueError(f"{name}.{key}: names starting with x_ are kept for fields added on an instance")
                if "__" in key or key.lower() == "id" or key == "to_dict" or any(
                    hasattr(base, key) and key not in getattr(base, "__fields__", {})
                    for base in bases
                ):
                    raise ValueError(f"Reserved field name: {key}")
                fields[key] = value
            elif isinstance(value, ReactiveQuery):
                identifier(key)
                if key in fields or key in tables or key in ('children', 'id', 'context', 'parent', 'source'):
                    raise ValueError(f'Reserved source name: {key}')
                sources[key] = value
            elif isinstance(value, Table):
                identifier(key)
                if key in fields or key in ('children', 'id', 'context'):
                    raise ValueError(f'Reserved table name: {key}')
                tables[key] = value
            elif key in fields:
                raise ValueError(f"Inherited field {key!r} must remain a field")
        from .naming import add_standard, is_row_table
        ledger_options = None
        if ledger:
            # No standard name and title; Meta.timestamps adds created_at and modified_at.
            if is_row_table(namespace, bases, fields):
                raise ValueError(f'{name}: a Table row or form cannot be a ledger model')
            ledger_options, added = steps('ledger_class')(name, namespace, bases, fields, tables, sources)
            for key in [key for key, field in fields.items() if getattr(field, 'engine_timestamp', None)]:
                del fields[key]
            fields.update(added)
            namespace = {**namespace, **added}
        # Every record model has the standard name and title columns (evlet/naming.py); row tables do not.
        if not ledger and any(isinstance(base, ModelType) for base in bases) and not is_row_table(namespace, bases, fields):
            namespace = {**namespace, **add_standard(fields)}
        if len({key.lower() for key in fields}) != len(fields):
            raise ValueError("Field names must be distinct ignoring case")
        columns = [column.lower() for key, value in fields.items() if value.stored for column, _ in value.columns(key)]
        if len(set(columns)) != len(columns) or 'id' in columns:
            raise ValueError("Reference columns (<name>_model, <name>_id) must not collide with other fields")
        cls = super().__new__(mcls, name, bases, namespace)
        cls.__app_fields__ = MappingProxyType(fields)
        cls.__app_stored__ = MappingProxyType({key: value for key, value in fields.items() if value.stored})
        cls.__tables__ = MappingProxyType(tables)
        check_totals(name, fields, tables)
        if getattr(cls, '__transient__', False) and any(getattr(table, 'sections', False) for table in tables.values()):
            raise ValueError(f'{name}: section rows (Table(..., sections=True)) are for Tables of saved records, '
                             'not dialogs, reports or slips')
        cls.__sources__ = MappingProxyType(sources)
        from .declarations import State
        states = [key for key, value in fields.items() if isinstance(value, State)]
        if len(states) > 1:
            raise ValueError(f"{name} declares more than one State: " + ", ".join(states))
        # The State field is always docstate, so lists, boards, rules and stored settings name it the same way.
        if states and states[0] != STATE_FIELD:
            raise ValueError(f'{name}.{states[0]} is a State: name the State field {STATE_FIELD} '
                             '(rename it with a migration; docs/states-actions.md)')
        if STATE_FIELD in fields and not states:
            raise ValueError(f'{name}.{STATE_FIELD} is reserved for the State field: declare {STATE_FIELD} = State(...) '
                             'or rename the field (docs/states-actions.md)')
        if states and fields[STATE_FIELD].label is None:
            fields[STATE_FIELD].label = 'Status'
        # The State field's name; its value changes only through moves, so the model is a document.
        cls.__state__ = states[0] if states else None
        for removed in ('confirmable', 'track_changes'):
            if namespace.get(removed):
                raise ValueError(f'{name}: {removed} was removed in 0.4.6; declare a State with rules and a release step '
                                 '(docs/states-actions.md)')
        cls.__ledger__ = ledger
        # Meta.abstract = True: a base other models inherit; read from the class's own Meta only, never inherited.
        own_meta = namespace.get('Meta')
        cls.__abstract__ = bool(isinstance(own_meta, type) and vars(own_meta).get('abstract', False))
        # Meta.image names the Image field shown as the record's avatar or thumbnail.
        image = getattr(meta, 'image', None)
        if image is not None and (type(image) is not str or not isinstance(fields.get(image), Image)):
            images = [key for key, value in fields.items() if isinstance(value, Image)]
            raise ValueError(f'{name}.Meta.image: name an Image field of {name}, not {image!r}'
                             + (f' (it has {", ".join(images)})' if images else '; it declares none'))
        cls.__image__ = image
        check_search_meta(name, meta if 'Meta' in namespace else None, fields)
        if ledger:
            cls.__append_only__, cls.__timestamps__, cls.__indexes__ = ledger_options
        elif getattr(cls, '__row__', False) and '__internal_fields__' not in namespace and 'evlet_position' not in fields:
            # A Child row class gets its owner field when a Table registers it, so its entries may name that
            # field; the generated row class checks them (evlet/indexes.py).
            cls.__indexes__ = ()
        else:
            from .indexes import normalize
            cls.__indexes__ = normalize(name, getattr(meta, 'indexes', ()) if meta is not None else (), fields)
        cls.__derives__ = declared_hooks(cls, name, meta, fields, tables, is_row_table(namespace, bases, fields))
        steps('statemanager')(cls)
        # Meta.activity = Activity(...) makes a document, so every write goes through the pipeline that records it.
        recorded = False
        if hasattr(meta, 'activity'):
            recorded = steps('activity_declared')(meta.activity)
        if ledger:
            pass  # Trusted code writes ledger rows through the session; nothing is declarative.
        elif recorded or states or sources or tables or 'permissions' in namespace or cls.__derives__ or cls.__hooks__ or any(field.kind == 'sequence' for field in fields.values()) or any(hasattr(value, '__action__') for value in namespace.values()) or any(field.compute is not None or not field.stored or getattr(field, 'initial', None) is not None
                or any(getattr(field, key, None) is not None for key in (*RULES, 'visible_when')) for field in fields.values()):
            cls.__declaration_mode__ = True
        reserved = {'context', 'parent', 'source', 'key', 'read', 'values', 'stored_values', 'before', 'is_new', 'release'}
        if isinstance(fields.get('source'), Reference) and not tables:
            # A flat record may name a Reference field source; it has no dialog source to shadow.
            reserved.discard('source')
        clashes = sorted((set(fields) | set(tables) | set(sources)) & reserved) if getattr(cls, '__declaration_mode__', False) else ()
        if clashes:
            owned = ', '.join(f'{name}.{field}' for field in clashes)
            quoted = ', '.join(f'"{field}"' for field in clashes)
            raise ValueError(f'{owned}: {quoted} {"is" if len(clashes) == 1 else "are"} reserved on documents '
                             f'({", ".join(sorted(reserved))}); rename the field')
        # Reactive help text: {field} names a field of this model; {parent.field} one of the record that owns a
        # row's Table, checked here for every Table this class declares.
        row = getattr(cls, '__row__', False)
        for field in fields.values():
            check_help_references(cls, field, fields)
            if not row and any(ref.startswith('parent.') for ref in help_references(field.help_text)):
                raise ValueError(f'{name}.{field.name}: help_text names a parent. field, but {name} is not a Table row '
                                 '(only Child rows have a parent record)')
        for table in tables.values():
            if isinstance(table.child, type) and issubclass(table.child, Model):
                for field in table.child.__app_fields__.values():
                    check_help_references(table.child, field, table.child.__app_fields__, parent=cls)
        return cls

    # The fields of a model: those its class declares, plus the instance fields (x_) of the database in
    # use (see fields.Layer). Each database keeps its own, so applications that share classes stay apart.
    @property
    def __declared_fields__(cls):
        return active_layer().declared.get(cls, cls.__app_fields__)

    @property
    def __fields__(cls):
        return active_layer().stored.get(cls, cls.__app_stored__)


class Model(metaclass=ModelType):
    # What the statemanager build step sets on every subclass (evlet/statemanager.py configure).
    __hooks__ = ()

    class Meta:
        pass

    # What @validate sees on a record built outside the document pipeline: it is new, with nothing saved before.
    is_new, before = True, None

    # A record keeps the Layer it was built with, so its instance fields read the same after the
    # transaction or request that made it has ended.
    @property
    def __declared_fields__(self):
        layer = self.__dict__.get('_layer')
        return type(self).__declared_fields__ if layer is None else layer.declared.get(type(self), type(self).__app_fields__)

    @property
    def __fields__(self):
        layer = self.__dict__.get('_layer')
        return type(self).__fields__ if layer is None else layer.stored.get(type(self), type(self).__app_stored__)

    def __getattr__(self, name):
        # Instance fields have no descriptor on the shared class.
        if name.startswith('x_') and name in self.__declared_fields__:
            return self._values.get(name)
        state = type(self).__declared_fields__.get(type(self).__state__) if type(self).__state__ else None
        if name in ('save', 'delete', 'move', 'reload') or state is not None and name in {rule.key for rule in state.rules}:
            raise ValueError('Get the record through a context to run its actions')
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name == 'id':
            raise ValidationError({'id': ASSIGNED_ID})
        if name.startswith('x_'):
            raise AttributeError("Records are snapshots; use Database.update() to save changes")
        object.__setattr__(self, name, value)

    def __init__(self, /, **values):
        self._layer = active_layer()
        refuse_id(values)
        errors = {key: f"{key} is an instance field. Build the record inside database.transaction() or database.activated()"
                  if key.startswith('x_') and ACTIVE.get() is None else "Unknown field" for key in values if key not in self.__fields__}
        cleaned = {}
        for name, field in self.__fields__.items():
            try:
                cleaned[name] = field.clean(values.get(name, MISSING))
            except ValueError as exc:
                errors[name] = str(exc)
        if errors:
            raise ValidationError(errors)
        self._values = MappingProxyType(cleaned)
        self._id = None
        # A record built outside the document pipeline (Session writes, legacy rows) runs its @validate checks here.
        for kind, _, name in type(self).__hooks__:
            if kind == 'validate':
                getattr(self, name)()

    @property
    def id(self):
        return self._id

    def to_dict(self):
        return {"id": self.id, **{
            name: field.serialize(self._values[name])
            for name, field in self.__fields__.items()
        }}

    @classmethod
    def _from_row(cls, row):
        # Stored snapshots must remain readable after business rules change.
        instance = cls.__new__(cls)
        instance._layer = active_layer()
        instance._values = MappingProxyType({
            name: field.from_columns(row, name) for name, field in cls.__fields__.items()
            # A column another process just dropped reads as empty, and so do the standard name and title
            # of a table framework step 0003 has not reached yet.
            if not (getattr(field, 'instance', None) or standard(field)) or name in row.keys()
        })
        # A ledger row's id is the plain integer the database assigned (evlet/ledger.py).
        instance._id = row["id"] if getattr(cls, '__ledger__', False) else RecordId(row["id"])
        # When and by whom the record was created and last changed (evlet/audit.py), when the row has them.
        instance._audit = tuple(row[name] for name in AUDIT) if AUDIT[0] in row.keys() else None
        return instance


def abstract_refusal(model, instead):
    return f'{model.__name__} is abstract (Meta.abstract = True) and has no table; {instead}'


@dataclass(frozen=True)
class ChildTable:
    parent: type
    name: str
    model: type
    link: str
    label: str


class Registry:
    def __init__(self, *models, extensions=()):
        from .fields import Integer
        from .calc import CalcError, LIMITS, uses_finite, root_language
        self.children = []
        for model in models:
            if isinstance(model, type) and issubclass(model, Child):
                raise ValueError(f'{model.__name__} is a Child row model. Declare row models inside their document with Table(...)')
            if isinstance(model, type) and getattr(model, '__abstract__', False):
                raise ValueError(abstract_refusal(model, 'register the models built from it'))
        expanded = list(models)

        def finite(model, seen=()):
            # Finite-v1 roots own nested tables; legacy roots keep one level.
            if model in seen:
                raise CalcError('MODEL_CONTAINMENT_CYCLE', ' -> '.join(item.__name__ for item in seen + (model,)))
            return any(uses_finite(field) for field in model.__declared_fields__.values()) or any(
                isinstance(table.child, type) and issubclass(table.child, Child) and finite(table.child, seen + (model,))
                for table in model.__tables__.values())

        def expand(model, owners, nested):
            for name, table in model.__tables__.items():
                if not isinstance(table.child, type) or not issubclass(table.child, Child):
                    raise ValueError('Tables require Child subclasses')
                if table.child in owners:
                    raise CalcError('MODEL_CONTAINMENT_CYCLE', ' -> '.join(item.__name__ for item in owners + (table.child,)))
                if table.child.__tables__ and not nested:
                    raise ValueError('Nested tables require a finite-v1 root (Meta.calculation_language = "finite-v1")')
                if table.max_rows > DEFAULT_TABLE_ROWS and not nested:
                    raise ValueError(f'{model.__name__}.{name}: max_rows above {DEFAULT_TABLE_ROWS} requires a finite-v1 root '
                                     '(Meta.calculation_language = "finite-v1")')
                if len(owners) > LIMITS['table_depth']:
                    raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'Owned tables nest deeper than {LIMITS["table_depth"]}')
                child = generate(model, name, table)
                expand(child, owners + (table.child,), nested)

        def generate(model, name, table):
            storage = table.storage_name or storage_name(model) + '_' + name
            identifier(storage)
            identifier(table.owner_field)
            if table.owner_field in table.child.__declared_fields__ or 'evlet_position' in table.child.__declared_fields__:
                raise ValueError('Child owner and order fields are framework-managed')
            child = type(table.child.__name__, (table.child,), {
                '__module__': model.__module__, 'storage_name': storage,
                '__resource_id__': getattr(model, '__resource_id__', model.__name__) + '.' + name,
                table.owner_field: Link(model, required=True, index=True, hidden=True, read_only=True),
                'evlet_position': Integer(required=True, default=0, hidden=True, read_only=True),
                '__internal_fields__': (table.owner_field, 'evlet_position'),
            })
            # Row fields the document's derive() sets (derived=True on an edited Table's rows) are refused to callers.
            child.__derives__ = tuple(key.split('.')[1] for key in getattr(model, '__derives__', ()) if key.startswith(name + '.'))
            expanded.append(child)
            self.children.append(ChildTable(model, name, child, table.owner_field, table.label or name.replace('_', ' ').title()))
            return child
        for model in models:
            if isinstance(model, type) and issubclass(model, Model) and getattr(model, '__tables__', None):
                expand(model, (model,), finite(model) or root_language(model) is not None)
        models = tuple(expanded)
        registered = {}
        resources = {}
        for model in models:
            if not isinstance(model, type) or not issubclass(model, Model) or model is Model:
                raise ValueError("Register Model subclasses")
            if getattr(model, '__transient__', False): raise ValueError('Transient forms cannot be persistent models')
            name = storage_name(model)
            identifier(name)
            if not re.fullmatch('[a-z][a-z0-9_]*', name):
                # Model names are also UI routes, which are lowercase.
                raise ValueError(f'Model and table names (storage_name or Table attribute) must use lowercase letters, digits and underscores: {name!r}')
            if name in ("schema", "auth") or name.startswith("evlet_"):
                raise ValueError("The model name is reserved for metadata")
            if name == "home":
                # Home is the sidebar's own entry (Workspace(home=...)); no model may take its name.
                raise ValueError(f"{model.__name__}: the model name home is reserved for the sidebar's Home; set storage_name")
            if set(model.__fields__) & set(AUDIT):
                raise ValueError(f"{model.__name__}: {', '.join(sorted(set(model.__fields__) & set(AUDIT)))} is reserved for the record's "
                                 "created and modified times; rename the field")
            if name in registered:
                raise ValueError(f"Duplicate model name: {name}")
            registered[name] = model
            logical = getattr(model, '__resource_id__', name)
            if logical in resources:
                raise ValueError(f'Duplicate resource identity: {logical}')
            resources[logical] = model
        for model in models:
            for field in model.__declared_fields__.values():
                if isinstance(field, Link) and isinstance(field.target, str):
                    target = resources.get(field.target) or registered.get(field.target)
                    if target is None:
                        raise ValueError(f'Unresolved Link {model.__name__}.{field.name}: {field.target}')
                    field.target = target
                if isinstance(field, Link) and getattr(field.target, '__abstract__', False):
                    raise ValueError(f'Link {model.__name__}.{field.name}: ' + abstract_refusal(field.target, 'link a model built from it'))
                if isinstance(field, Link) and field.target not in models:
                    raise ValueError(f"Link target for {model.__name__}.{field.name} is not registered")
        for model in models:
            for field in model.__declared_fields__.values():
                check_currency_source(model, field)
        from .ui.metadata import view_for
        for model in models:
            view_for(model)
        self.models = MappingProxyType(registered)
        self.resources = MappingProxyType(resources)
        for model in models:
            resolve_sources(self, model)
        # Key fields, reverse Links and attachment limits from each Meta (evlet/meaning.py).
        meaning_for = step('meaning')
        self.meaning = meaning_for(self)
        from .reactivity import graph
        child_models = {child.model for child in self.children}
        from .calc import compile_plan, formula_hash
        plans = {}
        for model in models:
            if getattr(model, '__declaration_mode__', False) and model not in child_models:
                graph(self, model)
                plan = compile_plan(self, model)
                if plan is not None:
                    plans[model] = plan
        self.plans = MappingProxyType(plans)
        # What saved documents record: changes only when formulas or the fields they use change.
        self.formula_hashes = MappingProxyType({root: formula_hash(plan) for root, plan in plans.items()})
        # Rule conditions and action availability, compiled once per root document.
        compile_guards, kept_plan = step('guards'), step('kept_plan')
        self.guards, self.guard_plans, self.guard_root_plans = compile_guards(self, models, plans, child_models)
        # Ordinary writes on a locked record evaluate rules over the results it keeps.
        self.kept_plans = MappingProxyType({root: kept_plan(plan) for root, plan in plans.items() if root.__state__ is not None})
        # Document numbers: {model: {field name: Sequence}}.
        compile_sequences = step('sequences')
        self.sequences = compile_sequences(self, models, plans, child_models)
        # Naming series ({model: NamingRules}); Controls replaces an entry when its series are overridden.
        compile_namings = step('namings')
        self.naming = compile_namings(models, child_models)
        from .naming import configure
        configure(self, child_models)
        compile_declared = step('measures')
        self.measures = MappingProxyType(compile_declared(self, models))
        # Cards at the top of a compact record page: derived fields and per-record measures (evlet/meaning.py).
        cards_for = step('cards')
        self.cards = MappingProxyType(cards_for(self, models, child_models))
        # Record layouts (Meta.layout, evlet/ui/metadata.py); what a layout ignores is logged once here and listed by
        # Controls as problems.
        from .ui.metadata import compile_layouts
        layouts, self.layout_warnings = compile_layouts(self)
        self.layouts = MappingProxyType(layouts)
        # Model scripts (Meta.script and the hooks that name its methods, evlet/ui/scripts.py), checked at start-up.
        from .ui.scripts import compile_extensions, compile_scripts
        scripts, self.script_roots = compile_scripts(self)
        self.scripts = MappingProxyType(scripts)
        # Additions apps make to another app's models (evlet.Extend), in app install order.
        self.extensions = MappingProxyType(compile_extensions(self, extensions))
        # What a released record's stored results read, which a move out of the released set may not change.
        posted_reads = step('posted_reads')
        self.posted_reads = MappingProxyType({model: posted_reads(self, model, plans.get(model)) for model in models
                                              if model.__state__ and model.__declared_fields__[model.__state__].releases})
        # Record histories: {model: Activity} (evlet/activity.py).
        compile_activities = step('activities')
        self.activities = compile_activities(models, child_models)
        for model in models:
            if model.__state__ and model not in self.activities and any(rule.levels for rule in model.__declared_fields__[model.__state__].rules):
                raise ValueError(f'{model.__name__}: approval levels are kept in its Activity, so Meta.activity cannot be None')
        # What callers reach of each model ({model: Meta.api}, evlet/permissions.py) and which stored date
        # places a record in a period that a lock can close ({model: Period}, evlet/periods.py).
        compile_api = step('api')
        self.api = compile_api(self)
        from .periods import compile_periods
        self.periods = compile_periods(self)
        # {name: model} of the models callers reach at all: every model but those with Meta.api = None.
        self.exposed = MappingProxyType({name: model for name, model in self.models.items() if self.api[model] is not None})

    def root_of(self, model):
        owners = {child.model: child.parent for child in self.children}
        while model in owners:
            model = owners[model]
        return model

    def finite_reads(self, model, name, kind=None):
        """(scope, model, field) inputs of a finite computed field (or its initial/rule), resolved to models."""
        plan, path, owners = self.plan_for(model), [], {child.model: child for child in self.children}
        item = model
        while item in owners:
            path.insert(0, owners[item].name)
            item = owners[item].parent
        field = next(entry for entry in plan['fields'] if entry['path'] == path and entry['field'] == name and entry.get('kind') == kind)
        tables = {child.name: child.model for child in self.children if child.parent is model}
        result = []
        for scope, table, read in field['reads']:
            if scope == 'self': result.append(('self', model, read))
            elif scope == 'parent': result.append(('parent', owners[model].parent, read))
            elif scope == 'lookup': result.append(('lookup', self.models[table], read))
            elif scope != 'today': result.append((scope, tables[table], read))
        return result

    def plan_for(self, model):
        """The finite plan governing a model's document, or None for legacy calculation. A form without storage
        (a Prompt, Report filters) gets its plan on first use."""
        if getattr(model, '__transient__', False):
            forms = self.__dict__.setdefault('_form_plans', {})
            if model not in forms:
                from .calc import compile_plan
                from .reactivity import graph
                resolve_sources(self, model)
                graph(self, model)
                forms[model] = compile_plan(self, model)
            return forms[model]
        return self.plans.get(self.root_of(model))

    def derive_split(self, model):
        """(the formulas that run before derive(): a finite plan, or legacy keys in order; {value: derived name it
        reads}) for a document that declares derive(), cached per model (docs/model-engine.md#derived-values)."""
        cache = self.__dict__.setdefault('_derive_splits', {})
        if model not in cache:
            from .calc import derive_split, derived_after
            plan = self.plan_for(model)
            if plan is not None:
                cache[model] = derive_split(plan, model.__derives__)
            else:
                from .reactivity import template
                _, order, inputs, _ = template(self, model)
                after = derived_after(model.__derives__, [(key, inputs[key]) for key in order])
                cache[model] = [key for key in order if key not in after], after
        return cache[model]

    def name_for(self, model):
        if vars(model).get('__form_name__'):
            return model.__form_name__
        name = storage_name(model)
        if self.models.get(name) is not model:
            raise ValueError("Model does not belong to this registry")
        return name

    def resolve(self, value):
        if isinstance(value, type) and getattr(value, '__abstract__', False):
            raise ValueError(abstract_refusal(value, 'use a model built from it'))
        if isinstance(value, type):
            self.name_for(value)
            return value
        model = self.resources.get(value) or self.models.get(value)
        if model is None:
            raise ValueError(f'Unknown model: {value}')
        return model

    def ui_schema(self):
        from .ui.metadata import ui_schema
        from copy import deepcopy
        if not getattr(self, '_freeze_ui', False) and not all(getattr(model, '__declaration_mode__', False) for model in self.models.values()):
            return ui_schema(self)
        if not hasattr(self, '_ui_schema'):
            self._ui_schema = ui_schema(self)
        return deepcopy(self._ui_schema)

    def schema(self):
        """The stored schema app migrations work from. Instance fields (x_) belong to Controls, not to it."""
        from .indexes import entry as index_entry
        ledger_schema = step('ledger_schema')
        return {"version": 1, "models": [
            {"name": name, "label": model.__name__,
             "fields": [field.metadata() for field in model.__app_stored__.values() if not standard(field)],
             # Ledger models record their options; normal models add Meta.indexes only when they declare some.
             **({"ledger": ledger_schema(model)} if ledger_schema(model) is not None else index_entry(model))}
            for name, model in self.models.items()
        ]}


class Child(Model):
    """Owned row definition. Register it through a parent's Table declaration."""
    __row__ = True


def check_currency_source(model, field):
    """A Currency field's currency names a Text or Select field: on the record, or behind one of its Links."""
    source = field.currency_source() if hasattr(field, 'currency_source') else None
    if source is None or 'code' in source or 'setting' in source:
        return
    owner = model
    if 'link' in source:
        link = model.__declared_fields__.get(source['link'])
        if not isinstance(link, Link) or not isinstance(link.target, type):
            raise ValueError(f'{model.__name__}.{field.name}: currency {field.currency} must start with a Link field of {model.__name__}')
        owner = link.target
    holder = owner.__declared_fields__.get(source['field'])
    if holder is None or holder.kind not in ('text', 'select'):
        raise ValueError(f'{model.__name__}.{field.name}: currency must be an ISO 4217 code or a Text or Select field '
                         f'({field.currency} is not one on {owner.__name__})')


def check_option(registry, model, option, where):
    """The fields an Option reads exist: F("field") or F("link.field") on model, and each aggregate's related
    model, its Link back to model, its field and its where= fields (evlet/search.py)."""
    from .calc import Expr
    for part in (*(option.title or ()), *(option.subtext or ())):
        if isinstance(part, str):
            continue
        if isinstance(part, Expr):
            name, _, rest = part.meta['name'].partition('.')
            field = model.__fields__.get(name)
            if field is None or rest and not isinstance(field, Link):
                raise ValueError(f'{where}: {model.__name__} has no {"Link " if rest else ""}field {name}')
            if rest:
                target = registry.resolve(field.target)
                if rest not in target.__fields__:
                    raise ValueError(f'{where}: {target.__name__} has no field {rest}')
            continue
        related = registry.resolve(part.model)
        link = related.__fields__.get(part.link)
        if not isinstance(link, Link) or registry.resolve(link.target) is not model:
            raise ValueError(f'{where}: {related.__name__}.{part.link} must be a Link to {model.__name__}')
        for name in (*([part.field] if part.field else []), *(part.where or {})):
            if name not in related.__fields__ or not related.__fields__[name].stored:
                raise ValueError(f'{where}: {related.__name__} has no stored field {name}')
        if part.function == 'sum' and getattr(related.__fields__[part.field], 'sql_type', '') != 'INTEGER':
            raise ValueError(f'{where}: Sum needs a number field; {part.field} is a {related.__fields__[part.field].kind} field')


def resolve_sources(registry, definition):
    from .query import SqlQuery
    for field in definition.__declared_fields__.values():
        if isinstance(field, Link):
            field.target = registry.resolve(field.target)
            for name, value in field.filters.items():
                # "field" or "field__op" (the list route's operators): a stored field of the target, compared with a
                # literal or with a value of this form (Ref).
                if name.split('__', 1)[0] not in field.target.__fields__: raise ValueError('Link filters must name stored target fields')
                if '__' in name: SqlQuery(registry, field.target).where({name: None if isinstance(value, Ref) else value})
            if field.option is not None:
                check_option(registry, field.target, field.option, f'{definition.__name__}.{field.name}: option')
        elif field.filters: raise ValueError('Selection filters require a Link field')
        if field.fetch:
            link_name, target_name = field.fetch.split('.')
            link = definition.__declared_fields__.get(link_name)
            if not isinstance(link, Link): raise ValueError('Fetch requires a Link source')
            link.target = registry.resolve(link.target)
            target = link.target.__declared_fields__.get(target_name)
            if target is None or target.kind != field.kind: raise ValueError('Fetch target must have the same declared field type')
    for source in definition.__sources__.values():
        if isinstance(source.target, (str, type)):
            target = registry.resolve(source.target)
            source.target = target
            if set(source.fields) - set(target.__fields__): raise ValueError('Query output names must be stored target fields')
            source.fields = {name: target.__fields__[name] for name in source.fields}
            for name in source.filters: SqlQuery(registry, target).where({name: None})
    option = getattr(getattr(definition, 'Meta', None), 'option', None)
    if option is not None:
        check_option(registry, definition, option, f'{definition.__name__}.Meta.option')
    for table in definition.__tables__.values(): resolve_sources(registry, table.child)
