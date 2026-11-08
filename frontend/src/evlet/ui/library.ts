// The Library page: every component of the design catalog with its details (description, model
// mapping, properties, variants, built from and used by), and a live demo for each component this
// library implements. It doubles as a visual test bench in both themes.
//
// Catalog data is docs/ui-design/reference/src/10-library.js extracted as JSON. Demos are safe:
// nothing here writes to a server except the record-bound demos, which go to the record's API.

import { html, when, type Child } from '../dom';
import { cell, derive, type Cell } from '../reactive';
import type { RecordStore } from '../model/record';
import { formatMoney } from '../model/format';
import catalog from './catalog.json';
import {
  AddChip, Avatar, Button, Checkbox, Chip, CountBadge, Divider, EmptyState, FilterChip, Icon, IconButton, IconTile, InfoNote, Key, LinkButton, ProgressBar,
  QuickAddInput, RecordMark, ScoreBars, SearchInput, SegmentedControl, Stat, StateBanner, StatusPill, SummaryStrip, Switch,
} from './base';
import { ChildRows, ChildTable } from './childtable';
import { SplitView } from './split';
import { tint, type Tint } from './core';
import { DocFrame, DocHeader, LockBanner, MoveButton, SideBlock, StatusFlow, TabPanel, Tabs, type Lifecycle } from './doc';
import { BaseField, displayValue, localField, type FieldLike } from './fields';
import { FormSection, InlineValue, KeyValueList } from './inline';
import { DialogFooter, Menu, MenuButton, openDrawer, openModal, openPopover, toast } from './layers';
import { Board, BoardCard, BulkBar, Filters, ListSummary, PageHeader, Toolbar, type ActiveFilter } from './list';
import { DateRangePanel, rangePresets, rangeText, type DateRange } from './pickers';
import { DataTable, type Column } from './table';
import { tooltip, TooltipSample } from './tooltip';

interface Entry { cat: string; id: string; name: string; level: string; desc: string; props: string[][]; from: string[]; used: string[]; variants: string[]; model?: string }

const LEVELS = catalog.levels as Record<string, Tint>;
const GROUPS = catalog.groups as Array<[string, string, string, Tint]>;
/** The catalog, with ReferenceField added for the Reference() field type and the date range picker after the date fields. */
export const ENTRIES: Entry[] = (() => {
  const list = catalog.components as Entry[];
  const at = list.findIndex(entry => entry.id === 'sequence-field');
  const reference: Entry = { cat: 'inputs', id: 'reference-field', name: 'ReferenceField', level: 'Element', model: 'Reference()',
    desc: 'A link to a record of any model, such as the record a payment settles. Shows the model and the record.',
    props: [['…BaseField', '', 'All BaseField properties'], ['model', 'field', 'Reference()']], from: ['base-field'], used: ['Details tab', 'Side panel'], variants: [] };
  const range: Entry = { cat: 'inputs', id: 'date-range', name: 'DateRangePanel', level: 'Element',
    desc: 'Picks a first and a last day: a preset such as This month or Last 30 days, or two days on the calendar. Lists filter dates with it.',
    props: [['value', 'accessor', '[first, last] or null'], ['onApply', 'function', 'Called with the range, or null to clear']], from: ['date-field'], used: ['List filters'], variants: [] };
  const withReference = [...list.slice(0, at + 1), reference, ...list.slice(at + 1)];
  const dates = withReference.findIndex(entry => entry.id === 'duration-field');
  return [...withReference.slice(0, dates + 1), range, ...withReference.slice(dates + 1)];
})();

const WIDE = new Set(['data-table', 'summary-strip', 'tabs', 'toolbar', 'page-header', 'banner']);
const stack = (...items: Child[]) => html`<div class="lib-stack">${items}</div>`;
const row = (...items: Child[]) => html`<div class="lib-row">${items}</div>`;

// ---------- demo data ----------

interface Deal { id: number; title: string; company: string; stage: string; amount: number; owner: string; close: string; probability: number }
const STAGES: Array<{ value: string; label: string; tint: Tint }> = [
  { value: 'qualification', label: 'Qualification', tint: 'slate' }, { value: 'discovery', label: 'Discovery', tint: 'blue' },
  { value: 'proposal', label: 'Proposal', tint: 'violet' }, { value: 'negotiation', label: 'Negotiation', tint: 'amber' },
  { value: 'won', label: 'Won', tint: 'green' }, { value: 'lost', label: 'Lost', tint: 'rose' },
];
const COMPANIES = ['Al Sahra Trading', 'Gulf Marine Supplies', 'Noor Hospitality', 'Desert Rose Logistics', 'Emirates Fresh Foods', 'Falcon Engineering'];
const PEOPLE = ['Aslam K', 'Mariam Haddad', 'Omar Farouk', 'Priya Nair'];
const deals = (count: number): Deal[] => Array.from({ length: count }, (_, index) => ({
  id: index + 1, title: `${['ERP rollout', 'Payroll setup', 'POS upgrade', 'Warehouse app', 'VAT review'][index % 5]} ${index + 1}`, company: COMPANIES[index % COMPANIES.length],
  stage: STAGES[index % 4].value, amount: 12000 + (index * 7919) % 180000, owner: PEOPLE[index % PEOPLE.length],
  close: `${String(1 + (index * 3) % 28).padStart(2, '0')}-${String(11 + Math.floor(index / 28) % 2).padStart(2, '0')}-2026`, probability: [10, 30, 60, 80][index % 4],
}));
const stageOf = (value: string) => STAGES.find(stage => stage.value === value) ?? STAGES[0];
const money = (value: number) => formatMoney(String(value), { scale: 0 }).replace('.', '');

