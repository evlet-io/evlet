// The Docs landing page after sign-in: the developer guide, with its topics, examples and the models of this workspace. The Python page renderer and the
// Projects demo are not rendered by this interface (docs/internal/handoff-plan.md, decision 3), so their
// topics point to the Python APIs instead of live demos.

import { html, type Child } from '../dom';
import { Button, Icon, IconTile } from '../ui/base';
import { copyText } from '../ui/layers';
import { PageHeader } from '../ui/list';
import type { AppContext } from './context';
import { identity, rootModels } from './meta';

const startCode = `from evlet import App, Model, Text, Boolean

class Note(Model):
    title = Text(required=True)
    done = Boolean(default=False)

app = App(name="notes", models=[Note])`;

const screensCode = `from evlet import Currency, Date, Link, Model, Move, State, Table, Text
from evlet.calc import F

class Invoice(Model):
    customer = Link(Customer, required=True)
    due_on = Date()
    lines = Table(InvoiceLine)
    total = Currency(compute=lines.sum("amount"))
    status = State(("draft", "issued", "paid"), moves=(Move("draft", "issued", "Issue"),),
                   locked=("issued", "paid"), final=("paid",))

    class Meta:
        list_fields = ("customer", "due_on", "status", "total")
        layout = [{"label": "Invoice", "fields": ["customer", "due_on", "status"]},
                  {"label": "Amounts", "fields": ["total"]}]`;

const rulesCode = `from evlet import Currency, Select, Text, action
from evlet.calc import F

class Deal(Model):
    stage = Select(("open", "won", "lost"), default="open")
    reason = Text(applies_when=F("stage") == "lost", required_when=F("stage") == "lost")
    remark = Text(locked_when=F("stage") == "won")

    @action("Record payment", inputs={"amount": Currency(required=True)},
            available_when=F("stage") == "won")
    def record_payment(self, amount): ...`;

const slipCode = `from evlet import Child, Date, Decimal, Link, Slip, Table, Text

class Task(Child):
    title = Text(required=True)
    hours = Decimal(minimum="0")

class PlanProject(Slip):
    title = "Plan a project"
    source = Customer          # a button on each customer
    becomes = Project          # handing it in creates a project
    name = Text(required=True)
    starts_on = Date(required=True)
    tasks = Table(Task, required=True)

app = Application(database, auth=auth, slips=[PlanProject])`;

const pageCode = `from evlet.ui import PageView, Column, Card, Text, Button, Navigate

welcome = PageView(
    name="welcome", route="/welcome", title="Welcome",
    layout=Column(children=[
        Card(title="Your workspace", children=[
            Text("A page defined in Python."),
            Button("Open notes", on_click=Navigate.to_list(Note)),
        ]),
    ]),
)`;

interface Topic { id: string; title: string; render: (ctx: AppContext) => Child }

const code = (text: string) => html`<div class="ev-code"><div><span>Python</span><button type="button" class="link" on:click=${() => void copyText(text, 'the example')}>Copy</button></div><pre><code>${text}</code></pre></div>`;
const terminal = (text: string) => html`<pre class="ev-terminal"><code>${text}</code></pre>`;
const section = (title: string, ...body: Child[]) => html`<section class="ev-doc-section"><h2>${title}</h2>${body}</section>`;
const p = (text: string) => html`<p>${text}</p>`;
const table = (rows: string[][], head?: string[]) => html`<table class="ev-contract">${head ? html`<thead><tr>${head.map(cell => html`<th>${cell}</th>`)}</tr></thead>` : null}
  <tbody>${rows.map(([name, ...cells]) => html`<tr><th>${name}</th>${cells.map(cell => html`<td>${cell}</td>`)}</tr>`)}</tbody></table>`;

