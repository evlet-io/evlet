// Link filters that read other fields of the form (Ref): the picker searches with the current,
// unsaved values, and a field change clears the Links that no longer fit, with a note.
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
  model('company', [field('title', 'Title')]),
  model('branch', [field('title', 'Title'), field('company', 'Company', 'company'), field('open', 'Open')]),
  model('floor', [field('title', 'Title'), field('branch', 'Branch', 'branch')]),
  model('visit', [field('title', 'Title'), field('company', 'Company', 'company'), field('branch', 'Branch', 'branch'),
    field('floor', 'Floor', 'floor'), field('open_branch', 'Open branch', 'branch')], {
    children: [{ name: 'stops', model: 'visit_stop', label: 'Stops', link: 'evlet_owner' }],
    behavior: {
      branch: { filters: { company: ref('company') } },
      floor: { filters: { branch: ref('branch') } },
      open_branch: { filters: { company: ref('company'), open: true } },
    } }),
  model('visit_stop', [field('branch', 'Branch', 'branch')], { child_only: true, behavior: { branch: { filters: { company: ref('parent.company') } } } }),
] } as AppSchema;

// Records the list route answers, filtered as the server would.
const records: Record<string, Array<Record<string, unknown>>> = {
  company: [{ id: 'c-harbor', title: 'Harbor Trading' }, { id: 'c-summit', title: 'Summit Services' }],
  branch: [{ id: 'b-port', title: 'Port office', company: 'c-harbor', open: true },
    { id: 'b-dock', title: 'Dock office', company: 'c-harbor', open: false },
    { id: 'b-hill', title: 'Hill office', company: 'c-summit', open: true },
    { id: 'b-shared', title: 'Shared office', company: 'c-summit', open: true }],
  floor: [{ id: 'f-port', title: 'Port floor 1', branch: 'b-port' }, { id: 'f-hill', title: 'Hill floor 1', branch: 'b-hill' }],
};

