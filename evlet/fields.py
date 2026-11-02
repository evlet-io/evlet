"""Field definitions, validation, and storage conversion."""

from contextvars import ContextVar
from datetime import date, datetime, timezone
from datetime import time as TimeValue
from decimal import Context, Decimal as DecimalValue, InvalidOperation, localcontext
import json
import re
from urllib.parse import urlsplit

MISSING = object()


class Layer:
    """What one database adds to the shared model classes: instance fields ({model: {name: Field}}) and
    options Controls added to choice fields ({field: (option, ...)}) and naming series overridden in Controls
    ({model: NamingRules}). Classes and the registry from app code are never
    changed; each Database holds its own Layer and makes it active while it is in use."""

    def __init__(self, fields=None, choices=None, naming=None):
        from types import MappingProxyType
        self.fields, self.choices, self.naming = dict(fields or {}), dict(choices or {}), dict(naming or {})
        self.declared = {model: MappingProxyType({**model.__app_fields__, **extra}) for model, extra in self.fields.items()}
        self.stored = {model: MappingProxyType({name: field for name, field in declared.items() if field.stored})
                       for model, declared in self.declared.items()}


EMPTY_LAYER = Layer()
# (database, Layer) in use for this request, job or transaction; None outside them.
ACTIVE = ContextVar("evlet_layer", default=None)


def active_layer():
    current = ACTIVE.get()
    return EMPTY_LAYER if current is None else current[1]


class _CurrentUser:
    """`default=CurrentUser` fills a Text field with the signed-in user's username, or an Integer field
    with their account id, when a record is created without a value. Outside a signed-in write (an
    action input, a migration backfill) it is no value."""
    def __call__(self):
        return None

    def __repr__(self):
        return 'CurrentUser'


CurrentUser = _CurrentUser()
MAX_INTEGER = 2**63 - 1
ROUNDING = ("half_up", "half_even")
# Finite-v1 expression options a field may declare, besides compute=.
RULES = ("applies_when", "required_when", "locked_when")


def rounding_mode(value, default):
    """How a finite calculation quantizes a result to this field's scale."""
    if value is None:
        return default
    if value not in ROUNDING:
        raise ValueError("rounding must be 'half_up' or 'half_even'")
    return value


def storage_name(model):
    from .models import storage_name
    return storage_name(model)


def _operand_like(value):
    from .calc import operand_like
    return operand_like(value)


def _expr(op, left, *right):
    from .calc import operand
    return operand(op, left, *right)


def _finite_option(value, name):
    """initial= and rule options take a finite expression or a field, never text or a callable."""
    from .calc import Expr
    if value is None or isinstance(value, (Expr, Field)):
        return value
    raise ValueError(f"{name} takes a finite expression such as F('stage') == 'lost'")


