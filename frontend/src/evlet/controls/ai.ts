// MCP: the workspace switches for outside AI assistants (drafts behind the change bar), the connected
// clients (sign one out at once) and totals for the last 24 hours. The tools are the Tool Engine's,
// the same ones Eva uses; every call is in the Tool log. Each person needs Allow MCP (Users).

import { html, when, type Child } from '../dom';
import { cell } from '../reactive';
import { Button, IconButton, StatusPill, Switch } from '../ui/base';
import { copyText, DialogFooter, openModal, toast } from '../ui/layers';
import { Callout, Head, plural, stamp, type ControlsContext } from './app';
import type { McpOverview } from './client';
import { idOf } from './drafts';
import { ask, message } from './people';

export const TOOLS: Array<[string, string, boolean]> = [
  ['discover_models', 'List permitted models and operations.', false],
  ['describe_model', 'Read one model, its fields, child collections and actions.', false],
  ['list_records', 'Query permitted records with filters and sort.', false],
  ['get_record', 'Read one record and its child collections.', false],
  ['aggregate_records', 'Totals, counts and group-by over permitted records.', false],
  ['record_history', "Read a record's journal.", false],
  ['list_reports', 'List the reports this person can run.', false],
  ['run_report', 'Run a permitted report.', false],
  ['list_files', 'List the files on a record.', false],
  ['read_file', 'Read the text of a file on a record.', false],
  ['list_services', 'List business services offered to assistants.', false],
  ['job_status', 'Read the status of a queued action.', false],
  ['create_record', 'Create a record through backend validation.', true],
  ['update_record', 'Update supplied values only.', true],
  ['change_state', 'Confirm, cancel or reopen a document. Approved in Evlet.', true],
  ['run_action', 'Run one registered action on a record. Approved in Evlet.', true],
  ['call_service', 'Call a business service. Approved in Evlet.', true],
  ['delete_record', 'Delete a record and its owned children. Approved in Evlet.', true],
];

