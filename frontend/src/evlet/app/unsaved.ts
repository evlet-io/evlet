// Keeping unsaved changes when someone leaves a record (docs/ui.md, Unsaved changes). Leaving never
// saves the record: a saved record's edits go to the person's own server entry
// (PUT /api/_unsaved/<model>/<id>), and an unsaved new record stays in this browser only, so nothing
// is created and no number is used until Save.
//
// The browser store is `evlet.unsaved.<instance>.<user>` in localStorage, one entry per model. Every
// access is guarded: without storage the edit stays only in the open tab.

import { ApiError } from '../api';
import { cell, effect, onCleanup } from '../reactive';
import type { RecordStore, Row } from '../model/record';
import type { CreateAttempt } from '../model/create-attempt';
import { captureNew, capture, isEmpty, sizeOf, type NodeChanges } from '../model/unsaved';
import type { UnsavedInfo, UnsavedLimits } from './client';
import type { AppContext } from './context';
import type { ScreenModel } from './meta';
import { safeStorage } from './prefs';

export const DEFAULT_LIMITS: UnsavedLimits = { server_days: 30, server_entries: 200, browser_days: 7, browser_entries: 20, entry_kb: 256 };
export const KEPT = 'Unsaved changes kept, restore when you return';
const DAY = 86400;
// How long leaving waits for the server before it asks instead.
const WAIT_MS = 5000;

const tooLarge = (kb: number) => `These unsaved changes are larger than ${kb} KB, so they are not kept. They stay in this tab until you save or close it.`;
const now = () => Math.floor(Date.now() / 1000);

export interface LocalEntry { changes: NodeChanges; saved_at: number; createAttempt?: CreateAttempt }
interface ReceiptEntry { model: string; entry: LocalEntry; written_at: number }
const RECEIPT_FULL = 'Pending create receipts fill this browser\'s draft limit. Resolve or explicitly discard one before keeping another draft.';
const RECEIPT_PENDING = 'A kept draft has a create awaiting confirmation. Restore it or check the record list before choosing Start fresh.';

/** Unsaved new records in this browser, for one instance and user. */
export class LocalUnsaved {
  private readonly prefix: string;
  private readonly key: string;
  private readonly receiptRoot: string;
  private readonly receiptPrefix: string;
  private lastRead: Record<string, LocalEntry> = {};

  constructor(readonly instance: string, readonly user: string, private readonly storage: Storage | null = safeStorage(), private readonly principal?: number) {
    this.prefix = `evlet.unsaved.${instance}.`;
    this.key = this.prefix + user;
    // JSON delimiters keep dotted usernames/instance names distinct from each other and from legacy keys.
    this.receiptRoot = `evlet.create-receipt.v1:${JSON.stringify(instance)}:`;
    this.receiptPrefix = this.receiptRoot + JSON.stringify(user) + ':';
  }

  /** Whether entries can be kept here at all. */
  get available() {
    if (!this.storage) return false;
    try { const probe = this.prefix + '_probe'; this.storage.setItem(probe, '1'); this.storage.removeItem(probe); return true; } catch { return false; }
  }

