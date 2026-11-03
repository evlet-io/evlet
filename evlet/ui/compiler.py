"""Validate authoring definitions and freeze them as renderer-neutral JSON."""

from dataclasses import dataclass
import json
import math
import re
from .definitions import Component, View
from .expressions import Expression, Binding, Operation, state_path, model_key
from .contracts import COMMON, default_components

IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,159}$")
EXPRESSION_ARITY = {
    "ref": 1,
    "eq": 2,
    "ne": 2,
    "gt": 2,
    "gte": 2,
    "lt": 2,
    "lte": 2,
    "contains": 2,
    "and": 2,
    "or": 2,
    "not": 1,
    "choose": 3,
}
OPERATIONS = {
    "navigate": {"path"},
    "back": set(),
    "refresh": {"target"},
    "set": {"path", "value"},
    "reset": {"path"},
    "open": {"target"},
    "close": {"target"},
    "notify": {"message"},
    "execute": {"action", "model", "record_id"},
    "submit": {"target"},
}


def safe_path(path):
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or any(char in path for char in ("\\", "\x00", "\n", "\r"))
    ):
        raise ValueError("Navigation needs an application-relative URL")
    return path


def serializable(value, depth=0):
    if depth > 32:
        raise ValueError("UI values exceed the maximum nesting depth")
    if isinstance(value, Expression):
        if (
            value.op not in EXPRESSION_ARITY
            or len(value.args) != EXPRESSION_ARITY[value.op]
        ):
            raise ValueError("Unsupported reactive expression")
        if value.op == "ref":
            state_path(value.args[0])
        return {
            "$expr": value.op,
            "args": [serializable(arg, depth + 1) for arg in value.args],
        }
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [serializable(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if any(
            not isinstance(key, str)
            or key in ("__proto__", "constructor", "prototype", "$expr")
            for key in value
        ):
            raise ValueError("Unsafe UI property key")
        return {key: serializable(item, depth + 1) for key, item in value.items()}
    raise ValueError(
        f"UI schemas accept only serializable data, not {type(value).__name__}"
    )


def references(value):
    if isinstance(value, dict):
        if value.get("$expr") == "ref":
            yield value["args"][0]
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def operation(value):
    if not isinstance(value, Operation) or value.kind not in OPERATIONS:
        raise ValueError(
            "Events need a registered client operation, not a callable or source code"
        )
    if set(value.args) != OPERATIONS[value.kind]:
        raise ValueError(f"Invalid arguments for {value.kind}")
    if value.kind == "navigate":
        safe_path(value.args["path"])
    if value.kind in ("set", "reset"):
        state_path(value.args["path"])
    if value.kind in ("open", "close") and not IDENTIFIER.fullmatch(
        str(value.args["target"])
    ):
        raise ValueError("Dialog target needs a component ID")
    if value.kind == "execute" and not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)?", value.args["action"]
    ):
        raise ValueError("Use a registered action name")
    if value.kind == "execute" and value.args["model"] is not None:
        if not isinstance(value.args["model"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", value.args["model"]):
            raise ValueError("Use a registered model name")
    return {"op": value.kind, **serializable(value.args)}


@dataclass(frozen=True)
class CompiledView:
    """Immutable bytes; to_dict returns a fresh copy for each consumer."""

    data: bytes

    def to_dict(self):
        return json.loads(self.data)

    def to_json(self):
        return self.data.decode()


class SchemaCompiler:
    def __init__(self, components=None):
        self.components = components or default_components()

    def compile(self, view):
        if not isinstance(view, View) or not IDENTIFIER.fullmatch(str(view.name)):
            raise ValueError("A view needs a stable name")
        if view.kind == "list":
            for key in ("searchable", "sortable", "filterable", "enabled"):
                if key in view.options and type(view.options[key]) is not bool:
                    raise ValueError(f"{key} must be a boolean")
            size = view.options.get("page_size", 25)
            if type(size) is not int or not 1 <= size <= 100:
                raise ValueError("page_size must be from 1 to 100")
            if view.options.get("open_in", "page") not in ("page", "panel"):
                raise ValueError('open_in must be "page" or "panel"')
            if "columns" in view.options and (
                not isinstance(view.options["columns"], (list, tuple))
                or not all(isinstance(value, str) for value in view.options["columns"])
            ):
                raise ValueError("columns must be field names")
        ids, objects, dependencies, targets = set(), set(), {}, []

        def node(component, path, depth=0):
            if depth > 32 or id(component) in objects:
                raise ValueError("Cyclic, reused, or excessively nested component tree")
            if not isinstance(component, Component):
                raise ValueError(f"{path}: expected a Component")
            objects.add(id(component))
            component_id = component.id or path
            if not isinstance(component_id, str) or not IDENTIFIER.fullmatch(
                component_id
            ):
                raise ValueError(f"Invalid component ID: {component_id!r}")
            if component_id in ids:
                raise ValueError(f"Duplicate component ID: {component_id}")
            ids.add(component_id)
            contract = self.components.get(component.type)
            accepted = {**COMMON, **contract.props}
            props = {**contract.defaults, **component.props}
            for key, value in props.items():
                if key not in accepted:
                    raise ValueError(
                        f"{component_id}: unknown property {key!r} on {component.type}"
                    )
                if isinstance(value, Expression):
                    continue
                kind = accepted[key]
                valid = (
                    kind == "any"
                    or value is None
                    or {
                        "string": isinstance(value, str),
                        "number": type(value) in (int, float),
                        "boolean": type(value) is bool,
                        "object": isinstance(value, dict),
                        "array": isinstance(value, (list, tuple)),
                    }.get(kind, False)
                )
                if not valid:
                    raise ValueError(f"{component_id}.{key}: expected {kind}")
            if component.type == "Image" and "src" in props:
                src = props["src"]
                if not isinstance(src, str) or not (
                    src.startswith("https://")
                    or src.startswith("/")
                    and not src.startswith("//")
                ):
                    raise ValueError(
                        "Images require HTTPS or an application-relative URL"
                    )
            if "style" in props and isinstance(props["style"], dict):
                if any(
                    not isinstance(v, (str, int, float))
                    or "\\" in str(v) or "/*" in str(v)
                    or re.search(r"(?:url|(?:-webkit-)?image-set|image|src|expression)\s*\(|@import|javascript:", str(v), re.I)
                    for v in props["style"].values()
                ):
                    raise ValueError(
                        "Styles cannot load remote resources or executable content"
                    )
            bindings = {}
            for key, value in component.bindings.items():
                if key not in contract.bindings or not isinstance(value, Binding):
                    raise ValueError(f"{component_id}: unsupported binding {key}")
                bindings[key] = value.path
            events = {}
            for key, value in component.events.items():
                if key not in contract.events:
                    raise ValueError(f"{component_id}: unsupported event {key}")
                events[key] = [
                    operation(item)
                    for item in (value if isinstance(value, (list, tuple)) else [value])
                ]
                for item in events[key]:
                    if (
                        item["op"] in ("open", "close")
                        or item["op"] in ("refresh", "submit")
                        and item["target"]
                    ):
                        targets.append(item["target"])
            slots = {}
            for key, items in component.slots.items():
                if not items:
                    continue
                if key not in contract.slots:
                    raise ValueError(f"{component_id}: unsupported slot {key}")
                slots[key] = [
                    node(child, f"{component_id}.{key}.{index}", depth + 1)
                    for index, child in enumerate(items)
                ]
            values = serializable(props)
            for ref in (*references(values), *bindings.values()):
                dependencies.setdefault(ref, []).append(component_id)
            return {
                "id": component_id,
                "type": component.type,
                "version": contract.version,
                "props": values,
                "slots": slots,
                "events": events,
                "bindings": bindings,
            }

        layout = node(view.layout, view.name + ".root")
        for target in targets:
            if target not in ids:
                raise ValueError(f"Unknown event target: {target}")
        state = serializable(view.state)
        computed = {}
        for key, value in view.computed.items():
            state_path(key)
            if not isinstance(value, Expression):
                raise ValueError("Computed values require declarative expressions")
            computed[key] = serializable(value)
        visited, active = set(), set()

        def visit(key):
            if key in active:
                raise ValueError(f"Circular computed dependency: {key}")
            if key in visited:
                return
            active.add(key)
            for ref in references(computed[key]):
                if ref in computed:
                    visit(ref)
            active.remove(key)
            visited.add(key)

        for key in computed:
            visit(key)
        result = {
            "version": 1,
            "name": view.name,
            "kind": view.kind,
            "route": view.route,
            "title": view.title,
            "subtitle": view.subtitle,
            "icon": view.icon,
            "section": view.section,
            "model": model_key(view.model) if view.model else None,
            "layout": layout,
            "state": state,
            "computed": computed,
            "dependencies": dependencies,
            "options": serializable(view.options),
        }
        return CompiledView(
            json.dumps(
                result, allow_nan=False, sort_keys=True, separators=(",", ":")
            ).encode()
        )
