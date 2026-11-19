// Model scripts (model/script.ts): the hooks a Python model names, run in the browser over the record store.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClient } from '../api';
import { cell, scope } from '../reactive';
import { RecordStore } from '../model/record';
import { evlet, loadScript, lookOf, MAX_DEPTH, scriptControl, scriptTip, ScriptHost, setScriptImporter, type ScriptPage } from '../model/script';
import type { AppSchema, FieldSchema } from '../model/schema';

const field = (name: string, extra: Partial<FieldSchema> = {}): FieldSchema => ({ name, type: 'text', label: name, required: false, read_only: false, ...extra });
const schema = {
  version: 1, ui_version: 1, models: [
    { name: 'quote', label: 'Quote', plural: 'Quotes', fields: [
      field('customer', { script: { on_change: 'customer_changed', tooltip: 'customer_tip', look: 'customer_look' } }),
      field('notes'), field('kept', { read_only: true }), field('total', { compute: { op: 'field', name: 'qty' } } as unknown as Partial<FieldSchema>),
      field('state_note', { versioned: false } as Partial<FieldSchema>),
      ...Array.from({ length: 10 }, (_, at) => field(`c${at}`, { script: { on_change: 'chain' } })),
      field('ping', { script: { on_change: 'ping' } }), field('pong', { script: { on_change: 'pong' } }),
      field('broken', { script: { on_change: 'broken' } }), field('missing', { script: { on_change: 'not_there', control: 'draw' } }),
    ], children: [{ name: 'lines', model: 'quote_line', label: 'Lines', link: 'quote' }] },
    { name: 'quote_line', label: 'Line', plural: 'Lines', fields: [field('qty', { type: 'decimal', script: { on_change: 'qty_changed' } })] },
  ],
} as unknown as AppSchema;

const calls: Array<[string, ...unknown[]]> = [];
class Quote extends evlet.Model {
  customer_changed(item: any, row: unknown) { calls.push(['customer_changed', item.name, item.value, row]); this.field('notes').set(`for ${item.value}`); item.set(`${item.value}!`); }
  customer_tip(item: any) { return { title: 'Customer', lines: [`now ${item.value ?? '-'}`] }; }
  customer_look(item: any) { return item.value === 'vip' ? { tone: 'success' } : null; }
  qty_changed(item: any, row: any) { calls.push(['qty_changed', item.name, item.value, row.index]); }
  chain(item: any) { const at = Number(item.name.slice(1)); calls.push(['chain', at]); if (at < 9) this.field(`c${at + 1}`).set('x'); }
  ping() { calls.push(['ping']); this.field('pong').set(String(Math.random())); }
  pong() { calls.push(['pong']); this.field('ping').set(String(Math.random())); }
  broken() { throw new Error('boom'); }
  draw(el: HTMLElement) { el.textContent = 'drawn'; return () => calls.push(['cleanup']); }
  opened() { calls.push(['opened', this.model, this.is_new]); }
}

const stops: Array<() => void> = [];
afterEach(() => { stops.splice(0).forEach(stop => stop()); calls.length = 0; vi.restoreAllMocks(); });

async function open(locked = false) {
  const store = new RecordStore({ api: createClient({ fetch: async () => new Response('{}') }), schema, model: 'quote' });
  await store.create();
  const page: ScriptPage = { api: store.api, locked: cell(locked), call: async () => true, move: async () => true, save: async () => true,
    reload: async () => {}, toast: () => {}, confirm: async () => true, open: () => {} };
  const host = new ScriptHost(store, page);
  host.start(Quote as any, 'opened');
  stops.push(() => { host.dispose(); store.dispose(); });
  return { store, host };
}

