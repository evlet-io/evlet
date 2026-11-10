// Decimal places per currency: amounts are stored at 6 places, and every place money is shown or typed uses the
// places of its own currency (ISO 4217 with the site's changes: KWD 3, JPY 0, a client code with 5), never the
// schema scale. Covers the formatter, form inputs, Controls, Currencies and the calculation engine.
import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { mount } from '../dom';
import { canonical, CalcError, FiniteDocument, fingerprint } from '../model/finite';
import { currencyDecimals, currencyDecimalsTable, formatMoney, moneyWire, setCurrencyDecimals, setCurrencyDisplays } from '../model/format';
import { currencyInput, RecordStore } from '../model/record';
import { SchemaIndex, type AppSchema, type FieldSchema } from '../model/schema';
import { displayValue, FieldControl, localField, moneyMeta } from '../ui/fields';
import { valueText } from '../app/record-compact';
import { KINDS } from '../ui/kinds';
import { moneyChild } from '../ui/money';
import { displayOf } from '../app/meta';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { server, type Sent } from './server';
import { byText, click, stopAll, text, typeInto, until } from './screens-helpers';

const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); setCurrencyDisplays({}); setCurrencyDecimals(null); });

// As /api/schema sends it: ISO codes that do not have 2, with this site's changes (XCL is a client's own code).
const SITE = { BHD: 3, JPY: 0, KWD: 3, OMR: 3, XCL: 5 };

describe('decimals per currency in the formatter', () => {
  it('uses ISO 4217 before the schema arrives, and 2 for codes it does not list or no code', () => {
    expect(currencyDecimals('KWD')).toBe(3);
    expect(currencyDecimals('JPY')).toBe(0);
    expect(currencyDecimals('CLF')).toBe(4);
    expect(currencyDecimals('AED')).toBe(2);
    expect(currencyDecimals('XCL')).toBe(2);
    expect(currencyDecimals(null)).toBe(2);
  });

  it('formats KWD at 3, JPY at 0 and a 5-decimal code at 5, whatever the field scale', () => {
    setCurrencyDecimals(SITE);
    expect(formatMoney('1234.5', { currency: 'KWD' })).toBe('KWD 1,234.500');
    expect(formatMoney('98000.00', { currency: 'JPY' })).toBe('JPY 98,000');
    expect(formatMoney('0.12345', { currency: 'XCL' })).toBe('XCL 0.12345');
    expect(formatMoney('25200')).toBe('AED 25,200.00');
    expect(moneyChild('1.5', 'KWD')).toBe('KWD 1.500');
    // The schema scale of a Currency field is the storage scale (6): it never sets the places shown.
    const kwd: FieldSchema = { name: 'total', type: 'currency', label: 'Total', required: false, read_only: false, scale: 6, currency: 'KWD' };
    expect(KINDS.currency.format('12.5', kwd)).toBe('KWD 12.500');
    expect(KINDS.currency.format('12.5', { ...kwd, currency: undefined })).toBe('AED 12.50');
    expect(displayOf({ ...kwd, currency: 'JPY' }, '100.00')).toBe('JPY 100');
    // The site's table drops codes the admin set back to 2 and replaces ISO.
    expect(currencyDecimals('CLF')).toBe(2);
    expect(currencyDecimalsTable()).toEqual(SITE);
  });

  it('ignores entries that are not well formed', () => {
    setCurrencyDecimals({ kwd: 3, KWD: 7, JPY: 1.5, OMR: 3 } as any);
    expect(currencyDecimals('KWD')).toBe(2);
    expect(currencyDecimals('JPY')).toBe(2);
    expect(currencyDecimals('OMR')).toBe(3);
  });

  it('sends money as the API does: at least 2 places, more only when the amount has them', () => {
    expect(moneyWire('12.5')).toBe('12.50');
    expect(moneyWire('1.2340')).toBe('1.234');
    expect(moneyWire('0.12345')).toBe('0.12345');
    expect(moneyWire('100')).toBe('100.00');
  });
});

