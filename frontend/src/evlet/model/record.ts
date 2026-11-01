// The record store: one open record in the browser, as cells.
//
//   const record = new RecordStore({ api, schema, model: 'order' });
//   await record.open(42);                     // loads every row, attaches calculations
//   record.field('title').set('Renamed');      // a draft change
//   record.table('lines').add({ qty: '2' });   // totals recalculate at once
//   record.field('total').value.get();         // '304.50'
//   await record.save();                       // the server's answer replaces the draft
//
// Each field has a value cell (the draft) over a server cell (the last saved value), so
// dirty tracking, revert and saving only changes follow from comparing the two. Rows of a
// Table are Row objects with a stable key, so a list keyed by row.key keeps its DOM.
//
// When the schema ships a calculation plan, a Session (session.ts) computes calculated
// fields, initial values and the applies/required/locked rules into the field cells.

import { compare, parse, type FiniteDocument } from '../../runtime/FiniteEngine';
import { ApiError, ConflictError, ValidationError, type ApiClient, type DraftResult, type FieldErrors, type RecordData } from '../api';
import { batch, cell, derive, getOwner, runWithOwner, scope, type Cell, type Derive } from '../reactive';
import { buildChanges, findField } from './changes';
import { todayGst } from './format';
import { Session } from './session';
import { isCalculated, SchemaIndex, type AppSchema, type ChildSchema, type FieldSchema, type ModelSchema } from './schema';

export type Rules = { applies: boolean; required: boolean; locked: boolean };
export type Outcome = { state: 'ready' } | { state: 'pending' | 'error'; code: string };
const READY: Outcome = { state: 'ready' };
const KEY = /^[A-Za-z0-9_-]{1,64}$/;
const NUMERIC = new Set(['decimal', 'currency', 'integer', 'percent', 'duration']);
const sameRules = (a: Rules, b: Rules) => a.applies === b.applies && a.required === b.required && a.locked === b.locked;
const sameOutcome = (a: Outcome, b: Outcome) => a.state === b.state && (a as any).code === (b as any).code;

/** Whether two field values mean the same: '' and null are both empty, '1.50' equals '1.5' for numbers. */
export function sameValue(meta: FieldSchema | undefined, a: unknown, b: unknown): boolean {
  const empty = (value: unknown) => value === null || value === undefined || value === '';
  if (empty(a) || empty(b)) return empty(a) && empty(b);
  if (a === b) return true;
  if (meta && NUMERIC.has(meta.type)) {
    try { return compare(parse(a), parse(b)) === 0; } catch { return false; }
  }
  return false;
}

// ---------- fields ----------

export class Field {
  /** The draft value shown and edited in the UI. */
  readonly value: Cell<unknown>;
  /** The value as the server last returned it. */
  readonly server: Cell<unknown>;
  /** The server's or a rule's message for this field. */
  readonly error: Cell<string | null>;
  /** applies_when, required_when and locked_when as they stand now. */
  readonly rules: Cell<Rules>;
  /** For calculated fields: ready, or pending/error with a code such as COMPUTE_INPUT_MISSING. */
  readonly result: Cell<Outcome>;
  readonly calculated: boolean;
  private changed?: Derive<boolean>;
  private open?: Derive<boolean>;

  constructor(readonly row: Row, readonly meta: FieldSchema, value: unknown) {
    const name = `${row.label}.${meta.name}`;
    this.value = cell(value, { name });
    this.server = cell(value, { name: `${name} (saved)` });
    this.error = cell<string | null>(null, { name: `${name} (error)` });
    this.rules = cell<Rules>({ applies: true, required: meta.required, locked: false }, { name: `${name} (rules)`, equals: sameRules });
    this.result = cell<Outcome>(READY, { name: `${name} (result)`, equals: sameOutcome });
    this.calculated = isCalculated(meta);
  }

  get name() { return this.meta.name; }
  get label() { return this.meta.label; }

  /** True while the draft differs from the saved value. */
  get dirty(): Derive<boolean> {
    return this.changed ??= runWithOwner(this.row.owner, () => derive(
      () => !this.calculated && (this.row.isNew || !sameValue(this.meta, this.value.get(), this.server.get())),
      { name: `${this.row.label}.${this.name} (dirty)` }));
  }

  /** True when the user may change this field now. */
  get editable(): Derive<boolean> {
    return this.open ??= runWithOwner(this.row.owner, () => derive(() => {
      const rules = this.rules.get();
      const record = this.row.record;
      return !this.calculated && !this.meta.read_only && rules.applies && !rules.locked && record.ready.get() && !record.saving.get();
    }, { name: `${this.row.label}.${this.name} (editable)` }));
  }

