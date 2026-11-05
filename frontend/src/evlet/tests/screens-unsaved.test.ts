// Unsaved changes kept on leave (docs/ui.md, Unsaved changes): leaving never saves the record. A saved
// record's edits go to PUT /api/_unsaved/<model>/<id>; a new record's stay in browser storage.
import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import type { AppSchema } from '../model/schema';
import { apply, capture, captureNew, conflicts, isEmpty } from '../model/unsaved';
import { DEFAULT_LIMITS, LocalUnsaved } from '../app/unsaved';
import { relativeTime } from '../app/unsaved-bar';
import { adminRoutes, byText, click, lastToast, press, recorded, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, id, responses, server, type Recorded } from './server';

afterEach(() => { stopAll(); localStorage.clear(); });

const ORDER = '0000000000001';
const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('.grid tbody tr[data-key]')];
const cellOf = (row: Element, col: string) => [...row.querySelectorAll<HTMLElement>('td[data-col]')].find(td => td.dataset.col === col)!;
const field = (host: Element, name: string) => host.querySelector<HTMLElement>(`.ff[data-field="${name}"]`);
const dialogTitle = () => text(document.querySelector('[role="dialog"] h2, [role="alertdialog"] h2, .modal h2'));
const bar = (host: Element) => host.querySelector('.ev-unsaved .inv-banner');

/** /api/_settings/me with the person's unsaved changes info and preferences changed as given. */
function me(count = 0, change: (body: any) => void = () => {}): Recorded {
  const value = copy(screen.settings_me);
  value.body.unsaved = { limits: { ...DEFAULT_LIMITS }, count, enabled: true };
  change(value.body);
  return value;
}

function schema(change: (models: any[]) => void = () => {}): Recorded {
  const value = copy(screen.schema);
  change(value.body.models);
  return value;
}

const orderRoutes = (extra: Route[] = [], settings = me()): Route[] => [
  ...extra,
  ...adminRoutes(),
  ['GET', /\/api\/_settings\/me$/, settings],
  ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
  ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
  ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
  ['PATCH', /\/api\/order\/0000000000001$/, recorded.update],
];
const kept = { status: 200, etag: null, body: { saved_at: 1, size: 40, dropped: [] } };

async function editLine(host: HTMLElement) {
  await until(() => expect(rowsIn(host).length).toBeGreaterThan(5));
  const qty = cellOf(rowsIn(host)[1], 'qty');
  click(qty);
  const input = qty.querySelector('input')!;
  input.value = '3';
  press(input, 'Enter');
  press(document.activeElement!, 'Escape');
  await until(() => expect(text(host.querySelector('.ev-savebar'))).toContain('Unsaved changes'));
}

