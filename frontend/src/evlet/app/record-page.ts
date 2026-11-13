// The generated record view of a model (docs/ui.md, Compact record page), on the full page and in a list's side
// panel: header with the title, status and actions; the stage bar; key cards; one white card with the state banner,
// the primary area (the first section, or Main, and the Tables) and "more information" rows; the side column with
// Attachments and Activity; the footer. There are no tabs. New records use the same view with inputs and a Create
// button. Everything the model and the role do not send is absent: fields, actions, moves, tables and blocks come
// from the schema and detail.

import { html, match, when, type Child } from '../dom';
import { ApiError, type RecordData } from '../api';
import { legacyRecordId, recordId } from '../ids';
import { uid } from '../ui/core';
import { cell, derive, effect, onCleanup, runWithOwner, untrack, type Cell, type Readable } from '../reactive';
import type { Route } from '../router';
import { RecordStore, type Field, type Row, type Rules } from '../model/record';
import type { FieldSchema } from '../model/schema';
import { Avatar, Button, Chip, EmptyState, Icon, IconButton, InfoNote, RecordMark, StateBanner, SummaryStrip, type StatProps } from '../ui/base';
import { ChildTable, type ChildTableControls } from '../ui/childtable';
import { DocFrame, DocHeader, flowFromState, MoveButton, SideBlock, StatusBadge, StatusFlow, type BadgeLook } from '../ui/doc';
import { currencyFor, currencyPlaces, enterMovesToNextField, fieldFor, localField, RichTextView, statusTint, type FieldLike } from '../ui/fields';
import { autofocusForm, initialFocusField, layoutFocusOrder } from '../ui/autofocus';
import { flowGrid, Folds, isEmpty, revealField, revealFirstError } from '../ui/fold';
import { NoteComposer } from './composer';
import { FieldGrid } from '../ui/inline';
import { humanize } from '../ui/kinds';
import { copyText, DialogFooter, MenuButton, openDrawer, SplitButton, openModal, toast, type MenuItem } from '../ui/layers';
import { DataTable, gridPrefs, type Column } from '../ui/table';
import { RelativeTime } from '../ui/time';
import { formatDate, formatDateTime } from '../model/format';
import type { AppContext } from './context';
import type { JournalEntry, JournalPage, MeasureValues } from './client';
import { displayOf, extrasOf, fieldOf, formatOf, headerFacts, identity, isStateField, listFields, measureLabel, measureView, numberField, screenModel, sections, sequenceField, summaryFields, titleOf, LIST_KEYS, panelView, stateLook, stepLabel, stepOf, type ActionMeta, type DetailExtras, type DetailMove, type ScreenModel } from './meta';
import { cellView } from './list-page';
import { blockFilters, declaresRelated, HtmlContent, MeasureChart, RelatedRecords, type BlockRecord } from './record-blocks';
import { CardRow, fieldsSummary, imageSource, MoreRow, RecordImage, rowNoun, rowsText, valueText } from './record-compact';
import type { LayoutBlock } from '../model/schema';
import { RELATED_PAGE, rangeText, relatedPages, whenVisible, type RelatedPage } from './related-data';
import { filterFields } from './filters';
import { presentSlip, takePrefill } from './slips';
import { openEvaPanel } from '../eva/chat';
import { KEPT, keepChanges, keepsUnsaved, keepWhenHidden } from './unsaved';
import { UnsavedBar } from './unsaved-bar';
import { SCREEN_KEYS, useShortcuts } from './shortcuts';
import { dropDuplicate, duplicateAllowed, duplicateOf, holdDuplicate, takeDuplicate, type DuplicateCopy } from './duplicate';
import { ariaKeys, commitTyped, formatKeys, withShortcut } from '../ui/shortcuts';

const RESERVED = new Set(['tab', 'slip']);
// A reactive part inside a list of children (arrays are read once; a template hole stays live).
const live = (render: () => Child) => html`${render}`;

/** A field as a screen shows it: the State lock and the role's write access close it, with the reason. */
function gate(field: Field, locked: Readable<boolean>, canWrite: boolean): FieldLike {
  const meta: FieldSchema = canWrite ? field.meta : { ...field.meta, read_only: true };
  const stateLocked = derive(() => locked.get() && (field.meta as any).versioned !== false);
  const rules = derive<Rules>(() => { const rules = field.rules.get(); return stateLocked.get() && !rules.locked ? { ...rules, locked: true } : rules; });
  const editable = derive(() => canWrite && field.editable.get() && !stateLocked.get());
  // Locked by the record (its state, or no write access): the banner or header says so once, not each field.
  const recordLocked = derive(() => !canWrite || stateLocked.get());
  // A view over the field (its prototype), so recordOf() still finds the record; writes go to the field itself.
  return Object.create(field, {
    meta: { value: meta }, rules: { value: rules }, editable: { value: editable }, recordLocked: { value: recordLocked },
    set: { value: (value: unknown) => field.set(value) },
    touched: { get: () => field.touched, set: (value: boolean) => { field.touched = value; } },
  }) as FieldLike;
}

/** Ask before discarding unsaved changes; resolves true to leave. */
export function confirmDiscard(reason?: string): Promise<boolean> {
  return new Promise(resolve => {
    let answered = false;
    openModal({ title: 'Discard unsaved changes?', icon: 'info', tint: 'amber', narrow: true,
      // A reason says why they could not be kept for later (app/unsaved.ts).
      body: () => html`<p>${reason ? `Your changes could not be kept for later. ${reason} ` : ''}Your changes have not been saved. Leave this page and discard them?</p>`,
      footer: done => DialogFooter({ cancel: () => { answered = true; resolve(false); done(); }, submit: { label: 'Discard changes', variant: 'danger', onClick: () => { answered = true; resolve(true); done(); } } }),
      onClose: () => { if (!answered) resolve(false); } });
  });
}

/**
 * Values a create link may set: only fields the form shows and the role can write. The schema
 * marks fields the role cannot write as read_only; hidden, calculated, State and Sequence fields
 * are skipped too, so a link cannot set what the person cannot see or change.
 */
function initialValues(model: ScreenModel, query: Record<string, string>) {
  const values: Record<string, unknown> = {};
  for (const [name, text] of Object.entries(query)) {
    const meta = fieldOf(model, name);
    if (RESERVED.has(name) || !meta || meta.hidden || meta.read_only || meta.compute || meta.type === 'sequence' || isStateField(model, meta)) continue;
    values[name] = meta.type === 'link' ? recordId(text) ?? legacyRecordId(text)
      : meta.type === 'integer' ? (Number.isSafeInteger(Number(text)) ? Number(text) : null) : meta.type === 'boolean' ? text === 'true' : text;
  }
  return values;
}

/**
 * What a list gives a record it opens in its side panel. The address stays the list's, with the
 * record in `?open=`.
 */
export interface PanelHost {
  /** True while the panel covers the list (narrow screens): the header shows Back instead of Expand. */
  overlay: Readable<boolean>;
  /** Whether a navigation keeps this record open: only the list's search or filters change. */
  stays(to: Route<any> | null): boolean;
  close(): void;
  /** Open another record of the model in the panel, the one just created, or 'new'. Resolves false when the navigation was refused. */
  show(id: string): void | Promise<boolean>;
  /** The record was deleted. */
  removed(): void;
}

// A record expanded from a side panel to its page keeps its store, so unsaved edits come along (and a
// duplicate keeps saying what it copies).
type Handoff = { model: string; id: string; store: RecordStore; duplicate?: DuplicateCopy | null };
let handoff: Handoff | null = null;
const handedOff = new WeakSet<RecordStore>();
function takeHandoff(model: string, id: string): Handoff | null {
  const found = handoff && handoff.model === model && handoff.id === id ? handoff : null;
  handoff = null;
  return found;
}

export function RecordPage(ctx: AppContext, name: string, idText: string, panel?: PanelHost): HTMLElement {
  const { api, router } = ctx;
  const model = screenModel(ctx.schema, name);
  const isNew = idText === 'new';
  // What held the focus when the form was asked for: autofocus leaves alone a focus moved while it loads.
  const focusedAtOpen = isNew ? document.activeElement : null;
  // An older bookmark carries a decimal id (/app/invoice/42): open the same record under its text id.
  const legacy = isNew ? null : legacyRecordId(idText);
  if (legacy && !panel) {
    const route = router.route.peek();
    queueMicrotask(() => void router.go(route?.name === 'view' ? 'view' : 'record', { model: name, id: legacy }, route?.query ?? {}, { replace: true }));
    return html`<div></div>` as HTMLElement;
  }
  const id = isNew ? null : recordId(legacy ?? idText);
  if (!isNew && !id) return missing(ctx, model, panel);
  const taken = panel ? null : takeHandoff(name, idText);
  const handed = taken?.store ?? null;
  // A panel's store lives outside the panel's scope so Expand can hand it to the record page.
  const make = () => new RecordStore({ api, schema: ctx.schema, model: name, user: ctx.user });
  const store = handed ?? (panel ? runWithOwner(null, make) : make());
  onCleanup(() => { if (!handedOff.has(store) || !panel) store.dispose(); });
  const status = cell<'loading' | 'ready' | 'missing' | 'error'>('loading', { name: `${name} page` });
  const failure = cell<string>('');
  const detail = isNew ? cell<RecordData | null>(null) : api.detail(name, id!);
  const extras = derive<DetailExtras>(() => extrasOf(detail.get()));
  const route = router.route.peek()?.query ?? {};
  // In a side panel the address is the list's: its own keys are not values for a new record.
  const query = panel ? Object.fromEntries(Object.entries(route).filter(([key]) => !LIST_KEYS.includes(key))) : route;
  // Values a slip handed over for review (outcome becomes, review): shown as a new record, not saved.
  const prefill = isNew ? takePrefill(name) : null;
  // A copy of a saved record (More > Duplicate), held in memory for this new record.
  const duplicate = isNew ? taken?.duplicate ?? (handed ? null : takeDuplicate(name)) : null;

  const load = async () => {
    if (handed) { status.set('ready'); return; }
    status.set('loading');
    try {
      if (isNew) {
        await store.create({ ...initialValues(model, query), ...(prefill?.values ?? {}), ...(duplicate?.values ?? {}) });
        // Copied values count as the person's own: the form is dirty from the start, and leaving keeps or asks.
        if (duplicate) for (const field of Object.keys(duplicate.values)) if (store.root.fields.has(field)) store.root.field(field).touched = true;
      } else await store.open(id!);
      status.set('ready');
      // A link such as /app/customer/4?slip=start_project presents that slip from the record.
      if (!isNew && query.slip && (model.slips ?? []).some(slip => slip.name === query.slip)) void presentSlip(query.slip, { source: { model: name, id: id! } });
    } catch (error) {
      if (error instanceof ApiError && (error.kind === 'not_found' || error.kind === 'forbidden')) status.set('missing');
      else if (error instanceof ApiError && error.kind === 'aborted') return;
      else if (store.loadError.peek() && untrack(() => { try { return Boolean(store.root); } catch { return false; } })) status.set('ready');
      else { failure.set(error instanceof ApiError ? error.message : 'This record could not be opened.'); status.set('error'); }
    }
  };
  void load();

  const page = html`<div class="ev-record">${match(status, {
    loading: () => html`<div class="page"><p class="muted" role="status">Loading…</p></div>`,
    missing: () => missing(ctx, model, panel),
    error: () => html`<div class="page">${EmptyState({ icon: 'info', tint: 'rose', title: 'This record could not be opened', text: () => failure.get(), action: Button({ label: 'Try again', onClick: () => void load() }) })}</div>`,
    ready: () => RecordView(ctx, model, store, { isNew, extras, detail, query, prefill: prefill?.from ?? null, duplicate, panel, focusedAtOpen }),
  })}</div>` as HTMLElement;
  return page;
}

function missing(ctx: AppContext, model: ScreenModel, panel?: PanelHost): HTMLElement {
  if (!panel) ctx.crumbs.set([{ label: model.plural, href: ctx.router.href('list', { model: model.name }) }, { label: 'Not found' }]);
  return html`<div class="page">${panel ? null : ctx.crumbBar?.()}${EmptyState({ icon: identity(model).icon, tint: identity(model).tint, title: `${model.label} not found`,
    text: 'It may have been deleted, or your role cannot open it.', action: panel ? Button({ label: 'Close', onClick: () => panel.close() })
      : Button({ label: `Open ${model.plural.toLowerCase()}`, onClick: () => void ctx.router.go('list', { model: model.name }) }) })}</div>` as HTMLElement;
}

