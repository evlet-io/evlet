// The Link dropdown over the search route (GET /api/<model>/search): each option's title with its subtext
// below it, the form values the Link reads, and "Create new <Model>" with the quick form.
import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import { SchemaIndex, type AppSchema, type FieldSchema, type ModelSchema } from '../model/schema';
import { quickCreate, quickCreateFields, quickCreateValues } from '../app/quick-create';
import { apiLinks, BaseField, setLinkSource } from '../ui/fields';
import { cleanup, key, render } from './ui-helpers';
import type { Sent } from './server';

const ref = (path: string) => ({ $expr: 'ref', args: [path] });
const field = (name: string, label: string, extra: Partial<FieldSchema> = {}): FieldSchema =>
  ({ name, label, type: 'text', required: false, read_only: false, ...extra });
const model = (name: string, fields: FieldSchema[], extra: Partial<ModelSchema> = {}): ModelSchema =>
  ({ name, label: name[0].toUpperCase() + name.slice(1), plural: name, title_field: 'title', fields, list_fields: ['title'], ...extra });

const schema = (create = true): AppSchema => ({ models: [
  model('group', [field('title', 'Title')]),
  model('warehouse', [field('title', 'Title')]),
  model('item', [field('title', 'Title', { required: true }), field('code', 'Code', { required: true, default_kind: 'static' }),
    field('group', 'Group', { type: 'link', target: 'group' }), field('notes', 'Notes')],
  { permissions: { create, update: true, delete: false } }),
  model('order', [field('title', 'Title'), field('group', 'Group', { type: 'link', target: 'group' }),
    field('warehouse', 'Warehouse', { type: 'link', target: 'warehouse' }),
    field('item', 'Item', { type: 'link', target: 'item', inputs: ['warehouse'] })],
  { behavior: { item: { filters: { group: ref('group') } } } }),
] } as AppSchema);

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });

function backend(create = true) {
  const sent: Sent[] = [];
  const index = new SchemaIndex(schema(create));
  const fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://127.0.0.1');
    const body = init.body === undefined ? undefined : JSON.parse(String(init.body));
    sent.push({ method: init.method ?? 'GET', url: decodeURIComponent(String(input)), headers: {}, body });
    if (url.pathname === '/api/item/search') return json({ items: [
      { id: 'i-box', title: 'Box 40 x 40', subtext: 'In stock: 12 box' }, { id: 'i-tape', title: 'Packing tape' }] });
    if (url.pathname === '/api/item' && init.method === 'POST')
      return json({ id: 'i-new', title: body.title, code: 'C-9', group: body.group ?? null, notes: null }, 201, { etag: '"1"' });
    return json({ items: [], total: 0, limit: 20, offset: 0 });
  };
  const api = createClient({ fetch: fetch as typeof globalThis.fetch });
  const store = new RecordStore({ api, schema: index, model: 'order' });
  return { store, api, index, sent };
}

const options = () => [...document.querySelectorAll('.link-pop [role="option"]')];
const open = async (host: HTMLElement) => {
  host.querySelector<HTMLButtonElement>('.in-link')!.click();
  await new Promise(resolve => setTimeout(resolve, 0));
};

let store: RecordStore | null = null;
afterEach(() => { store?.dispose(); store = null; cleanup(); setLinkSource(null); });

