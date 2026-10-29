"""Inspectable component contracts shared by the compiler and documentation."""

from dataclasses import dataclass, field
from copy import deepcopy
import re

EVENTS = (
    "on_click",
    "on_change",
    "on_input",
    "on_focus",
    "on_blur",
    "on_select",
    "on_submit",
    "on_open",
    "on_close",
)
COMMON = {
    "class": "string",
    "style": "object",
    "visible": "boolean",
    "disabled": "boolean",
    "width": "number",
    "aria_label": "string",
}


@dataclass(frozen=True)
class ComponentDefinition:
    name: str
    category: str
    props: dict = field(default_factory=dict)
    events: tuple = ()
    slots: tuple = ()
    bindings: tuple = ()
    defaults: dict = field(default_factory=dict)
    description: str = ""
    accessibility: str = (
        "Preserve native semantics and provide an accessible label for controls."
    )
    renderer: str | None = None
    version: int = 1
    example: str = ""

    def public(self):
        return {
            "name": self.name,
            "category": self.category,
            "props": {**COMMON, **deepcopy(self.props)},
            "events": list(self.events),
            "slots": list(self.slots),
            "bindings": list(self.bindings),
            "defaults": deepcopy(self.defaults),
            "description": self.description,
            "accessibility": self.accessibility,
            "renderer": self.renderer or self.name,
            "version": self.version,
            "example": self.example,
        }


class ComponentRegistry:
    def __init__(self, definitions=()):
        self._items = {}
        for item in definitions:
            self.register(item)

    def register(self, definition):
        if not isinstance(definition, ComponentDefinition) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,79}", definition.name
        ):
            raise ValueError("Use a named ComponentDefinition")
        if definition.name in self._items:
            raise ValueError(f"Duplicate component: {definition.name}")
        if definition.version != 1 or set(definition.events) - set(EVENTS):
            raise ValueError("Unsupported component version or event")
        if set(definition.props.values()) - {
            "any",
            "string",
            "number",
            "boolean",
            "object",
            "array",
        }:
            raise ValueError("Unsupported component property type")
        if set(definition.defaults) - set(definition.props) - set(COMMON):
            raise ValueError("Defaults must name declared properties")
        if set(definition.bindings) - set(definition.props):
            raise ValueError("Bindings must name declared properties")
        self._items[definition.name] = deepcopy(definition)
        return definition

    def get(self, name):
        if name not in self._items:
            raise ValueError(
                f"Unknown component {name!r}; register its contract and frontend renderer"
            )
        return self._items[name]

    def catalog(self):
        return [item.public() for item in self._items.values()]


LAYOUT_PROPS = {
    "gap": "number",
    "padding": "number",
    "columns": "number",
    "align": "string",
    "justify": "string",
    "wrap": "boolean",
    "title": "string",
    "description": "string",
}
INPUT_PROPS = {
    "label": "string",
    "value": "any",
    "placeholder": "string",
    "required": "boolean",
    "help": "string",
    "min": "number",
    "max": "number",
    "step": "string",
    "max_length": "number",
    "options": "array",
    "multiple": "boolean",
    "error": "string",
    "rows": "number",
}


def default_components():
    registry = ComponentRegistry()

    def add(
        name,
        category,
        props,
        *,
        events=(),
        slots=(),
        bindings=(),
        defaults=None,
        description="",
        example="",
    ):
        registry.register(
            ComponentDefinition(
                name,
                category,
                props,
                events,
                slots,
                bindings,
                defaults or {},
                description,
                example=example,
            )
        )

    for name in (
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
    ):
        add(
            name,
            "layout",
            LAYOUT_PROPS,
            slots=("children", "header", "footer"),
            defaults={"gap": 16},
            description="Arrange content with a responsive, named layout.",
            example=f'{name}(children=[Text("Hello")])',
        )
    add(
        "Text",
        "display",
        {"text": "any", "variant": "string"},
        description="Plain text with optional emphasis.",
        example='Text("Your application is ready.")',
    )
    add(
        "Heading",
        "display",
        {"text": "any", "level": "number"},
        defaults={"level": 2},
        example='Heading("Overview", level=2)',
    )
    add(
        "Badge",
        "display",
        {"text": "any", "tone": "string"},
        defaults={"tone": "neutral"},
        example='Badge("Active", tone="success")',
    )
    add("Icon", "display", {"name": "string"}, example='Icon(name="grid")')
    add(
        "Image",
        "display",
        {"src": "string", "alt": "string"},
        example='Image(src="/assets/logo.svg", alt="Company logo")',
    )
    add(
        "Avatar",
        "display",
        {"initials": "string", "label": "string"},
        example='Avatar(initials="AK", label="Aslam")',
    )
    add(
        "Progress",
        "display",
        {"value": "number", "label": "string", "max": "number"},
        defaults={"max": 100},
        example='Progress(value=60, label="Completion")',
    )
    for name in ("Alert", "EmptyState", "Skeleton", "Divider"):
        add(
            name,
            "display",
            {"title": "string", "text": "string", "tone": "string"},
            slots=("children",),
            example=f"{name}()",
        )
    for name in (
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
        "SearchBox",
    ):
        add(
            name,
            "input",
            INPUT_PROPS,
            events=("on_change", "on_input", "on_focus", "on_blur"),
            bindings=("value",),
            description="A labelled native control with safe, scoped data binding.",
            example=f'{name}(label="Value", bindings={{"value": Bind("value")}})',
        )
    add(
        "Field",
        "input",
        {"field": "string", "label": "string", "required": "boolean", "value": "any"},
        bindings=("value",),
        events=("on_change", "on_input"),
        description="Render a field from the current model contract.",
        example='Field("name", id="name")',
    )
    add(
        "Button",
        "action",
        {"label": "string", "variant": "string", "icon": "string"},
        events=("on_click",),
        defaults={"variant": "secondary"},
        example='Button("Open customers", on_click=Navigate.to_list(Customer))',
    )
    add(
        "ButtonGroup",
        "action",
        {"label": "string"},
        slots=("children",),
        example='ButtonGroup(children=[Button("Save"), Button("Cancel")])',
    )
    add(
        "DropdownButton",
        "action",
        {"label": "string", "items": "array"},
        events=("on_select",),
        example='DropdownButton(label="Actions", items=[{"value": "export", "label": "Export"}])',
    )
    for name in ("Dialog", "Drawer"):
        add(
            name,
            "action",
            {"title": "string", "open": "boolean"},
            slots=("children", "footer"),
            bindings=("open",),
            events=("on_open", "on_close"),
            example=f'{name}(id="details", title="Details", children=[Text("Hello")])',
        )
    add(
        "Tabs",
        "navigation",
        {"items": "array", "value": "string"},
        slots=("children",),
        bindings=("value",),
        events=("on_select",),
        example='Tabs(items=[{"value": "details", "label": "Details"}], children=[Text("Details")])',
    )
    for name in ("Table", "RecordList"):
        add(
            name,
            "data",
            {
                "columns": "array",
                "items": "array",
                "source": "object",
                "empty_text": "string",
            },
            events=("on_select",),
            description="Read a bounded page through the authenticated model query API.",
            example='RecordList(source={"model": "customer", "limit": 20}, columns=["name", "email"])',
        )
    add(
        "Pagination",
        "data",
        {"total": "number", "page_size": "number", "offset": "number"},
        events=("on_change",),
        bindings=("offset",),
        defaults={"page_size": 25, "offset": 0},
        example="Pagination(total=50, page_size=25)",
    )
    return registry
