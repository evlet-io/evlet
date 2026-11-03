// Regression tests for review findings on the router, API client, record store and formatting.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConflictError, createClient, ValidationError } from '../api';
import { buildChanges } from '../model/changes';
import { formatDate, formatDateTime, formatMoney, formatNumber } from '../model/format';
import { RecordStore } from '../model/record';
import type { AppSchema } from '../model/schema';
import { createRouter } from '../router';
import { copy, id, responses, server } from './server';

describe('router', () => {
  const routes = { home: '/', record: '/m/:model/:id' } as const;
  const stops: Array<() => void> = [];
  beforeEach(() => history.replaceState(null, '', '/'));
  afterEach(() => stops.splice(0).forEach(stop => stop()));

  it('round-trips dot segments and other awkward params', () => {
    const router = createRouter(routes);
    for (const value of ['..', '.', '...', 'a/b', '%', '%2E', 'ü', ' x ', '+', 'a%2Fb']) {
      const href = router.href('record', { model: value, id: '7' });
      expect(new URL(href, 'http://x').pathname.split('/')).toHaveLength(4);
      expect(router.match(href)?.params).toEqual({ model: value, id: '7' });
    }
  });

  it('keeps guarding the back button after a refusal', async () => {
    const router = createRouter({ list: '/m/:model' });
    stops.push(router.start());
    await router.navigate('/m/a');
    await router.navigate('/m/b');
    let allow = false, asked = 0;
    stops.push(router.guard(() => { asked++; return allow; }));
    const tick = () => new Promise(resolve => setTimeout(resolve, 20));
    history.back(); await tick(); await tick();
    expect(location.pathname).toBe('/m/b');
    allow = true;
    history.back(); await tick(); await tick();
    expect(location.pathname).toBe('/m/a');
    expect(router.route.peek()?.params.model).toBe('a');
    expect(asked).toBe(2);
  });
});

describe('api errors', () => {
  const replying = (status: number, body: string) => createClient({ fetch: (async () => new Response(body, { status })) as any });

  it.each([409, 412, 428])('%i is a ConflictError whatever the body', async status => {
    await expect(replying(status, '<html>Precondition Failed</html>').update('order', id(1), {}, 'x')).rejects.toBeInstanceOf(ConflictError);
    await expect(replying(status, '{"error":"changed"}').update('order', id(1), {}, 'x')).rejects.toBeInstanceOf(ConflictError);
  });

  it('422 is a ValidationError whatever the body, and fields must be an object', async () => {
    await expect(replying(422, 'bad').update('order', id(1), {}, 'x')).rejects.toBeInstanceOf(ValidationError);
    const error = await replying(422, JSON.stringify({ error: 'x', fields: ['a'] })).update('order', id(1), {}, 'x').catch(caught => caught);
    expect(error.fields).toEqual({});
  });

  it('removes its listeners from the page signal after each request', async () => {
    const page = new AbortController();
    const added = vi.spyOn(page.signal, 'addEventListener'), removed = vi.spyOn(page.signal, 'removeEventListener');
    const { fetch } = server([['GET', /order\/0000000000001$/, 'detail']]);
    const api = createClient({ fetch, signal: () => page.signal });
    const own = new AbortController();
    for (let index = 0; index < 5; index++) await api.get('order', id(1), own.signal);
    // Either AbortSignal.any combined them, or every listener added was removed again.
    expect(added.mock.calls.length).toBe(removed.mock.calls.length);
  });
});

const schema = responses.schema.body as AppSchema;
const stores: RecordStore[] = [];
afterEach(() => stores.splice(0).forEach(store => store.dispose()));

