// Fields: one BaseField (label, value cell, required, locked, applies-when, error, hint) wrapped
// by one component per model field type. A type changes only the input, parsing and display.
//
// Fields bind to the model runtime's field cells (record.ts Field) or to a local field made with
// localField() for forms that are not records (action dialogs, the Library). The model decides;
// the field explains: a lock hint says why, a calculated value shows its rule, an initial value
// says where it came from until the user edits it.

import { html, when, type Child } from '../dom';
import { cell, derive, effect, untrack, type Cell, type Readable } from '../reactive';
import type { ApiClient, RecordData } from '../api';
import { Field, sameValue, type Outcome, type RecordStore, type Rules } from '../model/record';
import type { FieldSchema, SchemaIndex } from '../model/schema';
import { Chip, Icon, IconButton, RecordMark, StatusPill, Switch } from './base';
import { read, tintFor, uid, type Prop, type Tint } from './core';
import { formula, labeller, phrase, source } from './explain';
import { checkTag, currencyOf, durationText, humanize, InputError, kindOf } from './kinds';
import { copyText, openPopover } from './layers';
import { DatePanel, DateTimePanel, Dropdown, DurationPanel, focusCalendar, TimePanel, type DateTimeValue } from './pickers';
import { formatDate, formatMoney, zoneOffset } from '../model/format';
import { parseClock, parseDay, parseDayTime, parseLength } from './dateparse';
import { tooltip } from './tooltip';
import { RichTextEditor } from '../richtext/editor';
import { parseHTML } from '../richtext/html';
import { renderView } from '../richtext/render';
import { recordImages, type ImageSource } from '../richtext/images';

// ---------- the field contract ----------

/** What a field component binds to. The record store's Field satisfies it. */
export interface FieldLike {
  readonly meta: FieldSchema;
  readonly value: Readable<unknown>;
  readonly error: Cell<string | null>;
  readonly rules: Readable<Rules>;
  readonly result?: Readable<Outcome>;
  readonly calculated: boolean;
  readonly editable: Readable<boolean>;
  set(value: unknown): void;
}

/** A field that is not part of a record: for dialogs, filters and demos. */
export function localField(meta: Partial<FieldSchema> & { name: string; type: string; label: string }, value: unknown = null,
  rules: Partial<Rules> = {}): FieldLike & { value: Cell<unknown>; rules: Cell<Rules> } {
  const full: FieldSchema = { required: false, read_only: false, ...meta };
  const valueCell = cell<unknown>(value, { name: `${meta.name}` });
  const rulesCell = cell<Rules>({ applies: true, required: full.required, locked: false, ...rules }, { name: `${meta.name} (rules)` });
  const calculated = full.compute !== null && full.compute !== undefined;
  return {
    meta: full, value: valueCell, rules: rulesCell, error: cell<string | null>(null), calculated,
    result: cell<Outcome>({ state: 'ready' }),
    editable: derive(() => !calculated && !full.read_only && rulesCell.get().applies && !rulesCell.get().locked),
    set(next: unknown) { valueCell.set(next); },
  };
}

// Whether the user has edited a field in this page; the initial-value hint shows until then.
const edits = new WeakMap<FieldLike, Cell<boolean>>();
export function edited(field: FieldLike): Cell<boolean> {
  let flag = edits.get(field);
  if (!flag) edits.set(field, flag = cell(Boolean((field as { touched?: boolean }).touched), { name: `${field.meta.name} (edited)` }));
  return flag;
}

/** The record store a field belongs to, or null for a local field. */
export function recordOf(field: FieldLike): RecordStore | null {
  return field instanceof Field ? field.row.record : null;
}

/** The fields of the model a field belongs to (just the field itself for a local field). */
export function siblingsOf(field: FieldLike): FieldSchema[] {
  return field instanceof Field ? field.row.model.fields : [field.meta];
}

// Labels of the other fields of the field's model, for sentences such as "Locked when Stage is won".
function labelsOf(field: FieldLike) {
  return labeller(siblingsOf(field));
}

const isEmpty = (value: unknown) => value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length);

// ---------- links ----------

export interface LinkOption { id: string; title: string; subtitle?: string }
/** Where Link fields find records and their titles. */
export interface LinkSource {
  search(meta: FieldSchema, query: string): Promise<LinkOption[]>;
  /** The title of a record, as a cell that fills in once it is read. */
  title(model: string, id: string): Readable<string | null>;
  remember?(model: string, id: string, title: string): void;
  /** A record's readable fields, as a cell that fills in once it is read (null until then, or when it can't be read). */
  record?(model: string, id: string): Readable<RecordData | null>;
  /** Forget everything read so far (on sign-out). */
  clear?(): void;
}

/** What apiLinks may use beyond the list route: a record read already in flight, or its last detail. */
type LinkApi = ApiClient & { reading?(model: string, id: string): Promise<{ data: RecordData }> | undefined; detail?(model: string, id: string): Readable<RecordData | null> };

/**
 * A LinkSource over the API: list search with ?q=, and titles read in batches: the titles a page asks
 * for in one turn are read with one list request per target model (`id__in`), unless the record is
 * already being read or was read. record() reads the same way and keeps the whole record (a Currency
 * field reads its linked currency from it).
 * Titles are kept for the signed-in session and forgotten when it ends.
 */
