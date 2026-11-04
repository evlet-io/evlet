// The record store replays the shared finite-v1 scenarios (Python-generated plans and results)
// through its public API: field.set, table.add, table.remove and lookups read over HTTP.
// After every step its cells must hold the values and rules Python recorded.
import { describe, expect, it } from 'vitest';
import fixtures from './finite-fixtures.json';
import { createClient } from '../api';
import { legacyRecordId } from '../ids';
import { RecordStore, type Row } from '../model/record';
import type { AppSchema, ModelSchema } from '../model/schema';

const plans = (fixtures as any).plans as Record<string, any>;

// A schema for a plan alone: one model per table, with calculated fields marked.
function schemaFor(plan: any): AppSchema {
  const entries = Object.values<any>(plan.tables);
  const models: ModelSchema[] = entries.map(entry => ({
    name: entry.model, label: entry.model, plural: entry.model, title_field: 'id', list_fields: [],
    fields: Object.entries<any>(entry.fields).map(([name, meta]) => ({
      name, type: meta.kind, label: name, required: meta.required, read_only: meta.computed, compute: meta.computed ? {} : null, default_kind: 'none',
    })),
    children: entry.tables.map((table: string) => ({
      name: table, label: table, link: 'evlet_owner',
      model: entries.find(item => item.path.join('/') === [...entry.path, table].join('/')).model,
    })),
  }));
  models[entries.findIndex(entry => entry.path.length === 0)].calculation = {
    language: plan.language, numeric_profile: plan.numeric_profile, model_schema_hash: plan.model_schema_hash, capabilities: {}, plan,
  };
  return { version: 1, ui_version: 1, models };
}

// The scenarios name records by their legacy integer ids; the browser reads them by text id.
const textId = (id: number | string) => typeof id === 'number' ? legacyRecordId(String(id))! : id;

// Lookups wait until the scenario supplies them, like a slow server.
function lookupServer() {
  const waiting = new Map<string, (values: Record<string, unknown>) => void>();
  const supplied = new Map<string, Record<string, unknown>>();
  const fetch = async (input: RequestInfo | URL) => {
    const [, model, id] = /\/api\/(\w+)\/([0-9a-z]{13})$/.exec(String(input))!;
    const key = `${model}:${id}`;
    const values = supplied.get(key) ?? await new Promise<Record<string, unknown>>(resolve => waiting.set(key, resolve));
    return new Response(JSON.stringify({ id, ...values }), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  return {
    fetch: fetch as typeof globalThis.fetch,
    supplied: (model: string, id: number) => supplied.has(`${model}:${textId(id)}`),
    supply(model: string, id: number, values: Record<string, unknown>) {
      const key = `${model}:${textId(id)}`;
      supplied.set(key, values);
      waiting.get(key)?.(values);
      waiting.delete(key);
    },
  };
}

function rowAt(root: Row, label: string): Row {
  let row = root;
  const parts = label ? label.split('.') : [];
  for (let index = 0; index < parts.length; index += 2) row = row.table(parts[index]).rows.peek().find(item => item.key === parts[index + 1])!;
  return row;
}

// The engine's internal pending marker reads as an empty value in the store.
const text = (value: unknown) => value === null || value === undefined || typeof value === 'object' ? null : String(value);

// Every value in the recorded snapshot equals the store's cell; rows match by key and order.
function expectSnapshot(row: Row, snapshot: any, at = '') {
  for (const [name, value] of Object.entries<any>(snapshot)) {
    if (name === 'children' || name === '_key' || name === 'id' || name === '_states') continue;
    expect(text(row.field(name).value.peek()), `${at}${name}`).toBe(text(value));
  }
  for (const [table, rows] of Object.entries<any[]>(snapshot.children ?? {})) {
    const mine = row.table(table).rows.peek();
    expect(mine.map(item => item.key), `${at}${table}`).toEqual(rows.map(item => item._key ?? String(item.id)));
    rows.forEach((item, index) => expectSnapshot(mine[index], item, `${at}${table}.${mine[index].key}.`));
  }
}

function expectRules(root: Row, rules: Record<string, any>) {
  for (const [label, fields] of Object.entries<any>(rules)) {
    const row = rowAt(root, label === '$root' ? '' : label);
    for (const [name, state] of Object.entries(fields)) expect(row.field(name).rules.peek(), `${label}.${name}`).toEqual(state);
  }
}

const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const REPLAYED = ['invoice-toggle', 'cost-round-trip', 'states', 'rounding-modes', 'datetime', 'hops', 'loaded-offer',
  'invoice-random-1', 'invoice-random-2', 'invoice-random-3'];

describe('record store against the shared finite-v1 scenarios', () => {
  for (const scenario of (fixtures as any).scenarios.filter((item: any) => REPLAYED.includes(item.name))) {
    it(`replays ${scenario.name}`, async () => {
      const plan = plans[scenario.model], model = plan.tables.$root.model;
      const data = structuredClone(scenario.data);
      const backend = lookupServer();
      // A saved document's detail is the scenario data; every other read is a lookup.
      const fetch = (async (input: RequestInfo | URL) => data.id && String(input) === `/api/${model}/${textId(data.id)}`
        ? new Response(JSON.stringify(data), { status: 200 }) : backend.fetch(input)) as typeof globalThis.fetch;
      const store = new RecordStore({ api: createClient({ fetch }), schema: schemaFor(plan), model });
      if (data.id) await store.open(textId(data.id)); else await store.create(data);
      const [initial, ...steps] = scenario.steps;
      await replay(store, backend, initial, steps);
    });
  }
});

async function replay(store: RecordStore, backend: ReturnType<typeof lookupServer>, initial: any, steps: any[]) {
  expectSnapshot(store.root, initial.snapshot);
  if (initial.rules) expectRules(store.root, initial.rules);
  expect(store.engine!.lookupRequests()).toEqual(initial.requests);
  for (const step of steps) {
    const [kind, ...args] = step.command;
    if (kind === 'lookup') {
      // The store reads each linked record once over HTTP. A second or invalid push for the
      // same record is a host-side engine check, past what the store does: stop there.
      if (step.error || backend.supplied(args[0], args[1])) return store.dispose();
      backend.supply(args[0], args[1], args[2]);
    } else {
      const command = () => {
        if (kind === 'set') rowAt(store.root, args[0]).field(args[1]).set(args[2]);
        else if (kind === 'add') rowAt(store.root, args[0]).table(args[1]).add(args[2]);
        else if (kind === 'remove') {
          const row = rowAt(store.root, args[0]);
          row.parent!.table(row.path.at(-1)!).remove(row);
        } else throw new Error(`Unsupported command ${kind}`);
      };
      // The store refuses some writes before the engine sees them (a calculated field).
      if (step.error) expect(command).toThrow(); else command();
    }
    await settle();
    expectSnapshot(store.root, step.snapshot);
    if (step.rules) expectRules(store.root, step.rules);
    expect(store.engine!.lookupRequests()).toEqual(step.requests);
  }
  store.dispose();
}
