// The Docs landing page after sign-in: the developer guide, with its topics, examples and the models of this workspace. The Python page renderer and the
// Projects demo are not rendered by this interface (docs/internal/handoff-plan.md, decision 3), so their
// topics point to the Python APIs instead of live demos.

import { html, type Child } from '../dom';
import { Button, Icon, IconTile, SegmentedControl } from '../ui/base';
import { StatusBadge } from '../ui/doc';
import { copyText, toast } from '../ui/layers';
import { PageHeader } from '../ui/list';
import type { AppContext } from './context';
import { identity, rootModels, stepLabel, type DetailMove, type ScreenModel } from './meta';
import { ActionDialog, LockBanner, moveIcon, moveInputs, MovesButton, StageBar, stateBadgeLook, type MoveEntry } from './record-page';
import type { FieldSchema } from '../model/schema';
import { chooseTheme } from './settings';
import { cell, effect, type Cell } from '../reactive';
import type { Theme } from '../ui/theme';
import type { Tint } from '../ui/core';

const startCode = `from evlet import App, Model, Text, Boolean

class Note(Model):
    title = Text(required=True)
    done = Boolean(default=False)

app = App(name="notes", models=[Note])`;

const screensCode = `from evlet import Currency, Date, Link, Model, State, Table, Text

class Invoice(Model):
    customer = Link(Customer, required=True)
    due_on = Date()
    lines = Table(InvoiceLine)
    total = Currency(compute=lines.sum("amount"))
    status = State(
        State.Step("draft", "Draft"),
        State.Step("issued", "Issued", color="blue", lock=True),
        State.Step("paid", "Paid", color="green", lock=True, final=True),
        rules=(State.Rule("draft", "issued", "Issue", key="issue", primary=True),
               State.Rule("issued", "paid", "Mark paid", key="pay")),
    )

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

// ---------- State display ----------
// Live examples of how a State shows, drawn with the record page's own pieces (stateBadgeLook, StageBar, LockBanner,
// MovesButton, moveInputs and ActionDialog in record-page.ts) over small in-memory schemas, each next to the Python
// that declares it. Nothing here calls the server. tests/test_docs_state_snippets.py runs every STATE_CODE snippet.

const STATE_CODE = {
  deal: `from evlet import Currency, Model, State, Text
from evlet.calc import F

class Deal(Model):
    name = Text(required=True)
    amount = Currency()
    competitor = Text()
    stage = State(
        State.Step("lead", "Lead"),
        State.Step("qualified", "Qualified", color="blue"),
        State.Step("proposal", "Proposal", color="violet"),
        State.Step("negotiation", "Negotiation", color="amber"),
        State.Step("won", "Won", color="green", final=True),
        State.Step("lost", "Lost", color="rose", final=True),
        rules=(
            State.Rule("lead", "qualified", "Qualify"),
            State.Rule("qualified", "proposal", "Send proposal"),
            State.Rule("proposal", "negotiation", "Negotiate"),
            State.Rule("negotiation", "proposal", "Revise proposal", key="revise"),
            State.Rule("negotiation", "won", "Mark won", key="win", when=F("amount") > 0, primary=True),
            State.Rule(("lead", "qualified", "proposal", "negotiation"), "lost", "Mark lost", key="lose",
                       remark=State.Remark("required", label="Why was it lost?"), ask=("competitor",)),
        ),
        show="badge",  # the default: a coloured badge in the record header
    )`,
  bar: `stage = State(
    ...,  # the same steps and rules
    show="bar",  # the stage bar under the header
)`,
  none: `stage = State(
    ...,
    show="none",  # no badge, no bar: the app places the state itself
)`,
  invoice: `from evlet import Currency, Model, State, Text

