"""View registration, deterministic extension order, and route resolution."""

from copy import deepcopy
import re
import json
from .compiler import SchemaCompiler, safe_path
from .contracts import default_components
from .definitions import View, ListView, FormView, DetailView, Column, FormLayout, Field
from .expressions import model_key
from ..routing import reserved

LAYERS = ("application", "site")


def route_pattern(route):
    safe_path(route)
    if (
        "?" in route
        or "#" in route
        or "//" in route
        or route != "/"
        and route.endswith("/")
    ):
        raise ValueError(f"Invalid route: {route}")
    parts, names = [], set()
    for part in route.split("/"):
        match = re.fullmatch(r"\{([A-Za-z][A-Za-z0-9_]*)(:int)?\}", part)
        if match:
            name, integer = match.groups()
            if name in names:
                raise ValueError("Duplicate route parameter")
            names.add(name)
            parts.append(
                f"(?P<{name}>"
                + (r"[1-9][0-9]{0,15}" if integer else r"[A-Za-z0-9_-]+")
                + ")"
            )
        else:
            if "{" in part or "}" in part:
                raise ValueError("Use {name} or {id:int} route parameters")
            parts.append(re.escape(part))
    return re.compile("^" + "/".join(parts) + "$")


def ambiguous(left, right):
    a, b = left.split("/"), right.split("/")
    return len(a) == len(b) and all(
        x == y or x.startswith("{") or y.startswith("{") for x, y in zip(a, b)
    )


def generated_layout(model):
    visible = {field["name"] for field in model["fields"] if not field.get("hidden")}
    groups = model.get("layout") or [
        {
            "label": "Details",
            "fields": [
                field["name"] for field in model["fields"] if field["name"] in visible
            ],
        }
    ]
    return Column(
        children=[
            FormLayout(
                id="main_details" if index == 0 else f"section_{index + 1}",
                title=group["label"]
                if len(groups) > 1 or group["label"] != "Details"
                else "",
                children=[
                    Field(name, id=name) for name in group["fields"] if name in visible
                ],
            )
            for index, group in enumerate(groups)
            if any(name in visible for name in group["fields"])
        ]
    )


