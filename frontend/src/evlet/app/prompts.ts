// Prompts: the one transient form (evlet.Prompt). An action, a prompt's submit() or an @api function returns one,
// and it opens here as a modal built from the record engine: a RecordStore over the prompt's schema (its finite
// session calculates as people type), the layout's sections, the Table grid and the field controls. The server is
// asked only to check (when the browser cannot calculate) and to submit, through the prompt's ticket. See
// docs/prompts.md.

import { html, type Child } from '../dom';
import { cell, effect } from '../reactive';
import { ApiError, ConflictError, ValidationError, type FieldErrors } from '../api';
import { RecordStore, type Row } from '../model/record';
import { loadScript, ScriptHost, type ScriptPage } from '../model/script';
import { SchemaIndex, type FieldSchema, type ModelSchema } from '../model/schema';
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
  /** A new record to review, filled with these values; nothing is saved until Create. `values` holds the fields the
   * person may write; `calculated` is the whole record as the server works it out (derived and calculated values too). */
  new?: { model: string; values: Record<string, unknown>; calculated?: Record<string, unknown> };
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
    prefills.set(outcome.new.model, { values: outcome.new.calculated ?? outcome.new.values ?? {}, from });
    await ctx.router.go('create', { model: outcome.new.model });
  } else if (outcome.open) await ctx.router.go('record', { model: outcome.open.model, id: String(outcome.open.id) });
  if (outcome.prompt) await openPrompt(ctx, outcome.prompt);
  return Boolean(outcome.message || outcome.new || outcome.open || outcome.prompt);
}

/** Call an @api endpoint and follow what it returns: a workspace shortcut that opens a prompt. */
export async function openService(name: string) {
  if (!context) throw new Error('openService needs the running app');
  try { await follow(context, await context.api.service<Outcome>(name)); }
  catch (error) { toast(error instanceof ApiError ? error.message : 'This could not be opened', { tone: 'error' }); }
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
  let outcome: Outcome | null = null;
  const sent = await sheet(ctx, { models: surface.models, name: surface.name, values: surface.values, errors: surface.errors,
    title: surface.source?.title ? `${surface.title} · ${surface.source.title}` : surface.title, primary: surface.primary,
    changed: surface.source ? 'The record changed after this opened. Close it and open it again.' : null,
    check: values => ctx.api.prompt<{ values: Record<string, any> }>(surface.ticket, 'check', values),
    send: async values => { outcome = await ctx.api.prompt<Outcome>(surface.ticket, 'submit', values); } });
  if (!sent) return null;
  if (!(await follow(ctx, outcome, surface.title))) toast(`${surface.title} done`, { icon: 'check' });
  return outcome;
}

/**
 * A form of a few fields in the same modal: what a move asks for (its remark and fields) or an action's typed
 * arguments. `run` answers null when done, a message, or field errors by name. Empty values are not sent.
 */
export function openForm(ctx: AppContext, options: { title: string; primary?: string; fields: FieldSchema[];
  run: (values: Record<string, unknown>) => Promise<string | Record<string, string> | null> }): Promise<boolean> {
  const name = '_form';
  const fields = options.fields.map(meta => ({ ...meta, hidden: false }));
  const model = { name, label: options.title, plural: options.title, title_field: 'id', list_fields: [], search_fields: [], fields, layout: [],
    children: [], actions: [], permissions: { create: true, update: true, delete: false } } as unknown as ModelSchema;
  return sheet(ctx, { models: [model], name, title: options.title, primary: options.primary ?? options.title, narrow: true,
    values: Object.fromEntries(fields.filter(meta => meta.default !== undefined).map(meta => [meta.name, meta.default])),
    send: async values => {
      const problem = await options.run(Object.fromEntries(Object.entries(values).filter(([key, value]) =>
        key !== 'children' && value !== null && value !== undefined && value !== '')));
      if (typeof problem === 'string') throw new ApiError(problem, 0, 'response');
      if (problem) throw new ValidationError('Validation failed', 422, 'validation', problem);
    } });
}

interface SheetOptions {
  models: ModelSchema[]; name: string; title: string; primary: string; values: Record<string, any>; errors?: FieldErrors;
  narrow?: boolean; changed?: string | null;
  /** Asks the server what it decides, for string computes the browser cannot run. */
  check?: (values: Record<string, unknown>) => Promise<{ values: Record<string, any> }>;
  /** Sends the values; rejects with an ApiError (a ValidationError for field errors) when refused. */
  send: (values: Record<string, unknown>) => Promise<void>;
}

/** Ask a model script's yes-or-no question (this.ui.confirm); resolves true for OK. */
export function confirmText(text: string): Promise<boolean> {
  return new Promise(resolve => {
    let answered = false;
    const answer = (value: boolean, done: () => void) => { answered = true; resolve(value); done(); };
    openModal({ title: text, icon: 'info', narrow: true, body: () => null,
      footer: done => DialogFooter({ cancel: () => answer(false, done), submit: { label: 'OK', variant: 'primary', onClick: () => answer(true, done) } }),
      onClose: () => { if (!answered) resolve(false); } });
  });
}

