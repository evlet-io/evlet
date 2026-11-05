// Link filters that read other fields of the form: `branch = Link(Branch, filters={'company': Ref('company')})`.
//
// The schema sends a Link's filters in `behavior[name].filters`: a literal value, or
// {"$expr": "ref", "args": ["company"]} for another field of the same row ("parent.company" from a
// row of a Table). The picker searches with the current, unsaved values, and when a field changes,
// the Links whose filters read it are checked again (record.ts).

import type { Field, Row } from './record';

/** The path a filter value reads (`company`, `parent.company`), or null for a literal value. */
export function refPath(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null;
  const expr = value as { $expr?: unknown; args?: unknown };
  return expr.$expr === 'ref' && Array.isArray(expr.args) && typeof expr.args[0] === 'string' ? expr.args[0] : null;
}

function filtersOf(row: Row, name: string): Record<string, unknown> | null {
  const entry = row.model.behavior?.[name];
  return entry?.filters && !entry.filters_blocked ? entry.filters : null;
}

/** The field a Ref path names: on this row, or on the parent row for `parent.<name>`. */
function fieldAt(row: Row, path: string): Field | null {
  const parts = path.split('.');
  if (parts.length === 1) return row.fields.get(parts[0]) ?? null;
  if (parts.length === 2 && parts[0] === 'parent' && row.parent) return row.parent.fields.get(parts[1]) ?? null;
  return null;
}

const isEmpty = (value: unknown) => value === null || value === undefined || value === '';

export interface LinkScope {
  /** The filters with the form's current values in place of each Ref. */
  filters: Record<string, unknown>;
  /** Referenced fields that are empty: no record can match until they are filled in. */
  missing: Field[];
}

/**
 * A Link's filters with the form's current, unsaved values. Null when the Link has no filters the
 * browser may apply (none, or they read a field the role cannot), so only the server decides.
 * Reads values reactively: call it inside a derive to follow changes.
 */
export function linkScope(field: Field): LinkScope | null {
  const filters = filtersOf(field.row, field.name);
  if (!filters) return null;
  const scope: LinkScope = { filters: {}, missing: [] };
  for (const [key, value] of Object.entries(filters)) {
    const path = refPath(value);
    if (path === null) { scope.filters[key] = value; continue; }
    const source = fieldAt(field.row, path);
    if (!source) return null;
    const current = source.value.get();
    if (isEmpty(current)) scope.missing.push(source);
    else scope.filters[key] = current;
  }
  return scope;
}

/** The Link fields whose filters read `changed`: on its row, and `parent.<name>` on the rows of its tables. */
export function dependentsOf(changed: Field): Field[] {
  const found: Field[] = [];
  const scan = (row: Row, path: string) => {
    for (const name of Object.keys(row.model.behavior ?? {})) {
      const filters = filtersOf(row, name);
      const field = filters && Object.values(filters).some(value => refPath(value) === path) ? row.fields.get(name) : undefined;
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