export function McpPage(ctx: ControlsContext): HTMLElement {
  const { drafts, state } = ctx;
  const id = (key: string) => idOf('setting', `mcp.${key}`);
  const get = <T>(key: string) => drafts.value<T>(id(key));
  const set = (key: string, value: unknown) => drafts.set(id(key), value);
  const on = () => get<boolean>('enabled'), writes = () => get<boolean>('allow_writes');
  const overview = cell<McpOverview | null>(null);
  const load = async () => { try { overview.set(await ctx.api.mcp()); } catch (failure) { toast(message(failure), { icon: 'info' }); } };
  void load();

  const mark = (key: string) => () => drafts.overridden(id(key)) ? html`<span class="ctl-tag">Changed</span>${IconButton({ icon: 'rotate-ccw', size: 'small',
    tip: 'Reset to the file value', onClick: () => drafts.reset(id(key)) })}` : null;
  const error = (key: string) => when(() => drafts.errors.get().has(id(key)), () => html`<span class="ctl-err">${() => drafts.errors.get().get(id(key))}</span>`);
  const row = (key: string | null, title: string, text: Child, control: Child) => html`<div class="ctl-setrow"><div class="grow">
    <strong>${title} ${key ? mark(key) : null}</strong><p>${text}</p>${key ? error(key) : null}</div><div class="ctl-setctl">${control}</div></div>`;
  const toggle = (list: 'disabled_tools' | 'hidden_models', name: string) => {
    const now = get<string[]>(list);
    set(list, now.includes(name) ? now.filter(item => item !== name) : [...now, name]);
  };
  const allowWrites = (next: boolean) => {
    if (!next) return set('allow_writes', false);
    openModal({ title: 'Allow AI clients to change data?', icon: 'info', tint: 'rose', narrow: true,
      body: () => html`<p class="ctl-confirm">Clients will be able to create, update and delete records and run actions, as the user they signed in with. Claude asks the user before each change, but other clients may not.</p>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Allow writes', variant: 'danger', onClick: () => { close(); set('allow_writes', true); } } }) });
  };
  const rate = (event: Event) => {
    const input = event.target as HTMLInputElement;
    let value = Number.parseInt(input.value, 10);
    if (Number.isNaN(value) || value < 1) value = 1;
    if (value > 60) { value = 60; toast('The framework allows at most 60 tool calls a minute', { icon: 'info' }); }
    input.value = String(value);
    set('rate', value);
  };
  const signOut = (client: McpOverview['clients'][number]) => ask(`Sign out ${client.client_name}?`,
    `${client.username}'s ${client.client_name} loses access straight away. They can connect again by signing in.`, 'Sign out', async () => {
      try { overview.set(await ctx.api.signOutAssistant(client.id)); toast('Client signed out'); }
      catch (failure) { toast(message(failure), { icon: 'info' }); }
    }, true);
  const totals = () => overview.get()?.totals ?? { reads: 0, writes: 0, denied: 0 };

  return html`<div>
    ${Head('MCP', 'Let AI clients such as Claude work with this instance. They sign in as a user with Allow MCP and see only what that user is allowed to see.')}
    <div class="ctl-grid main"><div class="ctl-stack">
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Server</h2></div>
        ${() => on() ? StatusPill({ label: 'On', tint: 'green' }) : StatusPill({ label: 'Off', tint: 'slate' })}</div>
        ${row('enabled', 'MCP server', 'Turning it off stops every client straight away.', Switch({ on, ariaLabel: 'MCP server', onChange: next => set('enabled', next) }))}
        ${row(null, 'Address', html`Remote clients connect here. Local clients can run <code class="ctl-icode">evlet mcp</code> instead.`,
          html`<code class="ctl-icode">${() => overview.get()?.address ?? ''}</code>${IconButton({ icon: 'copy', tip: 'Copy', onClick: () => void copyText(overview.peek()?.address ?? '', 'the address') })}`)}
        ${row('allow_writes', 'Allow writes', "Off by default. When on, clients can create, update and delete records and run actions, within the user's permissions.",
          Switch({ on: writes, ariaLabel: 'Allow writes', disabled: () => !on(), onChange: allowWrites }))}
        ${row('rate', 'Tool calls per minute', 'Per client. The framework allows at most 60.',
          html`<span class="ctl-addon"><input class="input num" type="number" min="1" max="60" aria-label="Tool calls per minute"
            prop:value=${() => String(get<number>('rate'))} disabled=${() => !on()} on:change=${rate}><span>/ min</span></span>`)}
      </section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Tools ${mark('disabled_tools')}</h2><p>What clients are allowed to call</p></div></div>
        ${error('disabled_tools')}
        <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Tool</th><th>What it does</th><th>Type</th><th class="c">On</th></tr></thead><tbody>
        ${TOOLS.map(([name, text, write]) => html`<tr><td class="mono"><strong>${name}</strong></td><td class="muted ctl-small-cell">${text}</td>
          <td>${write ? StatusPill({ label: 'Write', tint: 'amber' }) : StatusPill({ label: 'Read', tint: 'slate' })}</td>
          <td class="c">${() => write && !writes() ? html`<span class="muted ctl-small-cell">Writes off</span>`
            : Switch({ on: () => !get<string[]>('disabled_tools').includes(name), ariaLabel: name, disabled: () => !on(), onChange: () => toggle('disabled_tools', name) })}</td></tr>`)}
        </tbody></table></div></section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Models ${mark('hidden_models')}</h2><p>Hidden models never appear to AI clients, even for admins</p></div></div>
        ${error('hidden_models')}
        ${() => state.get().models.map(model => html`<div class="ctl-setrow"><div class="grow"><strong>${model.label}</strong><p>${model.app ?? model.name}</p></div>
          <div class="ctl-setctl">${Switch({ on: () => !get<string[]>('hidden_models').includes(model.name), ariaLabel: `Show ${model.name}`, disabled: () => !on(),
            onChange: () => toggle('hidden_models', model.name) })}</div></div>`)}
      </section>
    </div>
    <div class="ctl-stack">
      <section class="ctl-card pad"><div class="ctl-line"><h2 class="grow">Activity</h2><span class="muted ctl-small-cell">Last 24 hours</span></div>
        <div class="ctl-big num">${() => totals().reads + totals().writes + totals().denied}<small>tool calls</small></div>
        <dl class="ctl-kv"><dt>Reads</dt><dd class="num">${() => totals().reads}</dd><dt>Writes</dt><dd class="num">${() => totals().writes}</dd>
          <dt>Denied by permissions</dt><dd class="num">${() => totals().denied}</dd></dl></section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Connected clients</h2><p>${() => `${overview.get()?.clients.length ?? 0} signed in`}</p></div></div>
        ${() => overview.get() === null ? html`<div class="ctl-empty">Loading…</div>` : overview.get()!.clients.length ? overview.get()!.clients.map(client => html`<div class="ctl-row-btn">
          <span class="ctl-lr"><strong>${client.client_name}</strong><span>as ${client.username} · ${client.last_used ? `last call ${stamp(client.last_used)}` : 'no calls yet'} · ${plural(client.calls, 'call')}</span></span>
          ${Button({ label: 'Sign out', size: 'small', variant: 'soft-danger', onClick: () => signOut(client) })}</div>`)
          : html`<div class="ctl-empty">No clients connected.</div>`}
      </section>
      ${Callout('info', 'lock', 'The connector has no database access, SQL or file tools. Every call goes through the same API and permission checks as the browser.')}
      ${Callout('info', 'activity', html`Finalising documents, actions and deletes wait for the person's approval in Evlet, under Settings, AI. Every call is in the <a href=${ctx.router.href('log', {})}>Tool log</a>.`)}
    </div></div></div>` as HTMLElement;
}
