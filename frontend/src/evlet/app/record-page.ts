// The generated record (doc) view of a model (docs/ui-design/README.md section 7): header with the
// title, the document number, moves and actions; lifecycle banners; status flow; summary strip;
// tabs for Activity, Details, each Table, Related and Postings; the side panel. New records use
// the same layout with inputs and a Create button. Everything the model and the role do not
// send is absent: fields, actions, moves, tables and blocks come from the schema and detail.

import { html, match, when, type Child } from '../dom';
import { ApiError, ValidationError, type RecordData } from '../api';
import { legacyRecordId, recordId } from '../ids';
import { batch, cell, derive, effect, onCleanup, runWithOwner, untrack, type Readable } from '../reactive';
import type { Route } from '../router';
import { RecordStore, type Field, type Rules } from '../model/record';
import type { FieldSchema } from '../model/schema';
import { Avatar, Button, Chip, EmptyState, Icon, IconButton, IconTile, InfoNote, RecordMark, StateBanner, SummaryStrip, type StatProps } from '../ui/base';
import { ChildRows, ChildTable } from '../ui/childtable';
import { DocFrame, DocHeader, flowFromState, MoveButton, SideBlock, StatusFlow, TabPanel, Tabs, type TabItem } from '../ui/doc';
import { displayValue, fieldFor, localField, stateTint, type FieldLike } from '../ui/fields';
import { FoldSection, Folds, revealField, revealFirstError } from '../ui/fold';
import { FormSection, KeyValueList } from '../ui/inline';
import { humanize } from '../ui/kinds';
import { copyText, DialogFooter, MenuButton, openDrawer, openModal, toast, type MenuItem } from '../ui/layers';
import { DataTable, emptyPrefs, type Column } from '../ui/table';
import { formatDate, formatDateTime } from '../model/format';
import type { AppContext } from './context';
import type { JournalEntry, JournalPage } from './client';
import { extrasOf, fieldOf, formatOf, identity, isStateField, keyFields, listFields, measureLabel, measureText, numberField, screenModel, sections, sequenceField,
  summaryFields, titleOf, LIST_KEYS, type ActionMeta, type DetailExtras, type ScreenModel } from './meta';
import { cellView } from './list-page';
import { Revisions } from './revisions';
import { presentSlip, takePrefill } from './slips';
import { openEvaPanel } from '../eva/chat';

const RESERVED = new Set(['tab', 'slip']);
// A reactive part inside a list of children (arrays are read once; a template hole stays live).
const live = (render: () => Child) => html`${render}`;

/** A field as a screen shows it: the State lock and the role's write access close it, with the reason. */
function gate(field: Field, locked: Readable<boolean>, canWrite: boolean): FieldLike {
  const meta: FieldSchema = canWrite ? field.meta : { ...field.meta, read_only: true };
  const stateLocked = derive(() => locked.get() && !(field.meta as any).editable_when_locked);
  const rules = derive<Rules>(() => { const rules = field.rules.get(); return stateLocked.get() && !rules.locked ? { ...rules, locked: true } : rules; });
  const editable = derive(() => canWrite && field.editable.get() && !stateLocked.get());
  // A view over the field (its prototype), so recordOf() still finds the record; writes go to the field itself.
  return Object.create(field, {
    meta: { value: meta }, rules: { value: rules }, editable: { value: editable },
    set: { value: (value: unknown) => field.set(value) },
    touched: { get: () => field.touched, set: (value: boolean) => { field.touched = value; } },
  }) as FieldLike;
}

