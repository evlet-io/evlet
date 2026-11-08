// Slips: controlled forms an application declares in Python (evlet.Slip) and presents from a record,
// the New menu, an action's outcome or browser code (presentSlip). Every change is checked by the
// server; nothing is saved until the slip is handed in, and then only by its outcome: the slip's
// handler, or a document of another model. See docs/slips.md.

import { html, type Child } from '../dom';
import { cell, type Cell } from '../reactive';
import { ApiError, type FieldErrors } from '../api';
import type { FieldSchema } from '../model/schema';
import { Button, IconButton } from '../ui/base';
import { FieldControl, fieldFor, localField } from '../ui/fields';
import { humanize } from '../ui/kinds';
import { DialogFooter, openModal, toast } from '../ui/layers';
import type { AppContext } from './context';

export interface SlipSource { model: string; id: string }
/** Present this slip: what an action or a hand-in returns as `present`. */
export interface Presentation { slip: string; source?: SlipSource | null; values?: Record<string, unknown> }
/** What a hand-in returns. */
export interface SlipOutcome {
  message?: string;
  open?: SlipSource;
  data?: unknown;
  present?: Presentation;
  document?: { model: string; id?: number; values?: Record<string, unknown>; review?: boolean };
}

type SlipField = FieldSchema & { calculated?: boolean };
interface SlipDefinition { name: string; label: string; fields: SlipField[]; children: SlipTableSchema[] }
interface SlipTableSchema { name: string; label: string; allow_add?: boolean; definition: SlipDefinition }
type Rule = { visible?: boolean; required?: boolean; read_only?: boolean };

/** The neutral schema of POST /api/_slips/{name}/present and /check. */
export interface SlipSurface {
  slip: string;
  version: number;
  label: string;
  hand_in_label: string;
  source: (SlipSource & { title: string | null; revision: string }) | null;
  outcome: { kind: 'handler' } | { kind: 'document'; model: string; review: boolean };
  layout: Array<{ label: string; fields: string[] }>;
  /** Slip.focus: the field the form puts the cursor in. Absent: the first editable field. */
  focus?: string;
  definition: SlipDefinition;
  values: Record<string, any>;
  rules: Record<string, Rule>;
  errors: FieldErrors;
  states: Record<string, string>;
  revision?: unknown;
}

type Local = ReturnType<typeof localField>;
interface SlipRow { key: string; fields: Map<string, Local> }

// ---------- the app's slips ----------

let context: AppContext | null = null;

/** Called once by the app, so presentSlip() works from any browser code. */
export function useSlips(ctx: AppContext) { context = ctx; }

// Values a review outcome hands to the new-record screen of its model, taken once.
const prefills = new Map<string, { values: Record<string, unknown>; from: string }>();

/** The values a slip left for a new record of `model`, if any (taken once). */
export function takePrefill(model: string) {
  const found = prefills.get(model);
  prefills.delete(model);
  return found ?? null;
}

/**
 * Present a slip. Resolves with its outcome once handed in (after the screens followed it: a message,
 * a record to open, the next slip, or a document), or null when it was set aside.
 */
export function presentSlip(name: string, options: { source?: SlipSource | null; values?: Record<string, unknown> } = {}): Promise<SlipOutcome | null> {
  if (!context) throw new Error('presentSlip needs the running app');
  return present(context, name, options);
}

async function present(ctx: AppContext, name: string, options: { source?: SlipSource | null; values?: Record<string, unknown> }): Promise<SlipOutcome | null> {
  let surface: SlipSurface;
  try {
    surface = await ctx.api.slip<SlipSurface>(name, 'present', { source: options.source ?? null, values: options.values ?? {} });
  } catch (error) {
    toast(error instanceof ApiError ? error.message : 'This slip could not be opened', { icon: 'info' });
    return null;
  }
  const outcome = await SlipSheet(ctx, surface, options.source ?? null);
  if (outcome) await follow(ctx, surface, outcome);
  return outcome;
}

