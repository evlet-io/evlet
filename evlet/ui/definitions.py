"""Mutable authoring objects. Compilation produces an immutable snapshot."""

from copy import deepcopy
from .expressions import ExecuteAction


class Component:
    def __init__(
        self,
        type,
        *,
        id=None,
        name=None,
        children=None,
        slots=None,
        bindings=None,
        events=None,
        **props,
    ):
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "id", id or (name if type != "Icon" else None))
        object.__setattr__(
            self, "props", {"name": name} if type == "Icon" and name is not None else {}
        )
        object.__setattr__(self, "events", dict(events or {}))
        object.__setattr__(self, "bindings", dict(bindings or {}))
        object.__setattr__(
            self, "slots", {key: list(value) for key, value in (slots or {}).items()}
        )
        self.slots["children"] = list(children or self.slots.get("children", []))
        for key, value in props.items():
            if key.startswith("on_"):
                self.events[key] = value
            elif key == "action":
                self.events["on_click"] = ExecuteAction(value)
            else:
                self.props["class" if key == "class_name" else key] = value

    def __getattr__(self, key):
        try:
            return self.props[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        if key in ("type", "id", "props", "events", "bindings", "slots"):
            object.__setattr__(self, key, value)
        else:
            self.props[key] = value

    @property
    def children(self):
        return self.slots["children"]

    def walk(self):
        yield self
        for items in self.slots.values():
            for child in items:
                if not isinstance(child, Component):
                    raise ValueError("Layout children must be Components")
                yield from child.walk()

    def find(self, component_id):
        found = [node for node in self.walk() if node.id == component_id]
        if len(found) != 1:
            raise ValueError(
                f"Expected one component with ID {component_id!r}, found {len(found)}"
            )
        return found[0]

    def field(self, name):
        found = [
            node
            for node in self.walk()
            if node.type == "Field" and node.props.get("field") == name
        ]
        if len(found) != 1:
            raise ValueError(f"Expected one field {name!r}")
        return found[0]

    def add(self, component, *, after=None, slot="children"):
        if not isinstance(component, Component):
            raise TypeError("Add a Component")
        if after is not None:
            for node in self.walk():
                for items in node.slots.values():
                    for index, child in enumerate(items):
                        if child.id == after:
                            items.insert(index + 1, component)
                            return component
            raise ValueError(f"Unknown insertion target {after!r}")
        self.slots.setdefault(slot, []).append(component)
        return component

    def insert(self, index, component):
        if not isinstance(component, Component):
            raise TypeError("Insert a Component")
        self.children.insert(index, component)
        return component

    def remove(self, component_id):
        for node in self.walk():
            for items in node.slots.values():
                for index, child in enumerate(items):
                    if child.id == component_id:
                        return items.pop(index)
        raise ValueError(f"Unknown component {component_id!r}")

    def replace(self, component_id, component):
        if not isinstance(component, Component):
            raise TypeError("Replace with a Component")
        for node in self.walk():
            for items in node.slots.values():
                for index, child in enumerate(items):
                    if child.id == component_id:
                        if component.id is None:
                            component.id = component_id
                        items[index] = component
                        return component
        raise ValueError(f"Unknown component {component_id!r}")

    def __deepcopy__(self, memo):
        result = Component(self.type)
        memo[id(self)] = result
        for key in ("id", "props", "events", "bindings", "slots"):
            object.__setattr__(result, key, deepcopy(getattr(self, key), memo))
        return result


def _factory(name, content_prop=None):
    def build(*args, **props):
        if args:
            if content_prop is None or len(args) != 1:
                raise TypeError(f"{name} takes keyword arguments or one content value")
            if content_prop in props:
                raise TypeError(f"{content_prop} was provided twice")
            props[content_prop] = args[0]
        return Component(name, **props)

    build.__name__ = name
    build.__doc__ = f"Create a {name} component. Inspect ComponentRegistry for its property and event contract."
    return build


Text = _factory("Text", "text")
Heading = _factory("Heading", "text")
Button = _factory("Button", "label")
Badge = _factory("Badge", "text")
Field = _factory("Field", "field")
for _name in (
    "Row",
    "Column",
    "Grid",
    "Stack",
    "Container",
    "Section",
    "Card",
    "Split",
    "Panel",
    "FormLayout",
    "Divider",
    "Icon",
    "Image",
    "Avatar",
    "Progress",
    "Alert",
    "EmptyState",
    "Skeleton",
    "TextInput",
    "TextArea",
    "NumberInput",
    "CurrencyInput",
    "Checkbox",
    "Switch",
    "Select",
    "MultiSelect",
    "DatePicker",
    "DateTimePicker",
    "ButtonGroup",
    "DropdownButton",
    "Dialog",
    "Drawer",
    "Tabs",
    "Table",
    "SearchBox",
    "Pagination",
    "RecordList",
):
    globals()[_name] = _factory(_name)


def CustomComponent(type, **props):
    return Component(type, **props)


class View:
    kind = "page"

    def __init__(
        self,
        name=None,
        *,
        route=None,
        title=None,
        subtitle="",
        layout=None,
        model=None,
        state=None,
        computed=None,
        roles=(),
        requires=(),
        icon="grid",
        section="Pages",
        **options,
    ):
        self.model = model
        self.name = name or (
            model.__name__.lower() + "." + self.kind if model else None
        )
        self.route = route
        self.title = title or (model.__name__ if model else self.name)
        self.subtitle = subtitle
        self.layout = layout if layout is not None else Column()
        self.state = dict(state or {})
        self.computed = dict(computed or {})
        self.roles, self.requires = tuple(roles), tuple(requires)
        self.icon, self.section, self.options = icon, section, dict(options)

    def add(self, component, **options):
        return self.layout.add(component, **options)

    def remove(self, component_id):
        return self.layout.remove(component_id)

    def find(self, component_id):
        return self.layout.find(component_id)

    def replace(self, component_id, component):
        return self.layout.replace(component_id, component)

    def field(self, name):
        return self.layout.field(name)


class PageView(View):
    kind = "page"


class DashboardView(View):
    kind = "dashboard"


class ListView(View):
    kind = "list"


class FormView(View):
    kind = "form"


class DetailView(View):
    kind = "detail"
