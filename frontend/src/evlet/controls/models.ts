// Models: field overrides, form layout and list view are drafts behind the change bar. Instance
// fields (x_) save at once, because adding or removing one changes the table after a backup.

import { html, when } from '../dom';
import { ValidationError } from '../api';
import { cell, derive, type Cell } from '../reactive';
import { Button, Checkbox, Icon, IconButton, Switch } from '../ui/base';
import { DialogFooter, openDrawer, openModal, toast } from '../ui/layers';
import { Callout, Head, plural, type ControlsContext } from './app';
import type { FieldInfo, FieldOverride, FieldProps, LayoutSection, LayoutValue, ListValue, ModelInfo, PanelHidden } from './client';
import { layoutValue } from './client';
import { dragList } from '../ui/draglist';
import { flowGrid } from '../ui/fold';
import { idOf } from './drafts';
import { field, message, Select } from './people';

type Tab = 'fields' | 'layout' | 'list' | 'source';
const TYPES = ['Text', 'LongText', 'Integer', 'Decimal', 'Currency', 'Date', 'Boolean', 'Select', 'Link'];
const PY: Record<string, string> = { text: 'Text', longtext: 'LongText', integer: 'Integer', decimal: 'Decimal', currency: 'Currency', date: 'Date',
  datetime: 'DateTime', boolean: 'Boolean', select: 'Select', link: 'Link', email: 'Email', phone: 'Phone', url: 'URL', percent: 'Percent',
  sequence: 'Sequence', tags: 'Tags', time: 'Time', duration: 'Duration', reference: 'Reference' };
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
export const slug = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^(\d)/, 'f_$1').slice(0, 38);
const sectionId = (index: number) => index === 0 ? 'main_details' : `section_${index + 1}`;

// ---------- field values ----------

export interface Effective extends FieldProps { options_add: string[]; options_hidden: string[]; changed: boolean }

/** A field as people will see it: the file value with this instance's override on top. */
export function effective(ctx: ControlsContext, model: string, name: string): Effective {
  const id = idOf('field', `${model}.${name}`);
  const file = ctx.drafts.item(id)?.file as FieldProps;
  const override = (ctx.drafts.override(id) ?? {}) as FieldOverride;
  return { ...file, options_add: [], options_hidden: [], ...override, changed: Object.keys(override).length > 0 } as Effective;
}

/** Visible options first (models.py, then added), then hidden ones. */
export function options(value: Effective) {
  const file = value.options ?? [];
  return [...file.filter(item => !value.options_hidden.includes(item)).map(item => ({ value: item, source: 'file', hidden: false })),
    ...value.options_add.map(item => ({ value: item, source: 'instance', hidden: false })),
    ...file.filter(item => value.options_hidden.includes(item)).map(item => ({ value: item, source: 'file', hidden: true }))];
}

/** Change one prop of a field override; a value equal to models.py drops the prop. */
export function setProp(ctx: ControlsContext, model: string, name: string, prop: keyof FieldOverride, value: unknown) {
  const id = idOf('field', `${model}.${name}`);
  const file = ctx.drafts.item(id)?.file as FieldProps;
  const next: Record<string, unknown> = { ...(ctx.drafts.override(id) ?? {}) as FieldOverride };
  const fileValue = prop === 'options_add' || prop === 'options_hidden' ? [] : (file as any)[prop];
  if (value === undefined || same(value, fileValue) || (prop === 'default' && value === null)) delete next[prop];
  else next[prop] = value;
  ctx.drafts.set(id, Object.keys(next).length ? next : null);
}

const fieldsOf = (model: ModelInfo) => model.fields_detail;

// ---------- the page ----------

export function ModelsPage(ctx: ControlsContext): HTMLElement {
  const { state, drafts, router } = ctx;
  const query = router.route.peek()?.query ?? {};
  const models = () => state.get().models;
  const selected = cell(models().some(item => item.name === query.model) ? query.model! : models()[0]?.name ?? '', { name: 'models.model' });
  const tab = cell<Tab>((['fields', 'layout', 'list', 'source'] as Tab[]).includes(query.tab as Tab) ? query.tab as Tab : 'fields');
  const filter = cell('');
  const model = derive(() => models().find(item => item.name === selected.get()));
  const go = (name: string, next: Tab = tab.peek()) => {
    selected.set(name); tab.set(next);
    void router.go('models', {}, { model: name, tab: next }, { replace: true });
  };
  const changed = (name: string) => {
    const saved = drafts.saved.get();
    return [...saved.keys()].some(id => (id.startsWith(`field:${name}.`) || id === `layout:${name}` || id === `list:${name}`) && drafts.overridden(id));
  };
  const list = () => {
    const shown = models().filter(item => !filter.get() || item.name.toLowerCase().includes(filter.get().toLowerCase()));
    const apps = [...new Set(shown.map(item => item.app ?? ''))];
    return apps.map(app => [html`<div class="ctl-group-label">${app || 'Models'}</div>`, shown.filter(item => (item.app ?? '') === app).map(item =>
      html`<button type="button" class="ctl-mitem" class:on=${() => selected.get() === item.name} on:click=${() => go(item.name)}>
        ${Icon('layers', { size: 'sm' })}<span class="grow">${item.name}</span>${() => changed(item.name) ? html`<span class="ctl-ovdot" title="Changed on this instance"></span>` : null}
        <span class="ctl-n">${item.fields_detail.length}</span></button>`)]);
  };
  const tabs: Array<[Tab, string, () => number | null]> = [
    ['fields', 'Fields', () => model.get()?.fields_detail.length ?? 0], ['layout', 'Form layout', () => layoutOf(ctx, model.get()!).length],
    ['list', 'List view', () => null], ['source', 'Source', () => null]];
  const body = () => {
    const current = model.get();
    if (!current) return html`<div class="ctl-empty">No models are installed.</div>`;
    switch (tab.get()) {
      case 'layout': return LayoutTab(ctx, current);
      case 'list': return ListTab(ctx, current);
      case 'source': return SourceTab(ctx, current);
      default: return FieldsTab(ctx, current);
    }
  };
  return html`<div>
    ${Head('Models', 'Every model from every installed app. Change fields, form layout and list views for this instance. App files stay as they are.')}
    <div class="ctl-split">
      <nav class="ctl-card ctl-mlist" aria-label="Models"><div class="ctl-pad"><label class="ctl-search">${Icon('search', { size: 'sm' })}
        <input class="input" placeholder="Find a model…" aria-label="Find a model" prop:value=${filter} on:input=${(event: Event) => filter.set((event.target as HTMLInputElement).value)}></label></div>
        ${list}</nav>
      <div class="ctl-min">${() => {
        const current = model.get();
        if (!current) return null;
        const custom = current.fields_detail.filter(item => item.custom).length;
        return html`<section class="ctl-card ctl-mhead"><span class="ph-icon b-blue ctl-ic">${Icon('layers', { size: 'sm' })}</span>
          <span class="grow"><strong>${current.name}</strong><span class="ctl-sub">${[current.app ?? '', `table ${current.table}`, plural(current.records, 'record'),
            custom ? plural(custom, 'instance field') : ''].filter(Boolean).join(' · ')}</span></span>
          ${state.get().instance_fields ? Button({ label: 'Add field', icon: 'plus', size: 'small', variant: 'primary', onClick: () => addField(ctx, current) }) : null}</section>`;
      }}
        <div class="ctl-tabs" role="tablist">${tabs.map(([id, label, count]) => html`<button type="button" role="tab" class:on=${() => tab.get() === id}
          aria-selected=${() => String(tab.get() === id)} on:click=${() => go(selected.peek(), id)}>${label}${() => count() === null ? null : html`<span class="tc">${count()}</span>`}</button>`)}</div>
        ${() => { selected.get(); return body(); }}</div></div></div>` as HTMLElement;
}