  /** Change the draft. Calculations, rules and dirty state follow. */
  set(value: unknown) { this.row.record.write(this, value); }
}

// ---------- rows and tables ----------

export class Row {
  readonly fields = new Map<string, Field>();
  readonly tables = new Map<string, Table>();
  readonly label: string;
  readonly path: string[];
  /** The calculation session's handle for this row. */
  handle: string | null = null;
  owner: unknown;
  readonly dispose: () => void;
  private changed?: Derive<boolean>;
  private readonly fresh: Cell<boolean>;

  constructor(readonly record: RecordStore, readonly model: ModelSchema, readonly parent: Row | null, table: string | null,
    public key: string, public id: number | null, isNew: boolean, data: Record<string, any>) {
    this.fresh = cell(isNew);
    this.path = parent ? [...parent.path, table!] : [];
    this.label = parent ? `${parent.label}.${table}[${key}]` : model.name;
    this.dispose = runWithOwner(parent?.owner ?? record.owner, () => scope(() => { this.owner = getOwner(); }));
    for (const meta of model.fields) {
      const value = Object.prototype.hasOwnProperty.call(data, meta.name) ? data[meta.name]
        : isNew && meta.default_kind === 'static' ? meta.default ?? null : null;
      this.fields.set(meta.name, new Field(this, meta, value));
    }
    for (const child of model.children ?? []) {
      const table = new Table(this, child, record.index.model(child.model));
      this.tables.set(child.name, table);
      const items: Array<Record<string, any>> = data.children?.[child.name] ?? [];
      // A new record may start with new rows; their _key is kept when it is valid.
      if (isNew) table.rows.set(items.map(item => new Row(record, table.model, this, child.name,
        typeof item._key === 'string' && KEY.test(item._key) ? item._key : record.nextKey(), null, true, item)));
      else table.replace(items as RecordData[], data.children_pages?.[child.name], {});
    }
  }

  /** Not saved yet. Reactive: dirty state follows it. */
  get isNew() { return this.fresh.get(); }
  set isNew(value: boolean) { this.fresh.set(value); }

  field(name: string): Field {
    const field = this.fields.get(name);
    if (!field) throw new Error(`${this.model.name} has no field ${name}`);
    return field;
  }

  table(name: string): Table {
    const table = this.tables.get(name);
    if (!table) throw new Error(`${this.model.name} has no table ${name}`);
    return table;
  }

  /** True when this row, its values or its tables differ from what was saved. */
  get dirty(): Derive<boolean> {
    return this.changed ??= runWithOwner(this.owner, () => derive(() => this.isNew
      || [...this.fields.values()].some(field => field.dirty.get())
      || [...this.tables.values()].some(table => table.changed.get()), { name: `${this.label} (dirty)` }));
  }

  /** Server data replaces this row's draft. */
  replace(data: RecordData | Record<string, any>, rowIds: Record<string, number>) {
    this.id = typeof data.id === 'number' ? data.id : this.id;
    this.isNew = false;
    for (const field of this.fields.values()) {
      const value = data[field.name] ?? null;
      field.server.set(value);
      field.value.set(value);
      field.error.set(null);
      field.result.set(READY);
    }
    for (const [name, table] of this.tables) table.replace(data.children?.[name] ?? [], data.children_pages?.[name], rowIds);
  }

  revert() {
    for (const field of this.fields.values()) { field.value.set(field.server.peek()); field.error.set(null); field.result.set(READY); }
    for (const table of this.tables.values()) table.revert();
  }
}

export class Table {
  /** The rows in their current order. Key a list by row.key. */
  readonly rows: Cell<Row[]>;
  /** Saved rows removed in the draft; sent as delete on save. */
  readonly removed = cell<Row[]>([]);
  /** Rows the server holds, and whether only some of them are loaded yet. */
  readonly total = cell(0);
  readonly partial = cell(false);
  readonly error = cell<string | null>(null);
  /** Saved rows in server order. */
  saved: Row[] = [];
  // Rows a partial response left out, kept by id so the page that brings them back reuses them.
  private parked = new Map<number, Row>();
  private touched?: Derive<boolean>;

  constructor(readonly owner: Row, readonly meta: ChildSchema, readonly model: ModelSchema) {
    this.rows = cell<Row[]>([], { name: `${owner.label}.${meta.name}` });
  }

  get name() { return this.meta.name; }
  private get record() { return this.owner.record; }

