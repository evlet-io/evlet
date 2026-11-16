// Prompts: the one transient form (evlet.Prompt). An action, a prompt's submit() or an @api function returns one,
// and it opens here as a modal built from the record engine: a RecordStore over the prompt's schema (its finite
// session calculates as people type), the layout's sections, the Table grid and the field controls. The server is
// asked only to check (when the browser cannot calculate) and to submit, through the prompt's ticket. See
// docs/prompts.md.

import { html, type Child } from '../dom';
import { effect } from '../reactive';
import { ApiError, ConflictError, ValidationError, type FieldErrors } from '../api';
import { RecordStore, type Row } from '../model/record';
import { SchemaIndex, type ModelSchema } from '../model/schema';
import { autofocusForm, initialFocusField } from '../ui/autofocus';
import { ChildTable } from '../ui/childtable';
import { fieldFor } from '../ui/fields';
import { flowGrid } from '../ui/fold';
import { DialogFooter, openModal, toast } from '../ui/layers';
import { commitTyped } from '../ui/shortcuts';
import { sections, type ScreenModel } from './meta';
import type { AppContext } from './context';

/** A prompt as an action, a submit or an endpoint answers it: its ticket, title, schema and starting values. */
export interface PromptSurface {
  ticket: string;
  name: string;
  title: string;
  primary: string;
  source: { model: string; id: string; title: string } | null;
  /** The prompt and its Table rows, as models of the schema. */
  models: ModelSchema[];
  values: Record<string, any>;
  errors: FieldErrors;
  states: Record<string, string>;
}

/** What an action or a prompt's submit() returns. */
export interface Outcome {
  message?: string;
  open?: { model: string; id: string };
  /** A new record to review, filled with these values; nothing is saved until Create. */
  new?: { model: string; values: Record<string, unknown> };
  data?: unknown;
  /** The next prompt (a wizard). */
  prompt?: PromptSurface;
}

let context: AppContext | null = null;

/** Called once by the app, so a workspace shortcut can open an endpoint's prompt. */
export function usePrompts(ctx: AppContext) { context = ctx; }

// Values an outcome hands to the new-record screen of its model, taken once.
const prefills = new Map<string, { values: Record<string, unknown>; from: string }>();

/** The values an outcome left for a new record of `model`, if any (taken once). */
export function takePrefill(model: string) {
  const found = prefills.get(model);
  prefills.delete(model);
  return found ?? null;
}

/** Follow an outcome: say its message, open its record or new record, or show the next prompt. Whether it did anything. */
export async function follow(ctx: AppContext, outcome: Outcome | null | undefined, from = ''): Promise<boolean> {
  if (!outcome) return false;
  if (outcome.message) toast(outcome.message, { icon: 'check' });
  if (outcome.new) {
    prefills.set(outcome.new.model, { values: outcome.new.values ?? {}, from });
    await ctx.router.go('create', { model: outcome.new.model });
  } else if (outcome.open) await ctx.router.go('record', { model: outcome.open.model, id: String(outcome.open.id) });
  if (outcome.prompt) await openPrompt(ctx, outcome.prompt);
  return Boolean(outcome.message || outcome.new || outcome.open || outcome.prompt);
}

/** Call an @api endpoint and follow what it returns: a workspace shortcut that opens a prompt. */
export async function openService(name: string) {
  if (!context) throw new Error('openService needs the running app');
  try { await follow(context, await context.api.service<Outcome>(name)); }
  catch (error) { toast(error instanceof ApiError ? error.message : 'This could not be opened', { icon: 'info' }); }
}

/** The values a submit sends: what people can fill in, and each row under its key. */
function payload(row: Row): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const [name, field] of row.fields) if (!field.calculated && !field.meta.read_only) values[name] = field.value.peek();
  if (row.tables.size) values.children = Object.fromEntries([...row.tables].map(([name, table]) =>
    [name, table.rows.peek().map(line => ({ _key: line.key, ...payload(line) }))]));
  return values;
}