function backend(overrides: Array<[string, RegExp, any]> = []) {
  let saved = false;
  const stub = server([
    ...overrides,
    ['GET', /\/api\/order\/0000000000001$/, 'detail'],
    ['GET', /\/api\/order\/0000000000001\/lines\/lines\?limit=100&offset=100$/, () => responses[saved ? 'lines_page_after_update' : 'lines_page']],
    ['GET', /\/api\/customer\/0000000000001$/, { ...responses.customer, status: 200 }],
    ['GET', /\/api\/product\/0000000000001$/, { ...responses.product, status: 200 }],
    ['PATCH', /\/api\/order\/0000000000001$/, () => { saved = true; return responses.update; }],
    ['POST', /\/api\/order\/0000000000001\/actions\/close$/, () => { saved = true; return responses.action; }],
  ]);
  const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema, model: 'order', today: () => '2026-11-01' });
  stores.push(store);
  return { store, sent: stub.sent };
}
const line = (store: RecordStore, n: number) => store.table('lines').rows.peek().find(row => row.id === id(n))!;

describe('record store', () => {
  it('a 409 without a JSON body is a conflict', async () => {
    const { store } = backend([['PATCH', /\/api\/order\/0000000000001$/, { status: 409, body: undefined, etag: null }]]);
    await store.open(id(1));
    store.field('title').set('x');
    expect(await store.save()).toBe(false);
    expect(store.conflict.peek()).toBeInstanceOf(ConflictError);
  });

  it('a failed page load leaves a retryable error, not a record that looks still loading', async () => {
    let fail = true;
    const { store } = backend([['GET', /lines\/lines\?limit=100&offset=100$/, () => fail ? { status: 500, body: { error: 'boom' }, etag: null } : responses.lines_page]]);
    await expect(store.open(id(1))).rejects.toMatchObject({ status: 500 });
    expect(store.ready.peek()).toBe(false);
    expect(store.loadError.peek()).toMatchObject({ retryable: true });
    expect(() => store.field('title').set('x')).toThrow('could not be loaded');
    fail = false;
    await store.loadAll();
    expect(store.loadError.peek()).toBeNull();
    expect(store.ready.peek()).toBe(true);
  });

  it('a save that succeeds keeps succeeding when paging its answer fails', async () => {
    let fail = false;
    const { store } = backend([['GET', /lines\/lines\?limit=100&offset=100$/, () => fail ? { status: 503, body: { error: 'busy' }, etag: null } : responses.lines_page]]);
    await store.open(id(1));
    store.field('title').set('Renamed');
    fail = true;
    expect(await store.save()).toBe(true);
    expect(store.field('title').value.peek()).toBe('Renamed');
    expect(store.loadError.peek()).toMatchObject({ status: 503 });
    expect(store.saving.peek()).toBe(false);
  });

  it('ignores the answer of an older open', async () => {
    let calls = 0;
    const old = copy(responses.detail);
    old.body.title = 'Old';
    const { store } = backend([['GET', /\/api\/order\/0000000000001$/, async () => {
      calls++;
      if (calls === 1) { await new Promise(resolve => setTimeout(resolve, 30)); return old; }
      return responses.detail;
    }]]);
    const first = store.open(id(1), { loadAll: false }), second = store.open(id(1), { loadAll: false });
    await Promise.all([first, second]);
    expect(store.field('title').value.peek()).toBe('Big order');
  });

  it('retries a lookup that failed, and reads lookups again after reload', async () => {
    let available = false;
    const { store, sent } = backend([['GET', /\/api\/customer\/0000000000002$/, () => available
      ? { status: 200, body: { ...responses.customer.body, id: id(2), credit_days: 10 }, etag: null }
      : { status: 503, body: { error: 'busy' }, etag: null }]]);
    await store.open(id(1));
    await store.idle();
    store.field('customer').set(id(2));
    await store.idle();
    expect(store.field('due').result.peek().state).toBe('pending');
    available = true;
    store.field('title').set('Again');
    await store.idle();
    expect(store.field('due').value.peek()).toBe('2026-11-11');
    const before = sent.filter(item => item.url.endsWith('/api/customer/0000000000001')).length;
    await store.reload();
    await store.idle();
    expect(sent.filter(item => item.url.endsWith('/api/customer/0000000000001')).length).toBe(before + 1);
  });

  it('a response without a field does not clear it', async () => {
    const partial = copy(responses.update);
    delete partial.body.title;
    const { store } = backend([['PATCH', /\/api\/order\/0000000000001$/, partial]]);
    await store.open(id(1));
    store.field('remark').set('note');
    expect(await store.save()).toBe(true);
    expect(store.field('title').value.peek()).toBe('Big order');
  });

  it('a new record sends a default the user cleared as null', async () => {
    const { store } = backend();
    await store.create({ title: 'Fresh' });
    store.field('stage').set(null);
    expect(buildChanges(store.root)).toMatchObject({ title: 'Fresh', stage: null });
  });

  it('a digit-only _key never collides with a saved row', async () => {
    const { store } = backend();
    await store.open(id(1));
    const added = store.table('lines').add({ _key: '5', qty: '1', rate: '1' });
    const keys = store.table('lines').rows.peek().map(row => row.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(added.key).not.toMatch(/^\d+$/);
    expect(store.field('line_count').value.peek()).toBe(121);
    expect((buildChanges(store.root) as any).children.lines.create[0]._key).toBe(added.key);
  });

  it('refuses writes to fields that do not apply or are read only', async () => {
    const { store } = backend();
    await store.open(id(1));
    expect(() => store.field('reason').set('why')).toThrow('does not apply');
  });

  it('after an action the saved lock applies', async () => {
    const { store } = backend();
    await store.open(id(1));
    expect(await store.runAction('close')).toBe(true);
    expect(store.field('remark').rules.peek().locked).toBe(true);
    expect(() => store.field('remark').set('x')).toThrow('locked');
  });

  it('new rows nest full children and land errors by key at depth', async () => {
    const { store } = backend();
    await store.open(id(1));
    const added = store.table('lines').add({ qty: '1' });
    const part = added.table('parts').add({ hours: '1' });
    store.showErrors({ [`lines.${id(1)}.parts.${id(2)}.hours`]: 'A', [`lines.${added.key}.qty`]: 'B', [`lines.${added.key}.parts.${part.key}.hours`]: 'C' });
    expect(line(store, 1).table('parts').rows.peek()[1].field('hours').error.peek()).toBe('A');
    expect([added.field('qty').error.peek(), part.field('hours').error.peek(), store.formError.peek()]).toEqual(['B', 'C', null]);
  });
});

describe('formatting', () => {
  it('takes exact decimal strings and safe integers only', () => {
    expect(formatMoney('1234.565')).toBe('AED 1,234.57');
    expect(formatMoney('-0.004')).toBe('AED 0.00');
    expect(formatMoney(12)).toBe('AED 12.00');
    expect(formatMoney(0.1 + 0.2)).toBe('Invalid amount');
    expect(formatMoney(1e21)).toBe('Invalid amount');
    expect(formatMoney('abc')).toBe('Invalid amount');
    expect(formatMoney('1e3')).toBe('Invalid amount');
    expect(formatMoney(' 12 ')).toBe('Invalid amount');
    expect(formatMoney('')).toBe('');
    expect(formatNumber('1,000')).toBe('Invalid amount');
  });

  it('shows only real dates, in GST when a time zone is given', () => {
    expect(formatDate('2026-13-45')).toBe('Invalid date');
    expect(formatDate('2026-02-30')).toBe('Invalid date');
    expect(formatDate('2026-10-31T22:30:00Z')).toBe('01-11-2026');
    expect(formatDate('2026-10-31T22:30:00')).toBe('31-10-2026');
  });

  it('shows timestamps in GST whatever the browser zone; a timestamp without a zone is already GST', () => {
    expect(formatDateTime('2026-10-31T20:05:00Z')).toBe('01-11-2026 00:05');
    expect(formatDateTime('2026-11-01T08:00:00.000000Z')).toBe('01-11-2026 12:00');
    expect(formatDateTime('2026-11-01T10:00:00+04:00')).toBe('01-11-2026 10:00');
    expect(formatDateTime('2026-11-01T10:00:00')).toBe('01-11-2026 10:00');
    expect(formatDateTime('2026-11-31T10:00:00')).toBe('Invalid date');
  });
});
