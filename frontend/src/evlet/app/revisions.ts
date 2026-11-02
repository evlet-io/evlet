/** Read-only confirmed snapshots and comparisons from the shared revision contract. */
import { html, type Child } from '../dom';
import { ApiError } from '../api';
import { cell, onCleanup } from '../reactive';
import { Button } from '../ui/base';
import type { AppContext } from './context';
import { screenModel, type ScreenModel } from './meta';

export interface RevisionChange { path: string; operation: 'modified' | 'added' | 'removed' | 'reordered'; old: unknown; new: unknown }

export function revisionChanges(changes: Record<string, any>, prefix = ''): RevisionChange[] {
  const rows: RevisionChange[] = Object.entries(changes.fields ?? {}).map(([name, value]) => {
    const pair = value as { old: unknown; new: unknown };
    return { path: prefix + name, operation: 'modified', old: pair.old, new: pair.new };
  });
  for (const [table, value] of Object.entries(changes.rows ?? {})) {
    const delta = value as Record<string, any[]>;
    for (const operation of ['added', 'removed'] as const) for (const row of delta[operation] ?? []) {
      rows.push({ path: `${prefix}${table} #${row.id}`, operation, old: operation === 'removed' ? row : null, new: operation === 'added' ? row : null });
      for (const [nested, children] of Object.entries(row.children ?? {})) {
        rows.push(...revisionChanges({ rows: { [nested]: { [operation]: children } } }, `${prefix}${table} #${row.id}.`));
      }
    }
    for (const row of delta.updated ?? []) rows.push(...revisionChanges(row, `${prefix}${table} #${row.id}.`));
    for (const row of delta.positions ?? []) rows.push({ path: `${prefix}${table} #${row.id}`, operation: 'reordered', old: row.old + 1, new: row.new + 1 });
  }
  return rows;
}

const text = (value: unknown): string => {
  if (value === null || value === undefined) return 'Empty';
  if (typeof value === 'object') return Object.entries(value).filter(([key]) => key !== 'id' && key !== 'children' && !key.startsWith('evlet_')).map(([key, item]) => `${key.replaceAll('_', ' ')}: ${text(item)}`).join(', ');
  return String(value);
};

function snapshotView(ctx: AppContext, model: ScreenModel, data: Record<string, unknown>): Child {
  const fields = model.fields.filter(field => field.name in data && !field.name.startsWith('evlet_'));
  const children = data.children as Record<string, Array<Record<string, unknown>>> | undefined;
  return html`<div class="ev-revision-record"><dl>${fields.map(field => html`<dt>${field.label}</dt><dd>${text(data[field.name])}</dd>`)}</dl>
    ${(model.children ?? []).map(child => html`<section><h4>${child.label}</h4>${(children?.[child.name] ?? []).map(row => html`<article><b>${`Row #${row.id}`}</b>${snapshotView(ctx, screenModel(ctx.schema, child.model), row)}</article>`)}</section>`)}</div>`;
}

export function Revisions(ctx: AppContext, model: ScreenModel, id: number): Child {
  const total = cell(0), from = cell(1), to = cell(1), problem = cell(''), busy = cell(false);
  const snapshot = cell<Record<string, unknown> | null>(null), differences = cell<RevisionChange[] | null>(null);
  let alive = true;
  onCleanup(() => { alive = false; });
  void ctx.api.revisions(model.name, id, { limit: 1 }).then(page => {
    if (alive) { total.set(page.total); to.set(Math.max(1, page.total)); }
  }, error => { if (alive) problem.set(error instanceof ApiError ? error.message : 'Revisions could not be loaded'); });
  const load = async (compare: boolean) => {
    if (busy.peek()) return;
    problem.set(''); busy.set(true);
    try {
      if (compare) {
        const data = await ctx.api.revisionDiff(model.name, id, from.peek(), to.peek());
        if (alive) { differences.set(revisionChanges(data.changes)); snapshot.set(null); }
      } else {
        const data = await ctx.api.snapshot(model.name, id, to.peek());
        if (alive) { snapshot.set(data.document); differences.set(null); }
      }
    } catch (error) { if (alive) problem.set(error instanceof ApiError ? error.message : 'The revision could not be loaded'); }
    finally { if (alive) busy.set(false); }
  };
  return html`<section class="panel ev-revisions" aria-label="Revision history">
    <div class="panel-h"><h3>Confirmed revisions</h3><span>${() => String(total.get())}</span></div>
    <p>Snapshots preserve each confirmation. They can be viewed and compared.</p>
    <label>From revision <input aria-label="From revision" type="number" min="1" max=${() => total.get()} value=${() => from.get()} on:input=${(event: Event) => from.set(Number((event.target as HTMLInputElement).value))}></label>
    <label>To revision <input aria-label="To revision" type="number" min="1" max=${() => total.get()} value=${() => to.get()} on:input=${(event: Event) => to.set(Number((event.target as HTMLInputElement).value))}></label>
    ${Button({ label: 'View snapshot', disabled: () => !total.get() || busy.get(), onClick: () => void load(false) })}
    ${Button({ label: 'Compare revisions', disabled: () => !total.get() || busy.get(), onClick: () => void load(true) })}
    <p role="status">${() => problem.get() || (busy.get() ? 'Loading…' : total.get() ? '' : 'No confirmed revisions yet.')}</p>
    ${() => snapshot.get() ? html`<div aria-label="Immutable snapshot">${snapshotView(ctx, model, snapshot.get()!)}</div>` : null}
    ${() => differences.get() === null ? null : differences.get()!.length ? html`<table aria-label="Revision comparison"><thead><tr><th>Field or row</th><th>Change</th><th>Previous</th><th>New</th></tr></thead><tbody>
      ${differences.get()!.map(row => html`<tr><th>${row.path}</th><td>${row.operation}</td><td>${text(row.old)}</td><td>${text(row.new)}</td></tr>`)}</tbody></table>` : html`<p>No changes between these revisions.</p>`}
  </section>`;
}