describe('typing money', () => {
  const meta = (currency?: string): FieldSchema => ({ name: 'rate', type: 'currency', label: 'Rate', required: false, read_only: false, scale: 6, currency });
  const fails = (currency: string | undefined, typed: string) => { try { KINDS.currency.parse!(typed, meta(currency)); return null; } catch (error) { return (error as Error).message; } };

  it('accepts exactly the currency’s places and refuses more, naming the currency', () => {
    setCurrencyDecimals(SITE);
    expect(KINDS.currency.parse!('1.234', meta('KWD'))).toBe('1.234');
    expect(KINDS.currency.parse!('1.5', meta('KWD'))).toBe('1.50');
    expect(KINDS.currency.parse!('12.500', meta('AED'))).toBe('12.50');
    expect(fails('KWD', '1.2345')).toBe('Use at most 3 decimal places for KWD');
    expect(KINDS.currency.parse!('98,000', meta('JPY'))).toBe('98000.00');
    expect(fails('JPY', '98000.5')).toBe('Use a whole amount for JPY');
    expect(KINDS.currency.parse!('0.12345', meta('XCL'))).toBe('0.12345');
    expect(fails('XCL', '0.123456')).toBe('Use at most 5 decimal places for XCL');
    expect(fails(undefined, '1.005')).toBe('Use at most 2 decimal places');
  });

  it('shows the field’s error in the form and keeps the value', () => {
    setCurrencyDecimals(SITE);
    const field = localField(meta('KWD'), '10.00');
    const host = document.createElement('div');
    running.push(mount(host, () => [displayValue(field), FieldControl(field).element]));
    const input = host.querySelector('input') as HTMLInputElement;
    expect(input.value).toBe('10.000');
    expect(text(host.querySelector('.tx.num'))).toBe('KWD 10.000');
    typeInto(input, '2.5005');
    expect(field.error.peek()).toBe('Use at most 3 decimal places for KWD');
    expect(field.value.peek()).toBe('10.00');
    typeInto(input, '2.5');
    expect(field.error.peek()).toBeNull();
    expect(field.value.peek()).toBe('2.50');
    expect(input.value).toBe('2.500');
  });

  it('follows a currency that comes from the record', async () => {
    setCurrencyDecimals(SITE);
    const field = (name: string, extra: Partial<FieldSchema> = {}): FieldSchema => ({ name, type: 'text', label: name, required: false, read_only: false, ...extra });
    const schema = { models: [{ name: 'deal', label: 'Deal', plural: 'Deals', title_field: 'currency', list_fields: ['currency'],
      fields: [field('currency'), field('amount', { type: 'currency', scale: 6, currency_source: { field: 'currency' } })] }] } as unknown as AppSchema;
    const store = new RecordStore({ api: createClient({ fetch: server([]).fetch }), schema, model: 'deal', today: () => '2026-11-10' });
    running.push(() => store.dispose());
    await store.create({ currency: 'KWD' });
    const amount = store.field('amount');
    const host = document.createElement('div');
    running.push(mount(host, () => FieldControl(amount).element));
    const input = host.querySelector('input.num') as HTMLInputElement;
    typeInto(input, '1.2345');
    expect(amount.error.peek()).toBe('Use at most 3 decimal places for KWD');
    typeInto(input, '1.234');
    expect(amount.value.peek()).toBe('1.234');
    store.field('currency').set('JPY');
    typeInto(input, '12.5');
    expect(amount.error.peek()).toBe('Use a whole amount for JPY');
    typeInto(input, '1250');
    expect(amount.value.peek()).toBe('1250.00');
    expect(input.value).toBe('1,250');
  });

  it('shows a record currency’s places in one-line summaries (side panel subtitle, more information rows)', async () => {
    setCurrencyDecimals(SITE);
    const field = (name: string, extra: Partial<FieldSchema> = {}): FieldSchema => ({ name, type: 'text', label: name, required: false, read_only: false, ...extra });
    const schema = { models: [{ name: 'deal', label: 'Deal', plural: 'Deals', title_field: 'currency', list_fields: ['currency'],
      fields: [field('currency'), field('amount', { type: 'currency', scale: 6, currency_source: { field: 'currency' } })] }] } as unknown as AppSchema;
    const store = new RecordStore({ api: createClient({ fetch: server([]).fetch }), schema, model: 'deal', today: () => '2026-11-10' });
    running.push(() => store.dispose());
    await store.create({ currency: 'KWD', amount: '1.5' });
    const amount = store.field('amount');
    expect(valueText(amount, null)).toBe('KWD 1.500');
    expect(moneyMeta(amount, null)().currency).toBe('KWD');
    store.field('currency').set('JPY');
    expect(valueText(amount, null)).toBe('JPY 2');
  });
});

