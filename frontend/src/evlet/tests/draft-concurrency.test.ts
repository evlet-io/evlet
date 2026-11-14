import { afterEach, expect, it, vi } from 'vitest';
import { AppClient } from '../app/client';
import { acceptDraftVersion, keepChanges, keepWhenHidden, KEEP_PAUSE_MS } from '../app/unsaved';
import { RecordStore } from '../model/record';
import { cell, scope } from '../reactive';
import { id, responses, server, type Recorded, type Sent } from './server';
import recorded from './unsaved-cas-fixture.json';

const stops: Array<() => void> = [];
afterEach(() => { stops.splice(0).forEach(stop => stop()); vi.useRealTimers(); });
const path = /\/api\/_unsaved\/customer\/0000000000001$/;
const refused = (): Recorded => ({ status: 412, etag: null, body: { error: 'The kept draft changed' } });
async function setup(existing = false) {
  let version = 0, durable = existing ? 'Other tab' : '', first: (() => void) | null = null, postpone = false;
  const commit = (request: Sent): Recorded => {
    if (request.headers['if-match'] !== `"draft${version}"`) return refused();
    durable = request.method === 'DELETE' ? '' : request.body.changes.values.title; version++;
    return { status: request.method === 'DELETE' ? 204 : 200, etag: `"draft${version}"`, body: request.method === 'DELETE' ? null : { saved_at: 1, size: 40, dropped: [] } };
  };
  const stub = server([
    ['GET', path, () => ({ status: 200, etag: `"draft${version}"`, body: { entry: durable ? { changes: { values: { title: durable } }, base_revision: '"record0"', saved_at: 1, size: 40, stale: false } : null, dropped: [] } })],
    ['PUT', path, request => postpone && !first ? new Promise(resolve => { first = () => resolve(commit(request)); }) : commit(request)],
    ['DELETE', path, commit],
  ]);
  const api = new AppClient({ fetch: stub.fetch }), ctx: any = { api, me: cell(null) };
  const store = new RecordStore({ api, schema: responses.schema.body as any, model: 'customer' });
  store.applyServer({ id: id(1), title: 'Original', rate: '1.00', credit_days: 30 }, '"record0"');
  await store.loadAll(); stops.push(() => store.dispose());
  return { api, ctx, store, sent: stub.sent, durable: () => durable, release: () => first!(), postpone: () => { postpone = true; }, replace: (title: string) => { durable = title; version++; } };
}

it('keeps the newest acknowledged draft when an aborted older write reaches the server last', async () => {
  vi.useFakeTimers();
  const test = await setup(); test.postpone();
  let keeper!: ReturnType<typeof keepWhenHidden>;
  stops.push(scope(() => { keeper = keepWhenHidden(test.ctx, test.store.meta as any, test.store, false, () => test.store.dirty.peek()); }));
  test.store.field('title').set('Older draft'); await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS + 5000);
  expect(keeper.state.peek().kind).toBe('failed');
  test.store.field('title').set('Newest draft'); await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS);
  expect(keeper.state.peek().kind).toBe('kept'); expect(test.durable()).toBe('Newest draft');
  test.release(); await vi.advanceTimersByTimeAsync(0);
  expect(test.durable()).toBe('Newest draft'); expect(keeper.state.peek().kind).toBe('kept');
  expect(test.sent.filter(request => request.method === 'GET')).toHaveLength(1);
  expect(test.sent.filter(request => request.method === 'PUT').map(request => request.headers['if-match'])).toEqual(['"draft0"', '"draft0"']);
});

it('retains the expected version after a lost response and never refreshes it to overwrite another copy', async () => {
  vi.useFakeTimers();
  const test = await setup(); test.postpone(); test.store.field('title').set('Earlier');
  const pending = keepChanges(test.ctx, test.store.meta as any, test.store, false);
  await vi.advanceTimersByTimeAsync(5000); expect(await pending).toContain('did not answer');
  test.release(); await vi.advanceTimersByTimeAsync(0); expect(test.durable()).toBe('Earlier');
  test.store.field('title').set('Still open');
  expect(await keepChanges(test.ctx, test.store.meta as any, test.store, false)).toContain('Another tab or an earlier request');
  expect(await keepChanges(test.ctx, test.store.meta as any, test.store, false)).toContain('Another tab or an earlier request');
  expect(test.durable()).toBe('Earlier'); expect(test.store.field('title').value.peek()).toBe('Still open');
  expect(test.sent.filter(request => request.method === 'GET')).toHaveLength(1);
});

