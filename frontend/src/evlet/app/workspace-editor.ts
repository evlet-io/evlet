// The workspace editor (docs/ui.md, Customizing workspaces): admins and roles with "Customize workspaces"
// reorder, hide and rename a workspace's groups and items, add shortcuts and whole workspaces, and reset
// to the declared workspace. It edits the site overrides through the Controls routes
// (GET /api/_controls/workspaces, PUT and DELETE /api/_controls/workspaces/<key>), which check everything
// again; a 422 names each problem by its path (groups.0.items.2.shortcut) and shows beside that row.
// Nothing here widens access: the schema filters every item for each role after the override.

import { html } from '../dom';
import { cell, type Cell } from '../reactive';
import { ApiError } from '../api';
import { Button, Icon } from '../ui/base';
import { dragList } from '../ui/draglist';
import { DialogFooter, openModal, toast } from '../ui/layers';
import type { AppSchema, WorkspaceGroup, WorkspaceItem, WorkspaceSchema } from '../model/schema';
import type { AppContext } from './context';
import { rootModels } from './meta';
import { replaceWorkspaces } from './workspaces';
import { KIND_ICONS, SHELL_ICONS, kindOf, type ShortcutKind } from './nav-icons';

/** A shortcut as the override sends it: the arguments of evlet.Shortcut. */
export interface ShortcutSpec { model?: string; view?: 'list' | 'board'; filters?: unknown; label?: string; report?: string; slip?: string; url?: string }
/** An item of a saved override. */
export interface OverrideItem { key?: string; shortcut?: ShortcutSpec; label?: string; hidden?: boolean }
export interface OverrideGroup { key: string; label?: string; icon?: string; hidden?: boolean; items?: OverrideItem[] }
export interface Override { custom?: boolean; label?: string; icon?: string; groups?: OverrideGroup[] }
/** One workspace in GET /api/_controls/workspaces. */
export interface ListingItem {
  key: string; label: string; source: WorkspaceSchema['source'] | 'unknown'; app: string | null;
  override: Override | null; changed_by: string | null; changed_at: number | null;
  declared: WorkspaceSchema | null; effective: WorkspaceSchema | null;
}
export interface Listing { items: ListingItem[]; problems: Array<{ key?: string; text?: string; message?: string }> }

const ROUTE = '/api/_controls/workspaces';

// ---------- access ----------

// GET /api/_settings/me says whether this person may customize workspaces (`customize_workspaces`, the
// routes' own check), so the switcher never asks the routes just to find out.
const access = new WeakMap<AppContext, Cell<boolean>>();
export function customizeAccess(ctx: AppContext): Cell<boolean> {
  let found = access.get(ctx);
  if (!found) access.set(ctx, found = cell(ctx.me.peek()?.customize_workspaces === true, { name: 'workspaces.customize' }));
  return found;
}

// ---------- the editor's model ----------

export interface EdItem { id: string; key?: string; shortcut?: ShortcutSpec; label: string; base: string | null; hidden: boolean;
  /** What a declared item opens; an added shortcut's kind comes from its spec. */ kind?: ShortcutKind }
export interface EdGroup { id: string; key: string; label: string; base: string | null; icon?: string; hidden: boolean; items: EdItem[] }
export interface EdWorkspace { key: string; custom: boolean; label: string; base: string | null; icon?: string; groups: EdGroup[] }

let ids = 0;
const nextId = () => `w${++ids}`;

/** How a shortcut reads before the server names it. */
export function shortcutLabel(spec: ShortcutSpec, schema?: AppSchema): string {
  if (spec.label) return spec.label;
  if (spec.model) return schema?.models.find(model => model.name === spec.model)?.plural ?? spec.model;
  if (spec.report) return schema?.reports?.find(report => report.name === spec.report)?.label ?? spec.report;
  if (spec.slip) return schema?.slips?.find(slip => slip.name === spec.slip)?.label ?? spec.slip;
  return spec.url ?? 'Shortcut';
}

/**
 * The editor's view of a workspace: its saved override laid over the declared groups the way the server applies
 * it, but with hidden groups and items kept (so they can be shown again) and with each declared label kept for
 * comparison.
 */
