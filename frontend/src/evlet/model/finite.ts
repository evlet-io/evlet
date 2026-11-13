// Finite-v1 document evaluator. It mirrors evlet/calc.py over the same serialized plan.
// Pure: no imports, DOM, network or clock. The server remains the persistence authority.
export const LANGUAGE = 'finite-v1';
export const PROFILE = 'finite40-hu18-v1';
const MAX_DIGITS = 40, MAX_SCALE = 36, DIVISION_SCALE = 18, SAFE_INTEGER = 2n ** 53n - 1n, STORED_LIMIT = 2n ** 63n - 1n;
const MAX_HOPS = 3, MIN_DAY = 1, MAX_DAY = 3652059;  // 0001-01-01 and 9999-12-31 as proleptic Gregorian ordinals

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
const own = (object: any, key: string) => object !== null && typeof object === 'object' && Object.prototype.hasOwnProperty.call(object, key);
const ownValue = (object: any, key: string) => own(object, key) ? object[key] : undefined;
// JSON integers as both runtimes see them: safe whole numbers, never Booleans.
const integer = (value: unknown): number | null => typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
export function parse(value: unknown): Num {
  if (value instanceof Num) return value;
  if (typeof value === 'number') {
    if (integer(value) === null) throw new CalcError(Number.isInteger(value) ? 'COMPUTE_RANGE_ERROR' : 'COMPUTE_TYPE_ERROR', 'Use an exact decimal string or a safe integer');
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
// Nearest integer to numerator/denominator with exact ties to the even neighbour (HALF_EVEN).
function halfEven(numerator: bigint, denominator: bigint) {
  const negative = (numerator < 0n) !== (denominator < 0n);
  const n = numerator < 0n ? -numerator : numerator, d = denominator < 0n ? -denominator : denominator;
  let quotient = n / d;
  const twice = 2n * (n % d);
  if (twice > d || (twice === d && quotient % 2n === 1n)) quotient++;
  return negative ? -quotient : quotient;
}
export function quantize(value: Num, scale: number, rounding = 'half_up') {
  return value.scale <= scale ? value : normalize((rounding === 'half_even' ? halfEven : halfUp)(value.coef, 10n ** BigInt(value.scale - scale)), scale);
}
export function total(values: Num[]) {
  if (!values.length) return new Num(0n, 0);
  const scale = Math.max(...values.map(value => value.scale));
  return normalize(values.reduce((sum, value) => sum + value.coef * 10n ** BigInt(scale - value.scale), 0n), scale);
}

// Dates are ISO YYYY-MM-DD text; arithmetic uses proleptic Gregorian ordinals like Python's date.toordinal().
const DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const monthDays = (year: number, month: number) => month === 2 ? (year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28) : [4, 6, 9, 11].includes(month) ? 30 : 31;
export function dayNumber(value: unknown): number {
  if (typeof value === 'string' && DATE.test(value)) {
    const year = Number(value.slice(0, 4)), month = Number(value.slice(5, 7)), day = Number(value.slice(8, 10));
    if (year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= monthDays(year, month)) {
      const y = month <= 2 ? year - 1 : year, era = Math.floor(y / 400), yoe = y - era * 400;
      const doy = Math.floor((153 * (month + (month > 2 ? -3 : 9)) + 2) / 5) + day - 1;
      return era * 146097 + yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy - 305;
    }
  }
  throw new CalcError('COMPUTE_TYPE_ERROR', 'Use a date in YYYY-MM-DD format');
}
export function dayText(number: number): string {
  if (number < MIN_DAY || number > MAX_DAY) throw new CalcError('COMPUTE_RANGE_ERROR', 'The date is outside 0001-01-01 to 9999-12-31');
  const z = number + 305, era = Math.floor(z / 146097), doe = z - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100)), mp = Math.floor((5 * doy + 2) / 153);
  const day = doy - Math.floor((153 * mp + 2) / 5) + 1, month = mp < 10 ? mp + 3 : mp - 9, year = yoe + era * 400 + (month <= 2 ? 1 : 0);
  return String(year).padStart(4, '0') + '-' + String(month).padStart(2, '0') + '-' + String(day).padStart(2, '0');
}
function shift(day: string, count: Num, sign: number) {
  if (count.scale) throw new CalcError('COMPUTE_TYPE_ERROR', 'days() needs a whole number');
  if ((count.coef < 0n ? -count.coef : count.coef) > BigInt(MAX_DAY)) throw new CalcError('COMPUTE_RANGE_ERROR', 'The date is outside 0001-01-01 to 9999-12-31');
  return dayText(dayNumber(day) + sign * Number(count.coef));
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
  children: Record<string, Row[]>; id: RowId | null; key: string; label: string; order: number; created: boolean;
}

class Scope {
  constructor(readonly document: FiniteDocument, readonly row: Row, readonly member: Row | null = null, readonly at = '',
    readonly misses: Set<string> = new Set()) {}
  read(scope: string, field: string): Result {
    const target = scope === 'row' ? this.member : scope === 'parent' ? this.row.parent : this.row;
    return target ? this.document.result(target, field) : pending('PARENT_CONTEXT_REQUIRED', [this.at]);
  }
  members(table: string) { return (this.row.children[table] || []).map(member => new Scope(this.document, this.row, member, this.at, this.misses)); }
  address(field: string) { return (this.member || this.row).label + field; }
}

const OPERATIONS = new Set(['literal', 'ref', 'aggregate', 'add', 'sub', 'mul', 'div', 'neg', 'eq', 'ne', 'lt', 'lte', 'gt', 'gte',
  'and', 'or', 'not', 'round', 'coalesce', 'safe_divide', 'bool_to_decimal', 'when', 'today', 'hop', 'date_add', 'date_sub', 'date_diff']);