  /** True when rows were added, removed, moved or changed. */
  get changed(): Derive<boolean> {
    return this.touched ??= runWithOwner(this.owner.owner, () => derive(() => {
      const rows = this.rows.get();
      if (this.removed.get().length) return true;
      const kept = this.saved.filter(row => rows.includes(row));
      let saved = 0;
      for (const row of rows) {
        if (row.isNew || (row !== kept[saved++])) return true;
        if (row.dirty.get()) return true;
      }
      return false;
    }, { name: `${this.owner.label}.${this.name} (changed)` }));
  }

  /** The API path of this table: ['lines'] or ['lines', 17, 'parts']. */
  apiPath(): Array<string | number> {
    const path: Array<string | number> = [this.name];
    for (let row: Row | null = this.owner; row?.parent; row = row.parent) path.unshift([...row.parent.tables.values()].find(table => table.rows.peek().includes(row!))!.name, row.id!);
    return path;
  }

  /** Add a new row. Initial values and calculations apply at once. A valid _key in values names the row. */
  add(values: Record<string, unknown> = {}): Row {
    const record = this.record;
    record.assertEditable();
    const { _key, ...fields } = values;
    const key = typeof _key === 'string' && KEY.test(_key) ? _key : record.nextKey();
    return batch(() => {
      const row = new Row(record, this.model, this.owner, this.name, key, null, true, fields);
      record.session?.add(this, row, fields);
      this.rows.set([...this.rows.peek(), row]);
      return row;
    });
  }

  remove(row: Row) {
    this.record.assertEditable();
    batch(() => {
      this.record.session?.remove(row);
      this.rows.set(this.rows.peek().filter(item => item !== row));
      if (row.isNew) row.dispose(); else this.removed.set([...this.removed.peek(), row]);
    });
  }

  /** Move a row to a position in the current order. */
  move(row: Row, to: number) {
    this.record.assertEditable();
    const rows = this.rows.peek().filter(item => item !== row);
    if (rows.length === this.rows.peek().length || to < 0 || to > rows.length) throw new Error('Use a row of this table and a valid position');
    rows.splice(to, 0, row);
    batch(() => {
      this.record.session?.reorder(this, rows);
      this.rows.set(rows);
    });
  }

  /** Server rows replace this table's draft; rows keep their objects by id or by the _key they were created with. */
  replace(items: RecordData[], page: { total: number; partial: boolean } | undefined, rowIds: Record<string, number>) {
    const byId = new Map<number, Row>(), current = [...this.rows.peek(), ...this.removed.peek(), ...this.parked.values()];
    this.parked = new Map();
    for (const row of current) {
      const id = row.isNew ? rowIds[row.key] : row.id;
      if (id !== undefined && id !== null) byId.set(id, row);
    }
    const next = items.map(item => {
      const existing = byId.get(item.id);
      if (existing) { byId.delete(item.id); existing.replace(item, rowIds); return existing; }
      return new Row(this.record, this.model, this.owner, this.name, String(item.id), item.id, false, item);
    });
    const placed = new Set(next), unplaced = new Set(byId.values());
    for (const row of current) if (!placed.has(row) && !unplaced.has(row)) row.dispose();
    for (const [id, row] of byId) { if (page?.partial) this.parked.set(id, row); else row.dispose(); }
    this.saved = next.slice();
    this.rows.set(next);
    this.removed.set([]);
    this.error.set(null);
    this.total.set(page?.total ?? next.length);
    this.partial.set(page?.partial ?? false);
  }

  /** Append a page of saved rows read from the lines endpoint. */
  appendSaved(items: RecordData[]) {
    // Rows that shifted onto this page because others were added meanwhile are already here.
    const known = new Set(this.rows.peek().map(row => row.id));
    const rows = items.filter(item => !known.has(item.id)).map(item => {
      const parked = this.parked.get(item.id);
      if (!parked) return new Row(this.record, this.model, this.owner, this.name, String(item.id), item.id, false, item);
      this.parked.delete(item.id);
      parked.replace(item, {});
      return parked;
    });
    this.saved = [...this.saved, ...rows];
    this.rows.set([...this.rows.peek(), ...rows]);
  }

  /** Every row is loaded. */
  complete() {
    this.partial.set(false);
    for (const row of this.parked.values()) row.dispose();
    this.parked = new Map();
  }

  revert() {
    for (const row of this.rows.peek()) if (row.isNew) row.dispose();
    for (const row of this.saved) row.revert();
    this.rows.set(this.saved.slice());
    this.removed.set([]);
    this.error.set(null);
  }
}

// ---------- the record ----------

