"""Field definitions, validation, and storage conversion."""

from decimal import Decimal, InvalidOperation

MISSING = object()
MAX_INTEGER = 2**63 - 1


class Field:
    kind = "field"
    sql_type = "TEXT"

    def __init__(self, *, required=False, default=MISSING, unique=False):
        self.required = required
        self.default = default
        self.unique = unique
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
        raise AttributeError("Records are snapshots; field mutation is not supported yet")

    def clean(self, value):
        if value is MISSING:
            value = self.default() if callable(self.default) else self.default
        if value is MISSING or value is None:
            if self.required:
                raise ValueError("This field is required")
            return None
        return self.validate(value)

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
                "unique": self.unique}


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

    def __init__(self, *, minimum=None, **kwargs):
        super().__init__(**kwargs)
        self.minimum = minimum

    def validate(self, value):
        if type(value) is not int:
            raise ValueError("Expected an integer")
        if not -MAX_INTEGER - 1 <= value <= MAX_INTEGER:
            raise ValueError("Integer is outside SQLite's supported range")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"Must be at least {self.minimum}")
        return value

    def metadata(self):
        return {**super().metadata(), "minimum": self.minimum}


class Currency(Field):
    """Exact two-decimal amounts, stored as signed 64-bit minor units."""

    kind = "currency"
    sql_type = "INTEGER"

    def __init__(self, *, minimum=None, **kwargs):
        super().__init__(**kwargs)
        self.minimum = Decimal(str(minimum)) if minimum is not None else None

    def validate(self, value):
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError("Use a decimal string for currency, e.g. '12.50'")
        try:
            amount = Decimal(value)
            if not amount.is_finite() or abs(amount) > Decimal(MAX_INTEGER) / 100:
                raise ValueError("Currency is outside the supported range")
            rounded = amount.quantize(Decimal("0.01"))
            if rounded != amount:
                raise ValueError("Use at most two decimal places")
        except InvalidOperation as exc:
            raise ValueError("Expected a valid currency amount") from exc
        if self.minimum is not None and amount < self.minimum:
            raise ValueError(f"Must be at least {self.minimum}")
        return rounded

    def to_storage(self, value):
        return None if value is None else int(value * 100)

    def from_storage(self, value):
        return None if value is None else Decimal(value).scaleb(-2)

    def serialize(self, value):
        return None if value is None else format(value, ".2f")

    def metadata(self):
        return {**super().metadata(), "scale": 2,
                "minimum": None if self.minimum is None else str(self.minimum)}


class Link(Integer):
    kind = "link"

    def __init__(self, target, **kwargs):
        super().__init__(minimum=1, **kwargs)
        self.target = target

    def metadata(self):
        return {**super().metadata(), "target": self.target.__name__.lower()}
