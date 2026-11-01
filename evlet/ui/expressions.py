"""Small, serializable expressions and client operations. No source evaluation."""

from dataclasses import dataclass
import re


def model_key(model):
    """Schema and route name of a model, which honours storage_name."""
    from ..models import storage_name
    return storage_name(model)


SCOPES = ("component", "view", "record", "application")


def state_path(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(component|view|record|application)\.[A-Za-z][A-Za-z0-9_]*", value
    ):
        raise ValueError("Use a scoped state path such as view.name or record.status")
    if value.split(".")[-1] in ("constructor", "prototype", "__proto__"):
        raise ValueError("Reserved state property")
    return value


@dataclass(frozen=True)
class Expression:
    op: str
    args: tuple

    def eq(self, value):
        return Expression("eq", (self, value))

    def ne(self, value):
        return Expression("ne", (self, value))

    def gt(self, value):
        return Expression("gt", (self, value))

    def gte(self, value):
        return Expression("gte", (self, value))

    def lt(self, value):
        return Expression("lt", (self, value))

    def lte(self, value):
        return Expression("lte", (self, value))

    def contains(self, value):
        return Expression("contains", (self, value))

    def otherwise(self, yes, no):
        return Expression("choose", (self, yes, no))

    def __and__(self, other):
        return Expression("and", (self, other))

    def __or__(self, other):
        return Expression("or", (self, other))

    def __invert__(self):
        return Expression("not", (self,))

    def __bool__(self):
        raise TypeError("Use & and | to combine expressions, not Python and/or")


class When:
    @staticmethod
    def field(name):
        return Expression("ref", (state_path("record." + name),))

    @staticmethod
    def state(name, scope="view"):
        return Expression("ref", (state_path(scope + "." + name),))


@dataclass(frozen=True)
class Binding:
    path: str

    def __post_init__(self):
        state_path(self.path)


def Bind(name, scope="view"):
    return Binding(state_path(name if "." in name else scope + "." + name))


@dataclass(frozen=True)
class Operation:
    kind: str
    args: dict


class Navigate:
    @staticmethod
    def to(path):
        return Operation("navigate", {"path": path})

    @staticmethod
    def to_list(model):
        return Navigate.to("/app/" + model_key(model))

    @staticmethod
    def to_record(model, record_id):
        return Navigate.to("/app/" + model_key(model) + "/" + str(record_id))

    @staticmethod
    def to_new(model):
        return Navigate.to("/app/" + model_key(model) + "/new")

    @staticmethod
    def back():
        return Operation("back", {})


def Refresh(target=None):
    return Operation("refresh", {"target": target})


def SetValue(path, value):
    return Operation("set", {"path": state_path(path), "value": value})


def ResetValue(path):
    return Operation("reset", {"path": state_path(path)})


def OpenDialog(target):
    return Operation("open", {"target": target})


def CloseDialog(target):
    return Operation("close", {"target": target})


def ShowNotification(message):
    return Operation("notify", {"message": message})


def ExecuteAction(action, *, model=None, record_id=None):
    return Operation(
        "execute", {"action": action, "model": model, "record_id": record_id}
    )


def SubmitForm(target=None):
    return Operation("submit", {"target": target})