export interface RecordOptions {
  api: ApiClient;
  schema: AppSchema | SchemaIndex;
  model: string;
  /** Today's date for today() in calculations; defaults to the date in GST. */
  today?: () => string;
}

const PAGE = 100;

export class RecordStore {
  readonly index: SchemaIndex;
  readonly meta: ModelSchema;
  readonly api: ApiClient;
  /** The ETag of the saved version the draft is based on. */
  readonly etag = cell<string | null>(null, { name: 'record.etag' });
  /** Set when a save found the record changed on the server (409, 412, 428). Reload to continue. */
  readonly conflict = cell<ApiError | null>(null, { name: 'record.conflict' });
  /** A message for the whole form: an error the server did not tie to a field. */
  readonly formError = cell<string | null>(null, { name: 'record.formError' });
  readonly saving = cell(false, { name: 'record.saving' });
  /** Every row is loaded and calculations are attached: the record can be edited. */
  readonly ready = cell(false, { name: 'record.ready' });
  /** The draft differs from the saved record. */
  readonly dirty: Derive<boolean>;
  /** The calculation session, when the schema ships a plan. */
  readonly session: Session | null;
  owner: unknown;
  private current = cell<Row | null>(null, { name: 'record.root' });
  private keys = 0;
  private readonly stop: () => void;

  constructor(options: RecordOptions) {
    this.api = options.api;
    this.index = options.schema instanceof SchemaIndex ? options.schema : new SchemaIndex(options.schema);
    this.meta = this.index.model(options.model);
    const plan = this.meta.calculation?.plan;
    this.session = plan ? new Session(this.api, plan, options.today ?? (() => todayGst())) : null;
    let dirty!: Derive<boolean>;
    this.stop = scope(() => {
      this.owner = getOwner();
      dirty = derive(() => this.current.get()?.dirty.get() ?? false, { name: 'record.dirty' });
    }, { name: `record ${options.model}` });
    this.dirty = dirty;
  }

  /** The record's own row; tables hang off it. */
  get root(): Row {
    const root = this.current.peek();
    if (!root) throw new Error('Open or create the record first');
    return root;
  }
  get id() { return this.current.peek()?.id ?? null; }
  /** The live FiniteDocument, for debugging (explain(), trace). */
  get engine(): FiniteDocument | null { return this.session?.engine ?? null; }
  field(name: string) { return this.root.field(name); }
  table(name: string) { return this.root.table(name); }
  /** Actions the user may run, and the state machine when the model has one. */
  get actions() { return this.meta.actions ?? []; }
  get state() { return this.meta.state ?? null; }

  /** Load a saved record. By default every row is paged in so the record can be edited. */
  async open(id: number, options: { loadAll?: boolean } = {}) {
    const { data, etag } = await this.api.get(this.meta.name, id);
    this.applyServer(data, etag);
    if (options.loadAll !== false) await this.loadAll();
  }

  /** Start a new, unsaved record. Defaults, initial values and calculations apply. */
  async create(values: Record<string, unknown> = {}) {
    // Field values, and optionally children: {table: [rows]} for new rows.
    this.detach();
    batch(() => {
      this.current.peek()?.dispose();
      this.current.set(new Row(this, this.meta, null, null, 'new', null, true, values));
      this.etag.set(null);
    });
    this.attach();
  }

  /** Page in rows the detail left out (Tables over 100 rows), then attach calculations. */
  async loadAll() {
    for (let tables = this.partialTables(); tables.length; tables = this.partialTables()) {
      for (const table of tables) {
        let offset = table.rows.peek().length;
        while (offset < table.total.peek()) {
          const page = await this.api.lines(this.meta.name, this.root.id!, table.apiPath(), { limit: PAGE, offset });
          if (!page.items.length) break;
          table.appendSaved(page.items);
          table.total.set(page.total);
          offset += page.items.length;
        }
        table.complete();
      }
    }
    this.attach();
  }

  private partialTables(): Table[] {
    const found: Table[] = [];
    const visit = (row: Row) => {
      for (const table of row.tables.values()) {
        if (table.partial.peek()) found.push(table);
        table.rows.peek().forEach(visit);
      }
    };
    visit(this.root);
    return found;
  }

  /** The server's answer replaces the draft. Rows keep their objects (and DOM) by id. */
  applyServer(data: RecordData, etag: string | null) {
    this.detach();
    batch(() => {
      const root = this.current.peek();
      if (root && (root.isNew || root.id === data.id)) root.replace(data, data._row_ids ?? {});
      else {
        root?.dispose();
        this.current.set(new Row(this, this.meta, null, null, String(data.id), data.id, false, data));
      }
      this.etag.set(etag);
      this.conflict.set(null);
      this.formError.set(null);
    });
  }