function guarded(operation: () => Value, scope: Scope): Result {
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
  if (op === 'today') return scope.document.todayResult();
  if (op === 'hop') return hop(node, scope);
  if (op === 'when') {
    const condition = evaluate(node.args[0], scope, budget);
    if (condition.state !== 'ready') return condition;
    if (condition.value === null) return pending('COMPUTE_INPUT_MISSING', [scope.at]);
    return evaluate(condition.value ? node.args[1] : node.args[2], scope, budget);
  }
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
    const [a, b] = values;
    const order = a instanceof Num ? compare(a, b) : a === b ? 0 : a < b ? -1 : 1;
    return ready({lt: order < 0, lte: order <= 0, gt: order > 0, gte: order >= 0}[op as 'lt']);
  }
  if (op === 'neg') { const value = values[0] as Num; return ready(new Num(-value.coef, value.scale)); }
  if (op === 'round') return guarded(() => quantize(values[0], values[1] as number), scope);
  if (op === 'date_add' || op === 'date_sub') return guarded(() => shift(values[0], values[1], op === 'date_add' ? 1 : -1), scope);
  if (op === 'date_diff') return guarded(() => normalize(BigInt(dayNumber(values[0]) - dayNumber(values[1])), 0), scope);
  const operation = {add, sub, mul, div}[op as 'add'];
  return guarded(() => operation(values[0], values[1]), scope);
}

// A Link value as its 13-character text id (as ids.ts writes it; inlined to keep this file pure).
// The server sends text ids; a whole number reads as a legacy id, so 42 is '000000000001a'.
type RowId = string | number;
const ID_ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz';
function linkKey(value: unknown): string | null {
  if (typeof value === 'string') {
    const text = value.toLowerCase().replace(/[il]/g, '1').replace(/o/g, '0');
    if (/^[0-7][0-9a-hjkmnp-tv-z]{12}$/.test(text)) return text;
    if (!/^[1-9][0-9]{0,11}$/.test(value)) return null;
  } else if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) return null;
  let number = BigInt(value), text = '';
  for (let index = 0; index < 13; index += 1) { text = ID_ALPHABET[Number(number % 32n)] + text; number /= 32n; }
  return text;
}