function demoFields() {
  return {
    text: localField({ name: 'deal', type: 'text', label: 'Deal name', max_length: 120, required: true }, 'ERP rollout, 3 entities'),
    longtext: localField({ name: 'remarks', type: 'longtext', label: 'Remarks' }, 'Three entities across Dubai mainland, JAFZA and Oman.'),
    integer: localField({ name: 'credit_days', type: 'integer', label: 'Credit days', minimum: 0, maximum: 365 }, 30),
    decimal: localField({ name: 'qty', type: 'decimal', label: 'Quantity', scale: 3 }, '2.500'),
    currency: localField({ name: 'rate', type: 'currency', label: 'Rate', scale: 2 }, '42000.00'),
    percent: localField({ name: 'discount', type: 'percent', label: 'Discount', scale: 2, minimum: '0', maximum: '100' }, '12.50'),
    date: localField({ name: 'posting_date', type: 'date', label: 'Posting date' }, '2026-11-01'),
    datetime: localField({ name: 'received_at', type: 'datetime', label: 'Received at' }, '2026-11-01T09:20:00Z'),
    time: localField({ name: 'meeting', type: 'time', label: 'Meeting time' }, '14:00:00'),
    duration: localField({ name: 'spent', type: 'duration', label: 'Time spent' }, 'PT1H30M'),
    boolean: localField({ name: 'active', type: 'boolean', label: 'Active' }, true),
    select: localField({ name: 'source', type: 'select', label: 'Source', choices: ['referral', 'website', 'event', 'cold_call'] }, 'referral'),
    link: localField({ name: 'customer', type: 'link', label: 'Customer', target: 'customer', required: true }, null),
    email: localField({ name: 'email', type: 'email', label: 'Email' }, 'khalid@alsahra-trading.ae'),
    url: localField({ name: 'website', type: 'url', label: 'Website' }, 'https://alsahra-trading.ae'),
    phone: localField({ name: 'mobile', type: 'phone', label: 'Mobile' }, '+971504182290'),
    tags: localField({ name: 'tags', type: 'tags', label: 'Tags' }, ['Hot', 'Renewal']),
    state: localField({ name: 'status', type: 'state', label: 'Status' }, 'issued'),
    sequence: localField({ name: 'number', type: 'sequence', label: 'Number' }, 'INV-2026-00041'),
    reference: localField({ name: 'source_doc', type: 'reference', label: 'Settles' }, { model: 'invoice', id: 41 }),
    computed: localField({ name: 'total', type: 'currency', label: 'Total', scale: 2, read_only: true,
      compute: { $expr: 'add', args: [{ $expr: 'ref', scope: 'self', field: 'net_total' }, { $expr: 'ref', scope: 'self', field: 'vat_total' }] } }, '25200.00'),
  };
}

// ---------- demos ----------

type Demo = () => Child;

