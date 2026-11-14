import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClient } from '../api';
import { cell, scope } from '../reactive';
import { RecordStore } from '../model/record';
import { canonicalCreate, CREATE_RETRY_MS, prepareCreate } from '../model/create-attempt';
import { apply } from '../model/unsaved';
import { DEFAULT_LIMITS, keepChanges, keepCreateAttempts, LocalUnsaved } from '../app/unsaved';
import { UnsavedBar } from '../app/unsaved-bar';
import { byText, click, stopAll } from './screens-helpers';
import { copy, id, responses } from './server';

const stops: Array<() => void> = [];
afterEach(() => { stops.splice(0).forEach(stop => stop()); stopAll(); localStorage.clear(); vi.restoreAllMocks(); });
const user = { id: 7, username: 'editor', role: 'editor' };

function fixture(options: { storage?: Storage | null; keep?: boolean; model?: string; schema?: any; user?: typeof user; apiBase?: string } = {}) {
  const sent: Array<{ key: string; body: any; init: RequestInit }> = [];
  const records = new Map<string, any>();
  let lose = true, status = 201, pending: (() => Promise<void>) | null = null;
  const fetcher = vi.fn(async (_url: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = new Headers(init.headers), key = headers.get('Idempotency-Key')!;
    const body = JSON.parse(String(init.body));
    sent.push({ key, body, init });
    if (pending) await pending();
    if (status === 422) return new Response(JSON.stringify({ error: 'Invalid title', fields: { title: 'Change it' } }), { status });
    if (!records.has(key)) records.set(key, { ...body, id: id(records.size + 1) });
    if (lose) { lose = false; throw new TypeError('response lost after commit'); }
    return new Response(JSON.stringify(records.get(key)), { status: 201, headers: { etag: '"saved"' } });
  });
  const api = createClient({ fetch: fetcher as typeof fetch, base: options.apiBase });
  api.csrf = 'csrf-test';
  const schema = options.schema ?? copy(responses.schema.body);
  const model = options.model ?? 'customer';
  const store = new RecordStore({ api, schema, model, user: options.user ?? user });
  stops.push(() => store.dispose());
  const unsaved = new LocalUnsaved('receipt-test', (options.user ?? user).username, options.storage === undefined ? localStorage : options.storage);
  const ctx: any = { api, unsaved, me: cell({ prefs: { keep_unsaved: options.keep ?? true }, unsaved: { enabled: true, count: 0, limits: DEFAULT_LIMITS } }) };
  keepCreateAttempts(ctx, store.meta as any, store);
  return { store, api, ctx, sent, records, fetcher, next: (values: { lose?: boolean; status?: number; pending?: (() => Promise<void>) | null }) => {
    if (values.lose !== undefined) lose = values.lose;
    if (values.status !== undefined) status = values.status;
    if ('pending' in values) pending = values.pending!;
  } };
}

