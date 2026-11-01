// Finite-v1 document evaluator. It mirrors evlet/calc.py over the same serialized plan.
// Pure: no SolidJS, DOM, network or clock. The server remains the persistence authority.
export const LANGUAGE = 'finite-v1';
export const PROFILE = 'finite40-hu18-v1';
const MAX_DIGITS = 40, MAX_SCALE = 36, DIVISION_SCALE = 18, SAFE_INTEGER = 2n ** 53n - 1n, STORED_LIMIT = 2n ** 63n - 1n;

export class CalcError extends Error {
  constructor(public code: string, message: string, public causes: string[] = []) { super(code + ': ' + message); }
}

// Exact numbers: value = coef * 10^-scale, normalized (no trailing zeros, no negative zero).
export class Num {
  constructor(readonly coef: bigint, readonly scale: number) {}
}
export function normalize(coef: bigint, scale: number): Num {
  if (coef === 0n) return new Num(0n, 0);
  while (scale > 0 && coef % 10n === 0n) { coef /= 10n; scale--; }
  if (scale > MAX_SCALE || (coef < 0n ? -coef : coef).toString().length > MAX_DIGITS)
    throw new CalcError('COMPUTE_RANGE_ERROR', 'Calculation exceeds the supported range');
  return new Num(coef, scale);
}
const NUMBER = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/;
export function parse(value: unknown): Num {
  if (value instanceof Num) return value;
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new CalcError(Number.isInteger(value) ? 'COMPUTE_RANGE_ERROR' : 'COMPUTE_TYPE_ERROR', 'Use an exact decimal string');
    return normalize(BigInt(value), 0);
  }
  if (typeof value === 'string' && value.length <= 128 && NUMBER.test(value)) {
    const negative = value.startsWith('-');
    const [whole, fraction = ''] = (negative ? value.slice(1) : value).split('.');
    const coef = BigInt(whole + fraction);
    return normalize(negative ? -coef : coef, fraction.length);
  }
  throw new CalcError('COMPUTE_TYPE_ERROR', 'Use an exact decimal string');
}
export function text(value: Num, scale: number | null = null): string {
  let coef = value.coef, current = value.scale;
  if (scale !== null && scale > current) { coef *= 10n ** BigInt(scale - current); current = scale; }
  const digits = (coef < 0n ? -coef : coef).toString().padStart(current + 1, '0');
  const body = current ? digits.slice(0, digits.length - current) + '.' + digits.slice(digits.length - current) : digits;
  return (coef < 0n ? '-' : '') + body;
}
function aligned(a: Num, b: Num): [bigint, bigint, number] {
  const scale = Math.max(a.scale, b.scale);
  return [a.coef * 10n ** BigInt(scale - a.scale), b.coef * 10n ** BigInt(scale - b.scale), scale];
}
export function compare(a: Num, b: Num) { const [x, y] = aligned(a, b); return x === y ? 0 : x < y ? -1 : 1; }
export function add(a: Num, b: Num) { const [x, y, s] = aligned(a, b); return normalize(x + y, s); }
export function sub(a: Num, b: Num) { const [x, y, s] = aligned(a, b); return normalize(x - y, s); }
export function mul(a: Num, b: Num) { return normalize(a.coef * b.coef, a.scale + b.scale); }
// Nearest integer to numerator/denominator with exact ties away from zero (HALF_UP).
function halfUp(numerator: bigint, denominator: bigint) {
  const negative = (numerator < 0n) !== (denominator < 0n);
  const n = numerator < 0n ? -numerator : numerator, d = denominator < 0n ? -denominator : denominator;
  let quotient = n / d;
  if (2n * (n % d) >= d) quotient++;
  return negative ? -quotient : quotient;
}
export function div(a: Num, b: Num) {
  if (b.coef === 0n) throw new CalcError('COMPUTE_DIVISION_BY_ZERO', 'Division by zero');
  const power = DIVISION_SCALE + b.scale - a.scale;
  return normalize(halfUp(a.coef * 10n ** BigInt(Math.max(power, 0)), b.coef * 10n ** BigInt(Math.max(-power, 0))), DIVISION_SCALE);
}
export function quantize(value: Num, scale: number) {
  return value.scale <= scale ? value : normalize(halfUp(value.coef, 10n ** BigInt(value.scale - scale)), scale);
}
export function total(values: Num[]) {
  if (!values.length) return new Num(0n, 0);
  const scale = Math.max(...values.map(value => value.scale));
  return normalize(values.reduce((sum, value) => sum + value.coef * 10n ** BigInt(scale - value.scale), 0n), scale);
}