/** What a prompt's model script may do: a prompt has no saved record, so call, move, save and reload reject. */
function promptPage(ctx: AppContext): ScriptPage {
  const none = () => Promise.reject(new Error('A prompt has no saved record: call, move, save and reload work on a record page'));
  return { api: ctx.api, locked: cell(false), call: none, move: none, save: none, reload: none, toast: text => { toast(text); },
    confirm: confirmText, open: (model, id) => void ctx.router.go('record', { model, id }) };
}

/** The modal on the record engine. Resolves true once sent, false when closed. */
async function sheet(ctx: AppContext, options: SheetOptions): Promise<boolean> {
  const index = new SchemaIndex({ ...ctx.schema.schema, models: [...ctx.schema.schema.models, ...options.models] });
  const store = new RecordStore({ api: ctx.api, schema: index, model: options.name, user: ctx.user });
  await store.create(options.values);
  const model = index.model(options.name) as ScreenModel;
  const groups = sections(model);
  // The prompt's model script (Meta.script), loaded as on a record page once the values from load() are in. Closing
  // the modal makes its views inert; it never submits or closes the prompt.
  const script = model.script ? new ScriptHost(store, promptPage(ctx)) : null;
  if (script && model.script) {
    const { url, on_open } = model.script;
    void loadScript(url).then(Script => script.start(Script, on_open), error => console.error(`${options.name} script could not load`, error));
  }
  if (options.errors && Object.keys(options.errors).length) store.showErrors(options.errors);

  // Without a finite plan (string computes), the server calculates: ask it after each change, newest answer wins.
  let asked = 0;
  const check = options.check;
  if (check && !model.calculation?.plan && model.fields.some(meta => meta.compute)) effect(() => {
    if (!store.editVersion.get()) return;
    const mine = ++asked;
    void check(payload(store.root)).then(result => {
      if (mine !== asked) return;
      for (const [name, field] of store.root.fields) if (field.calculated) field.value.set(result.values[name] ?? null);
    }, () => {});
  });

  const group = (found: (typeof groups)[number], titled: boolean): Child => {
    const grid = flowGrid(found.fields.map(meta => ({ node: fieldFor({ field: store.field(meta.name), links: ctx.links }),
      wide: Boolean(meta.wide ?? meta.type === 'longtext') })), found.flow);
    return html`${titled && (found.heading || !found.main) ? html`<h3 class="prompt-group">${found.label}</h3>` : null}<div class="ev-cmp-grid">${grid}</div>`;
  };
  const table = (name: string): Child => {
    const child = (model.children ?? []).find(item => item.name === name);
    // Hidden while the Table's applies_when or visible_when is false, as on a record.
    return child ? html`<div class="prompt-table" hidden=${() => !store.table(name).rules.get().visible}>${ChildTable({ table: store.table(name), title: child.label, icon: 'rows-3', links: ctx.links })}</div>` : null;
  };
  const blocks = model.record_layout?.blocks;
  const body = () => blocks ? blocks.map(block => block.kind === 'table' ? table(block.name)
    : block.kind === 'section' ? (found => found ? group(found, true) : null)(groups.find(item => item.label === block.label)) : null)
    : groups.map(found => group(found, false));

  return new Promise(resolve => {
    let settled = false, busy = false;
    const finish = (sent: boolean) => { if (!settled) { settled = true; script?.dispose(); store.dispose(); resolve(sent); } };
    const submit = async (close: () => void) => {
      if (busy || !commitTyped()) return;
      busy = true;
      store.formError.set(null);
      try {
        await store.idle();
        await options.send(payload(store.root));
        // Settled before closing: closing alone means the form was set aside.
        finish(true);
        close();
      } catch (error) {
        if (error instanceof ValidationError) store.showErrors(error.fields);
        else if (error instanceof ConflictError) store.formError.set(options.changed ?? error.message);
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
    closeSheet = openModal({ title: options.title, icon: 'file-text', tint: 'blue', className: 'prompt', narrow: options.narrow, confirmClose,
      onClose: () => finish(false), onSubmit: () => void submit(closeSheet),
      body: () => [body(), html`<p class="ev-error full" role="alert" hidden=${() => !store.formError.get()}>${() => store.formError.get() ?? ''}</p>`],
      footer: close => DialogFooter({ note: options.narrow ? '' : 'Nothing is saved until you submit this.', cancel: () => { if (confirmClose()) close(); },
        submit: { label: options.primary, onClick: () => void submit(close) } }) });
    // The field Meta.focus names, else the first field that can be filled in, takes the focus.
    const opened = [...document.querySelectorAll<HTMLElement>('.modal.prompt')].pop();
    if (opened) autofocusForm(opened, () => initialFocusField({ name: options.name, focus: (model as { focus?: string }).focus },
      groups.map(item => ({ fields: item.fields.map(meta => store.field(meta.name)) }))));
  });
}