describe('Controls, Currencies: decimals', () => {
  const state = {
    instance: { name: 'Acme', host: 'localhost', evlet: '', version: 1 }, me: { id: 1, username: 'owner', role: 'admin' }, apps: [],
    roles: [], models: [], scopes: { '': 'All records' }, settings: {}, controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0,
    users: { active: 1, total: 1 }, jobs: { failed: 0 }, audit: [], two_step: false, links: false, client: '127.0.0.1', remove_fields: null, instance_fields: true,
  };
  const REFUSED = '12 stored KWD amounts have more than 2 decimal places (the first is Invoice #0000000000001 total). Keep the decimals, or round those amounts first with evlet.round_stored_money, an explicit step that changes them.';

  function open(value: unknown, reply: (sent: Sent) => { status: number; body: unknown; etag: null }) {
    window.history.replaceState(null, '', '/controls/currencies');
    const stub = server([
      ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' }, etag: null })],
      ['GET', /\/api\/_controls\/state$/, () => ({ status: 200, body: state, etag: null })],
      ['GET', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: { version: 1, problems: [], items: [
        { kind: 'setting', key: 'display.currencies', file: {}, value, changed_by: null, changed_at: null }] }, etag: null })],
      ['POST', /\/api\/_controls\/overrides$/, reply],
    ] as any);
    const router = controlsRouter();
    const stop = router.start();
    const host = document.createElement('div');
    document.body.append(host);
    const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
    running.push(() => { dispose(); stop(); host.remove(); });
    return { host, sent: stub.sent };
  }
  const choose = (host: HTMLElement, code: string, places: number) => {
    const select = host.querySelector(`#ctl-cur-dec-${code}`) as HTMLSelectElement;
    select.value = String(places);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  };

  it('adds a client code with its own decimals, defaulting to ISO, and saves them', { timeout: 10000 }, async () => {
    const { host, sent } = open(null, () => ({ status: 200, body: { version: 2, saved: 1 }, etag: null }));
    await until(() => expect(host.querySelector('.ctl-cur-code')).not.toBeNull());
    typeInto(host.querySelector('.ctl-cur-code') as HTMLInputElement, 'kwd');
    click(byText(host, 'button', 'Add currency'));
    await until(() => expect(host.querySelector('#ctl-cur-dec-KWD')).not.toBeNull());
    expect((host.querySelector('#ctl-cur-dec-KWD') as HTMLSelectElement).value).toBe('3');
    expect(text(host.querySelector('[data-code="KWD"] .ctl-cur-preview'))).toBe('KWD 1,234.500·KWD -75.000');
    typeInto(host.querySelector('.ctl-cur-code') as HTMLInputElement, 'xcl');
    click(byText(host, 'button', 'Add currency'));
    await until(() => expect(host.querySelector('#ctl-cur-dec-XCL')).not.toBeNull());
    expect((host.querySelector('#ctl-cur-dec-XCL') as HTMLSelectElement).value).toBe('2');
    choose(host, 'XCL', 5);
    await until(() => expect(text(host.querySelector('[data-code="XCL"] .ctl-cur-preview'))).toBe('XCL 1,234.50000·XCL -75.00000'));
    expect(text(host.querySelector('[data-code="XCL"]'))).toContain('XCL has 2 by default.');
    click(byText(host, '.ctl-changebar button', 'Save changes'));
    await until(() => expect(sent.some(item => item.method === 'POST')).toBe(true));
    expect(sent.find(item => item.method === 'POST')!.body.changes[0].value).toEqual({
      KWD: { symbol: 'KWD', image: null, position: 'prefix', space: true, tint: false, decimals: 3 },
      XCL: { symbol: 'XCL', image: null, position: 'prefix', space: true, tint: false, decimals: 5 } });
  });

  it('shows the server’s reason when lowering decimals is refused, and keeps the draft', { timeout: 10000 }, async () => {
    const saved = { KWD: { symbol: 'KWD', image: null, position: 'prefix', space: true, tint: false, decimals: 3 } };
    const { host, sent } = open(saved, () => ({ status: 409, body: { error: REFUSED }, etag: null }));
    await until(() => expect(host.querySelector('#ctl-cur-dec-KWD')).not.toBeNull());
    choose(host, 'KWD', 2);
    click(byText(host, '.ctl-changebar button', 'Save changes'));
    await until(() => expect(sent.some(item => item.method === 'POST')).toBe(true));
    expect(sent.find(item => item.method === 'POST')!.body.changes[0].value.KWD.decimals).toBe(2);
    await until(() => expect(text(host)).toContain(REFUSED));
    expect((host.querySelector('#ctl-cur-dec-KWD') as HTMLSelectElement).value).toBe('2');
  });
});

