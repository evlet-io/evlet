// Read first, edit in place: InlineValue shows a field as text and turns into its input on
// click. Enter saves, Esc cancels, leaving the field saves. Saving one field needs no Save
// button: the change is written and the record saved at once, with Undo in the toast.
// KeyValueList and FormSection lay InlineValues out for side panels and the Details tab.

import { html, when, type Child } from '../dom';
import { batch, cell, untrack } from '../reactive';
import { Button, Icon } from './base';
import { uid } from './core';
import { phrase, labeller } from './explain';
import { flowGrid, FoldSection, type Flow, type Folds, type FoldStart } from './fold';
import { displayValue, FieldControl, FieldHints, recordOf, siblingsOf, type Control, type FieldLike, type LinkSource } from './fields';
import { kindOf } from './kinds';
import { toast } from './layers';
import { tooltip } from './tooltip';

export type Save = () => Promise<boolean> | boolean;

/** The save that goes with a record store field: save the record. */
function recordSave(field: FieldLike): Save | null {
  const record = recordOf(field);
  return record ? () => record.save() : null;
}

/** Shown when the record changed on the server since it was opened (409, 412, 428). */
export const CONFLICT_MESSAGE = 'Someone else changed this record. Reload to see their changes.';

/** Why a field can't be edited, in plain words. */
export function lockReason(field: FieldLike): string {
  const meta = field.meta;
  if (field.calculated) return 'Calculated from a rule';
  if (meta.type === 'state') return 'Changes only through moves';
  if (meta.type === 'sequence') return 'Assigned by the system';
  if (field.rules.get().locked && meta.locked_when) {
    return `Locked when ${phrase(meta.locked_when, labeller(siblingsOf(field)))}`;
  }
  if (field.rules.get().locked) return 'Locked';
  if (meta.read_only) return 'This value can’t be changed here';
  const record = recordOf(field);
  if (record && !record.ready.get()) return 'Still loading';
  if (record?.saving.get()) return 'Saving';
  return '';
}

export interface InlineValueProps {
  field: FieldLike;
  /** Partial save of one field. Defaults to saving the field's record. */
  save?: Save;
  /** How the value reads at rest. */
  display?: (field: FieldLike) => Child;
  /** id of the element that labels this value. */
  labelledBy?: string;
  links?: LinkSource | null;
  /** Show a toast with Undo after a save (default true). */
  confirm?: boolean;
}

/** A field as plain text that turns into its input on click. */
export function InlineValue(props: InlineValueProps): HTMLElement {
  const { field } = props, meta = field.meta, kind = kindOf(meta.type);
  const save = props.save ?? recordSave(field);
  const editing = cell(false, { name: `${meta.name} (editing)` }), busy = cell(false), conflict = cell(false);
  const textual = !kind.control || kind.control === 'textarea' || kind.control === 'richtext';
  // Multi-line kinds: Enter adds a line, Ctrl or Cmd Enter saves.
  const multiline = kind.control === 'textarea' || kind.control === 'richtext';
  let before: unknown = null, control: Control | null = null, finishing = false;

  // Rich text at rest inside the edit button shows its links as text (a link inside a button can't be used).
  const rest = (editable = false) => html`<span class="tx-wrap">${props.display ? props.display(field)
    : displayValue(field, { links: props.links, plainLinks: editable })}</span>`;
  const start = () => {
    if (!untrack(() => field.editable.get())) return;
    before = untrack(() => field.value.get());
    field.error.set(null);
    conflict.set(false);
    editing.set(true);
    control?.focus();
  };
  // Back to rest. Focus returns to the value only after Enter or Esc; when the user left the
  // field (Tab, a click elsewhere) focus stays where they sent it.
  const stop = (focus: boolean) => {
    editing.set(false);
    if (focus) wrapper.querySelector<HTMLElement>('.fval')?.focus();
  };
  const cancel = () => {
    const now = untrack(() => field.value.get());
    if (JSON.stringify(now ?? null) !== JSON.stringify(before ?? null) && untrack(() => field.editable.get())) {
      try { field.set(before); } catch { /* the record moved on; keep its value */ }
    }
    field.error.set(null);
    conflict.set(false);
    stop(true);
  };
  // After a value was written: save the record, confirm with Undo, return to rest.
  const finish = async (refocus: boolean) => {
    if (finishing) return;
    const now = untrack(() => field.value.get());
    const changed = JSON.stringify(now ?? null) !== JSON.stringify(before ?? null);
    if (!changed || !save) { stop(refocus); return; }
    finishing = true;
    busy.set(true);
    const previous = before;
    let saved = false;
    try { saved = await save(); } catch { saved = false; }
    busy.set(false);
    finishing = false;
    if (!saved) {
      if (recordOf(field)?.conflict.peek()) { conflict.set(true); field.error.set(CONFLICT_MESSAGE); }
      else if (!field.error.peek()) field.error.set('This change was not saved. Check it and try again.');
      if (refocus && control?.element.isConnected) control.focus();
      return;
    }
    stop(refocus);
    if (props.confirm !== false) toast(`${meta.label} saved`, {
      undo: async () => {
        try { batch(() => field.set(previous)); await save(); } catch { toast(`Could not undo the change to ${meta.label}`, { icon: 'info' }); }
      },
    });
  };

  // Read the record again and put the user's value back as a draft, so they can save it again.
  const reload = async () => {
    const record = recordOf(field);
    if (!record) return;
    const mine = untrack(() => field.value.get());
    busy.set(true);
    try { await record.reload(); }
    catch { field.error.set('Could not reload the record. Check the connection and try again.'); return; }
    finally { busy.set(false); }
    conflict.set(false);
    field.error.set(null);
    before = untrack(() => field.value.get());
    try { if (untrack(() => field.editable.get())) field.set(mine); } catch { /* the reloaded record locks it */ }
    if (control?.element.isConnected) control.focus();
  };

  const editor = () => {
    control = FieldControl(field, {
      commitOn: 'manual', links: props.links, label: meta.label,
      // A day picked in a calendar (or Done in a picker) saves like Enter.
      onPicked: () => { void finish(true); },
      // Selects, switches, links and tags write on change; save straight away.
      onCommit: textual ? undefined : () => { void finish(true); },
    });
    return html`<div class="fval-edit" aria-busy=${() => busy.get() ? 'true' : null}>${control.element}${when(() => Boolean(field.error.get()), () => html`<span class="err-inline" role="alert">${() => field.error.get()}</span>`)}${when(conflict,
      () => Button({ label: 'Reload', icon: 'rotate-ccw', size: 'small', onClick: () => { void reload(); } }))}</div>`;
  };

  const wrapper = html`<div class="iv" data-field=${meta.name}
    on:keydown=${(event: KeyboardEvent) => {
      if (!editing.peek()) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); }
      else if (event.key === 'Enter' && textual && !(multiline && !(event.metaKey || event.ctrlKey))) {
        event.preventDefault();
        if (control?.commit()) void finish(true);
      }
    }}
    on:focusout=${(event: FocusEvent) => {
      const next = event.relatedTarget as Node | null;
      // Only focus leaving the input counts. The value at rest held the focus from the click and
      // Chrome reports it leaving (with no next element) as the input replaces it.
      if (!(event.target as Element).closest?.('.fval-edit')) return;
      if (!editing.peek() || busy.peek() || (next && (wrapper.contains(next) || (next as Element).closest?.('.pop')))) return;
      if (textual) { if (control?.commit()) void finish(false); }
      else if (!next || !(next as Element).closest?.('.pop')) void finish(false);
    }}>${when(editing, editor, () => {
      const at = html`<button type="button" class="fval" aria-labelledby=${props.labelledBy ?? null}
        aria-label=${props.labelledBy ? null : () => `Edit ${meta.label}`} on:click=${start}>${rest(true)}${Icon('pencil', { size: 'xs', className: 'pen' })}</button>` as HTMLElement;
      // Tab skips a value that can't change; a click still focuses it (and shows why) and screen readers read it.
      const readOnly = html`<div class="fval ro" tabindex="-1" aria-labelledby=${props.labelledBy ?? null}>${rest()}</div>` as HTMLElement;
      tooltip(readOnly, () => lockReason(field));
      return when(field.editable, () => at, () => readOnly);
    })}</div>` as HTMLElement;
  return wrapper;
}