interface ViewOptions { isNew: boolean; extras: Readable<DetailExtras>; detail: Readable<RecordData | null>; query: Record<string, string>; /** The slip that filled this new record. */ prefill?: string | null;
  /** The saved record this new one copies (Duplicate). */ duplicate?: DuplicateCopy | null;
  /** Shown in a list's side panel: the narrow layout. */ panel?: PanelHost; /** A new record: what held the focus when it was asked for. */ focusedAtOpen?: Element | null }

function RecordView(ctx: AppContext, model: ScreenModel, store: RecordStore, options: ViewOptions): HTMLElement {
  const { api, router, prefs } = ctx;
  const { isNew, extras, panel } = options;
  const name = model.name, look = identity(model);
  const canUpdate = model.api !== 'read' && model.permissions?.update !== false;
  // The role may create records of this model: what the list's New button reads.
  const canCreate = model.api !== 'read' && model.permissions?.create !== false;
  const canWrite = isNew ? canCreate : canUpdate;
  const state = model.state && fieldOf(model, model.state.field) ? model.state : null;
  const locked = derive(() => Boolean(extras.get()._locked));
  const views = new Map<string, FieldLike>();
  const view = (field: string): FieldLike => {
    let found = views.get(field);
    if (!found) views.set(field, found = gate(store.field(field), locked, canWrite));
    return found;
  };
  const has = (field: string) => store.root.fields.has(field);
  const sequence = sequenceField(model), numbered = numberField(model);
  const stateValue = state ? () => String(store.field(state.field).value.get() ?? '') : () => '';
  // One record view for the page and a list's side panel, responsive to its width (DocFrame), with Meta.panel hiding
  // parts of it in the panel.

  // ---------- title and crumbs ----------

  const titleText = derive(() => {
    if (isNew) return `New ${model.label.toLowerCase()}`;
    const field = has(model.title_field) ? store.field(model.title_field) : null;
    const data: Record<string, unknown> = { id: store.id, [model.title_field]: field?.value.get() };
    return titleOf(model, data);
  });
  // The breadcrumb ends with the record: its document number when the model has one, else its title.
  const crumbText = () => {
    const number = numbered && has(numbered.name) ? store.field(numbered.name).value.get()
      : model.naming?.field === 'name' && has('name') ? store.field('name').value.get() : null;
    return isNew || !number ? titleText.get() : String(number);
  };
  if (!panel) effect(() => ctx.crumbs.set([{ label: model.plural, href: router.href('list', { model: name }) }, { label: crumbText() }]));
  if (!isNew) {
    const remember = () => {
      const entry = { model: name, id: store.id!, title: untrack(() => titleText.get()), at: Date.now() };
      prefs.recentRecords.set([entry, ...prefs.recentRecords.peek().filter(item => !(item.model === name && item.id === entry.id))].slice(0, 8));
    };
    remember();
  }

  // ---------- unsaved changes ----------

  let leaving = false;
  const changedNew = () => [...store.root.fields.values()].some(field => field.touched) || [...store.root.tables.values()].some(table => table.rows.peek().length > 0);
  // In a side panel, leaving the record (another row, Close, Back) asks; the list's own changes do not.
  const unsaved = () => !leaving && (isNew ? changedNew() : store.dirty.peek());
  // With autosave, leaving keeps the edits apart from the record instead (app/unsaved.ts); if they can't be kept, it asks.
  const leave = async () => {
    if (!keepsUnsaved(ctx, model)) return confirmDiscard();
    const failed = await keepChanges(ctx, model, store, isNew);
    if (failed !== null) return confirmDiscard(failed);
    toast(KEPT);
    return true;
  };
  router.confirmLeave(unsaved, () => leave(), panel ? { stays: to => panel.stays(to) } : {});
  keepWhenHidden(ctx, model, store, isNew, unsaved);
  // Discard and Cancel mean the kept copy goes too.
  const forget = () => {
    if (isNew) ctx.unsaved.remove(name);
    else if (keepsUnsaved(ctx, model) && store.id) void ctx.api.discardUnsaved(name, store.id).catch(() => undefined);
  };
  // Expand: the record page takes this store, unsaved edits included, so it does not ask.
  const expand = () => {
    handoff = { model: name, id: isNew ? 'new' : String(store.id), store, duplicate: options.duplicate };
    handedOff.add(store);
    leaving = true;
    const going = isNew ? router.go('create', { model: name }) : router.go('record', { model: name, id: String(store.id) });
    void going.then(done => { if (!done) { leaving = false; handoff = null; handedOff.delete(store); } });
  };

  const save = async () => {
    if (store.saving.peek()) return false;
    const created = isNew;
    let ok = false;
    try { ok = await store.save(); } catch (error) { toast((error as Error).message, { icon: 'info' }); return false; }
    if (!ok) {
      toast(store.conflict.peek() ? 'Not saved: this record changed since you opened it.' : 'Not saved. Check the highlighted values.', { icon: 'info' });
      showErrors();
      return false;
    }
    if (created) {
      leaving = true;
      ctx.unsaved.remove(name);
      toast(`${model.label} created`);
      if (panel) panel.show(store.id!);
      else void router.go('record', { model: name, id: String(store.id) }, {}, { replace: true });
    } else toast('Saved');
    return true;
  };

  // ---------- moves and actions ----------

  // One move per target state: the detail lists the rules out of the current state.
  const moveTo = (to: string) => { const moves = extras.peek()._moves ?? []; return moves.find(item => item.to === to && item.allowed) ?? moves.find(item => item.to === to); };
  const blockedMove = (to: string): string | null => {
    if (isNew) return 'Save the record first';
    if (store.dirty.get()) return 'Save or discard your changes first';
    extras.get();
    const move = moveTo(to);
    if (!move) return `This record cannot move from ${stepLabel(model, stateValue())} to ${stepLabel(model, to)}`;
    return move.allowed ? null : move.reason ?? `${move.label} is not available now`;
  };
  const moved = (to: string, label: string) => {
    const number = sequence && has(sequence.name) ? store.field(sequence.name).value.peek() : null;
    toast(number && sequence?.name !== undefined && (sequence as any).sequence?.assign_on === to ? `${label} done. Number ${number}.` : `Moved to ${stepLabel(model, to)}`);
  };
  const runMove = async (to: string) => {
    const move = moveTo(to);
    if (!move) return;
    const label = move.label;
    // A move that takes a remark or asks for fields (a reason required once lost, say) collects them first.
    const asked = askedFields(model, move);
    const inputs = moveInputs(model, move);
    if (inputs.length) {
      ActionDialog({ name: move.key, label } as ActionMeta, inputs, async values => {
        if (await store.move(move.key, values)) { moved(to, label); return null; }
        const errors = Object.fromEntries(asked.map(meta => [meta.name, store.field(meta.name).error.peek()]).filter(([, error]) => error));
        return Object.keys(errors).length ? errors : store.formError.peek() ?? store.conflict.peek()?.message ?? `${label} did not run`;
      });
      return;
    }
    const ok = await store.move(move.key);
    if (!ok) { toast(store.formError.peek() ?? store.conflict.peek()?.message ?? `${label} did not run`, { icon: 'info' }); return; }
    moved(to, label);
  };
  const actions = (): ActionMeta[] => {
    const declared = model.actions ?? [];
    const allowed = extras.get()._actions;
    // A detail lists the actions this role may run; without it, the schema's actions are the ones.
    const list: ActionMeta[] = allowed ? allowed.map(item => ({ ...declared.find(action => action.name === item.name), name: item.name, label: item.label })) : declared;
    // Dialog actions (Python forms) have no screen here yet, so they are not offered.
    return list.filter(action => !action.dialog);
  };
  const actionBlocked = (action: ActionMeta): string | null => {
    if (isNew) return 'Save the record first';
    if (store.dirty.get()) return 'Save or discard your changes first';
    const entry = extras.get()._actions?.find(item => item.name === action.name);
    return entry && !entry.allowed ? entry.reason ?? `${action.label} is not available now` : null;
  };
  const runAction = (action: ActionMeta) => {
    const inputs = actionInputs(action);
    if (!inputs.length) {
      void store.runAction(action.name).then(ok => { if (!ok || !presentNext()) toast(ok ? `${action.label} done` : store.formError.peek() ?? `${action.label} did not run`, { icon: ok ? 'check' : 'info' }); });
      return;
    }
    ActionDialog(action, inputs, async values => {
      const ok = await store.runAction(action.name, values);
      if (ok && !presentNext()) toast(`${action.label} done`);
      return ok ? null : store.formError.peek() ?? store.conflict.peek()?.message ?? `${action.label} did not run`;
    });
  };
  // An action may answer with a slip to present next: return present(Slip, ...) in Python.
  const presentNext = () => {
    const next = (store.outcome as { present?: { slip: string; source?: { model: string; id: string } | null; values?: Record<string, unknown> } } | null)?.present;
    if (next) void presentSlip(next.slip, { source: next.source, values: next.values });
    return Boolean(next);
  };
  const slipBlocked = (): string | null => isNew ? 'Save the record first' : store.dirty.get() ? 'Save or discard your changes first' : null;
  const openSlip = (slip: string) => void presentSlip(slip, { source: { model: name, id: store.id! } }).then(outcome => {
    // The handler may have changed this record; a document or a record it opened is another page.
    if (outcome && !outcome.document && !outcome.open) void store.reload().catch(() => {});
  });
  const remove = () => openModal({ title: `Delete ${titleText.peek()}?`, icon: 'trash-2', tint: 'rose', narrow: true,
    body: () => html`<p>This can’t be undone.</p>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Delete', variant: 'danger', icon: 'trash-2', onClick: async () => {
      try {
        await api.remove(name, store.id!, store.etag.peek());
        close(); leaving = true;
        prefs.recentRecords.set(prefs.recentRecords.peek().filter(item => !(item.model === name && item.id === store.id)));
        toast(`${model.label} deleted`, { icon: 'trash-2' });
        if (panel) panel.removed(); else void router.go('list', { model: name });
      } catch (error) { close(); toast(error instanceof ApiError ? error.message : 'Not deleted.', { icon: 'info' }); }
    } } }) });

  // Duplicate: a new, unsaved record of this model with this one's values (app/duplicate.ts), where the
  // source was: on its page or in the side panel. Nothing is saved until Create.
  const canDuplicate = () => !isNew && canCreate && duplicateAllowed(model);
  const duplicateNow = () => {
    holdDuplicate(name, duplicateOf(model, store.root, titleText.peek()));
    const going = panel ? panel.show('new') : router.go('create', { model: name });
    void Promise.resolve(going).then(done => { if (done === false) dropDuplicate(name); });
  };
  // A record that ever posted is kept (its entries name it), so the server refuses its delete.
  const canDelete = () => !isNew && model.api !== 'read' && Boolean(model.permissions?.delete) && !locked.peek() && extras.peek()._deletable !== false;
  const moreItems = (): MenuItem[] => {
    const items: MenuItem[] = [{ label: 'Copy link', icon: 'link', onSelect: () => void copyText(location.href, 'the link') }];
    if (!isNew) items.push({ label: 'Copy record ID', icon: 'copy', onSelect: () => void copyText(String(store.id), 'the record ID') },
      { label: 'Reload', icon: 'rotate-ccw', onSelect: () => void store.reload().catch(() => {}) });
    if (canDuplicate()) items.push({ label: 'Duplicate', icon: 'copy', hint: formatKeys(SCREEN_KEYS.duplicate), onSelect: duplicateNow });
    if (foldable) items.push({ label: 'Reset layout', icon: 'layout-grid', disabled: !folds.changed, onSelect: () => folds.reset() });
    if (canDelete()) items.push('separator', { label: 'Delete', icon: 'trash-2', danger: true, hint: formatKeys(SCREEN_KEYS.remove), onSelect: remove });
    return items;
  };

  // The header's buttons. The State's moves make one split button: the primary move (else the first this person may
  // take) on it, the others behind its chevron. Actions, slips and Ask Eva are outline
  // buttons before it, as many as fit (OUTLINE_BUTTONS, one in a side panel); the rest, Print and the record's
  // own items (Copy link, Duplicate, Delete...) are in More.
  type HeaderEntry = MoveEntry;
  const moveEntries = (): HeaderEntry[] => (extras.get()._moves ?? []).map(move => ({ label: move.label, icon: moveIcon(model, move.to), allowed: move.allowed,
    primary: move.primary, blocked: () => blockedMove(move.to), run: () => void runMove(move.to) }));
  const outlineEntries = (): HeaderEntry[] => [
    ...actions().map(action => ({ label: action.label, icon: actionIcon(action), allowed: true, blocked: () => actionBlocked(action), run: () => runAction(action) })),
    ...(model.slips ?? []).map(slip => ({ label: slip.label, icon: 'file-text', allowed: true, blocked: slipBlocked, run: () => openSlip(slip.name) })),
  ];
  const askEvaNow = () => openEvaPanel(ctx, ctx.eva, { name, label: model.label }, store.id!, titleText.peek());
  /** Outline buttons in the title row: two on the page, one in a side panel; the rest join More. */
  const OUTLINE_BUTTONS = panel ? 1 : 2;
  // A new record's title row carries Cancel and Create where a saved record keeps its moves; the save bar below repeats them.
  const newActions = (): Child => [Button({ label: 'Cancel', onClick: () => void discardNew() }),
    canWrite ? withSaveKeys(Button({ label: () => store.saving.get() ? 'Creating…' : `Create ${model.label.toLowerCase()}`, variant: 'primary', icon: 'check',
      disabled: () => store.saving.get() || !store.ready.get(), onClick: () => void save(), tip: withShortcut(`Create ${model.label.toLowerCase()}`, SCREEN_KEYS.save) })) : null];
  const headerActions = (): Child => {
    if (isNew) return newActions();
    const moves = moveEntries();
    // Ask Eva, when Eva is on for this person: a chat about this record, in a side panel.
    const eva: HeaderEntry | null = ctx.eva.get() ? { label: 'Ask Eva', icon: 'sparkles', allowed: true, blocked: () => null, run: askEvaNow } : null;
    const outline = [...outlineEntries(), ...(eva && !panel ? [eva] : [])];
    const shown = outline.slice(0, OUTLINE_BUTTONS), rest = outline.slice(OUTLINE_BUTTONS);
    const buttons = shown.map(entry => entry === eva ? Button({ label: 'Ask Eva', icon: 'sparkles', iconTint: 'violet', hideLabelOnPhone: true, onClick: askEvaNow })
      : MoveButton({ label: entry.label, icon: entry.icon, blocked: entry.blocked, onMove: entry.run }));
    const split = MovesButton(moves);
    // Print, when the role may print this model and the server has the print extra: the preview is the server's PDF.
    const designs = ctx.schema.schema.printing?.available ? model.prints ?? [] : [];
    const printNow = () => openPrint(ctx, model, store.id!, titleText.peek(), designs);
    const items = (): MenuItem[] => {
      const first: MenuItem[] = rest.map(moveMenuEntry);
      if (designs.length) first.push({ label: 'Print', icon: 'printer', onSelect: printNow });
      if (panel && eva) first.push(moveMenuEntry(eva));
      if (panel) first.push({ label: 'Open full page', icon: 'maximize-2', onSelect: () => expand() });
      return [...first, ...(first.length ? ['separator' as const] : []), ...moreItems()];
    };
    return [...buttons, split, MenuButton({ icon: 'ellipsis', tip: 'More actions', ariaLabel: 'More actions', items, menuLabel: 'More actions' })];
  };

  // ---------- header ----------

  // The title is edited in place, except a new record's and a document number.
  const titleProp = isNew || !has(model.title_field) || (numbered && model.title_field === numbered.name) ? () => titleText.get() : view(model.title_field);
  const markName = () => titleText.get();
  // The mark is the model's icon tile, as on the list page (DocHeader's default); a person keeps their avatar.
  const mark = look.person && !isNew ? Avatar({ name: markName, size: 'xl' }) : undefined;
  // The status badges: the State (show="badge", or named by Meta.status) offers the moves this person may take
  // now; a Meta.status field that is not the State is shown, never changed, here.
  const show = state?.show ?? 'badge';
  const statusMeta = model.status ? fieldOf(model, model.status) : undefined;
  const takeable = () => canUpdate ? (extras.get()._moves ?? []).filter(move => move.allowed) : [];
  // How the State reads (stateLook): cancelled as red text with a ban icon, locked as a lock and grey text, the
  // starting state as a dashed Draft pill, any other as the coloured pill.
  const lookNow = () => stateLook(model, stateValue());
  // show="badge" draws it in the header; show="bar" draws the stage bar on the page and the badge in a side panel
  // (which has no bar); show="none" draws neither, unless Meta.status names the State.
  const stateBadge = state && !isNew && (show === 'badge' || (panel && show === 'bar') || statusMeta?.name === state.field) ? StatusBadge({
    value: stateValue, ...stateBadgeLook(model),
    moves: () => takeable().map(move => ({ to: move.to, label: move.label })), blocked: to => untrack(() => blockedMove(to)), onMove: to => void runMove(to) }) : null;
  const statusBadge = statusMeta && statusMeta.name !== state?.field && has(statusMeta.name) && !isNew ? StatusBadge({
    value: () => store.field(statusMeta.name).value.get() as string | null, tint: value => statusTint(statusMeta, value), name: statusMeta.label }) : null;
  // Key fields: the first two with a value make the subtitle (the form shows them all).
  const factFields = headerFacts(model, has);
  const numberFact = sequence && has(sequence.name) && !isNew ? when(() => !store.field(sequence.name).value.get(),
    () => html`<span class="muted">${(sequence as any).sequence?.assign_on && (sequence as any).sequence.assign_on !== 'create' ? `Number assigned on ${humanize((sequence as any).sequence.assign_on).toLowerCase()}` : 'Number assigned on save'}</span>`) : null;
  // The standard name is the document number: in mono next to the title, or when it will be assigned.
  const naming = model.naming?.field === 'name' && model.title_field !== 'name' && has('name') ? model.naming : null;
  const nameFact = naming ? live(() => {
    const value = store.field('name').value.get();
    // A title that falls back to the name already shows it as the heading.
    if (value && String(value) === titleText.get()) return null;
    // Shown once: on the page the breadcrumb ends with it (unless another number field takes that place); in a
    // side panel the top line has it when it is the number field.
    const numberIsName = Boolean(numbered && numbered.name === 'name');
    if (value && (panel ? numberIsName && numbered!.name !== model.title_field : numberIsName || !(numbered && has(numbered.name)))) return null;
    return value ? html`<span class="ev-fact mono" title="Name">${String(value)}</span>`
      : naming.placeholder ? html`<span class="muted">${naming.placeholder}</span>` : null;
  }) : null;
  // A cancelled record's title is struck through in grey (screens.css .doc-head.is-cancelled).
  const strikeTitle = (head: HTMLElement) => effect(() => { head.classList.toggle('is-cancelled', !isNew && lookNow() === 'cancelled'); });
  // The panel's number goes on the subtitle line (the page's breadcrumb ends with it).
  const numberText = numbered && numbered.name !== model.title_field && has(numbered.name) ? () => String(store.field(numbered.name).value.get() ?? '') : null;
  // One muted line under the title: in a side panel the record's number, then its first two key fields that have a
  // value, such as "Desert Rose Retail · 08-11-2026". A value shows once per view: key fields the primary area already
  // shows are left out of the subtitle.
  const primaryFields = new Set((() => { const all = sections(model); const main = all.find(section => section.main) ?? all[0]; return main ? main.fields.map(meta => meta.name) : []; })());
  const subtitleFacts = factFields.filter(meta => !primaryFields.has(meta.name)).slice(0, 2);
  const subtitle = (panel && numberText) || subtitleFacts.length
    ? () => [panel && numberText ? numberText() : '', ...subtitleFacts.map(meta => valueText(view(meta.name), ctx.links))].filter(Boolean).join(' · ') : undefined;
  // A record image (Meta.image naming a field of type "image") left of the title, in place of the icon tile.
  // The field's value is an attachment id (docs/model-engine.md#image-fields): a saved image is served by the record's
  // attachments route, one uploaded but not saved yet by the held image preview.
  const imageMeta = !isNew && model.image ? fieldOf(model, model.image) : undefined;
  const imageUrl = (field: string) => {
    const value = store.field(field).value.get();
    const direct = imageSource(value);
    if (direct) return direct;
    const idText = value === null || value === undefined ? '' : String(value).trim();
    if (!/^[1-9]\d*$/.test(idText)) return null;
    const saved = String(store.field(field).server.get() ?? '') === idText;
    return ctx.api.url(saved ? `/api/${name}/${store.id}/attachments/${idText}` : `/api/${name}/_images/${idText}`);
  };
  const image = imageMeta && has(imageMeta.name)
    ? RecordImage(() => imageUrl(imageMeta.name), { round: Boolean(look.person), label: () => titleText.get() }) : undefined;
  // A lock after the badges while nothing can be changed (locked by its state, or the role can't write), unless the
  // State already shows one.
  const readOnly = () => !isNew && (locked.get() || !canWrite) && !['locked', 'cancelled'].includes(lookNow() ?? '');
  const lockMark = when(readOnly, () => html`<span class="doc-lock" role="img" aria-label="Read only" title="Read only">${Icon('lock', { size: 'xs' })}</span>`);
  // Filled in below: the side column's drawer button, and in a side panel Expand and Close.
  const headTools = html`<span class="ev-head-tools"></span>` as HTMLElement;
  const header = DocHeader({
    type: model.label, icon: look.icon, tint: look.tint, mark, subtitle, image, tools: [headTools],
    title: titleProp,
    mono: () => Boolean(numbered && model.title_field === numbered.name && has(numbered.name) && store.field(numbered.name).value.get())
      // A title that is the record's name (no other title source) reads as a document number too.
      || Boolean(naming && store.field('name').value.get() && String(store.field('name').value.get()) === titleText.get()),
    status: [stateBadge, statusBadge, lockMark].filter(Boolean) as Child[],
    // Under the title row, only when it has something to say: the name or number still to be assigned. Key facts are
    // the subtitle; Record info is the footer.
    meta: [nameFact, numberFact].filter(Boolean) as Child[],
    actions: [live(headerActions)],
  });
  strikeTitle(header);
  // A side panel's header ends with the drawer button, Expand and Close. While the panel covers the list (a narrow
  // window) a line above it has Back to the list instead of Expand and Close.
  const panelTools = (toggle: Node | null): Child[] => panel ? [toggle, when(() => !panel.overlay.get(), () => [
    IconButton({ icon: 'maximize-2', tip: 'Open full page', onClick: () => expand() }),
    IconButton({ icon: 'x', tip: 'Close (Esc)', onClick: () => panel.close() })])] : [];
  const panelBar = () => panel ? when(panel.overlay, () => html`<div class="ev-panel-bar">
    ${Button({ label: model.plural, icon: 'chevron-left', variant: 'ghost', size: 'small', onClick: () => panel.close() })}</div>`) : null;

  // ---------- banners ----------

  // When and by whom a cancelled record was cancelled, from its journal: the newest move into its state, with the
  // reason the move recorded (or a value the move asked for). Nothing without a journal.
  const cancelled = cell<{ at: string; by: string | null; reason: string | null } | null>(null);
  if (!isNew && model.journal) effect(() => {
    const value = stateValue();
    if (lookNow() !== 'cancelled') return cancelled.set(null);
    untrack(() => void api.journal(name, store.id!, { limit: 50 }).then(page => {
      const entry = page.items.find(item => item.kind === 'moved' && String(item.data?.to ?? '') === value);
      if (!entry) return cancelled.set(null);
      const asked = Object.entries((entry.data.fields ?? {}) as Record<string, { new?: unknown }>)
        .map(([field, change]) => ({ meta: fieldOf(model, field), value: change?.new }))
        .find(item => item.meta && ['text', 'longtext'].includes(item.meta.type) && typeof item.value === 'string' && item.value.trim());
      const remark = (entry as { remark?: unknown }).remark;
      const reason = typeof remark === 'string' && remark.trim() ? remark.trim()
        : asked ? String(asked.value).trim() : null;
      cancelled.set({ at: entry.at, by: entry.actor === null ? null : page.users[String(entry.actor)] ?? null, reason });
    }, () => cancelled.set(null)));
  });
  const cancelledLine = (value: string) => {
    const info = cancelled.get();
    const when = info ? ` on ${formatDateTime(info.at).slice(0, 10)}${info.by ? ` by ${info.by}` : ''}` : '';
    return { title: `${stepLabel(model, value)}${when}.`, reason: info?.reason ? `Reason: ${info.reason.replace(/[.\s]+$/, '')}. ` : '' };
  };
  const lifecycle = state && state.steps.some(step => step.lock) && !isNew ? () => {
    const value = stateValue(), now = extras.get();
    return LockBanner(model, value, Boolean(now._locked), () => cancelledLine(value));
  } : null;
  const problems = () => {
    const conflict = store.conflict.get(), form = store.formError.get(), load = store.loadError.get();
    return [
      conflict ? StateBanner({ tone: 'danger', title: 'Changed elsewhere.', text: 'This record changed since you opened it. Your draft is still here: copy what you need, then reload.',
        actions: [Button({ label: 'Reload', icon: 'rotate-ccw', size: 'small', onClick: () => void store.reload().catch(() => {}) })] }) : null,
      form && !conflict ? StateBanner({ tone: 'danger', title: 'Not saved.', text: form }) : null,
      load ? StateBanner({ tone: 'danger', title: 'Some rows did not load.', text: `${load.message} Editing waits until every row is here.`,
        actions: [Button({ label: 'Try again', size: 'small', onClick: () => void store.loadAll().catch(() => {}) })] }) : null,
    ];
  };

  // What the header shows of a saved record, and edits there or nowhere: the form leaves these out. The title
  // goes when the header edits it in place or nothing can edit it; the name and the State likewise (the State
  // changes by its moves in the header). Anything the header shows but can't edit stays in the form.
  const formEditable = (meta: FieldSchema) => !meta.read_only && !meta.compute && meta.type !== 'sequence';
  const titleInHeader = typeof titleProp === 'object' && titleProp !== null && 'meta' in titleProp && titleProp.meta.type !== 'sequence';
  const inHeader = new Set<string>(isNew ? [] : [
    ...(has(model.title_field) && (titleInHeader || !formEditable(fieldOf(model, model.title_field)!)) ? [model.title_field] : []),
    ...(naming && !formEditable(fieldOf(model, 'name')!) ? ['name'] : []),
    ...(state && (show === 'badge' || show === 'bar' || statusMeta?.name === state.field) ? [state.field] : []),
    // The record's number, when nothing can edit it: the breadcrumb (in a side panel, the subtitle) shows it.
    ...(numbered && numbered.name !== model.title_field && has(numbered.name) && !formEditable(numbered) ? [numbered.name] : []),
  ]);
  /** Values shown above the form, by field: the key cards, or the summary strip when there are no cards. A value
   * shows once per view, so the form leaves these out; while the panel hides the cards or strip, the form keeps them. */
  const shownAbove = () => !summaryShown ? new Set<string>()
    : new Set(cardNames ? cardStats.length ? cardNames.filter(card => fieldOf(model, card)) : [] : stats.length ? summaryFields(model, new Set(factFields.map(meta => meta.name))).map(meta => meta.name) : []);
  // ---------- what a side panel shows ----------

  // model.panel (panel=False, Meta.panel or the Controls toggles) applies only here; the full page shows everything.
  // A required field that is empty on a new record always shows, so the record can be saved from the panel.
  const shows = panel ? panelView(model) : null;
  const mustShow = new Set(isNew ? model.fields.filter(meta => meta.required && has(meta.name) && isEmpty(store.field(meta.name).value.peek())).map(meta => meta.name) : []);
  const fieldShown = (section: string, name: string) => !shows || mustShow.has(name) || (shows.section(section) && shows.field(name));
  const panelSections = () => { const above = isNew ? new Set<string>() : shownAbove(); return sections(model).map(section => ({ ...section, fields: section.fields.filter(meta => has(meta.name) && !inHeader.has(meta.name) && !above.has(meta.name) && fieldShown(section.label, meta.name)) }))
    .filter(section => section.fields.length); };
  /** Meta.panel keeps this part of the record (Activity, a "more information" row) in the panel. */
  const tabShown = (id: string) => !shows || shows.tab(id);
  const fullPageNote = shows?.hides ? html`<p class="ev-panel-more muted">Some parts of this ${model.label.toLowerCase()} show only on the full page.
    <button type="button" class="link-q" on:click=${() => expand()}>Open full page</button></p>` : null;
  // ---------- flow and summary ----------

  // The stage bar, for a State declared show="bar", above the key cards on the full page: only the states this person
  // can move to now are buttons. A side panel has none; its header's status badge does the same.
  const flow = state && !isNew && show === 'bar' && !panel ? StageBar(model, { current: stateValue, canMove: to => takeable().some(move => move.to === to),
    blocked: to => untrack(() => blockedMove(to)), onMove: to => void runMove(to) }) : null;

  const perRecord = (model.measures ?? []).filter(measure => measure.per_record);
  const recordMeasures = cell<MeasureValues | null>(null);
  // Meta.cards replace the summary strip (an empty tuple shows no cards).
  const cardNames = model.cards;
  const measuredCards = perRecord.some(measure => cardNames?.includes(measure.name));
  if (perRecord.length && !isNew && (cardNames ? measuredCards : model.summary !== false)) effect(() => { options.detail.get(); void api.recordMeasures(name, store.id!).then(values => recordMeasures.set(values), () => recordMeasures.set(null)); });
  const stats: StatProps[] = summaryFields(model, new Set(factFields.map(meta => meta.name))).filter(meta => has(meta.name)).map(meta => ({
    label: meta.label, value: () => {
      const value = view(meta.name).value.get();
      if (meta.type === 'link' && meta.target && value) return ctx.links.title(meta.target, String(value)).get() ?? '…';
      return meta.type === 'currency' ? displayOf(meta, value, currencyFor(view(meta.name), ctx.links)(), currencyPlaces(view(meta.name), ctx.links)()) : formatOf(meta, value);
    },
    empty: 'Not set',
    note: view(meta.name).calculated ? 'Calculated' : undefined,
  }));
  if (model.summary !== false) for (const measure of perRecord) if (stats.length < 4) stats.push({ label: measureLabel(measure), value: () => recordMeasures.get() ? measureView(ctx.schema, model, measure, recordMeasures.get()!.values[measure.name], recordMeasures.get()!.currencies) : '…' });
  const cardStats = (cardNames ?? []).flatMap((card): StatProps[] => {
    const measure = perRecord.find(item => item.name === card);
    if (measure) return [{ label: measureLabel(measure), value: () => recordMeasures.get() ? measureView(ctx.schema, model, measure, recordMeasures.get()!.values[measure.name], recordMeasures.get()!.currencies) : '…' }];
    const meta = fieldOf(model, card);
    if (!meta || !has(card)) return [];
    return [{ label: meta.label, empty: 'Not set', value: () => {
      const value = view(card).value.get();
      if (meta.type === 'link' && meta.target && value) return ctx.links.title(meta.target, String(value)).get() ?? '…';
      return meta.type === 'currency' ? displayOf(meta, value, currencyFor(view(card), ctx.links)(), currencyPlaces(view(card), ctx.links)()) : formatOf(meta, value);
    } }];
  });
  const summaryShown = !isNew && (!shows || shows.block('summary'));
  const summary = cardNames ? (cardStats.length && summaryShown ? CardRow(cardStats) : null)
    : stats.length && summaryShown ? SummaryStrip(stats) : null;

  // ---------- blocks ----------

  const tables = (model.children ?? []).filter(child => store.root.tables.has(child.name));
  const journal = Boolean(model.journal) && !isNew;
  const relations = !isNew ? (model.relations ?? []).filter(relation => ctx.schema.schema.models.some(item => item.name === relation.model)) : [];
  // A declared record layout (Meta.layout with Main or blocks, record-blocks.ts). Once it lists related records itself,
  // the automatic Related row is off, unless Meta.side_blocks names 'related'.
  const layout = model.record_layout ?? null;
  const ownRelated = declaresRelated(layout ?? undefined);
  const relatedShown = !ownRelated || (model.side_blocks ?? []).includes('related');
  // Nothing is asked for until a Related row shows (related-data.ts).
  const related = relatedPages(relations, (relation, page) => ctx.api.related(model.name, store.id!, relation, page), store.etag,
    error => error instanceof ApiError ? error.message : 'Not loaded.');
  // A record that has posted nothing yet still lists each target, with zero rows.
  const postings = !isNew && model.effects?.length ? derive(() => extras.get()._effects?.some(item => item.rows || item.reversals) ? extras.get()._effects! : null) : null;

  // What a block reads of this record: saved values for filters, shown values for Html, and the etag to reload on.
  const blockRecord: BlockRecord = {
    id: String(store.id), version: store.etag,
    saved: field => (options.detail.get() as Record<string, unknown> | null)?.[field] ?? null,
    current: field => has(field) ? view(field).value.get() : (options.detail.get() as Record<string, unknown> | null)?.[field] ?? null,
  };
  /**
   * The page's blocks, in order: the declared layout's (or, without one, every section), then sections it does not
   * place (Other, or one a Controls layout added). The primary area is Main, else the first section (a schema section
   * marked main wins); every Table no block places joins it.
   */
  function compactBlocks(): LayoutBlock[] {
    const all = sections(model);
    const declared = layout?.blocks ?? all.map((section): LayoutBlock => ({ kind: 'section', label: section.label }));
    const named = new Set(declared.flatMap(block => block.kind === 'section' ? [block.label] : []));
    const blocks = [...declared, ...all.filter(section => !named.has(section.label)).map((section): LayoutBlock => ({ kind: 'section', label: section.label }))];
    if (!blocks.some(block => (block.kind === 'section' || block.kind === 'table') && block.main)) {
      const marked = all.find(section => section.main)?.label;
      const at = blocks.findIndex(block => block.kind === 'section' && (marked === undefined || block.label === marked));
      if (at >= 0) blocks[at] = { ...(blocks[at] as { kind: 'section'; label: string }), main: true };
    }
    const placed = new Set(blocks.flatMap(block => block.kind === 'table' ? [block.name] : []));
    return [...blocks, ...tables.filter(child => !placed.has(child.name)).map((child): LayoutBlock => ({ kind: 'table', name: child.name, main: true }))];
  }
  // Related records and charts need a saved record; a Table shows unless the side panel hides it.
  function blockShown(block: LayoutBlock) {
    return block.kind === 'related' || block.kind === 'chart' ? !isNew
      : block.kind === 'table' ? tables.some(child => child.name === block.name) && (!shows || shows.tab(block.name)) : true;
  }
  function sectionShown(label: string) { return panelSections().some(section => section.label === label); }
  /** The blocks this record and this view show. */
  const shownBlocks = () => compactBlocks().filter(block => block.kind === 'section' ? sectionShown(block.label) : blockShown(block));

  // Sections people open and close (evlet.Section), remembered per user and model by title; the app's
  // collapsed value is where they start. In the narrow side panel every section can close.
  const folds = new Folds(prefs.formSections(name), isNew);
  const foldOf = (section: { collapsible?: boolean; collapsed?: boolean | 'empty' }, index: number) =>
    ({ collapsible: Boolean(section.collapsible || panel), collapsed: section.collapsible ? section.collapsed ?? false : false, index });
  // "More information" rows open and close, so the layout can always be reset.
  const foldable = true;
  // A refused save: open the sections with errors, then scroll to and focus the first one.
  const showErrors = () => {
    const fields = sections(model).flatMap(section => section.fields).filter(meta => has(meta.name)).map(meta => view(meta.name));
    if (!fields.some(field => field.error.peek())) return;
    revealFirstError(frame, fields);
  };
  // A #field address (/app/invoice/7#due) opens that field's section and focuses it.
  const fromHash = () => { let target = ''; try { target = decodeURIComponent(location.hash.slice(1)); } catch { /* not a field name */ } if (target && has(target)) revealField(frame, target); };
  if (!panel) { queueMicrotask(fromHash); window.addEventListener('hashchange', fromHash); onCleanup(() => window.removeEventListener('hashchange', fromHash)); }
  const lockedText = () => model.fields.some(field => (field as any).versioned === false) ? `Locked. Lines can’t change while it is ${stepLabel(model, stateValue()).toLowerCase()}.` : 'Locked. Lines can’t change now.';
  /** A Table's row editor form: the row model's first section as a grid, the rest as "more information" rows, as
   * on the record itself; fields are inputs, since a row is saved with the record. */
  const rowForm = (child: { name: string; model?: string }) => {
    let groups: ReturnType<typeof sections> = [];
    try { groups = child.model ? sections(screenModel(ctx.schema, child.model)) : []; } catch { groups = []; }
    const rowFolds = new Folds(prefs.formSections(child.model ?? child.name), false);
    return (row: Row, field: (name: string) => FieldLike): Child => {
      const shown = groups.map(group => ({ ...group, fields: group.fields.filter(meta => row.fields.has(meta.name)) })).filter(group => group.fields.length);
      const gridOf = (names: string[], flow?: 'rows' | 'columns') => flowGrid(names.map(name => field(name))
        .map(item => ({ node: fieldFor({ field: item, links: ctx.links }), wide: Boolean(item.meta.wide ?? item.meta.type === 'longtext') })), flow);
      if (shown.length < 2) return html`<div class="ev-row-form">${gridOf([...row.fields.keys()].filter(name => !row.field(name).meta.hidden))}</div>`;
      const [first, ...rest] = shown;
      return html`<div class="ev-row-form"><div class="ev-cmp-grid">${gridOf(first.fields.map(meta => meta.name), first.flow)}</div>
        <div class="ev-more">${rest.map((section, position) => {
          const fields = section.fields.map(meta => field(meta.name));
          return MoreRow({ title: section.label, description: section.description, folds: rowFolds, eager: true, fields,
            start: rowFolds.start(section.collapsible ? section.collapsed ?? false : false, position + 1, fields),
            summary: () => fieldsSummary(fields, ctx.links), body: () => gridOf(section.fields.map(meta => meta.name), section.flow) });
        })}</div></div>`;
    };
  };
  const tableControls = new Map<string, ChildTableControls>();
  // A Table the record fills itself (Meta.derives) is read only here, whatever the state.
  const derivedText = () => 'Filled in by the record when it is saved.';
  const tableLocked = (child: { read_only?: boolean }) => () => Boolean(child.read_only) || locked.get() || !canWrite;
  const tableText = (child: { read_only?: boolean }) => child.read_only ? derivedText : lockedText;
  // A derived table drops its editing controls; in a "more information" row its note sits in the row's heading instead.
  const tablePanel = (child: { name: string; label: string; model?: string; read_only?: boolean }, note = true) => ChildTable({ table: store.table(child.name), title: child.label, icon: 'rows-3', tint: look.tint,
    rowForm: rowForm(child), controls: controls => tableControls.set(child.name, controls), derived: Boolean(child.read_only),
    locked: tableLocked(child), lockedText: child.read_only && !note ? '' : tableText(child), links: ctx.links, crumb: () => titleText.get(),
    heading: { type: model.label, icon: look.icon, title: () => titleText.get(),
      number: numberText ?? (naming ? () => { const value = store.field('name').value.get(); return value && String(value) !== titleText.get() ? String(value) : ''; } : null),
      mark: () => look.person ? Avatar({ name: markName, size: 'sm' }) : RecordMark({ name: markName, size: 'sm', tint: look.tint }), footer: () => saveBarView() },
    prefs: prefs.cell(`grid.${name}.${child.name}`, gridPrefs(store.table(child.name).model.fields)) });

  // ---------- a compact record page ----------

  /** A Table on a compact page: its title and a muted row count (with rows selected: "2 selected", Duplicate and
   * Delete), Focus and Columns on the right, the grid (whose last row adds a row), and a light dashed box while it has
   * no rows.
   * withTitle: the table heads the primary area with its title. Otherwise it sits in a "more information" row, whose
   * heading takes the tools (toolsOut) so the table spans the card's width. */
  const compactTable = (child: { name: string; label: string; model?: string; read_only?: boolean }, withTitle: boolean, toolsOut?: Cell<Child>) => {
    const table = store.table(child.name);
    const count = () => table.rows.get().length;
    // A table with section rows but no lines yet still shows them.
    const empty = () => count() === 0 && table.sections.get().length === 0;
    const canAdd = () => canWrite && !locked.get() && !child.read_only;
    const noun = rowNoun(child.label);
    const add = () => {
      const controls = tableControls.get(child.name);
      if (controls) { controls.add(); return; }
      try { table.add(); } catch (error) { toast((error as Error).message, { icon: 'info' }); }
    };
    // Only the empty box offers Add: once there are rows, the table's own last row adds one.
    const addLink = () => when(canAdd, () => html`<button type="button" class="link-q ev-cmp-add" on:click=${add}>${Icon('plus', { size: 'xs' })}Add ${noun}</button>${
      tableControls.get(child.name)?.sectioned ? html`<span class="ev-cmp-or muted">or <button type="button" class="link-q ev-cmp-add" on:click=${() => tableControls.get(child.name)?.addSection()}>add a section</button></span>` : null}`);
    const grid = tablePanel(child, withTitle), controls = tableControls.get(child.name);
    // The selection bar, on the title line while rows are selected (not on a locked record).
    const selectionBar = () => controls ? when(() => controls.selected.get() > 0 && controls.editable.get(), () => html`<span class="ev-cmp-sel" role="status">${() => `${controls.selected.get()} selected`}</span>
      ${Button({ label: 'Duplicate', size: 'small', onClick: () => controls.duplicateSelected() })}
      ${Button({ label: 'Delete', variant: 'soft-danger', size: 'small', className: 'ev-cmp-del', onClick: () => controls.deleteSelected() })}`) : null;
    // Focus (the table takes the content area; Esc leaves it) and Columns, as subtle square buttons by the title.
    const focusButton = controls ? (() => {
      const button = IconButton({ icon: 'maximize-2', tip: () => controls.focused.get() ? 'Leave focus (Esc)' : 'Focus', size: 'small', active: controls.focused, onClick: () => controls.toggleFocus() });
      effect(() => { const icon = Icon(controls.focused.get() ? 'minimize-2' : 'maximize-2', { size: 'sm' }); untrack(() => button.replaceChildren(icon)); });
      return button;
    })() : null;
    const tools = controls ? html`<span class="ev-cmp-tools">${focusButton}${(() => {
      const button = IconButton({ icon: 'columns-3', tip: 'Columns', size: 'small', onClick: () => controls.columns(button) });
      button.setAttribute('aria-haspopup', 'dialog');
      return button;
    })()}</span>` : null;
    return html`<div class="ev-cmp-table" class:is-empty=${empty} data-table-block=${child.name}>
      ${withTitle ? html`<div class="ev-cmp-th"><h3>${child.label}</h3><span class="muted num ev-cmp-count">${() => String(count())}</span>${selectionBar()}<span class="grow"></span>${tools}</div>`
        : toolsOut ? (toolsOut.set([selectionBar(), tools]), null) : html`<div class="ev-cmp-th ev-cmp-th-tools">${selectionBar()}<span class="grow"></span>${tools}</div>`}
      ${when(empty, () => html`<div class="ev-cmp-empty"><span>${canAdd() ? `No ${child.label.toLowerCase()} yet. Add the first one.` : `No ${child.label.toLowerCase()} yet.`}</span>${addLink()}</div>`)}
      ${grid}</div>` as HTMLElement;
  };
  /** Where a "more information" row that is not a section starts: open, except on a phone after the first. */
  const rowStart = (open: boolean, index: number) => folds.phone && index > 0 ? false : open;
  const compactContent = () => {
    const blocks = shownBlocks();
    const groups = panelSections(), all = sections(model);
    const fieldsOf = (label: string) => {
      const section = groups.find(group => group.label === label);
      if (!section) return [];
      const metas = isNew ? section.fields.filter(meta => (!meta.read_only || meta.name === model.naming?.field) && !meta.compute && !isStateField(model, meta)) : section.fields;
      return metas.map(meta => view(meta.name));
    };
    const grid = (fields: FieldLike[], flow?: 'rows' | 'columns') => isNew
      ? flowGrid(fields.map(field => ({ node: fieldFor({ field, links: ctx.links }), wide: Boolean(field.meta.wide ?? field.meta.type === 'longtext') })), flow)
      : FieldGrid(fields, { links: ctx.links, flow });
    const mainParts = blocks.filter(block => (block.kind === 'section' || block.kind === 'table') && block.main).map(block => block.kind === 'section'
      ? html`<div class="ev-cmp-grid">${grid(fieldsOf(block.label), all.find(group => group.label === block.label)?.flow)}</div>`
      : compactTable(tables.find(child => child.name === (block as { name: string }).name)!, true));
    const rows: Node[] = [];
    let index = 0;
    for (const block of blocks.filter(item => !('main' in item && item.main))) {
      const position = ++index;
      switch (block.kind) {
        case 'section': {
          const section = all.find(group => group.label === block.label);
          const fields = fieldsOf(block.label);
          if (!fields.length) continue;
          rows.push(MoreRow({ title: block.label, description: section?.description, folds, eager: true, fields,
            start: folds.start(section?.collapsible ? section.collapsed ?? false : false, position, fields),
            summary: () => fieldsSummary(fields, ctx.links), body: () => grid(fields, section?.flow) }));
          break;
        }
        case 'table': {
          const child = tables.find(item => item.name === block.name)!;
          const table = store.table(child.name);
          const tools = cell<Child>(null);
          rows.push(MoreRow({ title: child.label, folds, start: rowStart(untrack(() => table.rows.get().length) > 0, position), wide: true,
            description: child.read_only ? derivedText() : undefined, tools: () => tools.get(),
            summary: () => rowsText(table.rows.get().length), none: 'No rows', body: () => compactTable(child, false, tools) }));
          break;
        }
        case 'related': {
          // The count for the closed row: one row asked for, with the block's filters, once the row shows.
          const total = cell<number | null>(null);
          const target = ctx.schema.schema.models.find(item => item.name === block.model);
          const count = () => {
            const filters = blockFilters(block, blockRecord);
            if (!filters || !target) { total.set(0); return; }
            void api.list(block.model, { limit: 1, offset: 0, filters }).then(page => total.set((page as { total?: number }).total ?? page.items.length), () => total.set(null));
          };
          const row = MoreRow({ title: block.label, folds, start: rowStart(true, position), none: 'Loading…',
            summary: () => total.get() === null ? '' : `${target ? (target as { plural?: string }).plural ?? block.label : block.label}: ${total.get()}`,
            body: () => RelatedRecords(ctx, block, blockRecord) as Child });
          whenVisible(row, count);
          rows.push(row);
          break;
        }
        case 'html': {
          const text = block.html.replace(/<[^>]*>/g, ' ').replace(/\{[a-z_][a-z0-9_]*\}/g, '…').replace(/\s+/g, ' ').trim();
          rows.push(MoreRow({ title: 'Notes', folds, start: rowStart(true, position), summary: () => text, body: () => HtmlContent(ctx, model, block, blockRecord) }));
          break;
        }
        case 'chart':
          rows.push(MoreRow({ title: block.label ?? 'Chart', folds, start: rowStart(true, position), summary: () => '', none: 'A chart', body: () => MeasureChart(ctx, block, blockRecord) }));
          break;
      }
    }
    // Related records the layout does not list, and postings.
    if (relations.length && relatedShown && tabShown('related')) {
      const row = MoreRow({ title: 'Related', folds, start: rowStart(false, ++index), none: 'Loading…',
        summary: () => relations.map(relation => { const count = related(relation.name).total.get(); return count === null ? '' : `${relation.label}: ${count}`; }).filter(Boolean).join(' · '),
        body: () => Related(ctx, model, store.id!, relations, related) });
      whenVisible(row, () => relations.forEach(relation => related(relation.name).ensure()));
      rows.push(row);
    }
    if (postings && tabShown('postings')) rows.push(MoreRow({ title: 'Postings', folds, start: false, none: 'Nothing posted yet',
      summary: () => (postings.get() ?? []).map(item => `${targetPlural(ctx, item.target)}: ${item.rows + item.reversals}`).join(' · '), body: () => Postings(ctx, postings) }));
    // The stage bar and the key cards above one white card: the state banner strip, the primary area, then more
    // information.
    return [flow, summary, html`<div class="ev-cmp-card">${lifecycle ? live(lifecycle) : null}
      <section class="ev-cmp-main" aria-label=${model.label}>${mainParts}</section>
      ${rows.length ? html`<div class="ev-more" role="group" aria-labelledby=${`ev-more-${name}`}><h2 class="ev-more-label" id=${`ev-more-${name}`}>More information</h2>${rows}</div>` : null}</div>`];
  };

  // ---------- save bar ----------

  const saveBarView = () => isNew
    ? html`<div class="ev-savebar" role="region" aria-label="Create">${Button({ label: 'Cancel', variant: 'ghost', onClick: () => void discardNew() })}
        <span class="grow muted">${Icon('info', { size: 'sm' })} Values are checked again when you create it.</span>
        ${withSaveKeys(Button({ label: () => store.saving.get() ? 'Creating…' : `Create ${model.label.toLowerCase()}`, variant: 'primary', icon: 'check', disabled: () => store.saving.get() || !store.ready.get(), onClick: () => void save(),
          tip: withShortcut(`Create ${model.label.toLowerCase()}`, SCREEN_KEYS.save) }))}</div>`
    : when(() => store.dirty.get(), () => html`<div class="ev-savebar" role="region" aria-label="Unsaved changes">
        <span class="grow">${Icon('pencil', { size: 'sm' })} Unsaved changes</span>
        ${Button({ label: 'Discard', variant: 'ghost', onClick: () => { forget(); void store.revert(); } })}
        ${withSaveKeys(Button({ label: () => store.saving.get() ? 'Saving…' : 'Save', variant: 'primary', icon: 'check', disabled: () => store.saving.get(), onClick: () => void save(), tip: withShortcut('Save', SCREEN_KEYS.save) }))}</div>`);
  const saveBar = saveBarView();

  // ---------- side column ----------

  // The same side column on the page and in a list's side panel, less what Meta.panel hides there. A narrow frame
  // (DocFrame: a side panel, a narrow window) keeps it out of the way, behind the drawer button.
  // Activity never opens first: in a narrow frame the side column is a drawer the record's content comes before.
  const files = (!shows || shows.block('files')) && (model.side_blocks ?? ['files']).includes('files');
  const side = isNew ? undefined : CompactSide(ctx, model, store, journal && tabShown('activity'), files);

  // A new record focuses its first editable field in an open section, the primary area first: when it opens, and
  // again once its draft is restored or set aside (the restore bar held the focus).
  const shownSections = () => { const shown = shownBlocks(); return [...shown.filter(block => 'main' in block && block.main), ...shown.filter(block => !('main' in block && block.main))].flatMap(block => block.kind === 'section' ? [block.label] : []); };
  const focusFirst = (before?: Element | null) => onCleanup(autofocusForm(frame, () => initialFocusField(model, layoutFocusOrder(panelSections(), shownSections()).map(section => {
    const index = sections(model).findIndex(group => group.label === section.label);
    const fields = section.fields.filter(meta => !isStateField(model, meta)).map(meta => view(meta.name)), fold = foldOf(section, index);
    return { fields, open: !fold.collapsible || untrack(() => folds.open(section.label, folds.start(fold.collapsed, index, fields)).get()) };
  })), before));
  // A narrow frame folds the side column away (DocFrame's doc-narrow, screens.css): a button at the end of the
  // breadcrumb row (in a side panel, of its top line) opens it as a drawer on the right; Esc, a click outside or the
  // button closes it.
  const sideOpen = cell(false, { name: `${name} side drawer` });
  const sideId = uid('ev-side');
  const sideWhat = [model.attachments && files ? 'attachments' : null, journal && tabShown('activity') ? 'activity' : null].filter(Boolean).join(' and ') || 'side panel';
  const sideToggle = side ? html`<span class="ev-side-toggle">${IconButton({ icon: 'panel-right', tip: () => `${sideOpen.get() ? 'Hide' : 'Show'} ${sideWhat}`,
    active: sideOpen, onClick: () => sideOpen.set(!sideOpen.peek()) })}</span>` as HTMLElement : null;
  sideToggle?.firstElementChild?.setAttribute('aria-controls', sideId);
  const crumbRow = panel ? null : html`<div class="ev-crumb-row">${ctx.crumbBar?.() ?? null}${sideToggle}</div>`;
  if (panel) headTools.append(html`${panelTools(sideToggle)}`);
  // The footer under the form: who made and changed it, the record ID and its link. A side panel shows it when its
  // Record info block is on.
  const footer = isNew || (shows && !shows.block('info')) ? null : RecordFooter(ctx, model, String(store.id));
  const frame = DocFrame({ tint: look.tint, density: prefs.density, sidePosition: 'right', variant: panel ? 'panel' : 'page',
    main: [panel ? html`<div class="ev-panel-head">${panelBar()}${header}</div>` : [crumbRow, header], UnsavedBar(ctx, model, store, { isNew, draft: !options.duplicate, onSettled: () => focusFirst() }),
    options.prefill ? StateBanner({ tone: 'info', title: `Filled in from ${options.prefill}.`, text: `Nothing is saved until you select Create.` }) : null,
    options.duplicate ? StateBanner({ tone: 'info', icon: 'copy', title: `Copied from ${options.duplicate.from}.`, text: ['Nothing is saved until you select Create.',
      options.duplicate.unique.length ? html` <span class="ev-dup-unique">Unique fields were left empty: ${options.duplicate.unique.join(', ')}.</span>` : null] }) : null, live(problems),
    compactContent(), fullPageNote, footer, saveBar], side: side ?? undefined });
  frame.classList.add('doc-compact');
  if (side) {
    const aside = frame.querySelector<HTMLElement>(':scope > .doc-side');
    if (aside) { aside.id = sideId; aside.tabIndex = -1; }
    const scrim = html`<div class="scrim ev-side-drawer-scrim" class:show=${sideOpen} on:click=${() => sideOpen.set(false)}></div>` as HTMLElement;
    frame.append(scrim);
    effect(() => { frame.classList.toggle('side-open', sideOpen.get()); if (sideOpen.get()) untrack(() => aside?.focus({ preventScroll: true })); });
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !sideOpen.peek() || document.querySelector('.modal-wrap.show,.drawer.show,.pop.show:not([hidden]):not(.rt-mentions)')) return;
      event.preventDefault(); sideOpen.set(false); (sideToggle?.firstElementChild as HTMLElement | null)?.focus();
    };
    // On the frame first, so in a side panel Escape closes the drawer before the list closes the panel.
    frame.addEventListener('keydown', escape);
    document.addEventListener('keydown', escape);
    onCleanup(() => document.removeEventListener('keydown', escape));
  }
  // The person's "Enter moves to the next field" setting (Settings, Appearance).
  enterMovesToNextField(frame, () => Boolean(ctx.me.get()?.prefs.enter_next));
  if (isNew) focusFirst(options.focusedAtOpen);
  frame.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && (isNew || store.dirty.peek())) { event.preventDefault(); void save(); }
  });
  // Keyboard shortcuts (docs/ui.md#keyboard-shortcuts). In a side panel they act while the focus is inside it;
  // New then comes from the list, which opens the new record in the panel.
  const noun = model.label.toLowerCase();
  // A new record's Cancel (and Mod+Shift+D): it asks first when something was typed, then leaves and forgets the
  // kept draft, without keeping it again on the way out.
  const discardNew = async () => {
    if (changedNew() && !(await confirmDiscard())) return;
    leaving = true;
    forget();
    if (panel) panel.close(); else void router.go('list', { model: name });
  };
  useShortcuts({ root: frame, inner: Boolean(panel), area: panel ? () => frame.parentElement?.closest<HTMLElement>('[role="region"]') ?? frame : undefined, actions: () => ({
    // Save does what the Save (or Create) button does, with the same checks; with nothing changed it does nothing.
    save: canWrite ? { label: isNew ? `Create the ${noun}` : 'Save changes', run: () => { commitTyped(); if (isNew || store.dirty.peek()) void save(); } } : null,
    remove: isNew ? { label: `Discard the new ${noun}`, run: () => void discardNew() } : canDelete() ? { label: `Delete this ${noun}`, run: remove } : null,
    create: !panel && canCreate && !isNew ? { label: `New ${noun}`, run: () => void router.go('create', { model: name }) } : null,
    duplicate: canDuplicate() ? { label: `Duplicate this ${noun}`, run: duplicateNow } : null,
  }) });
  return frame;
}

/** The Save button announces its shortcut to assistive technology too. */
function withSaveKeys(button: HTMLButtonElement): HTMLButtonElement {
  button.setAttribute('aria-keyshortcuts', ariaKeys(SCREEN_KEYS.save));
  return button;
}

// ---------- state display ----------
// The pieces the record page draws its State with, exported so Docs (app/docs.ts) shows them live on sample schemas.

/** How the State badge reads for a model: the step label, the step colour, and its look (stateLook). */
export function stateBadgeLook(model: ScreenModel) {
  const field = model.state ? fieldOf(model, model.state.field) ?? {} : {};
  return { label: (value: string) => stepLabel(model, value), tint: (value: string) => statusTint(field, value), name: 'Status',
    look: (value: string): BadgeLook => { const kind = stateLook(model, value); return kind === null || kind === 'open' ? 'pill' : kind; } };
}

/** The stage bar for a State declared show="bar": every step in order, the current one marked, final steps green or rose. */
export function StageBar(model: ScreenModel, props: { current: () => string; canMove?: (to: string) => boolean; blocked?: (to: string) => string | null; onMove?: (to: string) => void }): HTMLElement {
  const { steps, good, bad } = flowFromState(model.state as any);
  return StatusFlow({ steps, good, bad, label: `${model.label} status`, ...props });
}

/** The strip above a record with locking steps: locked (cancelled in red, with when, by whom and why), or what can change until it locks. */
export function LockBanner(model: ScreenModel, value: string, locked: boolean, cancelled: () => { title: string; reason: string }): HTMLElement {
  if (locked) {
    const editable = model.fields.filter(field => (field as any).versioned === false).map(field => field.label.toLowerCase());
    const what = editable.length ? `Only ${editable.join(' and ')} can change.` : 'Nothing can change.';
    // Cancelled: a soft red strip with when and by whom, and the remark when one was given.
    if (stateLook(model, value) === 'cancelled') {
      const line = cancelled();
      return StateBanner({ tone: 'danger', icon: 'ban', title: line.title, text: [line.reason, what] });
    }
    return StateBanner({ tone: 'info', title: `${stepLabel(model, value)} and locked.`, text: what });
  }
  const next = model.state!.rules.find(rule => rule.from.includes(value) && stepOf(model, rule.to)?.lock);
  return StateBanner({ tone: 'neutral', icon: 'circle-alert', title: `${stepLabel(model, value)}.`, text: next ? `Everything can change until it is ${stepLabel(model, next.to).toLowerCase()}.` : 'Everything can change.' });
}

/** A header move: what the split button and its menu need. */
export interface MoveEntry { label: string; icon: string; allowed: boolean; primary?: boolean; blocked: () => string | null; run: () => void }

/** A move (or action) in a menu: it runs, or says why it cannot. */
export const moveMenuEntry = (entry: MoveEntry): MenuItem => ({ label: entry.label, icon: entry.icon,
  onSelect: () => { const why = untrack(entry.blocked); if (why) toast(why, { icon: 'info' }); else entry.run(); } });

/** The header's move button: the primary move (else the first allowed, else the first) on a split button, the others
 * behind its chevron. Null without moves. */
export function MovesButton(moves: MoveEntry[]): HTMLElement | null {
  const main = moves.find(move => move.primary) ?? moves.find(move => move.allowed) ?? moves[0];
  return main ? SplitButton({ main: MoveButton({ label: main.label, icon: main.icon, blocked: main.blocked, variant: 'primary', onMove: main.run }),
    items: moves.filter(move => move !== main).map(moveMenuEntry), menuLabel: 'Other moves' }) : null;
}

/** The model fields a move asks for (its `asks`). */
const askedFields = (model: ScreenModel, move: DetailMove) => (move.asks ?? []).map(field => model.fields.find(meta => meta.name === field)).filter(meta => meta !== undefined);

/** What a move's dialog collects: its remark first, then the fields it asks for, writable here. Empty: it runs at once. */
export function moveInputs(model: ScreenModel, move: DetailMove): FieldSchema[] {
  const remark = move.remark ? [{ name: 'remark', type: 'longtext', label: move.remark.label ?? 'Remark', required: move.remark.required, max_length: 1000 } as FieldSchema] : [];
  return [...remark, ...askedFields(model, move).map(meta => ({ ...meta, read_only: false, required: Boolean(meta.required || meta.required_when), applies_when: null, required_when: null, locked_when: null }))];
}

// ---------- action dialog ----------

/** A move's icon, read from where it goes: a final rose step crosses, Resume plays, On hold pauses, Complete checks. */
export function moveIcon(model: ScreenModel, to: string): string {
  const step = stepOf(model, to);
  if (step?.final && step.color === 'rose') return 'circle-x';
  if (/hold|pause|suspend|block/i.test(to)) return 'pause';
  if (/won|paid|done|complete|approve|accept|close|finish|deliver/i.test(to)) return 'circle-check';
  if (/issue|send|submit|sent/i.test(to)) return 'send';
  if (/active|progress|resume|start|open|running/i.test(to)) return 'play';
  return 'arrow-right';
}

/** An action's icon: the one it declares, else one read from its name ("Log hours" a clock), else a bolt. */
function actionIcon(action: ActionMeta): string {
  const declared = (action as ActionMeta & { icon?: string }).icon;
  if (declared) return declared;
  const words = `${action.name} ${action.label}`;
  if (/hour|time|log/i.test(words)) return 'clock';
  if (/mail|remind/i.test(words)) return 'mail';
  if (/send|share/i.test(words)) return 'send';
  if (/pay|receipt/i.test(words)) return 'wallet';
  if (/note|comment/i.test(words)) return 'message-square';
  if (/assign|owner/i.test(words)) return 'user';
  return 'zap';
}

const ARGUMENT_TYPES: Record<string, string> = { str: 'text', int: 'integer', bool: 'boolean', float: 'decimal' };

function actionInputs(action: ActionMeta): FieldSchema[] {
  if (action.inputs?.length) return action.inputs;
  return (action.arguments ?? []).map(argument => ({ name: argument.name, type: ARGUMENT_TYPES[argument.type] ?? 'text', label: humanize(argument.name),
    required: argument.required, read_only: false, default: argument.default }) as FieldSchema);
}

/** Small dialog that collects an action's (or a move's) inputs, checks them in place and runs it. `run`
 * answers null when done, a message, or field errors by input name. */
export function ActionDialog(action: ActionMeta, inputs: FieldSchema[], run: (values: Record<string, unknown>) => Promise<string | Record<string, string> | null>) {
  const fields = inputs.map(meta => localField(meta as any, meta.default ?? (meta.type === 'boolean' ? false : null)));
  const controls = fields.map(field => fieldFor({ field, full: true }));
  const note = cell<string>('');
  let busy = false;
  const submit = async (close: () => void) => {
    // A value typed but not yet committed (the field was not left) counts: commit every text input first.
    for (const input of controls.flatMap(element => [...element.querySelectorAll('input, textarea')])) input.dispatchEvent(new Event('change'));
    if (fields.some(field => field.error.peek())) return;
    let valid = true;
    for (const field of fields) {
      const value = field.value.peek();
      if (field.meta.required && (value === null || value === undefined || value === '')) { field.error.set(`${field.meta.label} is required`); valid = false; }
    }
    if (!valid || busy) return;
    busy = true;
    const values = Object.fromEntries(fields.filter(field => field.value.peek() !== null && field.value.peek() !== '').map(field => [field.meta.name, field.value.peek()]));
    const problem = await run(values);
    busy = false;
    if (typeof problem === 'string') { note.set(problem); return; }
    if (problem) { for (const field of fields) if (problem[field.meta.name]) field.error.set(problem[field.meta.name]); return; }
    close();
  };
  let closeDialog = () => {};
  closeDialog = openModal({ title: action.label, icon: 'zap', tint: 'blue', narrow: true, onSubmit: () => void submit(closeDialog),
    body: () => [...controls, html`<p class="ev-error" role="alert" hidden=${() => !note.get()}>${() => note.get()}</p>`],
    footer: close => DialogFooter({ cancel: close, submit: { label: action.label, onClick: () => void submit(close) } }) });
  return closeDialog;
}

// ---------- side column ----------

/** A compact page's side column: Attachments and Activity (with the slim note editor). */
function CompactSide(ctx: AppContext, model: ScreenModel, store: RecordStore, journal: boolean, files: boolean): Child[] | null {
  if (!(model.attachments && files) && !journal) return null;
  return [
    model.attachments && files ? SideBlock({ title: 'Attachments', body: Files(ctx, model, store) }) : null,
    journal ? SideBlock({ title: 'Activity', body: html`<div class="ev-cmp-activity">${Activity(ctx, model, store, 'compact')}</div>` }) : null,
  ];
}

/** The record page's footer, under the form: Created and Modified (who and when), the record ID with Copy, and
 * Copy link (the record's own address). Nothing else goes here. */
export function RecordFooter(ctx: AppContext, model: ScreenModel, id: string): HTMLElement {
  const audit = () => extrasOf(ctx.api.detail(model.name, id).get())._audit;
  const exact = ctx.prefs.exactTimes;
  const toggle = () => exact.set(!exact.peek());
  const link = () => new URL(ctx.router.href('record', { model: model.name, id }), location.origin).href;
  const item = (label: string, at: 'created_at' | 'modified_at', by: 'created_by' | 'modified_by') => when(() => Boolean(audit()?.[at]),
    () => html`<span class="ev-rf-item" data-part=${at === 'created_at' ? 'created' : 'modified'}><span class="ev-rf-l">${label}</span>${() => {
      const person = audit()?.[by] ?? null;
      // Who links to Controls Users for people who may open Controls.
      const who = person === null ? null : ctx.me.get()?.controls
        ? html`<a class="ev-rf-who link-q" href="/controls/users" target="_blank" rel="noopener">${person.name}</a>` : html`<span class="ev-rf-who">${person.name}</span>`;
      return who ? [who, html`<span class="ev-rf-dot" aria-hidden="true">·</span>`] : null;
    }}${RelativeTime({ at: () => audit()?.[at], exact, onToggle: toggle })}</span>`);
  return html`<footer class="ev-rec-foot" aria-label="Record details">
    ${item('Created', 'created_at', 'created_by')}${item('Modified', 'modified_at', 'modified_by')}<span class="grow"></span>
    <span class="ev-rf-item ev-rf-id"><span class="ev-rf-l">ID</span><span class="mono">${id}</span>
      ${IconButton({ icon: 'copy', tip: 'Copy record ID', size: 'small', onClick: () => void copyText(id, 'the record ID') })}
      ${IconButton({ icon: 'link', tip: 'Copy link', size: 'small', onClick: () => void copyText(link(), 'the link') })}</span></footer>` as HTMLElement;
}

/** The print preview: the PDF the server renders for this person, with Download and File a copy. */
export function openPrint(ctx: AppContext, model: ScreenModel, id: string, title: string, designs: Array<{ name: string; title: string }>) {
  const design = cell(designs[0].name);
  const canFile = Boolean(model.attachments) && model.api !== 'read' && model.permissions?.update !== false;
  const fileCopy = async () => {
    try {
      const file = await ctx.api.fileCopy(model.name, id, design.peek());
      window.dispatchEvent(new CustomEvent('evlet-files-changed', { detail: { model: model.name, id } }));
      toast(`${file.name} filed with this ${model.label.toLowerCase()}`, { icon: 'paperclip' });
    } catch (error) { toast(error instanceof ApiError ? `Not filed: ${error.fields.attachment ?? error.message}` : 'Not filed.', { icon: 'info' }); }
  };
  openDrawer({ title: `Print ${title}`, icon: 'printer', className: 'ev-print',
    body: () => html`<div class="ev-print-body">
      ${designs.length > 1 ? html`<label class="ev-print-design"><span class="muted">Print design</span>
        <select class="input" aria-label="Print design" on:change=${(event: Event) => design.set((event.target as HTMLSelectElement).value)}>
          ${designs.map(item => html`<option value=${item.name}>${item.title}</option>`)}</select></label>` : null}
      <iframe class="ev-print-frame" title=${() => `${title}, PDF preview`} src=${() => ctx.api.printUrl(model.name, id, design.get())}></iframe>
      <p class="muted ev-print-note">Printed as you: fields you cannot read are left out.</p></div>`,
    footer: close => html`<div class="ev-print-actions">
      <a class="btn primary" rel="external" href=${() => ctx.api.printUrl(model.name, id, design.get(), true)}>${Icon('download', { size: 'sm' })}<span>Download</span></a>
      ${canFile ? Button({ label: 'File a copy', icon: 'paperclip', onClick: () => void fileCopy() }) : null}
      ${Button({ label: 'Close', onClick: close })}</div>` });
}

function Files(ctx: AppContext, model: ScreenModel, store: RecordStore): Child {
  const files = cell<Array<{ id: number; name: string; size: number }> | null>(null);
  const problem = cell('');
  const load = () => ctx.api.attachments(model.name, store.id!).then(items => { files.set(items); }, error => { problem.set(error instanceof ApiError ? error.message : 'Files could not be loaded.'); });
  load();
  // A filed print copy is a new attachment of this record.
  const changed = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail?.model === model.name && detail?.id === store.id) void load(); };
  window.addEventListener('evlet-files-changed', changed);
  onCleanup(() => window.removeEventListener('evlet-files-changed', changed));
  const canWrite = model.api !== 'read' && model.permissions?.update !== false;
  // The files of a locked record cannot change: the server refuses with 409.
  const locked = () => Boolean(extrasOf(ctx.api.detail(model.name, store.id!).get())._locked);
  const limits = model.attachment_limits ?? { max_files: 10, max_size: 1_048_576 };
  const size = (bytes: number) => bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const upload = (event: Event) => {
    const input = event.target as HTMLInputElement, file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > limits.max_size) { toast(`Use a file up to ${size(limits.max_size)}`, { icon: 'info' }); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result).split(',')[1] ?? '';
      ctx.api.upload(model.name, store.id!, file.name, content).then(() => { toast(`${file.name} attached`, { icon: 'paperclip' }); void load(); },
        error => toast(error instanceof ApiError ? `Not attached: ${error.fields.attachment ?? error.message}` : 'Not attached.', { icon: 'info' }));
    };
    reader.readAsDataURL(file);
  };
  const box = html`<div class="ev-files" tabindex="-1">
    ${() => problem.get() ? html`<p class="muted">${problem.get()}</p>` : files.get() === null ? html`<p class="muted">Loading…</p>` : files.get()!.length === 0
      ? html`<p class="muted">No files yet.</p>` : html`<div class="files">${files.get()!.map((file, index) => html`<div class="file">${Icon('paperclip', { size: 'sm', className: 'muted' })}
        <div style="flex:1;min-width:0"><a class="link" rel="external" href=${ctx.api.url(`/api/${model.name}/${store.id}/attachments/${file.id}`)}>${file.name}</a><div class="muted" style="font-size:12px">${size(file.size)}</div></div>
        ${canWrite ? when(() => !locked(), () => removeButton(file, index)) : null}</div>`)}</div>`}
    ${canWrite ? when(() => !locked(), () => html`<label class="btn sm ev-upload">${Icon('upload', { size: 'sm', className: 'tint' })}Attach a file<input type="file" hidden on:change=${upload}></label>`) : null}</div>` as HTMLElement;
  // After a removal, focus moves to the remove button now in the same place, else the one before it, else the block.
  const refocus = (index: number) => {
    const buttons = [...box.querySelectorAll<HTMLButtonElement>('.ev-file-remove')];
    (buttons[Math.min(index, buttons.length - 1)] ?? box).focus();
  };
  const remove = (file: { id: number; name: string }, index: number) => openModal({ title: `Remove ${file.name}?`, icon: 'trash-2', tint: 'rose', narrow: true,
    body: () => html`<p>This can’t be undone.</p>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Remove', variant: 'danger', icon: 'trash-2', onClick: async () => {
      try {
        await ctx.api.removeAttachment(model.name, store.id!, file.id);
        close();
        toast(`${file.name} removed`, { icon: 'trash-2' });
        await load();
        refocus(index);
      } catch (error) { close(); toast(error instanceof ApiError ? `Not removed: ${error.fields.attachment ?? error.message}` : 'Not removed.', { icon: 'info' }); }
    } } }) });
  const removeButton = (file: { id: number; name: string }, index: number) => {
    const button = IconButton({ icon: 'trash-2', tip: `Remove ${file.name}`, size: 'small', onClick: () => remove(file, index) });
    button.classList.add('ev-file-remove');
    return button;
  };
  return box;
}

