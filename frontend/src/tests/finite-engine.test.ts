import { describe, expect, it } from 'vitest';
import fixtures from './finite-fixtures.json';
import engineSource from '../runtime/FiniteEngine.ts?raw';
import fixtureSource from './finite-fixtures.json?raw';
import { FiniteDocument, CalcError, parse, text, add, sub, mul, div, quantize, fingerprint, sha256, canonical } from '../runtime/FiniteEngine';

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
    const run = () => item.op === 'round' ? text(quantize(parse(item.a), item.b))
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
        const fresh = new FiniteDocument(plans[scenario.model], document.sources());
        expect(strip(fresh.snapshot())).toEqual(step.snapshot);
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
});