// ---------- fields ----------

function FieldsTab(ctx: ControlsContext, model: ModelInfo): HTMLElement {
  const toggle = (info: FieldInfo, prop: 'required' | 'read_only' | 'hidden') => () => {
    const value = effective(ctx, model.name, info.name);
    if (prop === 'hidden' && !value.hidden && value.required) return toast("A required field can't be hidden. People couldn't fill it in.", { icon: 'info' });
    if (prop === 'required' && !value.required && value.hidden) setProp(ctx, model.name, info.name, 'hidden', false);
    setProp(ctx, model.name, info.name, prop, !value[prop]);
  };
  const rule = (info: FieldInfo, prop: 'required' | 'read_only' | 'hidden', label: string) => () => {
    const value = effective(ctx, model.name, info.name);
    const locked = prop !== 'hidden' && (value as any)[prop] && (ctx.drafts.item(idOf('field', `${model.name}.${info.name}`))?.file as FieldProps)[prop];
    if (locked) return html`<span class="muted" title=${`${label} in models.py`}>${Icon('lock', { size: 'sm' })}</span>`;
    if (prop !== 'hidden' && !info.editable) return html`<span class="muted">—</span>`;
    return Switch({ on: () => effective(ctx, model.name, info.name)[prop], ariaLabel: `${label} ${info.name}`,
      disabled: prop === 'hidden' && value.required && !value.hidden, onChange: toggle(info, prop) });
  };
  const source = (info: FieldInfo) => info.custom ? html`<span class="ctl-src s-new"><i></i>This instance</span>`
    : effective(ctx, model.name, info.name).changed ? html`<span class="ctl-src s-ov"><i></i>Changed here</span>` : html`<span class="ctl-src s-file"><i></i>models.py</span>`;
  return html`<div>
    <div class="toolbar"><span class="muted ctl-small">Click a field to change its label, rules, default or options.</span><span class="grow"></span>
      <div class="ctl-legend"><span class="ctl-src s-file"><i></i>models.py</span><span class="ctl-src s-ov"><i></i>Changed here</span><span class="ctl-src s-new"><i></i>Added on this instance</span></div></div>
    <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Field</th><th>Label</th><th>Type</th><th class="c">Required</th><th class="c">Read only</th><th class="c">Hidden</th><th>Source</th><th></th></tr></thead>
      <tbody>${fieldsOf(model).map(info => html`<tr class="ctl-click" class:ctl-ovrow=${() => effective(ctx, model.name, info.name).changed} class:ctl-newrow=${info.custom}
        on:click=${(event: Event) => { if (!(event.target as Element).closest('[role="switch"]')) openField(ctx, model, info); }}>
        <td><span class="mono">${info.name}</span>${() => effective(ctx, model.name, info.name).help_text ? html`<span class="ctl-sub">${effective(ctx, model.name, info.name).help_text}</span>` : null}</td>
        <td>${() => effective(ctx, model.name, info.name).label}${() => effective(ctx, model.name, info.name).changed && 'label' in ((ctx.drafts.override(idOf('field', `${model.name}.${info.name}`)) ?? {}) as object) ? html` <span class="ctl-tag">Renamed</span>` : null}</td>
        <td>${PY[info.type] ?? info.type}<span class="ctl-sub">${() => info.target ? `→ ${info.target}` : effective(ctx, model.name, info.name).options ? plural(options(effective(ctx, model.name, info.name)).filter(item => !item.hidden).length, 'option') : info.unique ? 'unique' : ''}</span></td>
        <td class="c">${rule(info, 'required', 'Required')}</td><td class="c">${rule(info, 'read_only', 'Read only')}</td><td class="c">${rule(info, 'hidden', 'Hidden')}</td>
        <td>${() => source(info)}</td><td class="r">${Icon('chevron-right', { size: 'sm' })}</td></tr>`)}</tbody></table>
      <div class="ctl-tfoot"><span>${plural(model.fields_detail.length, 'field')} · ${model.fields_detail.filter(item => item.custom).length} added on this instance</span></div></div>
    ${Callout('info', 'info', "Changes can make a field stricter, not looser. A field that is required or read only in models.py stays that way. Hiding a required field isn't allowed, because nobody could fill it in.")}
  </div>` as HTMLElement;
}

