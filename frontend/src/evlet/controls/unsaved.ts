// Controls, Jobs: Unsaved changes (docs/controls.md#unsaved-changes). Counts and sizes of what people
// left unsaved on records, never what they changed; the limits go through the change bar; clearing
// old entries saves at once and is audited.

import { html, when } from '../dom';
import { cell } from '../reactive';
import { Button } from '../ui/base';
import { DialogFooter, openModal, toast } from '../ui/layers';
import { stamp, type ControlsContext } from './app';
import type { UnsavedSummary } from './client';
import { idOf } from './drafts';
import { message } from './people';

// [key, label, help, unit, smallest, largest]
const LIMITS: Array<[string, string, string, string, number, number]> = [
  ['unsaved.server_days', 'Kept on the server for', 'Older entries are deleted every day.', 'days', 1, 90],
  ['unsaved.server_entries', 'Entries per person', 'The oldest are dropped first.', 'entries', 10, 1000],
  ['unsaved.browser_days', 'New records kept in the browser for', 'Unsaved new records never reach the server.', 'days', 1, 30],
  ['unsaved.browser_entries', 'New records per person in one browser', 'One per model; the oldest go first.', 'entries', 1, 50],
  ['unsaved.entry_kb', 'Largest entry', 'A larger one stays only in the open tab.', 'KB', 16, 1024],
];

export const size = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

export function UnsavedSection(ctx: ControlsContext): HTMLElement {
  const { drafts } = ctx;
  const summary = cell<UnsavedSummary | null>(null);
  const days = cell(30);
  const id = (key: string) => idOf('setting', key);
  const load = () => void ctx.api.unsaved().then(value => summary.set(value), failure => toast(message(failure), { icon: 'info' }));
  load();
  const limit = ([key, label, help, unit, low, high]: (typeof LIMITS)[number]) => html`<div class="ctl-setrow"><div class="grow"><strong>${label}</strong><p>${help} ${low} to ${high} ${unit}.</p>
    ${when(() => drafts.errors.get().has(id(key)), () => html`<span class="ctl-err">${() => drafts.errors.get().get(id(key))}</span>`)}</div>
    <div class="ctl-setctl"><span class="ctl-addon"><input class="input num" type="number" min=${String(low)} max=${String(high)} aria-label=${label}
      prop:value=${() => String(drafts.value<number>(id(key)) ?? '')} on:change=${(event: Event) => {
        const input = event.target as HTMLInputElement, value = Math.min(high, Math.max(low, Number.parseInt(input.value, 10) || low));
        input.value = String(value);
        drafts.set(id(key), value);
      }}><span>${unit}</span></span></div></div>`;
  const clear = () => {
    const older = days.peek();
    openModal({ title: 'Clear unsaved changes?', icon: 'trash', tint: 'rose', narrow: true,
      body: () => html`<p>Everyone's unsaved changes older than ${older} days are deleted now. People can't restore them afterwards. This is recorded in the audit log.</p>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Clear', variant: 'danger', onClick: async () => {
        close();
        try { const result = await ctx.api.clearUnsaved(older); summary.set(result); toast(`Cleared ${result.cleared} ${result.cleared === 1 ? 'entry' : 'entries'}`); }
        catch (failure) { toast(message(failure), { icon: 'info' }); }
      } } }) });
  };
  return html`<section class="ctl-card ctl-gap ctl-unsaved"><div class="ctl-card-head"><div><h2>Unsaved changes</h2>
      <p>What people left unsaved on records, waiting for them to restore or discard. Only counts and sizes show here, never what was changed.</p></div></div>
    ${() => { const value = summary.get(); if (!value) return html`<div class="ctl-empty">Loading…</div>`; return html`<div class="ctl-pad">
      <p class="ctl-unsaved-total"><strong>${value.entries}</strong> ${value.entries === 1 ? 'entry' : 'entries'}, ${size(value.bytes)}${value.oldest ? `, the oldest from ${stamp(value.oldest)}` : ''}</p>
      ${value.people.length ? html`<div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Person</th><th class="r">Entries</th><th class="r">Size</th><th>Latest</th></tr></thead><tbody>
        ${value.people.map(person => html`<tr><td>${person.username}</td><td class="r num">${person.entries}</td><td class="r num">${size(person.bytes)}</td><td class="muted num">${stamp(person.latest)}</td></tr>`)}</tbody></table></div>` : null}
      <div class="ctl-line"><span>Clear entries older than</span><span class="ctl-addon"><input class="input num" type="number" min="0" max="365" aria-label="Clear entries older than"
        prop:value=${() => String(days.get())} on:change=${(event: Event) => days.set(Math.min(365, Math.max(0, Number.parseInt((event.target as HTMLInputElement).value, 10) || 0)))}><span>days</span></span>
        ${Button({ label: 'Clear', icon: 'trash', size: 'small', variant: 'soft-danger', onClick: clear })}</div></div>`; }}
    ${LIMITS.map(limit)}
  </section>` as HTMLElement;
}
