// Controls → AI → Tool log: every tool call from MCP clients and the API (and, with Eva, from Eva),
// with redacted inputs, newest first. Record changes are also in each record's Activity.

import { html, when } from '../dom';
import { cell } from '../reactive';
import { Button, StatusPill } from '../ui/base';
import { toast } from '../ui/layers';
import { Callout, Head, stamp, type ControlsContext } from './app';
import type { ToolCall } from './client';
import { message } from './people';

const THROUGH: Record<string, [string, any]> = { eva: ['Eva', 'violet'], schedule: ['Scheduled task', 'teal'], mcp: ['MCP', 'blue'], api: ['API', 'slate'] };
const OUTCOME: Record<string, [string, any]> = { ok: ['Done', 'green'], denied: ['Refused', 'rose'], failed: ['Failed', 'rose'], approval: ['Awaiting approval', 'amber'] };

export function ToolLogPage(ctx: ControlsContext): HTMLElement {
  const items = cell<ToolCall[]>([]), next = cell<number | null>(null), origin = cell(''), outcome = cell(''), query = cell(''), open = cell<number | null>(null), loaded = cell(false);
  const load = async (more = false) => {
    try {
      const page = await ctx.api.toolCalls({ origin: origin.peek() || undefined, outcome: outcome.peek() || undefined, q: query.peek() || undefined, before: more ? next.peek() ?? undefined : undefined });
      items.set(more ? [...items.peek(), ...page.items] : page.items);
      next.set(page.next);
    } catch (failure) { toast(message(failure), { icon: 'info' }); }
    loaded.set(true);
  };
  void load();
  let timer: ReturnType<typeof setTimeout> | null = null;
  const filter = (target: typeof origin) => (event: Event) => { target.set((event.target as HTMLInputElement).value); if (timer) clearTimeout(timer); timer = setTimeout(() => void load(), 200); };
  const who = (call: ToolCall) => THROUGH[call.origin] ?? [call.origin, 'slate'];
  return html`<div>
    ${Head('Tool log', 'Every tool call from Eva, MCP clients, the API and scheduled tasks: who, through what, which records, and whether it was allowed. Inputs are redacted. Kept 90 days.')}
    <div class="toolbar"><label class="ctl-search"><input class="input" placeholder="Search person, tool, model or client" aria-label="Search the log" on:input=${filter(query)}></label>
      <select class="input" aria-label="Through" on:change=${filter(origin)}><option value="">Through: all</option>${Object.entries(THROUGH).map(([key, [label]]) => html`<option value=${key}>${label}</option>`)}</select>
      <select class="input" aria-label="Outcome" on:change=${filter(outcome)}><option value="">Outcome: all</option>${Object.entries(OUTCOME).map(([key, [label]]) => html`<option value=${key}>${label}</option>`)}</select></div>
    <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>When</th><th>Person</th><th>Through</th><th>Tool</th><th>Target</th><th>Outcome</th><th class="r">Time</th></tr></thead>
      <tbody>${() => items.get().length ? items.get().map(call => html`<tr class="ctl-click" tabindex="0" on:click=${() => open.set(open.peek() === call.id ? null : call.id)}>
        <td class="num muted">${stamp(call.at)}</td><td>${call.username ?? ''}</td>
        <td>${StatusPill({ label: call.client && call.origin === 'mcp' ? `MCP · ${call.client}` : who(call)[0], tint: who(call)[1] })}</td>
        <td class="mono">${call.tool}</td><td>${call.model ? `${call.model.replace(/_/g, ' ')}${call.record_id ? ` ${call.record_id}` : ''}` : ''}</td>
        <td>${StatusPill({ label: OUTCOME[call.outcome]?.[0] ?? call.outcome, tint: OUTCOME[call.outcome]?.[1] ?? 'slate' })}</td>
        <td class="r num">${call.duration_ms === null ? '' : `${call.duration_ms} ms`}</td></tr>
        ${() => open.get() === call.id ? html`<tr class="ctl-sub"><td colspan="7"><dl class="ctl-kv">
          ${call.client ? html`<dt>Client</dt><dd>${call.client}</dd>` : null}${call.run_id ? html`<dt>Eva run</dt><dd class="num">${call.run_id}</dd>` : null}
          <dt>Inputs</dt><dd><code class="ctl-icode">${JSON.stringify(call.inputs ?? {})}</code></dd><dt>Kind</dt><dd>${call.kind === 'write' ? 'Change' : 'Read'}</dd>
          ${call.error ? html`<dt>Reason</dt><dd>${call.error}</dd>` : null}</dl></td></tr>` : null}`)
        : html`<tr><td colspan="7"><div class="ctl-empty">${() => loaded.get() ? 'No tool calls match.' : 'Loading…'}</div></td></tr>`}</tbody></table>
      ${when(() => next.get() !== null, () => html`<div class="ctl-tfoot">${Button({ label: 'Show older', size: 'small', onClick: () => void load(true) })}</div>`)}</div>
    ${Callout('info', 'info', "Record changes are also in each record's Activity, and settings changes in the Audit log.")}
  </div>` as HTMLElement;
}