// ---------- related ----------

/** A relation named after its model counts in that model's words ('1 invoice', 'No invoices yet'); any other
 * name ('Revisions', 'Open tasks') stays neutral, so its count never reads as something it is not. */
function relatedNoun(label: string, target: ScreenModel) {
  return label === target.plural ? { one: target.label.toLowerCase(), many: target.plural.toLowerCase() } : undefined;
}

/** The list of `target` filtered to the records whose `link` points to `id`, in the list's own ?f= form;
 * null when the list cannot filter on that field for this role. */
function viewAllHref(ctx: AppContext, target: ScreenModel, link: string, id: string) {
  if (!filterFields(ctx.schema, target).some(field => field.key === link)) return null;
  return ctx.router.href('list', { model: target.name }, { f: JSON.stringify([[link, 'eq', id]]) });
}

function Related(ctx: AppContext, model: ScreenModel, id: string, relations: NonNullable<ScreenModel['relations']>, related: (name: string) => RelatedPage): Child {
  return relations.map(relation => {
    const target = screenModel(ctx.schema, relation.model);
    const { rows, total, offset, problem, load, ensure } = related(relation.name);
    const columns: Column<RecordData>[] = listFields(target).filter(meta => meta.name !== relation.link).slice(0, 4).map((meta, index) => ({
      key: meta.name, label: meta.label, width: index === 0 ? 220 : 150, value: (row: RecordData) => row[meta.name],
      align: ['currency', 'decimal', 'integer', 'percent'].includes(meta.type) ? 'right' : 'left', sortable: false,
      render: index === 0 ? (row: () => RecordData) => html`<a class="ev-row-link" href=${() => ctx.router.href('record', { model: target.name, id: String(row().id) })}>${() => titleOf(target, row())}</a>`
        : (row: () => RecordData) => cellView(ctx, target, meta, row),
    }));
    const none = `No ${relation.label.toLowerCase()} yet.`;
    const all = viewAllHref(ctx, target, relation.link, id);
    const pages = () => (total.get() ?? 0) > RELATED_PAGE;
    // An empty relation is one short line: no table of headers over nothing, and no 0 in the heading.
    const section = html`<section class="panel ev-related" aria-label=${relation.label}><div class="panel-h"><h3>${relation.label}</h3><span class="muted num">${() => total.get() ? String(total.get()) : ''}</span>
        ${all ? when(() => Boolean(total.get()), () => html`<a class="ev-view-all" href=${all} aria-label=${`View all ${relation.label.toLowerCase()}`}>View all</a>`) : null}</div>
      ${() => problem.get() ? html`<p class="muted" style="padding:12px">${problem.get()}</p>` : null}
      ${when(() => total.get() === 0 && !problem.get(), () => html`<p class="ev-related-empty">${none}</p>`)}
      ${when(() => total.get() !== 0, () => DataTable<RecordData>({ columns, rows, key: row => row.id, label: relation.label, onOpen: row => void ctx.router.go('record', { model: target.name, id: String(row.id) }),
        height: 300, empty: () => html`<span class="muted">${none}</span>` }))}
      ${when(pages, () => html`<div class="ev-pager"><span class="muted num">${() => rangeText(offset.get(), rows.get().length, total.get() ?? 0)}</span>
        ${IconButton({ icon: 'chevron-left', tip: 'Previous page', size: 'small', disabled: () => offset.get() === 0, onClick: () => load(Math.max(0, offset.peek() - RELATED_PAGE)) })}
        ${IconButton({ icon: 'chevron-right', tip: 'Next page', size: 'small', disabled: () => offset.get() + RELATED_PAGE >= (total.get() ?? 0), onClick: () => load(offset.peek() + RELATED_PAGE) })}</div>`)}</section>` as Element;
    whenVisible(section, ensure);
    return section;
  });
}