function openField(ctx: ControlsContext, model: ModelInfo, info: FieldInfo) {
  const id = idOf('field', `${model.name}.${info.name}`);
  const value = () => effective(ctx, model.name, info.name);
  const file = ctx.drafts.item(id)!.file as FieldProps;
  const set = (prop: keyof FieldOverride, next: unknown) => setProp(ctx, model.name, info.name, prop, next);
  const text = (prop: 'label' | 'help_text' | 'tooltip' | 'placeholder', label: string, placeholder = '') => field(label, `ctl-f-${prop}`,
    html`<input class="input" id=${`ctl-f-${prop}`} placeholder=${placeholder} class:ctl-changed=${() => value()[prop] !== file[prop]} prop:value=${() => value()[prop]}
      on:change=${(event: Event) => {
        const input = event.target as HTMLInputElement, next = input.value.trim();
        if (prop === 'label' && !next) { toast("A label can't be empty", { icon: 'info' }); input.value = value().label; return; }
        set(prop, next);
      }}>`);
  const rule = (prop: 'required' | 'read_only' | 'hidden', title: string, note: () => string) => html`<div class="ctl-setrow flat">
    <div class="grow"><strong>${title}</strong><p>${note}</p></div><div class="ctl-setctl">${() => prop !== 'hidden' && file[prop]
      ? Icon('lock', { size: 'sm' }) : Switch({ on: () => value()[prop], ariaLabel: title, disabled: prop === 'hidden' ? () => value().required && !value().hidden : !info.editable,
        onChange: next => {
          if (prop === 'hidden' && next && value().required) return toast("A required field can't be hidden. People couldn't fill it in.", { icon: 'info' });
          if (prop === 'required' && next && value().hidden) set('hidden', false);
          set(prop, next);
        } })}</div></div>`;
  const newOption = cell('');
  const addOption = () => {
    const option = newOption.peek().trim().toLowerCase().replace(/\s+/g, '_');
    if (!option) return toast('Type an option first', { icon: 'info' });
    if (options(value()).some(item => item.value === option)) return toast(`${option} is already an option`, { icon: 'info' });
    set('options_add', [...value().options_add, option]);
    newOption.set('');
  };
  const hideOption = (option: string, hide: boolean) => {
    if (hide && options(value()).filter(item => !item.hidden).length <= 1) return toast('Keep at least one option shown', { icon: 'info' });
    if (hide && value().default === option) set('default', null);
    set('options_hidden', hide ? [...value().options_hidden, option] : value().options_hidden.filter(item => item !== option));
  };
  const defaultControl = () => {
    if (info.type === 'link') return html`<input class="input" disabled value="Links don't take a default">`;
    if (info.type === 'boolean' || value().options) {
      const choices: Array<[string, string]> = info.type === 'boolean' ? [['true', 'Yes'], ['false', 'No']] : options(value()).filter(item => !item.hidden).map(item => [item.value, item.value]);
      return Select({ id: 'ctl-f-default', value: () => value().default === null || value().default === undefined ? '' : String(value().default),
        options: () => [['', file.default === null ? 'None' : `As in models.py (${file.default})`], ...choices],
        onChange: next => set('default', next === '' ? null : info.type === 'boolean' ? next === 'true' : next) });
    }
    return html`<input class="input" id="ctl-f-default" placeholder=${file.default === null ? 'None' : `models.py: ${file.default}`}
      prop:value=${() => value().default === null || value().default === undefined || same(value().default, file.default) ? '' : String(value().default)}
      on:change=${(event: Event) => { const next = (event.target as HTMLInputElement).value.trim(); set('default', next || null); }}>`;
  };
  const remove = () => {
    if (ctx.state.peek().remove_fields) return toast(ctx.state.peek().remove_fields!, { icon: 'info' });
    openModal({ title: `Remove ${info.name}?`, icon: 'trash-2', tint: 'rose', narrow: true,
      body: () => html`<p class="ctl-confirm">The column is dropped from <code>${model.table}</code> after a backup. Values in ${plural(model.records, 'record')} are lost. Overrides that use the field are cleaned.</p>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Remove field', variant: 'danger', onClick: async () => {
        try {
          const result = await ctx.api.removeField(model.name, info.name);
          close(); closeDrawer();
          await ctx.reload();
          toast(result.backup ? `${info.name} removed. Backup ${result.backup} saved first.` : `${info.name} removed`);
        } catch (failure) { toast(message(failure), { icon: 'info' }); }
      } } }) });
  };
  const closeDrawer = openDrawer({ title: `${model.name}.${info.name}`, icon: 'layers', tint: info.custom ? 'teal' : 'blue', className: 'ctl-drawer',
    body: () => html`<div class="ctl-form">
      <p class="muted ctl-small">${PY[info.type] ?? info.type}${info.target ? ` → ${info.target}` : ''} · ${info.custom ? 'added on this instance' : 'from models.py'}</p>
      ${when(() => ctx.drafts.errors.get().has(id), () => Callout('err', 'info', () => ctx.drafts.errors.get().get(id)))}
      <section class="ctl-section"><h3>Display</h3><div class="ctl-form">${text('label', 'Label')}${text('help_text', 'Help text', 'Shown under the field')}
        ${text('tooltip', 'Tooltip', 'Shown from a ? at the end of the field')}
        ${text('placeholder', 'Placeholder', 'Shown inside an empty field')}</div></section>
      <section class="ctl-section"><h3>Rules</h3>
        ${rule('required', 'Required', () => file.required ? 'Required in models.py. It stays required.' : !info.editable ? 'Filled by the app or the server.' : 'New and edited records must have a value.')}
        ${when(() => value().required && !file.required && !info.custom && model.records > 0 && value().default === null, () => Callout('warn', 'info',
          `${plural(model.records, 'record')} already exist. Empty ones stay empty until someone edits them, then the value is asked for. Set a default to fill new records automatically.`))}
        ${rule('read_only', 'Read only', () => file.read_only ? 'Read only in models.py. It stays read only.' : !info.editable ? 'Filled by the app or the server.' : "Shown, but people can't change it. Actions and server code still can.")}
        ${rule('hidden', 'Hidden', () => value().required ? "A required field can't be hidden." : 'Left out of forms and lists. The value is kept and the API still returns it.')}
        ${info.editable ? field('Default for new records', 'ctl-f-default', defaultControl() as Node) : null}</section>
      ${value().options ? html`<section class="ctl-section"><h3>Options</h3><div class="ctl-card">${() => options(value()).map(option => html`<div class="ctl-optrow">
        <span class="grow"><strong class:ctl-struck=${option.hidden}>${option.value}</strong><span class="ctl-sub">${option.source === 'file' ? 'models.py' : 'Added on this instance'}</span></span>
        ${option.source === 'file' ? [html`<span class="muted ctl-small">${option.hidden ? 'Hidden' : 'Shown'}</span>`,
          Switch({ on: !option.hidden, ariaLabel: `Show ${option.value}`, onChange: next => hideOption(option.value, !next) })]
          : IconButton({ icon: 'trash-2', tip: 'Remove option', size: 'small', onClick: () => set('options_add', value().options_add.filter(item => item !== option.value)) })}</div>`)}
        <div class="ctl-optrow"><input class="input grow" placeholder="New option, for example exhibition" aria-label="New option" prop:value=${newOption}
          on:input=${(event: Event) => newOption.set((event.target as HTMLInputElement).value)} on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') addOption(); }}>
          ${Button({ label: 'Add', icon: 'plus', size: 'small', onClick: addOption })}</div></div>
        <p class="muted ctl-small">Options from models.py can be hidden from new records, not removed. Records that already use a hidden option keep it.</p></section>` : null}
      ${info.custom ? html`<section class="ctl-section"><h3>Remove</h3><div class="ctl-line"><span class="muted ctl-small grow">${() => ctx.state.get().remove_fields
        ?? `Drops the column from ${model.table} after a backup. Values in ${plural(model.records, 'record')} are lost.`}</span>
        ${Button({ label: 'Remove field', icon: 'trash-2', size: 'small', variant: 'danger', disabled: () => Boolean(ctx.state.get().remove_fields), onClick: remove })}</div></section>` : null}
    </div>`,
    footer: close => html`${() => value().changed ? Button({ label: 'Reset to models.py', icon: 'rotate-ccw', onClick: () => ctx.drafts.set(id, null) }) : null}
      <span class="grow"></span>${Button({ label: 'Done', variant: 'primary', onClick: close })}`,
  });
}

function addField(ctx: ControlsContext, model: ModelInfo) {
  const label = cell(''), name = cell(''), touched = cell(false), type = cell('Text'), help = cell(''), tip = cell(''), required = cell(false);
  const choices = cell(''), target = cell(ctx.state.peek().models[0]?.table ?? ''), fallback = cell(''), errors = cell<Record<string, string>>({}), busy = cell(false);
  const input = (id: string, value: Cell<string>, placeholder = '', after?: (next: string) => void) => html`<input class="input" id=${id} placeholder=${placeholder} autocomplete="off"
    prop:value=${value} on:input=${(event: Event) => { value.set((event.target as HTMLInputElement).value); after?.(value.peek()); errors.set({}); }}>`;
  const submit = async (close: () => void) => {
    const body: Record<string, unknown> = { name: slug(name.peek()), type: type.peek(), label: label.peek().trim(), help_text: help.peek().trim(), required: required.peek() };
    if (tip.peek().trim()) body.tooltip = tip.peek().trim();
    if (type.peek() === 'Select') body.options = choices.peek().split(',').map(item => item.trim().toLowerCase().replace(/\s+/g, '_')).filter(Boolean);
    if (type.peek() === 'Link') body.target = target.peek();
    if (fallback.peek().trim()) body.default = type.peek() === 'Boolean' ? fallback.peek() === 'true' : fallback.peek().trim();
    busy.set(true);
    try {
      const result = await ctx.api.addField(model.name, body);
      close();
      await ctx.reload();
      toast(`${result.name} added` + (result.backup ? `. Backup ${result.backup} saved first.` : ''));
    } catch (failure) {
      errors.set(failure instanceof ValidationError ? failure.fields : { _form: message(failure) });
    } finally { busy.set(false); }
  };
  const error = (key: string) => () => errors.get()[key] ?? null;
  openModal({ title: `Add a field to ${model.name}`, icon: 'plus', tint: 'teal', narrow: true,
    body: () => html`<div class="ctl-form">
      ${field('Label', 'ctl-nf-label', input('ctl-nf-label', label, 'LPO number', next => { if (!touched.peek()) name.set(slug(next)); }) as Node, () => null, error('label'))}
      ${field('Field name', 'ctl-nf-name', html`<span class="ctl-addon wide"><span>x_</span><input class="input mono" id="ctl-nf-name" prop:value=${name}
        on:input=${(event: Event) => { name.set((event.target as HTMLInputElement).value); touched.set(true); errors.set({}); }}></span>`, () => null, error('name'))}
      ${field('Type', 'ctl-nf-type', Select({ id: 'ctl-nf-type', value: () => type.get(), options: () => TYPES.map(item => [item, item]), onChange: next => type.set(next) }), () => null, error('type'))}
      ${when(() => type.get() === 'Select', () => field('Options', 'ctl-nf-options', input('ctl-nf-options', choices, 'local, export, re_export') as Node, () => 'Separate with commas.', error('options')))}
      ${when(() => type.get() === 'Link', () => field('Links to', 'ctl-nf-target', Select({ id: 'ctl-nf-target', value: () => target.get(),
        options: () => ctx.state.peek().models.map(item => [item.table, item.name]), onChange: next => target.set(next) }), () => null, error('target')))}
      ${field('Help text', 'ctl-nf-help', input('ctl-nf-help', help, 'Optional') as Node, () => null, error('help_text'))}
      ${field('Tooltip', 'ctl-nf-tip', input('ctl-nf-tip', tip, 'Optional') as Node, () => null, error('tooltip'))}
      <div class="ctl-line">${Switch({ on: required, ariaLabel: 'Required', onChange: next => required.set(next) })}<span>Required for new and edited records</span></div>
      ${when(() => required.get() || fallback.get(), () => field('Default', 'ctl-nf-default', input('ctl-nf-default', fallback, type.peek() === 'Boolean' ? 'true or false' : '') as Node,
        () => model.records ? `Fills the ${plural(model.records, 'existing record')} when the field is required.` : null, error('default')))}
      ${when(() => errors.get()._form, () => Callout('err', 'info', () => errors.get()._form))}
      ${Callout('info', 'info', () => `Saving adds the column x_${slug(name.get()) || '…'} to ${model.table} after a backup. The ${plural(model.records, 'existing record')} start empty. It belongs to this instance only, and the x_ prefix keeps it from clashing with a field an app adds later.`)}
    </div>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: () => busy.get() ? 'Adding…' : 'Add field', icon: 'plus', disabled: busy, onClick: () => void submit(close) } }),
  });
}