export function apiLinks(api: LinkApi, schema: SchemaIndex): LinkSource {
  let titles = new Map<string, Cell<string | null>>();
  let wanted = new Map<string, Set<string>>();
  let records = new Map<string, Cell<RecordData | null>>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  const clear = () => { titles = new Map(); wanted = new Map(); records = new Map(); };
  if (typeof window !== 'undefined') window.addEventListener('evlet-session-ended', clear);
  const titleOf = (model: string, data: Record<string, unknown>) => {
    const field = schema.model(model).title_field;
    return String(data[field] ?? `#${data.id}`);
  };
  const remember = (model: string, id: string, title: string) => {
    const key = `${model}:${id}`;
    if (titles.has(key)) titles.get(key)!.set(title); else titles.set(key, cell<string | null>(title));
  };
  const keep = (model: string, id: string, data: RecordData | null) => {
    records.get(`${model}:${id}`)?.set(data);
    remember(model, id, data ? titleOf(model, data) : `#${id}`);
  };
  const want = (model: string, id: string) => {
    if (!wanted.has(model)) wanted.set(model, new Set());
    wanted.get(model)!.add(id);
    // Everything asked for in this turn goes in one request per model.
    timer ??= setTimeout(flush, 0);
  };
  const flush = () => {
    timer = null;
    const batch = wanted;
    wanted = new Map();
    for (const [model, ids] of batch) {
      const rest: string[] = [];
      for (const id of ids) {
        const known = api.detail?.(model, id).peek(), reading = api.reading?.(model, id);
        if (known) keep(model, id, known);
        else if (reading) reading.then(response => keep(model, id, response.data), () => keep(model, id, null));
        else rest.push(id);
      }
      rest.sort();
      for (let start = 0; start < rest.length; start += 100) {
        const part = rest.slice(start, start + 100);
        // Records this user cannot read are left out of the answer and show their number.
        api.list(model, { filters: { id__in: part }, limit: part.length }).then(page => {
          const found = new Map(page.items.map(item => [item.id as string, item]));
          for (const id of part) keep(model, id, found.get(id) ?? null);
        }, () => { for (const id of part) keep(model, id, null); });
      }
    }
  };
  return {
    async search(meta, query) {
      const page = await api.list(meta.target!, { q: query || undefined, limit: 8 });
      return page.items.map(item => ({ id: item.id as string, title: titleOf(meta.target!, item) }));
    },
    title(model, id) {
      const key = `${model}:${id}`;
      let title = titles.get(key);
      if (!title) {
        const known = api.detail?.(model, id).peek();
        titles.set(key, title = cell<string | null>(known ? titleOf(model, known) : null));
        if (!known) want(model, id);
      }
      return title;
    },
    remember,
    clear,
    record(model, id) {
      const key = `${model}:${id}`;
      let record = records.get(key);
      if (!record) {
        const known = api.detail?.(model, id).peek() ?? null;
        records.set(key, record = cell<RecordData | null>(known));
        // Read through the list route, so the person's row and field permissions apply.
        if (!known) want(model, id);
      }
      return record;
    },
  };
}

let defaultLinks: LinkSource | null = null;
/** Set the LinkSource fields use when none is passed (the app sets it once after sign-in). */
export function setLinkSource(links: LinkSource | null) { defaultLinks = links; }

// ---------- states ----------

const STATE_TINTS: Record<string, Tint> = {
  draft: 'slate', new: 'blue', open: 'blue', issued: 'blue', submitted: 'blue', qualification: 'slate', discovery: 'blue',
  proposal: 'violet', negotiation: 'amber', contacted: 'amber', pending: 'amber', qualified: 'green', paid: 'green', won: 'green',
  done: 'green', closed: 'green', approved: 'green', converted: 'violet', lost: 'rose', void: 'rose', cancelled: 'rose',
  rejected: 'rose', unqualified: 'slate',
};
/** The tint of a state: known names get the design's colour, others a stable one. */
export const stateTint = (state: string): Tint => STATE_TINTS[String(state).toLowerCase()] ?? tintFor(state);

// ---------- currency ----------

const CODE = /^[A-Z]{3}$/;

/**
 * The currency code a Currency field shows. The schema sends a fixed code (a code on the field or
 * an app setting, read on the server) or a source on the record: a field of the record, or a
 * field of the record one of its Links points at. Follows the source as it changes; AED when it
 * is empty or can't be read.
 */
export function currencyFor(field: FieldLike, links: LinkSource | null = defaultLinks): () => string {
  const meta = field.meta;
  if (!meta.currency_source || !(field instanceof Field)) return () => currencyOf(meta);
  const row = field.row;
  return () => currencyIn(meta, name => row.fields.get(name)?.value.get(), links);
}

/** The currency code of a Currency field for one record's values (a list row, say): see currencyFor. */
export function currencyIn(meta: FieldSchema, value: (name: string) => unknown, links: LinkSource | null = defaultLinks): string {
  const source = meta.currency_source, fallback = currencyOf(meta);
  const code = (found: unknown) => typeof found === 'string' && CODE.test(found) ? found : fallback;
  if (!source) return fallback;
  if (!source.link) return code(value(source.field));
  const id = value(source.link);
  if (id === null || id === undefined || id === '' || !source.model || !links?.record) return fallback;
  return code(links.record(source.model, String(id)).get()?.[source.field]);
}

// ---------- display at rest ----------

