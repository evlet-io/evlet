// Periods: lock dates that close every record dated on or before them (Meta.period). A lock saves at once,
// for the whole instance or for one record of a scope model, and each change is in the history below.

import { html } from '../dom';
import { cell } from '../reactive';
import { Button } from '../ui/base';
import { toast } from '../ui/layers';
import { Callout, Head, stamp, type ControlsContext } from './app';
import type { Periods } from './client';
import { field, message, Select } from './people';

/** YYYY-MM-DD as DD-MM-YYYY. */
export const shown = (value: string | null) => value ? value.split('-').reverse().join('-') : 'Open';

export function PeriodsPage(ctx: ControlsContext): HTMLElement {
  const data = cell<Periods | null>(null, { name: 'periods.data' });
  const scope = cell('', { name: 'periods.scope' }), date = cell('', { name: 'periods.date' }), error = cell<string | null>(null);
  const admin = () => ctx.state.get().me.role === 'admin';
  const load = async () => { try { data.set(await ctx.api.periods()); } catch (failure) { toast(message(failure), { icon: 'info' }); } };
  void load();
  const target = () => { const [model, id] = scope.peek().split(':'); return model && id ? { model, id } : null; };
  const save = async (value: string | null) => {
    error.set(null);
    try { data.set(await ctx.api.setPeriod(target(), value)); date.set(''); toast(value ? `Locked up to ${shown(value)}` : 'Periods opened', { icon: 'lock' }); }
    catch (failure) { error.set(message(failure)); }
  };
  const choices = (): Array<[string, string]> => [['', 'All records'],
    ...(data.get()?.scopes ?? []).flatMap(item => item.records.map(record => [`${item.model}:${record.id}`, `${item.label}: ${record.title}`] as [string, string]))];
  return html`<div>
    ${Head('Periods', 'Records dated on or before a lock date are closed. Nobody can add, change, move or delete them, through the app, the API or app code.')}
    <section class="ctl-card"><div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Model</th><th>Dated by</th><th>Locked per</th></tr></thead>
      <tbody>${() => (data.get()?.models ?? []).length ? data.get()!.models.map(model => html`<tr><td>${model.label}</td><td class="mono">${model.field}</td>
        <td>${model.scope ? model.scope.label : html`<span class="muted">Whole instance</span>`}</td></tr>`)
        : html`<tr><td colspan="3"><div class="ctl-empty">No model is dated. Add <code>Meta.period = "posting_date"</code> to a model in its app.</div></td></tr>`}</tbody></table></div></section>
    <section class="ctl-card"><div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Applies to</th><th>Locked up to</th><th>Changed by</th><th>When</th></tr></thead>
      <tbody>${() => (data.get()?.locks ?? []).length ? data.get()!.locks.map(lock => html`<tr><td>${lock.label}</td><td class="num">${shown(lock.date)}</td>
        <td>${lock.changed_by ?? ''}</td><td class="num muted">${stamp(lock.changed_at)}</td></tr>`)
        : html`<tr><td colspan="4"><div class="ctl-empty">Every period is open.</div></td></tr>`}</tbody></table></div></section>
    ${() => admin() ? html`<section class="ctl-card"><div class="ctl-line">
      ${field('Applies to', 'ctl-period-scope', Select({ id: 'ctl-period-scope', value: () => scope.get(), options: choices, onChange: value => scope.set(value) }))}
      ${field('Lock up to', 'ctl-period-date', html`<input class="input" type="date" id="ctl-period-date" prop:value=${() => date.get()}
        on:input=${(event: Event) => date.set((event.target as HTMLInputElement).value)}>`, () => null, () => error.get())}
      ${Button({ label: 'Lock', variant: 'primary', disabled: () => !date.get(), onClick: () => void save(date.peek()) })}
      ${Button({ label: 'Open all periods', onClick: () => void save(null) })}</div></section>`
      : Callout('info', 'lock', 'Only an admin can move a lock date.')}
    <section class="ctl-card"><div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>When</th><th>Who</th><th>Applies to</th><th>Change</th></tr></thead>
      <tbody>${() => (data.get()?.history ?? []).length ? data.get()!.history.map(row => html`<tr><td class="num muted">${stamp(row.at)}</td><td>${row.actor}</td>
        <td>${row.target}</td><td>${row.detail}</td></tr>`)
        : html`<tr><td colspan="4"><div class="ctl-empty">No changes yet.</div></td></tr>`}</tbody></table></div></section>
  </div>` as HTMLElement;
}
