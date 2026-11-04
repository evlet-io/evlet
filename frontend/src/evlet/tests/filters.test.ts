// The list filter builder: fields from the schema (a model of 220 fields and a child table), the
// conditions each type offers, the address and server forms, and the builder on the list page.
import { afterEach, describe, expect, it } from 'vitest';
import { SchemaIndex, type FieldSchema } from '../model/schema';
import { compact, conditionsFor, decode, encode, filterFields, type FilterState } from '../app/filters';
import type { ScreenModel } from '../app/meta';
import { GroupedChoiceList } from '../ui/pickers';
import { mount } from '../dom';
import { adminRoutes, byText, click, press, recorded, screen, start, stopAll, text, typeInto, until } from './screens-helpers';
import { copy, id, type Sent } from './server';

afterEach(stopAll);

const field = (name: string, type: string, extra: Partial<FieldSchema> = {}): FieldSchema =>
  ({ name, type, label: name.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase()), required: false, read_only: false, stored: true, ...extra });
const MARKETS = ['uae', 'ksa', 'oman', 'qatar', 'bahrain', 'kuwait', 'india', 'egypt', 'jordan', 'export'];
const SPECS = Array.from({ length: 150 }, (_, index) => field(`spec_${index + 1}`, 'text'));

function itemSchema() {
  const schema = copy(screen.schema.body);
  const fields = [
    field('item_code', 'text'), field('item_name', 'text'), field('item_group', 'select', { choices: ['raw material', 'spare part', 'service'] }),
    field('standard_rate', 'currency', { currency: 'AED', scale: 2 }), field('disabled', 'boolean'), field('introduced_on', 'date'),
    field('default_customer', 'link', { target: 'customer' }), field('notes', 'longtext'), field('total', 'currency', { stored: false }),
    field('secret', 'text', { hidden: true }),
    ...MARKETS.flatMap(market => [field(`${market}_price`, 'currency', { currency: 'AED', scale: 2, label: `${market.toUpperCase()} price` }),
      field(`${market}_listed`, 'boolean'), field(`${market}_launch`, 'date'), field(`${market}_lead_days`, 'integer'), field(`${market}_channel`, 'select', { choices: ['retail', 'online'] }),
      field(`${market}_vat`, 'select', { choices: ['standard', 'exempt'] })]),
    ...SPECS,
  ];
  schema.models.push({ name: 'item', label: 'Item', plural: 'Items', title_field: 'item_code', fields, list_fields: ['item_code', 'item_name', 'standard_rate'],
    search_fields: ['item_code'], permissions: { create: false, update: false, delete: false },
    children: [{ name: 'prices', model: 'item_prices', label: 'Prices', link: 'evlet_owner', filterable: true }] });
  schema.models.push({ name: 'item_prices', label: 'Price', plural: 'Prices', title_field: 'id', child_only: true, list_fields: [], permissions: { create: false, update: false, delete: false },
    fields: [field('evlet_owner', 'link', { target: 'item', hidden: true }), field('price_list', 'select', { choices: ['wholesale', 'export'] }),
      field('customer', 'link', { target: 'customer' }), field('rate', 'currency', { currency: 'AED', scale: 2 }), field('min_qty', 'integer')] });
  return schema;
}
const index = () => new SchemaIndex(itemSchema());
const item = () => index().model('item') as unknown as ScreenModel;
const keys = () => new Map(filterFields(index(), item()).map(entry => [entry.key, entry]));

