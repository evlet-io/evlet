// Import (CSV) on a list whose model declares Meta.importable (docs/model-engine.md#csv-import). The person picks
// a file, maps its columns to fields (a saved mapping and matching names fill it in), optionally names a key
// column that groups rows into one record with its Table rows, checks the file (a dry run that writes nothing),
// then imports it: every record is created in one transaction, or none. Failed rows come back as an error CSV.

import { html } from '../dom';
import { batch, cell } from '../reactive';
import { Button, Checkbox, InfoNote } from '../ui/base';
import { DialogFooter, openModal, toast } from '../ui/layers';
import type { AppContext } from './context';
import type { ImportInfo, ImportResult } from './client';

/** The first record of a CSV text (its header), read with quotes as spreadsheets write them. */
export function csvHeader(text: string): string[] {
  const source = text.replace(/^﻿/, '');
  const cells: string[] = [];
  let value = '', quoted = false;
  for (let index = 0; index < source.length; index++) {
    const char = source[index];
    if (quoted) {
      if (char === '"' && source[index + 1] === '"') { value += '"'; index++; }
      else if (char === '"') quoted = false;
      else value += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') { cells.push(value.trim()); value = ''; }
    else if (char === '\n' || char === '\r') { if (value || cells.length) break; }
    else value += char;
  }
  if (value || cells.length) cells.push(value.trim());
  return cells;
}

/** A starting mapping: the saved one for the columns the file still has, else a field whose label or path matches. */
export function guessMapping(header: string[], info: ImportInfo): Record<string, string> {
  const result: Record<string, string> = {};
  const known = new Set(info.columns.map(column => column.path));
  const plain = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, '');
  for (const name of header) {
    const saved = info.saved?.mapping[name];
    if (saved && known.has(saved)) { result[name] = saved; continue; }
    const found = info.columns.find(column => [column.label, column.path, column.path.split('.').pop() ?? ''].some(text => plain(text) === plain(name)));
    if (found && !Object.values(result).includes(found.path)) result[name] = found.path;
  }
  return result;
}

const label = (info: ImportInfo, path: string) => {
  const column = info.columns.find(item => item.path === path);
  if (!column) return path;
  return column.table ? `${column.table.replace(/_/g, ' ')}: ${column.label}` : column.label;
};