export type Value = Num | boolean | string | null;
export type Result = {state: 'ready'; value: Value} | {state: 'pending' | 'error'; code: string; causes: string[]};
const ready = (value: Value): Result => ({state: 'ready', value});
const sorted = (causes: string[]) => [...new Set(causes)].sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
const pending = (code: string, causes: string[]): Result => ({state: 'pending', code, causes: sorted(causes)});
const failed = (code: string, causes: string[]): Result => ({state: 'error', code, causes: sorted(causes)});
function minimum(codes: string[]) { return codes.reduce((a, b) => a < b ? a : b); }
function blocked(results: Result[]): Result | null {
  const errors = results.filter(result => result.state === 'error') as any[];
  if (errors.length) return failed(minimum(errors.map(item => item.code)), errors.flatMap(item => item.causes));
  const waiting = results.filter(result => result.state === 'pending') as any[];
  if (waiting.length) return pending(minimum(waiting.map(item => item.code)), waiting.flatMap(item => item.causes));
  return null;
}
function same(a: Result, b: Result | undefined) {
  if (!b || a.state !== b.state) return false;
  if (a.state === 'ready') {
    const x = a.value, y = (b as any).value;
    return x instanceof Num && y instanceof Num ? x.coef === y.coef && x.scale === y.scale : x === y;
  }
  return a.code === (b as any).code && a.causes.join('\u0000') === (b as any).causes.join('\u0000');
}
export function wire(value: Value, scale: number | null = null) { return value instanceof Num ? text(value, scale) : value; }
export function publicResult(result: Result) {
  return result.state === 'ready' ? {state: 'ready', value: wire(result.value)} : {state: result.state, code: result.code, causes: result.causes};
}

class Budget {
  steps = 0;
  constructor(private limit: number) {}
  charge(amount = 1) {
    this.steps += amount;
    if (this.steps > this.limit) throw new CalcError('CALCULATION_LIMIT_EXCEEDED', 'The calculation exceeded its evaluation budget');
  }
}

export interface Row {
  handle: string; path: string[]; values: Record<string, any>; parent: Row | null;
  children: Record<string, Row[]>; id: number | null; key: string; label: string; order: number;
}

class Scope {
  constructor(readonly document: FiniteDocument, readonly row: Row, readonly member: Row | null = null, readonly at = '') {}
  read(scope: string, field: string): Result {
    const target = scope === 'row' ? this.member : scope === 'parent' ? this.row.parent : this.row;
    return target ? this.document.result(target, field) : pending('PARENT_CONTEXT_REQUIRED', [this.at]);
  }
  members(table: string) { return (this.row.children[table] || []).map(member => new Scope(this.document, this.row, member, this.at)); }
  address(field: string) { return (this.member || this.row).label + field; }
}

const OPERATIONS = new Set(['literal', 'ref', 'aggregate', 'add', 'sub', 'mul', 'div', 'neg', 'eq', 'ne', 'lt', 'lte', 'gt', 'gte',
  'and', 'or', 'not', 'round', 'coalesce', 'safe_divide', 'bool_to_decimal']);

function guarded(operation: () => Num, scope: Scope): Result {
  try { return ready(operation()); }
  catch (exc) { if (exc instanceof CalcError) return failed(exc.code, [scope.at]); throw exc; }
}
function missing(results: Result[], scope: Scope) {
  return results.some(result => (result as any).value === null) ? pending('COMPUTE_INPUT_MISSING', [scope.at]) : null;
}

