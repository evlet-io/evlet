// Read first, edit in place: InlineValue shows a field as text and turns into its input on
// click. Enter saves, Esc cancels, leaving the field saves. Saving one field needs no Save
// button: the change is written and the record saved at once, with Undo in the toast.
// KeyValueList and FormSection lay InlineValues out for side panels and the Details tab.

import { html, when, type Child } from '../dom';
import { batch, cell, untrack } from '../reactive';
import { Icon } from './base';
import { uid } from './core';
import { phrase, labeller } from './explain';
import { displayValue, FieldControl, FieldHints, type Control, type FieldLike, type LinkSource } from './fields';
import { kindOf } from './kinds';
import { toast } from './layers';
import { tooltip } from './tooltip';

export type Save = () => Promise<boolean> | boolean;

/** The save that goes with a record store field: save the record. */
function recordSave(field: FieldLike): Save | null {
  const record = (field as any).row?.record;
  return record && typeof record.save === 'function' ? () => record.save() : null;
}

/** Why a field can't be edited, in plain words. */
export function lockReason(field: FieldLike): string {
  const meta = field.meta;
  if (field.calculated) return 'Calculated from a rule';
  if (meta.type === 'state') return 'Changes only through moves';
  if (meta.type === 'sequence') return 'Assigned by the system';
  if (field.rules.get().locked && meta.locked_when) {
    const fields = (field as any).row?.model?.fields;
    return `Locked when ${phrase(meta.locked_when, labeller(fields ?? [meta]))}`;
  }
  if (field.rules.get().locked) return 'Locked';
  if (meta.read_only) return 'This value can’t be changed here';
  const record = (field as any).row?.record;
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
  const editing = cell(false, { name: `${meta.name} (editing)` }), busy = cell(false);
  const textual = !kind.control || kind.control === 'textarea';
  let before: unknown = null, control: Control | null = null, finishing = false;

  const rest = () => html`<span class="tx-wrap">${props.display ? props.display(field) : displayValue(field, { links: props.links })}</span>`;
  const start = () => {
    if (!untrack(() => field.editable.get())) return;
    before = untrack(() => field.value.get());
    field.error.set(null);
    editing.set(true);
    control?.focus();
  };
  const stop = (focus = true) => {
    editing.set(false);
    if (focus) wrapper.querySelector<HTMLElement>('.fval')?.focus();
  };
  const cancel = () => {
    const now = untrack(() => field.value.get());
    if (JSON.stringify(now ?? null) !== JSON.stringify(before ?? null) && untrack(() => field.editable.get())) {
      try { field.set(before); } catch { /* the record moved on; keep its value */ }
    }
    field.error.set(null);
    stop();
  };
  // After a value was written: save the record, confirm with Undo, return to rest.
  const finish = async () => {
    if (finishing) return;
    const now = untrack(() => field.value.get());
    const changed = JSON.stringify(now ?? null) !== JSON.stringify(before ?? null);
    if (!changed || !save) { stop(); return; }
    finishing = true;
    busy.set(true);
    const previous = before;
    let saved = false;
    try { saved = await save(); } catch { saved = false; }
    busy.set(false);
    finishing = false;
    if (!saved) {
      if (!field.error.peek()) field.error.set('This change was not saved. Check it and try again.');
      control?.focus();
      return;
    }
    stop();
    if (props.confirm !== false) toast(`${meta.label} saved`, {
      undo: async () => {
        try { batch(() => field.set(previous)); await save(); } catch { toast(`Could not undo the change to ${meta.label}`, { icon: 'info' }); }
      },
    });
  };

  const editor = () => {
    control = FieldControl(field, {
      commitOn: 'manual', links: props.links, label: meta.label,
      // Selects, switches, links and tags write on change; save straight away.
      onCommit: textual ? undefined : () => { void finish(); },
    });
    return html`<div class="fval-edit" aria-busy=${() => busy.get() ? 'true' : null}>${control.element}${when(() => Boolean(field.error.get()), () => html`<span class="err-inline" role="alert">${() => field.error.get()}</span>`)}</div>`;
  };

  const wrapper = html`<div class="iv" data-field=${meta.name}
    on:keydown=${(event: KeyboardEvent) => {
      if (!editing.peek()) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); }
      else if (event.key === 'Enter' && textual && !(kind.control === 'textarea' && !(event.metaKey || event.ctrlKey))) {
        event.preventDefault();
        if (control?.commit()) void finish();
      }
    }}
    on:focusout=${(event: FocusEvent) => {
      const next = event.relatedTarget as Node | null;
      if (!editing.peek() || busy.peek() || (next && (wrapper.contains(next) || (next as Element).closest?.('.pop')))) return;
      if (textual) { if (control?.commit()) void finish(); }
      else if (!next || !(next as Element).closest?.('.pop')) void finish();
    }}>${when(editing, editor, () => {
      const at = html`<button type="button" class="fval" aria-labelledby=${props.labelledBy ?? null}
        aria-label=${props.labelledBy ? null : () => `Edit ${meta.label}`} on:click=${start}>${rest()}${Icon('pencil', { size: 'xs', className: 'pen' })}</button>` as HTMLElement;
      const readOnly = html`<div class="fval ro" tabindex="0" aria-labelledby=${props.labelledBy ?? null}>${rest()}</div>` as HTMLElement;
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
export function FormSection(props: { title: string; note?: string; fields: FieldLike[]; save?: Save; links?: LinkSource | null; extra?: Child }): HTMLElement {
  const cells = props.fields.map(field => {
    const id = uid('ff');
    return html`<div class="ff" class:full=${field.meta.type === 'longtext'} hidden=${() => !field.rules.get().applies} data-field=${field.meta.name}>
      <label id=${id}>${field.meta.label}${when(() => field.rules.get().required && !field.calculated, () => html`<span class="req" aria-hidden="true">*</span>`)}${FieldHints(field)}</label>
      <div class="ff-v">${InlineValue({ field, save: props.save, labelledBy: id, links: props.links })}</div></div>`;
  });
  return html`<section class="fs" aria-label=${props.title}><div class="fs-h"><h3>${props.title}</h3>${props.note ? html`<p>${props.note}</p>` : null}</div>
    ${cells.length ? html`<div class="fs-g">${cells}</div>` : null}${props.extra ?? null}</section>` as HTMLElement;
}

