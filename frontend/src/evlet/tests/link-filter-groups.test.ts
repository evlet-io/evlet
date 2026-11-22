// Link filters as a list of groups, with Refs that read a Table column (unsaved rows included) or read through a
// Link: the picker sends the form values they read, and the server applies the filters (evlet/search.py).
import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import { SchemaIndex, type AppSchema, type FieldSchema, type ModelSchema } from '../model/schema';
import { apiLinks, BaseField, setLinkSource } from '../ui/fields';
import { cleanup, render } from './ui-helpers';
import type { Sent } from './server';

const ref = (path: string) => ({ $expr: 'ref', args: [path] });
const field = (name: string, label: string, target?: string): FieldSchema =>
  ({ name, label, type: target ? 'link' : 'text', required: false, read_only: false, ...(target ? { target } : {}) });
const model = (name: string, fields: FieldSchema[], extra: Partial<ModelSchema> = {}): ModelSchema =>
  ({ name, label: name, plural: name, title_field: 'title', fields, list_fields: ['title'], ...extra });

const schema: AppSchema = { models: [
  model('unit', [field('title', 'Title')]),
  model('item', [field('title', 'Title'), field('unit', 'Unit', 'unit'), field('sale_unit', 'Sell in', 'unit')], {
    children: [{ name: 'packs', model: 'item_packs', label: 'Packs', link: 'evlet_owner' }],
    behavior: { sale_unit: { filters: [{ id: ref('unit') }, { id__in: ref('packs.unit') }] } } }),
  model('item_packs', [field('unit', 'Unit', 'unit')], { child_only: true }),
  model('order', [field('title', 'Title')], { children: [{ name: 'lines', model: 'order_lines', label: 'Lines', link: 'evlet_owner' }] }),
  model('order_lines', [field('item', 'Item', 'item'), field('unit', 'Unit', 'unit')], {
    child_only: true, behavior: { unit: { filters: [{ id: ref('item.unit') }, { id__in: ref('item.packs.unit') }] } } }),
] } as AppSchema;

function backend(name: string) {
  const sent: Sent[] = [];
  const fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    sent.push({ method: init.method ?? 'GET', url: String(input), headers: {}, body: undefined });
    const searching = new URL(String(input), 'http://127.0.0.1').pathname.endsWith('/search');
    const body = searching ? { items: [{ id: 'u-kg', title: 'kg' }] } : { items: [], total: 0, limit: 20, offset: 0 };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  const api = createClient({ fetch: fetch as typeof globalThis.fetch });
  setLinkSource(apiLinks(api, new SchemaIndex(schema)));
  return { store: new RecordStore({ api, schema, model: name }), sent };
}

const requests = (sent: Sent[]) => sent.map(item => decodeURIComponent(item.url)).filter(url => url.includes('filters=') || url.includes('/search'));
const open = async (target: Parameters<typeof BaseField>[0]['field']) => {
  const host = render(() => BaseField({ field: target }));
  host.querySelector<HTMLButtonElement>('.in-link')!.click();
  await new Promise(resolve => setTimeout(resolve, 0));
};

let store: RecordStore | null = null;
afterEach(() => { store?.dispose(); store = null; cleanup(); setLinkSource(null); });

describe('a Link whose filters are groups', () => {
  it('sends a Table column as the values of its rows, unsaved rows included', async () => {
    const made = backend('item');
    store = made.store;
    await store.create({ unit: 'u-kg', children: { packs: [{ unit: 'u-bag' }] } });
    store.table('packs').add({ unit: 'u-box' });
    store.table('packs').add({ unit: 'u-bag' });
    store.table('packs').add({});
    await open(store.field('sale_unit'));
    expect(requests(made.sent).at(-1)).toBe('/api/unit/search?link=item.sale_unit&limit=8&values={"unit":"u-kg","packs.unit":["u-bag","u-box"]}');
    expect([...document.querySelectorAll('[role="option"]')].map(option => option.textContent)).toEqual(['Kkg']);
  });

  it('sends the Link a Ref reads through; the server reads the rest', async () => {
    const made = backend('order');
    store = made.store;
    await store.create({ children: { lines: [{ item: 'i-flour' }] } });
    await open(store.table('lines').rows.peek()[0].field('unit'));
    expect(requests(made.sent).at(-1)).toBe('/api/unit/search?link=order_lines.unit&limit=8&values={"item":"i-flour"}');
  });

  it('asks for the Link first when every group reads it', async () => {
    const made = backend('order');
    store = made.store;
    await store.create({ children: { lines: [{}] } });
    await open(store.table('lines').rows.peek()[0].field('unit'));
    expect(document.querySelector('.link-status')!.textContent).toBe('Choose Item first.');
    expect(requests(made.sent)).toEqual([]);
  });

  it('leaves the check of a chosen value to the server when the Link changes', async () => {
    const made = backend('order');
    store = made.store;
    await store.create({ children: { lines: [{ item: 'i-flour', unit: 'u-kg' }] } });
    const line = store.table('lines').rows.peek()[0];
    line.field('item').set('i-milk');
    await store.idle();
    expect(line.field('unit').value.peek()).toBe('u-kg');
    expect(requests(made.sent)).toEqual([]);
  });
});