function demos(record: RecordStore | null): Record<string, Demo> {
  const f = demoFields();
  const field = (item: FieldLike) => () => BaseField({ field: item });
  const lockedField = localField({ name: 'terms', type: 'text', label: 'Locked field' }, 'Can’t change', { locked: true });
  const missing = localField({ name: 'name', type: 'text', label: 'Required field', required: true });
  missing.error.set('Required field is required');
  const recordReady = () => record && record.ready.get();
  const onRecord = (render: (store: RecordStore) => Child, fallback: Demo): Demo => () => record
    ? when(() => recordReady(), () => render(record), () => html`<span class="muted">Loading the sample record</span>`) : fallback();
  const shown = cell('board');
  const selected = cell(new Set<string | number>([2, 3]));
  const lifecycle = cell<Lifecycle>('locked');
  const filters = cell<ActiveFilter[]>([{ key: 'owner', label: 'Owner: Aslam K' }, { key: 'stage', label: 'Stage: Proposal' }]);
  const flowState = cell('discovery');
  const board = cell(deals(10));
  const dealColumns: Column<Deal>[] = [
    { key: 'title', label: 'Deal', width: 220, render: deal => html`<span class="primary-cell">${RecordMark({ name: () => deal().company })}<span>${() => deal().title}</span></span>` },
    { key: 'company', label: 'Organization', width: 190 },
    { key: 'stage', label: 'Stage', width: 140, value: deal => STAGES.findIndex(stage => stage.value === deal.stage), render: deal => StatusPill({ label: () => stageOf(deal().stage).label, tint: () => stageOf(deal().stage).tint }) },
    { key: 'amount', label: 'Amount', width: 140, align: 'right', render: deal => () => money(deal().amount) },
    { key: 'probability', label: 'Probability', width: 150, render: deal => ProgressBar({ value: () => deal().probability }) },
    { key: 'close', label: 'Closes', width: 120, value: deal => deal.close.split('-').reverse().join('-') },
    { key: 'owner', label: 'Owner', width: 160, render: deal => html`<span class="cell-flex">${Avatar({ name: () => deal().owner, size: 'sm' })}${() => deal().owner}</span>` },
  ];
  const sampleDeals = deals(200);

  return {
    // inputs
    'base-field': () => stack(BaseField({ field: localField({ name: 'label', type: 'text', label: 'Label' }, 'Value') }), BaseField({ field: missing }), BaseField({ field: lockedField })),
    'text-field': field(f.text), 'longtext-field': field(f.longtext), 'integer-field': field(f.integer), 'decimal-field': field(f.decimal),
    'currency-field': field(f.currency), 'percent-field': field(f.percent), 'date-field': field(f.date), 'datetime-field': field(f.datetime),
    'date-range': () => {
      const chosen = cell<DateRange | null>(rangePresets()[4].range);
      return stack(html`<div class="lib-static pop show" style="padding:10px">${DateRangePanel({ value: () => chosen.get(), onApply: range => chosen.set(range) })}</div>`,
        html`<span class="muted">${() => chosen.get() ? rangeText(chosen.get()!) : 'No range'}</span>`);
    },
    'time-field': field(f.time), 'duration-field': field(f.duration), 'boolean-field': field(f.boolean), 'select-field': field(f.select),
    'link-field': onRecord(store => BaseField({ field: store.field('customer') }), field(f.link)),
    'email-field': field(f.email), 'url-field': field(f.url), 'phone-field': field(f.phone), 'tags-field': field(f.tags), 'state-field': field(f.state),
    'sequence-field': () => stack(BaseField({ field: f.sequence }), BaseField({ field: localField({ name: 'next', type: 'sequence', label: 'Number', placeholder: 'Assigned on issue' }) })),
    'reference-field': field(f.reference),
    'computed-field': onRecord(store => stack(BaseField({ field: store.field('total') }), BaseField({ field: store.field('due') })), field(f.computed)),
    'inline-value': onRecord(store => KeyValueList([store.field('title'), store.field('stage'), store.field('remark'), store.field('total')]),
      () => KeyValueList([f.text, f.select, f.computed], { save: () => true })),
    'search-input': () => SearchInput({ value: cell(''), placeholder: 'Search deals' }),
    checkbox: () => stack(Checkbox({ checked: cell(true), label: 'Selected' }), Checkbox({ checked: cell(false), label: 'Not selected' })),
    switch: () => stack(Switch({ on: cell(true), label: 'Status flow' }), Switch({ on: cell(false), label: 'Summary strip' })),
    segmented: () => stack(SegmentedControl({ options: [{ value: 'board', label: 'Board', icon: 'kanban' }, { value: 'list', label: 'List', icon: 'list' }], value: shown, label: 'View' }),
      SegmentedControl({ options: [{ value: 'c', label: 'Comfortable' }, { value: 'k', label: 'Compact' }], value: cell('c'), size: 'small', label: 'Density' })),
    'quick-add': () => QuickAddInput({ placeholder: 'Add a task and press Enter', onAdd: text => toast(`Added ${text}`) }),
    // buttons
    button: () => stack(row(Button({ label: 'Create deal', icon: 'plus', variant: 'primary' }), Button({ label: 'Email', icon: 'mail', iconTint: 'blue' }), Button({ label: 'Cancel', variant: 'ghost' })),
      row(Button({ label: 'Mark won', icon: 'trophy', variant: 'success' }), Button({ label: 'Void invoice', variant: 'danger' }), Button({ label: 'Delete', icon: 'trash-2', variant: 'soft-danger' })),
      row(Button({ label: 'Small', size: 'small' }), Button({ icon: 'ellipsis', tip: 'More actions' }))),
    'icon-button': () => row(IconButton({ icon: 'bell', tip: 'Notifications' }), IconButton({ icon: 'panel-left', tip: 'Toggle sidebar', active: cell(true) }), IconButton({ icon: 'copy', tip: 'Copy link' })),
    'menu-button': () => MenuButton({ label: 'New', icon: 'plus', variant: 'primary', items: [{ label: 'Deal', icon: 'handshake', tint: 'blue', hint: 'C', onSelect: () => toast('Opened New deal') },
      { label: 'Lead', icon: 'target', tint: 'teal', onSelect: () => toast('Opened New lead') }, 'separator', { label: 'Contact', icon: 'user-plus', tint: 'violet', onSelect: () => toast('Opened New contact') }] }),
    'move-button': () => row(MoveButton({ label: 'Issue', icon: 'send', variant: 'primary', blocked: null, onMove: () => toast('Issued as INV-2026-00042') }),
      MoveButton({ label: 'Mark paid', icon: 'circle-check', blocked: 'Outstanding is AED 25,200. Record the payment first.', onMove: () => {} })),
    'link-button': () => LinkButton({ label: 'All fields', icon: 'chevron-right', onClick: () => toast('Opened Details') }),
    'add-chip': () => row(Chip({ label: 'Renewal' }), AddChip({ label: 'Tag', onClick: () => toast('Added a tag') })),
    tab: () => Tabs({ tabs: [{ id: 'a', label: 'Activity' }, { id: 'd', label: 'Details' }, { id: 'l', label: 'Lines', count: 4 }], active: cell('l'), label: 'Record' }),
    // elements
    icon: () => stack(row(...['house', 'handshake', 'receipt', 'users', 'building-2', 'calendar', 'lock', 'sparkles'].map(name => Icon(name))),
      row(Icon('star', { size: 'xs' }), Icon('star', { size: 'sm' }), Icon('star'), Icon('star', { size: 'lg' }), Icon('flame', { tint: 'rose' }), Icon('trophy', { tint: 'green' }))),
    'icon-tile': () => row(IconTile('handshake', { tint: 'blue', size: 22 }), IconTile('target', { tint: 'teal', size: 32 }), IconTile('wallet', { tint: 'green', size: 44 })),
    avatar: () => row(Avatar({ name: 'Aslam K', size: 'sm' }), Avatar({ name: 'Mariam Haddad' }), Avatar({ name: 'Omar Farouk', size: 'lg' }), Avatar({ name: 'Priya Nair', size: 'xl' })),
    'org-mark': () => row(RecordMark({ name: 'Al Sahra Trading' }), RecordMark({ name: 'Noor Hospitality', size: 'lg' })),
    pill: () => row(...STAGES.map(stage => StatusPill({ label: stage.label, tint: stage.tint }))),
    chip: () => row(Chip({ label: 'Hot', icon: 'flame', tone: 'rose' }), Chip({ label: 'Renewal' }), Chip({ label: 'Primary' })),
    badge: () => row(html`<span class="nav-item" style="width:auto">Inbox ${CountBadge({ count: 3, tone: 'filled', label: '3 unread' })}</span>`, html`<span class="nav-item" style="width:auto">Deals ${CountBadge({ count: 42 })}</span>`),
    kbd: () => row(Key('⌘'), Key('K'), Key('Esc'), Key('Tab')),
    tooltip: () => stack(TooltipSample('Notifications', 'above'), TooltipSample('Outstanding is AED 25,200', 'below'), (() => { const button = Button({ label: 'Hover me' }); tooltip(button, 'Tooltips show at once'); return button; })()),
    progress: () => stack(ProgressBar({ value: 60 }), ProgressBar({ value: 85, tint: 'green' })),
    'score-bars': () => row(ScoreBars({ score: 22 }), ScoreBars({ score: 56 }), ScoreBars({ score: 88 })),
    divider: () => row(Button({ label: 'Left', variant: 'ghost' }), Divider(), Button({ label: 'Right', variant: 'ghost' })),
    // display
    stat: () => Stat({ label: 'Outstanding', value: 'AED 25,200', note: 'Due 16-12-2026' }),
    'summary-strip': onRecord(store => SummaryStrip([{ label: 'Total', value: () => formatMoney(store.field('total').value.get() as string), note: () => `${store.field('line_count').value.get()} lines` },
      { label: 'Issued', value: () => displayDate(store.field('issued').value.get()) }, { label: 'Due', value: () => displayDate(store.field('due').value.get()), note: 'From customer credit days' },
      { label: 'Stage', value: () => String(store.field('stage').value.get() ?? '') }]),
    () => SummaryStrip([{ label: 'Total', value: 'AED 25,200' }, { label: 'Outstanding', value: 'AED 25,200', note: 'Not paid' }, { label: 'Due', value: '16-12-2026', note: 'In 45 days' }, { label: 'Payments', value: '0' }])),
    'kv-list': () => KeyValueList([f.select, f.currency, f.date], { save: () => true }),
    'empty-state': () => EmptyState({ icon: 'circle-check', tint: 'teal', title: 'No tasks', text: 'Add a follow-up so nothing slips.', action: Button({ label: 'Add task', icon: 'plus', size: 'small' }) }),
    banner: () => stack(StateBanner({ tone: 'neutral', title: 'Draft.', text: 'Everything can change. Issuing assigns the number and locks the invoice.' }),
      StateBanner({ tone: 'danger', title: 'Void.', text: 'Customer cancelled the order. Postings were reversed and the number is kept.' })),
    'info-note': () => InfoNote('Rows here were written by this invoice. They can’t be edited; void or revise the invoice instead.'),
    'filter-chip': () => Filters({ filters }),
    toast: () => row(Button({ label: 'Show a toast', onClick: () => toast('Moved to Discovery', { undo: () => toast('Moved back') }) }),
      html`<div class="toast lib-static">${Icon('check', { size: 'sm', className: 'ok' })}<span>Saved</span><button type="button">Undo</button></div>`),
    'data-table': () => html`<div style="width:100%">${DataTable({ columns: dealColumns, rows: sampleDeals, key: deal => deal.id, label: 'Deals', selectable: true, selection: selected, height: 300,
      totals: { amount: money(sampleDeals.reduce((sum, deal) => sum + deal.amount, 0)) }, totalsLabel: `${sampleDeals.length} deals`, onOpen: deal => toast(`Opened ${deal.title}`) })}</div>`,
    'board-card': () => BoardCard({ title: 'ERP rollout, 3 entities', tags: ['Hot'], company: 'Al Sahra Trading', value: 'AED 186,000', date: '05 Nov', owner: 'Aslam K' }),
    // layout
    'page-header': () => PageHeader({ icon: 'handshake', tint: 'blue', title: 'Deals', count: 42, actions: [Button({ label: 'Import', icon: 'upload' }), Button({ label: 'New deal', icon: 'plus', variant: 'primary' })] }),
    tabs: () => Tabs({ tabs: [{ id: 'all', label: 'All deals', count: 42 }, { id: 'mine', label: 'Mine', count: 12 }, { id: 'closing', label: 'Closing this month', count: 5 }], active: cell('all'), label: 'Saved views' }),
    toolbar: () => Toolbar({ left: [SearchInput({ value: cell(''), placeholder: 'Search deals' }), Filters({ filters })], right: [ListSummary([{ label: 'Open', value: '12' }, { label: 'Pipeline', value: 'AED 1.15M' }])] }),
    'split-view': () => {
      const open = cell(true), width = cell(260);
      return html`<div style="width:100%;height:180px;position:relative;border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden">${SplitView({
        main: html`<div style="padding:12px">${Button({ label: () => open.get() ? 'Close the panel' : 'Open the panel', size: 'small', onClick: () => open.set(!open.peek()) })}</div>`,
        side: () => html`<p class="muted" style="padding:12px">Side panel</p>`, open, width, label: 'Resize the sample panel', minSide: 160, minMain: 120, initial: 260, breakpoint: 360,
      })}</div>`;
    },
    'doc-frame': () => html`<div style="width:100%;border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden">${DocFrame({ tint: 'blue', main: html`<p class="muted">Main column</p>`, side: html`<p class="muted">Side panel</p>` })}</div>`,
    'side-block': () => html`<div style="width:300px">${SideBlock({ title: 'Tags', body: row(Chip({ label: 'Hot', icon: 'flame', tone: 'rose' }), AddChip({ label: 'Tag' })) })}</div>`,
    drawer: () => Button({ label: 'Open a drawer', icon: 'panel-right', onClick: () => openDrawer({ title: 'Contact', body: () => KeyValueList([f.email, f.phone, f.tags], { save: () => true }),
      footer: close => Button({ label: 'Open record', variant: 'primary', onClick: () => { close(); toast('Opened the record'); } }) }) }),
    modal: () => Button({ label: 'Open a dialog', icon: 'sparkles', onClick: () => openCreate() }),
    popover: () => { const button: HTMLButtonElement = Button({ label: 'Open a menu', icon: 'chevron-down', onClick: () => openPopover(button, close => Menu([{ label: 'Copy link', icon: 'link', onSelect: () => toast('Copied the link') }, { label: 'Delete', icon: 'trash-2', danger: true, onSelect: () => toast('Deleted') }], close)) }); return button; },
    'bulk-bar': () => html`<div class="lib-bulk">${BulkBar({ count: () => selected.get().size, actions: [{ label: 'Assign', icon: 'user', onClick: () => toast('Assigned') }, { label: 'Delete', icon: 'trash-2', onClick: () => toast('Deleted') }], onClear: () => selected.set(new Set()) })}</div>`,
    // sections
    'doc-header': onRecord(store => DocHeader({ type: 'Order', icon: 'receipt', mark: RecordMark({ name: () => String(store.field('title').value.get() ?? ''), size: 'lg' }), title: store.field('title'),
      meta: [html`<b class="num">${() => formatMoney(store.field('total').value.get() as string)}</b>`, displayValue(store.field('customer')), html`${Icon('calendar', { size: 'xs' })}${() => `Due ${displayDate(store.field('due').value.get())}`}`],
      actions: [Button({ label: 'Email', icon: 'mail', iconTint: 'blue', hideLabelOnPhone: true }), MoveButton({ label: 'Mark won', icon: 'trophy', variant: 'success', blocked: () => store.dirty.get() ? 'Save or discard your changes first' : null, onMove: () => toast('Moved to Won') }),
        MenuButton({ icon: 'ellipsis', tip: 'More actions', items: [{ label: 'Copy link', icon: 'link', onSelect: () => toast('Copied the link') }] })] }),
      () => DocHeader({ type: 'Deal', icon: 'handshake', mark: RecordMark({ name: 'Al Sahra Trading', size: 'lg' }), title: 'ERP rollout, 3 entities', meta: [html`<b class="num">AED 186,000</b>`, 'Al Sahra Trading'] })),
    'status-flow': () => html`<div style="width:100%">${StatusFlow({ steps: STAGES.slice(0, 5), current: flowState, good: ['won'], onMove: to => { flowState.set(to); toast(`Moved to ${stageOf(to).label}`); },
      blocked: to => to === 'won' && flowState.peek() !== 'negotiation' ? 'Move to Negotiation before marking it won' : null })}</div>`,
    'state-banner': () => stack(SegmentedControl({ options: [{ value: 'draft', label: 'Draft' }, { value: 'locked', label: 'Issued' }, { value: 'void', label: 'Void' }], value: lifecycle, size: 'small', label: 'Lifecycle' }),
      LockBanner({ lifecycle, state: 'Issued', lockedText: 'Only remarks can change. To correct it, void or revise.', reason: 'Customer cancelled the order.', onRevise: () => toast('Opened a revision'), onVoid: () => toast('Opened Void invoice') })),
    'child-rows': onRecord(store => html`<div style="width:100%;max-width:420px">${ChildRows({ table: store.table('lines'), title: 'Lines' })}</div>`,
      () => html`<span class="muted">Needs a record. Open this page from the dev server to see the sample order.</span>`),
    'child-table': onRecord(store => html`<div style="width:100%">${ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'rate', 'amount'], title: 'Lines', icon: 'package', crumb: () => String(store.field('title').value.get() ?? ''), height: 340 })}</div>`,
      () => html`<span class="muted">Needs a record. Open this page from the dev server to see the sample order.</span>`),
    'form-section': onRecord(store => html`<div style="width:100%">${FormSection({ title: 'Details', note: 'Values save as you change them.', fields: ['title', 'customer', 'stage', 'issued', 'due', 'reason', 'remark', 'total'].map(name => store.field(name)) })}</div>`,
      () => FormSection({ title: 'Details', fields: [f.text, f.select, f.computed], save: () => true })),
    board: () => html`<div class="lib-scroll" style="width:100%">${Board({ columns: STAGES.slice(0, 4), cards: board, key: deal => deal.id, group: deal => deal.stage, measure: deal => deal.amount, label: 'Deals by stage',
      card: deal => BoardCard({ title: () => deal().title, company: () => deal().company, value: () => money(deal().amount), date: () => deal().close.slice(0, 5), owner: () => deal().owner }),
      onMove: (deal, to) => { board.set(board.peek().map(item => item.id === deal.id ? { ...item, stage: to } : item)); return null; }, onAdd: column => toast(`Opened New deal in ${column.label}`), addLabel: 'Add deal' })}</div>`,
    'list-view': () => html`<div style="width:100%">${PageHeader({ icon: 'handshake', tint: 'blue', title: 'Deals', count: 42 })}${Tabs({ tabs: [{ id: 'all', label: 'All deals', count: 42 }, { id: 'mine', label: 'Mine', count: 12 }], active: cell('all'), label: 'Saved views' })}
      ${Toolbar({ left: [SearchInput({ value: cell(''), placeholder: 'Search deals' })] })}${DataTable({ columns: dealColumns.slice(0, 4), rows: deals(12), key: deal => deal.id, label: 'Deals list', height: 240 })}</div>`,
    'create-form': () => Button({ label: 'New deal', icon: 'plus', variant: 'primary', onClick: () => openCreate() }),
    'action-dialog': () => Button({ label: 'Record payment', icon: 'wallet', onClick: () => openPayment() }),
    'peek-drawer': () => Button({ label: 'Peek a contact', icon: 'panel-right', onClick: () => openDrawer({ title: 'Contact', body: () => html`<div class="peek-top">${Avatar({ name: 'Khalid Al Mansoori', size: 'xl' })}<div><h2>Khalid Al Mansoori</h2><div class="sub">Finance manager, Al Sahra Trading</div></div></div>${KeyValueList([f.email, f.phone, f.tags], { save: () => true })}`,
      footer: close => Button({ label: 'Open record', variant: 'primary', onClick: () => { close(); toast('Opened the record'); } }) }) }),
    'side-panel': () => html`<div class="doc-side" style="width:320px;border:1px solid var(--border);border-radius:var(--r-lg)">${SideBlock({ title: 'Key fields', body: KeyValueList([f.select, f.currency], { save: () => true }), action: LinkButton({ label: 'All fields' }) })}
      ${SideBlock({ title: 'Tags', body: row(Chip({ label: 'Renewal' }), AddChip({ label: 'Tag' })) })}</div>`,
  };
}