/** Show a prompt. Resolves with its submit's outcome (after following it), or null when it was closed. */
export async function openPrompt(ctx: AppContext, surface: PromptSurface): Promise<Outcome | null> {
  const index = new SchemaIndex({ ...ctx.schema.schema, models: [...ctx.schema.schema.models, ...surface.models] });
  const store = new RecordStore({ api: ctx.api, schema: index, model: surface.name, user: ctx.user });
  await store.create(surface.values);
  const model = index.model(surface.name) as ScreenModel;
  const groups = sections(model);
  const plain = !model.calculation?.plan && model.fields.some(meta => meta.compute);
  if (Object.keys(surface.errors).length) store.showErrors(surface.errors);

  // Without a finite plan (string computes), the server calculates: ask it after each change, newest answer wins.
  let asked = 0;
  if (plain) effect(() => {
    if (!store.editVersion.get()) return;
    const mine = ++asked;
    void ctx.api.prompt<{ values: Record<string, any> }>(surface.ticket, 'check', payload(store.root)).then(result => {
      if (mine !== asked) return;
      for (const [name, field] of store.root.fields) if (field.calculated) field.value.set(result.values[name] ?? null);
    }, () => {});
  });

  const group = (label: string): Child => {
    const found = groups.find(item => item.label === label);
    if (!found) return null;
    const grid = flowGrid(found.fields.map(meta => ({ node: fieldFor({ field: store.field(meta.name), links: ctx.links }),
      wide: Boolean(meta.wide ?? meta.type === 'longtext') })), found.flow);
    return html`${found.heading || !found.main ? html`<h3 class="prompt-group">${label}</h3>` : null}<div class="ev-cmp-grid">${grid}</div>`;
  };
  const table = (name: string): Child => {
    const child = (model.children ?? []).find(item => item.name === name);
    return child ? html`<div class="prompt-table">${ChildTable({ table: store.table(name), title: child.label, icon: 'rows-3', links: ctx.links })}</div>` : null;
  };
  const blocks = model.record_layout?.blocks ?? [];
  const body = () => blocks.map(block => block.kind === 'table' ? table(block.name) : block.kind === 'section' ? group(block.label) : null);

  return new Promise(resolve => {
    let settled = false, busy = false;
    const finish = (outcome: Outcome | null) => { if (!settled) { settled = true; store.dispose(); resolve(outcome); } };
    const submit = async (close: () => void) => {
      if (busy || !commitTyped()) return;
      busy = true;
      store.formError.set(null);
      try {
        await store.idle();
        const outcome = await ctx.api.prompt<Outcome>(surface.ticket, 'submit', payload(store.root));
        // Settled before closing: closing alone means the prompt was set aside.
        finish(outcome);
        close();
        if (!(await follow(ctx, outcome, surface.title))) toast(`${surface.title} done`, { icon: 'check' });
      } catch (error) {
        if (error instanceof ValidationError) store.showErrors(error.fields);
        else if (error instanceof ConflictError) store.formError.set(surface.source ? 'The record changed after this opened. Close it and open it again.' : error.message);
        else store.formError.set(error instanceof ApiError ? error.message : 'This could not be submitted');
      } finally { busy = false; }
    };
    let closeSheet = () => {};
    const confirmClose = () => {
      if (!store.editVersion.peek()) return true;
      openModal({ title: 'Discard what you filled in?', icon: 'info', tint: 'amber', narrow: true, body: () => html`<p>Nothing is saved.</p>`,
        footer: close => DialogFooter({ cancel: close, submit: { label: 'Discard', variant: 'danger', onClick: () => { close(); closeSheet(); } } }) });
      return false;
    };
    const title = surface.source?.title ? `${surface.title} · ${surface.source.title}` : surface.title;
    closeSheet = openModal({ title, icon: 'file-text', tint: 'blue', className: 'prompt', confirmClose, onClose: () => finish(null),
      onSubmit: () => void submit(closeSheet),
      body: () => [body(), html`<p class="ev-error full" role="alert" hidden=${() => !store.formError.get()}>${() => store.formError.get() ?? ''}</p>`],
      footer: close => DialogFooter({ note: 'Nothing is saved until you submit this.', cancel: () => { if (confirmClose()) close(); },
        submit: { label: surface.primary, onClick: () => void submit(close) } }) });
    // The field Meta.focus names, else the first field that can be filled in, takes the focus.
    const sheet = [...document.querySelectorAll<HTMLElement>('.modal.prompt')].pop();
    if (sheet) autofocusForm(sheet, () => initialFocusField({ name: surface.name, focus: (model as { focus?: string }).focus },
      groups.map(item => ({ fields: item.fields.map(meta => store.field(meta.name)) }))));
  });
}
