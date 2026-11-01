// The calculation session of an open record: a FiniteDocument (the same finite-v1 evaluator
// the server's fixtures check) bound to the record's cells.
//
// The session computes calculated fields, initial values and the applies/required/locked
// rules. Its results flow into the field cells. It supplies today() and reads the Link
// lookups it waits for (customer.credit_days) with GET /api/{model}/{id}.

import { FiniteDocument, wire, type Result, type RuleState } from '../../runtime/FiniteEngine';
import type { ApiClient } from '../api';
import { batch } from '../reactive';
import type { Row, Table } from './record';

export class Session {
  /** The live FiniteDocument, or null when the record has no plan or is not attached. */
  engine: FiniteDocument | null = null;
  private publish: Array<() => void> = [];
  // Link targets read before, by "model:id", and the ones this session already asked for.
  private lookups = new Map<string, Record<string, unknown>>();
  private tried = new Set<string>();
  private work = new Set<Promise<unknown>>();

  constructor(private readonly api: ApiClient, private readonly plan: any, private readonly today: () => string) {}

  /**
   * Build a session from the rows and bind every row's cells to it. Known lookups are given
   * at once; missing ones are read in the background. Until they arrive, a saved result keeps
   * its saved value.
   */
  start(root: Row) {
    this.stop();
    const plan = this.plan, doc = new FiniteDocument(plan, this.data(root));
    this.engine = doc;
    bindHandles(doc.root, root);
    if (plan.fields.some((field: any) => field.reads.some((read: any[]) => read[0] === 'today'))) doc.setToday(this.today());
    for (let known = this.cached(doc); known.length; known = this.cached(doc))
      doc.batch(session => { for (const [model, id, values] of known) session.setLookups(model, id, values); });
    batch(() => {
      const visit = (row: Row) => {
        if (!row.handle) return;
        this.bind(row, !row.isNew);
        for (const table of row.tables.values()) table.rows.peek().forEach(visit);
      };
      visit(root);
    });
    this.fetchLookups();
  }

  stop() {
    this.engine?.dispose();
    this.engine = null;
    this.tried.clear();
    this.publish = [];
  }

  /** Forget lookups read so far, so the next start reads them again. */
  forget() { this.lookups.clear(); }

  /** Wait until background lookups settle. */
  async idle() {
    while (this.work.size) await Promise.allSettled([...this.work]);
  }

  set(row: Row, name: string, value: unknown) {
    if (this.engine && row.handle && this.source(row, name)) this.run(doc => doc.set(row.handle!, name, value));
    // Any edit is also a chance to retry lookups that failed before.
    this.fetchLookups();
  }

  add(table: Table, row: Row, values: Record<string, unknown>) {
    if (!this.engine || !table.owner.handle || !this.entry(row.path)) return;
    const known = { _key: row.key, ...Object.fromEntries(Object.entries(values).filter(([name]) => this.source(row, name))) };
    row.handle = this.run(doc => doc.addRow(table.owner.handle!, table.name, known));
    this.bind(row);
    this.fetchLookups();
  }

  remove(row: Row) {
    if (this.engine && row.handle) this.run(doc => doc.removeRow(row.handle!));
  }

  reorder(table: Table, rows: Row[]) {
    if (this.engine && table.owner.handle && rows.every(row => row.handle))
      this.run(doc => doc.reorder(table.owner.handle!, table.name, rows.map(row => row.handle!)));
  }

  private entry(path: string[]): any {
    return this.engine?.schema.get(path.join('/')) ?? null;
  }

  // A field the session reads as input (not calculated).
  private source(row: Row, name: string) {
    const entry = this.entry(row.path);
    return Boolean(entry && Object.prototype.hasOwnProperty.call(entry.fields, name) && !entry.fields[name].computed);
  }

  // Run engine commands as one batch, then publish its results to the cells as one batch.
  private run<T>(command: (doc: FiniteDocument) => T): T {
    let result!: T;
    try {
      this.engine!.batch(doc => { result = command(doc); });
    } finally {
      const updates = this.publish.splice(0);
      batch(() => updates.forEach(update => update()));
    }
    return result;
  }

  // The document as the engine takes it: plan fields only, new rows without empty values so
  // defaults and initial values apply.
  private data(row: Row): Record<string, unknown> {
    const entry = Object.values<any>(this.plan.tables).find(item => item.path.join('/') === row.path.join('/'));
    const data: Record<string, unknown> = {};
    if (row.id !== null) data.id = row.id;
    if (row.parent) data._key = row.key;
    for (const [name, meta] of Object.entries<any>(entry.fields)) {
      const value = row.fields.get(name)?.value.peek();
      if (!meta.computed && (value !== null && value !== undefined || !row.isNew)) data[name] = value ?? null;
    }
    const children: Record<string, unknown> = {};
    for (const name of entry.tables) {
      const table = row.tables.get(name);
      if (table) children[name] = table.rows.peek().map(item => this.data(item));
    }
    if (entry.tables.length) data.children = children;
    return data;
  }