  private read(): Record<string, LocalEntry> {
    try {
      const value = JSON.parse(this.storage?.getItem(this.key) ?? '{}');
      this.lastRead = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch { /* retain knowledge of a previously read draft when storage becomes blocked */ }
    return { ...this.lastRead };
  }

  private write(entries: Record<string, LocalEntry>): boolean {
    if (!this.storage) return false;
    try {
      if (Object.keys(entries).length) this.storage.setItem(this.key, JSON.stringify(entries));
      else this.storage.removeItem(this.key);
      this.lastRead = { ...entries };
      return true;
    } catch { return false; }
  }

  private receiptKey(model: string, attempt: CreateAttempt) {
    return this.receiptPrefix + JSON.stringify([attempt.principal, attempt.endpoint, model, attempt.key]);
  }

  /** Each attempt owns a separate key, so stale writes to the shared legacy map cannot erase it. */
  private receipts(limits: UnsavedLimits, expired: boolean | null = false): Array<[string, ReceiptEntry]> {
    if (!this.storage) return [];
    const found: Array<[string, ReceiptEntry]> = [], cutoff = now() - limits.browser_days * DAY;
    try {
      const keys: string[] = [];
      for (let index = 0; index < this.storage.length; index++) {
        const key = this.storage.key(index);
        if (key?.startsWith(this.receiptPrefix)) keys.push(key);
      }
      for (const key of keys) {
        let value: ReceiptEntry;
        try { value = JSON.parse(this.storage.getItem(key) ?? 'null'); } catch { continue; }
        const attempt = value?.entry?.createAttempt;
        if (!attempt || typeof value.model !== 'string' || typeof value.entry.saved_at !== 'number' ||
          !value.entry.changes || key !== this.receiptKey(value.model, attempt) ||
          (this.principal !== undefined && attempt.principal !== this.principal)) continue;
        if (expired === null || (expired ? value.entry.saved_at < cutoff : value.entry.saved_at >= cutoff)) found.push([key, value]);
      }
    } catch { /* storage became unavailable; existing keys are never deleted as a fallback */ }
    return found.sort((a, b) => b[1].written_at - a[1].written_at || a[0].localeCompare(b[0]));
  }

  private rememberReceipt(model: string, entry: LocalEntry, limits: UnsavedLimits): string | null {
    if (!this.storage) return 'This browser does not allow keeping create receipts.';
    const attempt = entry.createAttempt!, key = this.receiptKey(model, attempt);
    const text = JSON.stringify({ model, entry, written_at: Date.now() });
    if (new TextEncoder().encode(key + text).length > limits.entry_kb * 1024) return tooLarge(limits.entry_kb);
    try {
      for (const [old] of this.receipts(limits, true)) this.storage.removeItem(old);
      const previous = this.storage.getItem(key);
      if (previous !== null) {
        const held = (JSON.parse(previous) as ReceiptEntry).entry.createAttempt!;
        if (held.body !== attempt.body || held.startedAt !== attempt.startedAt) return RECEIPT_PENDING;
      } else if (this.receipts(limits).length >= limits.browser_entries) return RECEIPT_FULL;
      this.storage.setItem(key, text);
      // Another tab may already have discovered this published receipt. Retain it if
      // concurrent inserts exceed the soft cap; refuse this dispatch and further growth.
      if (previous === null && this.receipts(limits).length > limits.browser_entries) {
        return RECEIPT_FULL;
      }
      if (this.storage.getItem(key) !== text) return 'The browser could not verify this create receipt. Keep this tab open.';
      return null;
    } catch { return 'This browser has no room to keep the create receipt.'; }
  }

  hasAttempt(model: string, key: string, limits: UnsavedLimits = DEFAULT_LIMITS): boolean {
    return this.receipts(limits).some(([, value]) => value.model === model && value.entry.createAttempt?.key === key);
  }

  get(model: string, limits: UnsavedLimits = DEFAULT_LIMITS): LocalEntry | null {
    const entry = this.read()[model];
    const pending = this.receipts(limits).filter(([, value]) => value.model === model);
    if (pending.length) return pending[0][1].entry;
    if (!entry || typeof entry.saved_at !== 'number' || !entry.changes) return null;
    // Only ordinary legacy drafts are authoritative here. Attempted entries are indexes;
    // an orphan cannot recreate an acknowledged receipt or alias another ledger namespace.
    if (entry.createAttempt) return null;
    return entry.saved_at < now() - limits.browser_days * DAY ? null : entry;
  }

  /** Keep a new record's changes. A pending create belongs to its receipt until explicit discard. */
  put(model: string, changes: NodeChanges, limits: UnsavedLimits = DEFAULT_LIMITS, createAttempt?: CreateAttempt | null): string | null {
    const entry: LocalEntry = { changes, saved_at: now(), ...(createAttempt ? { createAttempt } : {}) };
    if (new TextEncoder().encode(JSON.stringify(entry)).length > limits.entry_kb * 1024) return tooLarge(limits.entry_kb);
    if (!this.storage) return 'This browser does not allow keeping them, so they stay in this tab until you save or close it.';
    const entries = this.trimmed(this.read(), limits);
    const receipts = this.receipts(limits), pending = receipts.filter(([, value]) => value.model === model);
    if (pending.length && (!createAttempt || !pending.some(([, value]) => value.entry.createAttempt?.key === createAttempt.key))) return RECEIPT_PENDING;
    if (createAttempt) {
      const failed = this.rememberReceipt(model, entry, limits);
      if (failed) return failed;
    } else if (!entries[model] && receipts.length >= limits.browser_entries) return RECEIPT_FULL;
    entries[model] = entry;
    const trimmed = this.trimmed(entries, limits);
    if (trimmed[model] !== entry && !createAttempt) return RECEIPT_FULL;
    const written = this.write(trimmed);
    // A durable receipt includes the full draft and remains recoverable without the shared map.
    return written || createAttempt ? null : 'This browser has no room to keep them, so they stay in this tab until you save or close it.';
  }

  /** A key removes only that receipt; null only an ordinary draft; omitted is explicit unconditional discard. */
  remove(model: string, expectedAttemptKey?: string | null): boolean {
    const entries = this.read();
    const pending = this.receipts(DEFAULT_LIMITS, null).filter(([, value]) => value.model === model);
    const matching = pending.filter(([, value]) => expectedAttemptKey === undefined || value.entry.createAttempt?.key === expectedAttemptKey);
    const attempt = entries[model]?.createAttempt;
    if (expectedAttemptKey === null && (attempt || pending.length)) return false;
    if (typeof expectedAttemptKey === 'string' && !matching.length && attempt?.key !== expectedAttemptKey)
      return !entries[model] && !pending.length;
    try { for (const [key] of matching) this.storage?.removeItem(key); } catch { return false; }
    if (expectedAttemptKey === undefined || expectedAttemptKey === null || attempt?.key === expectedAttemptKey) {
      delete entries[model];
      return this.write(entries);
    }
    return true;
  }

  /** A refused first attempt rolled back; do not change a newer draft's receipt. */
  releaseAttempt(model: string, key: string): boolean {
    const entries = this.read(), entry = entries[model];
    const matching = this.receipts(DEFAULT_LIMITS, null).filter(([, value]) => value.model === model && value.entry.createAttempt?.key === key);
    if (entry?.createAttempt?.key === key || (!entry && matching.length)) {
      const { createAttempt, ...kept } = entry ?? matching[0][1].entry;
      entries[model] = kept;
      if (!this.write(entries)) return false;
    }
    try { for (const [name] of matching) this.storage?.removeItem(name); return true; } catch { return false; }
  }

  /** Honor retention for whole drafts; do not evict an unexpired receipt for another model. */
  purge(limits: UnsavedLimits = DEFAULT_LIMITS) {
    try { for (const [key] of this.receipts(limits, true)) this.storage?.removeItem(key); } catch { /* keep existing entries if blocked */ }
    this.write(this.trimmed(this.read(), limits));
  }

  private trimmed(entries: Record<string, LocalEntry>, limits: UnsavedLimits) {
    const cutoff = now() - limits.browser_days * DAY;
    const receipts = this.receipts(limits);
    const valid = Object.entries(entries).filter(([model, entry]) => entry && typeof entry.saved_at === 'number' && entry.saved_at >= cutoff &&
      !(entry.createAttempt && !receipts.some(([, value]) => value.model === model && value.entry.createAttempt?.key === entry.createAttempt?.key)));
    const pending = valid.filter(([, entry]) => entry.createAttempt);
    const kept = valid.filter(([, entry]) => !entry.createAttempt)
      .sort((a, b) => b[1].saved_at - a[1].saved_at).slice(0, Math.max(0, limits.browser_entries - receipts.length));
    // Lowering the cap cannot erase existing uncertainty; put refuses further receipt growth.
    return Object.fromEntries([...pending, ...kept]);
  }

  /** On sign-in: when another person signed in on this browser last, their entries go. */
  signedIn() {
    if (!this.storage) return;
    try {
      const last = this.storage.getItem(this.prefix + '_user');
      if (last !== null && last !== this.user) this.clearInstance();
      this.storage.setItem(this.prefix + '_user', this.user);
    } catch { /* blocked */ }
  }

  /** On sign-out: every entry of this instance goes. */
  clearInstance() {
    if (!this.storage) return;
    try {
      const keys: string[] = [];
      for (let index = 0; index < this.storage.length; index++) {
        const key = this.storage.key(index);
        if ((key?.startsWith(this.prefix) && key !== this.prefix + '_user') || key?.startsWith(this.receiptRoot)) keys.push(key);
      }
      keys.forEach(key => this.storage!.removeItem(key));
    } catch { /* blocked */ }
    this.lastRead = {};
  }
}

/** Keep the receipt before sending. Unsupported/disabled storage permits current-tab creates
 * only when no earlier kept copy would be replaced or discarded without acknowledgement. */
export function keepCreateAttempts(ctx: AppContext, model: ScreenModel, store: RecordStore) {
  store.onCreateAttempt = (attempt, previous) => {
    if (attempt === null) {
      if (previous) ctx.unsaved.releaseAttempt(model.name, previous.key);
      return null;
    }
    const warning = 'Keep this tab open until Create is confirmed. After a lost connection, retry the same values here; reloading cannot safely recover this create.';
    const kept = ctx.unsaved.get(model.name, unsavedInfo(ctx).limits);
    if (kept?.createAttempt && kept.createAttempt.key !== attempt.key && !(previous?.key === attempt.key && ctx.unsaved.hasAttempt(model.name, attempt.key, unsavedInfo(ctx).limits)))
      throw new ApiError('A kept draft has another create awaiting confirmation. Restore that draft or check the record list before starting fresh.', 0, 'response');
    let failure: string | null;
    if (!keepsUnsaved(ctx, model)) failure = 'Browser draft keeping is off.';
    else if (unfinishedStructure(store.root)) failure = 'This draft has row order or sections that cannot be kept.';
    else failure = ctx.unsaved.put(model.name, captureNew(store.root, true), unsavedInfo(ctx).limits, attempt);
    if (failure === null) return null;
    if (failure === RECEIPT_FULL) throw new ApiError(`${failure} Create was not sent.`, 0, 'response');
    if (kept || ctx.unsaved.get(model.name, unsavedInfo(ctx).limits))
      throw new ApiError('This browser could not keep the create receipt. Create was not sent, and your earlier draft is unchanged. Restore it or choose Start fresh before retrying.', 0, 'response');
    return `${failure} ${warning}`;
  };
}

/** The person's limits and entry count, from /api/_settings/me; defaults on a server without them. */
export function unsavedInfo(ctx: Pick<AppContext, 'me'>): UnsavedInfo {
  return ctx.me.peek()?.unsaved ?? { limits: DEFAULT_LIMITS, count: 0, enabled: true };
}

/** Whether leaving this model's records keeps their unsaved changes for this person. */
export function keepsUnsaved(ctx: AppContext, model: ScreenModel): boolean {
  return model.autosave !== false && ctx.me.peek()?.prefs.keep_unsaved !== false && unsavedInfo(ctx).enabled;
}

const timeout = <T>(promise: Promise<T>, ms: number) => new Promise<T>((resolve, reject) => {
  const timer = setTimeout(() => reject(new ApiError('The server did not answer in time.', 0, 'network')), ms);
  promise.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
});

const pendingKeeps = new WeakMap<RecordStore, { key: string; promise: Promise<string | null> }>();
const draftVersions = new WeakMap<RecordStore, { base: string; etag: string }>();
const DRAFT_CHANGED = 'Another tab or an earlier request changed the kept draft. Save your open edits or keep this tab open; this copy will not overwrite it.';
const NO_DRAFT_VERSION = 'The server did not provide a draft version. Save the record or keep this tab open.';

/** Accept a draft the person explicitly restored/discarded, or an observed empty slot. */
export function acceptDraftVersion(store: RecordStore, etag: string | null | undefined) {
  const base = store.etag.peek();
  if (base && etag) { draftVersions.set(store, { base, etag }); store.draftRevision = etag; }
}

/** Discard only the copy this view acknowledged; another tab's later copy must survive. */
export async function discardKeptChanges(ctx: AppContext, model: ScreenModel, store: RecordStore) {
  const known = draftVersions.get(store);
  if (!known || !store.id) return;
  const etag = await ctx.api.discardUnsaved(model.name, store.id, known.etag);
  acceptDraftVersion(store, etag);
}

/** Keep the draft's changes. Resolves null when they are kept (or there were none), or why not. */
export async function keepChanges(ctx: AppContext, model: ScreenModel, store: RecordStore, isNew: boolean): Promise<string | null> {
  let changes: NodeChanges;
  try {
    if (unfinishedStructure(store.root)) return 'Changes to row order or sections cannot be kept as a draft. Save the record or keep this tab open.';
    changes = isNew ? captureNew(store.root, store.createAttempt !== null) : capture(store.root); } catch { return 'This record is still loading.'; }
  if (isEmpty(changes)) return null;
  const info = unsavedInfo(ctx), limits = info.limits;
  if (isNew) return ctx.unsaved.put(model.name, changes, limits, store.createAttempt);
  if (sizeOf(changes) > limits.entry_kb * 1024) return tooLarge(limits.entry_kb);
  const revision = store.etag.peek(), id = store.id;
  if (!revision || !id) return 'This record has no saved version to compare with.';
  const key = `${id}:${store.editVersion.peek()}:${revision}`;
  const previous = pendingKeeps.get(store);
  if (previous) return previous.key === key ? previous.promise : previous.promise.then(() => keepChanges(ctx, model, store, isNew));
  const request = new AbortController();
  const promise = (async () => { try {
    const result = await timeout((async () => {
      let known = draftVersions.get(store);
      if (!known || known.base !== revision) {
        const read = await ctx.api.unsaved(model.name, id, request.signal);
        if (request.signal.aborted) throw new ApiError('The request was cancelled.', 0, 'aborted');
        if (read.entry) throw new Error('A kept draft already exists. Restore or discard that copy before keeping these edits.');
        if (read.dropped === 'record') throw new Error('You can no longer keep changes to this record.');
        if (!read.etag) throw new Error(NO_DRAFT_VERSION);
        known = { base: revision, etag: read.etag };
        draftVersions.set(store, known);
        store.draftRevision = known.etag;
      }
      return ctx.api.keepUnsaved(model.name, id, changes, revision, request.signal, known.etag);
    })(), WAIT_MS);
    if (!result.etag) return NO_DRAFT_VERSION;
    // Only an acknowledged write advances this token. A timed-out request can still commit at
    // the server; a retry keeps the old token, so it cannot overwrite a later acknowledged copy.
    draftVersions.set(store, { base: revision, etag: result.etag });
    store.draftRevision = result.etag;
    if (store.etag.peek() !== revision && !store.dirty.peek()) {
      try { await timeout(ctx.api.discardUnsaved(model.name, id, result.etag), WAIT_MS); }
      catch (error) { if (!(error instanceof ApiError && error.status === 412)) throw error; }
      return null;
    }
    if (result.dropped.length) return 'Some fields can no longer be kept with your current permissions. Keep this tab open and review the record.';
    const me = ctx.me.peek();
    if (me) me.unsaved = { ...info, count: Math.max(1, info.count) };
    return null;
  } catch (error) {
    return error instanceof ApiError && error.status === 412 ? DRAFT_CHANGED : error instanceof Error ? error.message : 'The server did not keep them.';
  } finally { request.abort(); } })();
  pendingKeeps.set(store, { key, promise });
  try { return await promise; } finally { if (pendingKeeps.get(store)?.promise === promise) pendingKeeps.delete(store); }
}

/** The existing draft transport does not represent row order or section edits. Never label a
 * partial capture as a durable copy of those changes. The normal record Save preserves them. */
export function unfinishedStructure(row: Row): boolean {
  for (const table of row.tables.values()) {
    const rows = table.rows.peek(), kept = rows.filter(item => !item.isNew), before = table.saved.filter(item => kept.includes(item));
    const firstNew = rows.findIndex(item => item.isNew);
    if (kept.some((item, index) => item !== before[index]) || (firstNew !== -1 && rows.slice(firstNew).some(item => !item.isNew)) ||
      (row.isNew ? table.sections.peek().length > 0 : JSON.stringify(table.sections.peek()) !== JSON.stringify(table.savedSections)) ||
      table.removedSections.peek().length || rows.some(unfinishedStructure)) return true;
  }
  return false;
}

export interface DraftState { kind: 'idle' | 'pending' | 'keeping' | 'kept' | 'failed'; text: string }
export const KEEP_PAUSE_MS = 1500;

/** Keep committed edits after a pause and on tab hide. The status never promises offline recovery
 * for server drafts, and an unfinished text field is explicitly not covered by the last copy. */
export function keepWhenHidden(ctx: AppContext, model: ScreenModel, store: RecordStore, isNew: boolean, dirty: () => boolean) {
  const state = cell<DraftState>({ kind: 'idle', text: '' });
  let timer: ReturnType<typeof setTimeout> | null = null, alive = true, running = false, again = false;
  let keptVersion = -1, watched: HTMLElement | null = null;
  const pendingText = new Set<HTMLInputElement | HTMLTextAreaElement>();
  const typing = () => {
    for (const input of pendingText) if (!watched?.contains(input)) pendingText.delete(input);
    return pendingText.size > 0;
  };
  const cancelTimer = () => { if (timer !== null) clearTimeout(timer); timer = null; };
  const run = async () => {
    cancelTimer();
    if (!alive || !keepsUnsaved(ctx, model) || !dirty() || store.saving.peek()) return;
    if (typing()) { state.set({ kind: 'pending', text: 'Latest text is not kept yet. Finish the field to keep it.' }); return; }
    if (running) { again = true; return; }
    const version = store.editVersion.peek();
    if (version === keptVersion) return;
    running = true;
    state.set({ kind: 'keeping', text: 'Keeping draft…' });
    const failure = await keepChanges(ctx, model, store, isNew);
    running = false;
    if (!alive) return;
    if (!keepsUnsaved(ctx, model)) { state.set({ kind: 'idle', text: '' }); return; }
    if (version === store.editVersion.peek() && !typing() && dirty()) {
      if (failure) state.set({ kind: 'failed', text: `Draft not kept. ${failure}` });
      else { keptVersion = version; state.set({ kind: 'kept', text: isNew ? 'Draft kept on this browser. Not created.' : 'Draft kept on the server. Not saved to the record.' }); }
    }
    if (again || version !== store.editVersion.peek()) { again = false; schedule(); }
  };
  const schedule = () => {
    cancelTimer();
    if (!alive || !keepsUnsaved(ctx, model) || !dirty() || store.saving.peek()) { state.set({ kind: 'idle', text: '' }); return; }
    if (store.editVersion.peek() === keptVersion && !typing()) return;
    state.set({ kind: 'pending', text: typing() ? 'Latest text is not kept yet. Finish the field to keep it.' : 'Draft not kept yet.' });
    timer = setTimeout(() => { timer = null; void run(); }, KEEP_PAUSE_MS);
  };
  effect(() => {
    store.editVersion.get();
    store.dirty.get(); store.saving.get(); ctx.me.get(); schedule();
  });
  const hidden = () => { if (document.visibilityState === 'hidden') void run(); };
  document.addEventListener('visibilitychange', hidden);
  onCleanup(() => { alive = false; cancelTimer(); document.removeEventListener('visibilitychange', hidden); });
  return { state, retry: run, watch(frame: HTMLElement) {
    // Activity composers and search boxes do not describe this record's fields.
    const fieldInput = (event: Event) => event.target instanceof HTMLElement && Boolean(event.target.closest('.fld,.ff,.grid,.doc-title')) &&
      (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement);
    watched = frame;
    const input = (event: Event) => { if (fieldInput(event)) { pendingText.add(event.target as HTMLInputElement); schedule(); } };
    const committed = (event: Event) => {
      if (!fieldInput(event)) return;
      const target = event.target as HTMLInputElement | HTMLTextAreaElement;
      if (target.getAttribute('aria-invalid') === 'true') pendingText.add(target);
      else pendingText.delete(target);
      schedule();
    };
    const entered = (event: KeyboardEvent) => {
      if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey && !event.altKey && event.target instanceof HTMLInputElement) committed(event);
    };
    frame.addEventListener('input', input); frame.addEventListener('change', committed); frame.addEventListener('keydown', entered);
    onCleanup(() => { pendingText.clear(); frame.removeEventListener('input', input); frame.removeEventListener('change', committed); frame.removeEventListener('keydown', entered); });
  } };
}

/** A key for the browser store: the tenant, or the host this instance is served on. */
export function instanceKey(session: { tenant?: unknown } | null): string {
  const tenant = session?.tenant as { slug?: unknown } | undefined;
  if (tenant && typeof tenant.slug === 'string' && tenant.slug) return tenant.slug;
  return typeof location === 'undefined' ? 'local' : location.host || 'local';
}