// Follow a Link path through host-supplied lookups. A null link is a ready null at that step.
function hop(node: any, scope: Scope): Result {
  let current = scope.read('self', node.field);
  for (const [model, name, kind] of node.steps) {
    if (current.state !== 'ready' || current.value === null) return current;
    const id = linkKey(current.value);
    if (id === null) return failed('COMPUTE_TYPE_ERROR', [scope.at]);
    const values = scope.document.lookups.get(model)?.get(id), label = '@' + model + '.' + id + '.' + name;
    if (!values || !own(values, name)) { scope.misses.add(model + '\u0000' + id); return pending('COMPUTE_INPUT_MISSING', [label]); }
    current = sourceValue({type: kind}, values[name], label);
  }
  return current;
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

// Target assignment: quantize to the field's scale (a Currency amount: its currency's `places`) with its
// rounding; the declared bounds hold, and the range check stays at the field's (storage) scale.
function convert(result: Result, field: any, at: string, meta?: any, places: number | null = null): Result {
  if (result.state !== 'ready' || result.value === null || field.result_type !== 'decimal') return result;
  let value = result.value as Num;
  const scale = field.scale, target = places === null ? scale : places;
  if (target !== null && value.scale > target) { value = quantize(value, target, field.rounding || 'half_up'); result = ready(value); }
  const magnitude = value.coef < 0n ? -value.coef : value.coef;
  if (field.target_type === 'integer' && magnitude > SAFE_INTEGER) return failed('COMPUTE_RANGE_ERROR', [at]);
  if (scale !== null && magnitude * 10n ** BigInt(scale - value.scale) > STORED_LIMIT) return failed('COMPUTE_RANGE_ERROR', [at]);
  if (meta && ((meta.minimum != null && compare(value, parse(meta.minimum)) < 0) || (meta.maximum != null && compare(value, parse(meta.maximum)) > 0)))
    return failed('COMPUTE_RANGE_ERROR', [at]);
  return result;
}

// Whole seconds from an ISO 8601 duration (PT1H30M, P2DT4H) or a whole number of seconds.
const DURATION = /^P(?:([0-9]{1,9})D)?(?:T(?:([0-9]{1,9})H)?(?:([0-9]{1,9})M)?(?:([0-9]{1,9})S)?)?$/;
function duration(raw: unknown): Num {
  if (typeof raw === 'string' && raw.startsWith('P')) {
    const match = DURATION.exec(raw);
    if (!match || !match.slice(1).some(part => part !== undefined) || raw.endsWith('T'))
      throw new CalcError('COMPUTE_TYPE_ERROR', 'Use an ISO 8601 duration such as PT1H30M');
    const [days, hours, minutes, seconds] = match.slice(1).map(part => BigInt(part ?? 0));
    return normalize(((days * 24n + hours) * 60n + minutes) * 60n + seconds, 0);
  }
  const value = parse(raw);
  if (value.scale) throw new CalcError('COMPUTE_TYPE_ERROR', 'A duration is a whole number of seconds');
  return value;
}

function sourceValue(meta: any, raw: any, at: string): Result {
  if (raw && typeof raw === 'object' && raw.$state === 'pending') return pending('COMPUTE_INPUT_MISSING', [at]);
  if (raw === undefined || raw === null || (raw === '' && meta.type !== 'text')) return ready(null);
  try {
    if (meta.type === 'duration' || meta.kind === 'duration') return ready(duration(raw));
    if (meta.type === 'decimal') return ready(parse(raw));
    if (meta.type === 'date') return ready(dayText(dayNumber(raw)));
    if (meta.type === 'boolean' && typeof raw !== 'boolean') throw new CalcError('COMPUTE_TYPE_ERROR', 'Expected a Boolean');
    return ready(raw);
  } catch (exc) {
    if (exc instanceof CalcError) return failed(exc.code, [at]);
    throw exc;
  }
}

// The places a plan entry's value travels with: a Currency amount keeps at least 2 (as Currency.serialize).
export const wireScale = (field: any) => field.target_type === 'currency' ? 2 : field.scale;

/**
 * Host input for Currency targets: the decimal places of each code that does not have 2 (/api/schema
 * currency_decimals) and the code each app setting holds ('app.key' -> 'KWD'). A code not listed, or none, has 2.
 */
export interface CurrencyInput { decimals?: Record<string, number>; settings?: Record<string, string> }

const address = (path: string[], field: string) => path.map(name => name + '[*].').join('') + field;
// Plan entries: a calculation (no kind) or a field's initial and rules (applies_when, required_when, locked_when).
const KINDS = ['initial', 'applies_when', 'required_when', 'locked_when'], RULES = ['applies_when', 'required_when', 'locked_when'];
const nodeName = (entry: any) => entry.kind ? entry.field + ':' + entry.kind : entry.field;
export type RuleState = {applies: boolean; required: boolean; locked: boolean};
// Whether setting a source leaves it unchanged; objects and arrays always count as a change.
const sameValue = (values: any, field: string, value: unknown) => own(values, field) && (value === null || typeof value !== 'object') && values[field] === value;
const KEY = /^[A-Za-z0-9_-]{1,64}$/;
type Node = string;
const nodeKey = (handle: string, field: string): Node => handle + '\u0000' + field;
const nodeParts = (node: Node) => node.split('\u0000') as [string, string];
// Whether any input changed; `documentOnly` leaves out host inputs (@today, @lookups).
function anyChanged(inputs: Set<Node>, changed: Set<Node>, documentOnly: boolean) {
  for (const input of inputs) if (changed.has(input) && !(documentOnly && input.startsWith('@'))) return true;
  return false;
}

// collection_rows is the most any owned table may declare (Table max_rows); each plan table carries its own.
export const LIMITS = {expression_nodes: 128, expression_depth: 32, table_depth: 8, collection_rows: 1000, document_rows: 5000,
  graph_nodes: 50000, graph_edges: 250000, evaluation_steps: 1000000, decimal_text: 128};

export function checkPlan(plan: any) {
  if (!plan || plan.plan_version !== 1 || plan.language !== LANGUAGE || plan.numeric_profile !== PROFILE || plan.expression_version !== 1)
    throw new CalcError('SCHEMA_VERSION_MISMATCH', 'Unsupported calculation plan, language or profile');
  // The plan and its hash come from the server in one response; a save echoes the hash and the server refuses
  // a mismatch, so the browser checks the plan's shape but does not hash it again.
  if (typeof plan.model_schema_hash !== 'string' || !sameLimits(plan.limits))
    throw new CalcError('SCHEMA_VERSION_MISMATCH', 'The calculation plan uses different engine limits');
  for (const field of plan.fields) {
    if (field.scale !== null && (integer(field.scale) === null || field.scale < 0 || field.scale > MAX_SCALE))
      throw new CalcError('UNSUPPORTED_EXPRESSION', 'Invalid target scale');
    if (![undefined, null, 'half_up', 'half_even'].includes(field.rounding)) throw new CalcError('UNSUPPORTED_EXPRESSION', 'Invalid target rounding');
    if (own(field, 'kind') && !KINDS.includes(field.kind)) throw new CalcError('UNSUPPORTED_EXPRESSION', 'Invalid plan entry kind');
    checkNode(field.expression, 0, {count: 0});
    if (own(field, 'currency') && field.currency !== null && field.currency !== undefined) checkMoney(field);
  }
  for (const table of Object.values<any>(plan.tables)) {
    for (const key of ['min_rows', 'max_rows'])
      if (own(table, key) && (!Number.isInteger(table[key]) || table[key] < 0 || table[key] > LIMITS.collection_rows))
        throw new CalcError('SCHEMA_VERSION_MISMATCH', 'Invalid table row limits');
    if ((own(table, 'min_rows') ? table.min_rows : 0) > (own(table, 'max_rows') ? table.max_rows : LIMITS.collection_rows))
      throw new CalcError('SCHEMA_VERSION_MISMATCH', 'Invalid table row limits');
    for (const meta of Object.values<any>(table.fields))
      for (const bound of [meta.minimum, meta.maximum]) if (bound !== null && bound !== undefined) parse(bound);
  }
}

function sameLimits(limits: any) {
  if (!limits || typeof limits !== 'object' || Object.keys(limits).length !== Object.keys(LIMITS).length) return false;
  return Object.entries(LIMITS).every(([name, value]) => ownValue(limits, name) === value);
}

// Where a Currency target finds the code it rounds to: {code}, {setting} (a host input), or {expression, reads}
// reading a field of the row or of a linked record; those reads are also the entry's reads.
function checkMoney(field: any) {
  const money = field.currency, keys = money && typeof money === 'object' && !Array.isArray(money) ? Object.keys(money) : [];
  let ok = field.target_type === 'currency' && (keys.length === 1 || keys.length === 2);
  if (ok && own(money, 'code')) ok = keys.length === 1 && typeof money.code === 'string' && /^[A-Z]{3}$/.test(money.code);
  else if (ok && own(money, 'setting')) ok = keys.length === 1 && typeof money.setting === 'string' && /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(money.setting);
  else if (ok) {
    const reads = new Set((field.reads || []).map((read: any) => JSON.stringify(read)));
    ok = keys.length === 2 && own(money, 'expression') && own(money, 'reads') && !!money.expression && typeof money.expression === 'object'
      && !Array.isArray(money.expression) && ['ref', 'hop'].includes(ownValue(money.expression, '$expr')) && Array.isArray(money.reads)
      && money.reads.every((read: any) => reads.has(JSON.stringify(read)));
    if (ok) checkNode(money.expression, 0, {count: 0});
  }
  if (!ok) throw new CalcError('UNSUPPORTED_EXPRESSION', 'Invalid currency source');
}

const SCOPES = new Set(['self', 'parent', 'row']);
const LITERALS: Record<string, string> = {decimal: 'string', boolean: 'boolean', text: 'string'};
const ARITY: Record<string, number> = {add: 2, sub: 2, mul: 2, div: 2, neg: 1, eq: 2, ne: 2, lt: 2, lte: 2, gt: 2, gte: 2,
  and: 2, or: 2, not: 1, round: 2, coalesce: 2, safe_divide: 3, bool_to_decimal: 1, when: 3, date_add: 2, date_sub: 2, date_diff: 2};
const FIELD = /^[A-Za-z][A-Za-z0-9_]*$/, MODEL = /^[a-z0-9_]{1,64}$/, HOP_TYPES = ['decimal', 'boolean', 'date', 'text', 'link', 'duration'];
// One to three [model, field, type] lookups; every step but the last follows a Link.
const hopSteps = (steps: any) => Array.isArray(steps) && steps.length >= 1 && steps.length <= MAX_HOPS && steps.every((step: any, index: number) =>
  Array.isArray(step) && step.length === 3 && typeof step[0] === 'string' && MODEL.test(step[0]) && typeof step[1] === 'string' && FIELD.test(step[1])
  && HOP_TYPES.includes(step[2]) && (step[2] === 'link' || index === steps.length - 1));
// Closed, bounded IR: unknown operations, arities, scopes or shapes fail before evaluation.
// An integer literal is only valid as round()'s scale, and nothing else is valid there.
function checkNode(node: any, depth: number, counter: {count: number}, scale = false) {
  counter.count++;
  if (!node || typeof node !== 'object' || Array.isArray(node) || depth > LIMITS.expression_depth || counter.count > LIMITS.expression_nodes)
    throw new CalcError('UNSUPPORTED_EXPRESSION', 'Calculation plans must be bounded expression objects');
  const op = ownValue(node, '$expr');
  let ok: boolean;
  if (op === 'literal') {
    const kind = ownValue(node, 'type'), value = ownValue(node, 'value');
    if (kind === 'integer') ok = scale && integer(value) !== null && value >= 0 && value <= MAX_SCALE;
    else {
      ok = !scale && (kind === 'null' ? value === null : own(LITERALS, kind) && typeof value === LITERALS[kind]);
      if (ok && kind === 'decimal') parse(value);
    }
  } else if (op === 'ref') ok = !scale && SCOPES.has(node.scope) && typeof node.field === 'string' && FIELD.test(node.field);
  else if (op === 'today') ok = !scale;
  else if (op === 'hop') ok = !scale && typeof node.field === 'string' && FIELD.test(node.field) && hopSteps(node.steps);
  else if (op === 'aggregate') {
    const field = own(node, 'field') ? node.field : 0;
    ok = !scale && ['sum', 'min', 'max', 'count'].includes(node.kind) && typeof node.table === 'string' && own(node, 'where')
      && (node.kind === 'count' ? field === null : typeof field === 'string' && FIELD.test(field));
    if (ok && node.where !== null) checkNode(node.where, depth + 1, counter);
  } else {
    ok = !scale && typeof op === 'string' && own(ARITY, op) && Array.isArray(node.args) && node.args.length === ARITY[op];
    if (ok) node.args.forEach((arg: any, index: number) => checkNode(arg, depth + 1, counter, op === 'round' && index === 1));
  }
  if (!ok) throw new CalcError('UNSUPPORTED_EXPRESSION', 'Unknown or malformed calculation node');
}

// A batch callback must finish synchronously; an async one would change the document after the batch closed.
function runSync(run: (commands: FiniteDocument) => unknown, document: FiniteDocument) {
  const value: any = run(document);
  if (value && typeof value.then === 'function')
    throw new CalcError('ASYNC_BATCH_UNSUPPORTED', 'A calculation batch cannot await; apply network results as a later batch');
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
  // this.order with each node's row, plan field, inputs, target and target meta, built by bind().
  private steps: Array<{node: Node; handle: string; name: string; row: Row; field: any; inputs: Set<Node>; target: Node; meta: any}> = [];
  observers = new Map<Node, Array<(result: Result) => void>>();
  ruleObservers = new Map<string, Array<(states: Record<string, RuleState>) => void>>();
  // Initial suggestions waiting to be written, and fields the host set in the current write (touched).
  armed = new Set<Node>();
  touched = new Set<Node>();
  // Lock results of the document as loaded (handle -> field -> true, false or null when undecided).
  // Saved rows keep them for the life of the session; host inputs such as lookups refine them until the first edit.
  locks = new Map<string, Record<string, boolean | null>>();
  private pristine = true;
  revision = 0; structureRevision = 0; trace: any = {};
  // Host inputs: today's date and Link lookups (model -> id -> field values); misses are lookups still wanted.
  today: unknown = null;
  lookups = new Map<string, Map<string, Record<string, unknown>>>();
  misses = new Map<Node, Set<string>>();
  readonly lookupFields = new Map<string, Set<string>>();
  root: Row;
  private next = 0;
  private current: any = null;
  private publishing = false;
  private disposed = false;
  // Set when a batch failed to settle; the session then refuses every command.
  broken = false;

  // Host input for Currency targets (CurrencyInput); null gives every code 2 places.
  currencies: CurrencyInput | null;

  constructor(readonly plan: any, data: any = {}, private budget: number = plan?.limits?.evaluation_steps, currencies: CurrencyInput | null = null) {
    checkPlan(plan);
    this.currencies = currencies;
    for (const entry of Object.values<any>(plan.tables)) this.schema.set(entry.path.join('/'), entry);
    plan.fields.forEach((field: any, position: number) => {
      const path = field.path.join('/');
      if (!this.templates.has(path)) this.templates.set(path, []);
      this.templates.get(path)!.push([position, field]);
      this.fields.set(path + '\u0000' + nodeName(field), field);
      for (const [scope, model, name] of field.reads) if (scope === 'lookup') {
        if (!this.lookupFields.has(model)) this.lookupFields.set(model, new Set());
        this.lookupFields.get(model)!.add(name);
      }
    });
    this.root = this.build(data || {}, [], null, null);
    this.bind();
    const created = new Set([...this.deps.keys()].filter(node => this.rows.get(nodeParts(node)[0])!.created && node.endsWith(':initial')));
    this.settle(new Set(), new Set(this.deps.keys()), this.touched, created);
    this.touched = new Set();
    this.takeLocks();
    this.revision = 1;
  }

  private handle() { return 'h' + ++this.next; }

  private build(data: any, path: string[], owner: Row | null, created: boolean | null): Row {
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Rows must be objects');
    const entry = this.schema.get(path.join('/'));
    if (!entry) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown collection ' + address(path, ''));
    // A row id keeps the form it came in (text from the server, numbers in older fixtures).
    const rawId = ownValue(data, 'id') ?? null, id: RowId | null = rawId === null || linkKey(rawId) === null ? null : rawId;
    if (rawId !== null && id === null) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row IDs must be record ids');
    const handle = this.handle();
    const key = ownValue(data, '_key') ?? null;
    if (key !== null && (typeof key !== 'string' || !KEY.test(key)))
      throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row keys use at most 64 letters, digits, hyphens or underscores');
    const fresh = created === null ? id === null : created;
    const row: Row = {handle, path, values: Object.create(null), parent: owner, children: Object.create(null), id, key: key || (id ? String(id) : handle), label: '', order: 0, created: fresh};
    this.rows.set(handle, row);
    if (this.rows.size - 1 > this.plan.limits.document_rows) throw new CalcError('CALCULATION_LIMIT_EXCEEDED', 'The document has too many rows');
    for (const [name, value] of Object.entries(data)) {
      if (name === 'id' || name === '_key' || name === 'children' || name === '_handle') continue;
      const meta = ownValue(entry.fields, name);
      if (!meta) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown field ' + name);
      if (meta.computed && created) throw new CalcError('COMPUTED_FIELD_WRITE', name + ' is calculated');
      if (!meta.computed) {
        row.values[name] = value;
        // A value supplied with a new row is the host's choice: its initial does not replace it.
        if (fresh) (this.current ? this.current.touched : this.touched).add(nodeKey(handle, name));
      }
    }
    if (fresh) this.defaults(row, entry);
    const children = ownValue(data, 'children') || {};
    if (typeof children !== 'object' || Array.isArray(children) || Object.keys(children).some(name => !entry.tables.includes(name)))
      throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown child collection');
    for (const table of entry.tables) {
      const items = own(children, table) ? children[table] : [];
      if (!Array.isArray(items)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', table + ' must be a list of rows');
      if (items.length > this.maxRows(path, table))
        throw new CalcError('CALCULATION_LIMIT_EXCEEDED', table + ' exceeds the collection row limit');
      row.children[table] = [];
      const seen = {keys: new Set<string>(), ids: new Set<RowId | null>()};
      for (const item of items) this.place(row, table, this.build(item, [...path, table], row, created), row.children[table].length, seen);
    }
    return row;
  }

  private defaults(row: Row, entry: any) {
    for (const [name, meta] of Object.entries<any>(entry.fields)) {
      const fallback = meta.default;
      if (own(row.values, name) || !fallback || fallback.server) continue;
      if ('ref' in fallback) {
        const parts = fallback.ref.split('.');
        if (parts[0] !== 'parent' || parts.length !== 2 || !row.parent) continue;
        const source = this.rows.has(row.parent.handle) ? this.result(row.parent, parts[1]) : ready(null);
        row.values[name] = source.state === 'ready' ? wire(source.value) : {$state: 'pending'};
      } else row.values[name] = fallback.value;
    }
  }

  /** The table's declared max_rows (plans without one use the engine limit). */
  private maxRows(path: string[], table: string): number {
    const entry = this.schema.get([...path, table].join('/'));
    const declared = entry && own(entry, 'max_rows') ? entry.max_rows : this.plan.limits.collection_rows;
    return Math.min(declared, this.plan.limits.collection_rows);
  }

  /** Insert a row; `seen` (keys and ids of the rows already in the list) makes bulk loads linear. */
  private place(owner: Row, table: string, row: Row, at: number, seen?: {keys: Set<string>, ids: Set<RowId | null>}) {
    const items = owner.children[table];
    if (seen ? seen.keys.has(row.key) : items.some(item => item !== row && item.key === row.key))
      throw new CalcError('DOCUMENT_STRUCTURE_ERROR', `Repeated row key ${row.key} in ${table}`);
    if (row.id !== null && (seen ? seen.ids.has(row.id) : items.some(item => item !== row && item.id === row.id)))
      throw new CalcError('DOCUMENT_STRUCTURE_ERROR', `Repeated row ID ${row.id} in ${table}`);
    if (items.length >= this.maxRows(owner.path, table)) throw new CalcError('CALCULATION_LIMIT_EXCEEDED', table + ' exceeds the collection row limit');
    items.splice(at, 0, row);
    row.parent = owner;
    if (seen) { seen.keys.add(row.key); seen.ids.add(row.id); }
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
        // applies_when also runs when its own field changes, so a value set while it does not apply is cleared.
        const node = nodeKey(handle, nodeName(field)), inputs = new Set<Node>(field.kind === 'applies_when' ? [nodeKey(handle, field.field)] : []);
        for (const [scope, table, name] of field.reads) {
          if (scope === 'self') inputs.add(nodeKey(handle, name));
          else if (scope === 'parent' && row.parent) inputs.add(nodeKey(row.parent.handle, name));
          else if (scope === 'membership') inputs.add(nodeKey(handle, '#' + table));
          else if (scope === 'member') for (const member of row.children[table] || []) inputs.add(nodeKey(member.handle, name));
          else if (scope === 'today') inputs.add(nodeKey('@', 'today'));
          else if (scope === 'lookup') inputs.add(nodeKey('@' + table, name));
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
    this.steps = this.order.map(node => {
      const [handle, name] = nodeParts(node), row = this.rows.get(handle)!, path = row.path.join('/'), field = this.fields.get(path + '\u0000' + name);
      return {node, handle, name, row, field, inputs: deps.get(node)!, target: nodeKey(handle, field.field), meta: ownValue(this.schema.get(path).fields, field.field)};
    });
    this.dependents = new Map();
    for (const [node, inputs] of deps) for (const input of inputs) {
      if (!this.dependents.has(input)) this.dependents.set(input, new Set());
      this.dependents.get(input)!.add(node);
    }
  }

  result(row: Row, field: string): Result {
    const node = nodeKey(row.handle, field);
    if (this.deps.has(node)) return this.results.get(node) || pending('COMPUTE_INPUT_MISSING', [row.label + field]);
    const meta = ownValue(this.schema.get(row.path.join('/')).fields, field);
    return meta ? sourceValue(meta, ownValue(row.values, field), row.label + field) : failed('UNSUPPORTED_EXPRESSION', [row.label + field]);
  }

  // The decimal places of a Currency target's currency on this row, or a pending Result while a linked
  // record's currency is still being looked up. An empty or unknown currency has 2.
  moneyPlaces(money: any, scope: Scope, budget: Budget): number | Result {
    let code: unknown = null;
    if (!money || !Object.keys(money).length) code = null;
    else if (own(money, 'code')) code = money.code;
    else if (own(money, 'setting')) code = ownValue(this.currencies?.settings ?? {}, money.setting);
    else {
      const found = evaluate(money.expression, scope, budget);
      if (found.state === 'pending') return found;
      code = found.state === 'ready' ? found.value : null;
    }
    const places = typeof code === 'string' ? ownValue(this.currencies?.decimals ?? {}, code) : undefined;
    return typeof places === 'number' && Number.isInteger(places) ? places : 2;
  }

  todayResult(): Result {
    return this.today === null || this.today === undefined ? pending('COMPUTE_INPUT_MISSING', ['@today']) : sourceValue({type: 'date'}, this.today, '@today');
  }

  // An initial is armed when its row is new or a document input it reads changed (host inputs such as
  // lookups and today() do not arm it). A field touched in this write disarms it. An armed initial writes
  // its first ready result unless the field is locked. applies_when clears its field.
  private settle(changed: Set<Node>, fresh: Set<Node>, touched: Set<Node> = new Set(), arm: Set<Node> = new Set()) {
    const budget = new Budget(this.budget), updated = new Set<Node>();
    let evaluated = 0, skipped = 0;
    for (const {node, handle, name, row, field, inputs, target, meta} of this.steps) {
      if (field.kind === 'initial') {
        if (arm.has(node) || anyChanged(inputs, changed, true)) this.armed.add(node);
        if (touched.has(target)) this.armed.delete(node);
      }
      if (!fresh.has(node) && !anyChanged(inputs, changed, false)) { skipped++; continue; }
      const at = row.label + name, scope = new Scope(this, row, null, at);
      let outcome = evaluate(field.expression, scope, budget);
      let places: number | null = null;
      if (field.target_type === 'currency' && (!field.kind || field.kind === 'initial') && outcome.state === 'ready' && outcome.value !== null) {
        // A Currency target without a currency source has 2 places, as before amounts were stored at 6.
        const found = this.moneyPlaces(field.currency || {}, scope, budget);
        if (typeof found === 'number') places = found; else outcome = found;
      }
      if (!field.kind) outcome = convert(outcome, field, at, meta, places);
      else if (field.kind === 'initial') outcome = convert(outcome, field, at, undefined, places);
      if (scope.misses.size) this.misses.set(node, scope.misses); else this.misses.delete(node);
      evaluated++;
      this.counts.set(node, (this.counts.get(node) || 0) + 1);
      if (!same(outcome, this.results.get(node))) { this.results.set(node, outcome); changed.add(node); updated.add(node); }
      let value: unknown = undefined, write = false;
      if (field.kind === 'initial' && this.armed.has(node) && outcome.state !== 'pending') {
        this.armed.delete(node);
        if (outcome.state === 'ready' && !this.locked(row, field.field)) { value = wire(outcome.value, wireScale(field)); write = true; }
      } else if (field.kind === 'applies_when' && outcome.state === 'ready' && outcome.value === false && (ownValue(row.values, field.field) ?? null) !== null
        // A field locked on the saved row keeps its value; the server refuses the write instead.
        && (ownValue(this.locks.get(handle) || {}, field.field) ?? false) === false) {
        value = null; write = true;
      }
      if (write && (!own(row.values, field.field) || row.values[field.field] !== value)) {
        row.values[field.field] = value;
        changed.add(target);
        updated.add(target);
      }
    }
    this.trace = {evaluated, skipped, steps: budget.steps, changed: [...updated].map(node => this.address(node)).sort()};
    return updated;
  }

  private address(node: Node) {
    const [handle, field] = nodeParts(node);
    return (this.rows.get(handle)?.label ?? handle + '.') + field;
  }

  // Run commands as one batch; the document settles and publishes once at the end.
  // Every command checks its input before it changes anything, so a refused command leaves the document as it was.
  // If `run` throws after some commands ran, those commands are kept, settled and published, and the error is
  // thrown again. If settling itself fails (an engine limit), the session is broken and the host builds a new one.
  batch(run: (commands: FiniteDocument) => unknown) {
    this.usable();
    // A nested batch is part of the batch around it.
    if (this.current !== null) { runSync(run, this); return; }
    this.current = {sources: new Set<Node>(), membership: new Set<Node>(), structure: false, order: new Set<Node>(), touched: new Set<Node>()};
    let failed = false, failure: unknown;
    try { runSync(run, this); } catch (exc) { failed = true; failure = exc; }
    const batch = this.current;
    this.current = null;
    let notify: Array<() => void>;
    try { notify = this.commit(batch); }
    catch (exc) { this.broken = true; throw exc; }
    // The batch is committed; observer failures are reported after publication.
    const failures: unknown[] = [];
    this.publishing = true;
    try { for (const deliver of notify) { try { deliver(); } catch (exc) { failures.push(exc); } } }
    finally { this.publishing = false; }
    if (failed) throw failure;
    if (failures.length) throw failures[0];
  }

  // Run one command, inside the current batch or as its own batch.
  private checked(run: () => void) {
    if (this.current === null) { this.batch(run); return; }
    this.usable();
    run();
  }

  // Forget rows built after handle number `last` by a command that was then refused.
  private forgetRowsAfter(last: number) {
    for (let number = last + 1; number <= this.next; number++) this.rows.delete('h' + number);
  }

  private usable() {
    if (this.disposed) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'This document session was disposed');
    if (this.broken) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'This document session failed to settle; build a new one');
    if (this.publishing) throw new CalcError('REACTIVE_REENTRANCY', 'Observers cannot change the document during publication');
  }

  private row(handle: string) {
    const row = this.rows.get(handle);
    if (!row) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown row ' + handle);
    return row;
  }

  set(handle: string, field: string, value: unknown) {
    this.checked(() => {
      const row = this.row(handle), meta = ownValue(this.schema.get(row.path.join('/')).fields, field);
      if (!meta) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown field ' + field);
      if (meta.computed) throw new CalcError('COMPUTED_FIELD_WRITE', row.label + field + ' is calculated');
      this.current.touched.add(nodeKey(handle, field));
      if (!sameValue(row.values, field, value)) { row.values[field] = value; this.current.sources.add(nodeKey(handle, field)); }
    });
  }

  // The host's current date (YYYY-MM-DD) for today().
  setToday(value: unknown) {
    this.checked(() => { this.today = value; this.current.sources.add(nodeKey('@', 'today')); });
  }

  // Field values of one linked record, as the host read them, for Link path expressions.
  setLookups(model: string, id: string | number, values: Record<string, unknown>) {
    this.checked(() => {
      const fields = this.lookupFields.get(model), key = linkKey(id);
      if (!fields || key === null || !values || typeof values !== 'object' || Array.isArray(values) || Object.keys(values).some(name => !fields.has(name)))
        throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'This plan does not look up ' + model);
      if (!this.lookups.has(model)) this.lookups.set(model, new Map());
      const records = this.lookups.get(model)!;
      records.set(key, Object.assign(Object.create(null), records.get(key), values));
      for (const name of Object.keys(values)) this.current.sources.add(nodeKey('@' + model, name));
    });
  }

  // [model, id, fields] the current results still wait for, sorted; the host reads and supplies them.
  lookupRequests(): Array<[string, string, string[]]> {
    const wanted = new Map<string, Set<string>>();
    for (const [node, misses] of this.misses) {
      if (!this.deps.has(node)) continue;
      for (const miss of misses) {
        const [model, id] = miss.split('\u0000');
        if (!wanted.has(model)) wanted.set(model, new Set());
        wanted.get(model)!.add(id);
      }
    }
    return [...wanted.keys()].sort().flatMap(model => [...wanted.get(model)!].sort()
      .map(id => [model, id, [...this.lookupFields.get(model)!].sort()] as [string, string, string[]]));
  }

  addRow(owner: string, table: string, values: any = {}) {
    let handle = '';
    this.checked(() => {
      const parent = this.row(owner);
      if (!this.schema.get(parent.path.join('/')).tables.includes(table)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown collection ' + table);
      if (values?.id != null) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'New rows cannot carry persisted IDs');
      // Build the row first; if it or its place is refused, the half-built rows are forgotten.
      const last = this.next;
      let row: Row;
      try {
        row = this.build({...values}, [...parent.path, table], parent, true);
        this.place(parent, table, row, parent.children[table].length);
      } catch (exc) { this.forgetRowsAfter(last); throw exc; }
      this.structure(owner, table);
      handle = row.handle;
    });
    return handle;
  }

  removeRow(handle: string) {
    this.checked(() => {
      const row = this.row(handle);
      if (!row.parent) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'The root cannot be removed');
      const table = row.path[row.path.length - 1];
      row.parent.children[table] = row.parent.children[table].filter(item => item !== row);
      this.structure(row.parent.handle, table);
      this.drop(row);
    });
  }

  reorder(owner: string, table: string, handles: string[]) {
    this.checked(() => {
      const parent = this.row(owner), items = parent.children[table];
      const wanted = Array.isArray(handles) ? handles.map(String).sort() : null, current = (items || []).map(row => row.handle).sort();
      if (!items || !wanted || wanted.length !== current.length || wanted.some((handle, index) => handle !== current[index]))
        throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Reorder needs every row of the collection exactly once');
      parent.children[table] = handles.map(handle => this.rows.get(handle)!);
      this.current.order.add(nodeKey(owner, table));
      this.current.structure = true;
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

  private commit(batch: any): Array<() => void> {
    // Only a change of structure rebinds; the bindings as the batch began are then the old ones.
    const old = this.deps;
    if (batch.structure) this.bind();
    const live = this.deps, fresh = new Set<Node>();
    if (live !== old) {
      for (const node of old.keys()) if (!live.has(node)) { this.results.delete(node); this.counts.delete(node); this.misses.delete(node); }
      for (const node of live.keys()) if (!old.has(node)) fresh.add(node);
    }
    const updated = this.settle(new Set([...batch.sources, ...batch.membership]), fresh, batch.touched,
      new Set([...fresh].filter(node => node.endsWith(':initial'))));
    if (!updated.size && !batch.sources.size && !batch.structure) return [];
    if (batch.structure || [...batch.sources].some((key: string) => !key.startsWith('@'))) this.pristine = false;
    else if (this.pristine) this.takeLocks();
    this.revision++;
    if (batch.structure) this.structureRevision++;
    // Only a change of structure removes rows, and with them their observers.
    if (batch.structure) {
      for (const node of [...this.observers.keys()]) if (!this.rows.has(nodeParts(node)[0])) this.observers.delete(node);
      for (const handle of [...this.ruleObservers.keys()]) if (!this.rows.has(handle)) this.ruleObservers.delete(handle);
    }
    const notify: Array<() => void> = [];
    for (const [node, listeners] of [...this.observers]) {
      if (!updated.has(node) && !batch.sources.has(node)) continue;
      const [handle, field] = nodeParts(node), outcome = this.result(this.rows.get(handle)!, field);
      for (const listener of [...listeners]) notify.push(() => listener(outcome));
    }
    const ruled = new Set([...updated].filter(node => RULES.includes(nodeParts(node)[1].split(':')[1])).map(node => nodeParts(node)[0]));
    for (const [handle, listeners] of [...this.ruleObservers]) {
      if (!ruled.has(handle)) continue;
      const states = this.ruleStates(handle);
      for (const listener of [...listeners]) notify.push(() => listener(states));
    }
    return notify;
  }

  observe(handle: string, field: string, listener: (result: Result) => void) {
    this.usable();
    if (!own(this.schema.get(this.row(handle).path.join('/')).fields, field)) throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown field ' + field);
    const node = nodeKey(handle, field);
    if (!this.observers.has(node)) this.observers.set(node, []);
    this.observers.get(node)!.push(listener);
    return () => { const items = this.observers.get(node); if (items?.includes(listener)) items.splice(items.indexOf(listener), 1); };
  }

  // Call listener(ruleStates(handle)) after each batch that changes one of this row's rule results.
  observeRules(handle: string, listener: (states: Record<string, RuleState>) => void) {
    this.usable();
    this.row(handle);
    if (!this.ruleObservers.has(handle)) this.ruleObservers.set(handle, []);
    this.ruleObservers.get(handle)!.push(listener);
    return () => { const items = this.ruleObservers.get(handle); if (items?.includes(listener)) items.splice(items.indexOf(listener), 1); };
  }

  // A rule's Boolean result, or `undecided` when it has no ready true/false value; null when the field has no such rule.
  decided(row: Row, field: string, kind: string, undecided: boolean | null): boolean | null {
    const node = nodeKey(row.handle, field + ':' + kind);
    if (!this.deps.has(node)) return null;
    const result = this.results.get(node);
    return result && result.state === 'ready' && result.value !== null ? result.value as boolean : undecided;
  }

  // Record lock results as the saved state of rows loaded with an ID.
  private takeLocks() {
    this.locks = new Map();
    for (const [handle, row] of this.rows) {
      if (row.created) continue;
      const locks: Record<string, boolean | null> = {};
      for (const [, entry] of this.templates.get(row.path.join('/')) || []) if (entry.kind === 'locked_when') locks[entry.field] = this.decided(row, entry.field, 'locked_when', null);
      this.locks.set(handle, locks);
    }
  }

  // A field's lock as the server enforces it: decided on the row as saved. A new record or row has no saved
  // state, so nothing is locked on it and its fields may be filled in any order.
  locked(row: Row, field: string): boolean {
    const saved = this.locks.get(row.handle) || {};
    return own(saved, field) && saved[field] !== false;
  }

  // {field: {applies, required, locked}} for fields of this row that declare rules.
  // Undecided rules (pending, error or null) fail closed: the field applies, is required and is locked.
  ruleStates(handle: string): Record<string, RuleState> {
    const row = this.row(handle), states: Record<string, RuleState> = {};
    for (const [, entry] of this.templates.get(row.path.join('/')) || []) {
      if (!RULES.includes(entry.kind) || own(states, entry.field)) continue;
      const name = entry.field, applies = this.decided(row, name, 'applies_when', true) !== false;
      const required = Boolean(this.schema.get(row.path.join('/')).fields[name].required || this.decided(row, name, 'required_when', true));
      states[name] = {applies, required: applies && required, locked: this.locked(row, name)};
    }
    return Object.fromEntries(Object.keys(states).sort().map(name => [name, states[name]]));
  }

  find(label: string) {
    for (const [handle, row] of this.rows) if (row.label.replace(/\.$/, '') === label) return handle;
    throw new CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown row ' + label);
  }

  snapshot(row: Row = this.root): any {
    const result: any = {_handle: row.handle, ...(row.id !== null ? {id: row.id} : {}), ...(row.parent ? {_key: row.key} : {}), ...row.values};
    const states: any = {};
    for (const [, field] of this.templates.get(row.path.join('/')) || []) {
      if (field.kind) continue;
      const outcome = this.results.get(nodeKey(row.handle, field.field));
      result[field.field] = outcome?.state === 'ready' ? wire(outcome.value, wireScale(field)) : null;
      if (outcome && outcome.state !== 'ready') states[field.field] = publicResult(outcome);
    }
    if (Object.keys(states).length) result._states = states;
    if (Object.keys(row.children).length)
      result.children = Object.fromEntries(Object.entries(row.children).map(([table, items]) => [table, items.map(item => this.snapshot(item))]));
    return result;
  }

  dispose() {
    if (this.disposed) return;
    if (this.publishing) throw new CalcError('REACTIVE_REENTRANCY', 'Observers cannot dispose the document during publication');
    this.observers.clear(); this.ruleObservers.clear(); this.armed.clear(); this.rows.clear(); this.results.clear(); this.misses.clear();
    this.deps = new Map(); this.dependents = new Map(); this.order = []; this.steps = [];
    this.disposed = true;
  }
}

function compareChains(a: number[], b: number[]) {
  for (let index = 0; index < Math.min(a.length, b.length); index++) if (a[index] !== b[index]) return a[index] - b[index];
  return a.length - b.length;
}
