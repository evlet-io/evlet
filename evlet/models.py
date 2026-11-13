"""Explicit application registries and validated record snapshots."""

import re
from types import MappingProxyType

from .fields import ACTIVE, Field, Image, Link, MISSING, RULES, Reference, State, active_layer
from .declarations import Table, ReactiveQuery, DEFAULT_TABLE_ROWS
from .identity import RecordId
from .naming import standard
from .audit import COLUMNS as AUDIT


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


def revises_field():
    """The field the engine adds to a revisable model: an indexed Link to the same model, read only, set
    only when a revise creates the copy. The model class sets its target once it exists.

    An existing table needs a migration to gain it, like any field:
    AddField('<model>', 'revises', Link('<model>', index=True)).
    """
    field = Link(None, label='Revises', read_only=True, index=True)
    field.engine_revises = True
    return field


def unique_and_required(name, fields, tables):
    """A revision clears unique values, so a revisable document (rows included) cannot require one."""
    for key, field in fields.items():
        if field.unique and field.required:
            raise ValueError(f'{name}.{key} is unique and required, but a revision clears unique values; '
                             'make it optional or not unique')
    for table in tables.values():
        child = table.child
        if isinstance(child, type):
            unique_and_required(child.__name__, child.__declared_fields__, child.__tables__)


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


def closes(model):
    """Whether a model declares before_close(self, to)."""
    import inspect
    return inspect.isfunction(getattr(model, 'before_close', None))


def declared_hooks(cls, name, meta, fields, tables, row):
    """Check derive() with Meta.derives and before_close(self, to); returns the names derive() writes.

    derive(self) builds stored values that formulas cannot express. It runs on every write and preview
    of the document, before calculations, and may assign only the fields, Tables and "table.field" row
    fields Meta.derives names;
    callers cannot write those (docs/model-engine.md#derived-values)."""
    import inspect
    derive = getattr(cls, 'derive', None)
    derives = getattr(meta, 'derives', ()) if meta is not None else ()
    if isinstance(derives, str) or not isinstance(derives, (tuple, list)):
        raise ValueError(f'{name}: Meta.derives is a tuple of field and Table names')
    derives = tuple(derives)
    if not inspect.isfunction(derive):
        if derives:
            raise ValueError(f'{name}: Meta.derives needs a derive(self) method that sets them')
        derive = None
    elif not derives:
        raise ValueError(f'{name}.derive: name the fields and Tables it sets in Meta.derives')
    if derive is not None and len(inspect.signature(derive).parameters) != 1:
        raise ValueError(f'{name}.derive takes only self')
    if derive is not None and row:
        raise ValueError(f'{name}: derive() belongs to the document; a Table row cannot declare it')
    if len(set(derives)) != len(derives):
        raise ValueError(f'{name}: Meta.derives names each field or Table once')
    from .calc import is_finite

    def source(owner, key, field):
        if not field.stored or field.compute is not None or field.query is not None or is_finite(field) or \
                isinstance(field, State) or field.kind == 'sequence' or standard(field) or getattr(field, 'engine_revises', False):
            raise ValueError(f'{owner}.{key}: derive() sets stored source fields; calculated, State, Sequence and '
                             'standard fields have their own writers')
    for key in derives:
        if not isinstance(key, str):
            raise ValueError(f'{name}: Meta.derives is a tuple of field and Table names')
        if key.count('.') == 1:
            # "table.field": a field on every row of a Table that callers edit.
            table_name, row_field = key.split('.')
            table = tables.get(table_name)
            if table is None or not isinstance(table.child, type):
                raise ValueError(f'{name}: Meta.derives names {key!r}, but {table_name!r} is not a Table of the model')
            if table_name in derives:
                raise ValueError(f'{name}: Meta.derives names the Table {table_name!r}, so it sets its rows whole; '
                                 f'leave out {key!r}')
            field = table.child.__app_fields__.get(row_field)
            if field is None:
                raise ValueError(f'{name}: Meta.derives names {key!r}, which is not a field of {table.child.__name__}')
            source(table.child.__name__, row_field, field)
            continue
        field = fields.get(key)
        if key in tables:
            if getattr(tables[key], 'sections', False):
                raise ValueError(f'{name}: {key} has sections=True, so derive() cannot build it; '
                                 'section rows belong to Tables people edit')
            continue
        if field is None:
            raise ValueError(f'{name}: Meta.derives names {key!r}, which is not a field or Table of the model')
        source(name, key, field)
    close = getattr(cls, 'before_close', None)
    if inspect.isfunction(close) and len(inspect.signature(close).parameters) != 2:
        raise ValueError(f'{name}.before_close takes self and to')
    if inspect.isfunction(close) and row:
        raise ValueError(f'{name}: before_close() belongs to the document; a Table row cannot declare it')
    return derives