describe('filter fields and conditions', () => {
  it('lists the readable stored fields, then each child table, with more than 200 in all', () => {
    const fields = filterFields(index(), item());
    expect(fields.length).toBeGreaterThan(200);
    expect(fields.map(entry => entry.key)).not.toEqual(expect.arrayContaining(['secret', 'total', 'prices.evlet_owner']));
    expect(fields.slice(0, 3).map(entry => entry.label)).toEqual(['Item code', 'Item name', 'Item group']);
    expect(fields.slice(-4).map(entry => [entry.group, entry.label])).toEqual([['Prices', 'Prices → Price list'], ['Prices', 'Prices → Customer'],
      ['Prices', 'Prices → Rate'], ['Prices', 'Prices → Min qty']]);
  });

  it('offers conditions by type, less any the server does not take for the field', () => {
    const ops = (meta: FieldSchema) => conditionsFor(meta).map(item => item.label);
    expect(ops(field('a', 'text'))).toEqual(['equals', 'not equals', 'like', 'not like', 'is set', 'is not set', 'in', 'not in']);
    expect(ops(field('a', 'currency'))).toEqual(['=', '≠', '>', '≥', '<', '≤', 'between', 'is set', 'is not set']);
    expect(ops(field('a', 'date'))).toEqual(['on', 'before', 'after', 'between', 'timespan', 'is set', 'is not set']);
    expect(ops(field('a', 'link'))).toEqual(['equals', 'not equals', 'in', 'is set', 'is not set']);
    expect(ops(field('a', 'select'))).toEqual(['in', 'not in', 'is set', 'is not set']);
    expect(ops(field('a', 'boolean'))).toEqual(['is yes', 'is no']);
    expect(ops(field('a', 'text', { operators: ['eq', 'icontains', 'isnull'] }))).toEqual(['equals', 'like', 'is set', 'is not set']);
    expect(ops(field('a', 'reference'))).toEqual([]);
  });

  it('keeps rows in the address and sends server conditions, child rows grouped by table', () => {
    const state: FilterState = { matches: { prices: 'none' }, rows: [
      { id: 1, field: 'item_group', op: 'in', value: ['service'] },
      { id: 2, field: 'introduced_on', op: 'timespan', value: 'this_month' },
      { id: 3, field: 'prices.price_list', op: 'in', value: ['wholesale'] },
      { id: 4, field: 'prices.rate', op: 'gt', value: '50' },
      { id: 5, field: 'brand', op: 'eq', value: null },
      { id: 6, field: 'item_name', op: 'ne', value: 'Bolt' },
      { id: 7, field: 'notes', op: 'set', value: null },
      { id: 8, field: 'disabled', op: 'no', value: null },
    ] };
    const map = keys();
    const address = encode(map, state, false);
    expect(address).toEqual([['item_group', 'in', ['service']], ['introduced_on', 'timespan', 'this_month'],
      { child: 'prices', match: 'none', where: [['price_list', 'in', ['wholesale']], ['rate', 'gt', '50']] },
      ['item_name', 'ne', 'Bolt'], ['notes', 'set', null], ['disabled', 'no', null]]);
    const server = encode(map, state, true);
    expect(server[1]).toEqual(['introduced_on', 'between', [expect.stringMatching(/^\d{4}-\d{2}-01$/), expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/)]]);
    expect(server.slice(3)).toEqual([['item_name', 'nin', ['Bolt']], ['notes', 'isnull', false], ['disabled', 'eq', false]]);
    const back = decode(map, JSON.stringify(address));
    expect(back.matches).toEqual({ prices: 'none' });
    expect(back.rows.map(row => [row.field, row.op, row.value])).toEqual(state.rows.filter(row => row.value !== null || ['set', 'no'].includes(row.op)).map(row => [row.field, row.op, row.value]));
    // Links made before the builder: {"field": value}, and a date range object.
    expect(decode(map, '{"item_group":"service","disabled":true,"introduced_on":{"gte":"2026-11-01","lte":"2026-11-30"}}').rows.map(row => [row.field, row.op, row.value]))
      .toEqual([['item_group', 'in', ['service']], ['disabled', 'yes', null], ['introduced_on', 'between', ['2026-11-01', '2026-11-30']]]);
    expect(decode(map, 'not json').rows).toEqual([]);
    expect(decode(map, '[["secret","eq","x"],["nope","eq",1],["item_code","between",[1]]]').rows).toEqual([]);
    expect(compact([['stage', 'eq', 'open'], ['kind', 'eq', 'b']])).toEqual({ stage: 'open', kind: 'b' });
    expect(compact([['stage', 'eq', 'open'], ['stage', 'eq', 'won']])).toHaveLength(2);
    expect(compact([['rate', 'gt', 1]])).toEqual([['rate', 'gt', 1]]);
  });

  it('searches 220 fields by words in any order and draws at most 100 at a time', () => {
    const fields = filterFields(index(), item());
    let picked = '';
    const host = document.createElement('div');
    document.body.append(host);
    const dispose = mount(host, () => GroupedChoiceList({ choices: fields.map(entry => ({ value: entry.key, label: entry.label, group: entry.group, hint: entry.meta.type })),
      current: () => null, onPick: value => { picked = value; }, label: 'Field' }));
    const options = () => [...host.querySelectorAll('[role="option"]')].map(text);
    expect(options()).toHaveLength(100);
    expect(text(host.querySelector('[role="status"]'))).toBe(`${fields.length - 100} more: type to narrow`);
    const input = host.querySelector('input')!;
    const began = performance.now();
    typeInto(input, 'price uae');
    expect(options()).toEqual(['UAE pricecurrency']);
    typeInto(input, 'rate prices');
    expect(options()).toEqual(['Prices → Ratecurrency']);
    expect(text(host.querySelector('[role="group"] .mh'))).toBe('Prices');
    typeInto(input, 'launch');
    expect(options()).toHaveLength(10);
    press(input, 'ArrowDown');
    press(input, 'Enter');
    expect(picked).toBe('ksa_launch');
    expect(performance.now() - began).toBeLessThan(500);
    typeInto(input, 'zzz');
    expect(text(host.querySelector('[role="status"]'))).toBe('No matches');
    dispose();
  });
});

