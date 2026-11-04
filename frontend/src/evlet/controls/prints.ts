// Print designs: the block designer. A design is ordered blocks in five regions; any block can be
// written as HTML. The preview is the PDF the server renders for the unsaved design, so what shows is
// what prints. App designs are read only here; Copy makes a stored design to change. Saving checks the
// design on the server, which owns every rule.

import { html, when } from '../dom';
import { ApiError } from '../api';
import { cell, onCleanup } from '../reactive';
import { Button, Icon } from '../ui/base';
import { toast } from '../ui/layers';
import { Callout, Head, stamp, type ControlsContext } from './app';
import type { PrintBlock, PrintDefinition, PrintItem, PrintListing } from './client';
import { message, Select } from './people';

const REGIONS = [['header', 'Header'], ['main', 'Main'], ['footnote', 'Footnote'], ['footer', 'Footer'], ['watermark', 'Watermark']] as const;
type Region = typeof REGIONS[number][0];
const BLOCK_LABELS: Record<string, string> = { heading: 'Heading', text: 'Text', fields: 'Fields', table: 'Table', totals: 'Totals',
  image: 'Image', spacer: 'Spacer', pagebreak: 'Page break', pagenumber: 'Page number' };
const FORMATS = ['Page {page} of {pages}', '{page} of {pages}', '{page}/{pages}', 'Page {page}'];

/** A new block of a type, with options a person can start from. */
function fresh(type: string, source: string): PrintBlock {
  switch (type) {
    case 'heading': return { type, text: 'Heading' };
    case 'text': return { type, text: 'Text' };
    case 'fields': return { type, names: [], columns: 2 };
    case 'table': return { type, source, columns: [] };
    case 'totals': return { type, names: [] };
    case 'spacer': return { type, height: '6mm' };
    case 'pagenumber': return { type, format: FORMATS[0], align: 'right' };
    case 'image': return { type, src: '' };
    default: return { type };
  }
}

/** Field lists as lines: "name" or "name | Label" (Fields and Totals), "name | Label | width" (Table columns). */
const toLines = (pairs: unknown) => (Array.isArray(pairs) ? pairs : []).map(item => Array.isArray(item) ? (item[1] ? `${item[0]} | ${item[1]}` : item[0])
  : typeof item === 'object' && item ? [(item as any).name, (item as any).label ?? '', (item as any).width ?? ''].join(' | ').replace(/( \| )+$/, '') : String(item)).join('\n');
const pairsOf = (text: string) => text.split('\n').map(line => line.split('|').map(part => part.trim())).filter(parts => parts[0])
  .map(parts => [parts[0], parts[1] || null]);
const columnsOf = (text: string) => text.split('\n').map(line => line.split('|').map(part => part.trim())).filter(parts => parts[0])
  .map(([name, label, width]) => ({ name, ...(label ? { label } : {}), ...(width ? { width } : {}) }));

export function PrintsPage(ctx: ControlsContext): HTMLElement {
  const listing = cell<PrintListing | null>(null), problem = cell('');
  const selected = cell<string>('');
  // An unsaved design: New, or a copy of another design.
  const draft = cell<PrintItem | null>(null);
  const load = async (pick?: string) => {
    try {
      const next = await ctx.api.prints();
      listing.set(next);
      selected.set(pick ?? (next.designs.some(item => item.name === selected.peek()) ? selected.peek() : next.designs[0]?.name ?? ''));
    } catch (failure) { problem.set(message(failure)); }
  };
  void load();
  return html`<div>
    ${Head('Print designs', 'How records and reports print to PDF: blocks in a header, main area, footnote, footer and watermark. The preview is the PDF the server makes.')}
    ${() => problem.get() ? Callout('err', 'info', problem.get()) : null}
    ${() => listing.get() && !listing.get()!.available ? Callout('warn', 'info', 'Printing is not installed on this server. Install it with pip install evlet[print]; designs can still be edited.') : null}
    ${() => {
      const data = listing.get();
      if (!data) return problem.get() ? null : html`<section class="ctl-card"><div class="ctl-empty">Loading…</div></section>`;
      return html`<div class="ev-pd">
        <aside class="ctl-card ev-pd-list" aria-label="Designs">
          <div class="ctl-card-head"><h2>Designs</h2>${Button({ label: 'New', icon: 'plus', size: 'small', onClick: () => { draft.set(null); selected.set(''); } })}</div>
          <ul>${data.designs.map(item => html`<li><button type="button" class:on=${() => selected.get() === item.name} on:click=${() => { draft.set(null); selected.set(item.name); }}>
            <span class="grow"><b>${item.definition.title || item.name}</b><span class="muted">${item.name} · ${item.definition.model ?? item.definition.report}</span></span>
            <span class="ev-pd-src">${item.source === 'app' ? 'App' : 'Stored'}</span></button></li>`)}</ul>
          ${data.designs.length ? null : html`<div class="ctl-empty">No designs yet.</div>`}
        </aside>
        ${() => {
          const item = draft.get() ?? data.designs.find(entry => entry.name === selected.get()) ?? null;
          return Editor(ctx, data, item, name => { draft.set(null); void load(name); }, () => void load(''), copy => { selected.set(''); draft.set(copy); });
        }}
      </div>`;
    }}
  </div>` as HTMLElement;
}

