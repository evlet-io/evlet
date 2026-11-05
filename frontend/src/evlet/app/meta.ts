// What the screens read from /api/schema and record details.
//
// The keys of later engine phases (State and moves, void and revise, Sequence, measures, key
// fields, relations, attachments, effects, journal) are optional here: a screen shows a section
// only when the schema or the detail carries its key. Shapes follow docs/states-actions.md,
// docs/sequences.md, docs/measures.md, docs/meaning.md, docs/effects.md and docs/journal.md.

import type { RecordData } from '../api';
import type { FieldSchema, ModelSchema, SchemaIndex } from '../model/schema';
import { kindOf } from '../ui/kinds';
import { tintFor, type Tint } from '../ui/core';

export interface MoveSchema { from: string[] | string; to: string; label: string; roles?: string[]; guarded?: boolean; when?: unknown; asks?: string[] }
/** show: how the record page draws the State, a header badge (the default), the stage bar, or neither. */
export interface StateMeta { field: string; states: string[]; default?: string; moves: MoveSchema[]; locked?: string[]; final?: string[]; voidable?: string[]; show?: StateShow }
export type StateShow = 'badge' | 'bar' | 'none';
export interface MeasureSchema { name: string; function: string; field: string | null; per_record: boolean; model: string | null; link: string | null; where?: unknown }
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
  permissions?: { create: boolean; update: boolean; delete: boolean; void?: boolean; revise?: boolean; unpost?: boolean; repost?: boolean };
  voidable?: boolean;
  revisable?: boolean;
  /** Posted records can be unposted and reposted (the model's effects can all be reversed). */
  unpostable?: boolean;
  measures?: MeasureSchema[];
  key_fields?: string[];
  relations?: RelationSchema[];
  attachments?: boolean;
  attachment_limits?: { max_files: number; max_size: number };
  effects?: EffectSchema[];
  effect_target?: boolean;
  /** The record has a journal (Phase 8): whether entries are hash chained and the kinds this role may post. */
  journal?: { tamper_evident: boolean; kinds: string[] };
  lifecycle?: { confirmable: true; labels: Record<string, string>; permissions: Record<string, boolean> };
  /** The field naming fills (the standard `name`, or an older target field), when, and what it shows until then. */
  naming?: { field: string; assign_on?: string; manual?: boolean; placeholder?: string | null };
}

