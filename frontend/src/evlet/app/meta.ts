// What the screens read from /api/schema and record details.
//
// The keys of later engine phases (State steps and rules, Sequence, measures, key
// fields, relations, attachments, effects, journal) are optional here: a screen shows a section
// only when the schema or the detail carries its key. Shapes follow docs/states-actions.md,
// docs/sequences.md, docs/measures.md, docs/meaning.md, docs/effects.md and docs/journal.md.

import type { RecordData } from '../api';
import type { FieldSchema, ModelSchema, SchemaIndex } from '../model/schema';
import { currencyOf, humanize, kindOf, moneyPlaces } from '../ui/kinds';
import { currencyDecimals } from '../model/format';
import { moneyChild } from '../ui/money';
import type { Child } from '../dom';
import { tintFor, type Tint } from '../ui/core';

export interface RemarkSchema { mode: 'optional' | 'required'; label: string | null }
/** One State rule (docs/states-actions.md): its stable key names the move route, POST /api/{model}/{id}/moves/{key}. */
export interface MoveSchema { key: string; from: string[]; to: string; label: string; by?: string[]; guarded?: boolean; when?: unknown; primary?: boolean; reject?: boolean; remark?: RemarkSchema | null; asks?: string[] }
export interface StepSchema { key: string; label: string; color: Tint | null; lock: boolean; release: boolean; final: boolean }
/** show: how the record page draws the State, a header badge (the default), the stage bar, or neither. */
export interface StateMeta { field: string; default?: string; steps: StepSchema[]; rules: MoveSchema[]; show?: StateShow }
export type StateShow = 'badge' | 'bar' | 'none';
/** A declared measure's trend (docs/measures.md#trends): read the series from GET /api/{model}/aggregate with
 * measures=<name>, group_by=<field>:<grain> and the list filters plus <field>__gte the start of the first of the
 * last `points` periods. Periods without records are not returned. */
export interface MeasureTrend { field: string; grain: 'day' | 'month' | 'year'; points: number }
export interface MeasureSchema {
  name: string; function: string; field: string | null; per_record: boolean; model: string | null; link: string | null; where?: unknown;
  trend?: MeasureTrend; color?: 'blue' | 'green' | 'amber' | 'rose' | 'violet' | 'teal' | 'slate';
}
export interface RelationSchema { name: string; label: string; model: string; link: string; limit: number; order?: string[] }
export interface EffectSchema { target: string; on: string | null; balanced: string[] | null; reverse: unknown }
export interface ActionMeta {
  name: string; label: string; roles?: string[]; guarded?: boolean; available_when?: unknown;
  /** Field schemas of declared inputs (Phase 4). */
  inputs?: FieldSchema[];
  /** Scalar arguments of actions without declared inputs. */
  arguments?: Array<{ name: string; type: string; required: boolean; default?: unknown }>;
  /** A Python form dialog action: the generated screens do not offer it. */
  dialog?: boolean;
}

/** A model entry with the optional keys of later phases. */
export interface ScreenModel extends Omit<ModelSchema, 'state' | 'actions'> {
  state?: StateMeta;
  /** Meta.status: the Select or State shown as the record's status badge. */
  status?: string;
  actions?: ActionMeta[];
  permissions?: { create: boolean; update: boolean; delete: boolean };
  measures?: MeasureSchema[];
  key_fields?: string[];
  /** Meta.cards: up to five derived values for the top of a compact record page, each a field (compute, fetch or
   * query) or a per-record measure, cut to what this role may read. Absent: the automatic summary strip. */
  cards?: string[];
  /** The field (type "image") whose picture shows left of the record's title. Read defensively: absent, or naming a
   * field this role does not read, shows nothing. */
  image?: string;
  relations?: RelationSchema[];
  attachments?: boolean;
  attachment_limits?: { max_files: number; max_size: number };
  effects?: EffectSchema[];
  effect_target?: boolean;
  /** The record has a journal (Phase 8): whether entries are hash chained and the kinds this role may post. */
  journal?: { tamper_evident: boolean; kinds: string[] };
  /** The field naming fills (the standard `name`, or an older target field), when, and what it shows until then. */
  naming?: { field: string; assign_on?: string; manual?: boolean; placeholder?: string | null };
}

