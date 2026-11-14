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

export interface LocalEntry { changes: NodeChanges; saved_at: number }

/** Unsaved new records in this browser, for one instance and user. */
export class LocalUnsaved {
  private readonly prefix: string;
  private readonly key: string;

  constructor(readonly instance: string, readonly user: string, private readonly storage: Storage | null = safeStorage()) {
    this.prefix = `evlet.unsaved.${instance}.`;
    this.key = this.prefix + user;
  }

  /** Whether entries can be kept here at all. */
  get available() {
    if (!this.storage) return false;
    try { const probe = this.prefix + '_probe'; this.storage.setItem(probe, '1'); this.storage.removeItem(probe); return true; } catch { return false; }
  }

  private read(): Record<string, LocalEntry> {
    try {
      const value = JSON.parse(this.storage?.getItem(this.key) ?? '{}');
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch { return {}; }
  }

  private write(entries: Record<string, LocalEntry>): boolean {
    if (!this.storage) return false;
    try {
      if (Object.keys(entries).length) this.storage.setItem(this.key, JSON.stringify(entries));
      else this.storage.removeItem(this.key);
      return true;
    } catch { return false; }
  }

  get(model: string, limits: UnsavedLimits = DEFAULT_LIMITS): LocalEntry | null {
    const entry = this.read()[model];
    if (!entry || typeof entry.saved_at !== 'number' || !entry.changes) return null;
    return entry.saved_at < now() - limits.browser_days * DAY ? null : entry;
  }

  /** Keep a new record's changes; latest wins per model. Returns why it could not, or null. */
  put(model: string, changes: NodeChanges, limits: UnsavedLimits = DEFAULT_LIMITS): string | null {
    if (sizeOf(changes) > limits.entry_kb * 1024) return tooLarge(limits.entry_kb);
    if (!this.storage) return 'This browser does not allow keeping them, so they stay in this tab until you save or close it.';
    const entries = this.read();
    entries[model] = { changes, saved_at: now() };
    return this.write(this.trimmed(entries, limits)) ? null : 'This browser has no room to keep them, so they stay in this tab until you save or close it.';
  }

  remove(model: string) {
    const entries = this.read();
    if (model in entries) { delete entries[model]; this.write(entries); }
  }

  /** Drop expired entries and the oldest over the cap. */
  purge(limits: UnsavedLimits = DEFAULT_LIMITS) { this.write(this.trimmed(this.read(), limits)); }

  private trimmed(entries: Record<string, LocalEntry>, limits: UnsavedLimits) {
    const cutoff = now() - limits.browser_days * DAY;
    const kept = Object.entries(entries).filter(([, entry]) => entry && typeof entry.saved_at === 'number' && entry.saved_at >= cutoff)
      .sort((a, b) => b[1].saved_at - a[1].saved_at).slice(0, limits.browser_entries);
    return Object.fromEntries(kept);
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
        if (key?.startsWith(this.prefix) && key !== this.prefix + '_user') keys.push(key);
      }
      keys.forEach(key => this.storage!.removeItem(key));
    } catch { /* blocked */ }
  }
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

