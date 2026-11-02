import dataclasses
import json
import pytest
from evlet import (
    Application,
    Database,
    Model,
    Text as ModelText,
    Registry,
    User,
    Policy,
    Rule,
)
from evlet.ui import (
    PageView,
    Column,
    Row,
    Card,
    Text,
    Button,
    TextInput,
    Field,
    FormView,
    ListView,
    When,
    Bind,
    SetValue,
    Navigate,
    OpenDialog,
    Dialog,
    SchemaCompiler,
    ViewRegistry,
    ComponentDefinition,
    CustomComponent,
)
from test_business import secured, hashed, client, http
from examples.playground.models import Customer, Project


def test_declarative_and_incremental_compile_to_identical_immutable_snapshot():
    explicit = PageView(
        "example",
        route="/example",
        layout=Column(children=[Text("Hello", id="greeting")]),
    )
    manual = PageView("example", route="/example")
    manual.add(Text("Hello", id="greeting"))
    compiler = SchemaCompiler()
    frozen = compiler.compile(explicit)
    assert frozen.data == compiler.compile(manual).data
    explicit.find("greeting").text = "Changed"
    assert (
        frozen.to_dict()["layout"]["slots"]["children"][0]["props"]["text"] == "Hello"
    )
    decoded = frozen.to_dict()
    decoded["title"] = "Different"
    assert frozen.to_dict()["title"] == "example"
    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.data = b"{}"
    manual.layout.insert(0, Card(id="intro"))
    manual.add(Button("Go", on_click=Navigate.to("/example")), after="intro")
    manual.replace("intro", Text("Intro"))
    assert manual.find("intro").props["text"] == "Intro"
    assert manual.remove("intro").id == "intro"


@pytest.mark.parametrize(
    "layout,match",
    [
        (
            Column(children=[Text("a", id="same"), Text("b", id="same")]),
            "Duplicate component ID",
        ),
        (Text("x", unknown=True), "unknown property"),
        (Text(lambda: "source"), "serializable"),
        (Button("Bad", on_click=lambda: None), "registered client operation"),
        (TextInput(value="a", bindings={"nope": Bind("a")}), "unsupported binding"),
        (Button("Go", on_click=Navigate.to("//evil.test")), "application-relative"),
        (Button("Open", on_click=OpenDialog("missing")), "Unknown event target"),
        (Column(gap="wide"), "expected number"),
        (Text("unsafe", style={"background": "url(https://evil.test)"}), "Styles"),
    ],
)
def test_compiler_rejects_invalid_or_executable_definitions(layout, match):
    with pytest.raises(ValueError, match=match):
        SchemaCompiler().compile(PageView("bad", layout=layout))


def test_cyclic_components_computed_values_and_state_bindings():
    column = Column()
    column.add(column)
    with pytest.raises(ValueError, match="Cyclic"):
        SchemaCompiler().compile(PageView("cycle", layout=column))
    page = PageView(
        "cycle", computed={"view.a": When.state("b"), "view.b": When.state("a")}
    )
    with pytest.raises(ValueError, match="Circular"):
        SchemaCompiler().compile(page)
    page = PageView(
        "safe",
        state={"kind": "Person"},
        layout=Column(
            children=[
                TextInput(
                    id="company",
                    visible=When.state("kind").eq("Company"),
                    bindings={"value": Bind("company")},
                ),
                Button("Reset", on_click=SetValue("view.company", "")),
            ]
        ),
    )
    compiled = SchemaCompiler().compile(page).to_dict()
    assert compiled["dependencies"]["view.kind"] == ["company"]
    assert compiled["dependencies"]["view.company"] == ["company"]
    with pytest.raises(TypeError):
        bool(When.state("kind"))


