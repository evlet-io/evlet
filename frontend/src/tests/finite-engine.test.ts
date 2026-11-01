import { describe, expect, it } from 'vitest';
import fixtures from './finite-fixtures.json';
import engineSource from '../runtime/FiniteEngine.ts?raw';
import fixtureSource from './finite-fixtures.json?raw';
import { FiniteDocument, CalcError, parse, text, add, sub, mul, div, quantize, fingerprint, sha256, canonical, dayNumber, dayText } from '../runtime/FiniteEngine';

const plans = fixtures.plans as Record<string, any>;

function handle(document: FiniteDocument, label: string) { return label === '' ? document.root.handle : document.find(label); }

function apply(document: FiniteDocument, command: any[]) {
  const [kind, ...args] = command;
  if (kind === 'set') document.set(handle(document, args[0]), args[1], args[2]);
  else if (kind === 'add') document.addRow(handle(document, args[0]), args[1], args[2]);
  else if (kind === 'remove') document.removeRow(handle(document, args[0]));
  else if (kind === 'move') document.moveRow(handle(document, args[0]), handle(document, args[1]), args[2], args[3]);
  else if (kind === 'reorder') document.reorder(handle(document, args[0]), args[1], args[2].map((label: string) => handle(document, label)));
  else if (kind === 'replace') document.replaceRows(handle(document, args[0]), args[1], args[2]);
  else if (kind === 'today') document.setToday(args[0]);
  else if (kind === 'lookup') document.setLookups(args[0], args[1], args[2]);
}

// A rebuilt session receives the same host inputs (today and lookups) as the original.
function rebuild(document: FiniteDocument, plan: any) {
  const fresh = new FiniteDocument(plan, document.sources());
  fresh.batch(commands => {
    if (document.today !== null) commands.setToday(document.today);
    for (const [model, records] of document.lookups) for (const [id, values] of records) commands.setLookups(model, id, {...values});
  });
  return fresh;
}

function strip(value: any): any {
  if (Array.isArray(value)) return value.map(strip);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).filter(([key]) => key !== '_handle').map(([key, item]) => [key, strip(item)]));
  return value;
}