// ---------- postings ----------

/** A posting target's name in the plural ("Ledger entries"), or its name when this role has no schema for it. */
function targetPlural(ctx: AppContext, target: string): string {
  try { return ctx.schema.model(target).plural; } catch { return humanize(target); }
}

function Postings(ctx: AppContext, effects: Readable<DetailExtras['_effects'] | null>): Child {
  const label = (target: string) => targetPlural(ctx, target);
  return live(() => !effects.get()?.length ? EmptyState({ icon: 'layers', tint: 'teal', title: 'Nothing posted yet', text: 'Rows this record posts to other records show up here once it is posted.' }) : [
    InfoNote('Rows this record posted to other records. They are read only. Voiding writes mirror rows that cancel them.'),
    html`<div class="panel"><table class="dt"><thead><tr><th scope="col">Posted to</th><th scope="col" class="r">Rows</th><th scope="col" class="r">Reversals</th><th scope="col">Check</th></tr></thead>
      <tbody>${() => (effects.get() ?? []).map(item => html`<tr><td>${label(item.target)}</td><td class="r num">${String(item.rows)}</td><td class="r num">${String(item.reversals)}</td>
        <td>${item.balanced === undefined ? html`<span class="muted">Not checked</span>` : item.balanced ? Chip({ label: 'Balanced', tone: 'green', icon: 'check' }) : Chip({ label: 'Not balanced', tone: 'rose', icon: 'info' })}</td></tr>`)}</tbody></table></div>`]);
}