export function evaluate(node: any, scope: Scope, budget: Budget): Result {
  budget.charge();
  const op = node?.$expr;
  if (!OPERATIONS.has(op)) throw new CalcError('UNSUPPORTED_EXPRESSION', 'Unknown operation');
  if (op === 'literal') return ready(node.type === 'decimal' ? parse(node.value) : node.value);
  if (op === 'ref') return scope.read(node.scope, node.field);
  if (op === 'aggregate') return aggregate(node, scope, budget);
  if (op === 'coalesce') {
    const first = evaluate(node.args[0], scope, budget);
    return first.state === 'ready' && first.value === null ? evaluate(node.args[1], scope, budget) : first;
  }
  if (op === 'and' || op === 'or') {
    const results = node.args.map((arg: any) => evaluate(arg, scope, budget));
    const stop = blocked(results);
    if (stop) return stop;
    if (results.some((result: any) => result.value === null)) return pending('COMPUTE_INPUT_MISSING', [scope.at]);
    const values = results.map((result: any) => result.value);
    return ready(op === 'and' ? values.every(Boolean) : values.some(Boolean));
  }
  if (op === 'safe_divide') {
    const pair = node.args.slice(0, 2).map((arg: any) => evaluate(arg, scope, budget));
    const stop = blocked(pair) || missing(pair, scope);
    if (stop) return stop;
    const [numerator, denominator] = pair.map((result: any) => result.value as Num);
    if (denominator.coef === 0n) return evaluate(node.args[2], scope, budget);
    return guarded(() => div(numerator, denominator), scope);
  }
  const results: Result[] = node.args.map((arg: any) => evaluate(arg, scope, budget));
  const stop = blocked(results);
  if (stop) return stop;
  const values = results.map(result => (result as any).value);
  if (op === 'eq' || op === 'ne') {
    const [a, b] = values;
    const equal = a === null || b === null ? a === null && b === null : a instanceof Num ? compare(a, b) === 0 : a === b;
    return ready(op === 'eq' ? equal : !equal);
  }
  const absent = missing(results, scope);
  if (absent) return absent;
  if (op === 'not') return ready(!values[0]);
  if (op === 'bool_to_decimal') return ready(new Num(values[0] ? 1n : 0n, 0));
  if (op === 'lt' || op === 'lte' || op === 'gt' || op === 'gte') {
    const order = compare(values[0], values[1]);
    return ready({lt: order < 0, lte: order <= 0, gt: order > 0, gte: order >= 0}[op as 'lt']);
  }
  if (op === 'neg') { const value = values[0] as Num; return ready(new Num(-value.coef, value.scale)); }
  if (op === 'round') return guarded(() => quantize(values[0], values[1] as number), scope);
  const operation = {add, sub, mul, div}[op as 'add'];
  return guarded(() => operation(values[0], values[1]), scope);
}

function aggregate(node: any, scope: Scope, budget: Budget): Result {
  const selected: Num[] = [], problems: Result[] = [];
  let count = 0;
  for (const member of scope.members(node.table)) {
    budget.charge();
    if (node.where !== null) {
      const keep = evaluate(node.where, member, budget);
      if (keep.state !== 'ready') { problems.push(keep); continue; }
      if (keep.value === null) { problems.push(pending('COMPUTE_INPUT_MISSING', [scope.at])); continue; }
      if (!keep.value) continue;
    }
    if (node.field === null) { count++; continue; }
    let value = member.read('row', node.field);
    if (value.state === 'ready' && value.value === null) value = pending('COMPUTE_INPUT_MISSING', [member.address(node.field)]);
    if (value.state !== 'ready') { problems.push(value); continue; }
    selected.push(value.value as Num);
  }
  const stop = blocked(problems);
  if (stop) return stop;
  if (node.kind === 'count') return ready(new Num(BigInt(count), 0));
  if (node.kind === 'sum') return guarded(() => total(selected), scope);
  if (!selected.length) return ready(null);
  return ready(selected.reduce((best, value) => (compare(value, best) < 0) === (node.kind === 'min') ? value : best));
}

function convert(result: Result, field: any, at: string): Result {
  if (result.state !== 'ready' || result.value === null || field.result_type !== 'decimal') return result;
  const value = result.value as Num, scale = field.scale;
  if (scale !== null) {
    const exact = quantize(value, scale);
    if (exact.coef !== value.coef || exact.scale !== value.scale) return failed('COMPUTE_SCALE_ERROR', [at]);
  }
  const magnitude = value.coef < 0n ? -value.coef : value.coef;
  if (field.target_type === 'integer' && magnitude > SAFE_INTEGER) return failed('COMPUTE_RANGE_ERROR', [at]);
  if (field.stored && scale !== null && magnitude * 10n ** BigInt(scale - value.scale) > STORED_LIMIT) return failed('COMPUTE_RANGE_ERROR', [at]);
  return result;
}