function backend() {
  const sent: Sent[] = [];
  const fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://127.0.0.1');
    sent.push({ method: init.method ?? 'GET', url: String(input), headers: {}, body: undefined });
    const name = url.pathname.split('/')[2];
    const filters = JSON.parse(url.searchParams.get('filters') ?? '{}') as Record<string, unknown>;
    const items = (records[name] ?? []).filter(item => Object.entries(filters).every(([key, value]) =>
      key === 'id__in' ? (value as string[]).includes(item.id as string) : item[key] === value));
    return new Response(JSON.stringify({ items, total: items.length, limit: 20, offset: 0 }), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  const api = createClient({ fetch: fetch as typeof globalThis.fetch });
  const store = new RecordStore({ api, schema, model: 'visit' });
  return { store, api, sent };
}

const lists = (sent: Sent[]) => sent.map(item => decodeURIComponent(item.url)).filter(url => url.includes('filters='));

let store: RecordStore | null = null;
afterEach(() => { store?.dispose(); store = null; cleanup(); setLinkSource(null); });

describe('Links whose filters read another field', () => {
  it('clears a dependent Link that no longer fits, with a note, and keeps one that still fits', async () => {
    const made = backend();
    store = made.store;
    await store.create({ company: 'c-harbor', branch: 'b-port' });
    store.field('company').set('c-summit');
    await store.idle();
    expect(store.field('branch').value.peek()).toBeNull();
    expect(store.field('branch').note.peek()).toBe('Cleared because Company changed');
    // The browser did not hold the branch, so it asked the list route with the new values.
    expect(lists(made.sent)).toContain('/api/branch?limit=1&offset=0&filters={"company":"c-summit","id__in":["b-port"]}');

    store.field('branch').set('b-hill');
    expect(store.field('branch').note.peek()).toBeNull();
    // Choosing the same company again keeps a branch that fits.
    store.field('company').set('c-summit');
    await store.idle();
    expect(store.field('branch').value.peek()).toBe('b-hill');
    expect(store.field('branch').note.peek()).toBeNull();
  });

  it('clears chained Links: company, then branch, then floor', async () => {
    const made = backend();
    store = made.store;
    await store.create({ company: 'c-harbor', branch: 'b-port', floor: 'f-port' });
    store.field('company').set('c-summit');
    await store.idle();
    expect(store.field('branch').value.peek()).toBeNull();
    expect(store.field('floor').value.peek()).toBeNull();
    expect(store.field('floor').note.peek()).toBe('Cleared because Branch changed');
  });

  it('clears a Link when the field it reads is emptied, without asking the server', async () => {
    const made = backend();
    store = made.store;
    await store.create({ company: 'c-harbor', branch: 'b-port' });
    made.sent.length = 0;
    store.field('company').set(null);
    await store.idle();
    expect(store.field('branch').value.peek()).toBeNull();
    expect(made.sent).toEqual([]);
  });

  it('checks every filter of a Link, literal values included', async () => {
    const made = backend();
    store = made.store;
    await store.create({ company: 'c-summit', open_branch: 'b-hill' });
    // Dock office belongs to Harbor Trading but is closed.
    store.field('company').set('c-harbor');
    await store.idle();
    expect(store.field('open_branch').value.peek()).toBeNull();
    expect(lists(made.sent)).toContain('/api/branch?limit=1&offset=0&filters={"company":"c-harbor","open":true,"id__in":["b-hill"]}');
  });

  it('clears the Links of child rows that read the parent', async () => {
    const made = backend();
    store = made.store;
    await store.create({ company: 'c-summit', children: { stops: [{ branch: 'b-hill' }, { branch: 'b-shared' }] } });
    const [first, second] = store.table('stops').rows.peek();
    // Port office fits the new company; the others do not.
    first.field('branch').set('b-port');
    store.field('company').set('c-harbor');
    await store.idle();
    expect(first.field('branch').value.peek()).toBe('b-port');
    expect(second.field('branch').value.peek()).toBeNull();
    expect(second.field('branch').note.peek()).toBe('Cleared because Company changed');
  });

  it('shows the note under the field', async () => {
    const made = backend();
    store = made.store;
    await store.create({ company: 'c-harbor', branch: 'b-port' });
    const host = render(() => BaseField({ field: store!.field('branch') }));
    const note = host.querySelector<HTMLElement>('.fld-note')!;
    expect(note.hidden).toBe(true);
    store.field('company').set('c-summit');
    await store.idle();
    expect(note.hidden).toBe(false);
    expect(note.textContent).toBe('Cleared because Company changed');
  });
});

describe('the Link picker', () => {
  it('searches only records that fit the current, unsaved values', async () => {
    const made = backend();
    store = made.store;
    setLinkSource(apiLinks(made.api, new SchemaIndex(schema)));
    await store.create({ company: 'c-harbor' });
    store.field('company').set('c-summit');
    const host = render(() => BaseField({ field: store!.field('branch') }));
    host.querySelector<HTMLButtonElement>('.in-link')!.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    const options = [...document.querySelectorAll('[role="option"]')].map(option => option.textContent);
    expect(options).toEqual(['HHill office', 'SShared office']);
    expect(lists(made.sent).at(-1)).toBe('/api/branch?limit=8&offset=0&filters={"company":"c-summit"}');
  });

  it('asks for the referenced field first when it is empty', async () => {
    const made = backend();
    store = made.store;
    setLinkSource(apiLinks(made.api, new SchemaIndex(schema)));
    await store.create({});
    const host = render(() => BaseField({ field: store!.field('branch') }));
    host.querySelector<HTMLButtonElement>('.in-link')!.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(document.querySelector('.link-status')!.textContent).toBe('Choose Company first.');
    expect(document.querySelectorAll('[role="option"]')).toHaveLength(0);
    expect(lists(made.sent)).toEqual([]);
  });

  it('searches a child row with the parent value', async () => {
    const made = backend();
    store = made.store;
    setLinkSource(apiLinks(made.api, new SchemaIndex(schema)));
    await store.create({ company: 'c-harbor', children: { stops: [{}] } });
    const row = store.table('stops').rows.peek()[0];
    const host = render(() => BaseField({ field: row.field('branch') }));
    host.querySelector<HTMLButtonElement>('.in-link')!.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect([...document.querySelectorAll('[role="option"]')].map(option => option.textContent)).toEqual(['PPort office', 'DDock office']);
  });
});