/** A field's value as it reads at rest: pills for states, chips for tags, record titles for links. */
export function displayValue(field: FieldLike, options: { links?: LinkSource | null; empty?: string;
  /** Rich text links read as text (inside a button that starts editing). */
  plainLinks?: boolean } = {}): Node {
  const meta = field.meta, kind = kindOf(meta.type);
  const blank = () => html`<span class="muted">${options.empty ?? 'Not set'}</span>`;
  const value = () => field.value.get();
  const links = options.links ?? defaultLinks;
  switch (meta.type) {
    case 'state':
      return when(() => !isEmpty(value()), () => StatusPill({ label: () => humanize(String(value())), tint: () => stateTint(String(value())) }), blank);
    case 'tags':
      return when(() => !isEmpty(value()), () => html`<span class="tag-row">${() => ((value() as string[]) ?? []).map(tag => Chip({ label: tag }))}</span>`, blank);
    case 'richtext':
      return when(() => !isEmpty(value()), () => html`<div class="rt-holder" ref=${(holder: HTMLElement) =>
        effect(() => { holder.replaceChildren(RichTextView(String(value()), { links: !options.plainLinks, images: imagesFor(field) })); })}></div>`, blank);
    case 'sequence':
      return when(() => !isEmpty(value()), () => html`<span class="tx mono">${() => String(value())}</span>`, () => html`<span class="muted">${meta.placeholder || 'Not assigned yet'}</span>`);
    case 'link':
      return when(() => !isEmpty(value()), () => {
        const title = derive(() => {
          const id = String(value());
          return links && meta.target ? links.title(meta.target, id).get() ?? '' : `#${id}`;
        });
        return html`<span class="cell-flex">${RecordMark({ name: () => title.get() || '?' })}<span class="tx">${() => title.get() || 'Loading'}</span></span>`;
      }, blank);
  }
  if (meta.type === 'currency' && meta.currency_source) {
    const currency = currencyFor(field, links);
    return when(() => !isEmpty(value()), () => html`<span class="tx num">${() => formatMoney(value() as string, { currency: currency(), scale: typeof meta.scale === 'number' ? meta.scale : 2 })}</span>`, blank);
  }
  const numeric = kind.align === 'right';
  // A unit declared with suffix= reads after the number at rest too: 5.00%, 8 hours.
  const unit = (meta.type === 'integer' || meta.type === 'decimal') && meta.suffix ? (meta.suffix === '%' ? '%' : ` ${meta.suffix}`) : '';
  return when(() => !isEmpty(value()), () => html`<span class=${numeric ? 'tx num' : 'tx'}>${() => kind.format(value(), meta) + unit}</span>`, blank);
}

// ---------- hints ----------

/** The hints beside a label: calculated (with its rule), initial source, locked (with why). */
export function FieldHints(field: FieldLike): Node[] {
  const meta = field.meta, label = labelsOf(field);
  const hints: Node[] = [];
  if (field.calculated) {
    const hint = html`<span class="ff-hint calc" tabindex="0">ƒ calculated</span>` as HTMLElement;
    const rule = `= ${formula(meta.compute)}`;
    tooltip(hint, () => {
      const result = field.result?.get();
      return result && result.state !== 'ready' ? `${rule}. ${result.code === 'COMPUTE_INPUT_MISSING' ? 'Fills in when its inputs are set.' : 'Can’t be calculated yet.'}` : rule;
    });
    hints.push(hint);
    return hints;
  }
  if (meta.initial) {
    const from = source(meta.initial, label)?.toLowerCase() ?? 'a rule';
    hints.push(when(() => !edited(field).get() && field.editable.get(), () => {
      const hint = html`<span class="ff-hint init">from ${from}</span>` as HTMLElement;
      tooltip(hint, `Filled from ${from} until you change it`);
      return hint;
    }));
  }
  hints.push(when(() => field.rules.get().locked || meta.read_only, () => {
    const hint = html`<span class="ff-hint" tabindex="0">${Icon('lock', { size: 'xs' })}${meta.read_only ? 'read only' : 'locked'}</span>` as HTMLElement;
    tooltip(hint, meta.locked_when && !meta.read_only ? `Locked when ${phrase(meta.locked_when, label)}` : meta.read_only ? 'This value can’t be changed here' : 'Locked');
    return hint;
  }));
  return hints;
}

// ---------- controls ----------

export interface ControlOptions {
  id?: string;
  /** ids of the error and hint, for aria-describedby. */
  describedBy?: string;
  /** Text inputs commit on change (blur or Enter) by default; 'manual' leaves it to the caller. */
  commitOn?: 'change' | 'manual';
  /** Called after a value was written. */
  onCommit?: () => void;
  /** Called after a value was picked and the picker closed (a day, or Done), so the caller can save it. */
  onPicked?: () => void;
  links?: LinkSource | null;
  label?: string;
}

export interface Control {
  element: HTMLElement;
  /** Parse the typed text and write it. False when it is not valid (the field shows why). */
  commit(): boolean;
  focus(): void;
  /** Start editing with typed text (a Link control opens its search with it). */
  type?(text: string): void;
}

// Two date-times that name the same moment (the server keeps microseconds, typed text has minutes).
function sameInstant(meta: FieldSchema, a: unknown, b: unknown) {
  if (meta.type !== 'datetime' || typeof a !== 'string' || typeof b !== 'string') return false;
  const x = Date.parse(a), y = Date.parse(b);
  return !Number.isNaN(x) && x === y;
}