class Field:
    kind = "field"
    sql_type = "TEXT"

    def __init__(self, *, required=False, default=MISSING, unique=False, index=False, validator=None,
                 label=None, help_text="", placeholder="", hidden=False, read_only=False,
                 compute=None, stored=True, query=None, fetch=None, filters=None,
                 visible_if=None, required_if=None, read_only_if=None,
                 initial=None, applies_when=None, required_when=None, locked_when=None, editable_when_locked=False):
        self.required = required
        self.default = default
        if default is CurrentUser and self.kind not in ('text', 'integer'):
            raise ValueError('default=CurrentUser fills Text (the username) or Integer (the account id) fields')
        self.unique = unique
        self.index = index
        self.validator = validator
        self.name = ""
        if label is not None and (not isinstance(label, str) or not label):
            raise ValueError("label must be nonempty text")
        if not isinstance(help_text, str) or not isinstance(placeholder, str):
            raise ValueError("Field help and placeholder must be text")
        if type(hidden) is not bool or type(read_only) is not bool:
            raise ValueError("hidden and read_only must be booleans")
        self.label, self.help_text, self.placeholder = label, help_text, placeholder
        self.hidden, self.read_only = hidden, read_only
        self.compute, self.stored, self.query, self.fetch = compute, stored, query, fetch
        self.filters = dict(filters or {})
        self.visible_if, self.required_if, self.read_only_if = visible_if, required_if, read_only_if
        if type(stored) is not bool or (not stored and (index or unique)):
            raise ValueError('Virtual fields cannot have database indexes')
        if compute is not None and query is not None:
            raise ValueError('Choose compute or query for a field')
        if fetch is not None and (not isinstance(fetch, str) or len(fetch.split('.')) != 2 or compute is not None or query is not None):
            raise ValueError('Fetch uses link_field.target_field and cannot also compute/query')
        if query is not None and (not callable(query) or not hasattr(query, '__depends__') or stored):
            raise ValueError('Queries require @depends and stored=False')
        self.initial, self.applies_when = _finite_option(initial, "initial"), _finite_option(applies_when, "applies_when")
        self.required_when = _finite_option(required_when, "required_when")
        self.locked_when = _finite_option(locked_when, "locked_when")
        if initial is not None and (compute is not None or query is not None or fetch is not None or default is not MISSING):
            raise ValueError("initial= suggests a value; it cannot be combined with compute, default, fetch or query")
        if (compute is not None or query is not None) and any(getattr(self, key) is not None for key in RULES):
            raise ValueError("Calculated fields are read only; applies_when, required_when and locked_when apply to editable fields")
        if applies_when is not None and required:
            raise ValueError("A field that may not apply cannot be required; use required_when instead")
        if required_when is not None and required_if is not None or locked_when is not None and read_only_if is not None:
            raise ValueError("Use required_when or required_if, and locked_when or read_only_if, not both")
        if type(editable_when_locked) is not bool:
            raise ValueError("editable_when_locked must be a boolean")
        if editable_when_locked and (compute is not None or query is not None):
            raise ValueError("Calculated fields are read only; editable_when_locked applies to editable fields")
        # A locked State still lets ordinary writes change this field (for example remarks).
        self.editable_when_locked = editable_when_locked
        if compute is not None or query is not None:
            self.read_only = True

    def __set_name__(self, owner, name):
        if self.name and self.name != name:
            raise ValueError("Create a separate Field instance for each field name")
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return instance._values.get(self.name)

    def __set__(self, instance, value):
        raise AttributeError("Records are snapshots; use Database.update() to save changes")

    # Finite calculations: descriptors build expressions. Field-to-field equality stays
    # identity-based so fields remain safe dictionary keys; compare two fields with F("a") == F("b").
    __hash__ = object.__hash__

    def __eq__(self, other):
        # Fields and MISSING compare by identity (dictionary and list safety). Comparing with any
        # other value, including None, builds an expression so formulas never miscompile silently;
        # plain code should use `field is None`.
        if not isinstance(other, Field) and other is not MISSING and not _operand_like(other): return NotImplemented
        return self is other if isinstance(other, Field) or other is MISSING else _expr('eq', self, other)

    def __ne__(self, other):
        if not isinstance(other, Field) and other is not MISSING and not _operand_like(other): return NotImplemented
        return self is not other if isinstance(other, Field) or other is MISSING else _expr('ne', self, other)

    def __add__(self, other): return _expr('add', self, other)
    def __radd__(self, other): return _expr('add', other, self)
    def __sub__(self, other): return _expr('sub', self, other)
    def __rsub__(self, other): return _expr('sub', other, self)
    def __mul__(self, other): return _expr('mul', self, other)
    def __rmul__(self, other): return _expr('mul', other, self)
    def __truediv__(self, other): return _expr('div', self, other)
    def __rtruediv__(self, other): return _expr('div', other, self)
    def __neg__(self): return _expr('neg', self)
    def __lt__(self, other): return _expr('lt', self, other)
    def __le__(self, other): return _expr('lte', self, other)
    def __gt__(self, other): return _expr('gt', self, other)
    def __ge__(self, other): return _expr('gte', self, other)
    def __and__(self, other): return _expr('and', self, other)
    def __rand__(self, other): return _expr('and', other, self)
    def __or__(self, other): return _expr('or', self, other)
    def __ror__(self, other): return _expr('or', other, self)
    def __invert__(self): return _expr('not', self)

    def clean(self, value):
        if value is MISSING:
            value = self.default() if callable(self.default) else self.default
        if value is MISSING or value is None:
            if self.required:
                raise ValueError("This field is required")
            return None
        value = self.validate(value)
        if self.validator is not None:
            self.validator(value)
        return value

    def validate(self, value):
        return value

    def to_storage(self, value):
        return value

    def from_storage(self, value):
        return value

    def serialize(self, value):
        return value

    # A field normally occupies one column named after it; Reference uses two.
    def columns(self, name):
        return ((name, self.sql_type),)

    def to_columns(self, value):
        return (self.to_storage(value),)

    def from_columns(self, row, name):
        return self.from_storage(row[name])

    def metadata(self):
        return {"name": self.name, "type": self.kind, "required": self.required,
                "unique": self.unique, "index": self.index}


