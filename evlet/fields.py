"""Field definitions, validation, and storage conversion."""

from datetime import date, datetime, timezone
from decimal import Context, Decimal as DecimalValue, InvalidOperation, localcontext
import re
from urllib.parse import urlsplit

MISSING = object()
MAX_INTEGER = 2**63 - 1


class Field:
    kind = "field"
    sql_type = "TEXT"

    def __init__(self, *, required=False, default=MISSING, unique=False, index=False, validator=None):
        self.required = required
        self.default = default
        self.unique = unique
        self.index = index
        self.validator = validator
        self.name = ""

    def __set_name__(self, owner, name):
        if self.name and self.name != name:
            raise ValueError("Create a separate Field instance for each field name")
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return instance._values[self.name]

    def __set__(self, instance, value):
        raise AttributeError("Records are snapshots; use Database.update() to save changes")

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
        if self.required and not value.strip():
            raise ValueError("This field must not be blank")
        if len(value) > self.max_length:
            raise ValueError(f"Use at most {self.max_length} characters")
        return value

    def metadata(self):
        return {**super().metadata(), "max_length": self.max_length}


class Integer(Field):
    kind = "integer"
    sql_type = "INTEGER"

    def __init__(self, *, minimum=None, maximum=None, **kwargs):
        super().__init__(**kwargs)
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

    def metadata(self):
        return {**super().metadata(), "minimum": self.minimum, "maximum": self.maximum}


class Decimal(Field):
    """Exact fixed-scale decimal stored as signed 64-bit scaled integer."""

    kind = "decimal"
    sql_type = "INTEGER"

    def __init__(self, *, scale=4, minimum=None, maximum=None, **kwargs):
        super().__init__(**kwargs)
        if type(scale) is not int or not 0 <= scale <= 9:
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
                if not amount.is_finite() or abs(amount) > DecimalValue(MAX_INTEGER).scaleb(-self.scale):
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
        with localcontext(Context(prec=40)) as context:
            context.prec = 40
            return None if value is None else DecimalValue(value).scaleb(-self.scale)

    def serialize(self, value):
        return None if value is None else format(value, f".{self.scale}f")

    def metadata(self):
        return {**super().metadata(), "scale": self.scale,
                "minimum": None if self.minimum is None else str(self.minimum),
                "maximum": None if self.maximum is None else str(self.maximum)}


class Currency(Decimal):
    kind = "currency"

    def __init__(self, **kwargs):
        super().__init__(scale=2, **kwargs)


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
        if value not in self.choices:
            raise ValueError("Choose one of: " + ", ".join(self.choices))
        return value

    def metadata(self):
        return {**super().metadata(), "choices": list(self.choices)}


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
        return {**super().metadata(), "target": self.target.__name__.lower()}
