// Duplicate a saved record (docs/ui.md#duplicate): the values a new, unsaved record of the same model
// starts with. The copy stays in memory, held for the next new-record screen of that model and taken
// once, so nothing goes through the address and nothing is saved until the person creates it.
//
// Copied: the visible, stored fields this role may write, and the rows of every Table at every depth,
// without their ids, so they are created new. Left out: the id, the name or number the server assigns
// (the naming field, Sequence fields), the State, calculated and read-only fields, fields marked
// unique (named in a note), and a row's link to its parent. Files and the Journal belong to the record.
// Meta.duplicate = False (`duplicate: false` on a model) turns Duplicate off, and copy=False (`copy: false`
// on a field or a Table) leaves it as a new record starts. Every such schema key is read here only, so a rename touches this file.

import type { Row } from '../model/record';
import type { FieldSchema } from '../model/schema';
import type { ScreenModel } from './meta';

export interface DuplicateCopy {
  /** Values for RecordStore.create, with the rows as {children: {table: [rows]}}. */
  values: Record<string, unknown>;
  /** What it was copied from: the source record's title. */
  from: string;
  /** Unique fields that had a value and were left empty, by label ("Code", "Milestones: Code"). */
  unique: string[];
}

const empty = (value: unknown) => value === null || value === undefined || value === '';
const clone = (value: unknown) => value !== null && typeof value === 'object' ? JSON.parse(JSON.stringify(value)) : value;

/** Whether the model offers Duplicate at all: `duplicate: false` turns it off; absent means allowed. */
export const duplicateAllowed = (model: { duplicate?: boolean }) => model.duplicate !== false;

/** Whether a Table's rows are copied: not one marked `copy: false`; absent means copy. */
const tableCopied = (table: { copy?: boolean }) => table.copy !== false;

/** Whether a field's value is copied: a visible, stored field the role may write and the server does not set. */
function copied(meta: FieldSchema, calculated: boolean, skip: ReadonlySet<string>): boolean {
  return meta.name !== 'id' && !skip.has(meta.name) && meta.copy !== false && !meta.hidden && !meta.read_only && !calculated && !meta.compute
    && meta.stored !== false && meta.type !== 'sequence' && meta.type !== 'state' && (meta as { state?: boolean }).state !== true;
}

function copyRow(row: Row, skip: ReadonlySet<string>, unique: string[], where: string): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of row.fields.values()) {
    if (!copied(field.meta, field.calculated, skip)) continue;
    // The saved value: edits not yet saved are not part of the record being copied.
    const value = field.server.peek();
    if (field.meta.unique) {
      const label = where + field.meta.label;
      if (!empty(value) && !unique.includes(label)) unique.push(label);
      continue;
    }
    values[field.meta.name] = clone(value);
  }
  const children: Record<string, unknown[]> = {};
  for (const [name, table] of row.tables) {
    if (!tableCopied(table.meta)) continue;
    // A row's link to its parent is set by the server when the new parent is created.
    const rows: Array<Record<string, unknown>> = [];
    // Section rows come along as {section: label}, each before the line it starts at.
    const sections = table.meta.sections ? table.savedSections : [];
    for (const item of table.saved) {
      rows.push(...sections.filter(section => section.before === item.key).map(section => ({ section: section.label })));
      rows.push(copyRow(item, new Set([table.meta.link]), unique, `${table.meta.label}: `));
    }
    rows.push(...sections.filter(section => section.before === null || !table.saved.some(item => item.key === section.before)).map(section => ({ section: section.label })));
    if (rows.length) children[name] = rows;
  }
  if (Object.keys(children).length) values.children = children;
  return values;
}

/** The copy of a saved record for a new record of its model. */
export function duplicateOf(model: ScreenModel, root: Row, from: string): DuplicateCopy {
  const skip = new Set([model.state?.field, model.naming?.field].filter((name): name is string => Boolean(name)));
  const unique: string[] = [];
  return { values: copyRow(root, skip, unique, ''), from, unique };
}

const held = new Map<string, DuplicateCopy>();

/** Hold a copy for the next new-record screen of `model`. */
export function holdDuplicate(model: string, copy: DuplicateCopy) { held.set(model, copy); }

/** The copy held for a new record of `model`, if any (taken once). */
export function takeDuplicate(model: string): DuplicateCopy | null {
  const found = held.get(model) ?? null;
  held.delete(model);
  return found;
}

/** Forget a held copy: the navigation to the new record did not happen. */
export function dropDuplicate(model: string) { held.delete(model); }
