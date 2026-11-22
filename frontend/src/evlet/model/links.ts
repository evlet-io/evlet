// Link filters that read other fields of the form: `branch = Link(Branch, filters={'company': Ref('company')})`.
//
// The schema sends a Link's filters in `behavior[name].filters`: one group of conditions (an object), or a list
// of groups any one of which may match. Each value is a literal, or {"$expr": "ref", "args": [path]} where path is
// another field of the same row ("parent.company" from a row of a Table), a column of one of the form's Tables
// ("packs.unit": the rows' values, unsaved rows included), or a path through a Link ("item.unit",
// "item.packs.unit") whose rest the server reads as this person. The picker searches with the current, unsaved
// values, and when a field changes, the Links whose filters read it are checked again (record.ts).

import type { Field, Row } from './record';

type Group = Record<string, unknown>;

/** The path a filter value reads (`company`, `parent.company`), or null for a literal value. */
export function refPath(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null;
  const expr = value as { $expr?: unknown; args?: unknown };
  return expr.$expr === 'ref' && Array.isArray(expr.args) && typeof expr.args[0] === 'string' ? expr.args[0] : null;
}

/** The Link's filter groups (one for an object), or null when the browser may not apply them. */
function groupsOf(row: Row, name: string): Group[] | null {
  const entry = row.model.behavior?.[name];
  if (!entry?.filters || entry.filters_blocked) return null;
  return Array.isArray(entry.filters) ? entry.filters : [entry.filters];
}

/** What a Ref path reads on the form: a field, a Table column (one value per row) or a Link the server reads through. */
interface FormRef { form: string; kind: 'field' | 'column' | 'link'; row: Row; name: string; field: Field | null }

function formRef(row: Row, path: string): FormRef | null {
  let parts = path.split('.'), owner: Row = row, prefix = '';
  if (parts[0] === 'parent' && parts.length > 1) {
    if (!row.parent) return null;
    owner = row.parent; parts = parts.slice(1); prefix = 'parent.';
  }
  if (parts.length === 1) {
    const field = owner.fields.get(parts[0]);
    return field ? { form: path, kind: 'field', row: owner, name: parts[0], field } : null;
  }
  if (parts.length === 2 && owner.tables.has(parts[0])) return { form: path, kind: 'column', row: owner, name: parts[0], field: null };
  const link = owner.fields.get(parts[0]);
  return link && link.meta.type === 'link' ? { form: prefix + parts[0], kind: 'link', row: owner, name: parts[0], field: link } : null;
}

const isEmpty = (value: unknown) => value === null || value === undefined || value === '';

/** The form's current value for a Ref: one value, or the distinct non-empty values of a Table column. */
function formValue(ref: FormRef): unknown {
  if (ref.kind !== 'column') return ref.field!.value.get();
  const [table, column] = ref.form.split('.').slice(-2);
  const values = (ref.row.tables.get(table)?.rows.get() ?? []).map(row => row.fields.get(column)?.value.get());
  return [...new Set(values.filter(value => !isEmpty(value)))];
}

const isUnset = (value: unknown) => isEmpty(value) || (Array.isArray(value) && value.length === 0);

export interface LinkScope {
  /** The filters with the form's current values in place of each Ref (one group the browser can apply). */
  filters: Record<string, unknown>;
  /** Referenced fields that are empty: no record can match until they are filled in. */
  missing: Field[];
  /** True when only the server can apply the filters (several groups, a Table column or a Ref through a Link):
   * `filters` is then empty, and the browser neither checks a selection nor fills a new record with them. */
  server?: boolean;
}

/**
 * A Link's filters with the form's current, unsaved values. Null when the Link has no filters the
 * browser may apply (none, or they read a field the role cannot), so only the server decides.
 * Reads values reactively: call it inside a derive to follow changes.
 */
export function linkScope(field: Field): LinkScope | null {
  const groups = groupsOf(field.row, field.name);
  if (!groups) return null;
  const scope: LinkScope = { filters: {}, missing: [] };
  const local = groups.length === 1;
  const blocked: Field[][] = [];
  for (const group of groups) {
    const missing: Field[] = [];
    let unnamed = false;
    for (const [key, value] of Object.entries(group)) {
      const path = refPath(value);
      if (path === null) { if (local) scope.filters[key] = value; continue; }
      const ref = formRef(field.row, path);
      if (!ref) return null;
      if (ref.kind !== 'field') scope.server = true;
      const current = formValue(ref);
      if (isUnset(current)) { if (ref.field) missing.push(ref.field); else unnamed = true; }
      else if (local) scope.filters[key] = current;
    }
    if (missing.length || unnamed) blocked.push(unnamed ? [] : missing);
  }
  if (!local) scope.server = true;
  if (scope.server) scope.filters = {};
  // "Choose … first" only when every group waits on a field the form can name.
  if (blocked.length === groups.length && blocked.every(fields => fields.length)) scope.missing = [...new Set(blocked.flat())];
  return scope;
}

/**
 * What the search route needs to know about the form a Link is on (GET /api/<target>/search, evlet/search.py):
 * `link` names the Link (`<model>.<field>`, a Table's row model included) and `values` holds the current value
 * of every form path its filters and its option read ({"parent.company": "…"}; a Table column as the list of its
 * rows' values; for a Ref through a Link, the Link's value). The server applies the filters as declared with
 * these values; empty values are left out. Reads values reactively.
 */
export function linkContext(field: Field): { link: string; values: Record<string, unknown> } {
  const values: Record<string, unknown> = {};
  const add = (path: string) => {
    const ref = formRef(field.row, path);
    if (!ref || ref.form in values) return;
    const current = formValue(ref);
    if (!isUnset(current)) values[ref.form] = current;
  };
  for (const path of field.meta.inputs ?? []) add(path);
  for (const group of groupsOf(field.row, field.name) ?? []) for (const value of Object.values(group)) { const path = refPath(value); if (path) add(path); }
  return { link: `${field.row.model.name}.${field.name}`, values };
}

/** The Link fields whose filters read `changed`: on its row, and `parent.<name>` on the rows of its tables. */
export function dependentsOf(changed: Field): Field[] {
  const found: Field[] = [];
  const scan = (row: Row, path: string) => {
    for (const name of Object.keys(row.model.behavior ?? {})) {
      const groups = groupsOf(row, name);
      const reads = groups?.some(group => Object.values(group).some(value => {
        const ref = refPath(value);
        return ref !== null && formRef(row, ref)?.form === path;
      }));
      const field = reads ? row.fields.get(name) : undefined;
      if (field && field !== changed) found.push(field);
    }
  };
  scan(changed.row, changed.name);
  for (const table of changed.row.tables.values()) for (const row of table.rows.peek()) scan(row, `parent.${changed.name}`);
  return found;
}

/**
 * Whether a target record (as the browser already holds it) fits the filters: true or false, or
 * null when the record or one of the filtered fields is not at hand and the server must decide.
 */
export function fitsRecord(filters: Record<string, unknown>, record: Record<string, unknown> | null | undefined): boolean | null {
  if (!record) return null;
  for (const [key, expected] of Object.entries(filters)) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) return null;
    const actual = record[key];
    if (isEmpty(actual) || String(actual) !== String(expected)) return false;
  }
  return true;
}