/** What the screens do with an outcome: open the next slip, a document, a record, or say it is done. */
async function follow(ctx: AppContext, surface: SlipSurface, outcome: SlipOutcome) {
  const label = (model: string) => ctx.schema.schema.models.find(item => item.name === model)?.label ?? humanize(model);
  if (outcome.message) toast(outcome.message, { icon: 'check' });
  if (outcome.document?.review) {
    prefills.set(outcome.document.model, { values: outcome.document.values ?? {}, from: surface.label });
    await ctx.router.go('create', { model: outcome.document.model });
  } else if (outcome.document?.id) {
    if (!outcome.message) toast(`${label(outcome.document.model)} ${outcome.document.id} created`, { icon: 'check' });
  } else if (!outcome.message && !outcome.present && !outcome.open) toast(`${surface.label} done`, { icon: 'check' });
  if (outcome.open && !outcome.document?.review) await ctx.router.go('record', { model: outcome.open.model, id: String(outcome.open.id) });
  if (outcome.present) await present(ctx, outcome.present.slip, { source: outcome.present.source, values: outcome.present.values });
}

// ---------- the surface ----------

const blank = (meta: FieldSchema) => meta.type === 'boolean' ? false : null;
let rowNumber = 0;

function makeField(meta: SlipField, value: unknown): Local {
  const { calculated, ...rest } = meta;
  // A value the server calculates shows read only with its result; the browser never writes it.
  return localField({ ...rest, ...(calculated ? { compute: { $expr: 'server' } } : {}) } as FieldSchema, value ?? blank(meta));
}