const TOPICS: Topic[] = [
  { id: 'overview', title: 'Build with Evlet', render: ctx => {
    const models = rootModels(ctx.schema);
    const go = (topic: string) => ctx.router.go('topic', { topic });
    const link = (icon: string, title: string, text: string, onClick: () => void) => html`<button type="button" class="ev-doc-link" on:click=${onClick}>
      ${Icon(icon)}<span><strong>${title}</strong><small>${text}</small></span>${Icon('arrow-right', { size: 'sm' })}</button>`;
    return html`<div class="ev-doc-columns"><article>
      ${section('Start with a model', p('Define your fields. Evlet gives you a list, a record view, routes, search, filters and permissions. Add a custom page when your application needs one.'),
        code(startCode), Button({ label: 'Build your first application', icon: 'arrow-right', onClick: () => go('getting-started') }))}
      ${section('A useful place to try things', html`<div class="ev-doc-links">
        ${link('layers', 'How screens are built', 'Lists, boards and records from your model, with no frontend code.', () => go('screens'))}
        ${link('zap', 'Rules and actions', 'Calculated values, rules that explain themselves, moves and actions.', () => go('state'))}
        ${ctx.dev ? link('layout-grid', 'Meet the components', 'Every component of the design, live, with its details.', () => { location.href = ctx.libraryHref; }) : null}</div>`)}
    </article>
    <aside class="ev-doc-aside"><h3>In this workspace</h3><p>The models in the sidebar use your database. Changes there are saved on the server.</p>
      <span class="pill b-green">Connected models</span>
      <ul>${models.map(model => html`<li><a class="link" href=${ctx.router.href('list', { model: model.name })}>${IconTile(identity(model).icon, { tint: identity(model).tint, size: 22 })}${model.plural}</a></li>`)}</ul>
      ${models.length ? null : p('No models are available to your role yet.')}
      <hr><h3>Keep it simple</h3><p>Python defines the model. Evlet checks every request on the server. The browser renders what the model allows.</p></aside></div>`;
  } },
  { id: 'getting-started', title: 'Getting started', render: () => html`<article class="ev-doc-article">
    ${section('1. Create an application', p('Use Python 3.12 or later. Build the bundled wheel from this repository, then install it and a local server. Run these commands in the directory containing the wheel.'),
      terminal('python -m venv .venv\nsource .venv/bin/activate\npython -m pip install ./evlet-0.1.0-py3-none-any.whl uvicorn\nmkdir notes\ncd notes'),
      p('Save this in app.py. It defines a model and a protected local application:'), code(startCode))}
    ${section('2. Open the app', terminal('uvicorn app:app --host 127.0.0.1 --port 8000 --reload'),
      p('Open http://127.0.0.1:8000. Create the first administrator account, then sign in. Docs opens first. Choose Notes in the sidebar to create and edit a record.'),
      p('App uses a local SQLite database in .data and loopback-only authentication. Supply an explicit database and authentication configuration when deploying. For a scaffold with separate model and application files, run evlet new my_app and follow its README.'))}
    ${section('3. Shape the screens from the model', p('Every model gets a list, a board when it has a State, and a record view. Meta options choose the columns, sections, key fields and related records. See How screens are built.'))}
    ${section('4. Keep control of the details', p('Use Application with your existing database, registry, policy, actions and storage when you need explicit configuration. The screens use the same query and mutation endpoints.'))}
  </article>` },
  { id: 'screens', title: 'Screens and routes', render: () => html`<article class="ev-doc-article">
    ${section('The application shell', p('The sidebar lists your models, grouped into documents and records, with your recent records. The top bar shows where you are, search (Ctrl K or Cmd K) and the New menu. Only the view scrolls.'),
      table([['Sidebar', 'Workspace, app bar, models, recent records, and your account at the bottom.'], ['Top bar', 'Sidebar toggle, breadcrumbs, search, shortcuts, New.'],
        ['View', 'The page: a list, a board, a record or these docs.'], ['Layers', 'Menus, dialogs, drawers, toasts and the search palette.']]))}
    ${section('From a model to its screens', p('Lists show Meta.list_fields (up to eight), with the title pinned. A model with a State also gets a board and saved views per state. A record shows the title, the State as steps with its moves, a summary strip, a Details tab built from Meta.layout, one tab per Table and the side panel.'), code(screensCode))}
    ${section('Routes', p('Every model has /app/model, /app/model/new and /app/model/123. Lists keep their search, sort, page and view in the address, so a link opens the same list. A record keeps its tab: /app/invoice/42?tab=lines.'),
      p('This interface renders model screens and these docs. Python pages (PageView), reports and native panels keep their Python API but are not rendered here.'), code(pageCode))}
  </article>` },
  { id: 'state', title: 'Rules and actions', render: () => html`<article class="ev-doc-article">
    ${section('The model decides; the screen explains', p('Calculated values show their rule. Rules hide and clear fields, make them required, or lock them, and say why in place. The browser evaluates the same finite rules as the server, and the server checks every write again.'), code(rulesCode))}
    ${section('Moves and actions', p('A State changes only through its moves. A move that is not allowed stays visible, dims and says why. Actions with inputs open a small dialog that checks its values before it runs. Server actions keep their normal role, record and field checks.'))}
    ${section('Saving', p('Values save one at a time when you leave them, with Undo. Lines are saved together with Save. If the record changed since you opened it, your draft stays and you can reload.'))}
    ${section('Slips', p('A slip is a form with the fields of a model that saves nothing by itself. It opens from a record, the New menu, an action or browser code. The server checks every change. When it is handed in, its values go to the slip\'s hand_in() method, or become a record through the normal create path. See docs/slips.md.'), code(slipCode))}
  </article>` },
  { id: 'extensions', title: 'Customization', render: () => html`<article class="ev-doc-article">
    ${section('Change a screen through the model', table([['Meta.list_fields', 'List columns, up to eight.'], ['Meta.layout', 'Sections of the Details tab.'],
      ['Meta.title_field', 'The record title and the pinned list column.'], ['Meta.search_fields', 'What search looks at.'],
      ['Meta.key_fields', 'The Key fields block and the summary strip.'], ['Meta.relations', 'The Related tab.'], ['Meta.measures', 'Totals in lists, boards and records.']]))}
    ${section('Per user', p('Column widths and order, the sidebar and quick access are saved for each user; appearance and region are in Settings. Recent records and searches last until you sign out.'))}
  </article>` },
  { id: 'reference', title: 'API reference', render: () => html`<article class="ev-doc-article">
    ${section('The public surface', table([['evlet', 'App, Application, Model, Registry, Database, fields, migrations, LocalAuth, Policy, Rule, Action and ChildTable.'],
      ['evlet.ui', 'Views, components, layouts, ViewRegistry, SchemaCompiler, ComponentDefinition, Bind, When and client operations.'],
      ['Model routes', 'GET/POST /api/model, GET/PATCH/DELETE /api/model/id. Filters, search, sorting and pagination run on the server.'],
      ['Metadata', '/api/schema for permitted model contracts.'],
      ['Connector discovery', '/api/_discovery and /api/_discovery/model expose permitted framework operations to the Python HTTP client and MCP integration.']]))}
    ${section('Validation and persistence', p('Decimals are transmitted as strings. Server defaults apply to untouched fields. Child collections preserve row permissions. Private attachments are served through authenticated endpoints. Editing uses revision checks to prevent stale updates.'))}
  </article>` },
  { id: 'troubleshooting', title: 'Troubleshooting', render: () => html`<article class="ev-doc-article">
    ${section('A record changed before I saved', p('Your draft remains in the record. Copy anything you need, then use Reload to load the current record. Evlet never silently replaces an unsaved draft.'))}
    ${section('A field or action is missing', p('Check the model metadata, role policy, read-only settings and record permissions. The browser receives only permitted model fields and actions. A child model is edited through its parent.'))}
    ${section('Working from the repository', terminal('bash scripts/playground.sh --setup'), p('This starts the backend, browser development server and worker locally. Python-only installed applications use the bundled production UI and do not need Node.'))}
  </article>` },
];

export const docTopics = TOPICS.map(topic => ({ id: topic.id, title: topic.title }));

export function DocsPage(ctx: AppContext, topicId = 'overview'): HTMLElement {
  const topic = TOPICS.find(item => item.id === topicId) ?? TOPICS[0];
  ctx.crumbs.set(topic.id === 'overview' ? [{ label: 'Docs' }] : [{ label: 'Docs', href: ctx.router.href('docs', {}) }, { label: topic.title }]);
  return html`<div class="page ev-docs">
    ${PageHeader({ icon: 'file-text', tint: 'slate', title: topic.title })}
    <p class="ev-docs-sub">${topic.id === 'overview' ? 'The practical guide to building your application.' : 'Developer documentation'}</p>
    <nav class="tabs" aria-label="Docs topics">${TOPICS.map(item => html`<a class:on=${item.id === topic.id} aria-current=${item.id === topic.id ? 'page' : null}
      href=${item.id === 'overview' ? ctx.router.href('docs', {}) : ctx.router.href('topic', { topic: item.id })}>${item.title}</a>`)}</nav>
    ${topic.render(ctx)}</div>` as HTMLElement;
}