/** Write a value to a field: checks required, records the edit and reports a refused write. */
export function writeValue(field: FieldLike, value: unknown, label = field.meta.label): boolean {
  const required = untrack(() => field.rules.get().required);
  if (required && isEmpty(value)) { field.error.set(`${label} is required`); return false; }
  const current = untrack(() => field.value.get());
  if (sameValue(field.meta, current, value) || JSON.stringify(current ?? null) === JSON.stringify(value ?? null) || sameInstant(field.meta, current, value)) {
    field.error.set(null);
    return true;
  }
  try {
    field.set(value);
    field.error.set(null);
    edited(field).set(true);
    return true;
  } catch (error) {
    field.error.set(error instanceof Error ? error.message : String(error));
    return false;
  }
}

/** The input for a field, without label or error: used by BaseField, InlineValue and table cells. */
export function FieldControl(field: FieldLike, options: ControlOptions = {}): Control {
  const meta = field.meta, kind = kindOf(meta.type), id = options.id ?? uid('f');
  const disabled = () => !field.editable.get();
  const invalid = () => field.error.get() ? 'true' : null;
  const required = () => field.rules.get().required ? 'true' : null;
  const done = () => options.onCommit?.();

  if (field.calculated || kind.control === 'readonly') {
    const element = html`<div class="fval ro" id=${id} role="textbox" aria-readonly="true" aria-label=${options.label ?? meta.label}
      aria-describedby=${options.describedBy ?? null} tabindex="-1">${displayValue(field, { links: options.links, empty: field.calculated ? 'Not calculated yet' : undefined })}${meta.type === 'state'
        ? html`<span class="muted" style="font-size:12px;margin-left:6px">changes through moves</span>` : null}</div>` as HTMLElement;
    return { element, commit: () => true, focus: () => element.focus() };
  }

  if (kind.control === 'switch') {
    const element = Switch({ on: () => field.value.get() === true, id, describedBy: options.describedBy, disabled, ariaLabel: options.label ?? meta.label,
      label: () => field.value.get() === true ? 'Yes' : 'No',
      onChange: next => { if (writeValue(field, next)) done(); } });
    return { element, commit: () => true, focus: () => element.focus() };
  }

  if (kind.control === 'select') return SelectControl(field, id, options);
  if (kind.control === 'link') return LinkControl(field, id, options);
  if (kind.control === 'tags') return TagsControl(field, id, options);
  if (kind.control === 'multiselect') return MultiSelectControl(field, id, options);
  if (kind.control === 'richtext') return RichTextControl(field, id, options);

  // Text-like inputs: text, numbers, money, dates, times, durations, email, url, phone, long text.
  const textarea = kind.control === 'textarea';
  const shown = () => (kind.edit ?? kind.format)(field.value.get(), meta);
  const commit = (): boolean => {
    // Text the user did not change is not a change, even when the stored value is more precise
    // than its display (date-times with seconds) or the server normalized it (+971 numbers).
    if (input.value === untrack(shown)) {
      if (untrack(() => field.rules.get().required) && isEmpty(untrack(() => field.value.get()))) { field.error.set(`${field.meta.label} is required`); return false; }
      field.error.set(null);
      return true;
    }
    let value: unknown;
    try { value = kind.parse ? kind.parse(input.value, meta) : input.value; }
    catch (error) {
      if (!(error instanceof InputError)) throw error;
      if (kind.picker) return unreadable();
      field.error.set(error.message);
      return false;
    }
    const ok = writeValue(field, value);
    // Show the value as the field formats it, even when it did not change (2.5 -> 2.50).
    if (ok) input.value = untrack(shown);
    if (ok) done();
    return ok;
  };
  // A date, time or duration that can't be read keeps the field's value and shows it again, and
  // says what was typed; the message goes with the next edit.
  const unreadable = (): boolean => {
    const typed = input.value.trim();
    input.value = untrack(shown);
    field.error.set(`Couldn't read ${typed} as ${PICKED[kind.picker!]}`);
    return false;
  };
  const onChange = () => { if (options.commitOn !== 'manual') commit(); };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Enter' && !textarea && options.commitOn !== 'manual') { event.preventDefault(); commit(); }
  };
  const input = (textarea
    ? html`<textarea class="input" id=${id} rows="3" prop:value=${shown} disabled=${disabled} aria-invalid=${invalid} aria-required=${required}
        aria-describedby=${options.describedBy ?? null} aria-label=${options.label ?? null} placeholder=${meta.placeholder || null}
        on:change=${onChange} on:input=${(event: Event) => grow(event.target as HTMLTextAreaElement)}></textarea>`
    : html`<input class=${`input${kind.align === 'right' ? ' num r' : ''}`} id=${id} prop:value=${shown} disabled=${disabled}
        inputmode=${kind.inputMode ?? null} placeholder=${meta.placeholder || kind.placeholder || null} autocomplete="off"
        aria-invalid=${invalid} aria-required=${required} aria-describedby=${options.describedBy ?? null} aria-label=${options.label ?? null}
        maxlength=${meta.max_length ?? null} on:change=${onChange} on:keydown=${onKey}
        on:input=${() => { if (kind.picker && field.error.peek()) field.error.set(null); }}>`) as HTMLInputElement;
  // A Currency field's side box follows its currency source (a field of the record, say).
  const prefix: Child = meta.type === 'currency' ? currencyFor(field, options.links) : kind.prefix?.(meta), suffix = kind.suffix?.(meta);
  let element: HTMLElement = input;
  const picker = kind.picker && !textarea ? PickerButton(field, input, kind.picker, commit, () => options.onPicked?.()) : null;
  if (prefix || suffix || meta.type === 'email' || picker) {
    const copy = meta.type === 'email' ? when(() => !isEmpty(field.value.get()), () => IconButton({ icon: 'copy', tip: 'Copy email', size: 'small',
      onClick: () => copyText(String(field.value.peek()), String(field.value.peek())) })) : null;
    element = html`<div class="in-affix" class:in-pick=${Boolean(picker)}>${prefix ? html`<span class="in-box">${prefix}</span>` : null}${input}${suffix ? html`<span class="in-box">${suffix}</span>` : null}${copy}${picker}</div>` as HTMLElement;
    picker?.attach(element);
  }
  return { element, commit, focus: () => { input.focus(); input.select?.(); } };
}