// ---------- form layout ----------

/** A layout value as sections plus what a list's side panel hides: layouts without it are a plain list. panel is null
 * when the layout leaves the side panel to the app. A record tab order saved before 0.4 is read past and not saved again. */
export { layoutValue } from './client';

/** The value to save: a plain list unless it sets the side panel, so other layouts keep their old shape. */
export function layoutToSave(sections: LayoutSection[], panel: PanelHidden | null = null): LayoutValue {
  return panel === null ? sections : { sections, panel };
}

type PanelKind = keyof PanelHidden;
/** Whether a section, field, tab or block shows when a list opens the record in its side panel. */
export const inPanel = (panel: PanelHidden | null, kind: PanelKind, name: string) => !(panel?.[kind] ?? []).includes(name);
/** The panel value with one element shown or hidden; kinds left empty are dropped. */
export function togglePanel(panel: PanelHidden | null, kind: PanelKind, name: string): PanelHidden {
  const names = panel?.[kind] ?? [];
  const next: PanelHidden = { ...(panel ?? {}), [kind]: names.includes(name) ? names.filter(item => item !== name) : [...names, name].sort() };
  if (!next[kind]!.length) delete next[kind];
  return next;
}
/** The record parts besides sections, fields and tabs that a side panel can leave out. */
export const PANEL_BLOCKS: Array<[string, string]> = [['summary', 'Summary strip'], ['files', 'Files'], ['info', 'Record info']];