describe('leaving a saved record with unsaved edits', () => {
  it('keeps the edits apart from the record, says so and never saves the record', async () => {
    const { host, sent, router } = start(orderRoutes([['PUT', /\/api\/_unsaved\/order\/0000000000001$/, kept]]), `/app/order/${ORDER}?tab=t-lines`);
    await editLine(host);
    await router.go('docs', {});
    await until(() => expect(location.pathname).toBe('/docs'));
    expect(document.querySelector('.modal')).toBeNull();
    expect(lastToast()).toBe('Unsaved changes kept, restore when you return');
    const [put] = requests(sent, 'PUT', /_unsaved\/order\/0000000000001$/);
    const line = recorded.detail.body.children.lines[1];
    expect(put.body.base_revision).toBe(recorded.detail.etag);
    expect(put.body.changes).toEqual({ children: { lines: { changed: { [line.id]: { values: { qty: '3.0000' }, base: { qty: line.qty } } } } } });
    expect(put.headers['x-csrf-token']).toBeTruthy();
    expect(requests(sent, 'PATCH', /order/)).toHaveLength(0);
  });

  it('asks as before when the changes cannot be kept, and says why', async () => {
    const refused = { status: 413, etag: null, body: { error: 'These unsaved changes are larger than 256 KB, so they are not kept. They stay in this tab until you save or close it.' } };
    const { host, router } = start(orderRoutes([['PUT', /\/api\/_unsaved\//, refused]]), `/app/order/${ORDER}?tab=t-lines`);
    await editLine(host);
    void router.go('docs', {});
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    expect(text(document.querySelector('.modal'))).toContain('larger than 256 KB');
    click(byText(document, '[role="dialog"] button', 'Cancel'));
    await until(() => expect(location.pathname).toBe(`/app/order/${ORDER}`));
    expect(host.querySelector('.ev-savebar')).not.toBeNull();
  });

  it('a model with Meta.autosave = False keeps the leave prompt and sends nothing', async () => {
    const routes = orderRoutes().map(route => route[1].source.includes('schema') ? ['GET', /\/api\/schema$/, schema(models => { models.find(m => m.name === 'order').autosave = false; })] as Route : route);
    const { host, sent, router } = start(routes, `/app/order/${ORDER}?tab=t-lines`);
    await editLine(host);
    void router.go('docs', {});
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    expect(text(document.querySelector('.modal'))).not.toContain('could not be kept');
    expect(requests(sent, 'PUT', /_unsaved/)).toHaveLength(0);
  });

  it('a person who turned the setting off is asked, and nothing is sent', async () => {
    const { host, sent, router } = start(orderRoutes([], me(0, body => { body.prefs.keep_unsaved = false; })), `/app/order/${ORDER}?tab=t-lines`);
    await editLine(host);
    void router.go('docs', {});
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    expect(requests(sent, 'PUT', /_unsaved/)).toHaveLength(0);
  });

  it('hiding the tab keeps the edits without a toast or a prompt', async () => {
    const { host, sent } = start(orderRoutes([['PUT', /\/api\/_unsaved\//, kept]]), `/app/order/${ORDER}?tab=t-lines`);
    await editLine(host);
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    await until(() => expect(requests(sent, 'PUT', /_unsaved\/order/)).toHaveLength(1));
    delete (document as any).visibilityState;
    expect(document.querySelector('.modal')).toBeNull();
  });
});

describe('returning to a record with kept changes', () => {
  const entry = (stale: boolean, base: unknown = recorded.detail.body.title) => ({ status: 200, etag: null, body: { dropped: [], entry: {
    changes: { values: { title: 'Kept title' }, base: { title: base } }, base_revision: '"old"', saved_at: Math.floor(Date.now() / 1000) - 300, size: 60, stale } } });

  it('offers Restore: the edits come back unsaved and nothing is saved', async () => {
    const { host, sent } = start(orderRoutes([['GET', /\/api\/_unsaved\/order\/0000000000001$/, entry(false)]], me(1)), `/app/order/${ORDER}`);
    await until(() => expect(text(bar(host))).toContain('You have unsaved changes from 5 minutes ago.'));
    click(byText(bar(host)!, 'button', 'Restore'));
    await until(() => expect(bar(host)).toBeNull());
    expect(text(host.querySelector('.ev-savebar'))).toContain('Unsaved changes');
    expect(text(field(host, 'title'))).toContain('Kept title');
    expect(lastToast()).toContain('Save to keep them');
    expect(requests(sent, 'PATCH', /order/)).toHaveLength(0);
  });

  it('offers Discard, which deletes the kept changes', async () => {
    const { host, sent } = start(orderRoutes([['GET', /\/api\/_unsaved\/order\//, entry(false)], ['DELETE', /\/api\/_unsaved\/order\/0000000000001$/, { status: 204, etag: null, body: null }]], me(1)),
      `/app/order/${ORDER}`);
    await until(() => expect(bar(host)).not.toBeNull());
    click(byText(bar(host)!, 'button', 'Discard'));
    await until(() => expect(requests(sent, 'DELETE', /_unsaved\/order\/0000000000001$/)).toHaveLength(1));
    expect(bar(host)).toBeNull();
    expect(host.querySelector('.ev-savebar')).toBeNull();
  });

  it('when the record was saved since, lists each field both changed and keeps the chosen value', async () => {
    const { host } = start(orderRoutes([['GET', /\/api\/_unsaved\/order\//, entry(true, 'Older title')]], me(1)), `/app/order/${ORDER}`);
    await until(() => expect(text(bar(host))).toContain('This record was saved since.'));
    click(byText(bar(host)!, 'button', 'Restore'));
    await until(() => expect(dialogTitle()).toBe('This record was saved since you left it'));
    const conflict = document.querySelector('.ev-conflict[data-path="title"]')!;
    expect(text(conflict)).toContain('Kept title');
    expect(text(conflict)).toContain(recorded.detail.body.title);
    click(byText(conflict, 'button', 'Keep saved'));
    click(byText(document, '.modal button', 'Restore'));
    await until(() => expect(document.querySelector('.modal')).toBeNull());
    expect(host.querySelector('.ev-savebar')).toBeNull();
    expect(text(field(host, 'title'))).toContain(recorded.detail.body.title);
  });

  it('says so when the person can no longer change the record', async () => {
    const { host } = start(orderRoutes([['GET', /\/api\/_unsaved\/order\//, { status: 200, etag: null, body: { entry: null, dropped: 'record' } }]], me(1)), `/app/order/${ORDER}`);
    await until(() => expect(text(bar(host))).toContain('can no longer change this record'));
  });

  it('does not ask the server when the person has no kept changes', async () => {
    const { host, sent } = start(orderRoutes([], me(0)), `/app/order/${ORDER}`);
    await until(() => expect(field(host, 'title')).not.toBeNull());
    expect(requests(sent, 'GET', /_unsaved/)).toHaveLength(0);
  });
});

describe('an unsaved new record stays in the browser', () => {
  const routes = (): Route[] => [...adminRoutes(), ['GET', /\/api\/_settings\/me$/, me()]];

  it('keeps the draft on leave, offers it on the next New, and clears it on Start fresh', async () => {
    const { host, sent, router } = start(routes(), '/app/customer/new', { storage: localStorage });
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New customer'));
    typeInto(host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!, 'Acme Trading');
    await router.go('docs', {});
    await until(() => expect(location.pathname).toBe('/docs'));
    expect(lastToast()).toBe('Unsaved changes kept, restore when you return');
    expect(requests(sent, 'POST', /customer/)).toHaveLength(0);
    expect(requests(sent, 'PUT', /_unsaved/)).toHaveLength(0);
    const key = Object.keys(localStorage).find(item => item.startsWith('evlet.unsaved.') && !item.endsWith('_user'))!;
    expect(JSON.parse(localStorage.getItem(key)!).customer.changes.values).toEqual({ title: 'Acme Trading' });
    await router.go('create', { model: 'customer' });
    await until(() => expect(text(bar(host))).toContain('Restore your unfinished new customer from just now?'));
    click(byText(bar(host)!, 'button', 'Restore'));
    await until(() => expect(host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!.value).toBe('Acme Trading'));
    await router.go('docs', {});
    await router.go('create', { model: 'customer' });
    await until(() => expect(bar(host)).not.toBeNull());
    click(byText(bar(host)!, 'button', 'Start fresh'));
    expect(JSON.parse(localStorage.getItem(key) ?? '{}').customer).toBeUndefined();
  });

  it('without browser storage the edit stays in the tab and leaving asks', async () => {
    const { host, router } = start(routes(), '/app/customer/new', { storage: null });
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New customer'));
    typeInto(host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!, 'Acme');
    void router.go('docs', {});
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    expect(text(document.querySelector('.modal'))).toContain('does not allow keeping them');
  });
});

describe('the browser store', () => {
  const memory = (): Storage => {
    const items = new Map<string, string>();
    return { get length() { return items.size; }, clear: () => items.clear(), key: index => [...items.keys()][index] ?? null,
      getItem: key => items.get(key) ?? null, setItem: (key, value) => { items.set(key, value); }, removeItem: key => { items.delete(key); } };
  };

  it('keeps one entry per model, drops expired and extra ones, and refuses oversize drafts', () => {
    const storage = memory(), store = new LocalUnsaved('acme', 'sara', storage);
    expect(store.put('quote', { values: { title: 'One' } })).toBeNull();
    expect(store.put('quote', { values: { title: 'Two' } })).toBeNull();
    expect(store.get('quote')!.changes.values).toEqual({ title: 'Two' });
    for (const name of ['a', 'b', 'c']) store.put(name, { values: { title: name } }, { ...DEFAULT_LIMITS, browser_entries: 2 });
    expect(['a', 'b', 'c', 'quote'].filter(name => store.get(name))).toHaveLength(2);
    const old = JSON.parse(storage.getItem('evlet.unsaved.acme.sara')!);
    for (const entry of Object.values<any>(old)) entry.saved_at -= 8 * 86400;
    storage.setItem('evlet.unsaved.acme.sara', JSON.stringify(old));
    store.purge();
    expect(storage.getItem('evlet.unsaved.acme.sara')).toBeNull();
    expect(store.put('big', { values: { notes: 'x'.repeat(2048) } }, { ...DEFAULT_LIMITS, entry_kb: 1 })).toContain('larger than 1 KB');
  });

  it('clears on sign-out and when another person signs in on this browser', () => {
    const storage = memory();
    const sara = new LocalUnsaved('acme', 'sara', storage);
    sara.signedIn();
    sara.put('quote', { values: { title: 'Mine' } });
    new LocalUnsaved('acme', 'sara', storage).signedIn();
    expect(sara.get('quote')).not.toBeNull();
    new LocalUnsaved('acme', 'omar', storage).signedIn();
    expect(sara.get('quote')).toBeNull();
    sara.put('quote', { values: { title: 'Again' } });
    new LocalUnsaved('other', 'sara', storage).put('quote', { values: { title: 'Elsewhere' } });
    sara.clearInstance();
    expect(sara.get('quote')).toBeNull();
    expect(new LocalUnsaved('other', 'sara', storage).get('quote')).not.toBeNull();
  });

  it('works when storage throws', () => {
    const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); },
      key: () => null, length: 0, clear: () => {} } as unknown as Storage;
    const store = new LocalUnsaved('acme', 'sara', broken);
    expect(store.available).toBe(false);
    expect(store.get('quote')).toBeNull();
    expect(store.put('quote', { values: { title: 'x' } })).toContain('no room');
    expect(() => { store.signedIn(); store.clearInstance(); store.purge(); store.remove('quote'); }).not.toThrow();
  });

  it('says how long ago', () => {
    const now = 1_000_000;
    expect([10, 300, 7200, 86400, 3 * 86400].map(ago => relativeTime(now - ago, now))).toEqual(['just now', '5 minutes ago', '2 hours ago', 'yesterday', '3 days ago']);
  });
});

describe('capturing and applying changes', () => {
  const appSchema = responses.schema.body as AppSchema;
  const stores: RecordStore[] = [];
  afterEach(() => stores.splice(0).forEach(store => store.dispose()));
  async function order() {
    const stub = server([
      ['GET', /\/api\/order\/0000000000001$/, 'detail'],
      ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, 'lines_page'],
      ['GET', /\/api\/customer\/0000000000001$/, { ...responses.customer, status: 200 }],
      ['GET', /\/api\/product\/0000000000001$/, { ...responses.product, status: 200 }],
    ]);
    const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema: appSchema, model: 'order', today: () => '2026-11-01' });
    stores.push(store);
    await store.open(id(1));
    await store.idle();
    return store;
  }

  it('captures changed, added and removed rows, and puts them back on a fresh copy', async () => {
    const store = await order();
    const lines = store.table('lines');
    const [first, second] = lines.rows.peek();
    store.field('title').set('Renamed');
    first.field('qty').set('9');
    lines.remove(second);
    lines.add({ qty: '1' });
    const changes = capture(store.root);
    expect(changes.values).toEqual({ title: 'Renamed' });
    expect(changes.base).toEqual({ title: responses.detail.body.title });
    expect(changes.children!.lines.changed).toEqual({ [first.id!]: { values: { qty: '9' }, base: { qty: responses.detail.body.children.lines[0].qty } } });
    expect(changes.children!.lines.removed).toEqual([second.id]);
    expect(changes.children!.lines.added).toHaveLength(1);
    const again = await order();
    expect(conflicts(again.root, changes)).toEqual([]);
    expect(apply(again.root, JSON.parse(JSON.stringify(changes)))).toBe(0);
    expect(again.field('title').value.peek()).toBe('Renamed');
    expect(again.table('lines').rows.peek().length).toBe(lines.rows.peek().length);
    expect(again.dirty.peek()).toBe(true);
    expect(isEmpty(capture((await order()).root))).toBe(true);
  });

  it('finds fields both sides changed, and skips the ones kept as saved', async () => {
    const store = await order();
    const changes = { values: { title: 'Mine', remark: 'Mine too' }, base: { title: 'Before', remark: store.field('remark').server.peek() } };
    const found = conflicts(store.root, changes);
    expect(found.map(item => [item.path, item.mine, item.saved])).toEqual([['title', 'Mine', responses.detail.body.title]]);
    apply(store.root, changes, new Set(['title']));
    expect(store.field('title').value.peek()).toBe(responses.detail.body.title);
    expect(store.field('remark').value.peek()).toBe('Mine too');
  });

  it('a new record keeps the fields the person set and every row', async () => {
    const stub = server([]);
    const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema: appSchema, model: 'order', today: () => '2026-11-01' });
    stores.push(store);
    await store.create();
    store.field('title').set('Draft');
    store.table('lines').add({ qty: '2' });
    const changes = captureNew(store.root);
    expect(changes.values).toEqual({ title: 'Draft' });
    expect(changes.children!.lines.added![0].values!.qty).toBe('2');
  });
});

describe('the personal setting', () => {
  it('Appearance has "Keep unsaved changes when I leave a record", on by default', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/_settings\/me$/, me()],
      ['PATCH', /\/api\/_settings\/me\/prefs$/, screen.settings_prefs]], '/settings/appearance');
    const toggle = () => host.querySelector<HTMLButtonElement>('[aria-label="Keep unsaved changes"]');
    await until(() => expect(toggle()).not.toBeNull());
    expect(toggle()!.getAttribute('aria-checked')).toBe('true');
    click(toggle());
    // Opening the page also remembers it as the last settings page.
    await until(() => expect(requests(sent, 'PATCH', /prefs$/).map(item => item.body)).toContainEqual({ keep_unsaved: false }));
  });
});