/** A rule out of the record's current state, as its detail lists it: blocked moves carry their reason. */
export interface DetailMove { key: string; to: string; label: string; allowed: boolean; primary?: boolean; reason?: string; asks?: string[];
  remark?: RemarkSchema & { required: boolean } }
export interface DetailExtras {
  _locked?: boolean;
  _moves?: DetailMove[];
  _actions?: Array<{ name: string; label: string; allowed: boolean; reason?: string }>;
  /** false for a record that ever posted: it is kept, so delete is refused. */
  _deletable?: false;
  _effects?: Array<{ target: string; rows: number; reversals: number; balanced?: boolean }>;
  /** When and by whom the record was created and last changed (UTC). Who is null for the engine and for portal roles. */
  _audit?: RecordAudit;
}

export interface AuditPerson { id: number; name: string }
export interface RecordAudit { created_at: string | null; created_by: AuditPerson | null; modified_at: string | null; modified_by: AuditPerson | null }

export const screenModel = (index: SchemaIndex, name: string) => index.model(name) as unknown as ScreenModel;
export const extrasOf = (data: RecordData | null | undefined): DetailExtras => (data ?? {}) as DetailExtras;

/** Root models a person opens from the sidebar: not Table rows. */
export const rootModels = (index: SchemaIndex): ScreenModel[] => index.schema.models.filter(model => !model.child_only) as unknown as ScreenModel[];

export const fieldOf = (model: ScreenModel, name: string): FieldSchema | undefined => model.fields.find(field => field.name === name);
export const isStateField = (model: ScreenModel, field: FieldSchema) => model.state?.field === field.name || (field as any).state === true;

/**
 * How a record's State reads in lists and headers, from its steps alone (never from state names):
 * - `cancelled`: a `final` step coloured rose. Struck-through title, red "Cancelled" text, a soft red banner.
 * - `locked`: any other `final` or `lock` step, such as Confirmed or Done: a lock and the step label in grey.
 * - `draft`: the State's default step (else its first) while it is not locked: a dashed pill with a pencil.
 * - `open`: everything else, the coloured badge as before. Null without a State or a value.
 */
export type StateLook = 'cancelled' | 'locked' | 'draft' | 'open';
export function stateLook(model: ScreenModel, value: unknown): StateLook | null {
  const state = model.state;
  if (!state || value === null || value === undefined || value === '') return null;
  const name = String(value), step = state.steps.find(item => item.key === name);
  if (step?.final && step.color === 'rose') return 'cancelled';
  if (step?.final || step?.lock) return 'locked';
  return name === (state.default ?? state.steps[0]?.key) ? 'draft' : 'open';
}
/** A State step by key, and its label (a value that is no step reads humanized). */
export const stepOf = (model: ScreenModel, value: string): StepSchema | undefined => model.state?.steps.find(step => step.key === value);
export const stepLabel = (model: ScreenModel, value: string): string => stepOf(model, value)?.label ?? humanize(value);
/** Query keys of a list's address: search, sort, page, view, saved view, filters, and the side panel's record and tab. */
export const LIST_KEYS = ['q', 'sort', 'page', 'view', 'tab', 'f', 'open'];

export const sequenceField = (model: ScreenModel): FieldSchema | undefined => model.fields.find(field => field.type === 'sequence');
/** The document number the server assigns: a Sequence field, or the field a naming series fills. */
export const numberField = (model: ScreenModel): FieldSchema | undefined => sequenceField(model) ?? (model.naming ? fieldOf(model, model.naming.field) : undefined);

// ---------- identity ----------

