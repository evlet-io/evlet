// /settings/ai: a person's own AI assistants, mounted by the Settings shell. They connect through
// /_/mcp and act as this person; only this person sees their requests.
//
//   mount(element, () => MyAIPage({}));

import { html, when } from '../dom';
import { ApiClient, type ClientOptions } from '../api';
import { cell } from '../reactive';
import { Button, StatusPill, Switch } from '../ui/base';
import { copyText, mountToasts, toast } from '../ui/layers';
import { Callout, Head, plural, stamp } from '../controls/app';
import { ask, message } from '../controls/people';
import '../controls/controls.css';

export interface MyAssistant { id: number; client_name: string; created_at: number; last_used: number | null; calls: number; allow_changes: boolean; apps: string[] | null }
export interface MyRequest { at: number; client_name: string; tool: string; model: string | null; kind: 'read' | 'write'; outcome: 'ok' | 'denied' | 'failed' }
export interface MyAI {
  enabled: boolean; writes: boolean; address: string; clients: MyAssistant[]; requests: MyRequest[];
  /** Apps this person can use, and the ones assistants are narrowed to (null: all of them). */
  apps: string[]; chosen: string[] | null;
}

/** /api/_settings/me/ai: signed-in sessions only; an assistant or key can't change its own access. */
export class MyAIClient extends ApiClient {
  constructor(options: ClientOptions = {}) { super(options); }
  async load() { return (await this.request<MyAI>('GET', '/api/_settings/me/ai')).data; }
  async apps(apps: string[] | null) { return (await this.request<MyAI>('PATCH', '/api/_settings/me/ai', { body: { apps } })).data; }
  async allowChanges(id: number, allow: boolean) { return (await this.request<MyAI>('PATCH', `/api/_settings/me/ai/${id}`, { body: { allow_changes: allow } })).data; }
  async disconnect(id: number) { return (await this.request<MyAI>('DELETE', `/api/_settings/me/ai/${id}`)).data; }
}

const OUTCOMES: Record<MyRequest['outcome'], [string, 'green' | 'rose' | 'slate']> = { ok: ['Done', 'green'], denied: ['Not allowed', 'rose'], failed: ['Failed', 'slate'] };

export function MyAIPage(options: { api?: MyAIClient }): HTMLElement {
  const api = options.api ?? new MyAIClient();
  const data = cell<MyAI | null>(null);
  const run = async (step: () => Promise<MyAI>, done?: string) => {
    try { data.set(await step()); if (done) toast(done); } catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  void run(() => api.load());
  mountToasts();
  const d = () => data.get()!;
  const chosen = (app: string) => !d().chosen || d().chosen!.includes(app);
  const pick = (app: string) => {
    const next = d().apps.filter(item => item === app ? !chosen(app) : chosen(item));
    if (!next.length) return toast('Leave at least one app on, or disconnect the assistant instead', { icon: 'info' });
    void run(() => api.apps(next.length === d().apps.length ? null : next), 'Saved');
  };
  const disconnect = (client: MyAssistant) => ask(`Disconnect ${client.client_name}?`, 'It loses access straight away. You can connect it again any time.', 'Disconnect',
    () => run(() => api.disconnect(client.id), `${client.client_name} disconnected`), true);
  const notice = () => !d().enabled ? Callout('warn', 'lock', "Your admin has turned off AI access for this workspace, so assistants can't connect.")
    : d().writes ? Callout('info', 'info', 'AI access is on for this workspace. Assistants can make changes if you allow it below.')
      : Callout('warn', 'lock', "AI access is on for this workspace. Your admin has turned off changes, so assistants can read and search but can't create, edit or delete anything.");

  return html`<div class="ctl-page">
    ${Head('AI assistants', 'Use Evlet from Claude or other AI apps that support MCP. They act as you, so they see only what you can see.')}
    ${() => data.get() === null ? html`<div class="ctl-empty">Loading…</div>` : html`<div class="ctl-stack">${notice()}
      <section class="ctl-card pad"><h2>Connect an assistant</h2><ol class="ctl-steps">
        <li>Copy this address<div class="ctl-line"><code class="ctl-code">${d().address}</code>${Button({ label: 'Copy', icon: 'copy', size: 'small', onClick: () => void copyText(d().address, 'the address') })}</div></li>
        <li>In Claude, open <strong>Settings → Connectors</strong>, choose <strong>Add custom connector</strong> and paste the address.</li>
        <li>Sign in with your Evlet account when asked, and approve. The assistant shows up below.</li></ol></section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Your assistants</h2>${d().clients.length ? null : html`<p>None connected yet.</p>`}</div></div>
        ${d().clients.length ? d().clients.map(client => html`<div class="ctl-setrow"><div class="grow"><strong>${client.client_name}</strong>
          <p>Connected ${stamp(client.created_at)} · ${client.last_used ? `last used ${stamp(client.last_used)}` : 'not used yet'} · ${plural(client.calls, 'request')}</p></div>
          <div class="ctl-setctl"><span class="muted ctl-small-cell">Can make changes</span>${Switch({ on: client.allow_changes, ariaLabel: 'Can make changes', disabled: !d().writes,
            onChange: next => void run(() => api.allowChanges(client.id, next), next ? 'Changes allowed' : 'Changes turned off') })}
          ${Button({ label: 'Disconnect', size: 'small', variant: 'soft-danger', onClick: () => disconnect(client) })}</div></div>`)
          : html`<div class="ctl-empty">Follow the steps above to connect one.</div>`}</section>
      ${when(() => d().apps.length > 0, () => html`<section class="ctl-card"><div class="ctl-card-head"><div><h2>What assistants can use</h2>
        <p>You can narrow this. You can't give an assistant more than your own access.</p></div></div>
        ${d().apps.map(app => html`<div class="ctl-setrow"><div class="grow"><strong>${app}</strong></div><div class="ctl-setctl">
          ${Switch({ on: chosen(app), ariaLabel: app, disabled: !d().clients.length, onChange: () => pick(app) })}</div></div>`)}
        ${d().clients.length ? null : html`<p class="muted ctl-small ctl-pad">Connect an assistant first.</p>`}</section>`)}
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Recent requests</h2><p>Only you see these</p></div></div>
        ${d().requests.length ? html`<div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>When</th><th>Assistant</th><th>Tool</th><th>Model</th><th>Result</th></tr></thead><tbody>
          ${d().requests.map(item => html`<tr><td class="muted num">${stamp(item.at)}</td><td>${item.client_name}</td><td class="mono">${item.tool}</td>
            <td>${item.model ?? ''}</td><td>${StatusPill({ label: OUTCOMES[item.outcome][0], tint: OUTCOMES[item.outcome][1] })}</td></tr>`)}</tbody></table></div>`
          : html`<div class="ctl-empty">No requests yet.</div>`}</section>
      <section class="ctl-card pad"><h2>On this computer</h2>
        <p class="muted ctl-small">Assistants that run on your computer can use the local connector instead. Sign in once with <code class="ctl-code">evlet login</code>,
          then add <code class="ctl-code">${'evlet mcp --session <file>'}</code> to the assistant's MCP settings. It follows the same workspace rules.</p></section>
    </div>`}
  </div>` as HTMLElement;
}
