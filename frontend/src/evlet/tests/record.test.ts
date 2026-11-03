import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { effect, scope } from '../reactive';
import { buildChanges } from '../model/changes';
import { RecordStore, sameValue } from '../model/record';
import type { AppSchema } from '../model/schema';
import { copy, responses, server, type Sent } from './server';

const schema = responses.schema.body as AppSchema;
const stores: RecordStore[] = [];
afterEach(() => stores.splice(0).forEach(store => store.dispose()));

// The backend as recorded: a 120-line order (the detail embeds 100), its customer and product.
function backend(overrides: Array<[string, RegExp, any]> = []) {
  let saved = false;
  const routes: Array<[string, RegExp, any]> = [
    ...overrides,
    ['GET', /\/api\/order\/1$/, 'detail'],
    ['GET', /\/api\/order\/1\/lines\/lines\?limit=100&offset=100$/, () => responses[saved ? 'lines_page_after_update' : 'lines_page']],
    ['GET', /\/api\/customer\/1$/, { ...responses.customer, status: 200 }],
    ['GET', /\/api\/product\/1$/, { ...responses.product, status: 200 }],
    ['PATCH', /\/api\/order\/1$/, () => { saved = true; return responses.update; }],
    ['POST', /\/api\/order\/1\/actions\/close$/, () => { saved = true; return responses.action; }],
    ['POST', /\/api\/_draft\/order$/, 'draft'],
  ];
  const stub = server(routes);
  const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema, model: 'order', today: () => '2026-11-01' });
  stores.push(store);
  return { store, sent: stub.sent };
}
const paths = (sent: Sent[]) => sent.map(item => `${item.method} ${item.url}`);
const line = (store: RecordStore, id: number) => store.table('lines').rows.peek().find(row => row.id === id)!;

describe('opening a record', () => {
  it('pages in rows over 100, then allows edits, with calculations matching the server', async () => {
    const { store, sent } = backend();
    await store.open(1);
    await store.idle();
    expect(paths(sent).slice(0, 2)).toEqual(['GET /api/order/1', 'GET /api/order/1/lines/lines?limit=100&offset=100']);
    expect(paths(sent)).toContain('GET /api/customer/1');
    expect(paths(sent)).toContain('GET /api/product/1');
    const lines = store.table('lines');
    expect(lines.rows.peek()).toHaveLength(120);
    expect(lines.partial.peek()).toBe(false);
    expect(store.ready.peek()).toBe(true);
    expect(store.field('total').value.peek()).toBe('300.00');
    expect(store.field('line_count').value.peek()).toBe(120);
    expect(store.field('due').value.peek()).toBe('2026-12-16');
    expect(store.field('due').result.peek()).toEqual({ state: 'ready' });
    expect(store.dirty.peek()).toBe(false);
    expect(store.etag.peek()).toBe(responses.detail.etag);
  });

  it('refuses edits until every row is loaded', async () => {
    const { store } = backend();
    await store.open(1, { loadAll: false });
    expect(store.ready.peek()).toBe(false);
    expect(store.table('lines').partial.peek()).toBe(true);
    expect(() => store.field('title').set('x')).toThrow('Load every row');
    expect(() => store.table('lines').add()).toThrow('Load every row');
    await store.loadAll();
    store.field('title').set('x');
    expect(store.dirty.peek()).toBe(true);
  });

  it('names nested table paths for the lines endpoint', async () => {
    const { store } = backend();
    await store.open(1);
    const first = line(store, 1);
    expect(store.table('lines').apiPath()).toEqual(['lines']);
    expect(first.table('parts').apiPath()).toEqual(['lines', 1, 'parts']);
    expect(first.table('parts').rows.peek().map(row => row.field('hours').value.peek())).toEqual(['1.5000', '2.0000']);
  });

  it('exposes field metadata from the schema for renderers', async () => {
    const { store } = backend();
    await store.open(1);
    const total = store.field('total'), stage = store.field('stage'), reason = store.field('reason');
    expect([total.label, total.meta.type, total.meta.scale, total.calculated]).toEqual(['Total', 'currency', 2, true]);
    expect(stage.meta.choices).toEqual(['open', 'won', 'lost']);
    expect(reason.meta.applies_when).not.toBeNull();
    expect(store.actions.map(action => action.name)).toEqual(['close']);
    expect(store.state).toBeNull();
    expect(total.editable.peek()).toBe(false);
    expect(store.field('title').editable.peek()).toBe(true);
  });
});