export interface Identity { icon: string; tint: Tint; person: boolean }
const NAMED: Array<[RegExp, string, Tint, boolean?]> = [
  [/invoice|bill|receipt/, 'receipt', 'blue'], [/deal|opportunit/, 'handshake', 'blue'], [/lead/, 'target', 'teal', true],
  [/contact|person|people|employee|staff|user|member/, 'users', 'violet', true], [/org|company|compan|customer|supplier|vendor|client|account/, 'building-2', 'amber'],
  [/product|item|stock|inventory|part/, 'package', 'amber'], [/task|todo|ticket|issue/, 'circle-check', 'amber'], [/payment|wallet|expense/, 'wallet', 'green'],
  [/order|quote|quotation|proposal/, 'file-text', 'green'], [/ledger|journal|entry/, 'layers', 'slate'], [/report/, 'chart-column', 'rose'],
];
/** Icon, accent and mark of a model, from its name (the schema has no presentation keys). */
export function identity(model: Pick<ScreenModel, 'name'>): Identity {
  const name = model.name.toLowerCase();
  for (const [pattern, icon, tint, person] of NAMED) if (pattern.test(name)) return { icon, tint, person: Boolean(person) };
  return { icon: 'file-text', tint: tintFor(name), person: false };
}

/** Sidebar group: documents (with a State or lines) and other records. */
export const groupOf = (model: ScreenModel) => model.state || model.children?.length ? 'Documents' : 'Records';

// ---------- fields shown ----------

const visible = (model: ScreenModel) => model.fields.filter(field => !field.hidden);

/** List columns: Meta.list_fields (without id) or the first eight visible fields that are not column=False; the title first. */
export function listFields(model: ScreenModel): FieldSchema[] {
  const named = (model.list_fields ?? []).filter(name => name !== 'id').map(name => fieldOf(model, name)).filter((field): field is FieldSchema => Boolean(field && !field.hidden));
  let fields = named.length ? named : visible(model).filter(field => field.column !== false && field.type !== 'longtext' && field.type !== 'richtext' && field.type !== 'image');
  const title = fieldOf(model, model.title_field);
  if (title && !title.hidden && !fields.includes(title)) fields = [title, ...fields];
  return fields.slice(0, 8);
}

/** Key fields: Meta.key_fields, or the first readable fields that are not the title. */
export function keyFields(model: ScreenModel): FieldSchema[] {
  const named = (model.key_fields ?? []).map(name => fieldOf(model, name)).filter((field): field is FieldSchema => Boolean(field));
  if (named.length) return named;
  return visible(model).filter(field => field.name !== model.title_field && field.type !== 'longtext' && field.type !== 'richtext' && field.type !== 'image').slice(0, 6);
}

/** Details sections: Meta.layout, or one section with every visible field. Unknown names (not readable) drop out. */
export function sections(model: ScreenModel): Array<{ label: string; fields: FieldSchema[]; collapsible?: boolean; collapsed?: boolean | 'empty'; flow?: 'rows' | 'columns';
  /** Section(description=...). */ description?: string; /** The compact page's Main area. */ main?: boolean }> {
  const layout = model.layout?.length ? model.layout : [{ label: 'Details', fields: visible(model).map(field => field.name) }];
  const seen = new Set<string>();
  const result = layout.map(section => ({ label: section.label, collapsible: section.collapsible, collapsed: section.collapsed, flow: section.flow,
    description: 'description' in section ? section.description : undefined, main: 'main' in section ? section.main : undefined, fields: section.fields.map(name => fieldOf(model, name))
    .filter((field): field is FieldSchema => Boolean(field && !field.hidden && !seen.has(field.name) && seen.add(field.name))) }));
  // Readable fields the layout leaves out still show, last.
  const rest = visible(model).filter(field => !seen.has(field.name));
  if (rest.length) result.push({ label: 'Other', collapsible: false, collapsed: false, flow: 'rows', description: undefined, main: undefined, fields: rest });
  return result.filter(section => section.fields.length);
}

/** The record header's facts: the first three key fields that the title, name, State and status badge don't already show. */
export function headerFacts(model: ScreenModel, has: (name: string) => boolean = () => true): FieldSchema[] {
  return keyFields(model).filter(meta => meta.name !== model.title_field && meta.name !== 'name' && has(meta.name) && !isStateField(model, meta) && meta.name !== model.status).slice(0, 3);
}

/** Summary strip stats: calculated values the header doesn't show, at most four. Key fields belong to the
 * header; per-record measures follow these on the page. Meta.summary = False leaves the strip out. */