export function editable(entry: ListingItem, schema?: AppSchema): EdWorkspace {
  const declared: WorkspaceGroup[] = entry.declared?.groups ?? [];
  const override: Override = entry.override ?? {};
  const custom = Boolean(override.custom) || entry.source === 'custom';
  const byKey = new Map<string, WorkspaceItem>(declared.flatMap(group => group.items.map(item => [item.key, item] as const)));
  const placed = new Set((override.groups ?? []).flatMap(group => (group.items ?? []).flatMap(item => item.key ? [item.key] : [])));
  const declaredItem = (item: WorkspaceItem): EdItem => ({ id: nextId(), key: item.key, label: item.label, base: item.label, hidden: false, kind: kindOf(item) });
  const groups: EdGroup[] = (override.groups ?? []).map(group => {
    const base = declared.find(entry => entry.key === group.key);
    const items: EdItem[] = (group.items ?? []).map(spec => {
      if (spec.shortcut) return { id: nextId(), shortcut: spec.shortcut, label: spec.label ?? shortcutLabel(spec.shortcut, schema), base: shortcutLabel(spec.shortcut, schema), hidden: Boolean(spec.hidden) };
      const known = byKey.get(spec.key!);
      return { id: nextId(), key: spec.key, label: spec.label ?? known?.label ?? spec.key!, base: known?.label ?? null, hidden: Boolean(spec.hidden), kind: known ? kindOf(known) : undefined };
    });
    if (base) items.push(...base.items.filter(item => !placed.has(item.key)).map(declaredItem));
    return { id: nextId(), key: group.key, label: group.label ?? base?.label ?? group.key, base: base?.label ?? null, icon: group.icon, hidden: Boolean(group.hidden), items };
  });
  const listed = new Set(groups.map(group => group.key));
  groups.push(...declared.filter(group => !listed.has(group.key)).map(group => ({ id: nextId(), key: group.key, label: group.label, base: group.label, hidden: false,
    items: group.items.filter(item => !placed.has(item.key)).map(declaredItem) })));
  const baseLabel = entry.declared?.label ?? (custom ? null : entry.label);
  return { key: entry.key, custom, label: override.label ?? entry.effective?.label ?? baseLabel ?? entry.label, base: custom ? null : baseLabel, icon: override.icon, groups };
}

/** The override that gives this view: every group and item in order, with only what differs from the declared workspace. */
export function overrideOf(workspace: EdWorkspace): Override {
  const changed = (label: string, base: string | null) => base === null || label.trim() !== base;
  const result: Override = {};
  if (workspace.custom) result.custom = true;
  if (workspace.custom || changed(workspace.label, workspace.base)) result.label = workspace.label.trim();
  if (workspace.icon) result.icon = workspace.icon;
  result.groups = workspace.groups.map(group => {
    const entry: OverrideGroup = { key: group.key };
    if (changed(group.label, group.base)) entry.label = group.label.trim();
    if (group.icon) entry.icon = group.icon;
    if (group.hidden) entry.hidden = true;
    entry.items = group.items.map(item => {
      const spec: OverrideItem = item.shortcut ? { shortcut: item.shortcut } : { key: item.key };
      if (changed(item.label, item.base)) spec.label = item.label.trim();
      if (item.hidden) spec.hidden = true;
      return spec;
    });
    return entry;
  });
  return result;
}

/** Whether a view of a declared workspace is just the declared workspace: saving it is a reset. */
export function isDeclared(workspace: EdWorkspace, entry: ListingItem): boolean {
  if (workspace.custom) return false;
  const pristine = editable({ ...entry, override: null });
  const strip = (value: Override) => JSON.stringify(value);
  return strip(overrideOf(workspace)) === strip(overrideOf(pristine));
}

/** A workspace key from its label: lowercase letters, digits and underscores, starting with a letter. */
export function keyOf(label: string, taken: Set<string>): string {
  const base = (label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^(?=[0-9])/, 'w_') || 'workspace').slice(0, 56);
  let key = base, n = 2;
  while (taken.has(key)) key = `${base}_${n++}`;
  return key;
}

/** 422 problems by where they show: the form, a group (groups.0) or an item (groups.0.items.2), as the rows are sent. */
export function problemsAt(fields: Record<string, string>): { form: string[]; at: Map<string, string[]> } {
  const form: string[] = [], at = new Map<string, string[]>();
  for (const [path, text] of Object.entries(fields)) {
    const match = /^(groups\.\d+(?:\.items\.\d+)?)/.exec(path);
    if (!match) { form.push(path === '_form' ? text : `${path === 'label' ? 'Name' : path}: ${text}`); continue; }
    at.set(match[1], [...(at.get(match[1]) ?? []), text]);
  }
  return { form, at };
}

// ---------- the dialog ----------