  /** Save the draft. Returns false when the server refused it; see conflict, formError and field errors. */
  async save(): Promise<boolean> {
    this.assertEditable();
    const root = this.root, body = buildChanges(root);
    if (!root.isNew && !Object.keys(body).length) return true;
    const calculation = this.meta.calculation;
    if (calculation?.plan) body._calculation = { language: calculation.language, model_schema_hash: calculation.model_schema_hash };
    this.saving.set(true);
    try {
      const response = root.isNew ? await this.api.create(this.meta.name, body) : await this.api.update(this.meta.name, root.id!, body, this.etag.peek());
      this.applyServer(response.data, response.etag);
      await this.loadAll();
      return true;
    } catch (error) {
      return this.refused(error);
    } finally {
      this.saving.set(false);
    }
  }

  /** Run a model action on the saved record. The record must have no unsaved changes. */
  async runAction(name: string, args: Record<string, unknown> = {}): Promise<boolean> {
    return this.command(() => this.api.action(this.meta.name, this.root.id!, name, args, this.etag.peek()));
  }

  /** Move the record to another state (State and Move on the model). */
  async move(to: string, body: Record<string, unknown> = {}): Promise<boolean> {
    return this.command(() => this.api.move(this.meta.name, this.root.id!, to, this.etag.peek(), body));
  }

  private async command(send: () => Promise<{ data: any; etag: string | null }>) {
    if (this.dirty.peek()) { this.formError.set('Save or discard your changes first.'); return false; }
    this.saving.set(true);
    try {
      const response = await send();
      const data = response.data && 'job' in response.data && 'record' in response.data ? response.data.record : response.data;
      // Routes that answer without an ETag (legacy actions, jobs) are read again to get one.
      if (response.etag) { this.applyServer(data, response.etag); await this.loadAll(); }
      else await this.reload();
      return true;
    } catch (error) {
      return this.refused(error);
    } finally {
      this.saving.set(false);
    }
  }

  private refused(error: unknown): false {
    if (error instanceof ValidationError) this.showErrors(error.fields);
    else if (error instanceof ConflictError) this.conflict.set(error);
    else if (error instanceof ApiError) this.formError.set(error.message);
    else throw error;
    return false;
  }

  /** Put server field errors (lines.4.qty) on their fields; the rest go to formError. */
  showErrors(errors: FieldErrors) {
    const unplaced: string[] = [];
    batch(() => {
      for (const [path, message] of Object.entries(errors)) {
        const target = path === '_form' ? null : findField(this.root, path);
        if (target) target.error.set(message); else unplaced.push(message);
      }
      this.formError.set(unplaced.length ? unplaced.join(' ') : null);
    });
  }

  /** Discard the draft and return to the saved values. */
  async revert() {
    this.detach();
    batch(() => { this.root.revert(); this.conflict.set(null); this.formError.set(null); });
    this.attach();
  }

  /** Read the record again from the server, discarding the draft. */
  async reload() {
    this.session?.forget();
    await this.open(this.root.id!);
  }

  /** Ask the server to evaluate the draft (for calculations the browser cannot run). */
  async preview(): Promise<DraftResult> {
    const calculation = this.meta.calculation;
    return this.api.draft(this.meta.name, { id: this.root.id, values: buildChanges(this.root),
      ...(calculation ? { calculation: { language: calculation.language, model_schema_hash: calculation.model_schema_hash } } : {}) });
  }

  /** Wait until lookups and other background reads settle. */
  async idle() { await this.session?.idle(); }

  dispose() {
    this.detach();
    this.current.peek()?.dispose();
    this.stop();
  }

  // ---------- used by fields and tables ----------

  nextKey() { return `n${++this.keys}`; }

  assertEditable() {
    if (!this.ready.peek()) throw new Error('This record is still loading. Load every row before editing it.');
    if (this.saving.peek()) throw new Error('This record is being saved.');
  }

  write(field: Field, value: unknown) {
    if (field.calculated) throw new Error(`${field.label} is calculated`);
    this.assertEditable();
    if (field.rules.peek().locked) throw new Error(`${field.label} is locked`);
    batch(() => {
      field.value.set(value);
      field.error.set(null);
      this.session?.set(field.row, field.name, value);
    });
  }

  private detach() {
    this.ready.set(false);
    this.session?.stop();
  }

  // Bind the rows to a fresh calculation session; the record can then be edited.
  private attach() {
    this.detach();
    batch(() => {
      this.session?.start(this.root);
      this.ready.set(true);
    });
  }
}