def test_registry_routes_extensions_and_custom_contract():
    class Contact(Model):
        name = ModelText()

    ui = ViewRegistry(
        [PageView("schedule", route="/schedule/{id:int}", layout=Text("Schedule"))]
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        ui.register(PageView("duplicate", route="/schedule/{slug}"))
    with pytest.raises(ValueError, match="Reserved"):
        ui.register(PageView("reserved", route="/app/contact"))
    ui.components.register(
        ComponentDefinition(
            "Timeline",
            "custom",
            props={"compact": "boolean"},
            defaults={"compact": True},
            renderer="Timeline",
        )
    )
    ui.register(
        PageView("timeline", route="/timeline", layout=CustomComponent("Timeline"))
    )

    @ui.extend("contact.form", name="help")
    def add_help(view):
        view.field("name").width = 12
        view.add(Card(id="help", title="Help", children=[Text("First extension")]))

    @ui.extend("contact.form", layer="site", name="site_help")
    def adjust(view):
        view.find("help").title = "Site help"

    schema = Registry(Contact).ui_schema()
    ui.validate(schema)
    result = ui.resolve("/app/contact/12", schema)
    assert result["view"]["kind"] == "form"
    assert (
        result["view"]["layout"]["slots"]["children"][0]["slots"]["children"][0][
            "props"
        ]["width"]
        == 12
    )
    assert (
        result["view"]["layout"]["slots"]["children"][1]["props"]["title"]
        == "Site help"
    )
    assert ui.resolve("/schedule/123", schema)["params"] == {"id": "123"}
    for path in ("/schedule/abc", "/app/missing", "/app/contact/-3"):
        with pytest.raises(LookupError):
            ui.resolve(path, schema)
    ui.extend("contact.form", lambda view: view.remove("missing"), name="broken")
    with pytest.raises(ValueError, match="Extension 'broken'"):
        ui.validate(schema)


def test_authenticated_ui_metadata_and_field_permissions(secured):
    assert http(secured, "GET", "/api/ui")[0] == 401
    secured.business.policy = Policy(
        secured.registry,
        rules={
            ("editor", Customer): Rule(
                ("read", "update"), read_fields=("name",), write_fields=("name",)
            ),
            ("editor", Project): Rule(()),
        },
    )
    c = client(secured, "editor")
    status, data, _ = c("GET", "/api/ui")
    assert status == 200 and data["version"] == 1
    assert not any(view["model"] == "project" for view in data["views"])
    form = next(view for view in data["views"] if view["name"] == "customer.form")
    assert [
        node["props"]["field"]
        for node in form["layout"]["slots"]["children"][0]["slots"]["children"]
    ] == ["name"]
    assert c("GET", "/api/ui/resolve?path=/app/project")[0] == 404
    assert (
        c("GET", "/api/ui/resolve?path=/app/customer/new")[1]["view"]["kind"] == "form"
    )
    assert c("GET", "/api/ui", headers={b"host": b"evil.test"})[0] == 403


def test_custom_view_role_and_model_requirements(secured):
    secured.ui.register(
        PageView(
            "private",
            route="/private",
            roles=("admin",),
            layout=Text("Only administrators"),
        )
    )
    assert not any(
        v["name"] == "private"
        for v in client(secured, "viewer")("GET", "/api/ui")[1]["views"]
    )
    assert client(secured)("GET", "/api/ui/resolve?path=/private")[0] == 200


def test_revision_checks_prevent_stale_update_delete_and_action(secured):
    c = client(secured)
    record = c(
        "POST",
        "/api/project",
        {"name": "Original", "children": {"tasks": [{"title": "Task"}]}},
    )[1]
    path = "/api/project/" + str(record["id"])
    _, _, headers = c("GET", path)
    revision = headers[b"etag"]
    assert (
        c("PATCH", path, {"name": "Changed"}, headers={b"if-match": revision})[0] == 200
    )
    for method, suffix, body in [
        ("PATCH", "", {"name": "Stale"}),
        ("DELETE", "", None),
        ("POST", "/actions/start", {}),
    ]:
        assert c(method, path + suffix, body, headers={b"if-match": revision})[0] == 409
    _, latest, headers = c("GET", path)
    assert latest["name"] == "Changed" and latest["status"] == "planned"
    task = latest["children"]["tasks"][0]
    assert (
        c(
            "PATCH",
            path,
            {"children": {"tasks": [{**task, "done": True}]}},
            headers={b"if-match": headers[b"etag"]},
        )[0]
        == 200
    )
    assert (
        c(
            "PATCH",
            path,
            {"name": "Child stale"},
            headers={b"if-match": headers[b"etag"]},
        )[0]
        == 409
    )
    # Existing clients without a revision remain compatible.
    assert c("PATCH", path, {"name": "Compatible"})[0] == 200


def test_static_ui_preserves_unknown_routes_and_api_authentication(secured):
    from evlet.ui.serving import STATIC

    if not (STATIC / "index.html").is_file():
        pytest.skip("Build browser assets before checking static serving")
    for path in (
        "/",
        "/login",
        "/docs",
        "/docs/components",
        "/demo/projects",
        "/app/customer",
        "/app/customer/new",
        "/app/customer/12",
        "/app/customer/12/view",
    ):
        status, payload, headers = http(secured, "GET", path)
        assert status == 200 and b'<div id="evlet-app">' in payload
        assert b"frame-ancestors 'none'" in headers[b"content-security-policy"]
    assert http(secured, "GET", "/missing")[0] == 404
    assert http(secured, "GET", "/assets/../app.py")[0] == 404
    assert http(secured, "GET", "/api/customer")[0] == 401


def test_fonts_are_bundled_and_served_from_assets(secured):
    import re
    from evlet.ui.serving import STATIC
    styles = [path for path in (STATIC / "assets").glob("*.css")]
    if not styles:
        pytest.skip("Build browser assets before checking static serving")
    css = "\n".join(path.read_text() for path in styles)
    # Inter and JetBrains Mono come from /assets, never from another host, and swap in when loaded.
    fonts = sorted(set(re.findall(r"url\((?:/assets/)?([^)/]+\.woff2)\)", css)))
    assert len(fonts) == 3 and "fonts.googleapis.com" not in css and "font-display:swap" in css.replace(" ", "")
    assert "font-family:Inter;" in css and "font-family:JetBrains Mono;" in css and "ui-monospace" in css and "system-ui" in css
    for name in fonts:
        status, payload, headers = http(secured, "GET", "/assets/" + name)
        assert status == 200 and payload[:4] == b"wOF2" and headers[b"content-type"] == b"font/woff2"
        assert headers[b"content-security-policy"].startswith(b"default-src 'self'")


def test_generated_form_preserves_model_layout_groups(secured):
    views = client(secured)("GET", "/api/ui")[1]["views"]
    form = next(view for view in views if view["name"] == "project.form")
    groups = form["layout"]["slots"]["children"]
    assert [group["props"]["title"] for group in groups] == [
        "Project",
        "Planning",
        "Notes",
    ]
    assert [node["props"]["field"] for node in groups[1]["slots"]["children"]] == [
        "budget",
        "progress",
        "starts_on",
    ]
    assert groups[0]["id"] == "main_details"


def test_custom_model_route_fails_with_actionable_error():
    with pytest.raises(ValueError, match="use PageView for custom routes"):
        ViewRegistry([FormView(model=Customer, route="/customer-editor")])


def test_generated_extension_rejects_unknown_model_fields(secured):
    ui = ViewRegistry()
    ui.extend(
        "customer.form", lambda view: view.add(Field("missing")), name="invalid_field"
    )
    with pytest.raises(ValueError, match="Unknown model field: missing"):
        ui.validate(secured.business.schema(User(1, "owner", "admin")))


def test_editable_picker_contracts_compile_with_bindings():
    from evlet.ui import DatePicker, TimePicker, DateTimePicker

    ui = ViewRegistry([
        PageView(
            "schedule",
            route="/schedule",
            state={
                "date": "2026-12-10",
                "time": "18:30",
                "meeting": "2026-12-10T18:30+04:00",
            },
            layout=Column(children=[
                DatePicker(id="day", bindings={"value": Bind("date")}),
                TimePicker(id="time", bindings={"value": Bind("time")}),
                DateTimePicker(id="meeting", bindings={"value": Bind("meeting")}),
            ]),
        ),
    ])
    schema = ui.compile()
    children = schema["views"][0]["layout"]["slots"]["children"]
    names = ["DatePicker", "TimePicker", "DateTimePicker"]
    assert [child["type"] for child in children] == names
    catalog = {item["name"]: item for item in ui.components.catalog()}
    for name in names:
        assert catalog[name]["renderer"] == name
        assert "value" in catalog[name]["bindings"]
        assert "on_change" in catalog[name]["events"]
    assert json.loads(json.dumps(schema)) == schema


def test_input_prefixes_remain_presentation_metadata():
    from evlet.ui import CurrencyInput
    from examples.playground.ui import playground_ui

    view = PageView("price", layout=CurrencyInput(prefix="AED", value="123456789.01"))
    props = SchemaCompiler().compile(view).to_dict()["layout"]["props"]
    assert props["prefix"] == "AED"
    assert props["value"] == "123456789.01"
    ui = playground_ui()
    schema = Registry(Project, Customer).ui_schema()
    form = ui.resolve("/app/project/new", schema)["view"]
    planning = form["layout"]["slots"]["children"][1]
    budget = next(node for node in planning["slots"]["children"] if node["props"]["field"] == "budget")
    assert budget["props"]["prefix"] == "AED"


@pytest.mark.parametrize('value', ['image-set("https://example.test/a.png" 1x)', '-webkit-image-set("https://example.test/a.png" 1x)', r'\75rl(https://example.test/a.png)', 'u/**/rl(https://example.test/a.png)', 'image("https://example.test/a.png")'])
def test_styles_cannot_hide_external_resources(value):
    with pytest.raises(ValueError, match='Styles'):
        SchemaCompiler().compile(PageView('unsafe', layout=Text('Unsafe', style={'background':value})))


@pytest.mark.parametrize('model', ['../auth', '/customer', 'customer?x=1', 'customer/1', 12])
def test_execute_action_rejects_non_model_paths(model):
    from evlet.ui import ExecuteAction
    with pytest.raises(ValueError, match='model name'):
        SchemaCompiler().compile(PageView('unsafe', layout=Button('Run', on_click=ExecuteAction('rename', model=model, record_id=1))))


def test_rendered_deployment_preserves_browser_security_headers():
    from evlet.deployment import deployment_files
    from evlet.ui.serving import CSP
    caddy = deployment_files(['app.example.com'])['Caddyfile']
    assert f'Content-Security-Policy "{CSP}"' in caddy
    assert 'frame-ancestors' in caddy and 'Strict-Transport-Security' in caddy


def test_view_overrides_use_the_model_storage_name():
    class SalesOrder(Model):
        storage_name = "sales_orders"
        name = ModelText()

    ui = ViewRegistry([ListView(model=SalesOrder, title="Custom Orders")])
    schema = Registry(SalesOrder).ui_schema()
    ui.validate(schema)
    assert ui.resolve("/app/sales_orders", schema)["view"]["title"] == "Custom Orders"
