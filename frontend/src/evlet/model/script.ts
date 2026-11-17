// Model scripts: the browser side of Meta.script (docs/model-scripts.md).
//
//   export default class Quotation extends evlet.Model {
//     customer_changed(field) { this.field('notes').warn('Check the credit limit'); }
//   }
//
// The Python model names which method runs where (on_change=, tooltip=Js(...), look=Js(...), control=,
// Main/Section custom=, Meta.on_open); the script never attaches itself. A script sees the record only through
// the small, frozen views below, never the store, and every write goes through the same path as typing. After the
// record closes or reloads the views go inert; a hook that throws is logged and the form keeps working.

import { cell, effect, runWithOwner, scope, untrack, type Readable } from '../reactive';
import { ApiError, type ApiClient, type ListQuery } from '../api';
import { Field, type RecordStore, type Row, type Table } from './record';
import type { Look } from './schema';

/** What the page lets a script do besides reading and editing the record. */
export interface ScriptPage {
  api: ApiClient;
  /** The record can't change now: its state locks it, or the role can't write it (versioned=False fields still can). */
  locked: Readable<boolean>;
  call(action: string, values: Record<string, unknown>): Promise<boolean>;
  move(key: string, remark?: string): Promise<boolean>;
  save(): Promise<boolean>;
  reload(): Promise<void>;
  toast(text: string): void;
  confirm(text: string): Promise<boolean>;
  open(model: string, id: string): void;
}

type ScriptClass = new () => Model;
const LOOKS = new Set<string>(['strong', 'muted', 'success', 'warning', 'danger']);
/** How deep hooks may call each other through the changes they make. */
export const MAX_DEPTH = 8;
const hosts = new WeakMap<RecordStore, ScriptHost>();
const owners = new WeakMap<object, ScriptHost>();
let constructing: ScriptHost | null = null;

const hostOf = (script: object) => {
  const host = owners.get(script);
  if (!host) throw new Error('Create a model script through Evlet, not with new');
  return host;
};

/** evlet.Model: what every model script extends. This is the whole public surface. */
class Model {
  constructor() { if (constructing) owners.set(this, constructing); }
  get model() { return hostOf(this).store.meta.name; }
  get id() { return hostOf(this).store.id; }
  get is_new() { return hostOf(this).root()?.isNew ?? false; }
  get state() { const host = hostOf(this), name = host.store.meta.state?.field; return name ? host.root()?.fields.get(name)?.value.get() ?? null : null; }
  field(name: string) { return hostOf(this).fieldView(hostOf(this).store.root.field(name)); }
  table(name: string) { return hostOf(this).tableView(hostOf(this).store.root.table(name)); }
  call(action: string, values: Record<string, unknown> = {}) { return hostOf(this).later(page => page.call(String(action), { ...values }), false); }
  move(key: string, remark?: string) { return hostOf(this).later(page => page.move(String(key), remark === undefined ? undefined : String(remark)), false); }
  save() { return hostOf(this).later(page => page.save(), false); }
  reload() { return hostOf(this).later(page => page.reload(), undefined); }
  get api() { return hostOf(this).apiView; }
  get ui() { return hostOf(this).uiView; }
}
Object.freeze(Model.prototype);
Object.freeze(Model);

/** The one global a script reads, set before any script loads. */
export const evlet = Object.freeze({ Model });

let importer = (url: string): Promise<{ default?: unknown }> => import(/* @vite-ignore */ url);
const loaded = new Map<string, Promise<ScriptClass>>();
/** Replace how scripts are fetched (tests). */
export function setScriptImporter(load: (url: string) => Promise<{ default?: unknown }>) { importer = load; loaded.clear(); }

/** Load a model script once per address: its default export, a class extending evlet.Model. */
export function loadScript(url: string): Promise<ScriptClass> {
  if (!('evlet' in globalThis)) Object.defineProperty(globalThis, 'evlet', { value: evlet, enumerable: false });
  let found = loaded.get(url);
  if (!found) {
    found = importer(url).then(module => {
      const exported = module.default;
      if (typeof exported !== 'function' || !(exported.prototype instanceof Model)) throw new Error(`${url}: export default a class that extends evlet.Model`);
      return exported as ScriptClass;
    });
    loaded.set(url, found);
    found.catch(() => loaded.delete(url));
  }
  return found;
}