/** Ask before discarding unsaved changes; resolves true to leave. */
export function confirmDiscard(): Promise<boolean> {
  return new Promise(resolve => {
    let answered = false;
    openModal({ title: 'Discard unsaved changes?', icon: 'info', tint: 'amber', narrow: true,
      body: () => html`<p>Your changes have not been saved. Leave this page and discard them?</p>`,
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
 * record in `?open=` and its tab in `?rtab=`.
 */
export interface PanelHost {
  /** True while the panel covers the list (narrow screens): the header shows Back instead of Expand. */
  overlay: Readable<boolean>;
  /** The record tab from the address. */
  tab: string | undefined;
  setTab(tab: string | null): void;
  /** Whether a navigation keeps this record open: only the list's search, filters or the record tab change. */
  stays(to: Route<any> | null): boolean;
  close(): void;
  /** Open another record of the model in the panel, or the one just created. */
  show(id: string): void;
  /** The record was deleted. */
  removed(): void;
}

// A record expanded from a side panel to its page keeps its store, so unsaved edits come along.
let handoff: { model: string; id: string; store: RecordStore } | null = null;
const handedOff = new WeakSet<RecordStore>();
function takeHandoff(model: string, id: string): RecordStore | null {
  const found = handoff && handoff.model === model && handoff.id === id ? handoff.store : null;
  handoff = null;
  return found;
}

export function RecordPage(ctx: AppContext, name: string, idText: string, panel?: PanelHost): HTMLElement {
  const { api, router } = ctx;
  const model = screenModel(ctx.schema, name);
  const isNew = idText === 'new';
  // An older bookmark carries a decimal id (/app/invoice/42): open the same record under its text id.
  const legacy = isNew ? null : legacyRecordId(idText);
  if (legacy && !panel) {
    const route = router.route.peek();
    queueMicrotask(() => void router.go(route?.name === 'view' ? 'view' : 'record', { model: name, id: legacy }, route?.query ?? {}, { replace: true }));
    return html`<div></div>` as HTMLElement;
  }
  const id = isNew ? null : recordId(legacy ?? idText);
  if (!isNew && !id) return missing(ctx, model, panel);
  const handed = panel ? null : takeHandoff(name, idText);
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

  const load = async () => {
    if (handed) { status.set('ready'); return; }
    status.set('loading');
    try {
      if (isNew) await store.create({ ...initialValues(model, query), ...(prefill?.values ?? {}) });
      else await store.open(id!);
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
    ready: () => RecordView(ctx, model, store, { isNew, extras, detail, query, prefill: prefill?.from ?? null, panel }),
  })}</div>` as HTMLElement;
  return page;
}

function missing(ctx: AppContext, model: ScreenModel, panel?: PanelHost): HTMLElement {
  if (!panel) ctx.crumbs.set([{ label: model.plural, href: ctx.router.href('list', { model: model.name }) }, { label: 'Not found' }]);
  return html`<div class="page">${EmptyState({ icon: identity(model).icon, tint: identity(model).tint, title: `${model.label} not found`,
    text: 'It may have been deleted, or your role cannot open it.', action: panel ? Button({ label: 'Close', onClick: () => panel.close() })
      : Button({ label: `Open ${model.plural.toLowerCase()}`, onClick: () => void ctx.router.go('list', { model: model.name }) }) })}</div>` as HTMLElement;
}

interface ViewOptions { isNew: boolean; extras: Readable<DetailExtras>; detail: Readable<RecordData | null>; query: Record<string, string>; /** The slip that filled this new record. */ prefill?: string | null;
  /** Shown in a list's side panel: the narrow layout. */ panel?: PanelHost }

function RecordView(ctx: AppContext, model: ScreenModel, store: RecordStore, options: ViewOptions): HTMLElement {
  const { api, router, prefs } = ctx;
  const { isNew, extras, panel } = options;
  const name = model.name, look = identity(model);
  const canUpdate = model.permissions?.update !== false;
  const canWrite = isNew ? model.permissions?.create !== false : canUpdate;
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
  const recordId = () => store.id;

  // ---------- title and crumbs ----------

  const titleText = derive(() => {
    if (isNew) return `New ${model.label.toLowerCase()}`;
    const field = has(model.title_field) ? store.field(model.title_field) : null;
    const data: Record<string, unknown> = { id: store.id, [model.title_field]: field?.value.get() };
    return titleOf(model, data);
  });
  if (!panel) effect(() => ctx.crumbs.set([{ label: model.plural, href: router.href('list', { model: name }) }, { label: titleText.get() }]));
  if (!isNew) {
    const remember = () => {
      const entry = { model: name, id: store.id!, title: untrack(() => titleText.get()) };
      prefs.recentRecords.set([entry, ...prefs.recentRecords.peek().filter(item => !(item.model === name && item.id === entry.id))].slice(0, 8));
    };
    remember();
  }

  // ---------- unsaved changes ----------

  let leaving = false;
  const changedNew = () => [...store.root.fields.values()].some(field => field.touched) || [...store.root.tables.values()].some(table => table.rows.peek().length > 0);
  // In a side panel, leaving the record (another row, Close, Back) asks; the list's own changes do not.
  router.confirmLeave(() => !leaving && (isNew ? changedNew() : store.dirty.peek()), () => confirmDiscard(), panel ? { stays: to => panel.stays(to) } : {});
  // Expand: the record page takes this store, unsaved edits included, so it does not ask.
  const expand = (tab: string | null) => {
    handoff = { model: name, id: isNew ? 'new' : String(store.id), store };
    handedOff.add(store);
    leaving = true;
    const going = isNew ? router.go('create', { model: name }) : router.go('record', { model: name, id: String(store.id) }, tab ? { tab } : {});
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
      toast(`${model.label} created`);
      if (panel) panel.show(store.id!);
      else void router.go('record', { model: name, id: String(store.id) }, {}, { replace: true });
    } else toast('Saved');
    return true;
  };

  // ---------- moves, actions, void and revise ----------

  const blockedMove = (to: string): string | null => {
    if (isNew) return 'Save the record first';
    if (store.dirty.get()) return 'Save or discard your changes first';
    const move = extras.get()._moves?.find(item => item.to === to);
    if (!move) return `This record cannot move from ${humanize(stateValue())} to ${humanize(to)}`;
    return move.allowed ? null : move.reason ?? `${move.label} is not available now`;
  };
  const moved = (to: string, label: string) => {
    const number = sequence && has(sequence.name) ? store.field(sequence.name).value.peek() : null;
    toast(number && sequence?.name !== undefined && (sequence as any).sequence?.assign_on === to ? `${label} done. Number ${number}.` : `Moved to ${humanize(to)}`);
  };
  const runMove = async (to: string) => {
    const move = extras.peek()._moves?.find(item => item.to === to);
    const label = move?.label ?? humanize(to);
    // A move that asks for fields (a reason required once lost, say) collects them first.
    const asked = (move?.asks ?? []).map(field => model.fields.find(meta => meta.name === field)).filter(meta => meta !== undefined);
    if (asked.length) {
      const inputs = asked.map(meta => ({ ...meta, read_only: false, required: Boolean(meta.required || meta.required_when), applies_when: null, required_when: null, locked_when: null }));
      ActionDialog({ name: to, label } as ActionMeta, inputs, async values => {
        if (await store.move(to, values)) { moved(to, label); return null; }
        const errors = Object.fromEntries(asked.map(meta => [meta.name, store.field(meta.name).error.peek()]).filter(([, error]) => error));
        return Object.keys(errors).length ? errors : store.formError.peek() ?? store.conflict.peek()?.message ?? `${label} did not run`;
      });
      return;
    }
    const ok = await store.move(to);
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
  const correct = (kind: 'void' | 'revise' | 'unpost' | 'repost') => {
    // A repost's reason is optional; the other corrections need one.
    const optional = kind === 'repost';
    const reason = localField({ name: 'reason', type: 'longtext', label: optional ? 'Reason (optional)' : 'Reason', required: !optional, max_length: 1000 } as any, '');
    const noun = model.label.toLowerCase();
    const copy = {
      void: { title: `Void ${titleText.peek()}`, icon: 'circle-x', tint: 'rose' as const, variant: 'danger' as const, submit: `Void ${noun}`,
        note: 'Voiding keeps the record and its number, and reverses what it posted. It can’t be undone.' },
      revise: { title: `Revise ${titleText.peek()}`, icon: 'copy', tint: 'blue' as const, variant: 'primary' as const, submit: `Revise ${noun}`,
        note: 'Revising voids this record and opens an editable copy. The copy gets its own number when it is issued.' },
      unpost: { title: `Unpost ${titleText.peek()}`, icon: 'rotate-ccw', tint: 'amber' as const, variant: 'danger' as const, submit: `Unpost ${noun}`,
        note: `Unposting reverses what this ${noun} posted and returns it to the state it was posted from. It keeps its number. Repost it when it is right.` },
      repost: { title: `Repost ${titleText.peek()}`, icon: 'repeat', tint: 'blue' as const, variant: 'primary' as const, submit: `Repost ${noun}`,
        note: `Reposting checks this ${noun} again and posts it with fresh entries. It keeps its number.` },
    }[kind];
    let busy = false;
    const submit = async (close: () => void) => {
      const text = String(reason.value.peek() ?? '').trim();
      if (!text && !optional) { reason.error.set('Give a reason'); return; }
      if (text.length > 1000) { reason.error.set('Use at most 1,000 characters'); return; }
      if (busy) return;
      busy = true;
      try {
        if (store.dirty.peek()) { reason.error.set('Save or discard your changes first'); return; }
        const etag = store.etag.peek();
        const response = kind === 'void' ? await api.voidRecord(name, store.id!, text, etag)
          : kind === 'revise' ? await api.revise(name, store.id!, text, etag)
          : kind === 'unpost' ? await api.unpost(name, store.id!, text, etag) : await api.repost(name, store.id!, text, etag);
        close();
        if (kind === 'revise') {
          toast('Revised. You are editing the new draft.', { icon: 'copy' });
          const firstTable = model.children?.[0]?.name;
          if (panel) panel.show(response.data.id);
          else void router.go('record', { model: name, id: String(response.data.id) }, firstTable ? { tab: `t-${firstTable}` } : {});
        } else {
          store.applyServer(response.data, response.etag);
          await store.loadAll().catch(() => {});
          toast(kind === 'void' ? 'Voided. The number stays used.' : kind === 'unpost' ? 'Unposted. What it posted was reversed.' : 'Reposted.',
            { icon: copy.icon });
        }
      } catch (error) {
        if (error instanceof ValidationError && error.fields.reason) reason.error.set(error.fields.reason);
        else if (error instanceof ValidationError && error.fields._effects) reason.error.set(error.fields._effects);
        else reason.error.set(error instanceof ApiError ? error.message : 'This did not work. Try again.');
      } finally { busy = false; }
    };
    let closeDialog = () => {};
    closeDialog = openModal({ title: copy.title, icon: copy.icon, tint: copy.tint, narrow: true,
      onSubmit: () => void submit(closeDialog),
      body: () => [html`<p class="muted">${copy.note}</p>`, fieldFor({ field: reason, full: true })],
      footer: close => DialogFooter({ cancel: close, submit: { label: copy.submit, variant: copy.variant, onClick: () => void submit(close) } }) });
  };
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

  const moreItems = (): MenuItem[] => {
    const items: MenuItem[] = [{ label: 'Copy link', icon: 'link', onSelect: () => void copyText(location.href, 'the link') }];
    if (!isNew) items.push({ label: 'Reload', icon: 'rotate-ccw', onSelect: () => void store.reload().catch(() => {}) });
    if (foldable) items.push({ label: 'Reset layout', icon: 'layout-grid', disabled: !folds.changed, onSelect: () => folds.reset() });
    const now = extras.peek();
    if (now._revise) items.push({ label: `Revise ${model.label.toLowerCase()}`, icon: 'copy', disabled: !now._revise.allowed, onSelect: () => correct('revise') });
    if (now._void) items.push({ label: `Void ${model.label.toLowerCase()}`, icon: 'circle-x', danger: true, disabled: !now._void.allowed, onSelect: () => correct('void') });
    if (now._repost) items.push({ label: `Repost ${model.label.toLowerCase()}`, icon: 'repeat', disabled: !now._repost.allowed, onSelect: () => correct('repost') });
    if (now._unpost) items.push({ label: `Unpost ${model.label.toLowerCase()}`, icon: 'rotate-ccw', danger: true, disabled: !now._unpost.allowed, onSelect: () => correct('unpost') });
    // A record that ever posted is kept (its entries name it), so the server refuses its delete.
    if (!isNew && model.permissions?.delete && !locked.peek() && now._deletable !== false) items.push('separator', { label: 'Delete', icon: 'trash-2', danger: true, onSelect: remove });
    return items;
  };

  const runLifecycle = async (operation: 'confirm' | 'cancel' | 'reopen', reason?: string): Promise<string | null> => {
    if (store.dirty.peek()) return 'Save or discard your changes first';
    try {
      const response = await api.lifecycle(name, store.id!, operation, store.etag.peek(), reason);
      store.applyServer(response.data, response.etag);
      await store.loadAll();
      toast(`${model.lifecycle?.labels[operation] ?? humanize(operation)} done`);
      return null;
    } catch (error) { return error instanceof ApiError ? error.fields.reason ?? error.message : 'The operation failed'; }
  };
  const lifecycleAction = (operation: 'confirm' | 'cancel' | 'reopen') => {
    const label = model.lifecycle?.labels[operation] ?? humanize(operation);
    if (operation === 'cancel') {
      ActionDialog({ name: operation, label } as ActionMeta,
        [{ name: 'reason', type: 'longtext', label: 'Reason', required: true, max_length: 1000 } as FieldSchema],
        values => runLifecycle(operation, String(values.reason ?? '')));
    } else void runLifecycle(operation).then(error => { if (error) toast(error, { icon: 'info' }); });
  };

  // The header's buttons: lifecycle operations, moves, actions and slips, in that order.
  type HeaderEntry = { label: string; icon: string; blocked: () => string | null; run: () => void; variant: 'primary' | 'success' | 'default' };
  const headerEntries = (): HeaderEntry[] => {
    const moves = extras.get()._moves ?? [];
    const firstAllowed = moves.findIndex(move => move.allowed);
    return [
      ...(extras.get()._lifecycle_actions ?? []).map(operation => ({ label: model.lifecycle?.labels[operation] ?? humanize(operation), icon: operation === 'cancel' ? 'rotate-ccw' : 'arrow-right',
        blocked: () => store.dirty.get() ? 'Save or discard your changes first' : null, run: () => lifecycleAction(operation), variant: 'primary' as const })),
      ...moves.map((move, index) => ({ label: move.label, icon: 'arrow-right', blocked: () => blockedMove(move.to), run: () => void runMove(move.to),
        variant: index === (firstAllowed < 0 ? 0 : firstAllowed) ? (/won|paid|done|complete|approve/i.test(move.to) ? 'success' as const : 'primary' as const) : 'default' as const })),
      ...actions().map(action => ({ label: action.label, icon: 'zap', blocked: () => actionBlocked(action), run: () => runAction(action),
        variant: !moves.length && action === actions()[0] ? 'primary' as const : 'default' as const })),
      ...(model.slips ?? []).map(slip => ({ label: slip.label, icon: 'file-text', blocked: slipBlocked, run: () => openSlip(slip.name), variant: 'default' as const })),
    ];
  };
  const askEvaNow = () => openEvaPanel(ctx, ctx.eva, { name, label: model.label }, store.id!, titleText.peek());
  /** In a side panel the header keeps two buttons; the rest join the More menu. */
  const PANEL_BUTTONS = 2;
  const headerActions = (): Child => {
    if (isNew) return null;
    const entries = headerEntries();
    const shown = panel ? entries.slice(0, PANEL_BUTTONS) : entries;
    const buttons = shown.map(entry => MoveButton({ label: entry.label, icon: entry.icon, blocked: entry.blocked, variant: entry.variant, onMove: entry.run }));
    // Ask Eva, when Eva is on for this person: a chat about this record, in a side panel.
    const askEva = ctx.eva.get() && !panel ? Button({ label: 'Ask Eva', icon: 'sparkles', iconTint: 'violet', hideLabelOnPhone: true, onClick: askEvaNow }) : null;
    // Print, when the role may print this model and the server has the print extra: the preview is the server's PDF.
    const designs = ctx.schema.schema.printing?.available ? model.prints ?? [] : [];
    const printNow = () => openPrint(ctx, model, store.id!, titleText.peek(), designs);
    const print = designs.length && !panel ? Button({ label: 'Print', icon: 'printer', hideLabelOnPhone: true, onClick: printNow }) : null;
    const items = (): MenuItem[] => {
      if (!panel) return moreItems();
      const rest: MenuItem[] = entries.slice(PANEL_BUTTONS).map(entry => ({ label: entry.label, icon: entry.icon,
        onSelect: () => { const why = untrack(entry.blocked); if (why) toast(why, { icon: 'info' }); else entry.run(); } }));
      if (designs.length) rest.push({ label: 'Print', icon: 'printer', onSelect: printNow });
      if (ctx.eva.peek()) rest.push({ label: 'Ask Eva', icon: 'sparkles', onSelect: askEvaNow });
      return [...rest, ...(rest.length ? ['separator' as const] : []), { label: 'Open full page', icon: 'maximize-2', onSelect: () => expand(active.peek()) }, ...moreItems()];
    };
    return [...buttons, print, askEva, MenuButton({ icon: 'ellipsis', tip: 'More actions', ariaLabel: 'More actions', items, menuLabel: 'More actions' })];
  };

  // ---------- header ----------

  // The title is edited in place, except a new record's and a document number.
  const titleProp = isNew || !has(model.title_field) || (numbered && model.title_field === numbered.name) ? () => titleText.get() : view(model.title_field);
  const markName = () => titleText.get();
  const mark = look.person ? Avatar({ name: markName, size: 'xl' }) : RecordMark({ name: markName, size: 'lg', tint: look.tint });
  const metaFacts = keyFields(model).filter(meta => meta.name !== model.title_field && meta.name !== 'name' && has(meta.name) && !isStateField(model, meta)).slice(0, 3)
    .map(meta => html`<span class="ev-fact" title=${meta.label}>${displayValue(view(meta.name), { links: ctx.links, empty: `No ${meta.label.toLowerCase()}` })}</span>`);
  const numberFact = sequence && has(sequence.name) && !isNew ? when(() => !store.field(sequence.name).value.get(),
    () => html`<span class="muted">${(sequence as any).sequence?.assign_on && (sequence as any).sequence.assign_on !== 'create' ? `Number assigned on ${humanize((sequence as any).sequence.assign_on).toLowerCase()}` : 'Number assigned on save'}</span>`) : null;
  // The standard name is the document number: in mono next to the title, or when it will be assigned.
  const naming = model.naming?.field === 'name' && model.title_field !== 'name' && has('name') ? model.naming : null;
  const nameFact = naming ? live(() => {
    const value = store.field('name').value.get();
    // A title that falls back to the name already shows it as the heading.
    if (value && String(value) === titleText.get()) return null;
    return value ? html`<span class="ev-fact mono" title="Name">${String(value)}</span>`
      : naming.placeholder ? html`<span class="muted">${naming.placeholder}</span>` : null;
  }) : null;
  const header = DocHeader({
    type: model.label, icon: look.icon, mark,
    title: titleProp,
    mono: () => Boolean(numbered && model.title_field === numbered.name && has(numbered.name) && store.field(numbered.name).value.get())
      // A title that is the record's name (no other title source) reads as a document number too.
      || Boolean(naming && store.field('name').value.get() && String(store.field('name').value.get()) === titleText.get()),
    meta: [nameFact, ...metaFacts, numberFact, model.lifecycle && !isNew ? live(() => {
      const current = extras.get()._lifecycle;
      return current ? Chip({ label: `${model.lifecycle!.labels[current.state]} · Revision ${current.revision}`, tone: current.state === 'confirmed' ? 'green' : 'slate' }) : null;
    }) : null].filter(Boolean) as Child[],
    actions: [live(headerActions)],
  });
  // The panel's top line: the record type and its number, Expand and Close (Back to the list while the panel covers it).
  const numberText = numbered && numbered.name !== model.title_field && has(numbered.name) ? () => String(store.field(numbered.name).value.get() ?? '') : null;
  const panelBar = panel ? html`<div class="ev-panel-bar">
    ${when(panel.overlay, () => Button({ label: model.plural, icon: 'chevron-left', variant: 'ghost', size: 'small', onClick: () => panel.close() }))}
    <span class="eyebrow">${Icon(look.icon, { size: 'xs' })}${model.label}</span>${numberText ? html`<span class="mono muted">${numberText}</span>` : null}<span class="grow"></span>
    ${when(() => !panel.overlay.get(), () => [IconButton({ icon: 'maximize-2', tip: 'Open full page', onClick: () => expand(active.peek()) }),
      IconButton({ icon: 'x', tip: 'Close (Esc)', onClick: () => panel.close() })])}</div>` : null;

  // ---------- banners ----------

  const link = (target: number, label: string) => html`<a class="link-q" href=${router.href('record', { model: name, id: String(target) })}>${label}</a>`;
  const lifecycle = state && (state.locked?.length || state.voidable?.length) && !isNew ? () => {
    const value = stateValue(), now = extras.get(), revises = has('revises') ? store.field('revises').value.get() as number | null : null;
    const revisedBy = now.revised_by ?? null;
    const reviseOf = revises ? [' Revision of ', link(revises, `${model.label} ${revises}`), '.'] : [];
    if (value === 'void') return StateBanner({ tone: 'danger', title: 'Void.', text: [`This ${model.label.toLowerCase()} was voided. What it posted was reversed and its number is kept.`,
      ...(revisedBy ? [' Replaced by ', link(revisedBy, `${model.label} ${revisedBy}`), '.'] : []), ...reviseOf] });
    if (now._locked) {
      const buttons = [now._revise?.allowed ? Button({ label: 'Revise', icon: 'copy', size: 'small', onClick: () => correct('revise') }) : null,
        now._void?.allowed ? Button({ label: 'Void', icon: 'circle-x', size: 'small', variant: 'soft-danger', onClick: () => correct('void') }) : null,
        now._unpost?.allowed ? Button({ label: 'Unpost', icon: 'rotate-ccw', size: 'small', variant: 'soft-danger', onClick: () => correct('unpost') }) : null,
        // Unposted into a locked state (it posted from one): it stays locked and posts again by Repost.
        now._repost?.allowed ? Button({ label: 'Repost', icon: 'repeat', size: 'small', onClick: () => correct('repost') }) : null].filter(Boolean) as Node[];
      const editable = model.fields.filter(field => (field as any).editable_when_locked).map(field => field.label.toLowerCase());
      // Only the corrections this user can make now (the state allows them and so does the role).
      const ways = [now._void?.allowed ? 'void' : null, now._revise?.allowed ? 'revise' : null].filter(Boolean);
      const correction = ways.length === 2 ? ' To correct it, void or revise.' : ways.length ? ` To correct it, ${ways[0]} it.` : '';
      return StateBanner({ tone: 'info', title: `${humanize(value)} and locked.`, actions: buttons,
        text: [editable.length ? `Only ${editable.join(' and ')} can change.` : 'Nothing can change.', correction, ...reviseOf] });
    }
    const next = (state.moves.find(move => (Array.isArray(move.from) ? move.from : [move.from]).includes(value) && state.locked?.includes(move.to)));
    // An unposted record: what it posted was reversed, and it posts again by Repost (listed for roles that may).
    if (now._repost) return StateBanner({ tone: 'neutral', title: `${humanize(value)}, unposted.`,
      actions: now._repost.allowed ? [Button({ label: 'Repost', icon: 'repeat', size: 'small', onClick: () => correct('repost') })] : [],
      text: ['What it posted was reversed and its number is kept. Everything can change until it is reposted.', ...reviseOf] });
    return StateBanner({ tone: 'neutral', title: `${humanize(value)}.`, text: [next ? `Everything can change until it is ${humanize(next.to).toLowerCase()}.` : 'Everything can change.', ...reviseOf] });
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

  // ---------- flow and summary ----------

  const flow = state && !isNew ? (() => {
    const { steps, good, bad } = flowFromState(state as any);
    return StatusFlow({ steps, good, bad, current: stateValue, label: `${model.label} status`, blocked: to => untrack(() => blockedMove(to)), onMove: to => void runMove(to) });
  })() : null;

  const perRecord = (model.measures ?? []).filter(measure => measure.per_record);
  const recordMeasures = cell<Record<string, unknown> | null>(null);
  if (perRecord.length && !isNew) effect(() => { options.detail.get(); void api.recordMeasures(name, store.id!).then(values => recordMeasures.set(values), () => recordMeasures.set(null)); });
  const stats: StatProps[] = summaryFields(model).filter(meta => has(meta.name)).map(meta => ({
    label: meta.label, value: () => {
      const value = view(meta.name).value.get();
      if (meta.type === 'link' && meta.target && value) return ctx.links.title(meta.target, String(value)).get() ?? '…';
      return formatOf(meta, value);
    },
    empty: 'Not set',
    note: view(meta.name).calculated ? 'Calculated' : undefined,
  }));
  for (const measure of perRecord) if (stats.length < 4) stats.push({ label: measureLabel(measure), value: () => recordMeasures.get() ? measureText(ctx.schema, model, measure, recordMeasures.get()![measure.name]) : '…' });
  const summary = stats.length && !isNew ? SummaryStrip(stats) : null;

  // ---------- tabs ----------

  const tables = (model.children ?? []).filter(child => store.root.tables.has(child.name));
  const journal = Boolean(model.journal) && !isNew;
  const relations = !isNew ? (model.relations ?? []).filter(relation => ctx.schema.schema.models.some(item => item.name === relation.model)) : [];
  const tabItems: TabItem[] = [
    ...(journal ? [{ id: 'activity', label: 'Activity' }] : []),
    { id: 'details', label: 'Details' },
    ...(model.lifecycle && !isNew ? [{ id: 'revisions', label: 'Revisions' }] : []),
    ...tables.map(child => ({ id: `t-${child.name}`, label: child.label, count: () => store.table(child.name).rows.get().length })),
    ...(relations.length ? [{ id: 'related', label: 'Related' }] : []),
  ];
  // A record that has posted nothing yet still lists each target, with zero rows.
  const postings = !isNew && model.effects?.length ? derive(() => extras.get()._effects?.some(item => item.rows || item.reversals) ? extras.get()._effects! : null) : null;
  if (postings) tabItems.push({ id: 'postings', label: 'Postings', count: () => postings.get()?.length ? postings.get()!.reduce((sum, item) => sum + item.rows + item.reversals, 0) : null });
  const wanted = panel ? panel.tab : options.query.tab;
  const active = cell(tabItems.some(tab => tab.id === wanted) ? wanted! : tabItems[0].id, { name: `${name} tab` });
  effect(() => {
    const tab = active.get();
    untrack(() => {
      // In a side panel the tab is the list address's rtab.
      if (panel) { if ((panel.tab ?? tabItems[0].id) !== tab) panel.setTab(tab === tabItems[0].id ? null : tab); panel.tab = tab === tabItems[0].id ? undefined : tab; return; }
      const current = router.route.peek();
      if (!current || current.name !== (isNew ? 'create' : 'record')) return;
      if ((current.query.tab ?? tabItems[0].id) === tab) return;
      const next = { ...current.query };
      if (tab === tabItems[0].id) delete next.tab; else next.tab = tab;
      void router.navigate(router.href(current.name as 'record', current.params as any, next), { replace: true });
    });
  });
  const tabs = Tabs({ tabs: tabItems, active, label: `${model.label} sections`, doc: true, idPrefix: `ev-rec-${name}` });

  const details = () => {
    const groups = sections(model).map(section => ({ ...section, fields: section.fields.filter(meta => has(meta.name)) })).filter(section => section.fields.length);
    const childLinks = tables.length && !isNew ? tables.map(child => {
      const table = store.table(child.name);
      const sums = model.fields.filter(meta => (meta.compute as any)?.$expr === 'aggregate' && (meta.compute as any).table === child.name && has(meta.name));
      return html`<button type="button" class="fs-link" on:click=${() => active.set(`t-${child.name}`)}>${IconTile('rows-3', { tint: look.tint, size: 30 })}
        <span class="grow"><b>${() => { const count = table.rows.get().length; return count ? `${count} ${count === 1 ? 'row' : 'rows'}` : `No ${child.label.toLowerCase()} yet`; }}</b>
        <span class="muted">${() => sums.map(meta => `${meta.label} ${formatOf(meta, store.field(meta.name).value.get())}`).join(' · ') || child.label}</span></span>${Icon('chevron-right', { size: 'sm', className: 'muted' })}</button>`;
    }) : null;
    // A new record shows the fields it can fill in, and the naming series field saying it is assigned on save.
    if (isNew) return html`<div class="ev-new-form">${groups.map((section, index) => {
      const fields = section.fields.filter(meta => (!meta.read_only || meta.name === model.naming?.field) && !meta.compute && !isStateField(model, meta)).map(meta => view(meta.name));
      return FoldSection({ title: section.label, fields, folds, ...foldOf(section, index), body: html`<div class="fs-g">${fields.map(field => fieldFor({ field, links: ctx.links }))}</div>` });
    })}</div>`;
    const sectionsShown = groups.map((section, index) => FormSection({ title: section.label, fields: section.fields.map(meta => view(meta.name)), links: ctx.links,
      extra: index === groups.length - 1 ? childLinks : null, fold: { folds, ...foldOf(section, index) } }));
    // The full page's side column folds into the panel: Files and Record info close the Details tab.
    return panel ? [...sectionsShown, html`<div class="ev-panel-blocks">${SidePanel(ctx, model, store, view, relations, active, true)}</div>`] : sectionsShown;
  };
  // Sections people open and close (evlet.Section), remembered per user and model by title; the app's
  // collapsed value is where they start. In the narrow side panel every section can close.
  const folds = new Folds(prefs.cell<Record<string, boolean>>(`form.${name}.open`, {}), isNew);
  const foldOf = (section: { collapsible?: boolean; collapsed?: boolean | 'empty' }, index: number) =>
    ({ collapsible: Boolean(section.collapsible || panel), collapsed: section.collapsible ? section.collapsed ?? false : false, index });
  const foldable = Boolean(panel) || sections(model).some(section => section.collapsible);
  // A refused save: open the sections with errors, then scroll to and focus the first one.
  const showErrors = () => {
    const fields = sections(model).flatMap(section => section.fields).filter(meta => has(meta.name)).map(meta => view(meta.name));
    if (!fields.some(field => field.error.peek())) return;
    if (active.peek() !== 'details') active.set('details');
    revealFirstError(frame, fields);
  };
  // A #field address (/app/invoice/7#due) opens that field's section and focuses it.
  const fromHash = () => { let target = ''; try { target = decodeURIComponent(location.hash.slice(1)); } catch { /* not a field name */ } if (target && has(target) && active.peek() === 'details') revealField(frame, target); };
  if (!panel) { queueMicrotask(fromHash); window.addEventListener('hashchange', fromHash); onCleanup(() => window.removeEventListener('hashchange', fromHash)); }
  const lockedText = () => model.fields.some(field => (field as any).editable_when_locked) ? `Locked. Lines can’t change while it is ${humanize(stateValue()).toLowerCase()}.` : 'Locked. Lines can’t change now.';
  const tablePanel = (child: { name: string; label: string }) => panel ? ChildRows({ table: store.table(child.name), title: child.label,
    locked: () => locked.get() || !canWrite, lockedText, links: ctx.links, onOpenFull: () => expand(`t-${child.name}`),
    totals: model.fields.filter(meta => (meta.compute as any)?.$expr === 'aggregate' && (meta.compute as any).table === child.name && has(meta.name))
      .map(meta => ({ label: meta.label, value: () => formatOf(meta, store.field(meta.name).value.get()) })) }) : ChildTable({ table: store.table(child.name), title: child.label, icon: 'rows-3', tint: look.tint,
    locked: () => locked.get() || !canWrite, lockedText, links: ctx.links, crumb: () => titleText.get(),
    prefs: prefs.cell(`grid.${name}.${child.name}`, emptyPrefs()) });

  const panels = [
    journal ? TabPanel(tabs, 'activity', active, () => Activity(ctx, model, store)) : null,
    TabPanel(tabs, 'details', active, details),
    model.lifecycle && !isNew ? TabPanel(tabs, 'revisions', active, () => Revisions(ctx, model, store.id!)) : null,
    ...tables.map(child => TabPanel(tabs, `t-${child.name}`, active, () => tablePanel(child))),
    relations.length ? TabPanel(tabs, 'related', active, () => Related(ctx, model, store.id!, relations)) : null,
    postings ? TabPanel(tabs, 'postings', active, () => Postings(ctx, postings)) : null,
  ];

  // ---------- save bar ----------

  const saveBar = isNew
    ? html`<div class="ev-savebar" role="region" aria-label="Create">${Button({ label: 'Cancel', variant: 'ghost', onClick: () => panel ? panel.close() : void router.go('list', { model: name }) })}
        <span class="grow muted">${Icon('info', { size: 'sm' })} Values are checked again when you create it.</span>
        ${Button({ label: () => store.saving.get() ? 'Creating…' : `Create ${model.label.toLowerCase()}`, variant: 'primary', icon: 'check', disabled: () => store.saving.get() || !store.ready.get(), onClick: () => void save() })}</div>`
    : when(() => store.dirty.get(), () => html`<div class="ev-savebar" role="region" aria-label="Unsaved changes">
        <span class="grow">${Icon('pencil', { size: 'sm' })} Unsaved changes</span>
        ${Button({ label: 'Discard', variant: 'ghost', onClick: () => void store.revert() })}
        ${Button({ label: () => store.saving.get() ? 'Saving…' : 'Save', variant: 'primary', icon: 'check', disabled: () => store.saving.get(), onClick: () => void save() })}</div>`);

  // ---------- side panel ----------

  const side = isNew || panel ? undefined : SidePanel(ctx, model, store, view, relations, active);

  const frame = DocFrame({ tint: look.tint, density: prefs.density, sidePosition: 'right', variant: panel ? 'panel' : 'page',
    main: [panelBar, header, options.prefill ? StateBanner({ tone: 'info', title: `Filled in from ${options.prefill}.`, text: `Nothing is saved until you select Create.` }) : null, live(problems), lifecycle ? live(lifecycle) : null, flow, summary, tabs, panels, saveBar], side });
  frame.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && (isNew || store.dirty.peek())) { event.preventDefault(); void save(); }
  });
  return frame;
}

// ---------- action dialog ----------

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

// ---------- side panel ----------

function SidePanel(ctx: AppContext, model: ScreenModel, store: RecordStore, view: (name: string) => FieldLike, relations: NonNullable<ScreenModel['relations']>, active: { set(value: string): void },
  narrow = false): Child {
  const has = (field: string) => store.root.fields.has(field);
  // A narrow panel shows key fields in its header and relations in the Related tab.
  const keys = narrow ? [] : keyFields(model).filter(meta => has(meta.name));
  const blocks: Child[] = [];
  if (keys.length) blocks.push(SideBlock({ title: 'Key fields', action: html`<button type="button" class="h-link" on:click=${() => active.set('details')}>All fields</button>`,
    body: KeyValueList(keys.map(meta => view(meta.name)), { links: ctx.links }) }));
  if (relations.length && !narrow) blocks.push(SideBlock({ title: 'Related', body: html`<div>${relations.map(relation => {
    const total = cell<number | null>(null);
    // Counted again after every write of this record: a move or an action may add related records.
    effect(() => {
      store.etag.get();
      void ctx.api.related(model.name, store.id!, relation.name, { limit: 1 }).then(page => total.set(page.total), () => total.set(null));
    });
    const target = screenModel(ctx.schema, relation.model);
    return html`<button type="button" class="mini-card" on:click=${() => active.set('related')}>${IconTile(identity(target).icon, { tint: identity(target).tint, size: 28 })}
      <span class="grow"><span class="nm">${relation.label}</span><span class="sub">${() => total.get() === null ? 'Open to see them' : `${total.get()} ${total.get() === 1 ? 'record' : 'records'}`}</span></span>${Icon('chevron-right', { size: 'sm', className: 'muted' })}</button>`;
  })}</div>` }));
  if (model.attachments) blocks.push(SideBlock({ title: 'Files', body: Files(ctx, model, store) }));
  const revises = has('revises') ? view('revises') : null;
  const extras = () => extrasOf(ctx.api.detail(model.name, store.id!).get());
  blocks.push(SideBlock({ title: 'Record info', body: html`<dl class="fields">
    <dt>ID</dt><dd class="num mono">${String(store.id)}</dd>
    ${revises ? when(() => Boolean(revises.value.get()), () => html`<dt>Revises</dt><dd><a class="link-q" href=${() => ctx.router.href('record', { model: model.name, id: String(revises.value.get()) })}>${() => `${model.label} ${revises.value.get()}`}</a></dd>`) : null}
    ${when(() => Boolean(extras().revised_by), () => html`<dt>Revised by</dt><dd><a class="link-q" href=${() => ctx.router.href('record', { model: model.name, id: String(extras().revised_by) })}>${() => `${model.label} ${extras().revised_by}`}</a></dd>`)}
    </dl>` }));
  return blocks;
}

/** The print preview: the PDF the server renders for this person, with Download and File a copy. */
export function openPrint(ctx: AppContext, model: ScreenModel, id: string, title: string, designs: Array<{ name: string; title: string }>) {
  const design = cell(designs[0].name);
  const canFile = Boolean(model.attachments) && model.permissions?.update !== false;
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
  const load = () => void ctx.api.attachments(model.name, store.id!).then(items => files.set(items), error => problem.set(error instanceof ApiError ? error.message : 'Files could not be loaded.'));
  load();
  // A filed print copy is a new attachment of this record.
  const changed = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail?.model === model.name && detail?.id === store.id) load(); };
  window.addEventListener('evlet-files-changed', changed);
  onCleanup(() => window.removeEventListener('evlet-files-changed', changed));
  const canWrite = model.permissions?.update !== false;
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
      ctx.api.upload(model.name, store.id!, file.name, content).then(() => { toast(`${file.name} attached`, { icon: 'paperclip' }); load(); },
        error => toast(error instanceof ApiError ? `Not attached: ${error.fields.attachment ?? error.message}` : 'Not attached.', { icon: 'info' }));
    };
    reader.readAsDataURL(file);
  };
  return html`<div class="ev-files">
    ${() => problem.get() ? html`<p class="muted">${problem.get()}</p>` : files.get() === null ? html`<p class="muted">Loading…</p>` : files.get()!.length === 0
      ? html`<p class="muted">No files yet.</p>` : html`<div class="files">${files.get()!.map(file => html`<div class="file">${Icon('paperclip', { size: 'sm', className: 'muted' })}
        <div style="flex:1;min-width:0"><a class="link" rel="external" href=${ctx.api.url(`/api/${model.name}/${store.id}/attachments/${file.id}`)}>${file.name}</a><div class="muted" style="font-size:12px">${size(file.size)}</div></div></div>`)}</div>`}
    ${canWrite ? when(() => !locked(), () => html`<label class="btn sm ev-upload">${Icon('upload', { size: 'sm', className: 'tint' })}Attach a file<input type="file" hidden on:change=${upload}></label>`) : null}</div>`;
}

// ---------- related ----------

function Related(ctx: AppContext, model: ScreenModel, id: string, relations: NonNullable<ScreenModel['relations']>): Child {
  return relations.map(relation => {
    const target = screenModel(ctx.schema, relation.model);
    const rows = cell<RecordData[]>([]), total = cell<number | null>(null), problem = cell('');
    const more = (offset: number) => void ctx.api.related(model.name, id, relation.name, { limit: relation.limit, offset })
      .then(page => batch(() => { rows.set([...rows.peek(), ...page.items]); total.set(page.total); }), error => problem.set(error instanceof ApiError ? error.message : 'Not loaded.'));
    more(0);
    const columns: Column<RecordData>[] = listFields(target).filter(meta => meta.name !== relation.link).slice(0, 4).map((meta, index) => ({
      key: meta.name, label: meta.label, width: index === 0 ? 220 : 150, value: (row: RecordData) => row[meta.name],
      align: ['currency', 'decimal', 'integer', 'percent'].includes(meta.type) ? 'right' : 'left', sortable: false,
      render: index === 0 ? (row: () => RecordData) => html`<a class="ev-row-link" href=${() => ctx.router.href('record', { model: target.name, id: String(row().id) })}>${() => titleOf(target, row())}</a>`
        : (row: () => RecordData) => cellView(ctx, target, meta, row),
    }));
    return html`<section class="panel ev-related" aria-label=${relation.label}><div class="panel-h"><h3>${relation.label}</h3><span class="muted num">${() => total.get() === null ? '' : String(total.get())}</span></div>
      ${() => problem.get() ? html`<p class="muted" style="padding:12px">${problem.get()}</p>` : null}
      ${DataTable<RecordData>({ columns, rows, key: row => row.id, label: relation.label, onOpen: row => void ctx.router.go('record', { model: target.name, id: String(row.id) }), height: 300,
        empty: () => html`<span class="muted">${`No ${relation.label.toLowerCase()} yet.`}</span>` })}
      ${when(() => (total.get() ?? 0) > rows.get().length, () => html`<div class="ev-more">${Button({ label: 'Show more', size: 'small', onClick: () => more(rows.peek().length) })}</div>`)}</section>`;
  });
}

// ---------- postings ----------

function Postings(ctx: AppContext, effects: Readable<DetailExtras['_effects'] | null>): Child {
  const label = (target: string) => { try { return ctx.schema.model(target).plural; } catch { return humanize(target); } };
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

function Activity(ctx: AppContext, model: ScreenModel, store: RecordStore): Child {
  const page = cell<JournalPage | null>(null), problem = cell('');
  const load = () => void ctx.api.journal(model.name, store.id!, { limit: 50 }).then(result => page.set(result), error => problem.set(error instanceof ApiError ? error.message : 'The history could not be loaded.'));
  load();
  const text = cell('');
  const canNote = Boolean(model.journal?.kinds?.includes('note'));
  const label = (field: string) => fieldOf(model, field)?.label ?? humanize(field);
  const value = (field: string, raw: unknown) => { const meta = fieldOf(model, field); return raw === '[masked]' ? '[masked]' : raw === null || raw === undefined || raw === '' ? 'empty' : meta ? formatOf(meta, raw) : String(raw); };
  const table = (name: string) => model.children?.find(child => child.name === name)?.label ?? humanize(name);
  const target = (name: string) => { try { return ctx.schema.model(name).plural; } catch { return humanize(name); } };
  const action = (name: string) => model.actions?.find(item => item.name === name)?.label ?? humanize(name);
  const state = (name: unknown) => humanize(String(name ?? ''));
  const person = (id: unknown) => id === null || id === undefined ? null : page.peek()?.users[String(id)] ?? `User ${id}`;
  const addNote = async () => {
    const body = text.peek().trim();
    if (!body) return;
    try { await ctx.api.addNote(model.name, store.id!, body); text.set(''); toast('Note added', { icon: 'sticky-note' }); load(); }
    catch (error) { toast(error instanceof ApiError ? error.message : 'The note was not added.', { icon: 'info' }); }
  };
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
      case 'message': return html`<div class="note mail">${data.subject ? html`<div class="subj">${String(data.subject)}</div>` : null}${String(data.text ?? '')}</div>`;
      case 'follow_up': {
        const who = person(data.assignee);
        const facts = [data.due ? `Due ${formatDate(data.due)}` : null, who ? `for ${who}` : null].filter(Boolean).join(' ');
        return html`<div class="note">${String(data.text ?? '')}<div class="ev-fu muted">${facts}${facts ? ' · ' : ''}${item.done ? Chip({ label: 'Done', tone: 'green', icon: 'check' }) : Chip({ label: 'Open', tone: 'amber', icon: 'clock' })}</div></div>`;
      }
      default: return data.text || data.reason ? html`<div class="note">${String(data.text ?? data.reason)}</div>` : null;
    }
  };
  const entry = (item: JournalEntry) => {
    const [icon, tint] = KINDS[item.kind] ?? ['activity', 'slate'];
    const hidden = item.partial ? html`<span class="muted ev-partial" title="Parts of this entry are hidden from your role">${Icon('eye-off', { size: 'xs' })} Some details are hidden</span>` : null;
    return html`<div class="tl-item"><span class=${`ic b-${tint}`}>${Icon(icon, { size: 'sm' })}</span><div class="bd">
      <div class="hd"><b>${person(item.actor) ?? 'The system'}</b> ${sentence(item)}${item.via ? ` via ${item.via}` : ''}<span class="tm">${formatDateTime(item.at).slice(11)}</span></div>${body(item)}${hidden}</div></div>`;
  };
  return [
    InfoNote(model.journal?.tamper_evident ? 'Append-only history, chained so tampering shows. Entries are never edited or removed.' : 'Append-only history. Entries are never edited or removed.', 'lock'),
    canNote ? html`<div class="composer"><textarea aria-label="Note" placeholder="Write a note" prop:value=${text} on:input=${(event: Event) => text.set((event.target as HTMLTextAreaElement).value)}
      on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); event.stopPropagation(); void addNote(); } }}></textarea>
      <div class="cf"><span class="grow"></span>${Button({ label: 'Save note', variant: 'primary', size: 'small', disabled: () => !text.get().trim(), onClick: () => void addNote() })}</div></div>` : null,
    live(() => problem.get() ? html`<p class="muted">${problem.get()}</p>` : page.get() === null ? html`<p class="muted">Loading…</p>`
      : !page.get()!.items.length ? EmptyState({ icon: 'activity', tint: 'slate', title: 'No activity yet', text: 'Changes, moves and notes for this record land here.' })
      : html`<div class="timeline">${(() => {
        let last = '';
        return page.get()!.items.flatMap(item => { const label = day(item.at); const head = label !== last ? html`<div class="tl-day">${label}</div>` : null; last = label; return [head, entry(item)]; });
      })()}</div>`),
  ];
}