// ---------- pickers ----------

/** What each picker kind reads, for "Couldn't read 1.62 as a duration". */
const PICKED = { date: 'a date', datetime: 'a date and time', time: 'a time', duration: 'a duration' };

const pad = (number: number) => String(number).padStart(2, '0');

// Now in the person's zone, as a day and a time of day.
function wallNow(): DateTimeValue {
  const now = Date.now(), local = new Date(now + zoneOffset(now) * 60000);
  return { day: `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())}`, hours: local.getUTCHours(), minutes: local.getUTCMinutes() };
}

// What is typed in the input, read as the picker should open on it (dateparse.ts), or null.
const typedDateTime = (text: string): DateTimeValue | null => parseDayTime(text);
const typedTime = (text: string) => parseClock(text);
const typedSeconds = (text: string) => parseLength(text);

/**
 * The button at the end of a date, date-time, time or duration input that opens its picker.
 * Clicking the input opens it too; Alt+Down or Down opens it and moves into it. A picked value
 * is written as typed text and committed, so it goes through the same parsing as typing.
 */
function PickerButton(field: FieldLike, input: HTMLInputElement, picker: 'date' | 'datetime' | 'time' | 'duration', commit: () => boolean, picked: () => void) {
  const meta = field.meta;
  let anchor: HTMLElement = input, close: (() => void) | null = null, panel: HTMLElement | null = null;
  const clearable = () => !untrack(() => field.rules.get().required);
  const put = (text: string) => { input.value = text; return commit(); };
  const finish = () => { close?.(); input.focus(); if (commit()) picked(); };
  const render = (done: () => void): Node => {
    if (picker === 'date') return DatePanel({ label: `Choose ${meta.label.toLowerCase()}`, clearable: clearable(),
      value: () => parseDay(input.value), onPick: day => { done(); input.focus(); if (put(day ? formatDate(day) : '')) picked(); } });
    if (picker === 'datetime') return DateTimePanel({ label: `Choose ${meta.label.toLowerCase()}`, clearable: clearable(), now: wallNow, onDone: finish,
      value: () => typedDateTime(input.value), onChange: value => put(value ? `${formatDate(value.day)} ${pad(value.hours)}:${pad(value.minutes)}` : '') });
    if (picker === 'time') return TimePanel({ clearable: clearable(), now: wallNow, onDone: finish, value: () => typedTime(input.value),
      onChange: value => put(value ? `${pad(value.hours)}:${pad(value.minutes)}` : '') });
    return DurationPanel({ clearable: clearable(), onDone: finish, value: () => typedSeconds(input.value),
      onChange: seconds => put(seconds === null ? '' : durationText(seconds)) });
  };
  const open = (move = false) => {
    if (close || !field.editable.peek()) { if (move && panel) moveIn(); return; }
    close = openPopover(anchor, done => { panel = render(done) as HTMLElement; return panel; },
      { role: 'dialog', label: `Choose ${meta.label}`, className: 'pick-pop', align: 'left', onClose: () => { close = null; panel = null; } });
    if (move) queueMicrotask(moveIn);
  };
  const moveIn = () => {
    if (!panel) return;
    if (panel.querySelector('.cal-day')) focusCalendar(panel); else panel.querySelector<HTMLElement>('[role="spinbutton"],button')?.focus();
  };
  const button = html`<button type="button" class="in-pick-btn" tabindex="-1" aria-label=${`Choose ${meta.label.toLowerCase()}`}
    disabled=${() => !field.editable.get()} on:click=${() => { if (close) close(); else { open(); input.focus(); } }}>${Icon(picker === 'date' || picker === 'datetime' ? 'calendar' : 'clock', { size: 'sm' })}</button>` as HTMLButtonElement & { attach(wrapper: HTMLElement): void };
  input.setAttribute('aria-haspopup', 'dialog');
  input.setAttribute('aria-expanded', 'false');
  input.addEventListener('click', () => open());
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' && (event.altKey || picker !== 'duration')) { event.preventDefault(); open(true); }
  });
  // Leaving the input for somewhere other than its picker closes the picker.
  input.addEventListener('focusout', event => {
    const next = event.relatedTarget as Node | null;
    if (close && !(next && (panel?.contains(next) || anchor.contains(next)))) close();
  });
  button.attach = wrapper => {
    // The whole field is the anchor, so the panel lines up with it and a click inside it is not "outside".
    anchor = wrapper;
    wrapper.tabIndex = -1;
    wrapper.addEventListener('focus', () => input.focus());
    const sync = new MutationObserver(() => input.setAttribute('aria-expanded', wrapper.getAttribute('aria-expanded') ?? 'false'));
    sync.observe(wrapper, { attributes: true, attributeFilter: ['aria-expanded'] });
  };
  return button;
}

