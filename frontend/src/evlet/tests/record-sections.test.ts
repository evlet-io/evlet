import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { buildChanges } from '../model/changes';
import { RecordStore } from '../model/record';
import { isSectionRow, type AppSchema } from '../model/schema';
import { copy, id, responses, server } from './server';

// Table(sections=True): the record's children carry section rows {id, section}. Until the table editor draws them,
// the record model leaves them out of its rows, and its change sets never mention them, so the server keeps them.
const stores: RecordStore[] = [];
afterEach(() => stores.splice(0).forEach(store => store.dispose()));

const SECTION = (n: number, label: string) => ({ id: id(900 + n), section: label });

function sectioned() {
  const schema = copy(responses.schema.body) as AppSchema;
  const order = schema.models.find(model => model.name === 'order')!;
  order.children = order.children!.map(child => child.name === 'lines' ? { ...child, sections: true, section_sums: ['qty'] } : child);
  const detail = copy(responses.detail);
  const lines = (detail.body as any).children.lines as Array<Record<string, unknown>>;
  lines.splice(0, 0, SECTION(1, 'Goods'));
  lines.splice(3, 0, SECTION(2, 'Services'));
  const page = copy(responses.lines_page);
  (page.body as any).items.push(SECTION(3, 'Later'));
  const stub = server([
    ['GET', /\/api\/order\/0000000000001$/, detail],
    ['GET', /\/api\/order\/0000000000001\/lines\/lines\?limit=100&offset=100$/, page],
    ['GET', /\/api\/customer\/0000000000001$/, { ...responses.customer, status: 200 }],
    ['GET', /\/api\/product\/0000000000001$/, { ...responses.product, status: 200 }],
    ['POST', /\/api\/_draft\/order$/, 'draft'],
  ]);
  const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema, model: 'order', today: () => '2026-11-01' });
  stores.push(store);
  return { store, sent: stub.sent };
}

describe('section rows', () => {
  it('recognises a section row by its shape', () => {
    expect(isSectionRow({ id: 'x', section: 'Goods' })).toBe(true);
    expect(isSectionRow({ section: 'Goods', _key: 'k' })).toBe(true);
    expect(isSectionRow({ id: 'x', section: 'Goods', qty: '1' })).toBe(false);
    expect(isSectionRow({ id: 'x', qty: '1' })).toBe(false);
  });

  it('leaves section rows out of the editable rows and the page offsets, and out of change sets', async () => {
    const { store, sent } = sectioned();
    await store.open(id(1));
    await store.idle();
    const lines = store.table('lines');
    // 120 real lines, the same as without sections; the page is asked from offset 100 of the real rows.
    expect(lines.rows.peek()).toHaveLength(120);
    expect(sent.map(item => item.url)).toContain('/api/order/0000000000001/lines/lines?limit=100&offset=100');
    const sectionIds = [id(901), id(902), id(903)];
    expect(lines.rows.peek().some(row => sectionIds.includes(row.id ?? ''))).toBe(false);
    expect(store.field('line_count').value.peek()).toBe(120);
    store.field('title').set('Renamed');
    expect(buildChanges(store.root)).toEqual({ title: 'Renamed' });
  });
});