it('requires acknowledging an existing kept draft before overwriting it', async () => {
  const test = await setup(true); test.store.field('title').set('My draft');
  expect(await keepChanges(test.ctx, test.store.meta as any, test.store, false)).toContain('Restore or discard');
  expect(test.sent.filter(request => request.method === 'PUT')).toHaveLength(0);
  acceptDraftVersion(test.store, '"draft0"');
  expect(await keepChanges(test.ctx, test.store.meta as any, test.store, false)).toBeNull();
  expect(test.durable()).toBe('My draft');
});

it('uses the returned version for late-save cleanup and preserves a newer copy', async () => {
  const test = await setup(); test.postpone(); test.store.field('title').set('Saved now');
  const pending = keepChanges(test.ctx, test.store.meta as any, test.store, false);
  await vi.waitFor(() => expect(test.sent.filter(request => request.method === 'PUT')).toHaveLength(1));
  test.store.applyServer({ id: id(1), title: 'Saved now', rate: '1.00', credit_days: 30 }, '"record1"'); await test.store.loadAll();
  test.release(); test.replace('Newer other-tab draft');
  expect(await pending).toBeNull(); expect(test.durable()).toBe('Newer other-tab draft');
  expect(test.sent.find(request => request.method === 'DELETE')?.headers['if-match']).toBe('"draft1"');
});

it('reads a new empty draft version after an intentional normal save', async () => {
  const test = await setup(); test.store.field('title').set('First');
  expect(await keepChanges(test.ctx, test.store.meta as any, test.store, false)).toBeNull();
  test.store.applyServer({ id: id(1), title: 'First', rate: '1.00', credit_days: 30 }, '"record1"'); await test.store.loadAll();
  test.replace(''); test.store.field('title').set('Next edit');
  expect(await keepChanges(test.ctx, test.store.meta as any, test.store, false)).toBeNull(); expect(test.durable()).toBe('Next edit');
  expect(test.sent.filter(request => request.method === 'GET')).toHaveLength(2);
});

// Headers and bodies recorded through the real SQLite/ASGI draft routes.
it('uses the real draft version contract for reads, conditional writes and deletes', async () => {
  const stub = server([['GET', path, recorded.empty], ['PUT', path, recorded.kept], ['DELETE', path, recorded.deleted]]);
  const api = new AppClient({ fetch: stub.fetch });
  const empty = await api.unsaved('customer', id(1)); expect(empty).toEqual({ ...recorded.empty.body, etag: recorded.empty.etag });
  const kept = await api.keepUnsaved('customer', id(1), recorded.read_kept.body.entry.changes, recorded.read_kept.body.entry.base_revision, undefined, empty.etag!);
  expect(kept).toEqual({ ...recorded.kept.body, etag: recorded.kept.etag });
  expect(await api.discardUnsaved('customer', id(1), kept.etag!)).toBe(recorded.deleted.etag);
  expect(stub.sent[1].headers['if-match']).toBe(recorded.empty.etag);
  expect(stub.sent[2].headers['if-match']).toBe(recorded.kept.etag);
});

it('preserves unobserved kept drafts during record Save and sends only the acknowledged draft version', async () => {
  const detail = { ...responses.customer, status: 200, etag: '"record-saved"' };
  const stub = server([['PATCH', /\/api\/customer\/0000000000001$/, detail]]);
  const api = new AppClient({ fetch: stub.fetch });
  const store = new RecordStore({ api, schema: responses.schema.body as any, model: 'customer' });
  stops.push(() => store.dispose());
  store.applyServer(detail.body, '"record0"'); await store.loadAll();
  store.field('title').set('First save'); expect(await store.save()).toBe(true);
  expect(stub.sent[0].headers['x-evlet-draft-revision']).toBe('preserve');
  expect(stub.sent[0].headers['if-match']).toBe('"record0"');
  acceptDraftVersion(store, recorded.kept.etag);
  store.field('title').set('Second save'); expect(await store.save()).toBe(true);
  expect(stub.sent[1].headers['x-evlet-draft-revision']).toBe(recorded.kept.etag);
  expect(stub.sent[1].headers['if-match']).toBe('"record-saved"');
  await api.update('customer', id(1), { title: 'Legacy API caller' }, '"record-saved"');
  expect(stub.sent[2].headers['x-evlet-draft-revision']).toBeUndefined();
});