class Text(Field):
    kind = "text"

    def __init__(self, *, max_length=255, **kwargs):
        super().__init__(**kwargs)
        if type(max_length) is not int or max_length < 1:
            raise ValueError("max_length must be a positive integer")
        self.max_length = max_length

    def validate(self, value):
        if not isinstance(value, str):
            raise ValueError("Expected text")
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("Expected valid Unicode text") from exc
        if "\x00" in value:
            raise ValueError("Text must not contain NUL characters")
        if self.required and not value.strip():
            raise ValueError("This field must not be blank")
        if len(value) > self.max_length:
            raise ValueError(f"Use at most {self.max_length} characters")
        return value

    def from_storage(self, value):
        if value is not None and type(value) is not str:
            raise ValueError("Stored text has the wrong type")
        return value

    def metadata(self):
        return {**super().metadata(), "max_length": self.max_length}


class Integer(Field):
    kind = "integer"
    sql_type = "INTEGER"
    default_rounding = "half_up"

    def __init__(self, *, minimum=None, maximum=None, rounding=None, **kwargs):
        super().__init__(**kwargs)
        self.rounding = rounding_mode(rounding, self.default_rounding)
        for bound in (minimum, maximum):
            if bound is not None and type(bound) is not int:
                raise ValueError("Integer bounds must be integers")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("minimum exceeds maximum")
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value):
        if type(value) is not int:
            raise ValueError("Expected an integer")
        if not -MAX_INTEGER - 1 <= value <= MAX_INTEGER:
            raise ValueError("Integer is outside SQLite's supported range")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"Must be at least {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"Must be at most {self.maximum}")
        return value

    def from_storage(self, value):
        if value is not None and type(value) is not int:
            raise ValueError("Stored integer has the wrong type")
        return value

    def metadata(self):
        return {**super().metadata(), "minimum": self.minimum, "maximum": self.maximum}