/** Two-column label and value list. Values are InlineValues when editable. */
export function KeyValueList(items: Array<FieldLike | { label: string; field: FieldLike }>, options: { save?: Save; links?: LinkSource | null } = {}): HTMLElement {
  return html`<dl class="fields">${items.map(item => {
    const field = 'field' in item ? item.field : item, label = 'field' in item ? item.label : item.meta.label, id = uid('kv');
    return html`<dt id=${id} hidden=${() => !field.rules.get().applies}>${label}</dt><dd hidden=${() => !field.rules.get().applies}>${InlineValue({ field, save: options.save, labelledBy: id, links: options.links })}</dd>`;
  })}</dl>` as HTMLElement;
}

/** A titled group of fields in two columns; long text spans both. */
export function FormSection(props: { title: string; note?: string; fields: FieldLike[]; save?: Save; links?: LinkSource | null; extra?: Child;
  /** rows (default): across then down; columns: down the left column, then the right. */
  flow?: Flow;
  /** A collapsible form section (FoldSection): its form's Folds, how it starts and its place in the form. */
  fold?: { folds: Folds; collapsible: boolean; collapsed: FoldStart; index: number } }): HTMLElement {
  const wide = (field: FieldLike) => Boolean(field.meta.wide ?? field.meta.type === 'longtext');
  const cells = props.fields.map(field => {
    const id = uid('ff');
    return html`<div class="ff" class:full=${field.meta.wide ?? (field.meta.type === 'longtext' || field.meta.type === 'richtext')} hidden=${() => !field.rules.get().applies} data-field=${field.meta.name}>
      <label id=${id}>${field.meta.label}${when(() => field.rules.get().required && !field.calculated, () => html`<span class="req" aria-hidden="true">*</span>`)}${FieldHints(field)}</label>
      <div class="ff-v">${InlineValue({ field, save: props.save, labelledBy: id, links: props.links })}</div></div>` as Node;
  });
  const grid = flowGrid(cells.map((node, index) => ({ node, wide: wide(props.fields[index]) })), props.flow);
  if (props.fold) {
    return FoldSection({ title: props.title, note: props.note, fields: props.fields, folds: props.fold.folds, collapsible: props.fold.collapsible,
      collapsed: props.fold.collapsed, index: props.fold.index, body: [grid, props.extra ?? null] });
  }
  return html`<section class="fs" aria-label=${props.title}><div class="fs-h"><h3>${props.title}</h3>${props.note ? html`<p>${props.note}</p>` : null}</div>
    ${grid}${props.extra ?? null}</section>` as HTMLElement;
}