describe('the Link dropdown over the search route', () => {
  it('shows each title with its subtext below, and sends the form values the Link reads', async () => {
    const made = backend();
    store = made.store;
    setLinkSource(apiLinks(made.api, made.index));
    await store.create({ group: 'g-pack', warehouse: 'w-dubai' });
    const host = render(() => BaseField({ field: store!.field('item') }));
    await open(host);
    const first = options()[0];
    expect(first.querySelector('.link-title')!.textContent).toBe('Box 40 x 40');
    expect(first.querySelector('.link-sub')!.textContent).toBe('In stock: 12 box');
    // An option without a subtext shows its title alone.
    expect(options()[1].querySelector('.link-sub')).toBeNull();
    // The filter's value and the option's input, by their form paths.
    expect(made.sent.at(-1)!.url).toBe('/api/item/search?link=order.item&limit=8&values={"warehouse":"w-dubai","group":"g-pack"}');
    // Without a quick form, no "Create new".
    expect(document.querySelector('.link-create')).toBeNull();
    options()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(store.field('item').value.peek()).toBe('i-box');
  });

  it('offers "Create new" last, with the typed text and the filter values, and selects what was created', async () => {
    const made = backend();
    store = made.store;
    const asked: unknown[] = [];
    setLinkSource(apiLinks(made.api, made.index, { quickCreate: async (target, text, values) => { asked.push([target, text, values]); return { id: 'i-new', title: text }; } }));
    await store.create({ group: 'g-pack' });
    const host = render(() => BaseField({ field: store!.field('item') }));
    await open(host);
    const input = document.querySelector<HTMLInputElement>('.link-search input')!;
    input.value = 'Crate';
    input.dispatchEvent(new Event('input'));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(options().map(option => option.textContent)).toEqual(['BBox 40 x 40In stock: 12 box', 'PPacking tape', 'Create new item “Crate”']);
    // The arrow keys reach it, after the results.
    key(input, 'ArrowUp');
    expect(input.getAttribute('aria-activedescendant')).toBe(options()[2].id);
    key(input, 'Enter');
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(asked).toEqual([['item', 'Crate', { group: 'g-pack' }]]);
    expect(document.querySelector('.link-pop')).toBeNull();
    expect(store.field('item').value.peek()).toBe('i-new');
    expect(host.querySelector('.in-link')!.textContent).toContain('Crate');
  });

  it('hides "Create new" from a person who may not create the target', async () => {
    const made = backend(false);
    store = made.store;
    setLinkSource(apiLinks(made.api, made.index, { quickCreate: async () => null }));
    await store.create({ group: 'g-pack' });
    const host = render(() => BaseField({ field: store!.field('item') }));
    await open(host);
    expect(options()).toHaveLength(2);
    expect(document.querySelector('.link-create')).toBeNull();
  });
});

describe('the quick-create form', () => {
  it('starts with the typed title and the filter values, and shows the fields it needs', () => {
    const item = schema().models.find(entry => entry.name === 'item')!;
    expect(quickCreateValues(item, 'Crate', { group: 'g-pack', nothing: 1 })).toEqual({ title: 'Crate', group: 'g-pack' });
    // The title, required fields without a default, and the prefilled ones; not the code (it has a default) or notes.
    expect(quickCreateFields(item, ['group'])).toEqual(['title', 'group']);
  });

  it('creates the record through the create route and resolves with it', async () => {
    const made = backend();
    const created = quickCreate({ api: made.api, schema: made.index }, 'item', 'Crate', { group: 'g-pack' });
    await new Promise(resolve => setTimeout(resolve, 0));
    const dialog = document.querySelector<HTMLElement>('.quick-create')!;
    expect(dialog.querySelector('h2')!.textContent).toBe('New item');
    expect([...dialog.querySelectorAll('[data-field]')].map(node => node.getAttribute('data-field'))).toEqual(['title', 'group']);
    expect(dialog.querySelector<HTMLInputElement>('[data-field="title"] input')!.value).toBe('Crate');
    [...dialog.querySelectorAll<HTMLButtonElement>('.modal-f button')].find(button => button.textContent === 'Create item')!.click();
    expect(await created).toEqual({ id: 'i-new', title: 'Crate' });
    const post = made.sent.find(item => item.method === 'POST')!;
    expect(post.url).toBe('/api/item');
    expect(post.body).toMatchObject({ title: 'Crate', group: 'g-pack' });
    expect(document.querySelector('.quick-create')).toBeNull();
  });

  it('resolves null when it is closed without saving', async () => {
    const made = backend();
    const created = quickCreate({ api: made.api, schema: made.index }, 'item', '', {});
    await new Promise(resolve => setTimeout(resolve, 0));
    document.querySelector<HTMLButtonElement>('.quick-create .modal-h .icon-btn')!.click();
    expect(await created).toBeNull();
    expect(made.sent.some(item => item.method === 'POST')).toBe(false);
  });
});
