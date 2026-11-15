// Unsaved changes as data: what a draft changed, so it can be kept when the person leaves the
// record and put back when they return (docs/internal/unsaved-changes.md).
//
//   const changes = capture(store.root);        // edited writable fields and changed rows
//   apply(store.root, changes);                  // later: the same edits, as unsaved edits again
//   conflicts(store.root, changes);              // fields both the person and a later save changed
//
// Only fields the person may write are kept. `base` holds each field's saved value when the edit
// started, so a restore can tell which fields someone else changed since. Row order is not kept.

import { sameValue, type Field, type Row } from './record';

export interface NodeChanges {
  values?: Record<string, unknown>;
  base?: Record<string, unknown>;
  children?: Record<string, TableChanges>;
  /** A new row's key. */
  key?: string;
}
export interface TableChanges { added?: NodeChanges[]; changed?: Record<string, NodeChanges>; removed?: string[] }

const writable = (row: Row) => [...row.fields.values()].filter(field => !field.calculated && !field.meta.read_only);
const present = (value: unknown) => value !== null && value !== undefined;

export const isEmpty = (node: NodeChanges | null | undefined) => !node || (!Object.keys(node.values ?? {}).length && !Object.keys(node.children ?? {}).length);

/** What a saved row's draft changed: dirty writable fields with their saved values, and row changes. */
export function capture(row: Row): NodeChanges {
  const node: NodeChanges = {};
  const values: Record<string, unknown> = {}, base: Record<string, unknown> = {};
  for (const field of writable(row)) {
    if (!field.dirty.peek()) continue;
    values[field.name] = field.value.peek() ?? null;
    base[field.name] = field.server.peek() ?? null;
  }
  if (Object.keys(values).length) { node.values = values; node.base = base; }
  const children: Record<string, TableChanges> = {};
  for (const [name, table] of row.tables) {
    const change: TableChanges = {};
    const rows = table.rows.peek();
    const added = rows.filter(item => item.isNew).map(item => newRow(item));
    if (added.length) change.added = added;
    const changed: Record<string, NodeChanges> = {};
    for (const item of rows) {
      if (item.isNew || !item.id || !item.dirty.peek()) continue;
      const inner = capture(item);
      if (!isEmpty(inner)) changed[item.id] = inner;
    }
    if (Object.keys(changed).length) change.changed = changed;
    const removed = table.removed.peek().map(item => item.id).filter((id): id is string => Boolean(id));
    if (removed.length) change.removed = removed;
    if (Object.keys(change).length) children[name] = change;
  }
  if (Object.keys(children).length) node.children = children;
  return node;
}

/** A new record's draft: the fields the person set and every row. */
export function captureNew(row: Row, all = false): NodeChanges {
  const node: NodeChanges = {};
  const values: Record<string, unknown> = {};
  for (const field of writable(row)) if (field.touched || (all && present(field.value.peek()))) values[field.name] = field.value.peek() ?? null;
  if (Object.keys(values).length) node.values = values;
  const children: Record<string, TableChanges> = {};
  for (const [name, table] of row.tables) {
    const rows = table.rows.peek();
    if (rows.length) children[name] = { added: rows.map(item => newRow(item)) };
  }
  if (Object.keys(children).length) node.children = children;
  return node;
}

function newRow(row: Row): NodeChanges {
  const values: Record<string, unknown> = {};
  for (const field of writable(row)) {
    const value = field.value.peek();
    if (present(value) || field.touched) values[field.name] = value ?? null;
  }
  const node: NodeChanges = { key: row.key, values };
  const children: Record<string, TableChanges> = {};
  for (const [name, table] of row.tables) {
    const rows = table.rows.peek();
    if (rows.length) children[name] = { added: rows.map(item => newRow(item)) };
  }
  if (Object.keys(children).length) node.children = children;
  return node;
}

/** Values for Table.add or RecordStore.create: a row's values with its rows of rows. */
export function rowValues(node: NodeChanges, withKey = true): Record<string, unknown> {
  const result: Record<string, unknown> = { ...(withKey && node.key ? { _key: node.key } : {}), ...(node.values ?? {}) };
  const children: Record<string, unknown[]> = {};
  for (const [name, change] of Object.entries(node.children ?? {})) if (change.added?.length) children[name] = change.added.map(item => rowValues(item));
  if (Object.keys(children).length) result.children = children;
  return result;
}

export interface Conflict {
  /** title, or lines.<row id>.qty */
  path: string;
  label: string;
  field: Field;
  mine: unknown;
  saved: unknown;
}

/** Fields the person changed that a later save changed too, to something else. */
export function conflicts(row: Row, node: NodeChanges, prefix = '', place = ''): Conflict[] {
  const found: Conflict[] = [];
  for (const [name, mine] of Object.entries(node.values ?? {})) {
    const field = row.fields.get(name);
    if (!field || !node.base || !Object.prototype.hasOwnProperty.call(node.base, name)) continue;
    const saved = field.server.peek(), base = node.base[name];
    if (!sameValue(field.meta, saved, base) && !sameValue(field.meta, saved, mine))
      found.push({ path: prefix + name, label: place + field.label, field, mine, saved });
  }
  for (const [name, change] of Object.entries(node.children ?? {})) {
    const table = row.tables.get(name);
    if (!table) continue;
    for (const [id, inner] of Object.entries(change.changed ?? {})) {
      const item = table.rows.peek().find(each => each.id === id);
      if (item) found.push(...conflicts(item, inner, `${prefix}${name}.${id}.`, `${place}${table.meta.label}, row ${table.rows.peek().indexOf(item) + 1}: `));
    }
  }
  return found;
}

/**
 * Put kept changes back into a draft as unsaved edits. Paths in `skip` keep the saved value.
 * Returns how many edits no longer fit: a field that is gone or closed now, a row that was deleted.
 */
export function apply(row: Row, node: NodeChanges, skip: ReadonlySet<string> = new Set(), prefix = ''): number {
  let missed = 0;
  for (const [name, value] of Object.entries(node.values ?? {})) {
    if (skip.has(prefix + name)) continue;
    const field = row.fields.get(name);
    if (!field || !field.editable.peek()) { missed++; continue; }
    try { field.set(value); } catch { missed++; }
  }
  for (const [name, change] of Object.entries(node.children ?? {})) {
    const table = row.tables.get(name);
    if (!table) { missed++; continue; }
    const find = (id: string) => table.rows.peek().find(each => each.id === id);
    for (const id of change.removed ?? []) {
      const item = find(id);
      if (!item) { missed++; continue; }
      try { table.remove(item); } catch { missed++; }
    }
    for (const [id, inner] of Object.entries(change.changed ?? {})) {
      const item = find(id);
      if (!item) { missed++; continue; }
      missed += apply(item, inner, skip, `${prefix}${name}.${id}.`);
    }
    for (const added of change.added ?? []) {
      try { table.add(rowValues(added)); } catch { missed++; }
    }
  }
  return missed;
}

/** Size of the changes as the server counts them (UTF-8 bytes of the JSON). */
export const sizeOf = (node: NodeChanges) => new TextEncoder().encode(JSON.stringify(node)).length;