class ViewRegistry:
    def __init__(self, views=(), *, components=None):
        self.components = components or default_components()
        self.views, self.extensions = {}, []
        self._compiled = {}
        for view in views:
            self.register(view)

    def register(self, view):
        self._compiled.clear()
        if not isinstance(view, View):
            raise TypeError("Register a View")
        if view.name in self.views:
            raise ValueError(f"Duplicate view: {view.name}")
        if view.kind in ("list", "form", "detail") and (
            view.model is None
            or view.name != model_key(view.model) + "." + view.kind
            or view.route is not None
        ):
            raise ValueError(
                "Model views use their generated name and route; use PageView for custom routes"
            )
        if view.route:
            route_pattern(view.route)
            if view.route.split("/")[1].startswith("{"):
                raise ValueError(f"Routes start with a fixed segment: {view.route}")
            if view.route == "/" or reserved(view.route):
                raise ValueError(f"Reserved route namespace: {view.route}")
            for existing in self.views.values():
                if existing.route and ambiguous(existing.route, view.route):
                    raise ValueError(
                        f"Ambiguous routes: {existing.route} and {view.route}"
                    )
        self.views[view.name] = view
        return view

    def extend(self, target, callback=None, *, layer="application", name=None):
        if layer not in LAYERS:
            raise ValueError("Extensions use application or site layers")

        def register(function):
            self._compiled.clear()
            extension_name = name or function.__name__
            if any(
                item[3] == extension_name and item[0] == target and item[1] == layer
                for item in self.extensions
            ):
                raise ValueError(f"Duplicate extension: {extension_name}")
            self.extensions.append((target, layer, function, extension_name))
            return function

        return register(callback) if callback else register

    def _extended(self, view):
        view = deepcopy(view)
        for layer in LAYERS:
            for target, extension_layer, callback, name in self.extensions:
                if target == view.name and extension_layer == layer:
                    try:
                        callback(view)
                    except Exception as exc:
                        raise ValueError(
                            f"Extension {name!r} failed on {view.name}: {exc}"
                        ) from exc
        return view

    def compile(self, model_schema=None, user=None, *, validate_fields=False):
        key = (getattr(user, 'role', None), validate_fields, json.dumps(model_schema, sort_keys=True))
        if key not in self._compiled:
            self._compiled[key] = self._compile(model_schema, user, validate_fields=validate_fields)
        return deepcopy(self._compiled[key])

    def _compile(self, model_schema=None, user=None, *, validate_fields=False):
        models = (model_schema or {}).get("models", [])
        readable = {item["name"] for item in models}
        generated, overrides = [], set()
        for model in models:
            if model.get("child_only"):
                continue
            # Model references need only the public name when generating view definitions.
            reference = type(model["name"], (), {})
            for kind, factory in (
                ("list", ListView),
                ("form", FormView),
                ("detail", DetailView),
            ):
                name = model["name"] + "." + kind
                default = factory(
                    name,
                    model=reference,
                    title=model["plural"] if kind == "list" else model["label"],
                    section="Models",
                    layout=generated_layout(model),
                )
                definition = self.views.get(name, default)
                overrides.add(name)
                generated.append(self._extended(definition))
        available = generated + [
            self._extended(view)
            for name, view in self.views.items()
            if name not in overrides and view.kind in ("page", "dashboard")
        ]
        compiler = SchemaCompiler(self.components)
        result = []
        for view in available:
            if view.roles and (user is None or user.role not in view.roles):
                continue
            if (
                not set(
                    model_key(model) if isinstance(model, type) else model
                    for model in view.requires
                )
                <= readable
            ):
                continue
            if view.model and model_key(view.model) not in readable:
                continue
            # Generated layouts never disclose restricted model fields.
            if view.model:
                fields = {
                    field["name"]
                    for model in models
                    if model["name"] == model_key(view.model)
                    for field in model["fields"]
                    if not field.get("hidden")
                }

                if validate_fields:
                    known = {
                        field["name"]
                        for model in models
                        if model["name"] == model_key(view.model)
                        for field in model["fields"]
                    }
                    for node in view.layout.walk():
                        if (
                            node.type == "Field"
                            and node.props.get("field") not in known
                        ):
                            raise ValueError(
                                f"Unknown model field: {node.props.get('field')}"
                            )
                    if set(view.options.get("columns", [])) - known - {"id"}:
                        raise ValueError(f"Unknown column on {view.name}")
                if "columns" in view.options:
                    view.options["columns"] = [
                        name
                        for name in view.options["columns"]
                        if name in fields or name == "id"
                    ]

                def trim(node):
                    for key, children in node.slots.items():
                        node.slots[key] = [
                            child
                            for child in children
                            if child.type != "Field"
                            or child.props.get("field") in fields
                        ]
                        for child in node.slots[key]:
                            trim(child)

                trim(view.layout)
            result.append(compiler.compile(view).to_dict())
        return {
            "version": 1,
            "views": result,
            "components": self.components.catalog(),
            "extensions": [
                {"target": t, "layer": l, "name": n} for t, l, _, n in self.extensions
            ],
        }

    def validate(self, model_schema):
        names = {
            model["name"] + "." + kind
            for model in model_schema["models"]
            for kind in ("list", "form", "detail")
        } | set(self.views)
        for target, _, _, name in self.extensions:
            if target not in names:
                raise ValueError(f"Extension {name!r} targets unknown view {target!r}")
        for view in self.views.values():
            if view.model:
                model_name = model_key(view.model)
                metadata = next(
                    (
                        item
                        for item in model_schema["models"]
                        if item["name"] == model_name
                    ),
                    None,
                )
                if metadata is None:
                    raise ValueError(
                        f"View {view.name} references an unregistered model"
                    )
                fields = {field["name"] for field in metadata["fields"]}
                for node in view.layout.walk():
                    if node.type == "Field" and node.props.get("field") not in fields:
                        raise ValueError(
                            f"Unknown model field: {node.props.get('field')}"
                        )
                if set(view.options.get("columns", [])) - fields - {"id"}:
                    raise ValueError(f"Unknown column on {view.name}")
            SchemaCompiler(self.components).compile(self._extended(view))
        self.compile(model_schema, validate_fields=True)

    def resolve(self, path, model_schema, user=None):
        document = self.compile(model_schema, user)
        for view in document["views"]:
            if view["route"]:
                match = route_pattern(view["route"]).fullmatch(path)
                if match:
                    return {"view": view, "params": match.groupdict()}
        match = re.fullmatch(
            r"/app/([a-z][a-z0-9_]*)(?:/(new|[1-9][0-9]{0,15})(/view)?)?", path
        )
        if match:
            model, record_id, detail = match.groups()
            kind = "list" if record_id is None else "detail" if detail else "form"
            for view in document["views"]:
                if view["model"] == model and view["kind"] == kind:
                    return {"view": view, "params": {"id": record_id}}
        raise LookupError("View not found or not permitted")