class Decimal(Field):
    """Exact fixed-scale decimal stored as signed 64-bit scaled integer."""

    kind = "decimal"
    sql_type = "INTEGER"
    default_rounding = "half_even"

    def __init__(self, *, scale=4, minimum=None, maximum=None, rounding=None, **kwargs):
        super().__init__(**kwargs)
        self.rounding = rounding_mode(rounding, self.default_rounding)
        from .calc import Expr
        if scale is None and (self.stored or not isinstance(self.compute, Expr)):
            raise ValueError("scale=None is only for virtual finite calculations: stored=False with compute=")
        if scale is not None and (type(scale) is not int or not 0 <= scale <= 9):
            raise ValueError("scale must be an integer between 0 and 9")
        self.scale = scale
        self.minimum = DecimalValue(str(minimum)) if minimum is not None else None
        self.maximum = DecimalValue(str(maximum)) if maximum is not None else None
        if any(bound is not None and not bound.is_finite() for bound in (self.minimum, self.maximum)):
            raise ValueError("Decimal bounds must be finite")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")

    def validate(self, value):
        if isinstance(value, bool) or not isinstance(value, (str, int, DecimalValue)):
            raise ValueError("Use a decimal string, e.g. '12.50'")
        try:
            with localcontext(Context(prec=40)) as context:
                context.prec = 40
                amount = DecimalValue(value)
                if self.scale is None:
                    if not amount.is_finite():
                        raise ValueError("Decimal is outside the supported range")
                    return amount
                if not amount.is_finite() or amount.copy_abs() > DecimalValue(MAX_INTEGER).scaleb(-self.scale):
                    raise ValueError("Decimal is outside the supported range")
                rounded = amount.quantize(DecimalValue(1).scaleb(-self.scale))
                if rounded != amount:
                    raise ValueError(f"Use at most {self.scale} decimal places")
        except InvalidOperation as exc:
            raise ValueError("Expected a valid decimal amount") from exc
        if self.minimum is not None and amount < self.minimum:
            raise ValueError(f"Must be at least {self.minimum}")
        if self.maximum is not None and amount > self.maximum:
            raise ValueError(f"Must be at most {self.maximum}")
        return rounded

    def to_storage(self, value):
        with localcontext(Context(prec=40)) as context:
            context.prec = 40
            return None if value is None else int(value.scaleb(self.scale))

    def from_storage(self, value):
        if value is not None and type(value) is not int:
            raise ValueError("Stored decimal has the wrong type")
        with localcontext(Context(prec=40)) as context:
            context.prec = 40
            return None if value is None else DecimalValue(value).scaleb(-self.scale)

    def serialize(self, value):
        if value is not None and self.scale is None:
            from .calc import parse, text
            return text(parse(value))
        return None if value is None else format(value, f".{self.scale}f")

    def metadata(self):
        return {**super().metadata(), "scale": self.scale,
                "minimum": None if self.minimum is None else str(self.minimum),
                "maximum": None if self.maximum is None else str(self.maximum)}


class Currency(Decimal):
    kind = "currency"
    default_rounding = "half_up"

    def __init__(self, *, currency=None, **kwargs):
        super().__init__(scale=2, **kwargs)
        # Display metadata only: an ISO 4217 code or the name of a field holding the code. No conversion.
        if currency is not None and (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}|[A-Za-z][A-Za-z0-9_]*", currency)):
            raise ValueError("currency takes an ISO 4217 code such as 'AED' or the name of a field holding the code")
        self.currency = currency


class Percent(Decimal):
    """A percentage with two decimal places, 0 to 100 unless other bounds are given."""
    kind = "percent"

    def __init__(self, *, minimum="0", maximum="100", **kwargs):
        super().__init__(scale=2, minimum=minimum, maximum=maximum, **kwargs)


class Boolean(Field):
    kind = "boolean"
    sql_type = "INTEGER"

    def validate(self, value):
        if type(value) is not bool:
            raise ValueError("Expected true or false")
        return value

    def to_storage(self, value):
        return None if value is None else int(value)

    def from_storage(self, value):
        if value is None:
            return None
        if type(value) is not int or value not in (0, 1):
            raise ValueError("Stored boolean must be 0 or 1")
        return bool(value)


class Date(Field):
    kind = "date"

    def validate(self, value):
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                pass
        if type(value) is not date:
            raise ValueError("Expected a date in YYYY-MM-DD format")
        return value

    def to_storage(self, value):
        return None if value is None else value.isoformat()

    serialize = to_storage

    def from_storage(self, value):
        return None if value is None else self.validate(value)