// ---------- activity (journal) ----------

// Icon, tint and verb per entry kind (docs/journal.md). `changed` also stands for entries the
// reader may only partly see (a move, void or posting about a State or target it cannot read).
const KINDS: Record<string, [string, any, string]> = {
  created: ['plus', 'green', 'created this record'], changed: ['pencil', 'blue', 'changed it'], moved: ['arrow-right', 'violet', 'moved it'],
  voided: ['circle-x', 'rose', 'voided it'], revised: ['copy', 'blue', 'revised it'], number: ['hash', 'slate', 'assigned the number'],
  effects: ['layers', 'teal', 'posted rows'], recomputed: ['rotate-ccw', 'slate', 'recalculated it after an upgrade'], file: ['paperclip', 'slate', 'attached a file'], deleted: ['trash-2', 'rose', 'deleted it'],
  note: ['sticky-note', 'amber', 'added a note'], message: ['mail', 'blue', 'logged a message'], call: ['phone', 'green', 'logged a call'],
  follow_up: ['clock', 'amber', 'planned a follow-up'], follow_up_done: ['circle-check', 'green', 'completed a follow-up'],
};

interface Change { old: unknown; new: unknown }
interface RowChanges { added?: Array<{ id: string }>; updated?: Array<{ id: string }>; removed?: Array<{ id: string }>; reordered?: boolean }

