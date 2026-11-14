// /settings/ai: Eva and a person's own AI assistants. Assistants connect through /_/mcp and act as this
// person; only this person sees their requests and answers their approvals. The Eva section shows when
// Eva is on for them. The Settings shell passes its client and its local (evlet mcp) steps:
//
//   MyAIPage({ api: ctx.api, local: () => stdioSteps });

import { html, when, type Child } from '../dom';
import { ApiClient, type ClientOptions } from '../api';
import { evaStatus, type Approval, type EvaStatus } from '../eva/client';
import { cell } from '../reactive';
import { Button, StatusPill, Switch } from '../ui/base';
import { copyText, mountToasts, toast } from '../ui/layers';
import { Callout, Head, plural, stamp } from '../ui/settings-helpers';
import { ask, message } from '../ui/settings-helpers';
import '../controls/controls.css';

export interface MyAssistant { id: number; client_name: string; created_at: number; last_used: number | null; calls: number; allow_changes: boolean; apps: string[] | null }
export interface MyRequest { at: number; client_name: string; tool: string; model: string | null; kind: 'read' | 'write'; outcome: 'ok' | 'denied' | 'failed' | 'approval' }
export interface MyAI {
  enabled: boolean; allowed?: boolean; writes: boolean; address: string; clients: MyAssistant[]; requests: MyRequest[];
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

const OUTCOMES: Record<MyRequest['outcome'], [string, 'green' | 'rose' | 'slate' | 'amber']> = { ok: ['Done', 'green'], denied: ['Not allowed', 'rose'], failed: ['Failed', 'slate'], approval: ['Asked you', 'amber'] };

/** api: a MyAIClient, or any client (the Settings shell's); local: the "On this computer" section's content. */
export function MyAIPage(options: { api?: ApiClient; local?: () => Child }): HTMLElement {
  const client = options.api ?? new MyAIClient();
  const api = client instanceof MyAIClient ? client : {
    load: async () => (await client.request<MyAI>('GET', '/api/_settings/me/ai')).data,
    apps: async (apps: string[] | null) => (await client.request<MyAI>('PATCH', '/api/_settings/me/ai', { body: { apps } })).data,
    allowChanges: async (id: number, allow: boolean) => (await client.request<MyAI>('PATCH', `/api/_settings/me/ai/${id}`, { body: { allow_changes: allow } })).data,
    disconnect: async (id: number) => (await client.request<MyAI>('DELETE', `/api/_settings/me/ai/${id}`)).data,
  };
  const data = cell<MyAI | null>(null), eva = cell<EvaStatus | null>(null), approvals = cell<Approval[]>([]);
  const run = async (step: () => Promise<MyAI>, done?: string) => {
    try { data.set(await step()); if (done) toast(done); } catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  void run(() => api.load());
  // Eva (404 when it is off for this person) and the changes waiting for this person's approval.
  void evaStatus(client).then(status => eva.set(status), () => {});
  const loadApprovals = async () => {
    try { approvals.set((await client.request<{ items: Approval[] }>('GET', '/api/_tools/approvals')).data.items.filter(item => item.status === 'pending')); } catch { approvals.set([]); }
  };
  void loadApprovals();
  const answer = async (item: Approval, approve: boolean) => {
    try {
      await client.request('POST', `/api/_tools/approvals/${item.id}`, { body: { approve } });
      toast(approve ? `Approved. ${item.client ?? 'The assistant'} can run it now.` : 'Declined');
      await loadApprovals();
    } catch (failure) { toast(message(failure), { icon: 'info' }); await loadApprovals(); }
  };
  mountToasts();
  const d = () => data.get()!;
  const chosen = (app: string) => !d().chosen || d().chosen!.includes(app);
  const pick = (app: string) => {
    const next = d().apps.filter(item => item === app ? !chosen(app) : chosen(item));
    if (!next.length) return toast('Leave at least one app on, or disconnect the assistant instead', { icon: 'info' });
    void run(() => api.apps(next), 'Saved');
  };
  const disconnect = (client: MyAssistant) => ask(`Disconnect ${client.client_name}?`, 'It loses access straight away. You can connect it again any time.', 'Disconnect',
    () => run(() => api.disconnect(client.id), `${client.client_name} disconnected`), true);
  const notice = () => !d().enabled ? Callout('warn', 'lock', "Your admin has turned off AI access for this workspace, so assistants can't connect.")
    : d().writes ? Callout('info', 'info', 'AI access is on for this workspace. Assistants can make changes if you allow it below.')
      : Callout('warn', 'lock', "AI access is on for this workspace. Your admin has turned off changes, so assistants can read and search but can't create, edit or delete anything.");

  const allowanceText = (status: EvaStatus) => {
    const allowance = status.allowance, money = allowance.unit === 'aed';
    if (allowance.limit === null) return `AED ${allowance.used} used this month. No limit is set for you.`;
    return `${money ? 'AED ' : ''}${allowance.used} of ${money ? 'AED ' : ''}${allowance.limit}${money ? '' : ` ${allowance.unit}`} used this ${allowance.period}. It resets on ${allowance.resets_text ?? stamp(allowance.resets)}.`;
  };
  const approvalLine = (item: Approval) => {
    const summary = item.summary;
    const what = [summary.operation ?? summary.action ?? summary.service ?? item.tool.replace(/_/g, ' '), summary.model ? `${summary.model.replace(/_/g, ' ')}${summary.id ? ` ${summary.id}` : ''}` : ''].filter(Boolean).join(' · ');
    return html`<div class="ctl-setrow"><div class="grow"><strong>${item.client ?? 'An assistant'} wants to: ${what}</strong>
      <p>Asked ${stamp(item.created_at)}. Your approval covers this one change, exactly as asked. The assistant then calls it again.</p></div>
      <div class="ctl-setctl">${Button({ label: 'Decline', size: 'small', onClick: () => void answer(item, false) })}
        ${Button({ label: 'Approve', size: 'small', variant: 'primary', icon: 'check', onClick: () => void answer(item, true) })}</div></div>`;
  };

  return html`<div class="ctl-page">
    ${Head('AI', 'Eva and outside AI assistants work with your permissions. They can never see or change more than you can.')}
    ${() => eva.get() ? html`<section class="ctl-card"><div class="ctl-card-head"><div><h2>Eva</h2><p>Evlet's own assistant, allowed for you by your administrator</p></div>
        <a class="btn sm" href="/eva">Open Eva</a></div>
      <div class="ctl-setrow"><div class="grow"><strong>Your allowance</strong><p>${allowanceText(eva.get()!)}</p></div>
        <div class="ctl-setctl">${eva.get()!.allowance.exhausted ? StatusPill({ label: 'Used up', tint: 'amber' }) : StatusPill({ label: 'Available', tint: 'green' })}</div></div>
      ${eva.get()!.tasks ? html`<div class="ctl-setrow"><div class="grow"><strong>Scheduled tasks</strong><p>Things Eva does for you on a schedule.</p></div>
        <div class="ctl-setctl"><a class="btn sm" href="/eva/tasks">Manage</a></div></div>` : null}</section>` : null}
    ${() => approvals.get().length ? html`<section class="ctl-card" id="approvals"><div class="ctl-card-head"><div><h2>Waiting for your approval</h2>
        <p>Changes an assistant asked to make. Nothing happens until you approve.</p></div></div>${approvals.get().map(approvalLine)}</section>` : null}
    ${() => data.get() === null ? html`<div class="ctl-empty">Loading…</div>` : html`<div class="ctl-stack">${notice()}
      ${data.get()!.allowed === false ? Callout('warn', 'lock', "Your administrator hasn't turned on Allow MCP for you, so outside assistants can't connect as you.") : null}
      <section class="ctl-card pad"><h2>Connect an assistant</h2><ol class="ctl-steps">
        <li>Copy this address<div class="ctl-line"><code class="ctl-icode">${d().address}</code>${Button({ label: 'Copy', icon: 'copy', size: 'small', onClick: () => void copyText(d().address, 'the address') })}</div></li>
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
        <p>You can narrow this. You can't give an assistant more than your own access. Apps installed later stay off once you choose.</p></div></div>
        ${d().apps.map(app => html`<div class="ctl-setrow"><div class="grow"><strong>${app}</strong></div><div class="ctl-setctl">
          ${Switch({ on: chosen(app), ariaLabel: app, disabled: !d().clients.length, onChange: () => pick(app) })}</div></div>`)}
        ${d().clients.length ? null : html`<p class="muted ctl-small ctl-pad">Connect an assistant first.</p>`}</section>`)}
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Recent requests</h2><p>Eva and your assistants, the last 30 days. Only you see these.</p></div></div>
        ${d().requests.length ? html`<div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>When</th><th>Assistant</th><th>Tool</th><th>Model</th><th>Result</th></tr></thead><tbody>
          ${d().requests.map(item => html`<tr><td class="muted num">${stamp(item.at)}</td><td>${item.client_name}</td><td class="mono">${item.tool}</td>
            <td>${item.model ?? ''}</td><td>${StatusPill({ label: OUTCOMES[item.outcome]?.[0] ?? item.outcome, tint: OUTCOMES[item.outcome]?.[1] ?? 'slate' })}</td></tr>`)}</tbody></table></div>`
          : html`<div class="ctl-empty">No requests yet.</div>`}</section>
      <section class="ctl-card pad"><h2>On this computer</h2>
        ${options.local ? options.local() : html`<p class="muted ctl-small">Assistants that run on your computer can use the local connector instead. Sign in once with <code class="ctl-icode">evlet login</code>,
          then add <code class="ctl-icode">${'evlet mcp --session <file>'}</code> to the assistant's MCP settings. It follows the same workspace rules.</p>`}</section>
    </div>`}
  </div>` as HTMLElement;
}