class DateTime(Field):
    kind = "datetime"

    def validate(self, value):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                pass
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Expected an ISO datetime with a timezone")
        try:
            return value.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("Datetime is outside the supported UTC range") from exc

    def to_storage(self, value):
        return None if value is None else value.isoformat(timespec="microseconds").replace("+00:00", "Z")

    serialize = to_storage

    def from_storage(self, value):
        return None if value is None else self.validate(value)


class Select(Text):
    kind = "select"

    def __init__(self, choices, **kwargs):
        if not isinstance(choices, (list, tuple)) or not choices or any(not isinstance(x, str) or not x for x in choices):
            raise ValueError("choices must be a nonempty list of strings")
        if len(set(choices)) != len(choices):
            raise ValueError("choices must be distinct")
        super().__init__(**kwargs)
        self.choices = tuple(choices)
        for choice in choices:
            Text.validate(self, choice)

    def validate(self, value):
        value = super().validate(value)
        added = active_layer().choices.get(self, ())
        if value not in self.choices and value not in added:
            raise ValueError("Choose one of: " + ", ".join((*self.choices, *added)))
        return value

    def metadata(self):
        return {**super().metadata(), "choices": list(self.choices)}


class State(Select):
    """A Select whose value changes only through its moves (and, later, engine operations).

    Ordinary writes may create a record in the default state and never set the value. `locked`
    states refuse ordinary writes to the record and its Table rows, `final` states have no moves
    out and `voidable` names the states a void (and a revise) may start from. A State with voidable
    states must have a "void" state that is locked and final.
    """
    OPTIONS = ("label", "help_text", "placeholder", "hidden", "index", "max_length")

    def __init__(self, choices, *, default=MISSING, moves=(), locked=(), final=(), voidable=(), **kwargs):
        from .declarations import Move
        if set(kwargs) - set(self.OPTIONS):
            raise ValueError("State takes choices, default, moves, locked, final, voidable and " + ", ".join(self.OPTIONS))
        if default is MISSING and isinstance(choices, (list, tuple)) and choices:
            default = choices[0]
        super().__init__(choices, default=default, required=True, read_only=True, **kwargs)
        if default not in self.choices:
            raise ValueError("A State default must be one of its states")
        groups = {}
        for name, value in (("locked", locked), ("final", final), ("voidable", voidable)):
            if isinstance(value, str) or not isinstance(value, (list, tuple)) or len(set(value)) != len(value):
                raise ValueError(f"State {name} must be a list of distinct state names")
            if set(value) - set(self.choices):
                raise ValueError(f"State {name} names an unknown state: " + ", ".join(sorted(set(value) - set(self.choices))))
            groups[name] = tuple(value)
        self.locked, self.final, self.voidable = groups["locked"], groups["final"], groups["voidable"]
        if set(self.voidable) - set(self.locked) or set(self.voidable) & set(self.final):
            raise ValueError("Voidable states must be locked and not final")
        # A void moves the record to the state named "void", which keeps it locked and ends its life.
        if self.voidable and ("void" not in self.choices or "void" not in self.locked or "void" not in self.final):
            raise ValueError('A State with voidable states needs a "void" state that is locked and final')
        if not isinstance(moves, (list, tuple)) or any(not isinstance(move, Move) for move in moves):
            raise ValueError("State moves must be a list of Move declarations")
        self.moves, pairs = tuple(moves), set()
        for move in self.moves:
            unknown = {*move.sources, move.to} - set(self.choices)
            if unknown:
                raise ValueError(f"Move {move.label!r} names an unknown state: " + ", ".join(sorted(unknown)))
            if set(move.sources) & set(self.final):
                raise ValueError(f"Move {move.label!r} leaves a final state; final states have no moves out")
            if self.voidable and move.to == "void":
                raise ValueError(f'Move {move.label!r} goes to "void"; with voidable states a record is voided only '
                                 'through void() with a reason, so the engine can reverse its effects and record it')
            for source in move.sources:
                if (source, move.to) in pairs:
                    raise ValueError(f"Declare one move from {source} to {move.to}")
                pairs.add((source, move.to))

    def move(self, current, to):
        """The declared move from the current state to `to`, or None."""
        return next((move for move in self.moves if current in move.sources and move.to == to), None)

    def metadata(self):
        return {**super().metadata(), "state": True}


