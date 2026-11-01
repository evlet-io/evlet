// Build the body of a create (POST) or update (PATCH) from a record's draft.
//
// A new record sends every value and its rows as full lists. A saved record sends only
// changed values, and each Table as a change set:
//   children: {lines: {create: [{_key, ...}], update: [{id, ...}], delete: [ids], order: [ids and _keys]}}
// `order` is sent only when saved rows changed places or a new row is not at the end.

import type { Field, Row, Table } from './record';

type Body = Record<string, unknown>;

// Fields the client may write: not calculated and not read only for this role.
const writable = (row: Row) => [...row.fields.values()].filter(field => !field.calculated && !field.meta.read_only);
const present = (value: unknown) => value !== null && value !== undefined;

function fullRow(row: Row, withKey: boolean): Body {
  const body: Body = withKey ? { _key: row.key } : {};
  for (const field of writable(row)) {
    const value = field.value.peek();
    // An empty value the user chose (a cleared default) is sent, so the server keeps it empty.
    if (present(value)) body[field.name] = value;
    else if (field.touched) body[field.name] = null;
  }
  const children: Body = {};
  for (const [name, table] of row.tables) {
    const rows = table.rows.peek();
    if (rows.length) children[name] = rows.map(item => fullRow(item, true));
  }
  if (Object.keys(children).length) body.children = children;
  return body;
}

function changedRow(row: Row): Body {
  const body: Body = {};
  for (const field of writable(row)) if (field.dirty.peek()) body[field.name] = field.value.peek() ?? null;
  const children: Body = {};
  for (const [name, table] of row.tables) {
    const changes = tableChanges(table);
    if (changes) children[name] = changes;
  }
  if (Object.keys(children).length) body.children = children;
  return body;
}

/** The change set for one Table, or null when it did not change. */
export function tableChanges(table: Table): Body | null {
  const rows = table.rows.peek();
  const create = rows.filter(row => row.isNew).map(row => fullRow(row, true));
  const update: Body[] = [];
  for (const row of rows) {
    if (row.isNew) continue;
    const changes = changedRow(row);
    if (Object.keys(changes).length) update.push({ id: row.id, ...changes });
  }
  const remove = table.removed.peek().map(row => row.id);
  const kept = rows.filter(row => !row.isNew), before = table.saved.filter(row => kept.includes(row));
  const newAtEnd = rows.slice(rows.length - create.length).every(row => row.isNew);
  const moved = !newAtEnd || kept.some((row, index) => row !== before[index]);
  const changes: Body = {};
  if (create.length) changes.create = create;
  if (update.length) changes.update = update;
  if (remove.length) changes.delete = remove;
  if (moved) changes.order = rows.map(row => row.isNew ? row.key : row.id);
  return Object.keys(changes).length ? changes : null;
}

/** The request body for saving this record: everything for a new record, changes for a saved one. */
export function buildChanges(root: Row): Body {
  return root.isNew ? fullRow(root, false) : changedRow(root);
}

/** Field paths as the server names them in errors: table.<row id or _key>.field at any depth. */
export function findField(root: Row, path: string): Field | Table | null {
  const parts = path.split('.');
  let row = root;
  for (let index = 0; index < parts.length; index++) {
    const name = parts[index];
    const table = row.tables.get(name);
    if (table && index + 2 < parts.length) {
      const key = parts[index + 1];
      const next = table.rows.peek().find(item => String(item.id) === key || item.key === key);
      if (!next) return null;
      row = next;
      index++;
      continue;
    }
    if (index !== parts.length - 1) return null;
    return row.fields.get(name) ?? table ?? null;
  }
  return null;
}