/** The layout people see: the override (or file) with fields placed nowhere added to the last section. */
export function layoutOf(ctx: ControlsContext, model: ModelInfo): LayoutSection[] {
  const found = structuredClone(layoutValue(ctx.drafts.value<LayoutValue>(idOf('layout', model.name))).sections);
  const sections = found.length ? found : [{ title: 'Details', fields: [] }];
  const placeable = model.fields_detail.filter(info => !(ctx.drafts.item(idOf('field', `${model.name}.${info.name}`))?.file as FieldProps)?.hidden);
  const placed = new Set(sections.flatMap(section => section.fields.map(item => item.name)));
  for (const section of sections) section.fields = section.fields.filter(item => placeable.some(info => info.name === item.name));
  sections[sections.length - 1].fields.push(...placeable.filter(info => !placed.has(info.name)).map(info => ({ name: info.name, width: info.width })));
  return sections;
}

/** Move a field before another one (or to the end of a section). */
export function moveField(sections: LayoutSection[], name: string, to: number, before: string | null) {
  let moved: LayoutSection['fields'][number] | undefined;
  for (const section of sections) {
    const index = section.fields.findIndex(item => item.name === name);
    if (index >= 0) moved = section.fields.splice(index, 1)[0];
  }
  if (!moved) return sections;
  const target = sections[to].fields, at = before ? target.findIndex(item => item.name === before) : -1;
  target.splice(at < 0 ? target.length : at, 0, moved);
  return sections;
}

/** Move an item of a list before another one (or to the end). */
export function moveBefore<T>(list: T[], item: T, before: T | null): T[] {
  const rest = list.filter(entry => entry !== item), at = before === null ? -1 : rest.indexOf(before);
  rest.splice(at < 0 ? rest.length : at, 0, item);
  return rest;
}

/** Move a field one place earlier or later, across section edges. */
export function nudge(sections: LayoutSection[], name: string, step: -1 | 1) {
  const at = sections.findIndex(section => section.fields.some(item => item.name === name));
  const fields = sections[at].fields, index = fields.findIndex(item => item.name === name);
  if (index + step >= 0 && index + step < fields.length) [fields[index], fields[index + step]] = [fields[index + step], fields[index]];
  else if (sections[at + step]) {
    const [moved] = fields.splice(index, 1);
    if (step < 0) sections[at - 1].fields.push(moved); else sections[at + 1].fields.unshift(moved);
  }
  return sections;
}

/** Where a section starts in the form: open, closed, or closed when every field in it is empty. */
export const STARTS: Array<[string, string]> = [['open', 'Open'], ['closed', 'Closed'], ['empty', 'Closed when empty']];
export const startsOf = (section: LayoutSection) => !section.collapsible ? 'open' : section.collapsed === true ? 'closed' : section.collapsed === 'empty' ? 'empty' : 'open';
/** How a section orders its fields, on screen and for Tab. */
export const FLOWS: Array<[string, string]> = [['rows', 'Across, then down'], ['columns', 'Down each column']];

/** Can collapse, how the section starts, and how its fields flow. Layouts saved before these options read as
 * sections that always show and fill across. */
function sectionOptions(section: LayoutSection, index: number, change: (fn: (draft: LayoutSection[]) => void) => void): HTMLElement {
  const starts = `ctl-ls-starts-${index}`, flow = `ctl-ls-flow-${index}`;
  return html`<div class="ctl-lsec-fold">${Switch({ on: Boolean(section.collapsible), label: 'Can collapse',
    onChange: next => change(draft => {
      // A section that always shows carries neither option, as layouts saved before them.
      if (next) { draft[index].collapsible = true; draft[index].collapsed = draft[index].collapsed ?? false; }
      else { delete draft[index].collapsible; delete draft[index].collapsed; }
    }) })}
    <label class="muted ctl-small" for=${starts}>Starts</label>${Select({ id: starts, label: 'Starts', value: () => startsOf(section), options: () => STARTS, disabled: !section.collapsible,
      onChange: next => change(draft => { draft[index].collapsed = next === 'closed' ? true : next === 'empty' ? 'empty' : false; }) })}
    <label class="muted ctl-small" for=${flow}>Fields go</label>${Select({ id: flow, label: 'Fields go', value: () => section.flow ?? 'rows', options: () => FLOWS,
      onChange: next => change(draft => { if (next === 'columns') draft[index].flow = 'columns'; else delete draft[index].flow; }) })}</div>` as HTMLElement;
}

/** A stable key per section for dragging: its title, numbered when titles repeat. */
const sectionKeys = (sections: LayoutSection[]) => {
  const seen = new Map<string, number>();
  return sections.map(section => { const count = (seen.get(section.title) ?? 0) + 1; seen.set(section.title, count); return count > 1 ? `${section.title}#${count}` : section.title; });
};