describe('editing', () => {
  it('a quantity edit recalculates its amount and the total, and notifies only those readers', async () => {
    const { store } = backend();
    await store.open(1);
    const rows = store.table('lines').rows.peek();
    let amountRuns = 0, totalRuns = 0;
    const stop = scope(() => {
      for (const row of rows) effect(() => { row.field('amount').value.get(); amountRuns++; });
      effect(() => { store.field('total').value.get(); totalRuns++; });
    });
    amountRuns = totalRuns = 0;
    line(store, 5).field('qty').set('3');
    expect(line(store, 5).field('amount').value.peek()).toBe('7.50');
    expect(store.field('total').value.peek()).toBe('305.00');
    expect([amountRuns, totalRuns]).toEqual([1, 1]);
    stop();
  });

  it('tracks dirty fields and reverts to the saved values', async () => {
    const { store } = backend();
    await store.open(1);
    const qty = line(store, 5).field('qty');
    qty.set('1.0');
    expect(qty.dirty.peek()).toBe(false); // the same number as the saved 1.0000
    qty.set('2');
    store.field('title').set('Changed');
    expect([qty.dirty.peek(), store.dirty.peek()]).toEqual([true, true]);
    await store.revert();
    expect(qty.value.peek()).toBe('1.0000');
    expect(store.field('title').value.peek()).toBe('Big order');
    expect(store.field('total').value.peek()).toBe('300.00');
    expect(store.dirty.peek()).toBe(false);
  });

  it('a new line takes its rate from the product (initial) and updates the totals', async () => {
    const { store } = backend();
    await store.open(1);
    await store.idle();
    const row = store.table('lines').add({ product: 1 });
    expect(row.isNew).toBe(true);
    expect(row.field('rate').value.peek()).toBe('250.00');
    expect(row.field('amount').value.peek()).toBe('250.00');
    expect(store.field('total').value.peek()).toBe('550.00');
    expect(store.field('line_count').value.peek()).toBe(121);
    store.table('lines').remove(row);
    expect(store.field('total').value.peek()).toBe('300.00');
    expect(store.dirty.peek()).toBe(false);
  });

  it('rules flow into field cells; a lock is decided on the saved record', async () => {
    const { store } = backend();
    await store.open(1);
    const reason = store.field('reason'), remark = store.field('remark');
    expect(reason.rules.peek()).toEqual({ applies: false, required: false, locked: false });
    store.field('stage').set('lost');
    expect(reason.rules.peek()).toEqual({ applies: true, required: true, locked: false });
    expect(reason.editable.peek()).toBe(true);
    store.field('stage').set('won');
    // The server checks locked_when on the record as saved (stage open), so remark stays editable until saved.
    expect(remark.rules.peek().locked).toBe(false);
    expect(reason.editable.peek()).toBe(false);
  });

  it('refuses writes to calculated fields', async () => {
    const { store } = backend();
    await store.open(1);
    expect(() => store.field('total').set('1')).toThrow('Total is calculated');
  });
});