export async function openImport(ctx: AppContext, model: string, title: string, onDone: () => void) {
  let info: ImportInfo;
  try { info = await ctx.api.importInfo(model); }
  catch (error) { toast(error instanceof Error ? error.message : 'Import is unavailable.', { tone: 'error' }); return; }
  const text = cell(''), fileName = cell(''), header = cell<string[]>([]);
  const mapping = cell<Record<string, string>>({});
  const key = cell<string>(info.saved?.key ?? '');
  const remember = cell(true);
  const result = cell<ImportResult | null>(null);
  const busy = cell(false), note = cell('');
  const tables = () => Object.values(mapping.get()).some(path => path.startsWith('children.'));

  const choose = async (event: Event) => {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    if (file.size > info.limits.characters * 4) { note.set('This file is too large to import at once. Split it.'); return; }
    const content = await file.text();
    const names = csvHeader(content);
    batch(() => {
      text.set(content); fileName.set(file.name); header.set(names); mapping.set(guessMapping(names, info)); result.set(null);
      note.set(names.length ? '' : 'The file has no header row.');
      if (key.peek() && !names.includes(key.peek())) key.set('');
    });
  };
  const send = async (dryRun: boolean, close: () => void) => {
    busy.set(true); note.set('');
    try {
      const answer = await ctx.api.importCsv(model, { csv: text.peek(), mapping: mapping.peek(), key: tables() && key.peek() ? key.peek() : null,
        save_mapping: remember.peek() }, dryRun);
      result.set(answer);
      if (!dryRun && answer.created) { close(); toast(`Imported ${answer.created} ${answer.created === 1 ? 'record' : 'records'}`, { icon: 'check' }); onDone(); }
    } catch (error) { note.set(error instanceof Error ? error.message : 'The file could not be checked.'); }
    finally { busy.set(false); }
  };
  const download = () => {
    const csv = result.peek()?.error_csv;
    if (!csv) return;
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = `${fileName.peek().replace(/\.csv$/i, '') || model} errors.csv`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  const setPath = (column: string) => (event: Event) => {
    const value = (event.target as HTMLSelectElement).value;
    const next = { ...mapping.peek() };
    if (value) { for (const [name, path] of Object.entries(next)) if (path === value) delete next[name]; next[column] = value; }
    else delete next[column];
    batch(() => { mapping.set(next); result.set(null); });
  };

  const summary = () => {
    const answer = result.get();
    if (!answer) return null;
    const failed = answer.records - answer.valid;
    return html`<div class="ev-import-result" role="status">
      <p>${answer.dry_run ? 'Checked' : 'Not imported:'} ${answer.records} ${answer.records === 1 ? 'record' : 'records'} from ${answer.rows} ${answer.rows === 1 ? 'row' : 'rows'}.
        ${failed ? ` ${failed} ${failed === 1 ? 'has' : 'have'} errors; nothing is imported until every record passes.` : ' Every record passes.'}</p>
      ${answer.errors.length ? html`<ul class="ev-import-errors">${answer.errors.slice(0, 20).map(item =>
        html`<li><b>Line ${item.line}</b>${item.column ? ` · ${item.column}` : ''}: ${item.message}</li>`)}</ul>` : null}
      ${answer.error_csv ? Button({ label: 'Download rows with errors', icon: 'download', variant: 'ghost', size: 'small', onClick: download }) : null}</div>`;
  };

  openModal({ title: `Import ${title}`, icon: 'upload', tint: 'blue', className: 'ev-import',
    body: () => html`<div class="ev-import-body">
      <label class="ev-import-file">CSV file <input type="file" accept=".csv,text/csv" aria-label="CSV file" on:change=${choose}></label>
      ${() => header.get().length ? html`<table class="ev-import-map"><thead><tr><th>Column in the file</th><th>Field</th></tr></thead><tbody>
        ${header.get().map(column => html`<tr><td>${column}</td><td><select aria-label=${`Field for ${column}`} on:change=${setPath(column)}>
          <option value="" selected=${() => !mapping.get()[column]}>Skip</option>
          ${info.columns.map(item => html`<option value=${item.path} selected=${() => mapping.get()[column] === item.path}>${label(info, item.path)}${item.required ? ' *' : ''}</option>`)}
        </select></td></tr>`)}</tbody></table>` : InfoNote(`Choose a CSV file with a header row. Up to ${info.limits.records} records.`)}
      ${() => tables() ? html`<label class="ev-import-key">Rows with the same value in <select aria-label="Key column" on:change=${(event: Event) => key.set((event.target as HTMLSelectElement).value)}>
        <option value="" selected=${() => !key.get()}>(each row is a record)</option>
        ${header.get().map(column => html`<option value=${column} selected=${() => key.get() === column}>${column}</option>`)}</select> are one record</label>` : null}
      ${() => header.get().length ? Checkbox({ label: 'Remember this mapping', checked: remember, onChange: next => remember.set(next) }) : null}
      ${summary}
      ${() => note.get() ? html`<p class="ev-import-note" role="alert">${note.get()}</p>` : null}</div>`,
    footer: close => html`${Button({ label: 'Check', variant: 'ghost', disabled: () => busy.get() || !Object.keys(mapping.get()).length, onClick: () => void send(true, close) })}
      ${DialogFooter({ cancel: close, submit: { label: 'Import', icon: 'upload', disabled: () => busy.get() || !Object.keys(mapping.get()).length, onClick: () => void send(false, close) } })}`,
  });
}