function LayoutTab(ctx: ControlsContext, model: ModelInfo): HTMLElement {
  const id = idOf('layout', model.name);
  const sections = () => layoutOf(ctx, model);
  const value = () => layoutValue(ctx.drafts.value<LayoutValue>(id));
  const save = (next: LayoutSection[]) => {
    // A renamed or removed section keeps its side panel choice under its new title, or drops it.
    const panel = value().panel, before = sections();
    const renamed = panel?.sections ? { ...panel, sections: panel.sections.map(title => {
      const at = before.findIndex(section => section.title === title);
      return at >= 0 && next[at] && next.length === before.length ? next[at].title : title;
    }).filter(title => next.some(section => section.title === title)) } : panel;
    if (renamed?.sections && !renamed.sections.length) delete renamed.sections;
    ctx.drafts.set(id, layoutToSave(next, renamed));
  };
  const panelOf = () => value().panel;
  const setPanel = (kind: PanelKind, name: string) => ctx.drafts.set(id, layoutToSave(sections(), togglePanel(panelOf(), kind, name)));
  const panelToggle = (kind: PanelKind, name: string, what: string) => IconButton({ icon: 'panel-right', size: 'small',
    tip: `Show ${what} in the side panel`, active: () => inPanel(panelOf(), kind, name), onClick: () => setPanel(kind, name) });
  const change = (fn: (draft: LayoutSection[]) => void) => { const draft = sections(); fn(draft); save(draft); };
  const label = (name: string) => effective(ctx, model.name, name).label;
  const chip = (item: LayoutSection['fields'][number], index: number, position: number, count: number, total: number) => html`<div class="ctl-lf" class:full=${item.width === 12}
    class:off=${() => effective(ctx, model.name, item.name).hidden} data-field=${item.name} data-drag-item="field" data-drag-id=${item.name}>
    <button type="button" class="ctl-grip" data-drag-handle="field" aria-label=${`Move ${label(item.name)}`}
      aria-roledescription="draggable field" aria-describedby="ctl-drag-help">${Icon('grip-vertical', { size: 'xs' })}</button>
    <span class="grow"><strong>${() => label(item.name)}${() => effective(ctx, model.name, item.name).required ? html` <span class="ctl-req">*</span>` : null}</strong>
      <span class="ctl-subrow"><span class="ctl-sub mono">${item.name}${() => effective(ctx, model.name, item.name).hidden ? ' · hidden' : ''}</span>${panelToggle('fields', item.name, label(item.name))}</span></span>
    <button type="button" class="ctl-wbtn" title="Width" on:click=${() => change(draft => { draft[index].fields[position].width = item.width === 12 ? 6 : 12; })}>${item.width === 12 ? 'Full' : 'Half'}</button>
    ${IconButton({ icon: 'arrow-up', tip: 'Move earlier', size: 'small', disabled: index === 0 && position === 0, onClick: () => change(draft => nudge(draft, item.name, -1)) })}
    ${IconButton({ icon: 'arrow-down', tip: 'Move later', size: 'small', disabled: index === total - 1 && position === count - 1, onClick: () => change(draft => nudge(draft, item.name, 1)) })}</div>`;
  const editor = () => { const all = sections(), keys = sectionKeys(all); return all.map((section, index) => html`<section class="ctl-lsec" data-drag-item="section" data-drag-id=${keys[index]}>
    <div class="ctl-lsec-head"><button type="button" class="ctl-grip" data-drag-handle="section" aria-label=${`Move section ${section.title}`}
      aria-roledescription="draggable section" aria-describedby="ctl-drag-help">${Icon('grip-vertical', { size: 'xs' })}</button><span class="mono muted ctl-small">${sectionId(index)}</span>
      <input class="input" aria-label="Section title" prop:value=${section.title} on:change=${(event: Event) => {
        const input = event.target as HTMLInputElement, title = input.value.trim();
        if (!title) { toast('A section needs a title', { icon: 'info' }); input.value = section.title; return; }
        change(draft => { draft[index].title = title; });
      }}>
      ${panelToggle('sections', section.title, `section ${section.title}`)}
      ${IconButton({ icon: 'arrow-up', tip: 'Move section up', size: 'small', disabled: index === 0, onClick: () => change(draft => { [draft[index - 1], draft[index]] = [draft[index], draft[index - 1]]; }) })}
      ${IconButton({ icon: 'arrow-down', tip: 'Move section down', size: 'small', disabled: index === all.length - 1, onClick: () => change(draft => { [draft[index + 1], draft[index]] = [draft[index], draft[index + 1]]; }) })}
      ${IconButton({ icon: 'trash-2', tip: 'Remove section', size: 'small', disabled: all.length === 1, onClick: () => {
        change(draft => { const target = index > 0 ? index - 1 : 1; draft[target].fields.push(...draft[index].fields); draft.splice(index, 1); });
        toast(`Section removed. Its fields moved to ${sections()[Math.max(index - 1, 0)].title}.`);
      } })}</div>
    ${sectionOptions(section, index, change)}
    <div class="ctl-lgrid" data-drop-zone="field" data-zone=${String(index)}>${section.fields.length ? section.fields.map((item, position) => chip(item, index, position, section.fields.length, all.length))
      : html`<div class="ctl-lempty">Drop fields here</div>`}</div></section>`); };
  const preview = () => {
    const shown = sections().map(section => ({ ...section, fields: section.fields.filter(item => !effective(ctx, model.name, item.name).hidden) }));
    const hidden = sections().flatMap(section => section.fields).filter(item => effective(ctx, model.name, item.name).hidden).length;
    return [shown.filter(section => section.fields.length).map(section => html`<div class="ctl-pvsec"><h3>${section.title}${section.collapsible
      ? html` <span class="muted ctl-small">· ${STARTS.find(([key]) => key === startsOf(section))![1].toLowerCase()}</span>` : null}</h3>${flowGrid(section.fields.map(item => {
      const value = effective(ctx, model.name, item.name);
      return { wide: item.width === 12, node: html`<div class="ctl-field" class:full=${item.width === 12}><span class="lbl">${value.label}${value.required ? html` <span class="ctl-req">*</span>` : null}</span>
        <span class="input ctl-mock" class:ro=${value.read_only}>${value.placeholder || ''}</span>${value.help_text ? html`<span class="ctl-hint">${value.help_text}</span>` : null}</div>` as Node };
    }), section.flow ?? 'rows')}</div>`), hidden ? html`<p class="muted ctl-small">${plural(hidden, 'hidden field')} not shown.</p>` : null];
  };
  const element = html`<div>
    <div class="toolbar"><span class="muted ctl-small" id="ctl-drag-help">Drag fields and sections by their grip, or press Space on a grip, move with the arrow keys and press Space again. The preview updates as you go.</span><span class="grow"></span>
      ${() => ctx.drafts.overridden(id) ? Button({ label: 'Reset to file', icon: 'rotate-ccw', size: 'small', variant: 'ghost', onClick: () => ctx.drafts.reset(id) }) : null}
      ${Button({ label: 'Add section', icon: 'plus', size: 'small', onClick: () => change(draft => { draft.push({ title: `Section ${draft.length + 1}`, fields: [] }); }) })}</div>
    ${when(() => ctx.drafts.errors.get().has(id), () => Callout('err', 'info', () => ctx.drafts.errors.get().get(id)))}
    <div class="ctl-lwrap"><div class="ctl-stack"><div class="ctl-stack" data-drop-zone="section" data-zone="sections">${editor}</div>${() => PartsCard(model, panelToggle)}${() => PanelCard(panelToggle)}</div>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Preview</h2><p>${`New ${model.name.toLowerCase()} form, as people will see it`}</p></div>
        <span class="ctl-badge">${() => ctx.drafts.overridden(id) ? 'Changed here' : 'From the app'}</span></div><div class="ctl-pad">${preview}</div></section></div>
  </div>` as HTMLElement;
  // One drag helper per kind: fields between sections, and the sections themselves.
  dragList(element, { kind: 'field', label, zoneLabel: zone => sections()[Number(zone)]?.title ?? zone,
    move: (name, zone, before) => change(draft => moveField(draft, name, Number(zone), before)) });
  dragList(element, { kind: 'section', label: key => `Section ${key}`,
    move: (key, _zone, before) => change(draft => {
      const keys = sectionKeys(draft), moved = draft[keys.indexOf(key)], next = before === null ? null : draft[keys.indexOf(before)] ?? null;
      draft.splice(0, draft.length, ...moveBefore(draft, moved, next));
    }) });
  return element;
}