export function summaryFields(model: ScreenModel, shown: ReadonlySet<string> = new Set(headerFacts(model).map(meta => meta.name))): FieldSchema[] {
  if (model.summary === false) return [];
  return model.fields.filter(field => field.compute && !field.hidden && !shown.has(field.name) && field.type !== 'longtext' && field.type !== 'richtext' && field.type !== 'image').slice(0, 4);
}

// ---------- values ----------

export const formatOf = (meta: FieldSchema, value: unknown) => value === null || value === undefined || value === '' ? '' : kindOf(meta.type).format(value, meta);

/**
 * A value to show on screen: money with the site's currency symbol (an image when the site set one), anything
 * else as formatOf's text. `code` is the currency when it comes from the record; the field's fixed code otherwise.
 * Money shows `places` decimals when given (the record's currency, fields.ts placesIn), else those of `code`,
 * else those of the field's fixed code (2 without one): never the schema scale, which is the storage scale.
 */
export function displayOf(meta: FieldSchema, value: unknown, code?: string, places?: number): Child {
  if (meta.type === 'currency') return moneyChild(value, code ?? currencyOf(meta), places ?? (code ? currencyDecimals(code) : moneyPlaces(meta)));
  return formatOf(meta, value);
}

/** The title of a record from list or detail data. */
export function titleOf(model: ScreenModel, data: Record<string, unknown>): string {
  const value = data[model.title_field];
  if (value === null || value === undefined || value === '') {
    const sequence = sequenceField(model);
    if (sequence && model.title_field === sequence.name) return `Draft ${model.label.toLowerCase()}`;
    return `${model.label} ${data.id ?? ''}`.trim();
  }
  const field = fieldOf(model, model.title_field);
  return field && field.type !== 'text' && field.type !== 'sequence' ? formatOf(field, value) : String(value);
}

/**
 * A measure's value as text, by the measured field's type (an image currency symbol reads as its code).
 * `currencies` is the route's answer, as for measureView.
 */
export function measureText(index: SchemaIndex, model: ScreenModel, measure: MeasureSchema, value: unknown,
  currencies?: Record<string, string>): string {
  const field = measureField(index, model, measure, value);
  if (!field) return plainMeasure(measure, value);
  return formatOf(moneyField(field, measure, value, currencies) ?? field, value);
}

/**
 * A Currency field whose code comes from each record (currency_source), as a measure's money result shows it: with
 * the route's code (a SAR record's sum shows SAR), else as a plain amount without a symbol, since no one code covers
 * rows in several currencies. Without the route's `currencies`, a field with a fallback code keeps it (null).
 * The plain amount keeps the places the server sent ("0.00"), never the schema scale, which is the storage scale
 * (6): 2 when the value carries none. A field with no currency source keeps its own code (null).
 */
function moneyField(field: FieldSchema, measure: MeasureSchema, value: unknown, currencies?: Record<string, string>): FieldSchema | null {
  if (field.type !== 'currency' || !field.currency_source) return null;
  const code = currencies?.[measure.name];
  if (code) return { ...field, currency: code, currency_source: undefined };
  if (!currencies && field.currency) return null;
  const sent = typeof value === 'string' ? /\.(\d+)$/.exec(value.trim())?.[1].length : undefined;
  return { ...field, type: 'decimal', scale: sent ?? 2 };
}

/**
 * A measure's value on screen: money shows the site's currency symbol, an image when the site set one.
 * `currencies` is what the measures route answered (docs/measures.md): a money result in it takes that code, and one
 * missing from it (rows in several currencies, or none the caller may read) shows as a plain amount, never in the
 * default currency. Without `currencies` the field's own currency applies.
 */
export function measureView(index: SchemaIndex, model: ScreenModel, measure: MeasureSchema, value: unknown,
  currencies?: Record<string, string>): Child {
  const field = measureField(index, model, measure, value);
  if (!field) return plainMeasure(measure, value);
  return displayOf(moneyField(field, measure, value, currencies) ?? field, value);
}

