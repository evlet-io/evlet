// The parts of GET /api/schema the model runtime and renderers use.
// Python is the source of truth; this only names the shapes it sends.

import type { CurrencyDisplay } from './format';

export type Expression = Record<string, unknown> | null;

export interface FieldSchema {
  name: string;
  type: string;
  label: string;
  required: boolean;
  read_only: boolean;
  hidden?: boolean;
  help_text?: string;
  /** Text behind a ? icon at the right end of the input. */
  tooltip?: string;
  placeholder?: string;
  /** A unit shown after the input, such as % or days. */
  suffix?: string;
  scale?: number | null;
  /** A Currency field's code when it is fixed (on the field, or an app setting read by the server). */
  currency?: string;
  /** Where a Currency field's code comes from on the record: a field, or a field of the record a Link points at. */
  currency_source?: { field: string; link?: string; model?: string };
  minimum?: string | number | null;
  maximum?: string | number | null;
  max_length?: number;
  choices?: string[];
  /** Select colors: a badge colour for some choices. */
  colors?: Record<string, string>;
  /** Options Controls hid from new values; records that hold one keep it. */
  retired_choices?: string[];
  /** Full width in the form, from a Controls layout. */
  wide?: boolean;
  target?: string;
  default?: unknown;
  default_kind?: string;
  stored?: boolean;
  compute?: Expression;
  initial?: Expression;
  applies_when?: Expression;
  required_when?: Expression;
  locked_when?: Expression;
  /** What a list filter may use on the field (evlet/query.py); empty when it cannot be filtered. */
  operators?: string[];
}

/** row_changes false: the parent's save takes only full row lists without _key (a ChildTable on a plain model). */
export interface ChildSchema { name: string; model: string; label: string; link: string; min_rows?: number; max_rows?: number; row_changes?: boolean
  /** Whether list filters may match the parent on these rows. */
  filterable?: boolean }
export interface ActionSchema { name: string; label: string; arguments?: Array<{ name: string; type: string; required: boolean; default?: unknown }>; dialog?: boolean }

/** Phase 4 state metadata (field, states, moves with labels and roles, locked, final). Optional until the backend sends it. */
export interface StateSchema {
  field: string;
  states: string[];
  moves: Array<{ from: string; to: string; label: string; roles?: string[] }>;
  locked?: string[];
  final?: string[];
  show?: 'badge' | 'bar' | 'none';
}

export interface CalculationSchema {
  language: string;
  numeric_profile: string;
  model_schema_hash: string;
  /** Per target: local_universal (the browser may compute it) or unavailable. */
  capabilities: Record<string, string>;
  /** The plan to evaluate in the browser, present only when every result can be computed locally. */
  plan?: any;
}

/** Hidden names by kind in a list's side panel. Display only: permissions never read it. */
export interface PanelSchema { sections?: string[]; fields?: string[]; tabs?: string[]; blocks?: string[]; order?: string[] }

export interface ListTabsSchema { field: string; kind: 'state' | 'select' | 'link'; default: string }