describe('finite-v1 browser evaluator against the Python plans', () => {
  it('fingerprints plans exactly like Python', () => {
    for (const plan of Object.values(plans)) expect(fingerprint(plan)).toBe(plan.model_schema_hash);
    expect(sha256('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    expect(canonical({b: ['é', '\u007f', null], a: true})).toBe('{"a":true,"b":["\\u00e9","\\u007f",null]}');
  });

  it('agrees with Python on edge inputs and malformed documents', () => {
    // JSON.parse keeps "__proto__" as an own key, as a network response would; the module import does not.
    const inputs = JSON.parse(fixtureSource).invalid_inputs;
    for (const item of inputs.values) {
      const snap = new FiniteDocument(plans.states, {a: item.input}).snapshot();
      expect([snap._states?.sum_null ?? null, snap.whole, snap._states?.whole ?? null]).toEqual([item.sum_null, item.whole, item.whole_state]);
    }
    for (const item of inputs.documents) {
      let code: string | null = null;
      try { new FiniteDocument(plans.states, structuredClone(item.data)); } catch (exc) { code = (exc as CalcError).code; }
      expect(code).toBe(item.error);
    }
    const document = new FiniteDocument(plans.invoice, {children: {items: [{_key: 'a'}]}});
    expect(() => document.reorder(document.root.handle, 'items', ['h2,h3'] as any)).toThrow('DOCUMENT_STRUCTURE_ERROR');
    expect(() => document.set(document.root.handle, '__proto__', {inclusive: true})).toThrow('DOCUMENT_STRUCTURE_ERROR');
    expect(() => new FiniteDocument(plans.invoice, {children: {items: null}})).toThrow('DOCUMENT_STRUCTURE_ERROR');
  });

  it('imports without SolidJS or the DOM', () => {
    expect(engineSource).not.toMatch(/^\s*import\b/m);
    expect(engineSource).not.toMatch(/\b(window|globalThis)\.|solid-js|\bdocument\.(createElement|body|querySelector)/);
  });

  it.each(fixtures.numeric as any[])('computes $op($a, $b) like Python', (item: any) => {
    const run = () => item.op.startsWith('round') ? text(quantize(parse(item.a), item.b, item.op === 'round_even' ? 'half_even' : 'half_up'))
      : text(({add, sub, mul, div} as any)[item.op](parse(item.a), parse(item.b)));
    if (item.error) expect(run).toThrow(item.error);
    else expect(run()).toBe(item.expected);
  });

  for (const scenario of fixtures.scenarios as any[]) {
    it(`replays ${scenario.name} with identical snapshots, states and evaluation counts`, () => {
      const document = new FiniteDocument(plans[scenario.model], structuredClone(scenario.data));
      const [initial, ...steps] = scenario.steps;
      expect(strip(document.snapshot())).toEqual(initial.snapshot);
      expect(document.trace).toEqual(initial.trace);
      expect(document.lookupRequests()).toEqual(initial.requests);
      for (const step of steps) {
        if (step.error) {
          let code = '';
          try { apply(document, structuredClone(step.command)); } catch (exc) { code = (exc as CalcError).code; }
          expect(code).toBe(step.error);
        } else {
          apply(document, structuredClone(step.command));
          expect(document.trace).toEqual(step.trace);
        }
        expect(strip(document.snapshot())).toEqual(step.snapshot);
        expect(document.revision).toBe(step.revision);
        expect(document.lookupRequests()).toEqual(step.requests);
        expect(strip(rebuild(document, plans[scenario.model]).snapshot())).toEqual(step.snapshot);
      }
    });
  }

  it('rejects unknown plans, operations and async batches', () => {
    const plan = plans.invoice;
    expect(() => new FiniteDocument({...plan, numeric_profile: 'decimal28-half-up-v1'})).toThrow('SCHEMA_VERSION_MISMATCH');
    expect(() => new FiniteDocument({...plan, fields: plan.fields.slice(1)})).toThrow('SCHEMA_VERSION_MISMATCH');
    for (const invalid of fixtures.invalid_plans as any[]) {
      const broken = structuredClone(plan);
      broken.fields[broken.fields.length - 1].expression = invalid.expression;
      broken.model_schema_hash = fingerprint(broken);
      expect(() => new FiniteDocument(broken, {})).toThrow('UNSUPPORTED_EXPRESSION');
    }
    const document = new FiniteDocument(plan, {children: {items: []}});
    expect(() => document.batch(async () => undefined)).toThrow('ASYNC_BATCH_UNSUPPORTED');
    expect(document.revision).toBe(1);
  });

  it('keeps failed commands atomic, checks arity and bounds, and refuses async callbacks', () => {
    const plan = plans.invoice;
    const document = new FiniteDocument(plan, {inclusive: false, children: {items: [{_key: 'a', qty: '1', rate: '10'}, {_key: 'b', qty: '2', rate: '10'}]}});
    const before = JSON.stringify(document.snapshot()), rows = document.rows.size, a = document.find('items.a');
    document.batch(commands => {
      expect(() => commands.moveRow(a, document.root.handle, 'items', 99)).toThrow(CalcError);
      expect(() => commands.moveRow(a, document.root.handle, 'items', 2)).toThrow(CalcError);
      expect(() => commands.addRow(document.root.handle, 'items', {_key: 'b', qty: '5'})).toThrow(CalcError);
    });
    expect(JSON.stringify(document.snapshot())).toBe(before);
    expect(document.rows.size).toBe(rows);
    let late = false;
    expect(() => document.batch(async commands => { await null; late = true; commands.set(a, 'qty', '7'); })).toThrow('ASYNC_BATCH_UNSUPPORTED');
    expect(late).toBe(false);
    const literal = (type: string, value: unknown) => ({$expr: 'literal', type, value});
    for (const expression of [{$expr: 'add', args: []}, {$expr: 'add', args: [literal('integer', 1), literal('integer', 1)]},
      {$expr: 'round', args: [literal('decimal', '1'), literal('decimal', '1')]}, {$expr: 'aggregate', kind: 'sum', table: 'items', field: 'net'},
      literal('decimal', 'NaN')]) {
      const broken = structuredClone(plan);
      broken.fields[0].expression = expression;
      broken.model_schema_hash = fingerprint(broken);
      expect(() => new FiniteDocument(broken, {})).toThrow(CalcError);
    }
    const bounded = structuredClone(plan);
    bounded.tables.$root.fields.grand_total.minimum = '1000';
    bounded.model_schema_hash = fingerprint(bounded);
    const small = new FiniteDocument(bounded, {inclusive: false, children: {items: [{qty: '1', rate: '10'}]}});
    expect(small.snapshot()._states.grand_total.code).toBe('COMPUTE_RANGE_ERROR');
  });

  it('keeps rows added before a rolled-back command and undoes caught nested batches', () => {
    const document = new FiniteDocument(plans.invoice, {inclusive: false, children: {items: []}});
    const root = document.root.handle;
    document.batch(commands => {
      commands.addRow(root, 'items', {qty: '2', rate: '10'});
      expect(() => commands.set('nope', 'qty', '1')).toThrow(CalcError);
      expect(() => commands.batch(inner => { inner.set(root, 'inclusive', true); throw new Error('stop'); })).toThrow('stop');
    });
    expect(document.snapshot().grand_total).toBe('21.00');
    expect(document.snapshot().inclusive).toBe(false);
    const bounded = structuredClone(plans.invoice);
    bounded.tables.$root.fields.grand_total.maximum = '1E+3';
    bounded.model_schema_hash = fingerprint(bounded);
    expect(() => new FiniteDocument(bounded, {})).toThrow(CalcError);
  });

  it('publishes one revision per batch and blocks observer mutation', () => {
    const document = new FiniteDocument(plans.invoice, {inclusive: true, children: {items: [{_key: 'a', qty: '1', rate: '105'}]}});
    const seen: Array<[number, string]> = [];
    document.observe(document.root.handle, 'grand_total', result => seen.push([document.revision, String((result as any).value && text((result as any).value))]));
    document.batch(commands => {
      commands.set(commands.root.handle, 'inclusive', false);
      commands.batch(inner => inner.set(inner.find('items.a'), 'qty', '2'));
    });
    expect(seen).toEqual([[2, '220.5']]);
    const stop = document.observe(document.root.handle, 'net_total', () => document.set(document.root.handle, 'inclusive', true));
    expect(() => document.set(document.find('items.a'), 'qty', '3')).toThrow('REACTIVE_REENTRANCY');
    stop(); stop();
    expect(document.snapshot().inclusive).toBe(false);
    document.dispose();
    expect(document.subscriptions()).toBe(0);
    expect(() => document.set('h1', 'inclusive', true)).toThrow('DOCUMENT_STRUCTURE_ERROR');
  });

  it('supplies today and Link lookups through the host API and recomputes only their dependents', () => {
    const document = new FiniteDocument(plans.sale, {customer: 7, issued: '2026-10-28', amount: '100.05'});
    expect(document.lookupRequests()).toEqual([['customer', 7, ['credit_days', 'rate', 'region', 'since', 'vip']]]);
    document.setLookups('customer', 7, {credit_days: 30, rate: '2.50', vip: true, region: null, since: '2026-10-01'});
    expect(document.lookupRequests()).toEqual([]);
    const snap = document.snapshot();
    expect([snap.due, snap.discount, snap.net, snap.loyal]).toEqual(['2026-11-27', '2.50', '97.55', '27']);
    expect(snap._states.grace_due.state).toBe('pending');
    const evaluations = document.explain(document.root.handle, 'due').evaluations;
    document.setLookups('customer', 7, {rate: '5'});
    expect(document.explain(document.root.handle, 'due').evaluations).toBe(evaluations);
    expect(document.snapshot().discount).toBe('5.00');
    expect(document.explain(document.root.handle, 'discount').inputs).toContain('@customer.rate');
    // Rejected lookups and a failed batch leave the session unchanged.
    expect(() => document.setLookups('customer', 7, {secret: 1})).toThrow('DOCUMENT_STRUCTURE_ERROR');
    expect(() => document.setLookups('customer', -1, {rate: '1'})).toThrow('DOCUMENT_STRUCTURE_ERROR');
    expect(() => document.batch(commands => { commands.setLookups('customer', 7, {rate: '9'}); throw new Error('stop'); })).toThrow('stop');
    expect(document.snapshot().discount).toBe('5.00');
    const schedule = new FiniteDocument(plans.schedule, {start: '2024-02-28'});
    expect(schedule.snapshot()._states.age.causes).toEqual(['@today']);
    schedule.setToday('2024-03-01');
    expect(schedule.snapshot().age).toBe('2');
  });

  it('validates rounding in plans and matches Python ordinals for dates', () => {
    const broken = structuredClone(plans.rounding);
    broken.fields[0].rounding = 'down';
    broken.model_schema_hash = fingerprint(broken);
    expect(() => new FiniteDocument(broken, {})).toThrow('UNSUPPORTED_EXPRESSION');
    expect([dayNumber('0001-01-01'), dayNumber('1970-01-01'), dayNumber('2024-02-29'), dayNumber('9999-12-31')]).toEqual([1, 719163, 738945, 3652059]);
    for (let day = 1; day <= 3652059; day += 997) expect(dayNumber(dayText(day))).toBe(day);
    for (const bad of ['0000-01-01', '2023-02-29', '2024-13-01', '2024-1-01', '20240101', ' 2024-01-01'])
      expect(() => dayNumber(bad)).toThrow('COMPUTE_TYPE_ERROR');
    expect(() => dayText(3652060)).toThrow('COMPUTE_RANGE_ERROR');
    expect(text(quantize(parse('0.125'), 2, 'half_even'))).toBe('0.12');
    expect(text(quantize(parse('-0.135'), 2, 'half_even'))).toBe('-0.14');
  });
});