const query = (sent: Sent) => Object.fromEntries(new URL(sent.url, 'http://x').searchParams);
const itemCalls = (sent: Sent[]) => sent.filter(item => item.method === 'GET' && /\/api\/item\?/.test(item.url)).map(query);
const rows = [{ id: 1, item_code: 'A-100', item_name: 'Steel bolt', standard_rate: '12.50' }, { id: 2, item_code: 'B-200', item_name: 'Copper wire', standard_rate: '80.00' }];
const routes = () => [
  ['GET', /\/api\/auth\/session$/, recorded.session],
  ['GET', /\/api\/schema$/, { status: 200, etag: null, body: itemSchema() }],
  ['GET', /\/api\/item\?/, (sent: Sent) => {
    const filters = query(sent).filters;
    const items = filters ? rows.slice(0, 1) : rows;
    return { status: 200, etag: null, body: { items, total: items.length, limit: 50, offset: 0 } };
  }],
  ...adminRoutes().slice(2),
] as any[];

async function chooseField(host: HTMLElement, words: string) {
  // A new row opens its field search by itself.
  await until(() => expect(host.querySelector('.fb-row:last-child .fb-field, .fb-group .fb-row .fb-field')).not.toBeNull());
  await until(() => expect(document.querySelector('.field-pop')).not.toBeNull());
  await until(() => expect(document.querySelector('.field-pop input')).not.toBeNull());
  typeInto(document.querySelector<HTMLInputElement>('.field-pop input')!, words);
  press(document.querySelector<HTMLInputElement>('.field-pop input')!, 'Enter');
}
async function chooseCondition(host: HTMLElement, label: string) {
  click([...host.querySelectorAll('.fb-op')].pop()!);
  click(byText(document.body, '.select-pop [role="option"]', label));
}