  // Subscribe a row's cells to the session. On first bind of a saved row, a result that is
  // not ready keeps the saved value (the server stored it); later results always replace it.
  private bind(row: Row, keepSaved = false) {
    const doc = this.engine!, handle = row.handle!, entry = this.entry(row.path);
    const engineRow = doc.rows.get(handle)!;
    for (const [name, meta] of Object.entries<any>(entry.fields)) {
      const field = row.fields.get(name);
      if (!field) continue;
      if (meta.computed) {
        const planned = doc.fields.get(row.path.join('/') + '\u0000' + name);
        const scale = planned?.scale ?? null, integer = planned?.target_type === 'integer';
        const apply = (result: Result, keep: boolean) => {
          field.result.set(result.state === 'ready' ? { state: 'ready' } : { state: result.state, code: result.code });
          if (result.state === 'ready') {
            const value = wire(result.value, scale);
            // Integer fields arrive from the server as JSON numbers; keep them numbers.
            field.value.set(integer && typeof value === 'string' && Number.isSafeInteger(Number(value)) ? Number(value) : value);
          } else if (!keep) field.value.set(null);
        };
        apply(doc.result(engineRow, name), keepSaved);
        doc.observe(handle, name, result => this.publish.push(() => apply(result, false)));
      } else {
        // Initial values and applies_when write source fields too. A pending default reads as empty.
        const read = () => { const value = doc.rows.get(handle)?.values[name]; return value && typeof value === 'object' ? null : value ?? null; };
        field.value.set(read());
        doc.observe(handle, name, () => this.publish.push(() => field.value.set(read())));
      }
    }
    const rules = (states: Record<string, RuleState>) => {
      for (const [name, state] of Object.entries(states)) row.fields.get(name)?.rules.set({ ...state });
    };
    rules(doc.ruleStates(handle));
    doc.observeRules(handle, states => this.publish.push(() => rules(states)));
  }

  // Lookups this session wants that an earlier session of the record already read.
  private cached(doc: FiniteDocument): Array<[string, number, Record<string, unknown>]> {
    return doc.lookupRequests().filter(([model, id]) => !this.tried.has(`${model}:${id}`) && this.lookups.has(`${model}:${id}`))
      .map(([model, id, fields]) => {
        this.tried.add(`${model}:${id}`);
        const values = this.lookups.get(`${model}:${id}`)!;
        return [model, id, Object.fromEntries(fields.filter(name => name in values).map(name => [name, values[name]]))];
      });
  }

  // Read the Link targets the session is waiting for, then hand their values over. Repeats for
  // multi-step paths (customer.region.grace) until nothing new is wanted.
  private async read(doc: FiniteDocument) {
    const failed = new Set<string>();
    for (;;) {
      const wanted = doc.lookupRequests().filter(([model, id]) => !this.tried.has(`${model}:${id}`) && !failed.has(`${model}:${id}`));
      if (!wanted.length || this.engine !== doc) return;
      await Promise.all(wanted.map(async ([model, id, fields]) => {
        const key = `${model}:${id}`;
        this.tried.add(key);
        let values: Record<string, unknown>;
        try { values = (await this.api.get(model, id)).data; }
        catch {
          // Unreadable, missing or the network failed: results that need it stay pending,
          // and the next edit tries again.
          this.tried.delete(key);
          failed.add(key);
          return;
        }
        // A session replaced meanwhile (reload) does not take or cache this answer.
        if (this.engine !== doc) return;
        this.lookups.set(key, values);
        const known = Object.fromEntries(fields.filter(name => name in values).map(name => [name, values[name]]));
        this.run(session => session.setLookups(model, id, known));
      }));
    }
  }

  private fetchLookups() {
    const doc = this.engine;
    if (!doc || !doc.lookupRequests().some(([model, id]) => !this.tried.has(`${model}:${id}`))) return;
    const task = this.read(doc).finally(() => this.work.delete(task));
    this.work.add(task);
  }
}

// Match the session's rows to the store's rows: same tables, same order.
function bindHandles(engineRow: any, row: Row) {
  row.handle = engineRow.handle;
  for (const [name, items] of Object.entries<any[]>(engineRow.children)) {
    const rows = row.tables.get(name)?.rows.peek() ?? [];
    items.forEach((item, index) => { if (rows[index]) bindHandles(item, rows[index]); });
  }
}