/** The modal of one presented slip. Resolves with the outcome, or null when set aside. */
export function SlipSheet(ctx: AppContext, surface: SlipSurface, source: SlipSource | null): Promise<SlipOutcome | null> {
  const definition = surface.definition;
  const fields = new Map<string, Local>();
  for (const meta of definition.fields) fields.set(meta.name, makeField(meta, surface.values[meta.name]));
  const tables = new Map<string, { schema: SlipTableSchema; rows: Cell<SlipRow[]>; error: Cell<string | null> }>();
  const rowOf = (schema: SlipTableSchema, values: Record<string, any>): SlipRow => ({
    key: typeof values._key === 'string' ? values._key : `row-${++rowNumber}`,
    fields: new Map(schema.definition.fields.map(meta => [meta.name, makeField(meta, values[meta.name] ?? (meta.default_kind === 'static' ? meta.default : undefined))])),
  });
  for (const schema of definition.children) {
    const rows = (surface.values.children?.[schema.name] ?? []).map((values: Record<string, any>) => rowOf(schema, values));
    tables.set(schema.name, { schema, rows: cell<SlipRow[]>(rows, { name: `slip ${schema.name}` }), error: cell<string | null>(null) });
  }
  const touched = new Set<string>();
  const tried = cell(false);
  const changed = cell(false);
  const note = cell('');
  const key = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  let revision = 0, busy = false;

  // Every field by path: name, or table.rowKey.name for row fields.
  const each = (visit: (path: string, field: Local) => void) => {
    for (const [name, field] of fields) visit(name, field);
    for (const [name, table] of tables) for (const row of table.rows.peek()) for (const [field, local] of row.fields) visit(`${name}.${row.key}.${field}`, local);
  };
  const payload = () => {
    const writable = (map: Map<string, Local>) => Object.fromEntries([...map].filter(([, field]) => !field.calculated && !field.meta.read_only).map(([name, field]) => [name, field.value.peek()]));
    const values: Record<string, unknown> = writable(fields);
    if (tables.size) values.children = Object.fromEntries([...tables].map(([name, table]) => [name, table.rows.peek().map(row => ({ _key: row.key, ...writable(row.fields) }))]));
    return values;
  };
  const apply = (result: SlipSurface | null, errors: FieldErrors, states: Record<string, string>, rules: Record<string, Rule>) => {
    const rowValues = new Map<string, Record<string, any>>();
    for (const [name, rows] of Object.entries(result?.values.children ?? {})) for (const row of rows as Array<Record<string, any>>) rowValues.set(`${name}.${row._key}`, row);
    const unmatched: string[] = [];
    const known = new Set<string>();
    each((path, field) => {
      known.add(path);
      const meta = field.meta;
      if (result) {
        const parts = path.split('.');
        const value = parts.length === 1 ? result.values[path] : rowValues.get(`${parts[0]}.${parts[1]}`)?.[parts[2]];
        if (value !== undefined && (field.calculated || meta.read_only || !touched.has(path))) field.value.set(value);
      }
      const rule = rules[path] ?? {};
      field.rules.set({ applies: rule.visible ?? true, required: rule.required ?? meta.required, locked: rule.read_only ?? false });
      const shown = touched.has(path) || tried.peek();
      field.error.set(errors[path] ?? (shown && states[path] === 'required-input' ? 'This field is required' : null));
    });
    for (const [name, table] of tables) table.error.set(errors[name] ?? null);
    for (const [path, message] of Object.entries(errors)) if (!known.has(path) && !tables.has(path)) unmatched.push(path === '_form' ? message : `${humanize(path.split('.').pop()!)}: ${message}`);
    note.set(unmatched.join(' '));
  };
  apply(null, surface.errors, surface.states, surface.rules);

  // Checked on the server after each change; an older answer never overwrites a newer change.
  const check = async () => {
    const mine = ++revision;
    try {
      const result = await ctx.api.slip<SlipSurface>(surface.slip, 'check', { source, values: payload(), revision: mine });
      if (mine === revision) apply(result, result.errors, result.states, result.rules);
    } catch (error) {
      if (mine === revision) note.set(error instanceof ApiError ? error.message : 'The slip could not be checked');
    }
  };
  const edited = (path: string) => () => { touched.add(path); changed.set(true); void check(); };

  const control = (path: string, field: Local) => fieldFor({ field, links: ctx.links, onCommit: edited(path), full: field.meta.type === 'longtext' || field.meta.type === 'richtext' });
  const tableView = (name: string): Child => {
    const table = tables.get(name)!, { schema } = table;
    const columns = schema.definition.fields.filter(meta => !meta.hidden);
    const canAdd = schema.allow_add !== false;
    const add = () => { table.rows.set([...table.rows.peek(), rowOf(schema, {})]); changed.set(true); void check(); };
    const remove = (row: SlipRow) => { table.rows.set(table.rows.peek().filter(item => item !== row)); changed.set(true); void check(); };
    const cellView = (row: SlipRow, meta: SlipField) => {
      const field = row.fields.get(meta.name)!;
      return html`<td class=${meta.calculated || ['integer', 'decimal', 'currency', 'percent'].includes(meta.type) ? 'num' : null}
        class:invalid=${() => Boolean(field.error.get())}>${FieldControl(field, { label: meta.label, links: ctx.links, onCommit: edited(`${name}.${row.key}.${meta.name}`) }).element}
        <span class="err" role="alert">${() => field.error.get() ?? ''}</span></td>`;
    };
    return html`<div class="slip-table" data-table=${name}>
      <div class="slip-table-wrap"><table>
        <thead><tr><th class="idx" aria-label="Row"></th>${columns.map(meta => html`<th>${meta.label}${meta.required ? html`<span class="req" aria-hidden="true">*</span>` : null}</th>`)}${canAdd ? html`<th aria-label="Remove"></th>` : null}</tr></thead>
        <tbody>${() => table.rows.get().map((row, index) => html`<tr data-key=${row.key}><td class="idx">${String(index + 1)}</td>${columns.map(meta => cellView(row, meta))}
          ${canAdd ? html`<td>${IconButton({ icon: 'trash-2', tip: `Remove row ${index + 1}`, onClick: () => remove(row) })}</td>` : null}</tr>`)}</tbody>
      </table></div>
      ${() => table.rows.get().length ? null : html`<p class="muted slip-empty">No rows yet.</p>`}
      ${canAdd ? Button({ label: 'Add row', icon: 'plus', variant: 'ghost', size: 'small', onClick: add }) : null}
      <span class="err slip-table-err" role="alert">${() => table.error.get() ?? ''}</span></div>`;
  };

  const part = (name: string): Child => {
    if (tables.has(name)) return html`<div class="fld full">${tableView(name)}</div>`;
    const field = fields.get(name);
    return field && !field.meta.hidden ? control(name, field) : null;
  };
  const order = [...definition.fields.map(meta => meta.name), ...definition.children.map(table => table.name)];
  const groups: Array<{ label: string | null; names: string[] }> = surface.layout.length ? surface.layout.map(group => ({ label: group.label, names: group.fields })) : [];
  const rest = order.filter(name => !groups.some(group => group.names.includes(name)));
  if (rest.length) groups.push({ label: null, names: rest });
  const body = () => groups.map(group => [
    group.label ? html`<h3 class="slip-group">${group.label}</h3>` : null,
    // A table gets its own heading unless it is the whole group.
    ...group.names.map(name => tables.has(name) && !(group.label && group.names.length === 1) ? [html`<h3 class="slip-group">${tables.get(name)!.schema.label}</h3>`, part(name)] : part(name)),
  ]);

  return new Promise(resolve => {
    let settled = false;
    const finish = (outcome: SlipOutcome | null) => { if (!settled) { settled = true; resolve(outcome); } };
    const handIn = async (close: () => void) => {
      // A value typed but not yet committed (the field was not left) counts: commit every input first.
      for (const input of document.querySelectorAll('.slip .modal-b input, .slip .modal-b textarea')) input.dispatchEvent(new Event('change'));
      if (busy) return;
      busy = true;
      tried.set(true);
      revision++;
      note.set('');
      try {
        const outcome = await ctx.api.slip<SlipOutcome>(surface.slip, 'hand-in', { source, values: payload(), key }, surface.source?.revision ?? null);
        // Settled before closing: closing alone means the slip was set aside.
        finish(outcome);
        close();
      } catch (error) {
        if (error instanceof ApiError && error.kind === 'validation') {
          apply(null, error.fields, {}, Object.fromEntries([...fields].map(([name, field]) => [name, { visible: field.rules.peek().applies, required: field.rules.peek().required, read_only: field.rules.peek().locked }])));
          if (!note.peek()) note.set('Fix the marked fields to hand this in.');
        } else if (error instanceof ApiError && error.kind === 'conflict') {
          note.set(source ? 'The record changed after this slip opened. Close it and open it again.' : error.message);
        } else note.set(error instanceof ApiError ? error.message : 'The slip could not be handed in');
      } finally { busy = false; }
    };
    let closeSheet = () => {};
    const confirmClose = () => {
      if (!changed.peek()) return true;
      openModal({ title: 'Discard this slip?', icon: 'info', tint: 'amber', narrow: true,
        body: () => html`<p>What you filled in is not kept.</p>`,
        footer: close => DialogFooter({ cancel: close, submit: { label: 'Discard', variant: 'danger', onClick: () => { close(); closeSheet(); } } }) });
      return false;
    };
    const title = surface.source?.title ? `${surface.label} · ${surface.source.title}` : surface.label;
    closeSheet = openModal({ title, icon: 'file-text', tint: 'blue', className: 'slip', confirmClose, onClose: () => finish(null),
      onSubmit: () => void handIn(closeSheet),
      body: () => [body(), html`<p class="ev-error full" role="alert" hidden=${() => !note.get()}>${() => note.get()}</p>`],
      footer: close => DialogFooter({ note: () => tried.get() ? '' : 'Nothing is saved until you hand this in.',
        cancel: () => { if (confirmClose()) close(); }, submit: { label: surface.hand_in_label, onClick: () => void handIn(close) } }) });
  });
}