export interface ModelSchema {
  name: string;
  label: string;
  plural: string;
  /** The model's docstring, '' when it has none. */
  description?: string;
  /** "<app>.<Class>" for a model an installed app declares; the Explorer and the workspaces read the app from it. */
  resource?: string;
  title_field: string;
  fields: FieldSchema[];
  list_fields: string[];
  search_fields?: string[];
  /** Form sections. collapsible and collapsed come from evlet.Section or a Controls layout (absent from older servers). */
  layout?: Array<{ label: string; fields: string[]; wide?: string[]; collapsible?: boolean; collapsed?: boolean | 'empty';
    /** rows: across then down; columns: down the left column, then the right. */ flow?: 'rows' | 'columns' }>;
  /** Record page tabs in order (record tabs and child table names) and the tab a record opens on. */
  tabs?: string[]; default_tab?: string | null;
  /** Rows per page and default order, from a Controls list view. */
  page_size?: number;
  order_by?: string;
  /** How the list opens a record: the full page (default) or a side panel beside the list. */
  open_in?: 'page' | 'panel';
  /** The list's tabs (Meta.list_tabs, Meta.list_tab or a Controls list view): the field whose values are the tabs and
   * the tab the list opens on ("all", a value, or for a Link the linked record's title). Absent: no tabs. */
  list_tabs?: ListTabsSchema;
  /** The State field a list board groups by (Meta.board); without it the list has no board. */
  board?: string;
  /** Meta.autosave: false keeps the "Discard your changes?" prompt instead of keeping unsaved changes. */
  autosave?: boolean;
  /** Meta.summary: false leaves the record page's summary strip out. Absent means shown when it has values. */
  summary?: boolean;
  /** Meta.side_blocks: the record page's side blocks in order ('key_fields', 'related', 'files', 'info'). Absent means all. */
  side_blocks?: string[];
  /** Meta.focus: the field a new record's form puts the cursor in. Absent, or a field this role cannot edit: the
   * first editable field. Display only. */
  focus?: string;
  /** What a record opened in a list's side panel leaves out, by kind: panel=False flags, Meta.panel or the Controls
   * toggles. order is the order Meta.panel names tabs and blocks in. Absent means the panel shows everything. */
  panel?: PanelSchema;
  /** Meta.layout with Tabs or blocks besides sections (evlet.Tab, Related, Html, Chart, Table field names). Absent: the
   * record page shows its sections on Details, each Table on its own tab, and the automatic Related tab and block. */
  record_layout?: RecordLayout;
  children?: ChildSchema[];
  actions?: ActionSchema[];
  permissions?: { create: boolean; update: boolean; delete: boolean };
  /** "read" when the model's Meta.api is "read": no screen offers a write, for any role. Absent means "full". */
  api?: 'read';
  child_only?: boolean;
  calculation?: CalculationSchema;
  state?: StateSchema;
  /** Slips the role may present from a record of this model. */
  slips?: SlipSchema[];
  /** Print designs the role may print a record of this model with, the default first. */
  prints?: PrintSchema[];
  /**
   * Per-field behavior. `filters` are a Link's selection filters: a literal value, or
   * `{"$expr": "ref", "args": ["company"]}` for another field of the form (`parent.company` from a
   * row). `filters_blocked` means they read a field the role cannot, so only the server applies them.
   */
  behavior?: Record<string, { filters?: Record<string, unknown>; filters_blocked?: boolean } & Record<string, unknown>>;
}

/** A filter condition of a layout block: [field, operator, value], where the value may be
 * `{"$expr": "ref", "args": ["field"]}` for that field of the record. */
export type LayoutCondition = [string, string, unknown];

/** One block of a record layout, in order. Sections are named by title; their fields are in the model's layout. */
export type LayoutBlock =
  | { kind: 'section'; label: string }
  | { kind: 'table'; name: string }
  | { kind: 'related'; model: string; link: string; label: string; filters: LayoutCondition[]; fields: string[]; limit: number; order: string[] }
  | { kind: 'html'; html: string }
  | { kind: 'chart'; type: 'bar' | 'line' | 'stat'; model: string; link: string; x: string | null; y: string[]; label: string | null; filters: LayoutCondition[] };

/** A record page tab declared with evlet.Tab: its name (Meta.tabs, ?tab=t-<name>), label and blocks. */
export interface LayoutTab { name: string; label: string; blocks: LayoutBlock[] }

/** The record layout: Tabs, or blocks shown on Details without Tabs. */
export interface RecordLayout { tabs?: LayoutTab[]; blocks?: LayoutBlock[] }

/** A print design (evlet.PrintDesign) the role may print with; the PDF comes from the server. */
export interface PrintSchema { name: string; title: string; default: boolean }

/** A slip (evlet.Slip) the role may present; its fields come from the slip's own routes. */
export interface SlipSchema { name: string; label: string; hand_in_label: string }