function Activity(ctx: AppContext, model: ScreenModel, store: RecordStore, toolbar: 'full' | 'compact' = 'full'): Child {
  const page = cell<JournalPage | null>(null), problem = cell('');
  const load = () => void ctx.api.journal(model.name, store.id!, { limit: 50 }).then(result => page.set(result), error => problem.set(error instanceof ApiError ? error.message : 'The history could not be loaded.'));
  load();
  const canNote = Boolean(model.journal?.kinds?.includes('note'));
  const label = (field: string) => fieldOf(model, field)?.label ?? humanize(field);
  // A Link shows the linked record's title, as on the form; a Link to a ledger row is described as
  // an integer id (no grouping) by the schema, so it never reads as an amount.
  const value = (field: string, raw: unknown): Child => {
    const meta = fieldOf(model, field);
    if (raw === '[masked]') return '[masked]';
    if (raw === null || raw === undefined || raw === '') return 'empty';
    if (meta?.type === 'link' && meta.target) { const title = ctx.links.title(meta.target, String(raw)); return () => title.get() ?? '…'; }
    return meta ? formatOf(meta, raw) : String(raw);
  };
  const table = (name: string) => model.children?.find(child => child.name === name)?.label ?? humanize(name);
  const target = (name: string) => { try { return ctx.schema.model(name).plural; } catch { return humanize(name); } };
  const action = (name: string) => model.actions?.find(item => item.name === name)?.label ?? humanize(name);
  const state = (name: unknown) => humanize(String(name ?? ''));
  const person = (id: unknown) => id === null || id === undefined ? null : page.peek()?.users[String(id)] ?? `User ${id}`;
  const day = (at: string) => {
    const date = formatDateTime(at).slice(0, 10);
    return date === formatDateTime(new Date()).slice(0, 10) ? 'Today' : date;
  };
  // Field changes as old and new values; Table rows as counts per collection.
  const changes = (data: Record<string, any>) => {
    const fields = Object.entries((data.fields ?? {}) as Record<string, Change>).map(([field, change]) =>
      html`<div class="ev-diff"><b>${label(field)}</b> <s class="muted">${value(field, change.old)}</s> ${Icon('arrow-right', { size: 'xs' })} ${value(field, change.new)}</div>`);
    const rows = Object.entries((data.rows ?? {}) as Record<string, RowChanges>).map(([name, rows]) => {
      const parts = (['added', 'updated', 'removed'] as const).filter(part => rows[part]?.length).map(part => `${rows[part]!.length} ${part}`);
      if (rows.reordered) parts.push('reordered');
      return html`<div class="ev-diff"><b>${table(name)}</b> ${parts.join(', ')}</div>`;
    });
    return [...fields, ...rows];
  };
  const sentence = (item: JournalEntry): string => {
    const data = item.data ?? {};
    switch (item.kind) {
      case 'changed': return data.action ? `ran ${action(data.action)}` : 'changed it';
      case 'moved': return `moved it from ${state(data.from)} to ${state(data.to)}`;
      case 'number': return `assigned the number ${data.number}`;
      case 'revised': return data.copy ? `revised it into #${data.copy}` : 'revised it';
      case 'file': return `${data.action === 'removed' ? 'removed' : 'attached'} ${data.name}`;
      case 'effects': return (data.targets ?? []).map((posted: any) =>
        `${posted.reversal ? 'reversed' : 'posted'} ${posted.rows} ${posted.rows === 1 ? 'row' : 'rows'} ${posted.reversal ? 'in' : 'to'} ${target(posted.target)}`).join(' and ');
      case 'call': return `logged ${data.direction === 'in' ? 'an incoming' : 'an outgoing'} call${data.party ? ` with ${data.party}` : ''}${data.minutes ? ` (${data.minutes} min)` : ''}`;
      case 'message': return data.source === 'mailbox'
        ? (data.direction === 'in' ? `received an email from ${data.party ?? 'a contact'}` : `sent an email to ${data.party ?? 'a contact'}`)
        : `logged ${data.direction === 'in' ? 'a message from' : 'a message to'} ${data.party ?? 'the customer'}`;
      default: return (KINDS[item.kind] ?? ['', '', humanize(item.kind).toLowerCase()])[2];
    }
  };
  const body = (item: JournalEntry): Child => {
    const data = item.data ?? {};
    switch (item.kind) {
      case 'created': return null;
      case 'confirmed': case 'reconfirmed': case 'cancelled': case 'reopened': case 'post_confirmation_edit':
      case 'changed': case 'moved': case 'recomputed': { const list = changes(data); return list.length ? html`<div class="note">${list}</div>` : null; }
      case 'effects': return (data.targets ?? []).some((posted: any) => posted.balanced !== undefined)
        ? html`<div class="note">${(data.targets ?? []).filter((posted: any) => posted.balanced !== undefined).map((posted: any) =>
          Chip({ label: `${target(posted.target)} ${posted.balanced ? 'balanced' : 'not balanced'}`, tone: posted.balanced ? 'green' : 'rose', icon: posted.balanced ? 'check' : 'info' }))}</div>` : null;
      case 'message': return html`<div class="note mail">${data.subject ? html`<div class="subj">${String(data.subject)}</div>` : null}${typeof data.html === 'string' ? richNote(data.html) : String(data.text ?? '')}</div>`;
      case 'note': return typeof data.html === 'string' ? html`<div class="note">${richNote(data.html)}</div>` : data.text ? html`<div class="note">${String(data.text)}</div>` : null;
      case 'follow_up': {
        const who = person(data.assignee);
        const facts = [data.due ? `Due ${formatDate(data.due)}` : null, who ? `for ${who}` : null].filter(Boolean).join(' ');
        return html`<div class="note">${String(data.text ?? '')}<div class="ev-fu muted">${facts}${facts ? ' · ' : ''}${item.done ? Chip({ label: 'Done', tone: 'green', icon: 'check' }) : Chip({ label: 'Open', tone: 'amber', icon: 'clock' })}</div></div>`;
      }
      default: return data.text || data.reason ? html`<div class="note">${String(data.text ?? data.reason)}</div>` : null;
    }
  };
  // A note written with the composer: formatted, mentions shown with each person's current name.
  const richNote = (markup: string) => RichTextView(markup, { mentions: id => page.peek()?.users[id] });
  const entry = (item: JournalEntry) => {
    const [icon, tint] = KINDS[item.kind] ?? ['activity', 'slate'];
    const hidden = item.partial ? html`<span class="muted ev-partial" title="Parts of this entry are hidden from your role">${Icon('eye-off', { size: 'xs' })} Some details are hidden</span>` : null;
    return html`<div class="tl-item"><span class=${`ic b-${tint}`}>${Icon(icon, { size: 'sm' })}</span><div class="bd">
      <div class="hd"><b>${person(item.actor) ?? 'The system'}</b> ${sentence(item)}${item.via ? ` via ${item.via}` : ''}${RelativeTime({ at: item.at, className: 'tm' })}</div>${body(item)}${hidden}</div></div>`;
  };
  return [
    InfoNote(model.journal?.tamper_evident ? 'Append-only history, chained so tampering shows. Entries are never edited or removed.' : 'Append-only history. Entries are never edited or removed.', 'lock'),
    canNote ? NoteComposer({ api: ctx.api, model: model.name, id: store.id!, toolbar, name: id => page.peek()?.users[id],
      onAdded: () => { toast('Note added', { icon: 'sticky-note' }); load(); }, onError: message => toast(message, { icon: 'info' }) }) : null,
    live(() => problem.get() ? html`<p class="muted">${problem.get()}</p>` : page.get() === null ? html`<p class="muted">Loading…</p>`
      : !page.get()!.items.length ? EmptyState({ icon: 'activity', tint: 'slate', title: 'No activity yet', text: 'Changes, moves and notes for this record land here.' })
      : html`<div class="timeline">${(() => {
        let last = '';
        return page.get()!.items.flatMap(item => { const label = day(item.at); const head = label !== last ? html`<div class="tl-day">${label}</div>` : null; last = label; return [head, entry(item)]; });
      })()}</div>`),
  ];
}