def refuse_finite_expressions(name, fields, tables, prefix=''):
    """Slips, Report filter forms and Dialogs run the string rules and have no finite plan yet, so a finite
    expression on them (F(...) in compute, initial or a *_when rule) would be ignored or fail when the form is
    checked. Refuse it when the form is declared, naming the field, including fields of its table rows."""
    from .calc import uses_finite
    for key, field in fields.items():
        if uses_finite(field):
            raise ValueError(f"{name}.{prefix}{key}: dialogs, reports and slips do not run finite expressions yet; "
                             "use the string rules visible_if, required_if, read_only_if and compute='...' for now")
    for key, table in tables.items():
        child = table.child
        refuse_finite_expressions(name, child.__app_fields__, child.__tables__, f'{prefix}{key}.')


class ModelType(type):
    def __new__(mcls, name, bases, namespace):
        fields = {}
        tables = {}
        sources = {}
        for base in bases:
            fields.update(getattr(base, "__app_fields__", {}))
            tables.update(getattr(base, '__tables__', {}))
            sources.update(getattr(base, '__sources__', {}))
        # Meta.revisable = True: the engine adds `revises`, a Reference to the record a revision replaced.
        meta = namespace['Meta'] if 'Meta' in namespace else next((base.Meta for base in bases if hasattr(base, 'Meta')), None)
        # Record pages have no tabs since 0.4: the old options fail here, with where to go instead.
        if 'Meta' in namespace:
            from .ui.metadata import refuse_removed
            refuse_removed(name, namespace['Meta'])
        revisable = getattr(meta, 'revisable', False)
        if revisable not in (True, False):
            raise ValueError(f'{name}: Meta.revisable is True or False')
        for reserved in (('revises', 'revised_by') if revisable else ()):
            inherited = fields.get(reserved)
            if reserved in namespace or inherited is not None and not getattr(inherited, 'engine_revises', False):
                raise ValueError(f'{name}.{reserved} is reserved: a revisable model gets revises from the engine '
                                 'and shows revised_by in its details')
        if revisable:
            # Each revisable class gets its own field, so a subclass's revisions link to its own table.
            namespace = {**namespace, 'revises': revises_field()}
        # Meta.ledger = True: a lean table trusted code writes (evlet/ledger.py).
        from . import ledger as ledgers
        ledger = ledgers.declared(name, namespace, bases)
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
            ledger_options = ledgers.check_class(name, namespace, bases, fields, tables, sources)
            for key in [key for key, field in fields.items() if getattr(field, 'engine_timestamp', None)]:
                del fields[key]
            added = ledgers.timestamp_fields(ledger_options[1])
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
        if getattr(cls, '__transient__', False) and any(getattr(table, 'sections', False) for table in tables.values()):
            raise ValueError(f'{name}: section rows (Table(..., sections=True)) are for Tables of saved records, '
                             'not dialogs, reports or slips')
        if getattr(cls, '__transient__', False):
            refuse_finite_expressions(name, fields, tables)
        cls.__sources__ = MappingProxyType(sources)
        states = [key for key, value in fields.items() if isinstance(value, State)]
        if len(states) > 1:
            raise ValueError(f"{name} declares more than one State: " + ", ".join(states))
        # The State field's name; its value changes only through moves, so the model is a document.
        cls.__state__ = states[0] if states else None
        if revisable and (not states or not fields[states[0]].voidable):
            raise ValueError(f'{name}: Meta.revisable needs a State with voidable states, because a revise voids the original')
        cls.__revisable__ = revisable
        cls.__ledger__ = ledger
        # Meta.image names the Image field shown as the record's avatar or thumbnail.
        image = getattr(meta, 'image', None)
        if image is not None and (type(image) is not str or not isinstance(fields.get(image), Image)):
            images = [key for key, value in fields.items() if isinstance(value, Image)]
            raise ValueError(f'{name}.Meta.image: name an Image field of {name}, not {image!r}'
                             + (f' (it has {", ".join(images)})' if images else '; it declares none'))
        cls.__image__ = image
        if ledger:
            cls.__append_only__, cls.__timestamps__, cls.__indexes__ = ledger_options
        elif getattr(cls, '__row__', False) and '__internal_fields__' not in namespace and 'evlet_position' not in fields:
            # A Child row class gets its owner field when a Table registers it, so its entries may name that
            # field; the generated row class checks them (evlet/indexes.py).
            cls.__indexes__ = ()
        else:
            from .indexes import normalize
            cls.__indexes__ = normalize(name, getattr(meta, 'indexes', ()) if meta is not None else (), fields)
        if revisable:
            fields['revises'].target = cls
            unique_and_required(name, fields, tables)
        cls.__derives__ = declared_hooks(cls, name, meta, fields, tables, is_row_table(namespace, bases, fields))
        from .lifecycle import configure_model
        configure_model(cls)
        # Meta.journal = Journal(...) makes a document, so every write goes through the journaled pipeline.
        journaled = False
        if hasattr(meta, 'journal'):
            from .journal import Journal
            journaled = isinstance(meta.journal, Journal)
        if ledger:
            pass  # Trusted code writes ledger rows through the session; nothing is declarative.
        elif getattr(cls, "confirmable", False) or getattr(cls, "track_changes", False) or journaled or states or sources or tables or 'permissions' in namespace or cls.__derives__ or closes(cls) or any(field.kind == 'sequence' for field in fields.values()) or any(hasattr(value, '__action__') for value in namespace.values()) or any(field.compute is not None or not field.stored or getattr(field, 'initial', None) is not None
                or any(getattr(field, key, None) is not None for key in RULES) for field in fields.values()):
            cls.__declaration_mode__ = True
        reserved = {'context', 'parent', 'source', 'key', 'read', 'values', 'stored_values'}
        if isinstance(fields.get('source'), Reference) and not tables:
            # Effect targets declare source = Reference(); a flat record has no dialog source to shadow.
            reserved.discard('source')
        clashes = sorted((set(fields) | set(tables) | set(sources)) & reserved) if getattr(cls, '__declaration_mode__', False) else ()
        if clashes:
            owned = ', '.join(f'{name}.{field}' for field in clashes)
            quoted = ', '.join(f'"{field}"' for field in clashes)
            raise ValueError(f'{owned}: {quoted} {"is" if len(clashes) == 1 else "are"} reserved on documents '
                             f'({", ".join(sorted(reserved))}); rename the field')
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
    class Meta:
        pass

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
        self.validate()

    def validate(self):
        """Override for cross-field rules; raise ValidationError with field errors."""

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