class Invoice(Model):
    total = Currency(required=True)
    remarks = Text(default="", versioned=False)  # stays editable while locked
    status = State(
        State.Step("draft", "Draft"),
        State.Step("issued", "Issued", color="blue", lock=True, release=True),
        State.Step("paid", "Paid", color="green", lock=True, final=True),
        State.Step("void", "Void", color="rose", lock=True, final=True),
        rules=(
            State.Rule("draft", "issued", "Issue", key="issue", primary=True),
            State.Rule("issued", "paid", "Mark paid", key="pay", by=("admin",)),
            State.Rule("issued", "draft", "Reopen", key="reopen", by=("admin",)),
            State.Rule("issued", "void", "Void", key="void", by=("admin",), remark="required"),
        ),
    )`,
  expense: `from evlet import Currency, Model, State, Text
from evlet.calc import F

Level = State.Level

class Expense(Model):
    title = Text(required=True)
    total = Currency(required=True)
    status = State(
        State.Step("draft", "Draft"),
        State.Step("review", "In review", color="amber", lock=True),
        State.Step("approved", "Approved", color="green", lock=True, final=True),
        rules=(
            State.Rule("draft", "review", "Submit", key="submit", primary=True),
            State.Rule("review", "approved", "Approve", key="approve", primary=True, remark="optional",
                       levels=(Level(by=("manager",), pending="Pending manager approval"),
                               Level(by=("cfo",), when=F("total") >= 10000, pending="Pending CFO approval"))),
            State.Rule("review", "draft", "Reject", key="reject", reject=True, remark="required"),
        ),
        show="bar",
    )`,
};

type SampleStep = [key: string, label: string, color: string | null, flags?: { lock?: boolean; release?: boolean; final?: boolean }];
type SampleRule = { key: string; from: string[]; to: string; label: string; primary?: boolean; remark?: { mode: string; label: string | null }; asks?: string[] };

const sampleField = (name: string, type: string, label: string, extra: Partial<FieldSchema> = {}) => ({ name, type, label, required: false, read_only: false,
  hidden: false, default: null, help_text: '', placeholder: '', operators: [], applies_when: null, required_when: null, locked_when: null, ...extra }) as unknown as FieldSchema;

/** A model as /api/schema sends it, cut down to what the State pieces read. */
function sampleModel(name: string, label: string, field: string, steps: SampleStep[], rules: SampleRule[], fields: FieldSchema[] = [], show: 'badge' | 'bar' | 'none' = 'badge'): ScreenModel {
  const colors = Object.fromEntries(steps.filter(step => step[2]).map(step => [step[0], step[2]]));
  return { name, label, plural: `${label}s`, title_field: 'name', fields: [...fields, sampleField(field, 'select', humanizeKey(field), { read_only: true, required: true,
    choices: steps.map(step => step[0]), colors } as Partial<FieldSchema>)],
    state: { field, default: steps[0][0], show, rules, steps: steps.map(([key, text, color, flags]) => ({ key, label: text, color, lock: Boolean(flags?.lock || flags?.final),
      release: Boolean(flags?.release), final: Boolean(flags?.final) })) } } as unknown as ScreenModel;
}
const humanizeKey = (key: string) => key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, ' ');

const DEAL = sampleModel('deal', 'Deal', 'stage', [['lead', 'Lead', null], ['qualified', 'Qualified', 'blue'], ['proposal', 'Proposal', 'violet'],
  ['negotiation', 'Negotiation', 'amber'], ['won', 'Won', 'green', { final: true }], ['lost', 'Lost', 'rose', { final: true }]], [
  { key: 'lead_to_qualified', from: ['lead'], to: 'qualified', label: 'Qualify' },
  { key: 'qualified_to_proposal', from: ['qualified'], to: 'proposal', label: 'Send proposal' },
  { key: 'proposal_to_negotiation', from: ['proposal'], to: 'negotiation', label: 'Negotiate' },
  { key: 'revise', from: ['negotiation'], to: 'proposal', label: 'Revise proposal' },
  { key: 'win', from: ['negotiation'], to: 'won', label: 'Mark won', primary: true },
  { key: 'lose', from: ['lead', 'qualified', 'proposal', 'negotiation'], to: 'lost', label: 'Mark lost', remark: { mode: 'required', label: 'Why was it lost?' }, asks: ['competitor'] },
], [sampleField('name', 'text', 'Name', { required: true }), sampleField('amount', 'currency', 'Amount'), sampleField('competitor', 'text', 'Competitor')]);

const INVOICE = sampleModel('invoice', 'Invoice', 'status', [['draft', 'Draft', null], ['issued', 'Issued', 'blue', { lock: true, release: true }],
  ['paid', 'Paid', 'green', { final: true }], ['void', 'Void', 'rose', { final: true }]], [
  { key: 'issue', from: ['draft'], to: 'issued', label: 'Issue', primary: true },
  { key: 'pay', from: ['issued'], to: 'paid', label: 'Mark paid' },
  { key: 'reopen', from: ['issued'], to: 'draft', label: 'Reopen' },
  { key: 'void', from: ['issued'], to: 'void', label: 'Void', remark: { mode: 'required', label: null } },
], [sampleField('total', 'currency', 'Total', { required: true }), sampleField('remarks', 'text', 'Remarks', { versioned: false } as Partial<FieldSchema>)]);

const EXPENSE = sampleModel('expense', 'Expense', 'status', [['draft', 'Draft', null], ['review', 'In review', 'amber', { lock: true }],
  ['approved', 'Approved', 'green', { final: true }]], [
  { key: 'submit', from: ['draft'], to: 'review', label: 'Submit', primary: true },
  { key: 'approve', from: ['review'], to: 'approved', label: 'Approve', primary: true, remark: { mode: 'optional', label: null } },
  { key: 'reject', from: ['review'], to: 'draft', label: 'Reject', remark: { mode: 'required', label: null } },
], [sampleField('title', 'text', 'Title', { required: true }), sampleField('total', 'currency', 'Total', { required: true })], 'bar');

/** `_moves` as the detail sends it for a record in `value`: every rule out of it, allowed unless `blocked` gives a reason. */
function sampleMoves(model: ScreenModel, value: string, blocked: Record<string, string> = {}, extra: Record<string, Partial<DetailMove>> = {}): DetailMove[] {
  return model.state!.rules.filter(rule => rule.from.includes(value)).map(rule => ({ key: rule.key, to: rule.to, label: rule.label, allowed: !blocked[rule.key],
    ...(rule.primary ? { primary: true } : {}), ...(blocked[rule.key] ? { reason: blocked[rule.key] } : {}), ...(rule.asks ? { asks: rule.asks } : {}),
    ...(rule.remark ? { remark: { ...rule.remark, required: rule.remark.mode === 'required' } } : {}), ...extra[rule.key] }) as DetailMove);
}
const notSaved = () => toast('An example: nothing is saved', { icon: 'info' });
/** The header's moves for `_moves`, as the record page builds them. `onMove` runs an allowed one. */
const sampleEntries = (model: ScreenModel, moves: DetailMove[], onMove: (move: DetailMove) => void = notSaved): MoveEntry[] => moves.map(move => ({ label: move.label,
  icon: moveIcon(model, move.to), allowed: move.allowed, primary: move.primary, blocked: () => move.allowed ? null : move.reason ?? `${move.label} is not available now`, run: () => onMove(move) }));

/** A labelled example: what it shows, then the live pieces. */
const example = (name: string, caption: string, ...body: Child[]) => html`<figure class="ev-doc-example" data-example=${name}>
  <figcaption>${caption}</figcaption><div class="ev-doc-example-body">${body}</div></figure>`;
const labelled = (text: string, ...body: Child[]) => html`<div class="ev-doc-example-row"><span class="muted">${text}</span>${body}</div>`;
/** The State badge for `model` at `value`, as the record header draws it; with `moves`, it opens their menu. */
const badge = (model: ScreenModel, value: () => string, moves?: () => DetailMove[], onMove?: (to: string) => void) => StatusBadge({ value, ...stateBadgeLook(model),
  ...(moves && onMove ? { moves: () => moves().filter(move => move.allowed).map(move => ({ to: move.to, label: move.label })), onMove } : {}) });
/** A record's header, cut down: the title, the State badge when shown, and the move button. */
const header = (title: string, status: Child, actions: Child) => html`<div class="ev-doc-head"><strong>${title}</strong>${status}<span class="grow"></span>${actions}</div>`;

function StateDisplay(): Child {
  // 1. Badge: a live deal. Click the badge to move it; the menu offers only the moves allowed now.
  const badgeValue = cell('qualified', { name: 'docs.badge' });
  const badgeMoves = () => sampleMoves(DEAL, badgeValue.get());
  const restart = (value: Cell<string>, to: string) => Button({ label: 'Start again', size: 'small', variant: 'ghost', icon: 'rotate-ccw', onClick: () => value.set(to) });
  // 2. Bar: the same deal as a stage bar. A step is a button only when this person may move to it now.
  const barValue = cell('proposal', { name: 'docs.bar' });
  const barMoves = () => sampleMoves(DEAL, barValue.get());
  // 7. The primary move, then the same move blocked by its condition.
  const negotiation = sampleMoves(DEAL, 'negotiation');
  const blockedWin = sampleMoves(DEAL, 'negotiation', { win: 'The condition for this move is not met' });
  const noPrimary = negotiation.map(move => ({ ...move, primary: undefined }));
  // 6. An approval waiting at level 1: the manager may approve; the submitter sees why not.
  const approval = { level: 1, pending: 'Pending manager approval' };
  const managerMoves = sampleMoves(EXPENSE, 'review', {}, { approve: { ...approval, can_approve: true } as Partial<DetailMove> });
  const submitterMoves = sampleMoves(EXPENSE, 'review', { approve: 'Waiting for another approver', reject: 'Only the approvers of the waiting level can use this move' },
    { approve: { ...approval, can_approve: false } as Partial<DetailMove> });
  // A status badge in a fixed tint, for the two markers records don't draw yet.
  const marker = (label: string, color: Tint, name: string) => StatusBadge({ value: () => label, label: () => label, tint: () => color, name });
  const pendingChip = (move: DetailMove & { level?: number; pending?: string }) => marker(`${move.pending} · level ${move.level}`, 'amber', 'Approval');
  // 8. The Mark lost move takes a required remark and asks for the competitor.
  const lose = sampleMoves(DEAL, 'negotiation').find(move => move.key === 'lose')!;
  const loseInputs = moveInputs(DEAL, lose);
  const openLose = () => ActionDialog({ name: lose.key, label: lose.label } as any, loseInputs, async () => { notSaved(); return null; });
  const cancelled = () => ({ title: 'Void on 13-11-2026 by Sam Lee.', reason: 'Reason: Sent twice. ' });
  // 5. The version marker: what the detail sends as _version, drawn as status badges. Records don't show it yet.
  const version = (v: { number: number | null; released: boolean }) => v.number === null ? marker('Not released yet', 'slate', 'Version')
    : v.released ? marker(`v${v.number} released`, 'green', 'Version') : marker(`v${v.number}, changed since`, 'amber', 'Version');

  return html`<article class="ev-doc-article ev-doc-states">
    ${section('How a State shows', p('A State is drawn from its steps alone: the label, the colour and the flags lock, release and final. The examples below are the record page\'s own badge, stage bar, banners and move button, fed small sample schemas in this page. Click them: they move the example, and nothing is saved.'),
      code(STATE_CODE.deal))}
    ${section('show="badge"', p('The default. The state is a coloured badge next to the record title. Clicking it opens a menu of the moves this person may take now, and only those; the moves also stay in the header button.'),
      example('badge', 'A deal in Qualified. Choose a move from the badge.', header('Acme renewal', badge(DEAL, () => badgeValue.get(), badgeMoves, to => badgeValue.set(to)),
        () => MovesButton(sampleEntries(DEAL, badgeMoves(), move => badgeValue.set(move.to)))), () => badgeMoves().length ? null : restart(badgeValue, 'qualified')))}
    ${section('show="bar"', p('The stage bar sits under the header on the full page, every step in order. Done steps tick, the current step fills, and final steps end the bar: green, or red for a final step coloured rose. A step is a button only when this person may move to that state now. In a side panel the bar becomes the badge.'),
      code(STATE_CODE.bar),
      example('bar', 'The same deal in Proposal. Click Negotiation, or Lost.', StageBar(DEAL, { current: () => barValue.get(), canMove: to => barMoves().some(move => move.to === to && move.allowed),
        onMove: to => barValue.set(to) }), () => barMoves().length ? null : restart(barValue, 'proposal')))}
    ${section('show="none"', p('Neither a badge nor a bar: for an app that places the state itself, in a field or a custom block. The moves stay as the header button. When Meta.status names the State, its badge shows whatever show says.'),
      code(STATE_CODE.none),
      example('none', 'No state is drawn; the header keeps its moves.', header('Acme renewal', null, MovesButton(sampleEntries(DEAL, sampleMoves(DEAL, 'qualified'))))))}
    ${section('Step colours and final steps', p('color is one of blue, green, amber, rose, violet, teal and slate; a step without one is neutral, and the screen never guesses a colour from the name. The default step reads as a dashed Draft pill. A final or locked step shows a lock and its label in grey, and a final step coloured rose reads as cancelled, in red with a ban icon.'),
      example('colours', 'Every step of the deal, then the invoice\'s steps.',
        labelled('Deal', DEAL.state!.steps.map(step => badge(DEAL, () => step.key))),
        labelled('Invoice', INVOICE.state!.steps.map(step => badge(INVOICE, () => step.key)))))}
    ${section('Locked and released steps', p('lock=True makes the record read only to ordinary writes, except fields declared versioned=False. release=True marks a step whose content counts as released: entering it with changed content makes the next version, v0, v1, v2. A banner over the record says what can still change.'),
      code(STATE_CODE.invoice),
      example('locked', 'The invoice\'s banner in each state, as the record shows it.',
        LockBanner(INVOICE, 'draft', false, cancelled), LockBanner(INVOICE, 'issued', true, cancelled), LockBanner(INVOICE, 'void', true, cancelled)),
      example('version', 'The version marker. The detail sends _version; records don\'t draw it yet (the 0.4.7 status rework will). They are drawn as status badges here, for judging the wording.',
        labelled('Issued, released as v2', badge(INVOICE, () => 'issued'), version({ number: 2, released: true })),
        labelled('Reopened and edited', badge(INVOICE, () => 'draft'), version({ number: 2, released: false })),
        labelled('Never released', badge(INVOICE, () => 'draft'), version({ number: null, released: false }))),
      code('{"_version": {"number": 2, "released": true, "state": "issued"}}', 'Detail'))}
    ${section('Waiting for approval', p('A rule with levels moves when its last level that applies is approved. While it waits, _moves carries the level, its pending text and can_approve. Records show the bar and the buttons below today; the pending text is drawn as a status badge here for judging, as records don\'t show it yet.'),
      code(STATE_CODE.expense),
      example('approval', 'An expense of 2,500 in review: only the manager level applies.',
        StageBar(EXPENSE, { current: () => 'review' }),
        labelled('The manager', header('Team offsite', pendingChip(managerMoves[0]), MovesButton(sampleEntries(EXPENSE, managerMoves)))),
        labelled('The submitter', header('Team offsite', pendingChip(submitterMoves[0]), MovesButton(sampleEntries(EXPENSE, submitterMoves))))),
      code(JSON.stringify({ _moves: [managerMoves[0]] }, null, 2), 'Detail'))}
    ${section('The primary move', p('The header button is the rule marked primary=True, else the first allowed rule, with the other moves behind its chevron. A move that is not allowed stays visible, dims, and says why on hover or click.'),
      example('primary', 'A deal in Negotiation: Revise proposal is declared first, Mark won is primary.',
        labelled('primary=True', MovesButton(sampleEntries(DEAL, negotiation))),
        labelled('No primary', MovesButton(sampleEntries(DEAL, noPrimary))),
        labelled('Amount is 0', MovesButton(sampleEntries(DEAL, blockedWin)), html`<span class="muted">${blockedWin.find(move => move.key === 'win')!.reason}</span>`)))}
    ${section('A move that asks', p('remark="required" (or State.Remark with a label) makes the move ask for a remark, which the Journal keeps. ask=("field",) adds model fields to the dialog. The move runs only when the dialog\'s values pass.'),
      example('dialog', `Mark lost asks: ${loseInputs.map(input => `${input.label}${input.required ? ' (required)' : ''}`).join(', ')}.`,
        html`<div class="ev-doc-example-row">${Button({ label: `Try ${lose.label}`, icon: moveIcon(DEAL, lose.to), onClick: openLose })}</div>`))}
  </article>`;
}

interface Topic { id: string; title: string; render: (ctx: AppContext) => Child }

const code = (text: string, language = 'Python') => html`<div class="ev-code"><div><span>${language}</span><button type="button" class="link" on:click=${() => void copyText(text, 'the example')}>Copy</button></div><pre><code>${text}</code></pre></div>`;
const terminal = (text: string) => html`<pre class="ev-terminal"><code>${text}</code></pre>`;
const section = (title: string, ...body: Child[]) => html`<section class="ev-doc-section"><h2>${title}</h2>${body}</section>`;
const p = (text: string) => html`<p>${text}</p>`;
const table = (rows: string[][], head?: string[]) => html`<table class="ev-contract">${head ? html`<thead><tr>${head.map(cell => html`<th>${cell}</th>`)}</tr></thead>` : null}
  <tbody>${rows.map(([name, ...cells]) => html`<tr><th>${name}</th>${cells.map(cell => html`<td>${cell}</td>`)}</tr>`)}</tbody></table>`;

/** The person's theme, live: the same choice as Settings, Appearance. */
function themeSwitch(ctx: AppContext): Child {
  const me = ctx.me.peek();
  if (!me || me.portal) return null;
  const value = cell<Theme>(me.prefs.theme, { name: 'docs.theme' });
  effect(() => { const now = ctx.me.get()?.prefs.theme; if (now) value.set(now); });
  return html`<div class="ev-doc-theme">${SegmentedControl<Theme>({ label: 'Theme', value, size: 'small', onChange: theme => void chooseTheme(ctx.api, ctx.me, theme),
    options: [{ value: 'light', label: 'Light', icon: 'sun' }, { value: 'dark', label: 'Dark', icon: 'moon' }, { value: 'system', label: 'Match my device' }] })}</div>`;
}

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
        ${link('list-checks', 'State display', 'Badges, the stage bar, locks, approvals and moves, live.', () => go('states'))}
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
      p('App uses a local SQLite database in .data and loopback-only authentication. Supply an explicit database and authentication configuration when deploying. For a project with separate apps and sites, run evlet setup myproject --app notes --instance acme --dev.'))}
    ${section('3. Shape the screens from the model', p('Every model gets a list, a board when it has a State, and a record view. Meta options choose the columns, sections, key fields and related records. See How screens are built.'))}
    ${section('4. Keep control of the details', p('Use Application with your existing database, registry, policy, actions and storage when you need explicit configuration. The screens use the same query and mutation endpoints.'))}
  </article>` },
  { id: 'screens', title: 'Screens and routes', render: () => html`<article class="ev-doc-article">
    ${section('The application shell', p('The sidebar lists your models, grouped into documents and records. The top bar shows where you are, your recent records (the clock button left of search), search (Ctrl K or Cmd K), notifications and shortcuts. New records start from the New button on a list or from search. Only the view scrolls.'),
      table([['Sidebar', 'Workspace, app bar, models, and your account at the bottom.'], ['Top bar', 'Workspace switcher, search, recent records, notifications and shortcuts. The breadcrumb sits above each page title.'],
        ['View', 'The page: a list, a board, a record or these docs.'], ['Layers', 'Menus, dialogs, drawers, toasts and the search palette.']]))}
    ${section('From a model to its screens', p('Lists show Meta.list_fields (up to eight), with the title pinned. A model with a State also gets a board and saved views per state. A record shows the title, the State as steps with its moves, a summary strip, a Details tab built from Meta.layout, one tab per Table and the side panel.'), code(screensCode))}
    ${section('Routes', p('Every model has /app/model, /app/model/new and /app/model/123. Lists keep their search, sort, page and view in the address, so a link opens the same list. A record keeps its tab: /app/invoice/42?tab=lines.'),
      p('Reports run at /reports/name, with the filters they ran with, the sort and the page in the address.'))}
  </article>` },
  { id: 'state', title: 'Rules and actions', render: () => html`<article class="ev-doc-article">
    ${section('The model decides; the screen explains', p('Calculated values show their rule. Rules hide and clear fields, make them required, or lock them, and say why in place. The browser evaluates the same finite rules as the server, and the server checks every write again.'), code(rulesCode))}
    ${section('Moves and actions', p('A State changes only through its moves. A move that is not allowed stays visible, dims and says why. Actions with inputs open a small dialog that checks its values before it runs. Server actions keep their normal role, record and field checks.'))}
    ${section('Saving', p('Values save one at a time when you leave them, with Undo. Lines are saved together with Save. If the record changed since you opened it, your draft stays and you can reload. Leaving a record before Save never saves it: your unsaved changes are kept for you to restore or discard when you come back, and a new record waits in this browser.'))}
    ${section('Slips', p('A slip is a form with the fields of a model that saves nothing by itself. It opens from a record, an action, another slip or browser code. The server checks every change. When it is handed in, its values go to the slip\'s hand_in() method, or become a record through the normal create path. See docs/slips.md.'), code(slipCode))}
  </article>` },
  { id: 'states', title: 'State display', render: StateDisplay },
  { id: 'extensions', title: 'Customization', render: ctx => html`<article class="ev-doc-article">
    ${section('Change a screen through the model', table([['Meta.list_fields', 'List columns, up to eight.'], ['Meta.layout', 'Sections of the Details tab.'],
      ['Meta.title_field', 'The record title and the pinned list column.'], ['Meta.search_fields', 'What search looks at.'],
      ['Meta.key_fields', 'The Key fields block and the summary strip.'], ['Meta.relations', 'The Related tab.'], ['Meta.measures', 'Totals in lists, boards and records.']]))}
    ${section('Per user', p('Column widths and order, the sidebar and quick access are saved for each user; appearance and region are in Settings. Recent records and searches last until you sign out.'))}
    ${section('Light and dark', p('Every screen comes in a light and a dark theme, or follows the device and changes when it does. Each person chooses in Settings, Appearance, from the button beside their name or with the search palette; the server keeps the choice and opens every page in it, so nothing flashes. Screens use colour tokens only, so a model needs nothing for either theme.'),
      themeSwitch(ctx))}
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

export function DocsPage(ctx: AppContext, topicId = 'overview'): HTMLElement {
  const topic = TOPICS.find(item => item.id === topicId) ?? TOPICS[0];
  ctx.crumbs.set(topic.id === 'overview' ? [{ label: 'Docs' }] : [{ label: 'Docs', href: ctx.router.href('docs', {}) }, { label: topic.title }]);
  return html`<div class="page ev-docs">
    ${ctx.crumbBar?.()}${PageHeader({ icon: 'file-text', tint: 'slate', title: topic.title })}
    <p class="ev-docs-sub">${topic.id === 'overview' ? 'The practical guide to building your application.' : 'Developer documentation'}</p>
    <nav class="tabs" aria-label="Docs topics">${TOPICS.map(item => html`<a class:on=${item.id === topic.id} aria-current=${item.id === topic.id ? 'page' : null}
      href=${item.id === 'overview' ? ctx.router.href('docs', {}) : ctx.router.href('topic', { topic: item.id })}>${item.title}</a>`)}</nav>
    ${topic.render(ctx)}</div>` as HTMLElement;
}