export interface AppSchema { version: number; ui_version: number; models: ModelSchema[]; /** Menu slips (menu = True), presented from browser code. */ slips?: SlipSchema[];
  /** Reports the role may run (evlet.Report), with their filters, columns and view settings. */ reports?: ReportSchema[];
  /** Present when the app has print designs; available is false without the server's print extra. */ printing?: { available: boolean };
  /** How the currency codes the site changed are shown (Controls, Currencies); other codes show as 'AED 25,200.00'. */
  currency_display?: Record<string, CurrencyDisplay>;
  /** Named sidebars (evlet.Workspace), with only what this role can open; docs/ui.md#workspaces. */ workspaces?: WorkspaceSchema[] }

/** A workspace: declared by an app, or the automatic one of an app that declares none. */
export interface WorkspaceSchema {
  key: string;
  label: string;
  icon: string | null;
  /** The app that declares it ("app" for an application built by hand); null for a workspace added in Controls. */
  app: string | null;
  /** custom: added on this site (Controls workspace overrides). */
  source: 'declared' | 'automatic' | 'custom';
  groups: WorkspaceGroup[];
}

/** A titled group of a workspace's sidebar; never empty. */
export interface WorkspaceGroup { key: string; label: string; icon: string | null; items: WorkspaceItem[] }

/** One sidebar entry. The key is stable: model:<name>[:<label or view>], report:<name>, slip:<name> or url:<label>. */
export type WorkspaceItem =
  | { key: string; kind: 'model'; label: string; model: string; view: 'list' | 'board';
      /** Conditions in the list address form (?f=), or null for the whole list. */ filters: unknown[] | null }
  | { key: string; kind: 'report'; label: string; report: string }
  | { key: string; kind: 'slip'; label: string; slip: string }
  | { key: string; kind: 'url'; label: string; url: string };

/** A report column: its type, and for a link column the model its values open. */
export interface ReportColumn { name: string; type: 'text' | 'integer' | 'decimal' | 'currency' | 'date' | 'boolean' | 'link'; label: string | null;
  link: string | null; total: boolean; unit: string | null }

/** A report (GET /api/_reports or the schema's reports): its filters are the fields of a transient form. */
export interface ReportSchema {
  name: string;
  label: string;
  /** Shown on the report's Explorer card when the server sends one (none does yet). */
  description?: string;
  fields: Array<FieldSchema & { calculated?: boolean }>;
  autorun: boolean;
  columns: ReportColumn[];
  /** The sidebar heading it is listed under; empty lists it under Reports alone. */
  group: string;
  /** The model whose list offers it. */
  model: string | null;
  /** What its `model` is: "ledger" for a ledger model (never named in `model`), "model" otherwise, null without one. */
  source_kind?: 'ledger' | 'model' | null;
  sortable: string[];
  export: boolean;
  chart: { type: 'bar' | 'line'; x: string; y: string[]; label: string | null } | null;
  /** A column whose rows are kept together, with a heading and a subtotal for each group. */
  group_by?: string | null;
  /** Print designs the role may print it with (Print Design). */
  prints?: Array<{ name: string; title: string; default: boolean }>;
}

/** Look up models and fields by name. */
export class SchemaIndex {
  private readonly models = new Map<string, ModelSchema>();
  private readonly fields = new Map<string, Map<string, FieldSchema>>();

  constructor(readonly schema: AppSchema) {
    for (const model of schema.models) {
      this.models.set(model.name, model);
      this.fields.set(model.name, new Map(model.fields.map(field => [field.name, field])));
    }
  }

  model(name: string): ModelSchema {
    const model = this.models.get(name);
    if (!model) throw new Error(`Unknown model ${name}`);
    return model;
  }

  field(model: string, name: string): FieldSchema | undefined { return this.fields.get(model)?.get(name); }

  /** The model of a child table, for example order + lines -> order_lines. */
  childModel(model: string, table: string): ModelSchema {
    const child = this.model(model).children?.find(item => item.name === table);
    if (!child) throw new Error(`${model} has no table ${table}`);
    return this.model(child.model);
  }
}

/** Whether the value of this field is calculated (compute=), so it is never edited. */
export const isCalculated = (field: FieldSchema) => field.compute !== null && field.compute !== undefined;