/** The record parts a list's side panel can leave out besides sections, fields and parts: the summary strip and blocks. */
function PanelCard(toggle: (kind: PanelKind, name: string, what: string) => HTMLElement): HTMLElement {
  return html`<section class="ctl-card ctl-panel-parts"><div class="ctl-card-head"><div><h2>Side panel</h2>
      <p>What a record shows when a list opens it in a side panel. Use the panel button on each section, field and part, and here for the rest. The full page always shows everything, and hiding is not a permission.</p></div></div>
    <div class="ctl-pad"><ul class="ctl-tablist ctl-partlist">${PANEL_BLOCKS.map(([name, label]) => html`<li class="ctl-partrow"><span class="grow">${label}</span>${toggle('blocks', name, label)}</li>`)}</ul></div></section>` as HTMLElement;
}

type PanelToggle = (kind: PanelKind, name: string, what: string) => HTMLElement;
/** The record's parts besides its sections (Activity, Revisions, child tables, Related, Postings), each with its side
 * panel button. Record pages have no tabs, so there is no order to set. */
function PartsCard(model: ModelInfo, toggle: PanelToggle): HTMLElement | null {
  const known = (model.tabs ?? []).filter(part => part.name !== 'details');
  if (!known.length) return null;
  return html`<section class="ctl-card ctl-tabs"><div class="ctl-card-head"><div><h2>Record parts</h2>
      <p>The parts of a record besides its sections. A record shows only the parts it has.</p></div></div>
    <div class="ctl-pad"><ul class="ctl-tablist">${known.map(part => html`<li class="ctl-tabrow"><span class="grow">${part.label}</span>${toggle('tabs', part.name, part.label)}</li>`)}</ul></div></section>` as HTMLElement;
}

// ---------- list view ----------