/** The script host of the record a field belongs to, if its model has a script that is running. */
export function scriptOf(field: unknown): ScriptHost | null {
  return field instanceof Field ? hosts.get(field.row.record) ?? null : null;
}

/** The look a field shows now: its fixed look=, or what its script's look method returns. */
export function lookOf(field: { meta: { look?: Look; script?: { look?: string } } }): () => Look | null {
  const method = field.meta.script?.look;
  const host = method ? scriptOf(field) : null;
  if (!host || !method) return () => field.meta.look ?? null;
  return () => {
    const answer = host.ready.get() ? host.run(method, host.hookArgs(field as Field)) as { tone?: unknown } | null : null;
    return answer && typeof answer.tone === 'string' && LOOKS.has(answer.tone) ? answer.tone as Look : field.meta.look ?? null;
  };
}

/** The text of a field's script tooltip (tooltip=Js), or null when it has none. */
export function scriptTip(field: { meta: { script?: { tooltip?: string } } }): (() => string | null) | null {
  const method = field.meta.script?.tooltip, host = method ? scriptOf(field) : null;
  if (!host || !method) return null;
  return () => {
    const answer = host.ready.get() ? host.run(method, host.hookArgs(field as Field)) as unknown : null;
    if (typeof answer === 'string') return answer || null;
    if (answer && typeof answer === 'object') {
      const { title, lines } = answer as { title?: unknown; lines?: unknown };
      return [title, ...(Array.isArray(lines) ? lines : [])].filter(item => item !== undefined && item !== null && item !== '').map(String).join('\n') || null;
    }
    return null;
  };
}

/** The input a field's script draws (control=), or null when it has none. */
export function scriptControl(field: { meta: { script?: { control?: string } } }): HTMLElement | null {
  const method = field.meta.script?.control, host = method ? scriptOf(field) : null;
  return host && method ? host.mount(method, el => [el, ...host.hookArgs(field as Field)]) : null;
}

/** One open record's script: its instance, the views it is given, and the hooks it runs. */
export class ScriptHost {
  readonly ready = cell(false);
  readonly apiView: Readonly<Record<string, (...args: any[]) => Promise<unknown>>>;
  readonly uiView: Readonly<Record<string, (...args: any[]) => unknown>>;
  private instance: Model | null = null;
  private dead = false;
  // Views made before the record last reloaded (or saved) are inert; this counts those reloads.
  private generation = 0;
  private views = new WeakMap<object, object>();
  private rows = new WeakMap<object, Row>();
  // The hooks running now, outermost first, and the hooks behind a value a hook set (an on_change it causes runs
  // after the hook returns, so the chain travels with the change).
  private stack: string[] = [];
  private causes = new WeakMap<Field, { value: unknown; chain: string[] }>();
  private warned = new Set<string>();
  private stop: (() => void) | null = null;

  constructor(readonly store: RecordStore, private readonly page: ScriptPage) {
    hosts.set(store, this);
    const api = page.api;
    this.apiView = Object.freeze({
      get: (model: string, id: string) => this.later(() => api.get(String(model), String(id)).then(response => response.data), null),
      list: (model: string, query: ListQuery = {}) => this.later(() => api.list(String(model), query), null),
      call: (endpoint: string, params: Record<string, unknown> = {}) => this.later(() => service(api, String(endpoint), params), null),
    });
    this.uiView = Object.freeze({
      toast: (text: string) => { if (!this.dead) page.toast(String(text)); },
      confirm: (text: string) => this.later(() => page.confirm(String(text)), false),
      open: (model: string, id: string) => { if (!this.dead) page.open(String(model), String(id)); },
    });
  }

  get model() { return this.store.meta.name; }
  root(): Row | null { try { return this.store.root; } catch { return null; } }

