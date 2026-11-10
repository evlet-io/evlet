// Build the body of a create (POST) or update (PATCH) from a record's draft.
//
// A new record sends every value and its rows as full lists. A saved record sends only
// changed values, and each Table as a change set:
//   children: {lines: {create: [{_key, ...}], update: [{id, ...}], delete: [ids], order: [ids and _keys]}}
// `order` is sent only when saved rows changed places or a new row is not at the end.
// A table with sections (schema "sections": true) sends its section rows the same way, as {_key, section}
// to create, {id, section} to rename and ids to delete, and `order` (rows and sections) whenever a section changed.
// A table whose schema says row_changes: false (a ChildTable on a plain model) takes only a full
// list: saved rows as {id, ...changed values}, new rows without _key, rows left out are deleted.
// A read-only Table (one the record fills itself, Meta.derives) is never sent.

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
    if (table.meta.read_only) continue;
    const rows = table.rows.peek(), keyed = table.meta.row_changes !== false;
    // Section rows go in their places among the lines, as {_key, section}.
    const sections = new Map(table.sections.peek().map(section => [section.key, section]));
    if (rows.length || sections.size) children[name] = withSections(table).map(item => {
      const section = sections.get(item.key);
      return section ? { ...(keyed ? { _key: section.key } : {}), section: section.label } : fullRow(rows.find(line => line.key === item.key)!, keyed);
    });
  }
  if (Object.keys(children).length) body.children = children;
  return body;
}

function changedRow(row: Row): Body {
  const body: Body = {};
  for (const field of writable(row)) if (field.dirty.peek()) body[field.name] = field.value.peek() ?? null;
  const children: Body = {};
  for (const [name, table] of row.tables) {
    if (table.meta.read_only) continue;
    const changes = table.meta.row_changes === false ? fullList(table) : tableChanges(table);
    if (changes) children[name] = changes;
  }
  if (Object.keys(children).length) body.children = children;
  return body;
}

/** Every current row of a Table that takes only full lists, or null when it did not change. */
function fullList(table: Table): Body[] | null {
  if (!table.changed.peek()) return null;
  return table.rows.peek().map(row => row.isNew ? fullRow(row, false) : { id: row.id, ...changedRow(row) });
}

/** The change set for one Table, or null when it did not change. */
export function tableChanges(table: Table): Body | null {
  const rows = table.rows.peek();
  const create: Body[] = rows.filter(row => row.isNew).map(row => fullRow(row, true));
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
  const sections = table.sections.peek(), saved = table.savedSections;
  const sectionsChanged = JSON.stringify(sections) !== JSON.stringify(saved) || table.removedSections.peek().length > 0;
  for (const section of sections) {
    if (!section.id) create.push({ _key: section.key, section: section.label });
    else if (saved.find(item => item.id === section.id)?.label !== section.label) update.push({ id: section.id, section: section.label });
  }
  remove.push(...table.removedSections.peek());
  const changes: Body = {};
  if (create.length) changes.create = create;
  if (update.length) changes.update = update;
  if (remove.length) changes.delete = remove;
  if (moved || sectionsChanged) changes.order = withSections(table).map(item => item.id ?? item.key);
  return Object.keys(changes).length ? changes : null;
}

/** Rows and section rows in their order: each section just before the line it starts at. */
export function withSections(table: Table): Array<{ key: string; id: string | null }> {
  const sections = table.sections.peek(), out: Array<{ key: string; id: string | null }> = [];
  for (const row of table.rows.peek()) {
    out.push(...sections.filter(section => section.before === row.key));
    out.push({ key: row.key, id: row.isNew ? null : row.id });
  }
  out.push(...sections.filter(section => section.before === null || !table.rows.peek().some(row => row.key === section.before)));
  return out;
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