class Email(Text):
    kind = "email"

    def __init__(self, **kwargs):
        super().__init__(max_length=254, **kwargs)

    def validate(self, value):
        value = super().validate(value)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Expected an email address")
        return value


class URL(Text):
    kind = "url"

    def __init__(self, **kwargs):
        super().__init__(max_length=2048, **kwargs)

    def validate(self, value):
        value = super().validate(value)
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in ("http", "https") and parsed.hostname and not re.search(r"\s", value)
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Expected an absolute http or https URL")
        return value


class LongText(Text):
    kind = "longtext"

    def __init__(self, *, max_length=65535, **kwargs):
        super().__init__(max_length=max_length, **kwargs)


class Link(Integer):
    kind = "link"

    def __init__(self, target, **kwargs):
        super().__init__(minimum=1, **kwargs)
        self.target = target

    def metadata(self):
        target = self.target if isinstance(self.target, str) else storage_name(self.target)
        return {**super().metadata(), "target": target}


class Phone(Text):
    """Digits, spaces, +, - and parentheses; a number starting with + is stored in E.164 form."""
    kind = "phone"

    def __init__(self, *, max_length=32, **kwargs):
        super().__init__(max_length=max_length, **kwargs)

    def validate(self, value):
        value = super().validate(value).strip()
        if not re.fullmatch(r"\+?[0-9 ()-]+", value) or "+" in value[1:]:
            raise ValueError("Use digits, spaces, +, - and parentheses only")
        digits = re.sub(r"[^0-9]", "", value)
        if not 3 <= len(digits) <= 15:
            raise ValueError("A phone number has 3 to 15 digits")
        return "+" + digits if value.startswith("+") else value


class Time(Field):
    """A time of day, HH:MM:SS, without a timezone."""
    kind = "time"

    def validate(self, value):
        if isinstance(value, str) and re.fullmatch(r"[0-9]{2}:[0-9]{2}(:[0-9]{2})?", value):
            try:
                value = TimeValue.fromisoformat(value)
            except ValueError:
                pass
        if type(value) is not TimeValue or value.tzinfo is not None:
            raise ValueError("Expected a time in HH:MM:SS format without a timezone")
        if value.microsecond:
            raise ValueError("Use whole seconds")
        return value

    def to_storage(self, value):
        return None if value is None else value.isoformat(timespec="seconds")

    serialize = to_storage

    def from_storage(self, value):
        return None if value is None else self.validate(value)


DURATION = re.compile(r"P(?:([0-9]{1,9})D)?(?:T(?:([0-9]{1,9})H)?(?:([0-9]{1,9})M)?(?:([0-9]{1,9})S)?)?")


def duration_seconds(value):
    """Whole seconds from an ISO 8601 duration such as PT1H30M or P2DT4H, or None."""
    match = DURATION.fullmatch(value) if isinstance(value, str) else None
    if match is None or not any(match.groups()) or value.endswith("T"):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def duration_text(seconds):
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    clock = "".join(f"{amount}{unit}" for amount, unit in ((hours, "H"), (minutes, "M"), (seconds, "S")) if amount)
    return ("P" + (f"{days}D" if days else "") + ("T" + clock if clock else "")) if days or clock else "PT0S"


class Duration(Integer):
    """Whole seconds stored as an integer; serialized as ISO 8601 (PT1H30M). Accepts that format or seconds."""
    kind = "duration"

    def __init__(self, *, minimum=0, **kwargs):
        super().__init__(minimum=minimum, **kwargs)

    def validate(self, value):
        if isinstance(value, str):
            seconds = duration_seconds(value)
            if seconds is None:
                raise ValueError("Use an ISO 8601 duration such as PT1H30M, or whole seconds")
            value = seconds
        return super().validate(value)

    def serialize(self, value):
        return None if value is None else duration_text(value) if value >= 0 else "-" + duration_text(-value)