describe('saving', () => {
  it('builds a change set, saves with If-Match, and the response replaces the draft', async () => {
    const { store, sent } = backend();
    await store.open(1);
    store.field('title').set('Renamed');
    const lines = store.table('lines');
    line(store, 2).field('qty').set('3');
    lines.remove(line(store, 3));
    const added = lines.add({ qty: '2', rate: '1' });
    expect(added.key).toBe('n1');
    expect(buildChanges(store.root)).toEqual({
      title: 'Renamed',
      children: { lines: { create: [{ _key: 'n1', qty: '2', rate: '1' }], update: [{ id: 2, qty: '3' }], delete: [3] } },
    });
    expect(store.field('total').value.peek()).toBe('304.50');
    expect(await store.save()).toBe(true);
    const patch = sent.find(item => item.method === 'PATCH')!;
    expect(patch.headers['if-match']).toBe(responses.detail.etag);
    expect(patch.body._calculation).toEqual({ language: 'finite-v1', model_schema_hash: (schema.models.find(model => model.name === 'order')!.calculation!.model_schema_hash) });
    expect(store.etag.peek()).toBe(responses.update.etag);
    expect(store.dirty.peek()).toBe(false);
    expect(lines.rows.peek()).toHaveLength(120);
    // The new row kept its object (and so its DOM) and now has the id the server gave it.
    expect(added.id).toBe(121);
    expect(lines.rows.peek().includes(added)).toBe(true);
    expect(store.field('title').value.peek()).toBe('Renamed');
    expect(store.field('total').value.peek()).toBe('304.50');
  });

  it('nests change sets for rows of rows', async () => {
    const { store } = backend();
    await store.open(1);
    const parts = line(store, 1).table('parts');
    parts.rows.peek()[0].field('hours').set('3');
    parts.add({ hours: '1' });
    expect(buildChanges(store.root)).toEqual({ children: { lines: { update: [{ id: 1, children: { parts: {
      create: [{ _key: 'n1', hours: '1' }], update: [{ id: 1, hours: '3' }] } } }] } } });
  });

  it('sends nothing for an unchanged record', async () => {
    const { store, sent } = backend();
    await store.open(1);
    const before = sent.length;
    expect(await store.save()).toBe(true);
    expect(sent.length).toBe(before);
  });

  it('sends order when rows were moved', async () => {
    const { store } = backend();
    await store.open(1);
    const lines = store.table('lines');
    lines.move(line(store, 3), 0);
    const changes = buildChanges(store.root) as any;
    expect(changes.children.lines.order.slice(0, 3)).toEqual([3, 1, 2]);
    expect(changes.children.lines.order).toHaveLength(120);
  });

  it('a new record sends every value and full row lists', async () => {
    const { store } = backend();
    await store.create({ title: 'Fresh' });
    expect(store.field('stage').value.peek()).toBe('open');
    store.table('lines').add({ qty: '2', rate: '3' });
    expect(store.field('total').value.peek()).toBe('6.00');
    expect(buildChanges(store.root)).toEqual({ title: 'Fresh', stage: 'open', children: { lines: [{ _key: 'n1', qty: '2', rate: '3' }] } });
  });

  // A ChildTable on a plain model (the Playground's Project tasks) takes only full row lists.
  describe('a table that takes only full lists', () => {
    const text = (name: string) => ({ name, type: 'text', label: name, required: false, read_only: false, default_kind: 'none' });
    const plain = { version: 1, ui_version: 1, models: [
      { name: 'project', label: 'Project', plural: 'Projects', title_field: 'name', list_fields: ['name'], fields: [text('name')],
        children: [{ name: 'tasks', model: 'projecttask', link: 'project', label: 'Tasks', min_rows: 0, max_rows: 100, row_changes: false }] },
      { name: 'projecttask', label: 'Project task', plural: 'Project tasks', title_field: 'title', list_fields: ['title', 'done'], child_only: true,
        fields: [{ ...text('project'), type: 'link', target: 'project' }, text('title'),
          { name: 'done', type: 'boolean', label: 'Done', required: true, read_only: false, default_kind: 'static', default: false }] },
    ] } as unknown as AppSchema;
    const detail = { id: 4, name: 'Explore Evlet', children: { tasks: [{ id: 1, title: 'Read', done: false }, { id: 2, title: 'Try', done: true }] } };
    const open = async () => {
      const stub = server([['GET', /\/api\/project\/4$/, { status: 200, body: detail, etag: '"r1"' }],
        ['PATCH', /\/api\/project\/4$/, { status: 200, body: detail, etag: '"r2"' }]]);
      const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema: plain, model: 'project', today: () => '2026-11-01' });
      stores.push(store);
      return { store, sent: stub.sent };
    };

    it('sends saved rows by id with their changes and new rows without _key', async () => {
      const { store, sent } = await open();
      await store.open(4);
      const tasks = store.table('tasks');
      tasks.rows.peek()[0].field('done').set(true);
      tasks.add({ title: 'Ship' });
      expect(buildChanges(store.root)).toEqual({ children: { tasks: [{ id: 1, done: true }, { id: 2 }, { title: 'Ship', done: false }] } });
      expect(await store.save()).toBe(true);
      expect(sent.find(item => item.method === 'PATCH')!.body).toEqual({ children: { tasks: [{ id: 1, done: true }, { id: 2 }, { title: 'Ship', done: false }] } });
    });

    it('leaves out a removed row and sends nothing for an unchanged table', async () => {
      const { store } = await open();
      await store.open(4);
      store.field('name').set('Renamed');
      expect(buildChanges(store.root)).toEqual({ name: 'Renamed' });
      store.table('tasks').remove(store.table('tasks').rows.peek()[1]);
      expect(buildChanges(store.root)).toEqual({ name: 'Renamed', children: { tasks: [{ id: 1 }] } });
    });

    it('a new record sends its rows without _key', async () => {
      const { store } = await open();
      await store.create({ name: 'Fresh' });
      store.table('tasks').add({ title: 'Plan' });
      expect(buildChanges(store.root)).toEqual({ name: 'Fresh', children: { tasks: [{ title: 'Plan', done: false }] } });
    });
  });

  it('fills default=CurrentUser fields with the signed-in user on a new record', async () => {
    const order = schema.models.find(model => model.name === 'order')!;
    const withOwner = { ...schema, models: schema.models.map(model => model === order ? { ...order, fields: [...order.fields,
      { name: 'owner', type: 'text', label: 'Owner', required: true, read_only: false, default_kind: 'user' },
      { name: 'author_id', type: 'integer', label: 'Author', required: false, read_only: false, default_kind: 'user' }] } : model) };
    const store = new RecordStore({ api: createClient({ fetch: server([]).fetch }), schema: withOwner, model: 'order', today: () => '2026-11-01',
      user: { id: 7, username: 'sara', role: 'editor' } });
    stores.push(store);
    await store.create({ title: 'Fresh' });
    expect([store.field('owner').value.peek(), store.field('author_id').value.peek()]).toEqual(['sara', 7]);
    expect(buildChanges(store.root)).toMatchObject({ owner: 'sara', author_id: 7 });
  });

  it('keeps the draft and reports a conflict when the record changed on the server', async () => {
    const { store } = backend([['PATCH', /\/api\/order\/1$/, 'stale_if_match']]);
    await store.open(1);
    store.field('title').set('Mine');
    expect(await store.save()).toBe(false);
    expect(store.conflict.peek()).toMatchObject({ status: 409, message: 'This record changed since you opened it. Reload it before saving.' });
    expect(store.field('title').value.peek()).toBe('Mine');
    expect(store.dirty.peek()).toBe(true);
  });

  it('puts server validation errors on the field of the right row', async () => {
    const { store } = backend([['PATCH', /\/api\/order\/1$/, 'validation']]);
    await store.open(1);
    line(store, 4).field('qty').set('13');
    expect(await store.save()).toBe(false);
    expect(line(store, 4).field('qty').error.peek()).toBe('Thirteen is not allowed');
    expect(store.formError.peek()).toBeNull();
    line(store, 4).field('qty').set('12');
    expect(line(store, 4).field('qty').error.peek()).toBeNull();
  });

  it('sends errors it cannot place to the form', async () => {
    const { store } = backend();
    await store.open(1);
    store.showErrors({ _form: 'One or more values are invalid', 'lines.999.qty': 'Gone' });
    expect(store.formError.peek()).toBe('One or more values are invalid Gone');
  });
});