function Editor(ctx: ControlsContext, data: PrintListing, item: PrintItem | null, saved: (name: string) => void, removed: () => void,
  copied: (draft: PrintItem) => void): HTMLElement {
  const readOnly = item?.source === 'app';
  // A stored design keeps its name; a new one or a copy gets one before its first save.
  const unsaved = !item || (item.source === 'stored' && !item.changed_at);
  const firstModel = data.models.find(model => model.tables.length) ?? data.models[0];
  const definition: PrintDefinition = item ? structuredClone(item.definition) : {
    title: 'New design', default: false, model: firstModel?.name,
    page: { size: 'A4', orientation: 'portrait', margins: ['30mm', '14mm', '20mm', '14mm'], first_margin_top: null, font_size: '9pt', currency: null, date_format: '%d-%m-%Y' },
    header: [], header_first: null, main: [{ type: 'heading', text: 'Title' }], footer: [{ type: 'pagenumber', format: FORMATS[0], align: 'right' }], footnote: null, watermark: null,
  };
  const name = cell(item && !readOnly ? item.name : '');
  const editable = cell(!readOnly);
  const region = cell<Region>('main'), picked = cell(0), shape = cell(0);
  const previewUrl = cell(''), previewProblem = cell(''), sample = cell<string | null>(null), busy = cell(false);
  const restructure = () => { shape.set(shape.peek() + 1); later(); };
  const target = () => definition.model ? data.models.find(model => model.name === definition.model) : undefined;
  const report = () => definition.report ? data.reports.find(entry => entry.name === definition.report) : undefined;
  const blocksOf = (where: Region): PrintBlock[] => {
    if (where === 'footnote') return (definition.footnote ??= { place: 'last_page', height: null, blocks: [] }).blocks;
    if (where === 'watermark') return [];
    return definition[where];
  };

  // ---- preview: the server renders the unsaved design, half a second after the last change ----
  let timer: ReturnType<typeof setTimeout> | undefined, alive = true;
  onCleanup(() => { alive = false; clearTimeout(timer); });
  const sendable = (): PrintDefinition => {
    const copy = structuredClone(definition);
    if (copy.footnote && !copy.footnote.blocks.length) copy.footnote = null;
    return copy;
  };
  const render = async () => {
    if (!data.available) return;
    if (definition.model && sample.peek() === null) {
      try {
        const page = (await ctx.api.request<{ items: Array<{ id: string }> }>('GET', `/api/${definition.model}`, { query: { limit: 1 } })).data;
        sample.set(page.items[0]?.id ?? null);
      } catch { sample.set(null); }
      if (sample.peek() === null) { previewProblem.set('Add a record to preview this design with.'); return; }
    }
    busy.set(true);
    try {
      const result = await ctx.api.previewPrint({ definition: sendable(), ...(definition.model ? { record: sample.peek()! } : { filters: {} }) });
      if (alive) { previewUrl.set(ctx.api.url(result.url)); previewProblem.set(''); }
    } catch (failure) {
      if (alive) previewProblem.set(failure instanceof ApiError ? failure.fields.definition ?? failure.fields.record ?? failure.message : message(failure));
    } finally { if (alive) busy.set(false); }
  };
  const later = () => { clearTimeout(timer); timer = setTimeout(() => void render(), 500); };
  later();

  const save = async () => {
    if (!/^[A-Z][A-Za-z0-9]{0,63}$/.test(name.peek())) { toast('Name the design with letters and digits, starting with a capital, such as StatementPrint', { icon: 'info' }); return; }
    try {
      const result = await ctx.api.savePrint(name.peek(), sendable());
      toast(`${result.definition.title || result.name} saved`);
      saved(result.name);
    } catch (failure) { toast(failure instanceof ApiError ? failure.fields.definition ?? failure.fields.name ?? failure.message : message(failure), { icon: 'info' }); }
  };
  const remove = async () => {
    try { await ctx.api.removePrint(item!.name); toast(`${item!.name} removed`, { icon: 'trash-2' }); removed(); }
    catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  const copyDesign = () => copied({ name: `${item!.name}Copy`, source: 'stored',
    definition: { ...structuredClone(item!.definition), default: false, title: `${item!.definition.title || item!.name} (copy)` } });

  // ---- controls ----
  const text = (id: string, label: string, value: () => string, set: (value: string) => void, options: { area?: boolean; placeholder?: string; hint?: string } = {}) => html`
    <div class="ctl-field"><label for=${id}>${label}</label>
      ${options.area ? html`<textarea class="input" id=${id} rows="4" prop:value=${value()} placeholder=${options.placeholder ?? ''} disabled=${() => !editable.get()} on:input=${(event: Event) => { set((event.target as HTMLTextAreaElement).value); later(); }}></textarea>`
        : html`<input class="input" id=${id} prop:value=${value()} placeholder=${options.placeholder ?? ''} disabled=${() => !editable.get()} on:input=${(event: Event) => { set((event.target as HTMLInputElement).value); later(); }}>`}
      ${options.hint ? html`<p class="muted ev-pd-hint">${options.hint}</p>` : null}</div>`;
  const choose = (id: string, label: string, value: () => string, options: Array<[string, string]>, set: (value: string) => void, structural = false) => html`
    <div class="ctl-field"><label for=${id}>${label}</label>${Select({ id, value, options: () => options, disabled: !editable.peek(), onChange: next => { set(next); structural ? restructure() : later(); } })}</div>`;
  const range = (id: string, label: string, min: number, max: number, step: number, value: () => number, set: (value: number) => void, unit: string) => {
    const shown = cell(value());
    return html`<div class="ctl-field"><label for=${id}>${label}: <b>${() => `${shown.get()}${unit}`}</b></label>
      <input type="range" id=${id} min=${String(min)} max=${String(max)} step=${String(step)} prop:value=${String(value())} disabled=${() => !editable.get()}
        on:input=${(event: Event) => { const next = Number((event.target as HTMLInputElement).value); shown.set(next); set(next); later(); }}></div>`;
  };
  const sourceTables = () => (target()?.tables ?? []).map(table => [table.name, table.label] as [string, string]);
  const fieldHint = () => target() ? 'Fields: ' + target()!.fields.map(field => field.name).join(', ') : report() ? 'Filters: ' + report()!.fields.map(field => field.name).join(', ') : '';

  const properties = (block: PrintBlock) => {
    const htmlOn = typeof block.html === 'string';
    const own = (() => {
      switch (block.type) {
        case 'heading': case 'text': return text('pd-text', 'Text', () => String(block.text ?? ''), value => { block.text = value; },
          { area: block.type === 'text', hint: 'Use {{ doc.name }}, {{ money(doc.total) }} or {{ date(doc.posting_date) }}. Values are escaped.' });
        case 'fields': return [text('pd-names', 'Fields, one per line', () => toLines(block.names), value => { block.names = pairsOf(value); },
            { area: true, placeholder: 'name | Invoice no.', hint: fieldHint() }),
          choose('pd-cols', 'Columns', () => String(block.columns ?? 2), [['1', '1'], ['2', '2'], ['3', '3'], ['4', '4']], value => { block.columns = Number(value); })];
        case 'totals': return text('pd-totals', 'Figures, one per line', () => toLines(block.names), value => { block.names = pairsOf(value); },
          { area: true, placeholder: 'grand_total | Total', hint: report() ? 'Report columns with totals: ' + report()!.columns.map(field => field.name).join(', ') : fieldHint() });
        case 'table': return [report() ? null : choose('pd-source', 'Rows from', () => String(block.source ?? ''), sourceTables(), value => { block.source = value; }),
          text('pd-columns', 'Columns, one per line (empty for every field)', () => toLines(block.columns), value => { block.columns = columnsOf(value); },
            { area: true, placeholder: 'item | Item | 45%', hint: report() ? 'Columns: ' + report()!.columns.map(field => field.name).join(', ')
              : 'Fields: ' + ((target()?.tables ?? []).find(table => table.name === block.source)?.fields ?? []).map(field => field.name).join(', ') })];
        case 'spacer': return text('pd-height', 'Height', () => String(block.height ?? '6mm'), value => { block.height = value; }, { placeholder: '6mm' });
        case 'pagenumber': return text('pd-format', 'Format', () => String(block.format ?? FORMATS[0]), value => { block.format = value; },
          { hint: 'Use {page} and {pages}: ' + FORMATS.join(', ') + '. {0} and {1} also work.' });
        case 'image': return html`<div class="ctl-field"><label for="pd-image">Image</label>
          <input type="file" id="pd-image" accept="image/png,image/jpeg,image/svg+xml" disabled=${() => !editable.get()} on:change=${(event: Event) => readImage(event, src => { block.src = src; later(); })}>
          ${block.src ? html`<img class="ev-pd-thumb" src=${String(block.src)} alt="">` : null}</div>`;
        default: return null;
      }
    })();
    return html`<div class="ev-pd-props">
      <label class="ev-pd-switch"><input type="checkbox" checked=${htmlOn} disabled=${() => !editable.get()} on:change=${(event: Event) => {
        if ((event.target as HTMLInputElement).checked) block.html = block.type === 'text' || block.type === 'heading' ? `<p>${String(block.text ?? '')}</p>` : '<div></div>';
        else delete block.html;
        restructure();
      }}> Write this block as HTML</label>
      ${htmlOn ? text('pd-html', 'HTML (Jinja)', () => String(block.html ?? ''), value => { block.html = value; },
        { area: true, hint: 'Values are escaped and only fields the reader may see are there. The block keeps its place and spacing.' }) : own}
      <div class="ev-pd-row">
        ${choose('pd-align', 'Align', () => String(block.align ?? ''), [['', 'Default'], ['left', 'Left'], ['center', 'Center'], ['right', 'Right'], ['justify', 'Justify']], value => { if (value) block.align = value; else delete block.align; })}
        ${choose('pd-weight', 'Weight', () => String(block.weight ?? ''), [['', 'Default'], ['normal', 'Normal'], ['bold', 'Bold']], value => { if (value) block.weight = value; else delete block.weight; })}
      </div>
      <div class="ev-pd-row">
        ${text('pd-size', 'Size', () => String(block.size ?? ''), value => { if (value) block.size = value; else delete block.size; }, { placeholder: '9pt' })}
        ${text('pd-before', 'Space before', () => String(block.space_before ?? ''), value => { if (value) block.space_before = value; else delete block.space_before; }, { placeholder: '4mm' })}
      </div>
      ${text('pd-show', 'Show when', () => String(block.show_if ?? ''), value => { if (value) block.show_if = value; else delete block.show_if; }, { placeholder: "doc.status == 'draft'" })}
      <label class="ev-pd-switch"><input type="checkbox" checked=${block.break_before === 'page'} disabled=${() => !editable.get()}
        on:change=${(event: Event) => { if ((event.target as HTMLInputElement).checked) block.break_before = 'page'; else delete block.break_before; later(); }}> Start on a new page</label>
    </div>`;
  };

  const watermarkPanel = () => {
    const mark = definition.watermark;
    const kind = mark ? (mark.image !== undefined ? 'image' : mark.html !== undefined ? 'html' : 'text') : 'none';
    const setKind = (next: string) => {
      const base = { size: mark?.size ?? 55, opacity: mark?.opacity ?? 0.1, rotate: mark?.rotate ?? -30, ...(mark?.show_if ? { show_if: mark.show_if } : {}) };
      definition.watermark = next === 'none' ? null : next === 'text' ? { ...base, text: 'DRAFT' } : next === 'html' ? { ...base, html: '<b>{{ doc.name }}</b>' } : { ...base, image: '' };
    };
    return html`<div class="ev-pd-props">
      ${choose('pd-wm', 'Watermark', () => kind, [['none', 'None'], ['text', 'Text'], ['image', 'Image'], ['html', 'HTML']], setKind, true)}
      ${mark && kind === 'text' ? text('pd-wm-text', 'Text', () => mark.text ?? '', value => { mark.text = value; }) : null}
      ${mark && kind === 'html' ? text('pd-wm-html', 'HTML (Jinja)', () => mark.html ?? '', value => { mark.html = value; }, { area: true }) : null}
      ${mark && kind === 'image' ? html`<div class="ctl-field"><label for="pd-wm-image">Image</label><input type="file" id="pd-wm-image" accept="image/png,image/jpeg,image/svg+xml"
        disabled=${() => !editable.get()} on:change=${(event: Event) => readImage(event, src => { mark.image = src; later(); })}></div>` : null}
      ${mark ? [range('pd-wm-size', 'Size', 5, 100, 1, () => mark.size, value => { mark.size = value; }, '% of the page width'),
        range('pd-wm-opacity', 'Opacity', 0.02, 0.6, 0.02, () => mark.opacity, value => { mark.opacity = value; }, ''),
        range('pd-wm-rotate', 'Angle', -90, 90, 5, () => mark.rotate, value => { mark.rotate = value; }, '°'),
        text('pd-wm-show', 'Show when', () => mark.show_if ?? '', value => { if (value) mark.show_if = value; else delete mark.show_if; }, { placeholder: "doc.status == 'draft'" })] : null}
      <p class="muted ev-pd-hint">The watermark repeats behind the content of every page. The preview follows each change.</p>
    </div>`;
  };

  const blockList = () => {
    shape.get();
    const where = region.get();
    if (where === 'watermark') return watermarkPanel();
    const list = blocksOf(where);
    const index = Math.min(picked.get(), list.length - 1);
    const move = (from: number, to: number) => { const [block] = list.splice(from, 1); list.splice(to, 0, block); picked.set(to); restructure(); };
    const add = (type: string) => { list.push(fresh(type, sourceTables()[0]?.[0] ?? 'rows')); picked.set(list.length - 1); restructure(); };
    const types = Object.keys(BLOCK_LABELS).filter(type => where === 'main' || !['table', 'totals', 'pagebreak'].includes(type));
    return html`<div class="ev-pd-blocks">
      ${where === 'footnote' ? html`<div class="ev-pd-row">${choose('pd-place', 'Place', () => definition.footnote?.place ?? 'last_page',
        [['last_page', 'Bottom of the last page'], ['every_page', 'Bottom of every page']], value => { definition.footnote!.place = value; definition.footnote!.height = value === 'every_page' ? definition.footnote!.height ?? '12mm' : null; }, true)}
        ${definition.footnote?.place === 'every_page' ? text('pd-place-h', 'Height', () => definition.footnote!.height ?? '12mm', value => { definition.footnote!.height = value; }) : null}</div>` : null}
      <ol class="ev-pd-items">${list.map((block, position) => html`<li class:on=${position === index}>
        <button type="button" class="grow" on:click=${() => { picked.set(position); shape.set(shape.peek() + 1); }}>
          <b>${BLOCK_LABELS[block.type] ?? block.type}</b><span class="muted">${typeof block.html === 'string' ? 'HTML' : summary(block)}</span></button>
        ${editable.peek() ? [html`<button type="button" class="icon-btn" aria-label="Move up" disabled=${position === 0} on:click=${() => move(position, position - 1)}>${Icon('arrow-up', { size: 'sm' })}</button>`,
          html`<button type="button" class="icon-btn" aria-label="Move down" disabled=${position === list.length - 1} on:click=${() => move(position, position + 1)}>${Icon('arrow-down', { size: 'sm' })}</button>`,
          html`<button type="button" class="icon-btn" aria-label="Remove" on:click=${() => { list.splice(position, 1); picked.set(Math.max(0, position - 1)); restructure(); }}>${Icon('trash-2', { size: 'sm' })}</button>`] : null}
      </li>`)}</ol>
      ${list.length ? null : html`<p class="muted">No blocks in this region.</p>`}
      ${editable.peek() ? html`<div class="ev-pd-add">${Select({ id: 'pd-add', label: 'Add a block', value: () => '', options: () => [['', 'Add a block…'], ...types.map(type => [type, BLOCK_LABELS[type]] as [string, string])],
        onChange: value => { if (value) add(value); } })}</div>` : null}
      ${list[index] ? properties(list[index]) : null}
    </div>`;
  };

  const page = definition.page;
  const sources: Array<[string, string]> = [...data.models.map(model => [`model:${model.name}`, `${model.label} (record)`] as [string, string]),
    ...data.reports.map(entry => [`report:${entry.name}`, `${entry.title} (report)`] as [string, string])];
  return html`<section class="ctl-card ev-pd-editor" aria-label="Design">
    <div class="ctl-card-head"><div><h2>${item && !unsaved ? item.definition.title || item.name : 'New design'}</h2>
      <p>${item?.source === 'app' ? 'From the app. Copy it to make a design you can change.' : item?.changed_at ? `Saved by ${item.changed_by} · ${stamp(item.changed_at)}` : 'Not saved yet.'}</p></div>
      <div class="ev-pd-actions">${() => editable.get() ? [Button({ label: 'Save', icon: 'check', variant: 'primary', onClick: () => void save() }),
        item?.source === 'stored' && !unsaved ? Button({ label: 'Remove', icon: 'trash-2', onClick: () => void remove() }) : null]
        : Button({ label: 'Copy', icon: 'copy', onClick: copyDesign })}</div></div>
    <div class="ev-pd-grid">
      <div class="ev-pd-form">
        ${when(() => unsaved, () => text('pd-name', 'Name', () => name.peek(), value => name.set(value), { placeholder: 'StatementPrint', hint: 'Letters and digits, starting with a capital. Routes and the Print menu use it.' }))}
        ${text('pd-title', 'Title', () => definition.title, value => { definition.title = value; })}
        ${choose('pd-source-of', 'Prints', () => definition.model ? `model:${definition.model}` : `report:${definition.report}`, sources, value => {
          const [kind, which] = value.split(':');
          delete definition.model; delete definition.report; delete definition.roles;
          if (kind === 'model') definition.model = which; else { definition.report = which; definition.roles = data.reports.find(entry => entry.name === which)?.roles; }
          sample.set(null);
        }, true)}
        <label class="ev-pd-switch"><input type="checkbox" checked=${definition.default} disabled=${() => !editable.get()} on:change=${(event: Event) => { definition.default = (event.target as HTMLInputElement).checked; }}> The default design</label>
        <div class="ev-pd-row">
          ${choose('pd-size-of', 'Paper', () => String(page.size), ['A4', 'A5', 'A3', 'Letter', 'Legal'].map(size => [size, size] as [string, string]), value => { page.size = value; })}
          ${choose('pd-orient', 'Orientation', () => page.orientation, [['portrait', 'Portrait'], ['landscape', 'Landscape']], value => { page.orientation = value; })}
        </div>
        ${text('pd-margins', 'Margins: top, right, bottom, left', () => page.margins.join(' '), value => { const parts = value.trim().split(/\s+/); if (parts.length === 4) page.margins = parts; }, { placeholder: '30mm 14mm 20mm 14mm' })}
        <div class="seg ev-pd-regions" role="tablist" aria-label="Region">${REGIONS.map(([key, label]) => html`<button type="button" role="tab"
          class:on=${() => region.get() === key} aria-selected=${() => String(region.get() === key)} on:click=${() => { region.set(key); picked.set(0); }}>${label}</button>`)}</div>
        ${blockList}
      </div>
      <div class="ev-pd-preview">
        <div class="ev-pd-preview-head"><b>Preview</b><span class="muted">${() => busy.get() ? 'Rendering…' : definition.model ? sample.get() ? `With record ${sample.get()}` : '' : 'With the report’s default filters'}</span></div>
        ${() => previewProblem.get() ? Callout('warn', 'info', previewProblem.get()) : null}
        ${() => previewUrl.get() ? html`<iframe class="ev-pd-frame" title="PDF preview" src=${previewUrl.get()}></iframe>`
          : data.available ? html`<div class="ctl-empty">The preview shows here.</div>` : html`<div class="ctl-empty">Install the print extra to preview.</div>`}
      </div>
    </div>
  </section>` as HTMLElement;
}

function summary(block: PrintBlock): string {
  if (block.type === 'heading' || block.type === 'text') return String(block.text ?? '').slice(0, 40);
  if (block.type === 'fields' || block.type === 'totals') return (Array.isArray(block.names) ? block.names : []).map(item => Array.isArray(item) ? item[0] : item).join(', ');
  if (block.type === 'table') return String(block.source ?? '');
  if (block.type === 'pagenumber') return String(block.format ?? '');
  if (block.type === 'spacer') return String(block.height ?? '');
  return '';
}

/** An uploaded image as a data: URI; stored designs keep their images inside the design. */
function readImage(event: Event, done: (src: string) => void) {
  const input = event.target as HTMLInputElement, file = input.files?.[0];
  if (!file) return;
  if (file.size > 150_000) { toast('Use an image up to 150 KB', { icon: 'info' }); input.value = ''; return; }
  const reader = new FileReader();
  reader.onload = () => done(String(reader.result));
  reader.readAsDataURL(file);
}