const displayDate = (value: unknown) => typeof value === 'string' ? value.split('-').reverse().join('-') : 'Not set';

/** A create form: fields a new record needs, validated in place before it closes. */
function openCreate() {
  const name = localField({ name: 'deal', type: 'text', label: 'Deal name', required: true });
  const amount = localField({ name: 'amount', type: 'currency', label: 'Amount', scale: 2, required: true });
  const stage = localField({ name: 'stage', type: 'select', label: 'Stage', choices: STAGES.slice(0, 4).map(item => item.value) }, 'qualification');
  const submit = (close: () => void) => {
    let ok = true;
    for (const item of [name, amount]) if (item.value.peek() === null || item.value.peek() === '') { item.error.set(`${item.meta.label} is required`); ok = false; }
    if (ok) { close(); toast(`Created ${name.value.peek()}`); }
  };
  let closeIt = () => {};
  closeIt = openModal({ title: 'New deal', icon: 'handshake', tint: 'blue', onSubmit: () => submit(closeIt),
    body: () => [BaseField({ field: name, full: true }), BaseField({ field: amount }), BaseField({ field: stage })],
    footer: close => DialogFooter({ note: () => '⌘ Enter to save', cancel: close, submit: { label: 'Create deal', onClick: () => submit(close) } }) });
}

