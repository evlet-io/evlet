// What the parts of one record view share (app/record-page.ts builds it, app/record-content.ts draws the form from
// it): the record, what this person may do with it, and the pieces later parts fill in. A part reads another's
// slot only when it runs, so the slots filled after it are there by then.

import type { Readable } from '../reactive';
import type { RecordData } from '../api';
import type { RecordStore } from '../model/record';
import type { FieldSchema, LayoutBlock } from '../model/schema';
import type { ScriptHost } from '../model/script';
import type { FieldLike } from '../ui/fields';
import type { Folds } from '../ui/fold';
import type { AppContext } from './context';
import type { DuplicateCopy } from './duplicate';
import type { BlockRecord } from './record-blocks';
import type { identity, panelView, sections, DetailExtras, ScreenModel } from './meta';
import type { PanelHost } from './record-page';

export interface ViewOptions { isNew: boolean; extras: Readable<DetailExtras>; detail: Readable<RecordData | null>; query: Record<string, string>; /** What filled this new record (an outcome's new=...). */ prefill?: string | null;
  /** The saved record this new one copies (Duplicate). */ duplicate?: DuplicateCopy | null;
  /** Shown in a list's side panel: the narrow layout. */ panel?: PanelHost; /** A new record: what held the focus when it was asked for. */ focusedAtOpen?: Element | null }

export type Section = ReturnType<typeof sections>[number];
export type TableChild = { name: string; label: string; model?: string; read_only?: boolean };

export interface RecordScope {
  ctx: AppContext;
  model: ScreenModel;
  store: RecordStore;
  options: ViewOptions;
  isNew: boolean;
  panel?: PanelHost;
  extras: Readable<DetailExtras>;
  look: ReturnType<typeof identity>;
  canUpdate: boolean;
  canCreate: boolean;
  canWrite: boolean;
  state: ScreenModel['state'] | null;
  locked: Readable<boolean>;
  has(field: string): boolean;
  /** The field as this screen shows it (gate in record-page.ts). */
  view(field: string): FieldLike;
  sequence: FieldSchema | undefined;
  numbered: FieldSchema | undefined;
  numberNames: Set<string>;
  numberEditable(field: FieldLike): boolean;
  numberInForm(field: string): boolean;
  stateValue(): string;
  script: ScriptHost | null;
  titleText: Readable<string>;
  /** True once the view is going away on purpose (saved, deleted, expanded): leaving does not ask. */
  leaving: boolean;
  changedNew(): boolean;
  forget(): void;
  expand(): void;
  draft: ReturnType<typeof import('./unsaved').keepWhenHidden>;
  save(): Promise<boolean>;
  showErrors(): void;
  discardNew(): Promise<void>;
  followed(label: string): Promise<boolean>;
  blockedMove(to: string): string | null;
  runMove(to: string): Promise<void>;
  remove(): void;
  canDuplicate(): boolean;
  duplicateNow(): void;
  canDelete(): boolean;
  // Header
  show: string;
  statusMeta: FieldSchema | undefined;
  takeable(): NonNullable<DetailExtras['_moves']>;
  lookNow(): string | null;
  markName(): string;
  numberText: (() => string) | null;
  naming: ScreenModel['naming'] | null;
  factFields: FieldSchema[];
  // What the form shows
  shows: ReturnType<typeof panelView> | null;
  panelSections(): Section[];
  tabShown(id: string): boolean;
  summaryShown: boolean;
  cardNames: ScreenModel['cards'];
  cardStats: unknown[];
  statCount: number;
  // Blocks
  tables: NonNullable<ScreenModel['children']>;
  blockRecord: BlockRecord;
  layout: ScreenModel['record_layout'] | null;
  folds: Folds;
  foldable: boolean;
  saveBarView(): unknown;
  frame: HTMLElement;
  blocks?: LayoutBlock[];
}