function ListTab(ctx: ControlsContext, model: ModelInfo): HTMLElement {
  const id = idOf('list', model.name);
  const value = () => ctx.drafts.value<ListValue>(id);
  const set = (next: Partial<ListValue>) => ctx.drafts.set(id, { ...value(), ...next });
  const columns = model.fields_detail.filter(info => info.stored && info.type !== 'reference').map(info => info.name);
  const order = () => [...value().columns, ...columns.filter(name => !value().columns.includes(name))];
  const label = (name: string) => effective(ctx, model.name, name).label;
  const toggle = (name: string) => {
    const current = value().columns;
    if (current.includes(name)) { if (current.length > 1) set({ columns: current.filter(item => item !== name) }); }
    else if (current.length >= 8) toast('Show at most 8 columns', { icon: 'info' });
    else set({ columns: [...current, name] });
  };
  const move = (name: string, step: number) => {
    const next = [...value().columns], at = next.indexOf(name);
    [next[at], next[at + step]] = [next[at + step], next[at]];
    set({ columns: next });
  };
  // The list's tabs: a State, Select or Link field's values, and the tab the list opens on.
  const tabFields = model.fields_detail.filter(info => info.stored && (info.type === 'select' || info.type === 'link')).map(info => info.name);
  const tabInfo = () => model.fields_detail.find(info => info.name === value().list_tabs);
  const tabChoices = (): Array<[string, string]> => {
    const info = tabInfo();
    return info && info.type === 'select' ? options(effective(ctx, model.name, info.name)).filter(item => !item.hidden).map(item => [item.value, item.value]) : [];
  };
  const sorts = () => [...columns.flatMap(name => [[name, `${label(name)} ↑`], [`-${name}`, `${label(name)} ↓`]] as Array<[string, string]>), ['-id', 'Newest first'] as [string, string]];
  return html`<div class="ctl-grid g2">
    <section class="ctl-card"><div class="ctl-card-head"><div><h2>Columns</h2><p>Shown in the list, in this order</p></div>
      ${() => ctx.drafts.overridden(id) ? Button({ label: 'Reset', icon: 'rotate-ccw', size: 'small', variant: 'ghost', onClick: () => ctx.drafts.reset(id) }) : null}</div>
      ${() => order().map(name => {
        const on = value().columns.includes(name), at = value().columns.indexOf(name);
        return html`<div class="ctl-optrow">${Checkbox({ checked: on, ariaLabel: `Show ${name}`, disabled: on && value().columns.length === 1, onChange: () => toggle(name) })}
          <span class="grow"><strong>${label(name)}</strong><span class="ctl-sub mono">${name}</span></span>
          ${on ? [IconButton({ icon: 'arrow-up', tip: 'Move up', size: 'small', disabled: at === 0, onClick: () => move(name, -1) }),
            IconButton({ icon: 'arrow-down', tip: 'Move down', size: 'small', disabled: at === value().columns.length - 1, onClick: () => move(name, 1) })] : null}</div>`;
      })}</section>
    <div class="ctl-stack">
      <section class="ctl-card ctl-pad"><div class="ctl-form">
        ${field('Rows per page', 'ctl-l-size', Select({ id: 'ctl-l-size', value: () => String(value().page_size), options: () => [['25', '25'], ['50', '50'], ['100', '100']],
          onChange: next => set({ page_size: Number(next) }) }))}
        ${field('Default sort', 'ctl-l-sort', Select({ id: 'ctl-l-sort', value: () => value().sort, options: sorts, onChange: next => set({ sort: next }) }))}
        ${field('Open records in', 'ctl-l-open', Select({ id: 'ctl-l-open', value: () => value().open_in ?? 'page', options: () => [['page', 'Full page'], ['panel', 'Side panel beside the list']],
          onChange: next => set({ open_in: next as 'page' | 'panel' }) }))}
        ${field('Tabs by', 'ctl-l-tabs', Select({ id: 'ctl-l-tabs', value: () => value().list_tabs ?? '', options: () => [['', 'No tabs'], ...tabFields.map(name => [name, label(name)] as [string, string])],
          onChange: next => set({ list_tabs: next || null, list_tab: 'all' }) }))}
        ${when(() => Boolean(value().list_tabs), () => tabInfo()?.type === 'link'
          ? field('Opens on', 'ctl-l-home', html`<input class="input" id="ctl-l-home" placeholder="All, or the title of a linked record" prop:value=${() => value().list_tab === 'all' ? '' : value().list_tab ?? ''}
              on:change=${(event: Event) => set({ list_tab: (event.target as HTMLInputElement).value.trim() || 'all' })}>`)
          : field('Opens on', 'ctl-l-home', Select({ id: 'ctl-l-home', value: () => value().list_tab ?? 'all', options: () => [['all', 'All'], ...tabChoices()],
              onChange: next => set({ list_tab: next }) })))}
        ${when(() => ctx.drafts.errors.get().has(id), () => html`<span class="ctl-err">${() => ctx.drafts.errors.get().get(id)}</span>`)}</div></section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Preview</h2><p>${() => ctx.drafts.overridden(id) ? 'Changed on this instance' : 'Same as the app'}</p></div></div>
        <div class="table-wrap"><table class="dt ctl-table"><thead><tr>${() => value().columns.map(name => html`<th>${label(name)}</th>`)}</tr></thead>
          <tbody><tr>${() => value().columns.map(() => html`<td class="muted">…</td>`)}</tr></tbody></table></div>
        <p class="muted ctl-small ctl-pad">People only see the columns their role can read.</p></section></div></div>` as HTMLElement;
}

// ---------- source ----------

const quote = (value: unknown) => typeof value === 'string' ? JSON.stringify(value) : value === true ? 'True' : value === false ? 'False' : String(value);

/** One field line of models.py, with this instance's changes when `merged`. */
export function sourceLine(ctx: ControlsContext, model: ModelInfo, info: FieldInfo, merged: boolean): { text: string; changed: boolean; added: boolean } {
  const file = ctx.drafts.item(idOf('field', `${model.name}.${info.name}`))?.file as FieldProps;
  const value = merged ? effective(ctx, model.name, info.name) : { ...file, options_add: [], options_hidden: [], changed: false };
  const args: string[] = [];
  if (info.target) args.push(info.target);
  if (value.options) args.push(`(${options(value).filter(item => !item.hidden).map(item => quote(item.value)).join(', ')})`);
  if (value.required) args.push('required=True');
  if (info.unique) args.push('unique=True');
  if (value.default !== null && value.default !== undefined) args.push(`default=${quote(value.default)}`);
  if (value.read_only) args.push('read_only=True');
  if (value.hidden) args.push('hidden=True');
  if (value.label !== file.label || !info.name.replace(/_/g, ' ').toLowerCase().startsWith(value.label.toLowerCase())) args.push(`label=${quote(value.label)}`);
  if (value.help_text) args.push(`help_text=${quote(value.help_text)}`);
  if (value.tooltip) args.push(`tooltip=${quote(value.tooltip)}`);
  if (value.placeholder) args.push(`placeholder=${quote(value.placeholder)}`);
  return { text: `    ${info.name} = ${PY[info.type] ?? info.type}(${args.join(', ')})`, changed: merged && value.changed, added: merged && info.custom };
}

function SourceTab(ctx: ControlsContext, model: ModelInfo): HTMLElement {
  const merged = cell(true);
  const code = () => {
    const lines = model.fields_detail.filter(info => merged.get() || !info.custom).map(info => sourceLine(ctx, model, info, merged.get()));
    const types = [...new Set(lines.map(line => line.text.split('= ')[1].split('(')[0]))];
    return [html`<span>${`from evlet import Model, ${types.join(', ')}\n\n\nclass ${model.name}(Model):\n`}</span>`,
      lines.map(line => line.added ? html`<span class="ctl-code-add">${line.text}  # added on this instance\n</span>`
        : line.changed ? html`<span class="ctl-code-ov">${line.text}  # changed on this instance\n</span>` : html`<span>${line.text}\n</span>`)];
  };
  return html`<div>
    <div class="toolbar"><div class="seg" role="tablist">
      <button type="button" class:on=${merged} on:click=${() => merged.set(true)}>With this instance's changes</button>
      <button type="button" class:on=${() => !merged.get()} on:click=${() => merged.set(false)}>models.py only</button></div><span class="grow"></span>
      <span class="muted ctl-small mono">${model.app ? `${model.app}/models.py` : 'models.py'}</span></div>
    <pre class="ctl-code">${code}</pre>
    <p class="muted ctl-small">A preview of the field lines with this instance's changes. Export as code on the Overrides page gives the lines to paste into the app.</p></div>` as HTMLElement;
}
