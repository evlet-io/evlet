// Reactive help text: help_text that names fields ("per {uom}") is filled in from the record's values in the browser,
// as a small subtext line under a Table row's cell and as the help under a form field. It follows the values with no
// request, and reads empty while a named value is empty. Plain help text stays out of the cells.
import { afterEach, describe, expect, it } from 'vitest';
import { cell, effect, scope } from '../reactive';
import { ChildTable } from '../ui/childtable';
import { BaseField, helpParts, helpTextOf, type LinkSource } from '../ui/fields';
import { RecordStore } from '../model/record';
import type { AppSchema, FieldSchema } from '../model/schema';
import { backend, cleanup, render, schema } from './ui-helpers';

afterEach(cleanup);

/** The recorded order schema with a uom choice on its lines and help text on rate, qty and the order's title. */
function withHelp(): AppSchema {
  const copy = structuredClone(schema) as AppSchema;
  const lines = copy.models.find(model => model.name === 'order_lines')!;
  const uom: FieldSchema = { name: 'uom', type: 'select', label: 'Unit', required: false, read_only: false, choices: ['box', 'piece'], help_text: '' };
  lines.fields.splice(1, 0, uom);
  const rate = lines.fields.find(field => field.name === 'rate')!;
  Object.assign(rate, { help_text: 'per {uom} for {parent.title}', help_fields: ['uom', 'parent.title'] });
  Object.assign(lines.fields.find(field => field.name === 'qty')!, { help_text: 'How many', help_fields: undefined });
  const order = copy.models.find(model => model.name === 'order')!;
  Object.assign(order.fields.find(field => field.name === 'remark')!, { help_text: 'About {title}', help_fields: ['title'] });
  return copy;
}

async function order() {
  const { api } = backend();
  const store = new RecordStore({ api, schema: withHelp(), model: 'order', today: () => '2026-11-01' });
  await store.create({ title: 'Spring', children: { lines: [{ _key: 'a', qty: '2', rate: '4.00', uom: 'box' }, { _key: 'b', qty: '1', rate: '9.00' }] } });
  return store;
}

const subtext = (host: Element, row: number, col: string) =>
  host.querySelectorAll('tbody tr[data-key]')[row].querySelector<HTMLElement>(`td[data-col="${col}"] .g-sub`);

describe('help text templates', () => {
  it('split into text and field names, with {{ and }} as braces', () => {
    expect(helpParts('per {uom} in {parent.currency}')).toEqual(['per ', { ref: 'uom' }, ' in ', { ref: 'parent.currency' }]);
    expect(helpParts('JSON {{"a": 1}} for {item}')).toEqual(['JSON {"a": 1} for ', { ref: 'item' }]);
  });
});

describe('a Table cell subtext', () => {
  it('shows the filled-in help under the value and follows the row and the record as they change', async () => {
    const store = await order();
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['uom', 'qty', 'rate'] }));
    const first = subtext(host, 0, 'rate')!;
    expect(first.textContent).toBe('per Box for Spring');
    expect(first.hidden).toBe(false);
    // The second row has no uom yet: no "per " on its own.
    expect(subtext(host, 1, 'rate')!.hidden).toBe(true);
    const [a, b] = store.table('lines').rows.peek();
    a.field('uom').set('piece');
    expect(first.textContent).toBe('per Piece for Spring');
    b.field('uom').set('box');
    expect(subtext(host, 1, 'rate')!.textContent).toBe('per Box for Spring');
    expect(subtext(host, 1, 'rate')!.hidden).toBe(false);
    store.root.field('title').set('Autumn');
    expect(first.textContent).toBe('per Piece for Autumn');
    // The value still reads above it, in the same cell.
    expect(first.closest('td')!.querySelector('.g-cv-v')!.textContent).toContain('4.00');
  });

  it('re-runs only when a field it names changes', async () => {
    const store = await order();
    const row = store.table('lines').rows.peek()[0], help = helpTextOf(row.field('rate'));
    let runs = 0;
    const dispose = scope(() => effect(() => { help(); runs += 1; }));
    row.field('qty').set('7');
    row.field('rate').set('5.00');
    expect(runs).toBe(1);
    row.field('uom').set('piece');
    expect(runs).toBe(2);
    dispose();
  });

  it('leaves plain help text out of the cells', async () => {
    const store = await order();
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['uom', 'qty', 'rate'] }));
    expect(subtext(host, 0, 'qty')).toBeNull();
    expect(host.querySelector('td[data-col="qty"]')!.textContent).not.toContain('How many');
  });
});

describe('a form field help', () => {
  it('fills in the record values under the input', async () => {
    const store = await order();
    const host = render(() => BaseField({ field: store.root.field('remark') }));
    const help = host.querySelector<HTMLElement>('.fld-help')!;
    expect(help.textContent).toBe('About Spring');
    store.root.field('title').set('');
    expect(help.hidden).toBe(true);
    store.root.field('title').set('Winter');
    expect(help.textContent).toBe('About Winter');
    expect(help.hidden).toBe(false);
  });

  it('reads a Link by its title', async () => {
    const store = await order();
    const row = store.table('lines').rows.peek()[0];
    const titles = cell<string | null>(null);
    const links = { search: async () => [], title: () => titles } as unknown as LinkSource;
    const product = row.field('rate');
    Object.assign(product.meta, { help_text: 'of {product}', help_fields: ['product'] });
    row.field('product').set('0000000000001');
    const help = helpTextOf(product, links);
    expect(help()).toBe('');
    titles.set('Support hours');
    expect(help()).toBe('of Support hours');
  });
});