  /** Create the script's instance, run Meta.on_open once the record is ready, and watch on_change fields. */
  start(Script: ScriptClass, onOpen?: string) {
    if (this.dead) return;
    constructing = this;
    try { this.instance = new Script(); }
    catch (error) { this.report('constructor', error); return; }
    finally { constructing = null; }
    const store = this.store, wired = new WeakSet<Row>();
    this.stop = runWithOwner(store.owner, () => scope(() => {
      let opened = false, etag: unknown = untrack(() => store.etag.get());
      effect(() => {
        if (!store.ready.get()) return;
        // A reload (or the answer to a save) replaced the record: the views handed out before go inert.
        if (store.etag.get() !== etag) { etag = untrack(() => store.etag.get()); this.generation++; this.views = new WeakMap(); }
        const visit = (row: Row) => {
          if (!wired.has(row)) { wired.add(row); untrack(() => this.watchRow(row)); }
          for (const table of row.tables.values()) table.rows.get().forEach(visit);
        };
        const root = untrack(() => this.root());
        if (root) visit(root);
        if (!opened) { opened = true; this.ready.set(true); if (onOpen) untrack(() => this.run(onOpen, [])); }
      });
    }));
  }

  dispose() {
    this.dead = true;
    if (hosts.get(this.store) === this) hosts.delete(this.store);
    this.stop?.();
    this.ready.set(false);
  }

  /**
   * Run a method of the script. A missing method warns once; a hook already running for the same field (its own
   * change) is not run again, and hooks nest at most MAX_DEPTH deep. A hook that throws, or whose promise
   * rejects, is logged with the model and method.
   */
  run(method: string, args: unknown[], key = method, chain: string[] = this.stack): unknown {
    const instance = this.instance;
    if (this.dead || !instance) return undefined;
    const fn = (instance as unknown as Record<string, unknown>)[method];
    if (typeof fn !== 'function') { this.warn(`has no method ${method}`); return undefined; }
    if (chain.includes(key)) return undefined;
    if (chain.length >= MAX_DEPTH) { this.warn(`stopped ${method}: hooks nested more than ${MAX_DEPTH} deep`); return undefined; }
    const outer = this.stack;
    this.stack = [...chain, key];
    try {
      const result = fn.apply(instance, args);
      if (result && typeof (result as Promise<unknown>).then === 'function') (result as Promise<unknown>).then(undefined, error => this.report(method, error));
      return result;
    } catch (error) { this.report(method, error); return undefined; }
    finally { this.stack = outer; }
  }

  /** The (field, row) a field hook gets; row only for a field of a Table row. */
  hookArgs(field: Field): unknown[] {
    const real = field.row.field(field.name);
    return [this.fieldView(real), real.row.parent ? this.rowView(real.row) : undefined];
  }

  /** Draw a script's element (a custom block or a control) in a shadow root, so its styles stay inside. */
  mount(method: string, args: (el: HTMLElement) => unknown[]): HTMLElement {
    const host = document.createElement('div');
    host.className = 'ev-script';
    const el = document.createElement('div');
    host.attachShadow({ mode: 'open' }).append(el);
    effect(() => {
      if (!this.ready.get()) return;
      const cleanup = untrack(() => this.run(method, args(el)));
      return () => {
        if (typeof cleanup === 'function') { try { cleanup(); } catch (error) { this.report(method, error); } }
        el.replaceChildren();
      };
    });
    return host;
  }

  /** Why a script may not set this field now, or null when it may. Reactive. */
  blocked(field: Field): string | null {
    if (field.calculated) return 'it is calculated';
    if (field.derived) return 'the record fills it in';
    if (field.meta.read_only) return 'it is read only';
    const rules = field.rules.get();
    if (!rules.applies) return 'it does not apply now';
    const table = field.row.parent ? [...field.row.parent.tables.values()].find(item => item.rows.get().includes(field.row)) : null;
    if (rules.locked || table?.meta.read_only || (this.page.locked.get() && (table || (field.meta as { versioned?: boolean }).versioned !== false))) return 'it is locked';
    if (!field.editable.get()) return 'the record is loading or saving';
    return null;
  }

