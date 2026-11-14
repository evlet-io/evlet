import { describe, expect, it } from 'vitest';
import { CellCache } from '../cache';
import { AppClient } from '../app/client';
import { id, server } from './server';

describe('bounded cell cache', () => {
  it('evicts least recently used entries without breaking cells held by views', () => {
    const cache = new CellCache<string | null>(null, 2);
    cache.set('first', 'Before');
    const active = cache.get('first');
    cache.set('second', 'Second'); cache.set('third', 'Third');
    expect((cache as any).retained.size).toBe(2);
    expect((cache as any).retained.has('first')).toBe(false);
    cache.set('first', 'After');
    expect(cache.get('first')).toBe(active);
    expect(active.peek()).toBe('After');
    expect((cache as any).retained.has('second')).toBe(false);
    cache.clear();
    expect(active.peek()).toBeNull();
    expect((cache as any).retained.size).toBe(0);
  });

  it('limits retained data as well as entry count, including a single oversized detail', () => {
    const cache = new CellCache<Record<string, unknown> | null>(null, 100, 2048);
    for (let n = 0; n < 100; n++) cache.set(String(n), { text: 'x'.repeat(200) });
    expect((cache as any).bytes).toBeLessThanOrEqual(2048);
    expect((cache as any).retained.size).toBeLessThan(5);
    const held = cache.get('large');
    cache.set('large', { text: 'x'.repeat(5000) });
    expect(held.peek()?.text).toHaveLength(5000);
    expect((cache as any).retained.has('large')).toBe(false);
  });
});

describe('record detail cache', () => {
  it('bounds retention after browsing many records while preserving an open view cell', async () => {
    const api = new AppClient({ fetch: async input => new Response(JSON.stringify({ id: String(input).split('/').pop(), title: 'Record', notes: 'x'.repeat(10000) }), { status: 200 }) });
    const held = api.detail('customer', id(1));
    for (let n = 1; n <= 400; n++) await api.get('customer', id(n));
    const cache = (api as any).details;
    expect(cache.retained.size).toBeLessThanOrEqual(128);
    expect(cache.bytes).toBeLessThanOrEqual(4 * 1024 * 1024);
    expect(api.detail('customer', id(1))).toBe(held);
    expect(held.peek()?.notes).toHaveLength(10000);
    api.forget();
    expect(held.peek()).toBeNull();
  });

  it('does not let a late read replace a successful write or a forgotten session', async () => {
    let finish!: (reply: any) => void;
    const stub = server([
      ['GET', /\/api\/customer\/0000000000001$/, () => new Promise(resolve => { finish = resolve; })],
      ['PATCH', /\/api\/customer\/0000000000001$/, { status: 200, body: { id: id(1), title: 'New' }, etag: '"2"' }],
    ]);
    const api = new AppClient({ fetch: stub.fetch }), held = api.detail('customer', id(1));
    const reading = api.get('customer', id(1));
    await api.update('customer', id(1), { title: 'New' }, '"1"');
    finish({ status: 200, body: { id: id(1), title: 'Old' }, etag: '"1"' }); await reading;
    expect(held.peek()?.title).toBe('New');
    const oldSession = api.get('customer', id(1));
    api.forget();
    finish({ status: 200, body: { id: id(1), title: 'Old session' }, etag: '"1"' }); await oldSession;
    expect(held.peek()).toBeNull(); expect(api.detail('customer', id(1)).peek()).toBeNull();
  });
});
