import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import { SchemaIndex, type AppSchema, type FieldSchema } from '../model/schema';
import { apiLinks, BaseField, currencyIn, displayValue, localField, setLinkSource } from '../ui/fields';
import { InlineValue } from '../ui/inline';
import { addMonths, Calendar, DateRangePanel, Dropdown, rangePresets, rangeText, type DateRange } from '../ui/pickers';
import { cell } from '../reactive';
import { server } from './server';
import { backend, choiceValues, choose, cleanup, key, pick, render } from './ui-helpers';

afterEach(() => { setLinkSource(null); cleanup(); });

const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const panel = () => document.querySelector<HTMLElement>('.pop')!;
const options = () => [...document.querySelectorAll<HTMLElement>('.pop [role="option"]')];
const button = (label: string) => [...document.querySelectorAll<HTMLButtonElement>('.pop button')].find(item => item.textContent?.trim() === label)!;
const day = (iso: string) => document.querySelector<HTMLButtonElement>(`.pop [data-day="${iso}"]`)!;

describe('Select', () => {
  const choices = ['draft', 'sent', 'paid'];

  it('opens a list with the current choice ticked and picks with the keyboard', () => {
    const field = localField({ name: 'status', type: 'select', label: 'Status', choices }, 'draft');
    const host = render(() => BaseField({ field }));
    const control = host.querySelector<HTMLButtonElement>('button.in-select')!;
    expect(host.querySelector('label')!.getAttribute('for')).toBe(control.id);
    expect(control.textContent).toContain('Draft');
    key(control, 'ArrowDown');
    expect(control.getAttribute('aria-expanded')).toBe('true');
    expect(options().map(option => option.textContent?.trim())).toEqual(['Draft', 'Sent', 'Paid']);
    expect(options()[0].getAttribute('aria-selected')).toBe('true');
    expect(options()[0].querySelector('svg')).not.toBeNull();
    const list = document.querySelector<HTMLElement>('.pop [role="listbox"]')!;
    key(list, 'ArrowDown');
    expect(list.getAttribute('aria-activedescendant')).toBe(options()[1].id);
    key(list, 'Enter');
    expect(field.value.peek()).toBe('sent');
    expect(document.querySelector('.pop')).toBeNull();
    expect(control.textContent).toContain('Sent');
  });

  it('jumps to a choice by its first letter and offers Clear only when the field is optional', () => {
    const field = localField({ name: 'status', type: 'select', label: 'Status', choices }, 'draft');
    const host = render(() => BaseField({ field }));
    host.querySelector<HTMLButtonElement>('button.in-select')!.click();
    const list = document.querySelector<HTMLElement>('.pop [role="listbox"]')!;
    key(list, 'p');
    key(list, 'Enter');
    expect(field.value.peek()).toBe('paid');
    host.querySelector<HTMLButtonElement>('button.in-select')!.click();
    button('Clear').click();
    expect(field.value.peek()).toBeNull();
    expect(host.querySelector('button.in-select')!.textContent).toContain('Choose one');
    const required = localField({ name: 'kind', type: 'select', label: 'Kind', choices, required: true }, 'draft');
    const other = render(() => BaseField({ field: required }));
    other.querySelector<HTMLButtonElement>('button.in-select')!.click();
    expect(button('Clear')).toBeUndefined();
  });

  it('searches a long list and lists a retired choice only for the record that holds it', () => {
    const many = ['aed', 'sar', 'usd', 'eur', 'inr', 'gbp', 'omr', 'kwd', 'bhd', 'qar'];
    const field = localField({ name: 'currency', type: 'select', label: 'Currency', choices: many, retired_choices: ['old'] }, 'old');
    const host = render(() => BaseField({ field }));
    host.querySelector<HTMLButtonElement>('button.in-select')!.click();
    const search = document.querySelector<HTMLInputElement>('.pop input[role="combobox"]')!;
    expect(search).not.toBeNull();
    expect(options().at(-1)!.textContent).toContain('Old (retired)');
    expect(options().at(-1)!.getAttribute('aria-disabled')).toBe('true');
    search.value = 'om';
    search.dispatchEvent(new Event('input'));
    expect(options().map(option => option.textContent?.trim())).toEqual(['Omr']);
    key(search, 'Enter');
    expect(field.value.peek()).toBe('omr');
    host.querySelector<HTMLButtonElement>('button.in-select')!.click();
    expect(options().some(option => option.textContent?.includes('retired'))).toBe(false);
  });

  it('closes on Escape and gives focus back to the field', () => {
    const field = localField({ name: 'status', type: 'select', label: 'Status', choices }, null);
    const host = render(() => BaseField({ field }));
    const control = host.querySelector<HTMLButtonElement>('button.in-select')!;
    control.focus();
    control.click();
    key(document.querySelector<HTMLElement>('.pop [role="listbox"]')!, 'Escape');
    expect(document.querySelector('.pop')).toBeNull();
    expect(document.activeElement).toBe(control);
    expect(field.value.peek()).toBeNull();
  });

  it('saves at once when edited in place', async () => {
    const { store, sent } = backend();
    await store.open(1);
    const host = render(() => InlineValue({ field: store.field('stage') }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    pick(host.querySelector<HTMLElement>('.in-select')!, 'Lost');
    await flush();
    expect(sent.find(item => item.method === 'PATCH')!.body.stage).toBe('lost');
  });
});

describe('Dropdown', () => {
  it('shows the chosen label, lists choices by value and reports a pick', () => {
    const value = cell<string | null>('8');
    const picked: Array<string | null> = [];
    const host = render(() => Dropdown({ label: 'Session length', ariaLabel: 'Session length', value: () => value.get(),
      choices: () => ['1', '4', '8'].map(hours => ({ value: hours, label: `${hours} hours` })), onChange: next => { picked.push(next); value.set(next); } }));
    const button = host.querySelector<HTMLButtonElement>('button.in-select')!;
    expect(button.textContent).toContain('8 hours');
    expect(choiceValues(button)).toEqual(['1', '4', '8']);
    choose(button, '4');
    expect(picked).toEqual(['4']);
    expect(button.textContent).toContain('4 hours');
    // Picking the current choice again changes nothing.
    choose(button, '4');
    expect(picked).toEqual(['4']);
    expect(document.querySelector('select')).toBeNull();
  });

  it('stays closed while disabled', () => {
    const host = render(() => Dropdown({ label: 'Role', value: () => 'admin', disabled: true, choices: () => [{ value: 'admin', label: 'admin' }], onChange: () => {} }));
    host.querySelector<HTMLButtonElement>('button')!.click();
    expect(document.querySelector('.pop')).toBeNull();
  });
});

describe('date pickers', () => {
  it('opens a calendar at the chosen month and writes a picked day as DD-MM-YYYY', () => {
    const field = localField({ name: 'posting_date', type: 'date', label: 'Posting date' }, '2026-11-01');
    const host = render(() => BaseField({ field }));
    const input = host.querySelector<HTMLInputElement>('input')!;
    expect(input.value).toBe('01-11-2026');
    input.click();
    expect(panel().querySelector('.cal-title')!.textContent).toBe('November 2026');
    expect(day('2026-11-01').getAttribute('aria-selected')).toBe('true');
    expect(day('2026-11-01').getAttribute('aria-label')).toBe('Sunday, 1 November 2026');
    day('2026-11-15').click();
    expect(field.value.peek()).toBe('2026-11-15');
    expect(input.value).toBe('15-11-2026');
    expect(document.querySelector('.pop')).toBeNull();
  });

  it('moves by day, week and month with the keyboard and picks with Enter', async () => {
    const field = localField({ name: 'due', type: 'date', label: 'Due' }, '2026-01-31');
    const host = render(() => BaseField({ field }));
    const input = host.querySelector<HTMLInputElement>('input')!;
    input.focus();
    key(input, 'ArrowDown');
    await flush();
    expect((document.activeElement as HTMLElement).dataset.day).toBe('2026-01-31');
    const grid = panel().querySelector<HTMLElement>('[role="grid"]')!;
    key(document.activeElement as HTMLElement, 'PageDown');
    await flush();
    // 31 January plus a month stays in February.
    expect((document.activeElement as HTMLElement).dataset.day).toBe('2026-02-28');
    expect(panel().querySelector('.cal-title')!.textContent).toBe('February 2026');
    key(panel().querySelector<HTMLElement>('[role="grid"]')!, 'ArrowUp');
    key(panel().querySelector<HTMLElement>('[role="grid"]')!, 'ArrowRight');
    await flush();
    expect((document.activeElement as HTMLElement).dataset.day).toBe('2026-02-22');
    (document.activeElement as HTMLButtonElement).click();
    expect(field.value.peek()).toBe('2026-02-22');
    expect(grid.isConnected).toBe(false);
  });

  it('switches to months and years, and keeps typing possible', () => {
    const field = localField({ name: 'due', type: 'date', label: 'Due' }, null);
    const host = render(() => BaseField({ field }));
    const input = host.querySelector<HTMLInputElement>('input')!;
    host.querySelector<HTMLButtonElement>('.in-pick-btn')!.click();
    panel().querySelector<HTMLButtonElement>('.cal-title')!.click();
    panel().querySelector<HTMLButtonElement>('.cal-title')!.click();
    [...panel().querySelectorAll<HTMLButtonElement>('.cal-pick button')].find(item => item.textContent === '2025')!.click();
    [...panel().querySelectorAll<HTMLButtonElement>('.cal-pick button')].find(item => item.textContent === 'Mar')!.click();
    expect(panel().querySelector('.cal-title')!.textContent).toBe('March 2025');
    host.querySelector<HTMLButtonElement>('.in-pick-btn')!.click();
    expect(document.querySelector('.pop')).toBeNull();
    input.value = '3-4-2027';
    input.dispatchEvent(new Event('change'));
    expect(field.value.peek()).toBe('2027-04-03');
    input.value = '31-02-2027';
    input.dispatchEvent(new Event('change'));
    expect(field.error.peek()).toBe('Enter a date as DD-MM-YYYY');
  });

  it('offers Today, and Clear only for optional dates', () => {
    const field = localField({ name: 'due', type: 'date', label: 'Due' }, '2020-01-01');
    const host = render(() => BaseField({ field }));
    host.querySelector<HTMLInputElement>('input')!.click();
    button('Today').click();
    expect(field.value.peek()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(field.value.peek()).not.toBe('2020-01-01');
    host.querySelector<HTMLInputElement>('input')!.click();
    button('Clear').click();
    expect(field.value.peek()).toBeNull();
    const required = localField({ name: 'on', type: 'date', label: 'On', required: true }, '2026-11-01');
    render(() => BaseField({ field: required })).querySelector<HTMLInputElement>('input')!.click();
    expect(button('Clear')).toBeUndefined();
  });

  it('picks a date-time in GST with the time steppers and stores UTC', () => {
    const field = localField({ name: 'received_at', type: 'datetime', label: 'Received at' }, '2026-11-01T09:20:00Z');
    const host = render(() => BaseField({ field }));
    const input = host.querySelector<HTMLInputElement>('input')!;
    expect(input.value).toBe('01-11-2026 13:20');
    input.click();
    const [hours, minutes] = panel().querySelectorAll<HTMLElement>('[role="spinbutton"]');
    expect([hours.textContent, minutes.textContent]).toEqual(['13', '20']);
    day('2026-11-03').click();
    expect(field.value.peek()).toBe('2026-11-03T09:20:00Z');
    key(hours, 'ArrowUp');
    expect(field.value.peek()).toBe('2026-11-03T10:20:00Z');
    panel().querySelector<HTMLButtonElement>('[aria-label="More minutes"]')!.click();
    expect(input.value).toBe('03-11-2026 14:25');
    button('Done').click();
    expect(document.querySelector('.pop')).toBeNull();
    expect(field.value.peek()).toBe('2026-11-03T10:25:00Z');
  });

  it('picks a time and a duration with steppers and common values', () => {
    const time = localField({ name: 'meeting', type: 'time', label: 'Meeting' }, '14:00:00');
    const duration = localField({ name: 'spent', type: 'duration', label: 'Spent' }, 'PT45M');
    const host = render(() => [BaseField({ field: time }), BaseField({ field: duration })]);
    host.querySelector<HTMLInputElement>('[data-field="meeting"] input')!.click();
    const hours = panel().querySelector<HTMLElement>('[role="spinbutton"]')!;
    expect(hours.getAttribute('aria-valuenow')).toBe('14');
    key(hours, 'End');
    expect(time.value.peek()).toBe('23:00:00');
    key(hours, 'ArrowUp');
    expect(time.value.peek()).toBe('00:00:00');
    button('09:00').click();
    expect(time.value.peek()).toBe('09:00:00');
    button('Done').click();
    host.querySelector<HTMLInputElement>('[data-field="spent"] input')!.click();
    expect(button('45m').classList.contains('on')).toBe(true);
    button('1h 30m').click();
    expect(duration.value.peek()).toBe('PT1H30M');
    expect(host.querySelector<HTMLInputElement>('[data-field="spent"] input')!.value).toBe('1h 30m');
  });

  it('saves a picked day at once when edited in place', async () => {
    const field = localField({ name: 'due', type: 'date', label: 'Due' }, '2026-11-01');
    const saves: unknown[] = [];
    const host = render(() => InlineValue({ field, save: () => { saves.push(field.value.peek()); return true; }, confirm: false }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    host.querySelector<HTMLInputElement>('input')!.click();
    day('2026-11-20').click();
    await flush();
    expect(saves).toEqual(['2026-11-20']);
    expect(host.querySelector('input')).toBeNull();
  });
});

describe('date ranges', () => {
  it('names presets from today', () => {
    const presets = Object.fromEntries(rangePresets('2026-11-04').map(preset => [preset.label, preset.range]));
    expect(presets['This week']).toEqual(['2026-11-02', '2026-11-08']);
    expect(presets['Last month']).toEqual(['2026-10-01', '2026-10-31']);
    expect(presets['This quarter']).toEqual(['2026-10-01', '2026-12-31']);
    expect(presets['Last 7 days']).toEqual(['2026-10-29', '2026-11-04']);
    expect(rangeText(['2026-11-01', '2026-11-30'])).toBe('01-11-2026 to 30-11-2026');
    expect(addMonths('2024-01-31', 1)).toBe('2024-02-29');
  });

  it('picks two days in either order and applies them', () => {
    const applied: Array<DateRange | null> = [];
    render(() => DateRangePanel({ value: () => ['2026-11-01', '2026-11-30'], onApply: range => applied.push(range) }));
    expect(document.querySelector('[data-day="2026-11-15"]')!.classList.contains('between')).toBe(true);
    const at = (iso: string) => document.querySelector<HTMLButtonElement>(`[data-day="${iso}"]`)!;
    at('2026-11-20').click();
    expect(document.querySelector('.range-note')!.textContent).toBe('From 20-11-2026: choose the last day');
    at('2026-11-10').click();
    expect(document.querySelector('.range-note')!.textContent).toBe('10-11-2026 to 20-11-2026');
    [...document.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === 'Apply')!.click();
    expect(applied).toEqual([['2026-11-10', '2026-11-20']]);
  });

  it('marks today and a chosen day in a calendar', () => {
    const chosen = cell<string | null>('2026-11-04');
    render(() => Calendar({ value: () => chosen.get(), onPick: next => chosen.set(next) }));
    expect(document.querySelector('[aria-current="date"]')).not.toBeNull();
    document.querySelector<HTMLButtonElement>('[data-day="2026-11-05"]')!.click();
    expect(chosen.peek()).toBe('2026-11-05');
    expect(document.querySelector('[data-day="2026-11-05"]')!.getAttribute('aria-selected')).toBe('true');
  });
});

describe('side boxes', () => {
  const meta = (name: string, type: string, extra: Partial<FieldSchema> = {}): FieldSchema => ({ name, type, label: name, required: false, read_only: false, ...extra });
  const schema: AppSchema = { version: 1, ui_version: 1, models: [
    { name: 'company', label: 'Company', plural: 'Companies', title_field: 'name', list_fields: ['name'],
      fields: [meta('name', 'text'), meta('company_currency', 'select', { choices: ['AED', 'USD'] })] },
    { name: 'bill', label: 'Bill', plural: 'Bills', title_field: 'id', list_fields: [], fields: [
      meta('code', 'select', { choices: ['AED', 'SAR', 'USD'] }),
      meta('company', 'link', { target: 'company' }),
      meta('own', 'currency', { scale: 2, currency_source: { field: 'code' } }),
      meta('fetched', 'currency', { scale: 2, currency_source: { link: 'company', field: 'company_currency', model: 'company' } }),
      meta('fixed', 'currency', { scale: 2, currency: 'SAR' }),
      meta('rate', 'percent', { scale: 4, minimum: '0', maximum: '100' }),
    ] },
  ] };
  const boxes = (host: HTMLElement, name: string) => [...host.querySelectorAll(`[data-field="${name}"] .in-box`)].map(box => box.textContent);

  it('shows the currency from the record, a linked record or the schema, and % for percent fields', async () => {
    const stub = server([['GET', /\/api\/company\/2$/, { status: 200, etag: null, body: { id: 2, name: 'Gulf Trading', company_currency: 'USD' } }]]);
    const api = createClient({ fetch: stub.fetch });
    const links = apiLinks(api, new SchemaIndex(schema));
    setLinkSource(links);
    const store = new RecordStore({ api, schema, model: 'bill' });
    await store.create({ code: 'AED', own: '10.00', fetched: '20.00', fixed: '30.00', rate: '5.0000' });
    const host = render(() => ['code', 'own', 'fetched', 'fixed', 'rate'].map(name => BaseField({ field: store.field(name) })));
    expect(boxes(host, 'own')).toEqual(['AED']);
    expect(boxes(host, 'fetched')).toEqual(['AED']);
    expect(boxes(host, 'fixed')).toEqual(['SAR']);
    expect(boxes(host, 'rate')).toEqual(['%']);
    pick(host.querySelector<HTMLElement>('[data-field="code"] .in-select')!, 'SAR');
    expect(boxes(host, 'own')).toEqual(['SAR']);
    store.field('company').set(2);
    await flush();
    await flush();
    expect(boxes(host, 'fetched')).toEqual(['USD']);
    // Lists read the same source from the row's values, one batched request per linked model.
    expect(currencyIn(store.field('fetched').meta, name => ({ company: 2 } as Record<string, unknown>)[name], links)).toBe('USD');
    expect(currencyIn(store.field('own').meta, name => ({ code: 'nope' } as Record<string, unknown>)[name], links)).toBe('AED');
    expect(stub.sent.filter(item => item.url.includes('/api/company')).length).toBe(1);
    store.dispose();
  });
});

describe('units at rest', () => {
  it('reads a suffix after the number', () => {
    const rate = localField({ name: 'tax_rate', type: 'decimal', label: 'Tax rate', scale: 4, suffix: '%' }, '5.0000');
    const hours = localField({ name: 'hours', type: 'integer', label: 'Hours', suffix: 'hours' }, 8);
    const host = render(() => [displayValue(rate), displayValue(hours)]);
    expect(host.textContent).toBe('5.0000%8 hours');
  });
});