function grow(area: HTMLTextAreaElement) {
  area.style.height = 'auto';
  area.style.height = `${area.scrollHeight}px`;
}

// A Select: a button showing the choice; a popover lists the choices with the current one ticked,
// a search box when there are many, and Clear when the field is not required.
function SelectControl(field: FieldLike, id: string, options: ControlOptions): Control {
  const meta = field.meta, choices = meta.choices ?? [], retired = meta.retired_choices ?? [];
  const current = () => { const value = field.value.get(); return isEmpty(value) ? null : String(value); };
  const button = Dropdown({ id, label: meta.label, ariaLabel: options.label, placeholder: meta.placeholder || undefined, describedBy: options.describedBy,
    value: current, disabled: () => !field.editable.get(), invalid: () => Boolean(field.error.get()), required: () => field.rules.get().required,
    clearable: () => !field.rules.peek().required,
    // Retired choices are listed only for the record that still holds one, and can't be picked again.
    choices: () => [...choices.map(value => ({ value, label: humanize(value) })),
      ...retired.filter(value => value === current()).map(value => ({ value, label: `${humanize(value)} (retired)`, disabled: true }))],
    onChange: value => { if (writeValue(field, value)) options.onCommit?.(); } });
  return { element: button, commit: () => true, focus: () => button.focus(), type: text => button.open(text) };
}

// A Link: the chosen record's mark and title; a popover searches records of the target model.
function LinkControl(field: FieldLike, id: string, options: ControlOptions): Control {
  const meta = field.meta, links = options.links ?? defaultLinks;
  const button = html`<button type="button" class="in-link input" id=${id} disabled=${() => !field.editable.get()} aria-haspopup="listbox" aria-expanded="false"
    aria-invalid=${() => field.error.get() ? 'true' : null} aria-describedby=${options.describedBy ?? null}
    aria-label=${options.label ?? null}>${displayValue(field, { links, empty: `Choose ${meta.label.toLowerCase()}` })}${Icon('chevron-down', { size: 'sm' })}</button>` as HTMLButtonElement;
  const choose = (option: LinkOption | null) => {
    if (option && meta.target) links?.remember?.(meta.target, option.id, option.title);
    if (writeValue(field, option ? option.id : null)) options.onCommit?.();
  };
  const open = (typed = '') => openPopover(button, close => LinkSearch(field, links, option => { close(); choose(option); }, typed),
    { role: 'dialog', label: `Choose ${meta.label}`, className: 'link-pop', align: 'left' });
  button.addEventListener('click', () => open());
  button.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { event.preventDefault(); open(); }
    // Typing a letter or digit starts the search with it.
    else if (event.key.length === 1 && event.key !== ' ' && !event.metaKey && !event.ctrlKey && !event.altKey && field.editable.peek()) {
      event.preventDefault(); event.stopPropagation(); open(event.key);
    }
  });
  return { element: button, commit: () => true, focus: () => button.focus(), type: text => { if (field.editable.peek()) open(text); } };
}

function LinkSearch(field: FieldLike, links: LinkSource | null, choose: (option: LinkOption | null) => void, typed = ''): Node {
  const meta = field.meta, query = cell(typed), results = cell<LinkOption[]>([]), active = cell(0), state = cell<'loading' | 'ready' | 'error'>('loading');
  const listId = uid('lb');
  let asked = 0;
  const search = async (text: string) => {
    const mine = ++asked;
    state.set('loading');
    try {
      const found = links ? await links.search(meta, text) : [];
      if (mine !== asked) return;
      results.set(found); active.set(0); state.set('ready');
    } catch { if (mine === asked) state.set('error'); }
  };
  const optionId = (index: number) => `${listId}-${index}`;
  const input = html`<input class="input" role="combobox" aria-expanded="true" aria-controls=${listId} aria-autocomplete="list"
    aria-activedescendant=${() => results.get().length ? optionId(active.get()) : null} placeholder=${`Search ${meta.label.toLowerCase()}`}
    aria-label=${`Search ${meta.label}`} autocomplete="off"
    on:input=${(event: Event) => { query.set((event.target as HTMLInputElement).value); search(query.peek()); }}
    on:keydown=${(event: KeyboardEvent) => {
      const count = results.peek().length;
      if (event.key === 'ArrowDown' && count) { event.preventDefault(); active.set((active.peek() + 1) % count); }
      else if (event.key === 'ArrowUp' && count) { event.preventDefault(); active.set((active.peek() - 1 + count) % count); }
      else if (event.key === 'Enter') { event.preventDefault(); const option = results.peek()[active.peek()]; if (option) choose(option); }
    }}>` as HTMLInputElement;
  const list = html`<div class="link-list" role="listbox" id=${listId} aria-label=${meta.label}>${() => results.get().map((option, index) => html`<button type="button" tabindex="-1"
    class="mi" class:on=${() => active.get() === index} role="option" id=${optionId(index)} aria-selected=${() => String(active.get() === index)}
    on:click=${() => choose(option)} on:mouseenter=${() => active.set(index)}>${RecordMark({ name: option.title })}<span>${option.title}</span>${option.subtitle ? html`<span class="muted">${option.subtitle}</span>` : null}</button>`)}</div>`;
  const status = () => state.get() === 'loading' ? 'Searching' : state.get() === 'error' ? 'Could not search. Try again.' : results.get().length ? '' : 'No matches. Try another name.';
  input.value = typed;
  search(typed);
  queueMicrotask(() => { input.focus(); input.setSelectionRange?.(typed.length, typed.length); });
  return html`<div class="link-search">${input}<div class="muted link-status" role="status">${status}</div>${list}${!field.rules.peek().required
    ? html`<hr><button type="button" class="mi" on:click=${() => choose(null)}>${Icon('x', { size: 'sm' })}<span>Clear</span></button>` : null}</div>`;
}