describe('create receipt recovery', () => {
  it('persists before sending, never automatically retries, and recovers one committed record after a lost response', async () => {
    const f = fixture();
    await f.store.create({ title: 'One record', credit_days: 30 });
    f.next({ pending: async () => {
      expect(f.ctx.unsaved.get('customer').createAttempt.key).toBe(f.sent.at(-1)!.key);
    } });
    expect(await f.store.save()).toBe(false);
    expect(f.sent).toHaveLength(1); expect(f.records.size).toBe(1);
    expect(await f.store.save()).toBe(true);
    expect(f.sent).toHaveLength(2); expect(f.sent[1].key).toBe(f.sent[0].key);
    expect(f.sent[1].body).toEqual(f.sent[0].body); expect(f.records.size).toBe(1);
    expect(f.sent[0].init.credentials).toBe('same-origin');
    expect(new Headers(f.sent[0].init.headers).get('X-CSRF-Token')).toBe('csrf-test');
    expect(f.store.lastSavedId).toBe(id(1));
  });

  it('restores an attempted browser draft through the actual Restore button with defaults/prefills and its original key', async () => {
    const f = fixture();
    await f.store.create({ title: 'Initial prefill', credit_days: 30 });
    expect(await f.store.save()).toBe(false);
    const old = f.sent[0]; f.store.dispose();
    const restored = new RecordStore({ api: f.api, schema: copy(responses.schema.body), model: 'customer', user });
    stops.push(() => restored.dispose());
    await restored.create({ credit_days: 90 });
    keepCreateAttempts(f.ctx, restored.meta as any, restored);
    let bar!: Node;
    stops.push(scope(() => { bar = UnsavedBar(f.ctx, restored.meta as any, restored, { isNew: true }); }));
    document.body.append(bar);
    click(byText(document, 'button', 'Restore'));
    expect(restored.field('title').value.peek()).toBe('Initial prefill');
    expect(restored.field('credit_days').value.peek()).toBe(30);
    expect(await restored.save()).toBe(true);
    expect(f.sent[1].key).toBe(old.key); expect(f.sent[1].body).toEqual(old.body);
    expect(f.records.size).toBe(1);
  });

  it('blocks changed ambiguous payloads without making another request or dropping the receipt', async () => {
    const f = fixture(); await f.store.create({ title: 'Sent' });
    expect(await f.store.save()).toBe(false);
    const attempt = f.store.createAttempt;
    f.store.field('title').set('Changed');
    expect(await f.store.save()).toBe(false);
    expect(f.store.formError.peek()).toContain('different values');
    expect(f.store.createAttempt).toBe(attempt); expect(f.sent).toHaveLength(1);
    await keepChanges(f.ctx, f.store.meta as any, f.store, true);
    expect(f.ctx.unsaved.get('customer').createAttempt.key).toBe(attempt!.key);
    f.store.field('title').set('Sent');
    expect(await f.store.save()).toBe(true); expect(f.records.size).toBe(1);
  });

  it('refuses expiry and account/server changes instead of allocating a fresh key', async () => {
    const f = fixture(); await f.store.create({ title: 'Sent' }); await f.store.save();
    const attempt = f.store.createAttempt!;
    vi.spyOn(Date, 'now').mockReturnValue(attempt.startedAt + CREATE_RETRY_MS);
    expect(await f.store.save()).toBe(false); expect(f.store.formError.peek()).toContain('retry period');
    expect(f.sent).toHaveLength(1); expect(f.store.createAttempt!.key).toBe(attempt.key);
    vi.restoreAllMocks();
    for (const values of [{ user: { ...user, id: 8 } }, { apiBase: 'https://different.example' }]) {
      const other = fixture(values); await other.store.create({ title: 'Sent' }); other.store.createAttempt = attempt;
      expect(await other.store.save()).toBe(false); expect(other.store.formError.peek()).toContain('another account or server');
      expect(other.sent).toHaveLength(0);
    }
  });

  it('releases a definitive first422 but never uses a retry422 to erase an earlier uncertain outcome', async () => {
    const f = fixture(); f.next({ status: 422 }); await f.store.create({ title: 'Bad' });
    expect(await f.store.save()).toBe(false); expect(f.store.createAttempt).toBeNull();
    expect(f.ctx.unsaved.get('customer').createAttempt).toBeUndefined();
    f.store.field('title').set('Good'); f.next({ status: 201, lose: true });
    expect(await f.store.save()).toBe(false); expect(f.sent[1].key).not.toBe(f.sent[0].key);
    const uncertain = f.store.createAttempt;
    f.next({ status: 422 }); expect(await f.store.save()).toBe(false);
    expect(f.store.createAttempt).toBe(uncertain);
    f.store.field('title').set('Changed again'); expect(await f.store.save()).toBe(false);
    expect(f.sent).toHaveLength(3);
  });

  it('preserves client row keys in a restored attempted draft, including nested rows', async () => {
    const schema = copy(responses.schema.body);
    schema.models.find((model: any) => model.name === 'order').calculation = null;
    const f = fixture({ model: 'order', schema });
    await f.store.create({ title: 'Rows', children: { lines: [{ _key: 'line-a', qty: '1', rate: '2', children: { parts: [{ _key: 'part-a', hours: '3' }] } }] } });
    await f.store.save();
    const kept = f.ctx.unsaved.get('order');
    const restored = new RecordStore({ api: f.api, schema, model: 'order', user });
    stops.push(() => restored.dispose()); await restored.create();
    restored.createAttempt = kept.createAttempt; apply(restored.root, kept.changes);
    expect(restored.table('lines').rows.peek()[0].key).toBe('line-a');
    expect(restored.table('lines').rows.peek()[0].table('parts').rows.peek()[0].key).toBe('part-a');
    // Refuse at the server so this test can inspect the outgoing restored row keys without
    // substituting a fake child-id mapping for the real server's response contract.
    f.next({ status: 422 }); await restored.save();
    expect(f.sent).toHaveLength(2); expect(f.sent[1].key).toBe(f.sent[0].key); expect(f.sent[1].body).toEqual(f.sent[0].body);
  });

  it.each([{ storage: null }, { keep: false }])('permits current-tab retries when browser keeping is unavailable or off: %j', async options => {
    const f = fixture(options); await f.store.create({ title: 'Private current tab' });
    expect(await f.store.save()).toBe(false);
    expect(f.store.createNotice.peek()).toContain('Keep this tab open');
    expect(localStorage.getItem('evlet.unsaved.receipt-test.editor')).toBeNull();
    expect(await f.store.save()).toBe(true); expect(f.sent[1].key).toBe(f.sent[0].key);
  });

  it('does not send or discard an older kept copy when its receipt cannot be kept', async () => {
    const f = fixture(); f.ctx.unsaved.put('customer', { values: { title: 'Older copy' } });
    expect(f.ctx.unsaved.get('customer')).not.toBeNull();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    const remove = vi.spyOn(Storage.prototype, 'removeItem');
    await f.store.create({ title: 'New values' });
    expect(await f.store.save()).toBe(false); expect(f.sent).toHaveLength(0);
    expect(f.store.formError.peek()).toContain('earlier draft is unchanged'); expect(f.store.createAttempt).toBeNull();
    expect(remove).not.toHaveBeenCalled();
    expect(f.ctx.unsaved.get('customer').changes.values.title).toBe('Older copy');
  });

  it('leaves an older kept draft intact when keeping is switched off, requiring explicit Start fresh', async () => {
    const f = fixture({ keep: false });
    f.ctx.unsaved.put('customer', { values: { title: 'Earlier work' } });
    await f.store.create({ title: 'New work' });
    expect(await f.store.save()).toBe(false); expect(f.sent).toHaveLength(0);
    expect(f.store.formError.peek()).toContain('Restore it or choose Start fresh');
    expect(f.ctx.unsaved.get('customer').changes.values.title).toBe('Earlier work');
    expect(f.ctx.unsaved.remove('customer')).toBe(true); // explicit Start fresh
    expect(await f.store.save()).toBe(false); expect(f.sent).toHaveLength(1); // response intentionally lost
    expect(f.store.createNotice.peek()).toContain('Keep this tab open');
  });

  it('does not overwrite a different pending create receipt with a new request key', async () => {
    const original = fixture(); await original.store.create({ title: 'Original' }); await original.store.save();
    const other = fixture(); await other.store.create({ title: 'Another view' });
    expect(await other.store.save()).toBe(false); expect(other.sent).toHaveLength(0);
    expect(other.store.formError.peek()).toContain('another create awaiting confirmation');
    expect(other.ctx.unsaved.get('customer').createAttempt.key).toBe(original.store.createAttempt!.key);
  });

  it('protects a pending receipt from ordinary background keeping and from a different supplied key', async () => {
    const original = fixture(); await original.store.create({ title: 'Awaiting confirmation' }); await original.store.save();
    const kept = original.ctx.unsaved.get('customer');
    const other = fixture(); await other.store.create(); other.store.field('title').set('Other draft');
    expect(await keepChanges(other.ctx, other.store.meta as any, other.store, true)).toContain('create awaiting confirmation');
    const different = prepareCreate({ title: 'Other draft' }, null, user.id, kept.createAttempt.endpoint);
    expect(other.ctx.unsaved.put('customer', { values: { title: 'Other draft' } }, DEFAULT_LIMITS, different)).toContain('create awaiting confirmation');
    expect(other.ctx.unsaved.get('customer')).toEqual(kept);
    expect(other.ctx.unsaved.remove('customer', null)).toBe(false); // a stale ordinary-draft view cannot discard this receipt
    expect(other.ctx.unsaved.get('customer')).toEqual(kept);
    expect(other.ctx.unsaved.remove('customer')).toBe(true); // explicit Start fresh remains available
    expect(await keepChanges(other.ctx, other.store.meta as any, other.store, true)).toBeNull();
    expect(other.ctx.unsaved.get('customer').changes.values.title).toBe('Other draft');
    expect(other.ctx.unsaved.remove('customer', null)).toBe(true);
    expect(other.ctx.unsaved.get('customer')).toBeNull();
  });

  it('protects pending receipts from cap eviction while retaining the configured whole-draft expiry', async () => {
    const f = fixture(); const limits = { ...DEFAULT_LIMITS, browser_entries: 1, browser_days: 7 };
    f.ctx.me.set({ prefs: { keep_unsaved: true }, unsaved: { enabled: true, count: 0, limits } });
    await f.store.create({ title: 'Uncertain' }); await f.store.save();
    const kept = f.ctx.unsaved.get('customer', limits);
    expect(f.ctx.unsaved.put('order', { values: { title: 'Another model' } }, limits)).toContain('draft limit');
    expect(f.ctx.unsaved.get('customer', limits)).toEqual(kept);
    expect(f.ctx.unsaved.get('order', limits)).toBeNull();
    const clock = vi.spyOn(Date, 'now').mockReturnValue(kept.createAttempt.startedAt + 2 * 86400000);
    f.ctx.unsaved.purge(limits);
    expect(f.ctx.unsaved.get('customer', limits)).toEqual(kept);
    expect(await f.store.save()).toBe(false); expect(f.store.formError.peek()).toContain('safe retry period');
    expect(f.sent).toHaveLength(1);
    clock.mockReturnValue(kept.createAttempt.startedAt + 8 * 86400000);
    f.ctx.unsaved.purge(limits);
    expect(f.ctx.unsaved.get('customer', limits)).toBeNull();
    expect(localStorage.getItem('evlet.unsaved.receipt-test.editor')).toBeNull();
  });

  it('preserves old receipts when the browser entry limit is lowered while refusing further growth', () => {
    const local = new LocalUnsaved('limit', 'editor'), limits = { ...DEFAULT_LIMITS, browser_entries: 2 };
    for (const model of ['customer', 'order']) {
      const body = { title: model }, attempt = prepareCreate(body, null, user.id, `/api/${model}`);
      expect(local.put(model, { values: body }, limits, attempt)).toBeNull();
    }
    const reduced = { ...limits, browser_entries: 1 };
    local.purge(reduced);
    expect(local.get('customer', reduced)?.createAttempt).toBeTruthy();
    expect(local.get('order', reduced)?.createAttempt).toBeTruthy();
    expect(local.put('product', { values: { title: 'New' } }, reduced, prepareCreate({ title: 'New' }, null, user.id, '/api/product'))).toContain('draft limit');
    expect(Object.keys(JSON.parse(localStorage.getItem('evlet.unsaved.limit.editor')!))).toHaveLength(2);
  });

  it('recovers both receipts after two tabs interleave writes to the shared ordinary-draft map', () => {
    const a = new LocalUnsaved('race', 'editor', localStorage, user.id), b = new LocalUnsaved('race', 'editor', localStorage, user.id);
    const first = prepareCreate({ title: 'A' }, null, user.id, '/api/customer'), second = prepareCreate({ title: 'B' }, null, user.id, '/api/order');
    const read = Storage.prototype.getItem; let interleave = true;
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function(this: Storage, key: string) {
      const value = read.call(this, key);
      if (key === 'evlet.unsaved.race.editor' && interleave) {
        interleave = false;
        expect(b.put('order', { values: { title: 'B' } }, DEFAULT_LIMITS, second)).toBeNull();
      }
      return value;
    });
    expect(a.put('customer', { values: { title: 'A' } }, DEFAULT_LIMITS, first)).toBeNull();
    expect(JSON.parse(localStorage.getItem('evlet.unsaved.race.editor')!).order).toBeUndefined();
    const reopened = new LocalUnsaved('race', 'editor', localStorage, user.id);
    expect(reopened.get('customer')?.createAttempt?.key).toBe(first.key);
    expect(reopened.get('order')?.createAttempt?.key).toBe(second.key);
  });

  it('exact acknowledgement cannot erase a sibling receipt inserted during ordinary-map cleanup', () => {
    const a = new LocalUnsaved('cleanup-race', 'editor'), b = new LocalUnsaved('cleanup-race', 'editor');
    const first = prepareCreate({ title: 'A' }, null, user.id, '/api/customer'), second = prepareCreate({ title: 'B' }, null, user.id, '/api/order');
    expect(a.put('customer', { values: { title: 'A' } }, DEFAULT_LIMITS, first)).toBeNull();
    const read = Storage.prototype.getItem; let interleave = true;
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function(this: Storage, key: string) {
      const value = read.call(this, key);
      if (key === 'evlet.unsaved.cleanup-race.editor' && interleave) {
        interleave = false;
        expect(b.put('order', { values: { title: 'B' } }, DEFAULT_LIMITS, second)).toBeNull();
      }
      return value;
    });
    expect(a.remove('customer', first.key)).toBe(true);
    expect(a.get('customer')).toBeNull(); expect(a.get('order')?.createAttempt?.key).toBe(second.key);
  });

  it('retains published receipts when tabs overlap at capacity while refusing this dispatch and further growth', async () => {
    const f = fixture(), limits = { ...DEFAULT_LIMITS, browser_entries: 1 };
    f.ctx.me.set({ prefs: { keep_unsaved: true }, unsaved: { enabled: true, count: 0, limits } });
    const sibling = new LocalUnsaved('receipt-test', user.username);
    const second = prepareCreate({ title: 'Other tab' }, null, user.id, '/api/order');
    const write = Storage.prototype.setItem; let interleave = true;
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function(this: Storage, key: string, value: string) {
      if (key.startsWith('evlet.create-receipt.v1:') && !key.includes(second.key) && interleave) {
        interleave = false;
        expect(sibling.put('order', { values: { title: 'Other tab' } }, limits, second)).toBeNull();
      }
      write.call(this, key, value);
    });
    await f.store.create({ title: 'This tab' });
    expect(await f.store.save()).toBe(false); expect(f.sent).toHaveLength(0);
    expect(f.store.formError.peek()).toContain('Create was not sent');
    expect(f.ctx.unsaved.get('customer', limits)?.createAttempt).toBeTruthy(); expect(sibling.get('order', limits)?.createAttempt?.key).toBe(second.key);
    expect([...Array(localStorage.length)].map((_, index) => localStorage.key(index)).filter(key => key?.startsWith('evlet.create-receipt.v1:'))).toHaveLength(2);
    expect(sibling.put('product', { values: { title: 'Further growth' } }, limits, prepareCreate({ title: 'Further growth' }, null, user.id, '/api/product'))).toContain('draft limit');
  });

  it('keeps ledger namespaces separate for dotted usernames and account identities, and clears them on sign-out', () => {
    const a = new LocalUnsaved('tenant.one', 'editor', localStorage, 7), b = new LocalUnsaved('tenant', 'one.editor', localStorage, 7);
    const attempt = prepareCreate({ title: 'Private' }, null, 7, '/api/customer');
    expect(a.put('customer', { values: { title: 'Private' } }, DEFAULT_LIMITS, attempt)).toBeNull();
    expect(b.get('customer')).toBeNull();
    expect(new LocalUnsaved('tenant.one', 'editor', localStorage, 8).get('customer')).toBeNull();
    localStorage.removeItem('evlet.unsaved.tenant.one.editor');
    expect(a.get('customer')?.createAttempt?.key).toBe(attempt.key);
    a.clearInstance(); expect(a.get('customer')).toBeNull();
    expect([...Array(localStorage.length)].map((_, index) => localStorage.key(index)).filter(key => key?.startsWith('evlet.create-receipt.v1:'))).toHaveLength(0);
  });

  it('never resurrects an acknowledged receipt from a stale shared-map index and still reads ordinary legacy drafts', () => {
    const local = new LocalUnsaved('stale-index', 'editor');
    const attempt = prepareCreate({ title: 'Created' }, null, user.id, '/api/customer');
    expect(local.put('customer', { values: { title: 'Created' } }, DEFAULT_LIMITS, attempt)).toBeNull();
    const stale = localStorage.getItem('evlet.unsaved.stale-index.editor')!;
    expect(local.remove('customer', attempt.key)).toBe(true);
    localStorage.setItem('evlet.unsaved.stale-index.editor', stale);
    expect(local.get('customer')).toBeNull();
    expect(local.put('order', { values: { title: 'Ordinary legacy-compatible draft' } })).toBeNull();
    expect(local.get('customer')).toBeNull(); expect(local.get('order')?.changes.values?.title).toBe('Ordinary legacy-compatible draft');
    expect([...Array(localStorage.length)].map((_, index) => localStorage.key(index)).filter(key => key?.startsWith('evlet.create-receipt.v1:'))).toHaveLength(0);
  });

  it.each(['receipt', 'draft'])('removes only its acknowledged receipt and preserves a newer %s after Start fresh', async kind => {
    const first = fixture(); await first.store.create({ title: 'First pending create' });
    let finish!: () => void; first.next({ lose: false, pending: () => new Promise(resolve => { finish = resolve; }) });
    const saving = first.store.save(), firstKey = first.store.createAttempt!.key;
    expect(first.ctx.unsaved.remove('customer')).toBe(true); // another view explicitly chooses Start fresh
    const later = fixture(); await later.store.create(); later.store.field('title').set('Later draft');
    if (kind === 'receipt') await later.store.save(); else await keepChanges(later.ctx, later.store.meta as any, later.store, true);
    const newer = later.ctx.unsaved.get('customer');
    finish(); expect(await saving).toBe(true);
    expect(first.store.lastCreatedKey).toBe(firstKey);
    expect(first.ctx.unsaved.remove('customer', first.store.lastCreatedKey!)).toBe(false);
    expect(later.ctx.unsaved.get('customer')).toEqual(newer);
    if (kind === 'receipt') {
      expect(later.ctx.unsaved.remove('customer', newer.createAttempt.key)).toBe(true);
      expect(later.ctx.unsaved.get('customer')).toBeNull();
    }
  });

  it('counts receipt payload toward the storage size cap and does not claim structural drafts are recoverable', async () => {
    const f = fixture();
    const body = { title: 'x'.repeat(700) }, attempt = prepareCreate(body, null, 7, 'https://example/api/customer');
    expect(f.ctx.unsaved.put('customer', { values: body }, { ...DEFAULT_LIMITS, entry_kb: 1 }, attempt)).toContain('larger than');
    expect(f.ctx.unsaved.get('customer')).toBeNull();
    const schema = copy(responses.schema.body); schema.models.find((model: any) => model.name === 'order').calculation = null;
    const rows = fixture({ model: 'order', schema }); await rows.store.create({ title: 'Sectioned' });
    rows.store.table('lines').sections.set([{ key: 'section-a', id: null, label: 'Parts', before: null }]);
    expect(await rows.store.save()).toBe(false); expect(rows.sent).toHaveLength(1);
    expect(rows.store.createNotice.peek()).toContain('row order or sections');
    expect(rows.ctx.unsaved.get('order')).toBeNull();
  });

  it('retains receipt state across pending view disposal and still records the late acknowledged id', async () => {
    const f = fixture(); await f.store.create({ title: 'Closing' });
    let finish!: () => void; f.next({ lose: false, pending: () => new Promise(resolve => { finish = resolve; }) });
    const saving = f.store.save();
    expect(() => f.store.field('title').set('While pending')).toThrow('being saved');
    const kept = f.ctx.unsaved.get('customer'); expect(kept.createAttempt).toBeTruthy();
    f.store.dispose(); finish(); expect(await saving).toBe(true);
    expect(f.store.lastSavedId).toBe(id(1)); expect(f.ctx.unsaved.get('customer').createAttempt.key).toBe(kept.createAttempt.key);
  });

  it('compares canonical wire values including calculation metadata while retaining row order', () => {
    const body = { z: undefined, title: 'Unicode \u00e9', children: { lines: [{ _key: 'a', qty: '1' }, { _key: 'b', qty: '2' }] }, _calculation: { language: 1, model_schema_hash: 'first' } };
    const attempt = prepareCreate(body, null, 7, '/api/order');
    expect(prepareCreate({ _calculation: body._calculation, children: body.children, title: body.title }, attempt, 7, '/api/order')).toBe(attempt);
    expect(() => prepareCreate({ ...body, _calculation: { ...body._calculation, model_schema_hash: 'changed' } }, attempt, 7, '/api/order')).toThrow('different values');
    expect(canonicalCreate(body)).not.toBe(canonicalCreate({ ...body, children: { lines: [...body.children.lines].reverse() } }));
  });
});