describe('the filter builder on a list', () => {
  it('adds rows with the field search, sends them to the server, shows chips and keeps them in the address', async () => {
    const { host, sent } = start(routes(), '/app/item');
    await until(() => expect(host.querySelectorAll('tbody tr[data-key]')).toHaveLength(2));
    const button = byText(host, '.ph-actions button', /^Filter/)!;
    click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    // The first row opens its field search at once.
    await chooseField(host, 'standard rate');
    await until(() => expect(text(host.querySelector('.fb-field'))).toBe('Standard rate'));
    await chooseCondition(host, '>');
    const value = host.querySelector<HTMLInputElement>('.fb-value input')!;
    typeInto(value, '20');
    value.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(JSON.parse(itemCalls(sent).pop()!.filters)).toEqual([['standard_rate', 'gt', '20.00']]));
    await until(() => expect(text(host.querySelector('.filter-chip'))).toBe('Standard rate > AED 20.00'));
    expect(text(button.querySelector('.badge'))).toBe('1');

    // A child table row: no price row is wholesale.
    click(byText(host, '.fb-foot button', 'Add filter'));
    await chooseField(host, 'prices list');
    await until(() => expect(host.querySelector('.fb-group')).not.toBeNull());
    // Choice fields tick their values: "in" is the first condition.
    click(byText(host, '.fb-group .fb-value .chip', 'Wholesale'));
    click(host.querySelector('.fb-match'));
    click(byText(document.body, '.select-pop [role="option"]', 'No Prices row'));
    await until(() => expect(JSON.parse(itemCalls(sent).pop()!.filters)).toEqual([['standard_rate', 'gt', '20.00'],
      { child: 'prices', match: 'none', where: [['price_list', 'in', ['wholesale']]] }]));
    await until(() => expect([...host.querySelectorAll('.filter-chip')].map(text)).toEqual(['Standard rate > AED 20.00', 'No Prices row: Price list in Wholesale']));
    await until(() => expect(JSON.parse(new URLSearchParams(location.search).get('f')!)).toEqual([['standard_rate', 'gt', '20.00'],
      { child: 'prices', match: 'none', where: [['price_list', 'in', ['wholesale']]] }]));

    // A row without a value yet is not sent and shows no chip.
    const before = itemCalls(sent).length;
    click(byText(host, '.fb-foot button', 'Add filter'));
    await chooseField(host, 'item name');
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(itemCalls(sent)).toHaveLength(before);
    expect(host.querySelectorAll('.filter-chip')).toHaveLength(2);

    // Removing a chip removes its row; Clear filters removes the rest.
    click(host.querySelector('.filter-chip button'));
    await until(() => expect(JSON.parse(itemCalls(sent).pop()!.filters)).toEqual([{ child: 'prices', match: 'none', where: [['price_list', 'in', ['wholesale']]] }]));
    click(byText(host, '.toolbar button', 'Clear filters'));
    await until(() => expect(itemCalls(sent).pop()!.filters).toBeUndefined());
    expect(host.querySelectorAll('.fb-row')).toHaveLength(0);
    await until(() => expect(location.search).toBe(''));
  });

  it('adds records to a link "in" filter through the record picker, which searches the list route', async () => {
    const { host, sent } = start([...routes(), ['GET', /\/api\/customer\?/, screen.customers_page1]], '/app/item');
    await until(() => expect(host.querySelectorAll('tbody tr[data-key]')).toHaveLength(2));
    click(byText(host, '.ph-actions button', /^Filter/));
    await chooseField(host, 'default customer');
    await chooseCondition(host, 'in');
    const pick = async (name: string) => {
      click(host.querySelector('.fb-list .in-link'));
      await until(() => expect(byText(document.body, '.link-pop [role="option"]', new RegExp(name))).not.toBeNull());
      click(byText(document.body, '.link-pop [role="option"]', new RegExp(name)));
    };
    await pick('Al Sahra Trading');
    await pick('Bayan Medical');
    await until(() => expect(JSON.parse(itemCalls(sent).pop()!.filters)).toEqual([['default_customer', 'in', [id(1), id(2)]]]));
    expect(text(host.querySelector('.toolbar .filter-chip'))).toBe('Default customer in Al Sahra Trading, Bayan Medical');
    // The picker empties itself after each record; the chosen records show as chips in the row.
    expect(text(host.querySelector('.fb-list .in-link'))).toBe('Choose default customer');
    expect([...host.querySelectorAll('.fb-list .filter-chip')].map(text)).toEqual(['Al Sahra Trading', 'Bayan Medical']);
    // The search went through the customer list route, which applies the person's permissions.
    expect(sent.some(item => /\/api\/customer\?/.test(item.url) && !/id__in/.test(decodeURIComponent(item.url)))).toBe(true);
  });

  it('opens a shared link with its filters, and the condition decides the value input', async () => {
    const f = JSON.stringify([['introduced_on', 'timespan', 'this_month'], ['disabled', 'no', null], ['default_customer', 'isnull', null],
      ['item_group', 'nin', ['service']], ['standard_rate', 'between', ['10.00', '90.00']]]);
    const { host, sent } = start(routes(), `/app/item?f=${encodeURIComponent(f)}`);
    await until(() => expect([...host.querySelectorAll('.filter-chip')].map(text)).toEqual(['Introduced on timespan This month', 'Disabled is no',
      'Item group not in Service', 'Standard rate between AED 10.00 and AED 90.00']));
    const sentFilters = JSON.parse(itemCalls(sent)[0].filters);
    expect(sentFilters[0]).toEqual(['introduced_on', 'between', [expect.any(String), expect.any(String)]]);
    expect(sentFilters.slice(1)).toEqual([['disabled', 'eq', false], ['item_group', 'nin', ['service']], ['standard_rate', 'between', ['10.00', '90.00']]]);
    click(byText(host, '.ph-actions button', /^Filter/));
    await until(() => expect(host.querySelectorAll('.fb-row')).toHaveLength(4));
    const row = (at: number) => host.querySelectorAll('.fb-row')[at];
    expect(row(0).querySelector('.fb-value button')!.textContent).toContain('This month');
    expect(row(1).querySelector('.fb-value')!.children).toHaveLength(0);
    expect(row(3).querySelectorAll('.fb-value input')).toHaveLength(2);
    // A date row offers the date conditions; changing to "is set" drops the value input.
    click(row(0).querySelector('.fb-op'));
    expect([...document.querySelectorAll('.select-pop [role="option"]')].map(text)).toEqual(['on', 'before', 'after', 'between', 'timespan', 'is set', 'is not set']);
    click(byText(document.body, '.select-pop [role="option"]', 'is set'));
    await until(() => expect(row(0).querySelector('.fb-value')!.children).toHaveLength(0));
    await until(() => expect(JSON.parse(itemCalls(sent).pop()!.filters)[0]).toEqual(['introduced_on', 'isnull', false]));
  });
});