function sourceValue(meta: any, raw: any, at: string): Result {
  if (raw && typeof raw === 'object' && raw.$state === 'pending') return pending('COMPUTE_INPUT_MISSING', [at]);
  if (raw === undefined || raw === null || (raw === '' && meta.type !== 'text')) return ready(null);
  try {
    if (meta.type === 'decimal') return ready(parse(raw));
    if (meta.type === 'boolean' && typeof raw !== 'boolean') throw new CalcError('COMPUTE_TYPE_ERROR', 'Expected a Boolean');
    return ready(raw);
  } catch (exc) {
    if (exc instanceof CalcError) return failed(exc.code, [at]);
    throw exc;
  }
}

const address = (path: string[], field: string) => path.map(name => name + '[*].').join('') + field;
const KEY = /^[A-Za-z0-9_-]{1,64}$/;
type Node = string;
const nodeKey = (handle: string, field: string): Node => handle + '\u0000' + field;
const nodeParts = (node: Node) => node.split('\u0000') as [string, string];

export function checkPlan(plan: any) {
  if (!plan || plan.plan_version !== 1 || plan.language !== LANGUAGE || plan.numeric_profile !== PROFILE || plan.expression_version !== 1)
    throw new CalcError('SCHEMA_VERSION_MISMATCH', 'Unsupported calculation plan, language or profile');
}

// A browser document session: one bound graph, synchronous batches, one publication per batch.
export class FiniteDocument {
  readonly schema = new Map<string, any>();
  readonly templates = new Map<string, Array<[number, any]>>();
  readonly fields = new Map<string, any>();
  rows = new Map<string, Row>();
  results = new Map<Node, Result>();
  counts = new Map<Node, number>();
  deps = new Map<Node, Set<Node>>();
  dependents = new Map<Node, Set<Node>>();
  order: Node[] = [];
  observers = new Map<Node, Array<(result: Result) => void>>();
  tableObservers = new Map<Node, Array<(handles: string[]) => void>>();
  revision = 0; structureRevision = 0; trace: any = {};
  root: Row;
  private next = 0;
  private current: any = null;
  private publishing = false;
  private disposed = false;

  constructor(readonly plan: any, data: any = {}, private budget = plan?.limits?.evaluation_steps) {
    checkPlan(plan);
    for (const entry of Object.values<any>(plan.tables)) this.schema.set(entry.path.join('/'), entry);
    plan.fields.forEach((field: any, position: number) => {
      const path = field.path.join('/');
      if (!this.templates.has(path)) this.templates.set(path, []);
      this.templates.get(path)!.push([position, field]);
      this.fields.set(path + '\u0000' + field.field, field);
    });
    this.root = this.build(data || {}, [], null, null);
    this.bind();
    this.settle(new Set(), new Set(this.deps.keys()));
    this.revision = 1;
  }

  private handle() { return 'h' + ++this.next; }