/** Keep the draft's changes. Resolves null when they are kept (or there were none), or why not. */
export async function keepChanges(ctx: AppContext, model: ScreenModel, store: RecordStore, isNew: boolean): Promise<string | null> {
  let changes: NodeChanges;
  try {
    if (unfinishedStructure(store.root)) return 'Changes to row order or sections cannot be kept as a draft. Save the record or keep this tab open.';
    changes = isNew ? captureNew(store.root) : capture(store.root); } catch { return 'This record is still loading.'; }
  if (isEmpty(changes)) return null;
  const info = unsavedInfo(ctx), limits = info.limits;
  if (isNew) return ctx.unsaved.put(model.name, changes, limits);
  if (sizeOf(changes) > limits.entry_kb * 1024) return tooLarge(limits.entry_kb);
  const revision = store.etag.peek(), id = store.id;
  if (!revision || !id) return 'This record has no saved version to compare with.';
  const key = `${store.editVersion.peek()}:${revision}`;
  const previous = pendingKeeps.get(store);
  if (previous) return previous.key === key ? previous.promise : previous.promise.then(() => keepChanges(ctx, model, store, isNew));
  const request = new AbortController();
  const promise = (async () => { try {
    const result = await timeout(ctx.api.keepUnsaved(model.name, id, changes, revision, request.signal), WAIT_MS);
    // A normal Save may have completed while this draft request was running. Do not re-offer that
    // older copy after Save already removed it; newer local edits will schedule their own copy.
    if (store.etag.peek() !== revision && !store.dirty.peek()) { await timeout(ctx.api.discardUnsaved(model.name, id), WAIT_MS); return null; }
    if (result.dropped.length) return 'Some fields can no longer be kept with your current permissions. Keep this tab open and review the record.';
    // Records opened from now on look for kept changes (only people with entries ask the server).
    const me = ctx.me.peek();
    if (me) me.unsaved = { ...info, count: Math.max(1, info.count) };
    return null;
  } catch (error) {
    return error instanceof ApiError ? error.message : 'The server did not keep them.';
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
  let typing = false, keptVersion = -1, observedVersion = store.editVersion.peek();
  const cancelTimer = () => { if (timer !== null) clearTimeout(timer); timer = null; };
  const run = async () => {
    cancelTimer();
    if (!alive || !keepsUnsaved(ctx, model) || !dirty() || store.saving.peek()) return;
    if (typing) { state.set({ kind: 'pending', text: 'Latest text is not kept yet. Finish the field to keep it.' }); return; }
    if (running) { again = true; return; }
    const version = store.editVersion.peek();
    if (version === keptVersion) return;
    running = true;
    state.set({ kind: 'keeping', text: 'Keeping draft…' });
    const failure = await keepChanges(ctx, model, store, isNew);
    running = false;
    if (!alive) return;
    if (!keepsUnsaved(ctx, model)) { state.set({ kind: 'idle', text: '' }); return; }
    if (version === store.editVersion.peek() && !typing && dirty()) {
      if (failure) state.set({ kind: 'failed', text: `Draft not kept. ${failure}` });
      else { keptVersion = version; state.set({ kind: 'kept', text: isNew ? 'Draft kept on this browser. Not created.' : 'Draft kept on the server. Not saved to the record.' }); }
    }
    if (again || version !== store.editVersion.peek()) { again = false; schedule(); }
  };
  const schedule = () => {
    cancelTimer();
    if (!alive || !keepsUnsaved(ctx, model) || !dirty() || store.saving.peek()) { state.set({ kind: 'idle', text: '' }); return; }
    if (store.editVersion.peek() === keptVersion && !typing) return;
    state.set({ kind: 'pending', text: typing ? 'Latest text is not kept yet. Finish the field to keep it.' : 'Draft not kept yet.' });
    timer = setTimeout(() => { timer = null; void run(); }, KEEP_PAUSE_MS);
  };
  effect(() => {
    const version = store.editVersion.get();
    if (version !== observedVersion) { typing = false; observedVersion = version; }
    store.dirty.get(); store.saving.get(); ctx.me.get(); schedule();
  });
  const hidden = () => { if (document.visibilityState === 'hidden') void run(); };
  document.addEventListener('visibilitychange', hidden);
  onCleanup(() => { alive = false; cancelTimer(); document.removeEventListener('visibilitychange', hidden); });
  return { state, retry: run, watch(frame: HTMLElement) {
    // Activity composers and search boxes do not describe this record's fields.
    const fieldInput = (event: Event) => event.target instanceof HTMLElement && Boolean(event.target.closest('.fld,.ff,.grid,.doc-title')) &&
      (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement);
    const input = (event: Event) => { if (fieldInput(event)) { typing = true; schedule(); } };
    const committed = (event: Event) => { if (fieldInput(event)) { typing = (event.target as HTMLElement).getAttribute('aria-invalid') === 'true'; schedule(); } };
    frame.addEventListener('input', input); frame.addEventListener('change', committed);
    onCleanup(() => { frame.removeEventListener('input', input); frame.removeEventListener('change', committed); });
  } };
}

/** A key for the browser store: the tenant, or the host this instance is served on. */
export function instanceKey(session: { tenant?: unknown } | null): string {
  const tenant = session?.tenant as { slug?: unknown } | undefined;
  if (tenant && typeof tenant.slug === 'string' && tenant.slug) return tenant.slug;
  return typeof location === 'undefined' ? 'local' : location.host || 'local';
}