describe('actions and previews', () => {
  it('runs an action on a clean record and applies the result', async () => {
    const { store, sent } = backend();
    await store.open(1);
    store.field('title').set('Unsaved');
    expect(await store.runAction('close', { note: 'done' })).toBe(false);
    expect(store.formError.peek()).toBe('Save or discard your changes first.');
    await store.revert();
    expect(await store.runAction('close', { note: 'done' })).toBe(true);
    const post = sent.find(item => item.url.endsWith('/actions/close'))!;
    expect(post.body).toEqual({ note: 'done' });
    expect(post.headers['if-match']).toBe(responses.detail.etag);
    expect(store.field('stage').value.peek()).toBe('won');
    expect(store.etag.peek()).toBe(responses.action.etag);
  });

  it('asks the server to evaluate a draft', async () => {
    const { store, sent } = backend();
    await store.open(1);
    store.field('stage').set('lost');
    const result = await store.preview();
    expect(result.states).toEqual({ reason: 'required-input' });
    expect(sent.at(-1)!.body).toMatchObject({ id: 1, values: { stage: 'lost' } });
  });

  it('reloads from the server and forgets the draft', async () => {
    const { store } = backend();
    await store.open(1);
    store.field('title').set('Temp');
    await store.reload();
    expect(store.field('title').value.peek()).toBe('Big order');
    expect(store.dirty.peek()).toBe(false);
  });
});

describe('values', () => {
  it('compares numbers exactly and treats empty values alike', () => {
    const decimal = { name: 'qty', type: 'decimal', label: 'Qty', required: false, read_only: false };
    expect(sameValue(decimal, '1.50', '1.5')).toBe(true);
    expect(sameValue(decimal, '0.1', '0.10000000000000000001')).toBe(false);
    expect(sameValue(decimal, '', null)).toBe(true);
    expect(sameValue({ ...decimal, type: 'text' }, '1.50', '1.5')).toBe(false);
  });

  it('the recorded detail is not changed by the store', async () => {
    const before = copy(responses.detail.body);
    const { store } = backend();
    await store.open(1);
    line(store, 1).field('qty').set('9');
    expect(responses.detail.body).toEqual(before);
  });
});