/** An action dialog with inputs, validated against the record ("Outstanding is only AED 25,200"). */
function openPayment() {
  const amount = localField({ name: 'amount', type: 'currency', label: 'Amount', scale: 2, required: true }, '25200.00');
  const date = localField({ name: 'date', type: 'date', label: 'Received on', required: true }, '2026-11-01');
  const submit = (close: () => void) => {
    if (Number(amount.value.peek() ?? 0) > 25200) { amount.error.set('Outstanding is only AED 25,200'); return; }
    if (!amount.value.peek()) { amount.error.set('Amount is required'); return; }
    close();
    toast(`Recorded payment of ${formatMoney(amount.value.peek() as string)}`);
  };
  let closeIt = () => {};
  closeIt = openModal({ title: 'Record payment', icon: 'wallet', tint: 'green', narrow: true, onSubmit: () => submit(closeIt),
    body: () => [BaseField({ field: amount }), BaseField({ field: date })],
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Record payment', onClick: () => submit(close) } }) });
}

// ---------- the page ----------

/** Details of a component in a popup: description, model, properties, variants, built from, used by. */
export function openDetails(id: string, demo?: Demo) {
  const entry = ENTRIES.find(item => item.id === id);
  if (!entry) return;
  const by = ENTRIES.filter(item => item.from.includes(id));
  let close = () => {};
  const chips = (ids: string[]) => html`<div class="tag-row">${ids.map(other => ENTRIES.find(item => item.id === other)).filter(Boolean).map(item => html`<button type="button" class="chip lib-link"
    on:click=${() => { close(); openDetails(item!.id, LIBRARY_DEMOS?.[item!.id]); }}>${item!.name}</button>`)}</div>`;
  close = openModal({
    title: entry.name, className: 'lib-modal',
    body: () => html`<div class="lib-mb" style="grid-column:1/-1;padding:0">
      <div class="lib-kv"><span class=${`lvl b-${tint(LEVELS[entry.level])}`}>${entry.level}</span><span>${GROUPS.find(group => group[0] === entry.cat)?.[1] ?? ''}</span></div>
      ${demo ? html`<div class="lib-prev" style="margin-top:12px">${demo()}</div>` : html`<p class="muted">Built with the screens in a later phase.</p>`}
      <p class="lib-desc">${entry.desc}</p>
      ${entry.model ? html`<div class="lib-kv"><span>Model</span><code>${entry.model}</code></div>` : null}
      ${entry.props.length ? html`<h4>Properties</h4><table class="lib-props"><tbody>${entry.props.map(([name, type, note]) => html`<tr><td><code>${name}</code></td><td class="muted">${type}</td><td>${note}</td></tr>`)}</tbody></table>` : null}
      ${entry.variants.length ? html`<h4>Variants and states</h4><div class="tag-row">${entry.variants.map(variant => Chip({ label: variant }))}</div>` : null}
      ${entry.from.length ? html`<h4>Built from</h4>${chips(entry.from)}` : null}
      ${by.length ? html`<h4>Used by components</h4>${chips(by.map(item => item.id))}` : null}
      ${entry.used.length ? html`<h4>Appears in</h4><div class="tag-row">${entry.used.map(place => Chip({ label: place }))}</div>` : null}</div>`,
  });
}

let LIBRARY_DEMOS: Partial<Record<string, Demo>> | null = null;

/** The ids of catalog components that have a live demo here (the ones this library implements). */
export const builtIds = (record: RecordStore | null = null) => Object.keys(demos(record));

/** The Library page: an index on the left, the component cards on the right. */
export function LibraryPage(options: { record?: RecordStore | null; theme?: Cell<'system' | 'light' | 'dark'> } = {}): HTMLElement {
  const record = options.record ?? null;
  const all: Partial<Record<string, Demo>> = LIBRARY_DEMOS = demos(record);
  const query = cell('');
  const active = cell<string | null>(null);
  const matches = (entry: Entry) => !query.get() || `${entry.name} ${entry.cat}`.toLowerCase().includes(query.get().toLowerCase());
  const count = derive(() => ENTRIES.filter(matches).length);
  const built = ENTRIES.filter(entry => all[entry.id]).length;
  const theme = options.theme ?? cell<'system' | 'light' | 'dark'>('system');
  const cardId = (id: string) => `libc-${id}`;

  const card = (entry: Entry) => {
    const demo = all[entry.id];
    const wide = entry.level === 'Section' || WIDE.has(entry.id);
    return html`<article class="lib-card" class:wide=${wide} id=${cardId(entry.id)} data-libcard=${entry.id} hidden=${() => !matches(entry)} aria-labelledby=${`${cardId(entry.id)}-name`}>
      <div class="lib-demo">${demo ? demo() : html`<span class="muted lib-later">Built with the screens in a later phase</span>`}</div>
      <button type="button" class="lib-foot" on:click=${() => openDetails(entry.id, demo && !wide ? demo : undefined)}><b id=${`${cardId(entry.id)}-name`}>${entry.name}</b>
        <span class=${`lvl b-${tint(LEVELS[entry.level])}`}>${entry.level}</span><span class="grow"></span><span class="muted">Details</span>${Icon('chevron-right', { size: 'xs', className: 'muted' })}</button></article>`;
  };
  const sections = GROUPS.map(([key, name, icon, color]) => {
    const list = ENTRIES.filter(entry => entry.cat === key);
    return html`<section class="lib-sec" id=${`lib-${key}`} hidden=${() => !list.some(matches)} aria-labelledby=${`lib-${key}-h`}>
      <div class="lib-sh">${IconTile(icon, { tint: color, size: 26 })}<h2 id=${`lib-${key}-h`}>${name}</h2><span class="muted num">${() => String(list.filter(matches).length)}</span></div>
      <div class="lib-grid">${list.map(card)}</div></section>`;
  });
  const jump = (id: string) => {
    const target = document.getElementById(cardId(id));
    const view = page.querySelector<HTMLElement>('.view');
    if (!target || !view) return;
    const header = page.querySelector<HTMLElement>('.lib .ph')?.offsetHeight ?? 0;
    view.scrollTo?.({ top: view.scrollTop + target.getBoundingClientRect().top - view.getBoundingClientRect().top - header - 14, behavior: 'smooth' });
    active.set(id);
    target.querySelector<HTMLElement>('.lib-foot')?.focus({ preventScroll: true });
  };
  const index = GROUPS.map(([key, name]) => {
    const list = ENTRIES.filter(entry => entry.cat === key);
    return html`<div class="nav-group" hidden=${() => !list.some(matches)}><div class="nav-label">${name}<span>${() => String(list.filter(matches).length)}</span></div>
      ${list.map(entry => html`<button type="button" class="nav-item lib-idx" class:active=${() => active.get() === entry.id} aria-current=${() => active.get() === entry.id ? 'true' : null}
        hidden=${() => !matches(entry)} data-idx=${entry.id} on:click=${() => jump(entry.id)}><span class="lvl-dot" style:background=${`var(--t-${tint(LEVELS[entry.level])})`}></span><span class="lbl">${entry.name}</span>${all[entry.id] ? null : html`<span class="count">later</span>`}</button>`)}</div>`;
  });
  const page = html`<div class="app lib-app">
    <aside class="sidebar" aria-label="Component index"><div class="side-body">
      <div class="side-app"><b>Library</b><span>${String(ENTRIES.length)}</span></div>
      <nav class="nav"><div class="lib-filter search-field">${SearchInput({ value: query, placeholder: 'Filter components', label: 'Filter components' })}</div>${index}</nav></div></aside>
    <main class="main"><div class="view"><div class="page lib">
      <div class="ph">${IconTile('layers', { tint: 'violet', size: 32 })}<h1>Library</h1><span class="count num">${() => `${count.get()} components, ${built} live`}</span><span class="grow"></span>
        <div class="lib-legend">${Object.entries(LEVELS).map(([level, color]) => html`<span class=${`lvl b-${tint(color)}`}>${level}</span>`)}</div>
        ${SegmentedControl({ options: [{ value: 'system', label: 'System' }, { value: 'light', label: 'Light', icon: 'sun' }, { value: 'dark', label: 'Dark', icon: 'moon' }], value: theme, size: 'small', label: 'Theme' })}</div>
      ${sections}${when(() => count.get() === 0, () => EmptyState({ icon: 'search', tint: 'slate', title: 'No components match', text: 'Try another name.' }))}</div></div></main></div>` as HTMLElement;
  // Scrolling marks the card in view in the index.
  const view = page.querySelector<HTMLElement>('.view')!;
  let frame = 0;
  view.addEventListener('scroll', () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const line = view.getBoundingClientRect().top + 90;
      const card = [...page.querySelectorAll<HTMLElement>('[data-libcard]:not([hidden])')].find(item => item.getBoundingClientRect().bottom > line);
      if (card && card.dataset.libcard !== active.peek()) active.set(card.dataset.libcard!);
    });
  }, { passive: true });
  return page;
}