// ---------- the calculation engine ----------

const LIMITS = { collection_rows: 1000, decimal_text: 128, document_rows: 5000, evaluation_steps: 1000000, expression_depth: 32,
  expression_nodes: 128, graph_edges: 250000, graph_nodes: 50000, table_depth: 8 };
const source = (kind: string, type: string, scale: number | null = null) => ({ computed: false, default: null, kind, maximum: null, minimum: null, required: false, scale, stored: true, type });

/** A plan with one calculated Currency field, total = amount, rounding to `currency` (a plan entry's currency source). */
function moneyPlan(currency: any, extraReads: any[][] = [], initial = false) {
  const plan: any = {
    compiler_version: 'evlet-finite-1', expression_version: 1, language: 'finite-v1', numeric_profile: 'finite40-hu18-v1', plan_version: 1,
    limits: LIMITS, root_resource: 'deal',
    fields: [{ expression: { $expr: 'ref', scope: 'self', field: 'amount' }, field: 'total', path: [],
      reads: [['self', null, 'amount'], ...extraReads], result_type: 'decimal', rounding: 'half_up', scale: 6, stored: true,
      target: initial ? 'total:initial' : 'total', target_type: 'currency', ...(initial ? { kind: 'initial' } : {}), currency }],
    tables: { $root: { model: 'deal', path: [], tables: [], fields: {
      amount: source('decimal', 'decimal', 6), currency: source('text', 'text'), customer: source('link', 'link'),
      total: { ...source('currency', 'decimal', 6), computed: !initial } } } },
  };
  plan.model_schema_hash = fingerprint(plan);
  return plan;
}
const DECIMALS = { decimals: { KWD: 3, JPY: 0, XCL: 5 }, settings: { 'billing.currency': 'KWD' } };