// Tags: chips with remove buttons; type and press Enter (or comma) to add, Backspace removes the last.
function TagsControl(field: FieldLike, id: string, options: ControlOptions): Control {
  const tags = () => (field.value.get() as string[] | null) ?? [];
  const write = (next: string[]) => { if (writeValue(field, next.length ? next : null)) options.onCommit?.(); };
  const add = (raw: string): boolean => {
    if (!raw.trim()) return true;
    let tag: string;
    try { tag = checkTag(raw); } catch (error) { field.error.set((error as Error).message); return false; }
    const list = untrack(tags);
    if (list.some(item => item.toLowerCase() === tag.toLowerCase())) { field.error.set(`${tag} is already there`); return false; }
    if (list.length >= 20) { field.error.set('Use at most 20 tags'); return false; }
    write([...list, tag]);
    return true;
  };
  const input = html`<input id=${id} placeholder="Add tag" autocomplete="off" disabled=${() => !field.editable.get()}
    aria-describedby=${options.describedBy ?? null} aria-label=${options.label ?? null}
    on:keydown=${(event: KeyboardEvent) => {
      const target = event.target as HTMLInputElement;
      if ((event.key === 'Enter' || event.key === ',') && target.value.trim()) { event.preventDefault(); if (add(target.value)) target.value = ''; }
      else if (event.key === 'Backspace' && !target.value && untrack(tags).length) write(untrack(tags).slice(0, -1));
    }}>` as HTMLInputElement;
  const element = html`<div class="in-tags" aria-invalid=${() => field.error.get() ? 'true' : null}>${() => tags().map(tag => html`<span class="chip">${tag}<button type="button"
    class="chip-x" aria-label=${`Remove ${tag}`} disabled=${() => !field.editable.get()} on:click=${() => write(untrack(tags).filter(item => item !== tag))}>${Icon('x', { size: 'xs' })}</button></span>`)}${input}</div>` as HTMLElement;
  return { element, commit: () => { const ok = add(input.value); if (ok) input.value = ''; return ok; }, focus: () => input.focus() };
}

// Rich text: Evlet's own editor (richtext/editor.ts). It writes on every change, or on commit() when
// the caller commits by hand (InlineValue), so Esc can still cancel.
/** The image source of a rich text field on a record (its own fields, not a child row's): the record's
 * private attachments, or the person's holding area until it is saved. */
export function imagesFor(field: FieldLike): ImageSource | undefined {
  const record = recordOf(field);
  if (!record || !(field instanceof Field) || field.row.parent !== null) return undefined;
  return recordImages(record.api, record.meta.name, () => record.id);
}

function RichTextControl(field: FieldLike, id: string, options: ControlOptions): Control {
  const manual = options.commitOn === 'manual';
  let draft: { value: string | null } | null = null;
  const editor = RichTextEditor({
    id, value: () => field.value.get() as string | null, label: options.label, labelledBy: options.label ? undefined : `${id}-label`,
    describedBy: options.describedBy, placeholder: field.meta.placeholder, maxLength: field.meta.max_length,
    disabled: () => !field.editable.get(), invalid: () => Boolean(field.error.get()), required: () => field.rules.get().required,
    images: imagesFor(field),
    onChange: value => {
      if (manual) { draft = { value }; return; }
      if (writeValue(field, value)) options.onCommit?.();
    },
  });
  const commit = () => {
    if (draft) { const { value } = draft; draft = null; return writeValue(field, value); }
    if (untrack(() => field.rules.get().required) && isEmpty(untrack(() => field.value.get()))) { field.error.set(`${field.meta.label} is required`); return false; }
    return true;
  };
  return { element: editor.element, commit, focus: () => editor.focus() };
}

/** Stored rich text as formatted, read-only DOM. Links stay links; nothing is parsed as markup. */
export function RichTextView(value: string, options: { links?: boolean; images?: ImageSource;
  /** Show mentions (the activity journal), each with the person's current name where known. */
  mentions?: (id: string) => string | undefined } = {}): HTMLElement {
  const view = html`<div class="rt-view"></div>` as HTMLElement;
  view.append(renderView(parseHTML(value, { mentions: Boolean(options.mentions) }),
    { links: options.links !== false, imageUrl: options.images?.url, mentionName: options.mentions }));
  return view;
}

