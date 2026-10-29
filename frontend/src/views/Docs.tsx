import { createSignal, For, Show, Switch, Match } from "solid-js";
import { Badge, Button, PageHeader, SummaryRow, Glyph } from "../ui";
import { Icon } from "../demos/icons";
import { useApp } from "../runtime/Context";
import { NodeRenderer, makeRuntime } from "../runtime/ViewRenderer";
import type {
  ComponentContract,
  NodeSchema,
  ViewSchema,
} from "../runtime/types";
const startCode = `from evlet import App, Model, Text, Boolean

class Note(Model):
    title = Text(required=True)
    done = Boolean(default=False)

app = App(name="notes", models=[Note])`;
const pageCode = `from evlet.ui import PageView, Column, Card, Text, Button, Navigate

welcome = PageView(
    name="welcome", route="/welcome", title="Welcome",
    layout=Column(children=[
        Card(title="Your workspace", children=[
            Text("A page defined in Python."),
            Button("Open notes", on_click=Navigate.to_list(Note)),
        ]),
    ]),
)
app = App(name="notes", models=[Note], views=[welcome])`;
const stateCode = `from evlet.ui import Bind, When, Select, TextInput

page.state = {"kind": "Person", "company": ""}
page.add(Select(
    label="Customer type", options=["Person", "Company"],
    bindings={"value": Bind("kind")},
))
page.add(TextInput(
    label="Company name", bindings={"value": Bind("company")},
    visible=When.state("kind").eq("Company"),
    required=When.state("kind").eq("Company"),
))`;
const extendCode = `from evlet.ui import ViewRegistry, Card, Text

ui = ViewRegistry([welcome])

@ui.extend("note.form", name="note_help")
def note_help(view):
    view.field("title").width = 12
    view.add(Card(id="help", title="Keep it brief", children=[
        Text("A title is enough to get started.")
    ]))

app = App(name="notes", models=[Note], ui=ui)`;
const shellAreas = [
  ["AppShell", "The viewport and its fixed navigation."],
  [
    "Sidebar",
    "Brand, workspace switcher, navigation groups, saved views, footer.",
  ],
  ["TopBar", "Leading, center and trailing slots."],
  ["ViewHost", "The active page and its scroll areas."],
  ["StatusBar", "Page status and supporting actions."],
  ["OverlayLayer", "Menus, notifications and dialogs."],
];
const explorerAreas = [
  [
    "ExplorerView",
    "A list with optional summary, categories and record preview.",
  ],
  [
    "PageHeader",
    "Title, subtitle, badge and primary actions. One action is a button; multiple actions form a menu.",
  ],
  ["SummaryRow", "A row of summary cards. It stays expanded while scrolling."],
  [
    "ViewBar",
    "Category tabs and the view switcher. Categories can use any select field.",
  ],
  ["ListToolbar", "Search, filters, sorting and column controls."],
  ["RecordList", "Columns, selection, record actions and pagination."],
  [
    "RecordPreview",
    "Icon, title, badge, identifier, metadata and tabs. The full card scrolls in its own area.",
  ],
];
function Code(props: { children: string }) {
  const app = useApp();
  return (
    <div class="code-example">
      <div>
        <span>Python</span>
        <button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(props.children);
              app.notify("Copied.");
            } catch {
              app.notify("Select the example and copy it.");
            }
          }}
        >
          Copy
        </button>
      </div>
      <pre>
        <code>{props.children}</code>
      </pre>
    </div>
  );
}
function Section(props: { title: string; children: any }) {
  return (
    <section class="doc-section">
      <h2>{props.title}</h2>
      {props.children}
    </section>
  );
}
export default function Docs(props: { path: string }) {
  const app = useApp();
  const topic = () => props.path.split("/")[2] || "overview";
  const titles: Record<string, string> = {
    overview: "Build with Evlet",
    "getting-started": "Getting started",
    components: "Component explorer",
    layout: "Layout & views",
    state: "State & actions",
    extensions: "Customization",
    reference: "API reference",
    troubleshooting: "Troubleshooting",
  };
  const go = (path: string) => app.router.navigate(path);
  return (
    <div class="docs-view">
      <PageHeader
        title={titles[topic()] || "Docs"}
        subtitle={
          topic() === "overview"
            ? "The practical guide to building your application."
            : "Developer documentation"
        }
        badge={<Badge tone="info">Evlet UI</Badge>}
        actions={
          topic() === "overview"
            ? [
                {
                  id: "demo",
                  label: "Explore Projects",
                  icon: <Icon name="arrow" />,
                  onSelect: () => go("/demo/projects"),
                },
              ]
            : []
        }
      />
      <Show when={topic() === "overview"}>
        <SummaryRow
          items={[
            {
              id: "start",
              label: "Start here",
              value: "A small app",
              caption: "in Python",
              tone: "primary",
              icon: <Icon name="bolt" />,
              onSelect: () => go("/docs/getting-started"),
            },
            {
              id: "components",
              label: "Component library",
              value: app.ui.components.length,
              caption: "documented contracts",
              icon: <Icon name="layers" />,
              onSelect: () => go("/docs/components"),
            },
            {
              id: "demos",
              label: "Demo pages",
              value: app.ui.views.filter((v) => v.section === "Demo Pages")
                .length,
              caption: "working examples",
              icon: <Icon name="grid" />,
              onSelect: () => go("/demo/projects"),
            },
          ]}
        />
      </Show>
      <div
        class="docs-scroll"
        classList={{ "docs-components-scroll": topic() === "components" }}
      >
        <Switch>
          <Match when={topic() === "overview"}>
            <div class="docs-columns">
              <article>
                <Section title="Start with a model">
                  <p>
                    Define your fields. Evlet gives you a list, an editor,
                    routes, search, filters and permissions. Add a custom page
                    when your application needs one.
                  </p>
                  <Code>{startCode}</Code>
                  <Button onClick={() => go("/docs/getting-started")}>
                    Build your first application <Glyph name="arrow" />
                  </Button>
                </Section>
                <Section title="A useful place to try things">
                  <div class="doc-links">
                    <button onClick={() => go("/docs/components")}>
                      <Icon name="layers" />
                      <span>
                        <strong>Meet the components</strong>
                        <small>
                          Properties, events, bindings and live examples.
                        </small>
                      </span>
                      <Glyph name="arrow" />
                    </button>
                    <button onClick={() => go("/demo/projects")}>
                      <Icon name="grid" />
                      <span>
                        <strong>Projects, as a complete screen</strong>
                        <small>
                          A working Explorer View with preview, stages and
                          tasks.
                        </small>
                      </span>
                      <Glyph name="arrow" />
                    </button>
                    <button onClick={() => go("/demo/layouts")}>
                      <Icon name="bolt" />
                      <span>
                        <strong>A page written in Python</strong>
                        <small>
                          Layouts, reactive fields and local interactions.
                        </small>
                      </span>
                      <Glyph name="arrow" />
                    </button>
                  </div>
                </Section>
              </article>
              <aside class="docs-aside">
                <h3>In this workspace</h3>
                <p>
                  The models in the sidebar use your database. Changes there are
                  saved on the server.
                </p>
                <Badge tone="success">Connected models</Badge>
                <ul>
                  <For each={app.models.filter((m) => !m.child_only)}>
                    {(model) => (
                      <li>
                        <button
                          class="ui-text-link"
                          onClick={() => go("/app/" + model.name)}
                        >
                          {model.plural}
                          <Glyph name="arrow" />
                        </button>
                      </li>
                    )}
                  </For>
                </ul>
                <hr />
                <h3>Demo Pages</h3>
                <p>
                  Projects uses sample records saved in this browser. It is
                  separate from the database-backed Project model.
                </p>
                <p>
                  Layouts & state is rendered from a Python view definition.
                </p>
                <hr />
                <h3>Keep it simple</h3>
                <p>
                  Python defines the page. Evlet validates the contract. SolidJS
                  renders the interface.
                </p>
              </aside>
            </div>
          </Match>
          <Match when={topic() === "getting-started"}>
            <article class="doc-article">
              <Section title="1. Create an application">
                <p>
                  Use Python 3.12 or later. Build the bundled wheel from this
                  repository, then install it and a local server. Run these
                  commands in the directory containing the wheel.
                </p>
                <pre class="terminal-example">
                  {
                    "python -m venv .venv\nsource .venv/bin/activate\npython -m pip install ./evlet-0.1.0-py3-none-any.whl uvicorn\nmkdir notes\ncd notes"
                  }
                </pre>
                <p>
                  Save this in <code>app.py</code>. It defines a model and a
                  protected local application:
                </p>
                <Code>{startCode}</Code>
              </Section>
              <Section title="2. Open the app">
                <pre class="terminal-example">
                  {"uvicorn app:app --host 127.0.0.1 --port 8000 --reload"}
                </pre>
                <p>
                  Open <code>http://127.0.0.1:8000</code>. Create the first
                  administrator account, then sign in. Docs opens first. Choose
                  Notes in the sidebar to create and edit a record.
                </p>
                <p>
                  <code>App</code> uses a local SQLite database in{" "}
                  <code>.data</code> and loopback-only authentication. Supply an
                  explicit database and authentication configuration when
                  deploying. For a scaffold with separate model and application
                  files, run <code>evlet new my_app</code> and follow its
                  README.
                </p>
              </Section>
              <Section title="3. Add your own page">
                <Code>{pageCode}</Code>
                <p>
                  Restart or use the server’s reload mode. The page appears in
                  navigation and opens directly at <code>/welcome</code>.
                </p>
              </Section>
              <Section title="4. Keep control of the details">
                <p>
                  Use <code>Application</code> with your existing database,
                  registry, policy, actions and storage when you need explicit
                  configuration. The UI uses the same query and mutation
                  endpoints.
                </p>
              </Section>
            </article>
          </Match>
          <Match when={topic() === "components"}>
            <ComponentExplorer />
          </Match>
          <Match when={topic() === "layout"}>
            <article class="doc-article">
              <Section title="The application shell">
                <p>
                  Each area accepts its own content, class, style, native
                  attributes and element reference. Navigation and the top bar
                  stay fixed; views own their scrolling.
                </p>
                <table class="contract-table">
                  <tbody>
                    <For each={shellAreas}>
                      {(area) => (
                        <tr>
                          <th>{area[0]}</th>
                          <td>{area[1]}</td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </Section>
              <Section title="Explorer View">
                <p>
                  The Projects demo uses Explorer View. Its regions work
                  independently, so a page can replace a toolbar or preview
                  without replacing the whole screen.
                </p>
                <table class="contract-table">
                  <tbody>
                    <For each={explorerAreas}>
                      {(area) => (
                        <tr>
                          <th>{area[0]}</th>
                          <td>{area[1]}</td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </Section>
              <Section title="Compose a Python page">
                <Code>{pageCode}</Code>
                <p>
                  Use <code>Row</code>, <code>Column</code>, <code>Grid</code>,{" "}
                  <code>Split</code> and <code>FormLayout</code>. Forms use a
                  12-column grid; a Field’s <code>width</code> controls its
                  span.
                </p>
                <Code>
                  {
                    'page = PageView(name="overview", route="/overview")\npage.layout = Column(gap=16)\npage.add(Text("Overview", id="heading", variant="heading"))\npage.layout.insert(0, Badge("Ready", id="status"))\npage.find("heading").text = "Your overview"\npage.replace("status", Badge("Active"))\npage.remove("status")'
                  }
                </Code>
              </Section>
              <Section title="Routes">
                <p>
                  Every model has <code>/app/model</code>,{" "}
                  <code>/app/model/new</code> and <code>/app/model/123</code>.
                  Add <code>/view</code> for read-only detail. Custom routes can
                  use <code>/orders/{"{reference}"}</code> or{" "}
                  <code>/orders/{"{id:int}"}</code>; parameters become view
                  state.
                </p>
                <p>
                  The framework reserves /api, /app, /assets, /docs, /login,
                  /settings and /health. Duplicate or ambiguous routes fail
                  during registration. Browser refresh and back/forward preserve
                  routes.
                </p>
              </Section>
            </article>
          </Match>
          <Match when={topic() === "state"}>
            <article class="doc-article">
              <Section title="Bind a value once">
                <p>
                  State has component, view, record and application scopes. Use
                  Bind for two-way inputs and When for expressions. The browser
                  evaluates a small set of declarative operators; it never
                  evaluates source code.
                </p>
                <Code>{stateCode}</Code>
                <Button onClick={() => go("/demo/layouts")}>
                  Try the live example <Glyph name="arrow" />
                </Button>
              </Section>
              <Section title="Events and operations">
                <p>
                  Contracts list supported events. Buttons use on_click; inputs
                  use on_input and on_change. Events can run one operation or an
                  ordered list.
                </p>
                <Code>
                  {
                    'Button("Reset", on_click=[\n    ResetValue("view.company"),\n    ShowNotification("Company cleared."),\n])\nButton("Show details", on_click=OpenDialog("details"))\nButton("Save", on_click=SubmitForm())\nButton("Start", action="project.start")'
                  }
                </Code>
                <p>
                  Navigate, Refresh, SetValue, ResetValue, OpenDialog,
                  CloseDialog, ShowNotification, ExecuteAction and SubmitForm
                  are the built-in operations. Server actions keep their normal
                  role, record and field checks.
                </p>
              </Section>
              <Section title="Computed values">
                <Code>
                  {
                    'page.computed = {\n    "view.show_company": When.state("kind").eq("Company"),\n}\npage.add(Text("Company details", visible=When.state("show_company")))'
                  }
                </Code>
                <p>
                  Computed values are read-only. Circular dependencies are
                  rejected. Unsaved record changes are preserved when live
                  updates arrive.
                </p>
              </Section>
            </article>
          </Match>
          <Match when={topic() === "extensions"}>
            <article class="doc-article">
              <Section title="Change a view without copying it">
                <Code>{extendCode}</Code>
                <p>
                  Extensions run after framework defaults and application
                  definitions, in registration order within the application
                  layer, then the site layer. Browser column and saved-filter
                  preferences apply last.
                </p>
                <p>
                  Use explicit component IDs for extension targets. Missing
                  targets, duplicate IDs, duplicate extension names and invalid
                  properties produce errors during application construction.
                  Later property assignments deliberately override earlier ones.
                </p>
              </Section>
              <Section title="Custom SolidJS components">
                <p>
                  Register a Python component contract and a matching typed
                  frontend renderer. Keep props serializable; use the standard
                  data client for record access.
                </p>
                <Code>
                  {
                    'from evlet.ui import ComponentDefinition, CustomComponent\n\nui.components.register(ComponentDefinition(\n    name="Schedule", category="custom",\n    props={"compact": "boolean"}, defaults={"compact": True},\n    renderer="Schedule", version=1,\n))\npage.add(CustomComponent("Schedule", compact=True))'
                  }
                </Code>
                <pre class="terminal-example">
                  {
                    'registerLazyComponent("Schedule", () => import("./Schedule"), 1);'
                  }
                </pre>
                <p>
                  Projects is a working example of a lazily loaded custom
                  renderer. Python-only pages need no frontend changes. Build
                  the frontend again only when adding or changing a custom
                  renderer.
                </p>
              </Section>
            </article>
          </Match>
          <Match when={topic() === "reference"}>
            <article class="doc-article">
              <Section title="The public surface">
                <table class="contract-table">
                  <tbody>
                    <tr>
                      <th>evlet</th>
                      <td>
                        App, Application, Model, Registry, Database, fields,
                        migrations, LocalAuth, Policy, Rule, Action and
                        ChildTable.
                      </td>
                    </tr>
                    <tr>
                      <th>evlet.ui</th>
                      <td>
                        Views, components, layouts, ViewRegistry,
                        SchemaCompiler, ComponentDefinition, Bind, When and
                        client operations.
                      </td>
                    </tr>
                    <tr>
                      <th>Model routes</th>
                      <td>
                        GET/POST /api/model, GET/PATCH/DELETE /api/model/id.
                        Filters, search, sorting and pagination run on the
                        server.
                      </td>
                    </tr>
                    <tr>
                      <th>Metadata</th>
                      <td>
                        /api/schema for permitted model contracts, /api/ui for
                        view and component contracts, /api/ui/resolve?path=… for
                        route resolution.
                      </td>
                    </tr>
                    <tr>
                      <th>Connector discovery</th>
                      <td>
                        /api/_discovery and /api/_discovery/model expose
                        permitted framework operations to the Python HTTP client
                        and MCP integration.
                      </td>
                    </tr>
                  </tbody>
                </table>
              </Section>
              <Section title="Registered views">
                <table class="contract-table">
                  <thead>
                    <tr>
                      <th>View</th>
                      <th>Kind</th>
                      <th>Route</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For each={app.ui.views}>
                      {(view) => (
                        <tr>
                          <td>{view.name}</td>
                          <td>{view.kind}</td>
                          <td>
                            <code>
                              {view.route ||
                                "/app/" +
                                  view.model +
                                  (view.kind === "list"
                                    ? ""
                                    : view.kind === "detail"
                                      ? "/123/view"
                                      : "/new or /123")}
                            </code>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </Section>
              <Section title="Registered extensions">
                <Show
                  when={app.ui.extensions.length}
                  fallback={<p>No application extensions are registered.</p>}
                >
                  <ul>
                    <For each={app.ui.extensions}>
                      {(item) => (
                        <li>
                          {item.name} · {item.target} · {item.layer}
                        </li>
                      )}
                    </For>
                  </ul>
                </Show>
              </Section>
              <Section title="Validation and persistence">
                <p>
                  Decimals are transmitted as strings. Server defaults apply to
                  untouched fields. Child collections preserve row permissions.
                  Private attachments are served through authenticated
                  endpoints. Editing uses revision checks to prevent stale
                  updates.
                </p>
              </Section>
            </article>
          </Match>
          <Match when={topic() === "troubleshooting"}>
            <article class="doc-article">
              <Section title="A record changed before I saved">
                <p>
                  Your draft remains in the editor. Copy anything you need, then
                  use Reload to load the current record. Evlet never silently
                  replaces an unsaved draft with a live update.
                </p>
              </Section>
              <Section title="A field or action is missing">
                <p>
                  Check the model metadata, role policy, read-only settings and
                  record permissions. The browser receives only permitted model
                  fields and actions. A child model is edited through its
                  parent.
                </p>
              </Section>
              <Section title="A view does not compile">
                <p>
                  The exception identifies the view, component or extension.
                  Check property types, duplicate IDs, missing extension targets
                  and reserved route namespaces. Assign explicit IDs to
                  components referenced by events or extensions.
                </p>
              </Section>
              <Section title="The browser says a renderer is unavailable">
                <p>
                  Check that the frontend name and compatibility version match
                  the Python component contract. Rebuild the browser assets
                  after registering a custom renderer.
                </p>
              </Section>
              <Section title="Working from the repository">
                <pre class="terminal-example">
                  {"bash scripts/playground.sh --setup"}
                </pre>
                <p>
                  This starts the backend, browser development server and worker
                  locally. Python-only installed applications use the bundled
                  production UI and do not need Node. Changes to Python views
                  need a server reload; frontend changes use the development
                  server’s hot reload.
                </p>
              </Section>
              <Section title="Migrating a Vue screen">
                <p>
                  Keep the model schema, permission policy and API calls.
                  Replace the view with Solid components or a Python PageView.
                  No model or database migration is needed for the UI
                  replacement.
                </p>
              </Section>
            </article>
          </Match>
        </Switch>
      </div>
    </div>
  );
}
function ComponentExplorer() {
  const app = useApp();
  const [search, setSearch] = createSignal(""),
    [selected, setSelected] = createSignal("Button");
  let detail!: HTMLElement;
  const choose = (name: string) => {
    setSelected(name);
    if (detail) detail.scrollTop = 0;
  };
  const current = () =>
    app.ui.components.find((c) => c.name === selected()) ||
    app.ui.components[0];
  return (
    <div class="component-explorer">
      <aside aria-label="Component list" tabIndex={0}>
        <input
          type="search"
          aria-label="Find a component"
          placeholder="Find a component…"
          value={search()}
          onInput={(e) => setSearch(e.currentTarget.value)}
        />
        <For each={[...new Set(app.ui.components.map((c) => c.category))]}>
          {(category) => (
            <section>
              <h3>{category}</h3>
              <For
                each={app.ui.components.filter(
                  (c) =>
                    c.category === category &&
                    c.name.toLowerCase().includes(search().toLowerCase()),
                )}
              >
                {(component) => (
                  <button
                    classList={{ selected: component.name === selected() }}
                    onClick={() => choose(component.name)}
                  >
                    {component.name}
                  </button>
                )}
              </For>
            </section>
          )}
        </For>
      </aside>
      <article ref={detail} aria-label="Component details" tabIndex={0}>
        <Show when={current()} keyed>
          {(contract) => (
            <>
              <div class="component-title">
                <h2>{contract.name}</h2>
                <Badge>{contract.category}</Badge>
              </div>
              <p>{contract.description || `The ${contract.name} component.`}</p>
              <div class="component-live">
                <span>LIVE EXAMPLE</span>
                <ComponentSample contract={contract} />
              </div>
              <Code>
                {contract.example || `CustomComponent("${contract.name}")`}
              </Code>
              <h3>Properties</h3>
              <table class="contract-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Default</th>
                  </tr>
                </thead>
                <tbody>
                  <For each={Object.entries(contract.props)}>
                    {([name, type]) => (
                      <tr>
                        <td>
                          <code>{name}</code>
                        </td>
                        <td>{type}</td>
                        <td>
                          {JSON.stringify(contract.defaults[name]) ?? "—"}
                        </td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
              <h3>Events, bindings and slots</h3>
              <p>
                <strong>Events:</strong> {contract.events.join(", ") || "None"}
              </p>
              <p>
                <strong>Bindings:</strong>{" "}
                {contract.bindings.join(", ") || "None"}
              </p>
              <p>
                <strong>Slots:</strong> {contract.slots.join(", ") || "None"}
              </p>
              <h3>Accessibility</h3>
              <p>{contract.accessibility}</p>
              <small class="muted">
                Renderer {contract.renderer} · Contract version{" "}
                {contract.version}
              </small>
            </>
          )}
        </Show>
      </article>
    </div>
  );
}
function ComponentSample(props: { contract: ComponentContract }) {
  const app = useApp();
  const make = (
    type: string,
    id: string,
    p: Record<string, any> = {},
  ): NodeSchema => ({
    type,
    id,
    version: 1,
    props: p,
    slots: {},
    events: {},
    bindings: {},
  });
  const contract = props.contract,
    type = contract.name;
  const p: Record<string, any> = { ...contract.defaults };
  const samples: Record<string, any> = {
    text: "A little progress, every day.",
    label: "Example",
    title: "A useful detail",
    initials: "EV",
    name: "grid",
    value: 42,
    total: 75,
    offset: 0,
    page_size: 25,
    alt: "Evlet",
    src: "/assets/evlet.svg",
    placeholder: "Write something…",
    options: ["Planned", "Active", "Done"],
    items: [
      { value: "details", label: "Details" },
      { value: "activity", label: "Activity" },
    ],
    columns: ["name", "status"],
  };
  for (const key of Object.keys(contract.props))
    if (key in samples) p[key] = samples[key];
  if (type === "Checkbox" || type === "Switch") p.value = true;
  if (type === "TextInput" || type === "TextArea" || type === "SearchBox")
    p.value = "Hello, Evlet";
  if (type === "CurrencyInput") { p.value = "1250.00"; p.prefix = "AED"; }
  if (type === "TextInput") { p.value = "example.com"; p.prefix = "https://"; }
  if (type === "DatePicker") {
    p.value = "2026-12-10";
    p.placeholder = "DD MMM YYYY";
  }
  if (type === "TimePicker") {
    p.value = "18:30";
    p.placeholder = "HH:mm";
  }
  if (type === "DateTimePicker") {
    p.value = "2026-12-10T18:30:00+04:00";
    p.placeholder = "DD MMM YYYY, HH:mm";
  }
  if (type === "Select") p.value = "Active";
  if (type === "MultiSelect") p.value = ["Active"];
  if (type === "Tabs") p.value = "details";
  if (type === "Table" || type === "RecordList") {
    p.items = [
      { name: "First record", status: "Active" },
      { name: "Second record", status: "Planned" },
    ];
    p.columns = ["name", "status"];
  }
  const node = make(type, "sample", p);
  if (contract.slots.includes("children"))
    node.slots.children = [
      make("Text", "sample-text", { text: "Content goes here." }),
    ];
  if (type === "Button")
    node.events.on_click = [{ op: "notify", message: "Button clicked." }];
  if (contract.bindings.includes("value")) node.bindings.value = "view.value";
  const view: ViewSchema = {
    version: 1,
    name: "sample",
    kind: "page",
    title: "",
    subtitle: "",
    section: "",
    icon: "grid",
    layout: node,
    state: { value: p.value },
    computed: {},
    dependencies: {},
    options: {},
  };
  const runtime = makeRuntime(view);
  return (
    <Show
      when={type !== "ProjectsDemo" && type !== "Field"}
      fallback={
        <Button onClick={() => useNavigation(type)}>
          Open{" "}
          {type === "ProjectsDemo"
            ? "Projects"
            : type === "Field"
              ? "a model form"
              : "the layout example"}{" "}
          <Glyph name="arrow" />
        </Button>
      }
    >
      <Show when={type === "Dialog" || type === "Drawer"}>
        <Button
          onClick={() =>
            runtime.dispatch([{ op: "open", target: "sample" }], node)
          }
        >
          Open {type}
        </Button>
      </Show>
      <NodeRenderer node={node} runtime={runtime} />
    </Show>
  );
  function useNavigation(name: string) {
    const path =
      name === "ProjectsDemo"
        ? "/demo/projects"
        : name === "Field"
          ? "/app/customer/new"
          : "/demo/layouts";
    app.router.navigate(path);
  }
}