/** A small question over the editor; resolves true for the action. */
function ask(title: string, text: string, action: string): Promise<boolean> {
  return new Promise(resolve => {
    let answered = false;
    openModal({ title, icon: 'info', tint: 'amber', narrow: true, body: () => html`<p>${text}</p>`,
      footer: done => DialogFooter({ cancel: () => { answered = true; resolve(false); done(); }, submit: { label: action, variant: 'danger', onClick: () => { answered = true; resolve(true); done(); } } }),
      onClose: () => { if (!answered) resolve(false); } });
  });
}
const UNSAVED = 'Your changes to this workspace are not saved.';

/** Opens the editor on a workspace (the current one), loading the site's listing first. */
export async function openWorkspaceEditor(ctx: AppContext, key: string | null): Promise<void> {
  let listing: Listing;
  try { listing = (await ctx.api.request<Listing>('GET', ROUTE)).data; }
  catch (error) { toast(error instanceof ApiError ? error.message : 'The workspaces could not be loaded.'); return; }
  WorkspaceEditor(ctx, listing, key);
}

export function WorkspaceEditor(ctx: AppContext, start: Listing, key: string | null): () => void {
  const schema = ctx.schema.schema;
  let listing = start;
  const pick = (wanted: string | null) => listing.items.find(item => item.key === wanted && item.source !== 'unknown') ?? listing.items.find(item => item.source !== 'unknown') ?? null;
  let entry = pick(key);
  let view: EdWorkspace | null = entry ? editable(entry, schema) : null;
  const tick = cell(0, { name: 'workspaces.editor' });
  const redraw = () => tick.set(tick.peek() + 1);
  const problems = cell<{ form: string[]; at: Map<string, string[]> }>({ form: [], at: new Map() });
  const busy = cell(false);
  const adding = cell<string | null>(null);
  let dirty = false;
  const touch = () => { dirty = true; };

  const choose = async (next: string) => {
    if (dirty && !await ask('Discard your changes?', UNSAVED, 'Discard changes')) { redraw(); return; }
    entry = pick(next); view = entry ? editable(entry, schema) : null; dirty = false;
    problems.set({ form: [], at: new Map() }); adding.set(null); redraw();
  };

  const settle = async (request: Promise<{ data: Listing }>, done: string) => {
    busy.set(true);
    try {
      listing = (await request).data;
      problems.set({ form: [], at: new Map() });
      const fresh = await ctx.api.schema<AppSchema>();
      replaceWorkspaces(ctx, fresh.workspaces);
      entry = pick(view?.key ?? null); view = entry ? editable(entry, schema) : null; dirty = false;
      toast(done); redraw();
    } catch (error) {
      // Problems name rows by their place as sent: draw the rows again in that order.
      if (error instanceof ApiError && error.status === 422) problems.set(problemsAt(error.fields));
      else problems.set({ form: [error instanceof ApiError ? error.message : 'The change could not be saved.'], at: new Map() });
      redraw();
    } finally { busy.set(false); }
  };
  const save = () => {
    if (!view || !entry || busy.peek()) return;
    if (!view.label.trim()) { problems.set({ form: ['Give the workspace a name.'], at: new Map() }); return; }
    const isNew = !listing.items.some(item => item.key === view!.key);
    // A new workspace takes its key from its name when it is first saved.
    if (isNew) view.key = keyOf(view.label, new Set(listing.items.map(item => item.key)));
    if (!isNew && isDeclared(view, entry)) {
      if (!entry.override) { toast('Nothing to save.'); return; }
      void settle(ctx.api.request<Listing>('DELETE', `${ROUTE}/${view.key}`), `${view.label} is back to its declared layout`);
      return;
    }
    void settle(ctx.api.request<Listing>('PUT', `${ROUTE}/${view.key}`, { body: overrideOf(view) }), `Saved ${view.label.trim()}`);
  };
  const reset = async () => {
    if (!view || !entry) return;
    const custom = view.custom;
    if (!listing.items.some(item => item.key === view!.key)) { dirty = false; void choose(listing.items[0]?.key ?? ''); return; }
    if (!await ask(custom ? 'Remove this workspace?' : 'Reset to the declared workspace?', custom ? `${view.label} is removed for everyone on this site.`
      : `The changes this site made to ${view.label} are removed for everyone.`, custom ? 'Remove' : 'Reset')) return;
    void settle(ctx.api.request<Listing>('DELETE', `${ROUTE}/${view.key}`), custom ? `Removed ${view.label}` : `${view.label} is back to its declared layout`);
  };
  const addWorkspace = async () => {
    if (dirty && !await ask('Discard your changes?', UNSAVED, 'Discard changes')) return;
    const label = 'New workspace', key = '';
    entry = { key, label, source: 'custom', app: null, override: null, changed_by: null, changed_at: null, declared: null, effective: null };
    view = { key, custom: true, label, base: null, groups: [{ id: nextId(), key: 'shortcuts', label: 'Shortcuts', base: null, hidden: false, items: [] }] };
    dirty = true; adding.set(view.groups[0].id); problems.set({ form: [], at: new Map() }); redraw();
    queueMicrotask(() => document.querySelector<HTMLInputElement>('.wse-top .wse-name')?.select());
  };

  // ---------- moving ----------

  // A move changes the model and moves the row itself, so a keyboard move keeps its lifted row and focus; the
  // rows are drawn again on the next structural change (hide, add, save).
  let rootEl: HTMLElement | null = null;
  const placeRow = (kind: string, id: string, zone: string, before: string | null) => {
    const row = rootEl?.querySelector<HTMLElement>(`[data-drag-item="${kind}"][data-drag-id="${id}"]`);
    const list = rootEl?.querySelector<HTMLElement>(`[data-drop-zone="${kind}"][data-zone="${zone}"]`);
    if (!row || !list) { redraw(); return; }
    const next = before ? list.querySelector<HTMLElement>(`:scope > [data-drag-item="${kind}"][data-drag-id="${before}"]`) : list.querySelector<HTMLElement>(':scope > .wse-empty');
    list.insertBefore(row, next);
  };
  const findItem = (id: string) => { for (const group of view?.groups ?? []) { const at = group.items.findIndex(item => item.id === id); if (at >= 0) return { group, at }; } return null; };
  const moveGroup = (id: string, _zone: string, before: string | null) => {
    if (!view) return;
    const group = view.groups.find(entry => entry.id === id)!, rest = view.groups.filter(entry => entry.id !== id);
    const at = before === null ? rest.length : rest.findIndex(entry => entry.id === before);
    rest.splice(at < 0 ? rest.length : at, 0, group);
    view.groups = rest; touch(); placeRow('ws-group', id, 'groups', before);
  };
  const moveItem = (id: string, zone: string, before: string | null) => {
    const found = findItem(id), target = view?.groups.find(group => group.id === zone);
    if (!found || !target) return;
    const [item] = found.group.items.splice(found.at, 1);
    const at = before === null ? target.items.length : target.items.findIndex(entry => entry.id === before);
    target.items.splice(at < 0 ? target.items.length : at, 0, item);
    touch(); placeRow('ws-item', id, zone, before);
  };

  // ---------- rows ----------

  const grip = (kind: string, label: string) => html`<button type="button" class="col-grip" data-drag-handle=${kind} aria-label=${`Move ${label}`} aria-pressed="false">${Icon('grip-vertical', { size: 'sm' })}</button>`;
  const eye = (label: string, hidden: boolean, flip: () => void) => html`<button type="button" class="col-eye" aria-pressed=${String(!hidden)}
    aria-label=${`${hidden ? 'Show' : 'Hide'} ${label}`} on:click=${flip}>${Icon(hidden ? 'eye-off' : 'eye', { size: 'sm' })}</button>`;
  const nameInput = (value: string, label: string, set: (text: string) => void, placeholder?: string) => html`<input class="input wse-name" aria-label=${label}
    maxlength="80" autocomplete="off" prop:value=${value} placeholder=${placeholder ?? null} on:input=${(event: Event) => { set((event.target as HTMLInputElement).value); touch(); }}>`;
  const notes = (path: string) => () => {
    const list = problems.get().at.get(path);
    return list?.length ? html`<p class="wse-err" role="alert">${list.join(' ')}</p>` : null;
  };

  const itemRow = (group: EdGroup, item: EdItem, gIndex: number, iIndex: number) => html`<div class="wse-item" class:hid=${item.hidden} data-drag-item="ws-item" data-drag-id=${item.id}>
    <div class="wse-line">${grip('ws-item', item.label)}${Icon(KIND_ICONS[item.shortcut ? kindOf(item.shortcut) : item.kind ?? 'list'], { size: 'xs', className: 'wse-kind' })}
      ${nameInput(item.label, `Name of ${item.base ?? item.label}`, text => { item.label = text; }, item.base ?? undefined)}
      ${item.shortcut ? html`<button type="button" class="col-eye" aria-label=${`Remove ${item.label}`} on:click=${() => { group.items = group.items.filter(entry => entry !== item); touch(); redraw(); }}>${Icon('trash-2', { size: 'sm' })}</button>`
        : eye(item.label, item.hidden, () => { item.hidden = !item.hidden; touch(); redraw(); })}</div>
    ${notes(`groups.${gIndex}.items.${iIndex}`)}</div>`;

  const groupRow = (group: EdGroup, gIndex: number) => html`<section class="wse-group" class:hid=${group.hidden} data-drag-item="ws-group" data-drag-id=${group.id}>
    <div class="wse-line wse-gh">${grip('ws-group', group.label)}
      ${nameInput(group.label, `Name of the group ${group.base ?? group.label}`, text => { group.label = text; }, group.base ?? undefined)}
      ${eye(group.label, group.hidden, () => { group.hidden = !group.hidden; touch(); redraw(); })}</div>
    ${notes(`groups.${gIndex}`)}
    <div class="wse-items" data-drop-zone="ws-item" data-zone=${group.id} aria-label=${`Items of ${group.label}`}>
      ${group.items.map((item, index) => itemRow(group, item, gIndex, index))}
      <p class="muted wse-empty">No items. A group without items is hidden.</p></div>
    ${() => adding.get() === group.id ? AddShortcut(schema, ctx, spec => { group.items.push({ id: nextId(), shortcut: spec, label: shortcutLabel(spec, schema), base: shortcutLabel(spec, schema), hidden: false }); touch(); adding.set(null); redraw(); }, () => adding.set(null))
      : html`<button type="button" class="wse-add" on:click=${() => adding.set(group.id)}>${Icon('plus', { size: 'xs' })}<span>Add shortcut</span></button>`}
  </section>`;

  const addGroup = () => {
    if (!view) return;
    const label = 'New group', taken = new Set(view.groups.map(group => group.key));
    let key = 'new_group', n = 2;
    while (taken.has(key)) key = `new_group_${n++}`;
    const group: EdGroup = { id: nextId(), key, label, base: null, hidden: false, items: [] };
    view.groups.push(group); touch(); adding.set(group.id); redraw();
  };

  const body = () => {
    tick.get();
    if (!view) return html`<p class="muted">No workspaces to customize.</p>`;
    const current = view;
    const options = [...listing.items.filter(item => item.source !== 'unknown').map(item => ({ key: item.key, label: item.effective?.label ?? item.label })),
      ...(listing.items.some(item => item.key === current.key) ? [] : [{ key: current.key, label: current.label }])];
    const root = html`<div class="wse">
      <div class="wse-top">
        <label class="wse-field"><span>Workspace</span><select class="input" aria-label="Workspace to customize" on:change=${(event: Event) => void choose((event.target as HTMLSelectElement).value)}>
          ${options.map(option => html`<option value=${option.key} prop:selected=${option.key === current.key}>${option.label}</option>`)}</select></label>
        <label class="wse-field"><span>Name</span>${nameInput(current.label, 'Name of the workspace', text => { current.label = text; }, current.base ?? undefined)}</label>
      </div>
      ${() => problems.get().form.length ? html`<p class="wse-err" role="alert">${problems.get().form.join(' ')}</p>` : null}
      ${entry?.changed_by ? html`<p class="muted wse-meta">${`Changed by ${entry.changed_by}`}</p>` : null}
      <div class="wse-groups" data-drop-zone="ws-group" data-zone="groups">${current.groups.map(groupRow)}</div>
      <div class="wse-more">${Button({ label: 'Add group', icon: 'plus', variant: 'ghost', size: 'small', onClick: addGroup })}
        ${Button({ label: 'New workspace', icon: SHELL_ICONS.workspace, variant: 'ghost', size: 'small', onClick: () => void addWorkspace() })}</div>
      <p class="muted col-hint">Drag a grip to move a group or an item, or focus it and press Space, then the arrow keys. Hiding never removes access: each page still checks it.</p>
    </div>` as HTMLElement;
    rootEl = root;
    dragList(root, { kind: 'ws-group', move: moveGroup, label: id => current.groups.find(group => group.id === id)?.label ?? id });
    dragList(root, { kind: 'ws-item', move: moveItem, label: id => findItem(id)?.group.items[findItem(id)!.at].label ?? id,
      zoneLabel: zone => current.groups.find(group => group.id === zone)?.label ?? zone });
    return root;
  };

  // Closing with unsaved changes asks first; the question closes the editor when answered with Discard.
  const leave = () => {
    if (!dirty) return true;
    void ask('Discard your changes?', UNSAVED, 'Discard changes').then(yes => { if (yes) { dirty = false; closeEditor(); } });
    return false;
  };
  const closeEditor: () => void = openModal({ title: 'Customize workspaces', icon: SHELL_ICONS.customize, tint: 'slate', className: 'wse-modal', onSubmit: save,
    confirmClose: leave,
    body: () => body,
    footer: close => html`${() => { tick.get(); return view && entry ? Button({ label: view.custom ? 'Remove workspace' : 'Reset to declared', icon: 'rotate-ccw', variant: 'ghost', onClick: () => void reset(), disabled: busy }) : null; }}
      <span class="grow"></span>${Button({ label: 'Close', variant: 'ghost', onClick: () => { if (leave()) close(); } })}
      ${Button({ label: () => busy.get() ? 'Saving…' : 'Save', variant: 'primary', onClick: save, disabled: busy })}` });
  return closeEditor;
}