// A count is a plain number, never money, whatever field it counts (moneyField says how money shows).
function measureField(index: SchemaIndex, model: ScreenModel, measure: MeasureSchema, value: unknown): FieldSchema | undefined {
  if (value === null || value === undefined || measure.function === 'count' || measure.function === 'count_distinct') return undefined;
  const target = measure.model ? (index.schema.models.find(item => item.name === measure.model) as unknown as ScreenModel | undefined) : model;
  return target && measure.field ? fieldOf(target, measure.field) : undefined;
}

function plainMeasure(measure: MeasureSchema, value: unknown): string {
  if (value === null || value === undefined) return 'None';
  if (measure.function === 'count' || measure.function === 'count_distinct') return Number(value).toLocaleString('en-US');
  return String(value);
}

/** The period before a trend bucket ("2026-06" -> "2026-05", "2026" -> "2025", "2026-03-01" -> "2026-02-28"). */
function previousBucket(bucket: string, grain: MeasureTrend['grain']): string {
  if (grain === 'year') return String(Number(bucket) - 1);
  if (grain === 'month') {
    const [year, month] = bucket.split('-').map(Number);
    return month === 1 ? `${year - 1}-12` : `${year}-${String(month - 1).padStart(2, '0')}`;
  }
  const date = new Date(`${bucket}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

/**
 * A measure's trend line from an aggregate grouped by its trend field and grain: the last `points` periods up to
 * the latest one with records, in date order. Periods without records count 0 for a count or a sum, and are
 * left as gaps (NaN) for a lowest, highest or average value. Records without a date are left out. Null when no
 * period has records.
 */
export function trendValues(items: Array<Record<string, unknown>>, key: string, measure: Pick<MeasureSchema, 'name' | 'function'>, trend: MeasureTrend): number[] | null {
  const byBucket = new Map<string, number>();
  for (const item of items) {
    const bucket = item[key];
    if (bucket === null || bucket === undefined || bucket === '') continue;
    byBucket.set(String(bucket), Number(item[measure.name] ?? 0));
  }
  if (!byBucket.size) return null;
  const zero = ['count', 'count_distinct', 'sum'].includes(measure.function) ? 0 : Number.NaN;
  let bucket = [...byBucket.keys()].sort().pop()!;
  const values: number[] = [];
  for (let index = 0; index < trend.points; index++) {
    values.unshift(byBucket.get(bucket) ?? zero);
    bucket = previousBucket(bucket, trend.grain);
  }
  return values;
}

/** The accents a list's measure cards take in turn when a measure declares no color=. */
export const MEASURE_TONES: Tint[] = ['blue', 'teal', 'violet', 'amber'];

/** A measure card's icon by its function: a count, a sum of money or of numbers, a lowest or highest value. */
export function measureIcon(model: ScreenModel, measure: MeasureSchema): string {
  if (measure.function === 'count' || measure.function === 'count_distinct') return 'hash';
  if (measure.function === 'min') return 'arrow-down';
  if (measure.function === 'max') return 'arrow-up';
  const field = !measure.model && measure.field ? fieldOf(model, measure.field) : undefined;
  return field?.type === 'currency' ? 'wallet' : 'chart-column';
}

export const measureLabel = (measure: MeasureSchema) => measure.name.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());

/**
 * What a record opened in a list's side panel shows (model.panel: panel=False flags, Meta.panel or the Controls
 * toggles). The full page ignores it.
 */
export interface PanelView {
  /** True when the panel leaves anything out: it then offers the full page. */
  hides: boolean;
  section(label: string): boolean;
  field(name: string): boolean;
  /** A part of the record (the schema's panel "tabs"): activity, details, related, postings or a Table field. */
  tab(name: string): boolean;
  /** summary, key_fields, related, files or info. */
  block(name: string): boolean;
}

export function panelView(model: ScreenModel): PanelView {
  const panel = model.panel ?? {};
  const hidden = (names?: string[]) => new Set(names ?? []);
  const sections = hidden(panel.sections), fields = hidden(panel.fields), tabs = hidden(panel.tabs), blocks = hidden(panel.blocks);
  return {
    hides: Boolean(sections.size || fields.size || tabs.size || blocks.size),
    section: label => !sections.has(label),
    field: name => !fields.has(name),
    tab: name => !tabs.has(name),
    block: name => !blocks.has(name),
  };
}