class Registry:
    def __init__(self, *models):
        from .fields import Integer
        from .business import ChildTable
        from .calc import CalcError, LIMITS, uses_finite, root_language
        self.children = []
        for model in models:
            if isinstance(model, type) and issubclass(model, Child):
                raise ValueError(f'{model.__name__} is a Child row model. Declare row models inside their document with Table(...)')
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
            # Row fields the document's derive() sets ("table.field" in Meta.derives) are refused to callers.
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
        from .meaning import meaning_for
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
        # Move guards and action availability, compiled once per root document.
        from .states import compile_guards, kept_plan
        self.guards, self.guard_plans, self.guard_root_plans = compile_guards(self, models, plans, child_models)
        # Ordinary writes on a locked record evaluate rules over the results it keeps.
        self.kept_plans = MappingProxyType({root: kept_plan(plan) for root, plan in plans.items() if root.__state__ is not None or getattr(root, 'confirmable', False)})
        # Document numbers: {model: {field name: Sequence}}.
        from .sequences import compile_sequences
        self.sequences = compile_sequences(self, models, plans, child_models)
        # Naming series ({model: NamingRules}); Controls replaces an entry when its series are overridden.
        from .sequences import compile_namings
        self.naming = compile_namings(models, child_models)
        from .naming import configure
        configure(self, child_models)
        from .measures import compile_declared
        self.measures = MappingProxyType(compile_declared(self, models))
        # Cards at the top of a compact record page: derived fields and per-record measures (evlet/meaning.py).
        from .meaning import cards_for
        self.cards = MappingProxyType(cards_for(self, models, child_models))
        # Record layouts with Main, Related, Html, Chart or Table blocks (Meta.layout, evlet/ui/metadata.py).
        from .ui.metadata import compile_layouts
        self.layouts = MappingProxyType(compile_layouts(self))
        # Rows documents post to other models (Meta.effects); the targets' rows are read only.
        from .effects import compile_effects
        self.effects, self.effect_targets = compile_effects(self, models, child_models)
        # What a posted record's stored results read, which its actions and moves may no longer change.
        from .states import posted_reads
        self.posted_reads = MappingProxyType({model: posted_reads(self, model, plans.get(model)) for model in self.effects})
        # Record histories: {model: Journal} (evlet/journal.py).
        from .journal import compile_journals
        self.journals = compile_journals(models, child_models, self.effects)
        # What callers reach of each model ({model: Meta.api}, evlet/permissions.py) and which stored date
        # places a record in a period that a lock can close ({model: Period}, evlet/periods.py).
        from .permissions import compile_api
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
        """The finite plan governing a model's document, or None for legacy calculation."""
        return self.plans.get(self.root_of(model))

    def name_for(self, model):
        name = storage_name(model)
        if self.models.get(name) is not model:
            raise ValueError("Model does not belong to this registry")
        return name

    def resolve(self, value):
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
        from .ledger import schema as ledger_schema
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


def resolve_sources(registry, definition):
    from .query import SqlQuery
    for field in definition.__declared_fields__.values():
        if isinstance(field, Link):
            field.target = registry.resolve(field.target)
            for name in field.filters:
                if name not in field.target.__fields__: raise ValueError('Link filters must name stored target fields')
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
    for table in definition.__tables__.values(): resolve_sources(registry, table.child)
