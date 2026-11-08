// Keeping unsaved changes when someone leaves a record (docs/ui.md, Unsaved changes). Leaving never
// saves the record: a saved record's edits go to the person's own server entry
// (PUT /api/_unsaved/<model>/<id>), and an unsaved new record stays in this browser only, so nothing
// is created and no number is used until Save.
//
// The browser store is `evlet.unsaved.<instance>.<user>` in localStorage, one entry per model. Every
// access is guarded: without storage the edit stays only in the open tab.

import { ApiError } from '../api';
import { onCleanup } from '../reactive';
import type { RecordStore } from '../model/record';
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
  return model.autosave !== false && ctx.me.peek()?.prefs.keep_unsaved !== false;
}

const timeout = <T>(promise: Promise<T>, ms: number) => new Promise<T>((resolve, reject) => {
  const timer = setTimeout(() => reject(new ApiError('The server did not answer in time.', 0, 'network')), ms);
  promise.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
});

/** Keep the draft's changes. Resolves null when they are kept (or there were none), or why not. */
export async function keepChanges(ctx: AppContext, model: ScreenModel, store: RecordStore, isNew: boolean): Promise<string | null> {
  let changes: NodeChanges;
  try { changes = isNew ? captureNew(store.root) : capture(store.root); } catch { return 'This record is still loading.'; }
  if (isEmpty(changes)) return null;
  const info = unsavedInfo(ctx), limits = info.limits;
  if (isNew) return ctx.unsaved.put(model.name, changes, limits);
  if (sizeOf(changes) > limits.entry_kb * 1024) return tooLarge(limits.entry_kb);
  const revision = store.etag.peek(), id = store.id;
  if (!revision || !id) return 'This record has no saved version to compare with.';
  try {
    await timeout(ctx.api.keepUnsaved(model.name, id, changes, revision), WAIT_MS);
    // Records opened from now on look for kept changes (only people with entries ask the server).
    const me = ctx.me.peek();
    if (me) me.unsaved = { ...info, count: info.count + 1 };
    return null;
  } catch (error) {
    return error instanceof ApiError ? error.message : 'The server did not keep them.';
  }
}

/**
 * Keep the draft when the tab is hidden, without asking or showing anything: the person may
 * close the tab next. Removed with the page.
 */
export function keepWhenHidden(ctx: AppContext, model: ScreenModel, store: RecordStore, isNew: boolean, dirty: () => boolean) {
  if (typeof document === 'undefined') return;
  const hidden = () => {
    if (document.visibilityState !== 'hidden' || !keepsUnsaved(ctx, model) || !dirty()) return;
    void keepChanges(ctx, model, store, isNew);
  };
  document.addEventListener('visibilitychange', hidden);
  onCleanup(() => document.removeEventListener('visibilitychange', hidden));
}

/** A key for the browser store: the tenant, or the host this instance is served on. */
export function instanceKey(session: { tenant?: unknown } | null): string {
  const tenant = session?.tenant as { slug?: unknown } | undefined;
  if (tenant && typeof tenant.slug === 'string' && tenant.slug) return tenant.slug;
  return typeof location === 'undefined' ? 'local' : location.host || 'local';
}