/** Availability keys a detail may carry (outside the ETag). */
export interface Availability { allowed: boolean; reason?: string }
export interface DetailExtras {
  _lifecycle?: { state: 'draft' | 'confirmed' | 'cancelled'; revision: number; confirmation_id: string | null };
  _lifecycle_actions?: Array<'confirm' | 'cancel' | 'reopen'>;
  _locked?: boolean;
  _moves?: Array<{ to: string; label: string; allowed: boolean; reason?: string; asks?: string[] }>;
  _actions?: Array<{ name: string; label: string; allowed: boolean; reason?: string }>;
  _void?: Availability;
  _revise?: Availability;
  /** Listed only for roles that may unpost, on a posted record. */
  _unpost?: Availability;
  /** Listed only for roles that may repost, on a record that was unposted. */
  _repost?: Availability;
  /** false for a record that ever posted: it is kept, so delete is refused. */
  _deletable?: false;
  _effects?: Array<{ target: string; rows: number; reversals: number; balanced?: boolean }>;
  revised_by?: number | null;
  _original?: number;
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
/** Query keys of a list's address: search, sort, page, view, saved view, filters, and the side panel's record and tab. */
export const LIST_KEYS = ['q', 'sort', 'page', 'view', 'tab', 'f', 'open', 'rtab'];

export const sequenceField = (model: ScreenModel): FieldSchema | undefined => model.fields.find(field => field.type === 'sequence');
/** The document number the server assigns: a Sequence field, or the field a naming series fills. */
export const numberField = (model: ScreenModel): FieldSchema | undefined => sequenceField(model) ?? (model.naming ? fieldOf(model, model.naming.field) : undefined);
export const sources = (move: MoveSchema): string[] => Array.isArray(move.from) ? move.from : [move.from];

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

/** List columns: Meta.list_fields (without id) or the first eight visible fields; the title first. */
export function listFields(model: ScreenModel): FieldSchema[] {
  const named = (model.list_fields ?? []).filter(name => name !== 'id').map(name => fieldOf(model, name)).filter((field): field is FieldSchema => Boolean(field && !field.hidden));
  let fields = named.length ? named : visible(model).filter(field => field.type !== 'longtext' && field.type !== 'richtext');
  const title = fieldOf(model, model.title_field);
  if (title && !title.hidden && !fields.includes(title)) fields = [title, ...fields];
  return fields.slice(0, 8);
}

/** Key fields: Meta.key_fields, or the first readable fields that are not the title. */
export function keyFields(model: ScreenModel): FieldSchema[] {
  const named = (model.key_fields ?? []).map(name => fieldOf(model, name)).filter((field): field is FieldSchema => Boolean(field));
  if (named.length) return named;
  return visible(model).filter(field => field.name !== model.title_field && field.type !== 'longtext' && field.type !== 'richtext').slice(0, 6);
}

/** Details sections: Meta.layout, or one section with every visible field. Unknown names (not readable) drop out. */
export function sections(model: ScreenModel): Array<{ label: string; fields: FieldSchema[]; collapsible?: boolean; collapsed?: boolean | 'empty'; flow?: 'rows' | 'columns' }> {
  const layout = model.layout?.length ? model.layout : [{ label: 'Details', fields: visible(model).map(field => field.name) }];
  const seen = new Set<string>();
  const result = layout.map(section => ({ label: section.label, collapsible: section.collapsible, collapsed: section.collapsed, flow: section.flow, fields: section.fields.map(name => fieldOf(model, name))
    .filter((field): field is FieldSchema => Boolean(field && !field.hidden && !seen.has(field.name) && seen.add(field.name))) }));
  // Readable fields the layout leaves out still show, last.
  const rest = visible(model).filter(field => !seen.has(field.name));
  if (rest.length) result.push({ label: 'Other', collapsible: false, collapsed: false, flow: 'rows', fields: rest });
  return result.filter(section => section.fields.length);
}

/** Summary strip stats: key fields first, else calculated fields, at most four. */
export function summaryFields(model: ScreenModel): FieldSchema[] {
  const keyed = (model.key_fields ?? []).map(name => fieldOf(model, name)).filter((field): field is FieldSchema => Boolean(field));
  if (keyed.length) return keyed.slice(0, 4);
  return model.fields.filter(field => field.compute && !field.hidden).slice(0, 4);
}

// ---------- values ----------

export const formatOf = (meta: FieldSchema, value: unknown) => value === null || value === undefined || value === '' ? '' : kindOf(meta.type).format(value, meta);

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

/** A measure's value as text, by the measured field's type. */
export function measureText(index: SchemaIndex, model: ScreenModel, measure: MeasureSchema, value: unknown): string {
  if (value === null || value === undefined) return 'None';
  if (measure.function === 'count' || measure.function === 'count_distinct') return Number(value).toLocaleString('en-US');
  const target = measure.model ? (index.schema.models.find(item => item.name === measure.model) as unknown as ScreenModel | undefined) : model;
  const field = target && measure.field ? fieldOf(target, measure.field) : undefined;
  return field ? formatOf(field, value) : String(value);
}

export const measureLabel = (measure: MeasureSchema) => measure.name.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());

/** The record page tab id of a Meta.tabs name: a record tab keeps its name, a Table field's tab is t-<name>. */
const tabId = (name: string) => ['activity', 'details', 'revisions', 'related', 'postings'].includes(name) ? name : `t-${name}`;

/**
 * Record page tabs in the model's order (Meta.tabs or the Controls tab order): the tabs it names first,
 * the rest after them in their usual order. Also the tab a record opens on: its default_tab when the
 * record has that tab, else the first.
 */
export function orderTabs<T extends { id: string }>(model: ScreenModel, items: T[]): { tabs: T[]; home: string } {
  const order = (model.tabs ?? []).map(tabId);
  const rank = (item: T) => { const at = order.indexOf(item.id); return at < 0 ? order.length : at; };
  const tabs = items.map((item, index) => ({ item, index })).sort((a, b) => rank(a.item) - rank(b.item) || a.index - b.index).map(entry => entry.item);
  const wanted = model.default_tab ? tabId(model.default_tab) : null;
  return { tabs, home: tabs.some(tab => tab.id === wanted) ? wanted! : tabs[0]?.id ?? 'details' };
}