  private build(data: any, path: string[], owner: Row | null, created: boolean | null): Row {
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Rows must be objects');
    const entry = this.schema.get(path.join('/'));
    if (!entry) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown collection ' + address(path, ''));
    const id = data.id ?? null;
    if (id !== null && (!Number.isSafeInteger(id) || id < 1)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row IDs must be positive integers');
    const handle = this.handle();
    const key = data._key ?? null;
    if (key !== null && (typeof key !== 'string' || !KEY.test(key)))
      throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row keys use at most 64 letters, digits, hyphens or underscores');
    const row: Row = {handle, path, values: {}, parent: owner, children: {}, id, key: key || (id ? String(id) : handle), label: '', order: 0};
    this.rows.set(handle, row);
    if (this.rows.size > this.plan.limits.document_rows) throw new CalcError('CALCULATION_LIMIT_EXCEEDED', 'The document has too many rows');
    const fresh = created === null ? id === null : created;
    for (const [name, value] of Object.entries(data)) {
      if (name === 'id' || name === '_key' || name === 'children' || name === '_handle') continue;
      const meta = entry.fields[name];
      if (!meta) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown field ' + name);
      if (meta.computed && created) throw new CalcError('COMPUTED_FIELD_WRITE', name + ' is calculated');
      if (!meta.computed) row.values[name] = value;
    }
    if (fresh) this.defaults(row, entry);
    const children = data.children || {};
    if (typeof children !== 'object' || Object.keys(children).some(name => !entry.tables.includes(name)))
      throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown child collection');
    for (const table of entry.tables) {
      const items = children[table] || [];
      if (!Array.isArray(items) || items.length > this.plan.limits.collection_rows)
        throw new CalcError('CALCULATION_LIMIT_EXCEEDED', table + ' exceeds the collection row limit');
      row.children[table] = [];
      for (const item of items) this.place(row, table, this.build(item, [...path, table], row, created), row.children[table].length);
    }
    return row;
  }

  private defaults(row: Row, entry: any) {
    for (const [name, meta] of Object.entries<any>(entry.fields)) {
      const fallback = meta.default;
      if (name in row.values || !fallback || fallback.server) continue;
      if ('ref' in fallback) {
        const parts = fallback.ref.split('.');
        if (parts[0] !== 'parent' || parts.length !== 2 || !row.parent) continue;
        const source = this.rows.has(row.parent.handle) ? this.result(row.parent, parts[1]) : ready(null);
        row.values[name] = source.state === 'ready' ? wire(source.value) : {$state: 'pending'};
      } else row.values[name] = fallback.value;
    }
  }

  private place(owner: Row, table: string, row: Row, at: number) {
    const items = owner.children[table];
    if (items.some(item => item !== row && item.key === row.key)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', `Repeated row key ${row.key} in ${table}`);
    if (row.id !== null && items.some(item => item !== row && item.id === row.id)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', `Repeated row ID ${row.id} in ${table}`);
    if (items.length >= this.plan.limits.collection_rows) throw new CalcError('CALCULATION_LIMIT_EXCEEDED', table + ' exceeds the collection row limit');
    items.splice(at, 0, row);
    row.parent = owner;
  }

  private labels(row: Row, label: string) {
    row.label = label;
    for (const [table, items] of Object.entries(row.children))
      items.forEach((item, position) => { item.order = position; this.labels(item, label + table + '.' + item.key + '.'); });
  }

  private sortKey(row: Row) {
    const chain: number[] = [];
    for (let item: Row | null = row; item; item = item.parent) chain.push(item.order);
    return chain.reverse();
  }

  private bind() {
    this.labels(this.root, '');
    const deps = new Map<Node, Set<Node>>(), order: Array<[number, number[], Node]> = [];
    let edges = 0;
    for (const [handle, row] of this.rows) {
      for (const [position, field] of this.templates.get(row.path.join('/')) || []) {
        const node = nodeKey(handle, field.field), inputs = new Set<Node>();
        for (const [scope, table, name] of field.reads) {
          if (scope === 'self') inputs.add(nodeKey(handle, name));
          else if (scope === 'parent' && row.parent) inputs.add(nodeKey(row.parent.handle, name));
          else if (scope === 'membership') inputs.add(nodeKey(handle, '#' + table));
          else if (scope === 'member') for (const member of row.children[table] || []) inputs.add(nodeKey(member.handle, name));
        }
        deps.set(node, inputs);
        edges += inputs.size;
        order.push([position, this.sortKey(row), node]);
      }
    }
    if (deps.size > this.plan.limits.graph_nodes || edges > this.plan.limits.graph_edges)
      throw new CalcError('CALCULATION_LIMIT_EXCEEDED', 'The document graph exceeds its node or edge limit');
    order.sort((a, b) => a[0] - b[0] || compareChains(a[1], b[1]));
    this.deps = deps;
    this.order = order.map(item => item[2]);
    this.dependents = new Map();
    for (const [node, inputs] of deps) for (const input of inputs) {
      if (!this.dependents.has(input)) this.dependents.set(input, new Set());
      this.dependents.get(input)!.add(node);
    }
  }

  result(row: Row, field: string): Result {
    const node = nodeKey(row.handle, field);
    if (this.deps.has(node)) return this.results.get(node) || pending('COMPUTE_INPUT_MISSING', [row.label + field]);
    const meta = this.schema.get(row.path.join('/')).fields[field];
    return meta ? sourceValue(meta, row.values[field], row.label + field) : failed('UNSUPPORTED_EXPRESSION', [row.label + field]);
  }

  private settle(changed: Set<Node>, fresh: Set<Node>) {
    const budget = new Budget(this.budget), updated = new Set<Node>();
    let evaluated = 0, skipped = 0;
    for (const node of this.order) {
      const inputs = this.deps.get(node)!;
      if (!fresh.has(node) && ![...inputs].some(input => changed.has(input))) { skipped++; continue; }
      const [handle, name] = nodeParts(node);
      const row = this.rows.get(handle)!, field = this.fields.get(row.path.join('/') + '\u0000' + name);
      const at = row.label + name;
      const outcome = convert(evaluate(field.expression, new Scope(this, row, null, at), budget), field, at);
      evaluated++;
      this.counts.set(node, (this.counts.get(node) || 0) + 1);
      if (!same(outcome, this.results.get(node))) { this.results.set(node, outcome); changed.add(node); updated.add(node); }
    }
    this.trace = {evaluated, skipped, steps: budget.steps, changed: [...updated].map(node => this.address(node)).sort()};
    return updated;
  }

  private address(node: Node) {
    const [handle, field] = nodeParts(node);
    return (this.rows.get(handle)?.label ?? handle + '.') + field;
  }

  batch(run: (commands: FiniteDocument) => unknown) {
    this.usable();
    const outer = this.current === null;
    if (outer) this.current = {snapshot: this.capture(), sources: new Set<Node>(), membership: new Set<Node>(), moved: new Set<string>(), structure: false, order: new Set<Node>()};
    try {
      const value: any = run(this);
      if (value && typeof value.then === 'function')
        throw new CalcError('ASYNC_BATCH_UNSUPPORTED', 'A calculation batch cannot await; apply network results as a later batch');
    } catch (exc) {
      if (outer) { this.restore(this.current.snapshot); this.current = null; }
      throw exc;
    }
    if (!outer) return;
    const batch = this.current;
    this.current = null;
    let failures: unknown[];
    try { failures = this.commit(batch); }
    catch (exc) { this.restore(batch.snapshot); throw exc; }
    if (failures.length) throw failures[0];
  }

  private command<T>(run: () => T): T {
    if (this.current === null) { let value!: T; this.batch(() => { value = run(); }); return value; }
    this.usable();
    return run();
  }

  private usable() {
    if (this.disposed) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'This document session was disposed');
    if (this.publishing) throw new CalcError('REACTIVE_REENTRANCY', 'Observers cannot change the document during publication');
  }

  private row(handle: string) {
    const row = this.rows.get(handle);
    if (!row) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown row ' + handle);
    return row;
  }

  set(handle: string, field: string, value: unknown) {
    this.command(() => {
      const row = this.row(handle), meta = this.schema.get(row.path.join('/')).fields[field];
      if (!meta) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown field ' + field);
      if (meta.computed) throw new CalcError('COMPUTED_FIELD_WRITE', row.label + field + ' is calculated');
      row.values[field] = value;
      this.current.sources.add(nodeKey(handle, field));
    });
  }

  addRow(owner: string, table: string, values: any = {}) {
    return this.command(() => {
      const parent = this.row(owner);
      if (!this.schema.get(parent.path.join('/')).tables.includes(table)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown collection ' + table);
      if (values?.id != null) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'New rows cannot carry persisted IDs');
      const row = this.build({...values}, [...parent.path, table], parent, true);
      this.place(parent, table, row, parent.children[table].length);
      this.structure(owner, table);
      return row.handle;
    });
  }

  removeRow(handle: string) {
    this.command(() => {
      const row = this.row(handle);
      if (!row.parent) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'The root cannot be removed');
      const table = row.path[row.path.length - 1];
      row.parent.children[table] = row.parent.children[table].filter(item => item !== row);
      this.structure(row.parent.handle, table);
      this.drop(row);
    });
  }

  replaceRows(owner: string, table: string, items: any[]) {
    this.command(() => {
      const parent = this.row(owner);
      if (!this.schema.get(parent.path.join('/')).tables.includes(table) || !Array.isArray(items)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown collection ' + table);
      const existing = new Map(parent.children[table].filter(row => row.id !== null).map(row => [row.id, row]));
      const old = [...parent.children[table]], seen = new Set<number>();
      parent.children[table] = [];
      for (const data of items) {
        const id = data?.id ?? null;
        let row: Row;
        if (id !== null) {
          if (!existing.has(id) || seen.has(id)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row IDs must belong to this collection and appear once');
          seen.add(id);
          row = existing.get(id)!;
          for (const [name, value] of Object.entries(data)) {
            if (name === 'id' || name === '_key' || name === 'children') continue;
            const meta = this.schema.get(row.path.join('/')).fields[name];
            if (!meta || meta.computed) throw new CalcError(meta ? 'COMPUTED_FIELD_WRITE' : 'DOCUMENT_STRUCTURE_ERROR', name + ' cannot be written');
            row.values[name] = value;
            this.current.sources.add(nodeKey(row.handle, name));
          }
        } else row = this.build(data, [...parent.path, table], parent, true);
        this.place(parent, table, row, parent.children[table].length);
      }
      for (const row of old) if (!parent.children[table].includes(row)) this.drop(row);
      this.structure(owner, table);
    });
  }

  reorder(owner: string, table: string, handles: string[]) {
    this.command(() => {
      const parent = this.row(owner), items = parent.children[table];
      if (!items || [...handles].sort().join() !== items.map(row => row.handle).sort().join())
        throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Reorder needs every row of the collection exactly once');
      parent.children[table] = handles.map(handle => this.rows.get(handle)!);
      this.current.order.add(nodeKey(owner, table));
      this.current.structure = true;
    });
  }

  moveRow(handle: string, owner: string, table: string, at: number) {
    this.command(() => {
      const row = this.row(handle), destination = this.row(owner);
      if ([...destination.path, table].join('/') !== row.path.join('/')) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Rows move only between collections of the same embedding');
      for (let ancestor: Row | null = destination; ancestor; ancestor = ancestor.parent)
        if (ancestor === row) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'A row cannot move into its own subtree');
      const source = row.parent!;
      source.children[table] = source.children[table].filter(item => item !== row);
      this.structure(source.handle, table);
      if (!Number.isInteger(at) || at < 0 || at > destination.children[table].length) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Use a valid destination position');
      this.place(destination, table, row, at);
      this.structure(owner, table);
      const stack = [row];
      while (stack.length) { const item = stack.pop()!; this.current.moved.add(item.handle); for (const items of Object.values(item.children)) stack.push(...items); }
    });
  }

  private structure(owner: string, table: string) {
    this.current.structure = true;
    this.current.membership.add(nodeKey(owner, '#' + table));
  }

  private drop(row: Row) {
    const stack = [row];
    while (stack.length) { const item = stack.pop()!; this.rows.delete(item.handle); for (const items of Object.values(item.children)) stack.push(...items); }
  }

  private capture() {
    return {rows: new Map([...this.rows].map(([handle, row]) => [handle, {row, values: {...row.values},
      children: Object.fromEntries(Object.entries(row.children).map(([table, items]) => [table, [...items]])), parent: row.parent}])),
      results: new Map(this.results), next: this.next};
  }

  private restore(snapshot: any) {
    for (const state of snapshot.rows.values()) { state.row.values = state.values; state.row.children = state.children; state.row.parent = state.parent; }
    this.rows = new Map([...snapshot.rows].map(([handle, state]: any) => [handle, state.row]));
    this.results = snapshot.results;
    this.next = snapshot.next;
    this.bind();
  }

  private commit(batch: any): unknown[] {
    const old = new Set(this.deps.keys());
    if (batch.structure) this.bind();
    const live = new Set(this.deps.keys());
    for (const node of old) if (!live.has(node)) { this.results.delete(node); this.counts.delete(node); }
    const fresh = new Set([...live].filter(node => !old.has(node) || batch.moved.has(nodeParts(node)[0])));
    const updated = this.settle(new Set([...batch.sources, ...batch.membership]), fresh);
    if (!updated.size && !batch.sources.size && !batch.structure) return [];
    this.revision++;
    if (batch.structure) this.structureRevision++;
    for (const node of [...this.observers.keys()]) if (!this.rows.has(nodeParts(node)[0])) this.observers.delete(node);
    for (const node of [...this.tableObservers.keys()]) if (!this.rows.has(nodeParts(node)[0])) this.tableObservers.delete(node);
    const failures: unknown[] = [];
    this.publishing = true;
    try {
      for (const [node, listeners] of [...this.observers]) {
        if (!updated.has(node) && !batch.sources.has(node)) continue;
        const [handle, field] = nodeParts(node), outcome = this.result(this.rows.get(handle)!, field);
        for (const listener of [...listeners]) { try { listener(outcome); } catch (exc) { failures.push(exc); } }
      }
      for (const [node, listeners] of [...this.tableObservers]) {
        const [handle, table] = nodeParts(node);
        if (!batch.membership.has(nodeKey(handle, '#' + table)) && !batch.order.has(node)) continue;
        const handles = this.rows.get(handle)!.children[table].map(row => row.handle);
        for (const listener of [...listeners]) { try { listener(handles); } catch (exc) { failures.push(exc); } }
      }
    } finally { this.publishing = false; }
    return failures;
  }

  observe(handle: string, field: string, listener: (result: Result) => void) {
    this.usable();
    this.row(handle);
    const node = nodeKey(handle, field);
    if (!this.observers.has(node)) this.observers.set(node, []);
    this.observers.get(node)!.push(listener);
    return () => { const items = this.observers.get(node); if (items?.includes(listener)) items.splice(items.indexOf(listener), 1); };
  }

  observeTable(handle: string, table: string, listener: (handles: string[]) => void) {
    this.usable();
    this.row(handle);
    const node = nodeKey(handle, table);
    if (!this.tableObservers.has(node)) this.tableObservers.set(node, []);
    this.tableObservers.get(node)!.push(listener);
    return () => { const items = this.tableObservers.get(node); if (items?.includes(listener)) items.splice(items.indexOf(listener), 1); };
  }

  subscriptions() {
    return [...this.observers.values(), ...this.tableObservers.values()].reduce((sum, items) => sum + items.length, 0);
  }

  explain(handle: string, field: string) {
    const row = this.row(handle), node = nodeKey(handle, field), computed = this.deps.has(node);
    return {role: computed ? 'computed' : 'source', target: row.label + field, owner: row.parent ? row.parent.label.replace(/\.$/, '') : null,
      inputs: [...(this.deps.get(node) || [])].map(item => this.address(item)).sort(),
      dependents: [...(this.dependents.get(node) || [])].map(item => this.address(item)).sort(),
      state: publicResult(this.result(row, field)), evaluations: this.counts.get(node) || 0,
      language: this.plan.language, profile: this.plan.numeric_profile, plan: this.plan.model_schema_hash, revision: this.revision};
  }

  find(label: string) {
    for (const [handle, row] of this.rows) if (row.label.replace(/\.$/, '') === label) return handle;
    throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown row ' + label);
  }

  snapshot(row: Row = this.root): any {
    const result: any = {_handle: row.handle, ...(row.id !== null ? {id: row.id} : {}), ...(row.parent ? {_key: row.key} : {}), ...row.values};
    const states: any = {};
    for (const [, field] of this.templates.get(row.path.join('/')) || []) {
      const outcome = this.results.get(nodeKey(row.handle, field.field));
      result[field.field] = outcome?.state === 'ready' ? wire(outcome.value, field.scale) : null;
      if (outcome && outcome.state !== 'ready') states[field.field] = publicResult(outcome);
    }
    if (Object.keys(states).length) result._states = states;
    if (Object.keys(row.children).length)
      result.children = Object.fromEntries(Object.entries(row.children).map(([table, items]) => [table, items.map(item => this.snapshot(item))]));
    return result;
  }

  sources(row: Row = this.root, identities = true): any {
    const result: any = {...row.values};
    if (identities) Object.assign(result, row.id !== null ? {id: row.id} : {}, row.parent ? {_key: row.key} : {});
    if (Object.keys(row.children).length)
      result.children = Object.fromEntries(Object.entries(row.children).map(([table, items]) => [table, items.map(item => this.sources(item, identities))]));
    return result;
  }

  dispose() {
    this.observers.clear(); this.tableObservers.clear(); this.rows.clear(); this.results.clear();
    this.deps = new Map(); this.dependents = new Map(); this.order = [];
    this.disposed = true;
  }
}

function compareChains(a: number[], b: number[]) {
  for (let index = 0; index < Math.min(a.length, b.length); index++) if (a[index] !== b[index]) return a[index] - b[index];
  return a.length - b.length;
}
