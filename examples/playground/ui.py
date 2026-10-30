"""Playground pages using the public UI contracts."""

from evlet.ui import (
    ViewRegistry,
    ComponentDefinition,
    CustomComponent,
    PageView,
    Column,
    Grid,
    Card,
    Text,
    Heading,
    Badge,
    Button,
    TextInput,
    Select,
    Checkbox,
    Progress,
    Dialog,
    Row,
    Bind,
    When,
    SetValue,
    ResetValue,
    OpenDialog,
    CloseDialog,
    ShowNotification,
    Navigate,
)


def playground_ui():
    ui = ViewRegistry()
    for view_name, field_name in (
        ("invoice.form", "total"),
        ("payment.form", "amount"),
        ("project.form", "budget"),
    ):
        def currency_prefix(view, name=field_name):
            view.field(name).prefix = "AED"
        ui.extend(view_name, currency_prefix, name="currency_prefix")
    ui.components.register(
        ComponentDefinition(
            name="ProjectsDemo",
            category="custom",
            renderer="ProjectsDemo",
            description="The Projects Explorer View with browser-local sample data.",
            accessibility="Keyboard navigation, labelled controls, native dialogs and independent scroll regions.",
            example='CustomComponent("ProjectsDemo")',
        )
    )
    ui.register(
        PageView(
            name="projects_demo",
            route="/demo/projects",
            title="Projects",
            section="Demo Pages",
            subtitle="A complete Explorer View with sample data.",
            layout=CustomComponent("ProjectsDemo"),
        )
    )
    page = PageView(
        name="layouts_demo",
        route="/demo/layouts",
        title="Layouts & state",
        section="Demo Pages",
        subtitle="A working page defined entirely in Python.",
        state={"kind": "Person", "name": "", "company": "", "subscribed": True},
        computed={"view.is_company": When.state("kind").eq("Company")},
        layout=Column(
            gap=18,
            children=[
                Grid(
                    columns=2,
                    gap=18,
                    children=[
                        Card(
                            title="A small form",
                            children=[
                                Text("Change the customer type to see a field appear."),
                                Select(
                                    label="Customer type",
                                    options=["Person", "Company"],
                                    bindings={"value": Bind("kind")},
                                ),
                                TextInput(
                                    label="Name",
                                    placeholder="Alex Morgan",
                                    bindings={"value": Bind("name")},
                                ),
                                TextInput(
                                    label="Company name",
                                    placeholder="Company name",
                                    visible=When.state("is_company"),
                                    required=When.state("is_company"),
                                    bindings={"value": Bind("company")},
                                ),
                                Checkbox(
                                    label="Receive updates",
                                    bindings={"value": Bind("subscribed")},
                                ),
                                Row(
                                    gap=10,
                                    children=[
                                        Button(
                                            "Preview details",
                                            variant="primary",
                                            on_click=OpenDialog("details"),
                                        ),
                                        Button(
                                            "Reset",
                                            on_click=[
                                                ResetValue("view.name"),
                                                ResetValue("view.company"),
                                                SetValue("view.kind", "Person"),
                                                ShowNotification("Form reset."),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        Card(
                            title="The same state, another place",
                            children=[
                                Badge(text=When.state("kind"), tone="info"),
                                Heading(
                                    text=When.state("name")
                                    .eq("")
                                    .otherwise(
                                        "Your name goes here", When.state("name")
                                    ),
                                    level=2,
                                ),
                                Text(
                                    text=When.state("is_company").otherwise(
                                        When.state("company"), "Personal customer"
                                    )
                                ),
                                Text(
                                    text=When.state("subscribed").otherwise(
                                        "Updates are enabled.", "Updates are disabled."
                                    )
                                ),
                                Progress(
                                    value=When.state("name").eq("").otherwise(25, 100),
                                    label="Profile completion",
                                ),
                                Text(
                                    "The values update directly from the view state. Nothing here is saved to the database."
                                ),
                                Button(
                                    "Read about state & actions",
                                    on_click=Navigate.to("/docs/state"),
                                ),
                            ],
                        ),
                    ],
                ),
                Card(
                    title="A reusable shell",
                    children=[
                        Text(
                            "Sidebar, TopBar, ViewHost, StatusBar and OverlayLayer are independent areas. The page owns its content and scrolling."
                        ),
                        Row(
                            children=[
                                Button(
                                    "Open Projects",
                                    on_click=Navigate.to("/demo/projects"),
                                ),
                                Button(
                                    "Explore components",
                                    on_click=Navigate.to("/docs/components"),
                                ),
                            ]
                        ),
                    ],
                ),
                Dialog(
                    id="details",
                    title="Preview details",
                    children=[
                        Text(
                            text=When.state("name")
                            .eq("")
                            .otherwise("No name entered yet.", When.state("name"))
                        ),
                        Text(text=When.state("kind")),
                        Text(
                            text=When.state("company"), visible=When.state("is_company")
                        ),
                    ],
                    slots={
                        "footer": [Button("Close", on_click=CloseDialog("details"))]
                    },
                ),
            ],
        ),
    )
    ui.register(page)
    return ui