  fieldView(field: Field) {
    return this.cached(field, live => {
      const host = this;
      return {
        name: field.name, label: field.meta.label,
        get value() { return live() ? field.value.get() : undefined; },
        get editable() { return live() && host.blocked(field) === null; },
        set(value: unknown) {
          if (!live()) return;
          const why = untrack(() => host.blocked(field));
          if (why) throw new Error(`${host.model}.${field.name} cannot be set: ${why}`);
          if (host.stack.length) host.causes.set(field, { value, chain: host.stack });
          field.set(value);
        },
        warn(text: unknown) { if (live()) field.note.set(text === null || text === undefined || text === '' ? null : String(text)); },
      };
    });
  }

  tableView(table: Table) {
    const host = this;
    return this.cached(table, live => {
      const check = () => {
        if (table.meta.read_only || untrack(() => this.page.locked.get())) throw new Error(`${this.model}.${table.name} cannot change now: it is locked`);
      };
      return {
        name: table.name,
        get rows() { return live() ? table.rows.get().map(row => host.rowView(row)) : []; },
        add(values: Record<string, unknown> = {}) { if (!live()) return null; check(); return host.rowView(table.add({ ...values })); },
        remove(view: object) {
          if (!live()) return;
          check();
          const row = host.rows.get(view);
          if (!row || !table.rows.peek().includes(row)) throw new Error(`Use a row of ${host.model}.${table.name}`);
          table.remove(row);
        },
      };
    });
  }

  rowView(row: Row) {
    const view = this.cached(row, live => ({
      get index() { const parent = row.parent; if (!live() || !parent) return -1; for (const table of parent.tables.values()) { const at = table.rows.get().indexOf(row); if (at >= 0) return at; } return -1; },
      field: (name: string) => this.fieldView(row.field(name)),
    }));
    this.rows.set(view, row);
    return view;
  }

  /** Run a page call unless the views are gone; afterwards a dead host answers `none`. */
  later<T>(call: (page: ScriptPage) => Promise<T>, none: T): Promise<T> {
    if (this.dead) return Promise.resolve(none);
    return call(this.page).then(result => this.dead ? none : result);
  }

  private cached<T extends object>(target: object, make: (live: () => boolean) => T): T {
    let view = this.views.get(target) as T | undefined;
    if (!view) {
      const generation = this.generation;
      view = Object.freeze(make(() => !this.dead && generation === this.generation));
      this.views.set(target, view);
    }
    return view;
  }

  private watchRow(row: Row) {
    for (const field of row.fields.values()) {
      const method = field.meta.script?.on_change;
      if (!method) continue;
      runWithOwner(row.owner, () => {
        let first = true;
        effect(() => {
          const value = field.value.get(), cause = this.causes.get(field);
          this.causes.delete(field);
          if (first) { first = false; return; }
          // A change a hook made carries that hook's chain: no hook runs again for its own change.
          const chain = cause && Object.is(cause.value, value) ? cause.chain : [];
          untrack(() => { if (this.store.ready.peek()) this.run(method, this.hookArgs(field), `${method}:${row.label}.${field.name}`, chain); });
        });
      });
    }
  }

  private warn(text: string) {
    if (this.warned.has(text)) return;
    this.warned.add(text);
    console.warn(`${this.model} script ${text}`);
  }

  private report(method: string, error: unknown) { console.error(`${this.model} script: ${method} failed`, error); }
}

/** An @api endpoint by name: POST with the params as its body; one that answers 405 is a GET with them as the query. */
async function service(api: ApiClient, endpoint: string, params: Record<string, unknown>) {
  if (!/^[A-Za-z][A-Za-z0-9_.]*$/.test(endpoint)) throw new Error(`${endpoint} is not an endpoint name`);
  const path = `/api/_services/${endpoint}`;
  try { return (await api.request('POST', path, { body: params })).data; }
  catch (error) {
    if (!(error instanceof ApiError) || error.status !== 405) throw error;
    return (await api.request('GET', path, { query: Object.fromEntries(Object.entries(params).map(([key, value]) => [key, String(value)])) })).data;
  }
}