describe('finite engine: Currency targets round to their currency', () => {
  it('rounds to a fixed code, not the storage scale', () => {
    const doc = new FiniteDocument(moneyPlan({ code: 'KWD' }), { amount: '1.23456' }, undefined, DECIMALS);
    expect(doc.snapshot().total).toBe('1.235');
    const jpy = new FiniteDocument(moneyPlan({ code: 'JPY' }), { amount: '99.5' }, undefined, DECIMALS);
    expect(jpy.snapshot().total).toBe('100.00');
    // Without a currency input every code has 2.
    expect(new FiniteDocument(moneyPlan({ code: 'KWD' }), { amount: '1.23456' }).snapshot().total).toBe('1.23');
  });

  it('rounds to the code in a field of the row, and rounds again when that field changes', () => {
    const plan = moneyPlan({ expression: { $expr: 'ref', scope: 'self', field: 'currency' }, reads: [['self', null, 'currency']] }, [['self', null, 'currency']]);
    const doc = new FiniteDocument(plan, { amount: '1.234567', currency: 'XCL' }, undefined, DECIMALS);
    expect(doc.snapshot().total).toBe('1.23457');
    doc.set(doc.root.handle, 'currency', 'JPY');
    expect(doc.snapshot().total).toBe('1.00');
    doc.set(doc.root.handle, 'currency', null);
    expect(doc.snapshot().total).toBe('1.23');
    doc.set(doc.root.handle, 'currency', 'ZZZ');
    expect(doc.snapshot().total).toBe('1.23');
  });

  it('rounds to the code of an app setting, a host input', () => {
    const doc = new FiniteDocument(moneyPlan({ setting: 'billing.currency' }), { amount: '2.0005' }, undefined, DECIMALS);
    expect(doc.snapshot().total).toBe('2.001');
    const unknown = new FiniteDocument(moneyPlan({ setting: 'billing.other' }), { amount: '2.0005' }, undefined, DECIMALS);
    expect(unknown.snapshot().total).toBe('2.00');
  });

  it('waits for a linked record’s currency, then rounds to it', () => {
    const hop = { $expr: 'hop', field: 'customer', steps: [['customer', 'currency', 'text']] };
    const reads = [['self', null, 'customer'], ['lookup', 'customer', 'currency']];
    const doc = new FiniteDocument(moneyPlan({ expression: hop, reads }, reads), { amount: '5.55555', customer: '0000000000007' }, undefined, DECIMALS);
    const snap = doc.snapshot();
    expect(snap.total).toBeNull();
    expect(snap._states.total.state).toBe('pending');
    expect([...doc.misses.values()].some(misses => misses.size > 0)).toBe(true);
    doc.setLookups('customer', '0000000000007', { currency: 'KWD' });
    expect(doc.snapshot().total).toBe('5.556');
  });

  it('writes an initial at the currency’s places, with at least 2', () => {
    const doc = new FiniteDocument(moneyPlan({ code: 'JPY' }, [], true), { amount: '1234.4' }, undefined, DECIMALS);
    expect(doc.snapshot().total).toBe('1234.00');
    const kwd = new FiniteDocument(moneyPlan({ code: 'KWD' }, [], true), { amount: '1.2' }, undefined, DECIMALS);
    expect(kwd.snapshot().total).toBe('1.20');
  });

  it('refuses a malformed currency source like Python', () => {
    const refused = (currency: any, extra: any = {}) => {
      const plan = moneyPlan(currency);
      Object.assign(plan.fields[0], extra);
      plan.model_schema_hash = fingerprint(plan);
      try { new FiniteDocument(plan, {}); return null; } catch (error) { return (error as CalcError).message; }
    };
    expect(refused({ code: 'kwd' })).toContain('Invalid currency source');
    expect(refused({ code: 'KWD', setting: 'a.b' })).toContain('Invalid currency source');
    expect(refused({ setting: 'billing' })).toContain('Invalid currency source');
    expect(refused({ expression: { $expr: 'literal', type: 'text', value: 'KWD' }, reads: [] })).toContain('Invalid currency source');
    // Its reads must be the entry's reads too.
    expect(refused({ expression: { $expr: 'ref', scope: 'self', field: 'currency' }, reads: [['self', null, 'currency']] })).toContain('Invalid currency source');
    expect(refused({ code: 'KWD' }, { target_type: 'decimal' })).toContain('Invalid currency source');
    expect(refused({ code: 'KWD' })).toBeNull();
  });

  it('gets the host input from the schema: the site’s places and each setting’s code', () => {
    setCurrencyDecimals(SITE);
    const plan = moneyPlan({ setting: 'billing.currency' });
    const index = new SchemaIndex({ models: [{ name: 'deal', label: 'Deal', plural: 'Deals', title_field: 'currency', list_fields: [],
      fields: [{ name: 'total', type: 'currency', label: 'Total', required: false, read_only: false, scale: 6, currency: 'OMR' }] }] } as unknown as AppSchema);
    const input = currencyInput(plan, index);
    expect(canonical(input)).toBe(canonical({ decimals: SITE, settings: { 'billing.currency': 'OMR' } }));
    expect(new FiniteDocument(plan, { amount: '1.00049' }, undefined, input).snapshot().total).toBe('1.00');
  });
});