describe('model scripts', () => {
  it('runs on_open once, and on_change once with the field (and the row on a Table row)', async () => {
    const { store } = await open();
    expect(calls).toEqual([['opened', 'quote', true]]);
    calls.length = 0;
    store.field('customer').set('acme');
    // The hook's own change to customer does not call it again; its change to notes went through as typed.
    expect(calls).toEqual([['customer_changed', 'customer', 'acme', undefined]]);
    expect(store.field('customer').value.peek()).toBe('acme!');
    expect(store.field('notes').value.peek()).toBe('for acme');
    const row = store.table('lines').add({});
    calls.length = 0;
    row.field('qty').set('2');
    expect(calls).toEqual([['qty_changed', 'qty', '2', 0]]);
  });

  it('set() refuses a field that is not editable, with the reason', async () => {
    const { host, store } = await open();
    const view = (name: string) => host.fieldView(store.field(name));
    expect(() => view('kept').set('x')).toThrow('quote.kept cannot be set: it is read only');
    expect(() => view('total').set('x')).toThrow('it is calculated');
    expect(view('kept').editable).toBe(false);
    view('notes').set('ok');
    expect(store.field('notes').value.peek()).toBe('ok');
    const locked = await open(true);
    expect(() => locked.host.fieldView(locked.store.field('notes')).set('x')).toThrow('it is locked');
    // versioned=False fields change on a locked record, as on the form.
    locked.host.fieldView(locked.store.field('state_note')).set('paid');
    expect(() => locked.host.tableView(locked.store.table('lines')).add({})).toThrow('cannot change now');
  });

  it('caps nested hooks and never re-enters one, warning once', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { store } = await open();
    calls.length = 0;
    store.field('c0').set('x');
    expect(calls.map(call => call[1])).toEqual(Array.from({ length: MAX_DEPTH }, (_, at) => at));
    store.field('ping').set('go');
    // ping sets pong, pong sets ping: ping is still running, so it is not called again.
    expect(calls.slice(MAX_DEPTH)).toEqual([['ping'], ['pong']]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('a missing method warns once and a throwing hook is logged; the form keeps working', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { store } = await open();
    store.field('missing').set('a');
    store.field('missing').set('b');
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain('quote script has no method not_there');
    store.field('broken').set('x');
    expect(error.mock.calls[0][0]).toBe('quote script: broken failed');
    expect(store.field('broken').value.peek()).toBe('x');
    store.field('notes').set('still editable');
    expect(store.field('notes').value.peek()).toBe('still editable');
  });

  it('tooltip and look run as derives over what they read', async () => {
    const { store } = await open();
    const customer = store.field('customer');
    expect(scriptTip(customer)!()).toBe('Customer\nnow -');
    const look = lookOf(customer);
    expect(look()).toBe(null);
    customer.set('vip');
    expect(scriptTip(customer)!()).toBe('Customer\nnow vip!');
    expect(lookOf({ meta: field('x', { look: 'muted' }) })()).toBe('muted');
  });

  it('draws controls in a shadow root and runs their cleanup', async () => {
    const { store } = await open();
    let element!: HTMLElement;
    const stop = scope(() => { element = scriptControl(store.field('missing'))!; });
    expect(element.shadowRoot!.textContent).toBe('drawn');
    stop();
    expect(calls).toContainEqual(['cleanup']);
  });

  it('goes inert after the record closes: late calls are ignored', async () => {
    const { store, host } = await open();
    const view = host.fieldView(store.field('notes'));
    host.dispose();
    view.set('late');
    expect(store.field('notes').value.peek()).toBe(null);
    expect(view.value).toBe(undefined);
    await expect(host.apiView.get('quote', 'x')).resolves.toBe(null);
  });

  it('runs an extension\'s hooks after the model\'s, and no script re-runs for a change another made', async () => {
    const { store } = await open();
    class Acme extends evlet.Model {
      check(item: any) { calls.push(['acme', item.value]); item.set('blocked'); }
      line(item: any, row: any) { calls.push(['acme line', item.value, row.index]); }
      opened() { calls.push(['acme opened']); }
    }
    const extension = new ScriptHost(store, { api: store.api, locked: cell(false) } as unknown as ScriptPage,
      { url: '/assets/scripts/x/acme.js', on_change: { customer: 'check', 'lines.qty': 'line' } });
    extension.start(Acme as any, 'opened');
    stops.push(() => extension.dispose());
    expect(calls.pop()).toEqual(['acme opened']);
    calls.length = 0;
    store.field('customer').set('acme');
    // The model's hook first; each script's change to customer is the other's to ignore, so nothing loops.
    expect(calls).toEqual([['customer_changed', 'customer', 'acme', undefined], ['acme', 'acme!']]);
    expect(store.field('customer').value.peek()).toBe('blocked');
    calls.length = 0;
    store.table('lines').add({}).field('qty').set('3');
    expect(calls).toEqual([['qty_changed', 'qty', '3', 0], ['acme line', '3', 0]]);
  });

  it('gives each record or prompt that names one file its own instance: they share only code', async () => {
    const first = await open(), second = await open();
    calls.length = 0;
    first.store.field('customer').set('a');
    expect(calls).toEqual([['customer_changed', 'customer', 'a', undefined]]);
    expect(second.store.field('notes').value.peek()).toBe(null);
    expect(first.store.field('notes').value.peek()).toBe('for a');
  });

  it('loads a script once per address, after setting the evlet global', async () => {
    const load = vi.fn(async () => ({ default: Quote }));
    setScriptImporter(load);
    expect(await loadScript('/assets/scripts/abc/quote.js')).toBe(Quote);
    await loadScript('/assets/scripts/abc/quote.js');
    expect(load).toHaveBeenCalledTimes(1);
    expect((globalThis as any).evlet).toBe(evlet);
    expect(Object.isFrozen(evlet)).toBe(true);
    setScriptImporter(async () => ({ default: class {} }));
    await expect(loadScript('/x.js')).rejects.toThrow('extends evlet.Model');
  });
});
