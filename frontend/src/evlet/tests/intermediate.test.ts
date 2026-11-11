// A hidden unstored intermediate (tests/test_hidden_intermediates.py writes this schema): the server leaves it out of
// the model's fields but ships its formula in the plan, so the browser works out every formula on the row, including
// those that read it. The bill's currency code is column=False: out of the default columns, still read for display.
import { afterEach, describe, expect, it } from 'vitest';
import fixture from './intermediate-fixture.json';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import type { AppSchema, ModelSchema } from '../model/schema';
import { listFields, type ScreenModel } from '../app/meta';
import { gridPrefs } from '../ui/table';
import { currencyFor, currencyIn } from '../ui/fields';
import { ChildTable } from '../ui/childtable';
import { cleanup, render } from './ui-helpers';

const schema = { version: 1, ui_version: 1, ...(fixture as any) } as AppSchema;
const model = (name: string) => schema.models.find(item => item.name === name) as ModelSchema;
const offline = (async () => { throw new Error('No requests expected'); }) as typeof globalThis.fetch;
const stores: RecordStore[] = [];
afterEach(() => { cleanup(); stores.splice(0).forEach(store => store.dispose()); });

async function newBill() {
  const store = new RecordStore({ api: createClient({ fetch: offline }), schema, model: 'bill', today: () => '2026-11-11' });
  stores.push(store);
  await store.create({});
  return store;
}

describe('hidden unstored intermediates', () => {
  it('are not fields of the row, yet every formula on the row works out, those that read them too', async () => {
    expect(model('bill_items').fields.map(field => field.name)).not.toContain('tax_exact');
    const store = await newBill();
    const row = store.table('items').add({ qty: '2', price: '10' });
    expect(row.fields.has('tax_exact')).toBe(false);
    expect(row.field('net').value.peek()).toBe('20.00');
    expect(row.field('gross').value.peek()).toBe('21.00');
    expect(store.field('tax_total').value.peek()).toBe('1.00');
    row.field('qty').set('3');
    expect(row.field('gross').value.peek()).toBe('31.50');
    expect(store.field('tax_total').value.peek()).toBe('1.50');
    expect(store.field('net_total').value.peek()).toBe('30.00');
  });
});

describe('column=False', () => {
  it('keeps a currency source out of the default list and Table columns, and amounts still show in its currency', async () => {
    const bill = model('bill') as unknown as ScreenModel;
    expect(model('bill').fields.find(field => field.name === 'code')?.column).toBe(false);
    expect(listFields(bill).map(field => field.name)).not.toContain('code');
    expect(gridPrefs(model('bill_items').fields).hidden).toEqual(['code']);
    const store = await newBill();
    const row = store.table('items').add({ qty: '1', price: '1.5' });
    expect(currencyFor(row.field('net'), null)()).toBe('KWD');
    const net = model('bill').fields.find(field => field.name === 'net_total')!;
    expect(currencyIn(net, name => ({ code: 'USD' } as Record<string, unknown>)[name], null)).toBe('USD');
  });

  it('starts hidden in a Table grid, the Columns menu shows it, and Reset columns hides it again', async () => {
    const store = await newBill();
    store.table('items').add({ qty: '1', price: '2' });
    const host = render(() => ChildTable({ table: store.table('items') }));
    const headers = () => [...host.querySelectorAll('thead th[data-key]')].map(th => th.getAttribute('data-key'));
    expect(headers()).not.toContain('code');
    expect(headers()).toContain('net');
    [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes('Columns'))!.click();
    const menu = document.querySelector('.cols-pop')!;
    [...menu.querySelectorAll<HTMLButtonElement>('button')].find(button => button.getAttribute('aria-label') === 'Show Code')!.click();
    expect(headers()).toContain('code');
    [...menu.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent!.includes('Reset columns'))!.click();
    expect(headers()).not.toContain('code');
  });

  it('a list still shows a field the model names in Meta.list_fields', () => {
    const bill = ({ ...model('bill'), list_fields: ['title', 'code'] }) as unknown as ScreenModel;
    expect(listFields(bill).map(field => field.name)).toEqual(['title', 'code']);
  });
});