TAG_LIMIT, TAG_LENGTH = 20, 40


class Tags(Field):
    """Up to 20 distinct short labels stored as a JSON array; the contains filter matches one tag.

    A text value is split on commas, so a plain text input can edit the list.
    """
    kind = "tags"

    def validate(self, value):
        if isinstance(value, str):
            value = [part for part in value.split(",") if part.strip()]
        if not isinstance(value, (list, tuple)):
            raise ValueError("Expected a list of tags")
        if len(value) > TAG_LIMIT:
            raise ValueError(f"Use at most {TAG_LIMIT} tags")
        tags, seen = [], set()
        for tag in value:
            tag = Tags.tag(tag)
            if tag.casefold() in seen:
                raise ValueError(f"Remove the repeated tag {tag}")
            seen.add(tag.casefold())
            tags.append(tag)
        if self.required and not tags:
            raise ValueError("This field is required")
        return tuple(tags)

    @staticmethod
    def tag(value):
        if not isinstance(value, str):
            raise ValueError("Each tag must be text")
        value = value.strip()
        # Quotes, backslashes, commas and control characters are refused so a stored array can be matched exactly.
        if not value or len(value) > TAG_LENGTH or re.search(r'["\\,\x00-\x1f\x7f]', value):
            raise ValueError(f"Each tag has 1 to {TAG_LENGTH} characters and no quotes, backslashes or commas")
        return value

    @staticmethod
    def encode(tag):
        return json.dumps(tag, ensure_ascii=False)

    def to_storage(self, value):
        return None if value is None else "[" + ",".join(Tags.encode(tag) for tag in value) + "]"

    def from_storage(self, value):
        if value is None:
            return None
        if type(value) is not str:
            raise ValueError("Stored tags have the wrong type")
        try:
            tags = json.loads(value)
        except ValueError as exc:
            raise ValueError("Stored tags are not a JSON array") from exc
        return tuple(tags) if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags) else self.validate(tags)

    def serialize(self, value):
        return None if value is None else list(value)


class Reference(Field):
    """A link to a record of any registered model, stored as <name>_model and <name>_id; no SQL foreign key.

    The value is {"model": "<storage name>", "id": <id>}. Writes check that the record exists.
    index=True creates one index over both columns, (<name>_model, <name>_id).
    """
    kind = "reference"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.unique:
            raise ValueError("Reference fields cannot be unique")

    def validate(self, value):
        if isinstance(value, (list, tuple)) and len(value) == 2:
            value = {"model": value[0], "id": value[1]}
        if not isinstance(value, dict) or set(value) != {"model", "id"}:
            raise ValueError('Expected {"model": "<name>", "id": <id>}')
        model, record_id = value["model"], value["id"]
        if not isinstance(model, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", model):
            raise ValueError("Use a registered model name")
        if type(record_id) is not int or not 1 <= record_id <= MAX_INTEGER:
            raise ValueError("Use a positive record ID")
        return (model, record_id)

    def columns(self, name):
        return ((name + "_model", "TEXT"), (name + "_id", "INTEGER"))

    def to_columns(self, value):
        return (None, None) if value is None else value

    def from_columns(self, row, name):
        model, record_id = row[name + "_model"], row[name + "_id"]
        if model is None and record_id is None:
            return None
        if type(model) is not str or type(record_id) is not int:
            raise ValueError("Stored reference has the wrong type")
        return (model, record_id)

    def to_storage(self, value):
        raise ValueError("Reference fields use two columns")

    def serialize(self, value):
        return None if value is None else {"model": value[0], "id": value[1]}