// MultiSelect: one chip per choice; pressing a chip adds or removes it. Saved in the order of the choices.
function MultiSelectControl(field: FieldLike, id: string, options: ControlOptions): Control {
  const chosen = () => (field.value.get() as string[] | null) ?? [];
  const toggle = (choice: string) => {
    const now = untrack(chosen), next = (field.meta.choices ?? []).filter(item => item === choice ? !now.includes(item) : now.includes(item));
    if (writeValue(field, next.length ? next : null)) options.onCommit?.();
  };
  const element = html`<div class="chips in-multi" id=${id} role="group" aria-label=${options.label ?? field.meta.label} aria-describedby=${options.describedBy ?? null}>${(field.meta.choices ?? []).map(choice =>
    html`<button type="button" class="chip" class:on=${() => chosen().includes(choice)} aria-pressed=${() => String(chosen().includes(choice))}
      disabled=${() => !field.editable.get()} on:click=${() => toggle(choice)}>${() => chosen().includes(choice) ? Icon('check', { size: 'xs' }) : null}${humanize(choice)}</button>`)}</div>` as HTMLElement;
  return { element, commit: () => true, focus: () => element.querySelector<HTMLButtonElement>('button')?.focus() };
}

// ---------- BaseField and the field types ----------

export interface FieldProps {
  field: FieldLike;
  /** Overrides the model label. */
  label?: Prop<string>;
  /** Extra note beside the label. */
  hint?: Child;
  /** Span both columns of a form. */
  full?: boolean;
  links?: LinkSource | null;
  onCommit?: () => void;
}

/**
 * The reactive core every field type wraps: label, value, required, locked and applies rules,
 * error and hint. Hidden while its applies_when rule is false. help_text reads under the input
 * (an error takes its place) and tooltip behind a ? icon at the input's right end.
 */
export function BaseField(props: FieldProps): HTMLElement {
  const { field } = props, meta = field.meta, id = uid('f'), errorId = `${id}-err`, helpId = `${id}-help`;
  const label = props.label ?? meta.label;
  const describedBy = meta.help_text ? `${errorId} ${helpId}` : errorId;
  const control = FieldControl(field, { id, describedBy, links: props.links, onCommit: props.onCommit });
  const star = html`<span class="req" aria-hidden="true">*</span>` as HTMLElement;
  if (meta.required_when) tooltip(star, `Required when ${phrase(meta.required_when, labelsOf(field))}`);
  const labelled = ['switch', 'link', 'select'].includes(kindOf(meta.type).control ?? '') || control.element.tagName !== 'DIV' || control.element.classList.contains('in-affix') || control.element.classList.contains('in-tags');
  return html`<div class="fld" class:full=${Boolean(props.full) || meta.type === 'longtext' || meta.type === 'richtext'} class:invalid=${() => Boolean(field.error.get())}
    hidden=${() => !field.rules.get().applies} data-field=${meta.name}>
    <label for=${labelled ? id : null} id=${`${id}-label`}>${label}${when(() => field.rules.get().required && !field.calculated, () => star)}${FieldHints(field)}${props.hint ?? null}</label>
    ${meta.tooltip ? withTip(control.element, meta.tooltip, meta.label) : control.element}<span class="err" id=${errorId} role="alert">${() => field.error.get() ?? ''}</span>${meta.help_text
      ? html`<span class="fld-help" id=${helpId} hidden=${() => Boolean(field.error.get())}>${meta.help_text}</span>` : null}</div>` as HTMLElement;
}

// The ? icon at the right end of a control: inside an input's box, before a select's or link's
// chevron, before a unit box. Hover or focus shows the text.
function withTip(control: HTMLElement, text: string, label: string): HTMLElement {
  const tip = html`<button type="button" class="fld-tip" aria-label=${`About ${label.toLowerCase()}`}>${Icon('circle-help', { size: 'sm' })}</button>` as HTMLElement;
  tooltip(tip, text);
  // A unit box or picker: the icon is one more part of the box, right after the input.
  if (control.classList.contains('in-affix')) { control.querySelector('input')!.after(tip); return control; }
  const kind = control.tagName === 'INPUT' ? 'tip-in' : control.tagName === 'TEXTAREA' ? 'tip-area'
    : control.classList.contains('in-select') || control.classList.contains('in-link') ? 'tip-chev' : 'tip-end';
  return html`<div class=${`fld-ctl ${kind}`}>${control}${tip}</div>` as HTMLElement;
}

const typed = (type: string) => (props: FieldProps) => {
  if (props.field.meta.type !== type && !(type === 'text' && !props.field.meta.type)) throw new Error(`${props.field.meta.label} is a ${props.field.meta.type} field, not ${type}`);
  return BaseField(props);
};

export const TextField = typed('text');
export const LongTextField = typed('longtext');
export const RichTextField = typed('richtext');
export const IntegerField = typed('integer');
export const DecimalField = typed('decimal');
export const CurrencyField = typed('currency');
export const PercentField = typed('percent');
export const DateField = typed('date');
export const DateTimeField = typed('datetime');
export const TimeField = typed('time');
export const DurationField = typed('duration');
export const BooleanField = typed('boolean');
export const SelectField = typed('select');
export const LinkField = typed('link');
export const EmailField = typed('email');
export const URLField = typed('url');
export const PhoneField = typed('phone');
export const TagsField = typed('tags');
export const MultiSelectField = typed('multiselect');
export const StateField = typed('state');
export const SequenceField = typed('sequence');
export const ReferenceField = typed('reference');

/** A read-only value calculated from a rule; hovering the hint shows the rule. */
export function ComputedField(props: FieldProps): HTMLElement {
  if (!props.field.calculated) throw new Error(`${props.field.meta.label} is not calculated`);
  return BaseField(props);
}

/** The field component for any field, chosen by its type. */
export function fieldFor(props: FieldProps): HTMLElement {
  return props.field.calculated ? ComputedField(props) : BaseField(props);
}