/** The small form that adds a shortcut: a model (list or board, optional filters), a report, a slip or an address. The server checks it on Save. */
function AddShortcut(schema: AppSchema, ctx: AppContext, add: (spec: ShortcutSpec) => void, cancel: () => void): HTMLElement {
  const kind = cell<'model' | 'report' | 'slip' | 'url'>('model');
  const target = cell(''), label = cell(''), view = cell<'list' | 'board'>('list'), filters = cell(''), error = cell('');
  const models = rootModels(ctx.schema), reports = schema.reports ?? [], slips = schema.slips ?? [];
  const choices = () => kind.get() === 'model' ? models.map(model => [model.name, model.plural]) : kind.get() === 'report' ? reports.map(report => [report.name, report.label])
    : kind.get() === 'slip' ? slips.map(slip => [slip.name, slip.label]) : [];
  const submit = () => {
    const spec: ShortcutSpec = {};
    const value = target.peek().trim();
    if (!value) { error.set(kind.peek() === 'url' ? 'Enter an address.' : 'Choose what it opens.'); return; }
    spec[kind.peek()] = value;
    if (kind.peek() === 'model' && view.peek() === 'board') spec.view = 'board';
    if (kind.peek() === 'model' && filters.peek().trim()) {
      try { spec.filters = JSON.parse(filters.peek()); } catch { error.set('Filters are JSON, such as {"done": false}.'); return; }
    }
    if (label.peek().trim()) spec.label = label.peek().trim();
    add(spec);
  };
  const input = (cellOf: Cell<string>, aria: string, placeholder: string | (() => string)) => html`<input class="input" aria-label=${aria} placeholder=${placeholder} autocomplete="off"
    prop:value=${() => cellOf.get()} on:input=${(event: Event) => { cellOf.set((event.target as HTMLInputElement).value); error.set(''); }}>`;
  return html`<div class="wse-new" role="group" aria-label="New shortcut">
    <div class="wse-new-row">
      <select class="input" aria-label="Opens" on:change=${(event: Event) => { kind.set((event.target as HTMLSelectElement).value as never); target.set(''); error.set(''); }}>
        <option value="model">List</option><option value="report">Report</option><option value="slip">Slip</option><option value="url">Address</option></select>
      ${() => kind.get() === 'url' ? input(target, 'Address', '/reports or https://…')
        : html`<select class="input" aria-label="Target" on:change=${(event: Event) => { target.set((event.target as HTMLSelectElement).value); error.set(''); }}>
          <option value="">Choose…</option>${choices().map(([value, text]) => html`<option value=${value}>${text}</option>`)}</select>`}
    </div>
    ${() => kind.get() === 'model' ? html`<div class="wse-new-row">
      <select class="input" aria-label="View" on:change=${(event: Event) => view.set((event.target as HTMLSelectElement).value as 'list' | 'board')}><option value="list">List</option><option value="board">Board</option></select>
      ${input(filters, 'Filters', 'Filters, such as {"done": false}')}</div>` : null}
    <div class="wse-new-row">${input(label, 'Label', () => kind.get() === 'url' ? 'Label (needed)' : 'Label (optional)')}
      ${Button({ label: 'Add', size: 'small', onClick: submit })}${Button({ label: 'Cancel', size: 'small', variant: 'ghost', onClick: cancel })}</div>
    ${() => error.get() ? html`<p class="wse-err" role="alert">${error.get()}</p>` : null}
  </div>` as HTMLElement;
}
