// Controls → AI: Eva's settings (drafts behind the change bar, like MCP) and Usage. The Tool log is log.ts.
// The Eva page only offers settings when config.py turned AI on; otherwise it says so and stops.
// Usage shows who used Eva and what it cost, never what was asked: chats appear by owner and number.

import { html, when, type Child } from '../dom';
import { cell, derive } from '../reactive';
import { IconButton, ProgressBar, StatusPill, Switch } from '../ui/base';
import { toast } from '../ui/layers';
import { Callout, Head, plural, type ControlsContext } from './app';
import type { EvaConfig, EvaUsage } from './client';
import { idOf } from './drafts';
import { message } from './people';

const GROUPS: Array<[string, string, string]> = [
  ['create', 'Create a record or draft', 'New records Eva makes for the person.'],
  ['update', 'Change a record', 'Edits to saved records.'],
  ['confirm', 'Confirm, approve, post', 'Finalising documents, such as confirming an invoice.'],
  ['action', 'Other app actions', 'Actions apps register on records, and business services.'],
];

export function EvaSettingsPage(ctx: ControlsContext): HTMLElement {
  const { drafts } = ctx;
  const config = cell<EvaConfig | null>(null);
  void ctx.api.evaConfig().then(value => config.set(value), failure => toast(message(failure), { icon: 'info' }));
  const id = (key: string) => idOf('setting', `eva.${key}`);
  const get = <T>(key: string) => drafts.value<T>(id(key));
  const set = (key: string, value: unknown) => drafts.set(id(key), value);
  const mark = (key: string) => () => drafts.overridden(id(key)) ? html`<span class="ctl-tag">Changed</span>${IconButton({ icon: 'rotate-ccw', size: 'small',
    tip: 'Reset to the file value', onClick: () => drafts.reset(id(key)) })}` : null;
  const error = (key: string) => when(() => drafts.errors.get().has(id(key)), () => html`<span class="ctl-err">${() => drafts.errors.get().get(id(key))}</span>`);
  const row = (key: string | null, title: string, text: Child, control: Child) => html`<div class="ctl-setrow"><div class="grow">
    <strong>${title} ${key ? mark(key) : null}</strong><p>${text}</p>${key ? error(key) : null}</div><div class="ctl-setctl">${control}</div></div>`;
  const on = () => get<boolean>('enabled');
  type Limit = { amount: string | number | null; unit: 'aed' | 'tokens' | 'requests'; period: 'day' | 'week' | 'month' };
  const limit = (key: 'instance_limit' | 'user_limit', label: string) => {
    const value = () => get<Limit>(key);
    const change = (patch: Partial<Limit>) => set(key, { ...value(), ...patch });
    return html`<span class="ctl-addon eva-limit">
      <input class="input num" aria-label=${`${label} amount`} placeholder="No limit" prop:value=${() => value().amount ?? ''}
        on:change=${(event: Event) => { const text = (event.target as HTMLInputElement).value.trim().replace(/,/g, '');
          change({ amount: !text ? null : value().unit === 'aed' ? text : Number.parseInt(text, 10) }); }}>
      <select class="input" aria-label=${`${label} unit`} on:change=${(event: Event) => change({ unit: (event.target as HTMLSelectElement).value as Limit['unit'], amount: null })}>
        ${(['aed', 'tokens', 'requests'] as const).map(unit => html`<option value=${unit} selected=${() => value().unit === unit}>${unit === 'aed' ? 'AED' : unit}</option>`)}</select>
      <span>a</span><select class="input" aria-label=${`${label} period`} on:change=${(event: Event) => change({ period: (event.target as HTMLSelectElement).value as Limit['period'] })}>
        ${(['day', 'week', 'month'] as const).map(period => html`<option value=${period} selected=${() => value().period === period}>${period}</option>`)}</select></span>`;
  };
  const number = (key: string, label: string, min: number, max: number, suffix: string) => html`<span class="ctl-addon"><input class="input num" type="number" min=${min} max=${max}
    aria-label=${label} prop:value=${() => String(get<number>(key))} on:change=${(event: Event) => {
      const value = Math.min(max, Math.max(min, Number.parseInt((event.target as HTMLInputElement).value, 10) || min)); set(key, value); }}><span>${suffix}</span></span>`;

  return html`<div>
    ${Head('Eva', "Evlet's built-in assistant. It works only with each person's own permissions. Turning it off hides it from everyone and stops scheduled tasks.")}
    ${() => { const c = config.get(); if (!c) return html`<div class="ctl-empty">Loading…</div>`;
      if (!c.configured) return html`<div class="ctl-stack">${Callout('warn', 'lock', html`<b>Turned off in the application's configuration.</b> Controls can't turn it on.
        A developer sets <code class="ctl-icode">ai = AI(enabled=True, models={...})</code> in <code class="ctl-icode">config.py</code> and releases the app. Nothing else on this page applies until then.`)}
        <section class="ctl-card"><div class="ctl-setrow"><div class="grow"><strong>MCP</strong><p>Separate from Eva. Outside assistants keep working.</p></div>
          <div class="ctl-setctl"><a class="btn sm" href=${ctx.router.href('mcp', {})}>Open MCP</a></div></div></section></div>`;
      return html`<div class="ctl-grid main"><div class="ctl-stack">
        <section class="ctl-card"><div class="ctl-card-head"><div><h2>Eva for this instance</h2></div>
          ${() => on() ? StatusPill({ label: 'On', tint: 'green' }) : StatusPill({ label: 'Off', tint: 'slate' })}</div>
          ${row('enabled', 'Eva', "Turned on in the application's configuration. This switch can only narrow that.", Switch({ on, ariaLabel: 'Eva', onChange: next => set('enabled', next) }))}
          ${row(null, 'Who can use Eva', `People with Allow Eva. Now: ${c.allowed_users} of ${plural(c.active_users, 'active user')}.`,
            html`<a class="btn sm" href=${ctx.router.href('users', {})}>Manage in Users</a>`)}</section>
        <section class="ctl-card"><div class="ctl-card-head"><div><h2>Provider</h2><p>Eva keeps conversations in Evlet. The provider only sees what each request needs.</p></div>
          ${c.connector ? (c.connector.saved ? StatusPill({ label: 'Key saved', tint: 'green' }) : StatusPill({ label: 'No key yet', tint: 'amber' })) : StatusPill({ label: c.provider ?? '', tint: 'slate' })}</div>
          ${row(null, 'Provider', c.provider ?? '', null)}
          ${row('model', 'Default model', 'The models are set in the configuration. People use this one unless you let them choose.',
            html`<select class="input" aria-label="Default model" on:change=${(event: Event) => set('model', (event.target as HTMLSelectElement).value)}>
              ${(c.models ?? []).map((name, index) => html`<option value=${name} selected=${() => (get<string>('model') || (c.models ?? [])[0]) === name && index >= 0}>${name}</option>`)}</select>`)}
          ${row('choose_model', 'Let people choose a model', 'Off: everyone uses the default model.', Switch({ on: () => get<boolean>('choose_model'), ariaLabel: 'Let people choose a model', onChange: next => set('choose_model', next) }))}
          ${c.connector ? row(null, 'API key', 'Stored encrypted with your other connector secrets and never shown again. Test or replace it under Connectors.',
            html`<span class="ctl-icode">${c.connector.saved ? 'sk-••••••••••••' : 'Not set'}</span><a class="btn sm" href=${ctx.router.href('connectors', {})}>Open Connectors</a>`) : null}</section>
        ${c.missing_rates?.length ? Callout('warn', 'info', `No rate yet for ${c.missing_rates.join(', ')}. While an allowance is in AED, Eva refuses requests to a model without a rate, because their cost can't be counted. Set the rates below.`) : null}
        <section class="ctl-card"><div class="ctl-card-head"><div><h2>Allowances</h2><p>Eva stops before a new request would go over a limit. Amounts are estimates from the rates below.</p></div></div>
          ${row('instance_limit', 'Whole instance', 'Shared by everyone, scheduled tasks included.', limit('instance_limit', 'Instance limit'))}
          ${row('user_limit', 'Each person', 'The default for everyone. Days, weeks and months follow the instance time zone; weeks start on Monday.', limit('user_limit', 'Per person limit'))}
          ${row('task_reserve', 'Kept for scheduled tasks', 'Part of the instance allowance chats cannot use, so scheduled tasks still run.', number('task_reserve', 'Kept for scheduled tasks', 0, 50, '%'))}
          ${row('max_steps', 'Per answer', 'The most model requests, tool calls and seconds Eva may use for one message.',
            [number('max_steps', 'Model requests per answer', 1, 40, 'requests'), number('max_tools', 'Tool calls per answer', 1, 100, 'tools'), number('max_seconds', 'Seconds per answer', 10, 900, 's')])}
          ${row('rates', 'Rates', 'AED per million tokens, per model. Each usage row keeps the rate it was charged at.', html`<div class="eva-rates">${(c.models ?? []).map(name => {
            const rate = () => (get<Record<string, Record<string, string>>>('rates') ?? {})[name] ?? { input: '', cached: '', output: '' };
            const change = (part: string, value: string) => set('rates', { ...(get<Record<string, unknown>>('rates') ?? {}), [name]: { ...{ input: '0', cached: '0', output: '0' }, ...rate(), [part]: value || '0' } });
            return html`<div class="eva-rate"><b>${name}</b>${(['input', 'cached', 'output'] as const).map(part => html`<label>${part}<input class="input num" aria-label=${`${name} ${part} rate`}
              prop:value=${() => rate()[part] ?? ''} on:change=${(event: Event) => change(part, (event.target as HTMLInputElement).value.trim())}></label>`)}</div>`; })}</div>`)}
        </section>
        <section class="ctl-card"><div class="ctl-card-head"><div><h2>When Eva asks first ${mark('confirm')}</h2><p>Allowed changes can still need a click. Formal approvals in your apps always apply as well.</p></div></div>
          ${error('confirm')}
          <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Kind of change</th><th>Eva</th><th>MCP clients</th></tr></thead><tbody>
            <tr><td>Read, search, totals, files</td><td class="muted">Never asks</td><td class="muted">Never asks</td></tr>
            ${GROUPS.map(([group, label, help]) => html`<tr><td><strong>${label}</strong><div class="muted ctl-small-cell">${help}</div></td>
              <td><select class="input" aria-label=${`Eva: ${label}`} on:change=${(event: Event) => set('confirm', { ...get<Record<string, string>>('confirm'), [group]: (event.target as HTMLSelectElement).value })}>
                <option value="ask" selected=${() => get<Record<string, string>>('confirm')[group] === 'ask'}>Ask every time</option>
                <option value="never" selected=${() => get<Record<string, string>>('confirm')[group] === 'never'}>Never asks</option></select></td>
              <td class="muted">${['confirm', 'action'].includes(group) ? 'Approve in Evlet' : 'Client asks'}</td></tr>`)}
            <tr><td><strong>Cancel, delete, unpost</strong></td><td class="muted">Always asks</td><td class="muted">Approve in Evlet</td></tr>
          </tbody></table></div></section>
      </div>
      <div class="ctl-stack">
        ${Callout('info', 'lock', 'Eva has no database, SQL, file system or shell access. Every call goes through the same routes and permission checks as the browser, as the person.')}
        <section class="ctl-card"><div class="ctl-card-head"><div><h2>Tools</h2><p>What Eva can call, before each person's permissions</p></div></div>
          ${(c.tools ?? []).map(tool => html`<div class="ctl-setrow"><div class="grow"><strong class="mono">${tool.name}</strong></div><div class="ctl-setctl">
            ${tool.kind === 'read' ? StatusPill({ label: 'Read', tint: 'slate' }) : StatusPill({ label: tool.kind === 'destructive' ? 'Destructive' : 'Write', tint: tool.kind === 'destructive' ? 'rose' : 'amber' })}
            ${Switch({ on: () => !get<string[]>('disabled_tools').includes(tool.name), ariaLabel: tool.name, onChange: next => set('disabled_tools',
              next ? get<string[]>('disabled_tools').filter(name => name !== tool.name) : [...get<string[]>('disabled_tools'), tool.name]) })}</div></div>`)}</section>
        <section class="ctl-card pad"><h2>Usage and activity</h2><p class="muted ctl-small">Costs per person and the log of every tool call.</p>
          <div class="ctl-line"><a class="btn sm" href=${ctx.router.href('usage', {})}>Usage</a><a class="btn sm" href=${ctx.router.href('log', {})}>Tool log</a></div></section>
      </div></div>`; }}
  </div>` as HTMLElement;
}

export function UsagePage(ctx: ControlsContext): HTMLElement {
  const period = cell('month'), usage = cell<EvaUsage | null>(null), missing = cell('');
  const load = async () => {
    try { usage.set(await ctx.api.evaUsage(period.peek())); missing.set(''); } catch (failure) { missing.set(message(failure)); }
  };
  void load();
  const totals = () => usage.get()!.totals;
  const share = derive(() => {
    const data = usage.get(); if (!data || data.limit.amount === null || data.limit.unit !== 'aed') return null;
    return Math.min(100, Math.round(100 * Number(data.totals.cost) / Number(data.limit.amount)));
  });
  const chart = () => {
    const days = usage.get()!.days;
    const values = days.map(day => Number(day.chats) + Number(day.tasks));
    const top = Math.max(1, ...values) * 1.15;
    const width = 720, height = 180, left = 44, bottom = 22, step = (width - left - 8) / Math.max(1, days.length);
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('class', 'eva-chart');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Cost per day from ${usage.get()!.from} to ${usage.get()!.to}`);
    const add = (tag: string, attributes: Record<string, string | number>, text?: string) => {
      const element = document.createElementNS(ns, tag);
      for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
      if (text !== undefined) element.textContent = text;
      svg.append(element);
    };
    for (const fraction of [0, 0.5, 1]) {
      const y = height - bottom - (height - bottom - 8) * fraction;
      add('line', { x1: left, x2: width, y1: y, y2: y, class: 'grid' });
      add('text', { x: left - 6, y: y + 3, 'text-anchor': 'end' }, (top * fraction / 1.15).toFixed(top > 10 ? 0 : 2));
    }
    days.forEach((day, index) => {
      const x = left + index * step + 2, w = Math.max(2, step - 4);
      const chats = (height - bottom - 8) * Number(day.chats) / top, tasks = (height - bottom - 8) * Number(day.tasks) / top;
      add('rect', { x, y: height - bottom - chats, width: w, height: chats, rx: 2, class: 'bar' });
      if (tasks) add('rect', { x, y: height - bottom - chats - tasks, width: w, height: tasks, rx: 2, class: 'bar tasks' });
      if (index % 5 === 0 || index === days.length - 1) add('text', { x: x + w / 2, y: height - 6, 'text-anchor': 'middle' }, day.day.slice(0, 5));
    });
    add('text', { x: 4, y: 12 }, 'AED');
    return svg;
  };

  return html`<div>
    ${Head('Usage', "Tokens, requests and estimated cost of Eva, in AED. Your provider's invoice is the final word.",
      html`<select class="input" aria-label="Period" on:change=${(event: Event) => { period.set((event.target as HTMLSelectElement).value); void load(); }}>
        <option value="month">This month</option><option value="last_month">Last month</option><option value="7d">Last 7 days</option></select>`)}
    ${() => missing.get() ? Callout('info', 'info', missing.get()) : usage.get() === null ? html`<div class="ctl-empty">Loading…</div>` : html`<div class="ctl-stack">
      <div class="eva-tiles">
        <div class="ctl-tile"><div class="t-label"><span>Estimated cost</span>${() => share.get() !== null ? StatusPill({ label: `${share.get()}%`, tint: share.get()! >= 90 ? 'rose' : share.get()! >= 70 ? 'amber' : 'green' }) : null}</div>
          <div class="t-val num">AED ${totals().cost}</div>
          ${() => share.get() !== null ? ProgressBar({ value: share.get()!, tint: share.get()! >= 90 ? 'rose' : share.get()! >= 70 ? 'amber' : 'blue' }) : null}
          <div class="muted ctl-small">${usage.get()!.limit.amount !== null && usage.get()!.limit.unit === 'aed' ? `of AED ${usage.get()!.limit.amount} a ${usage.get()!.limit.period}` : `${usage.get()!.from} to ${usage.get()!.to}`}</div></div>
        <div class="ctl-tile"><div class="t-label"><span>Tokens</span></div><div class="t-val num">${totals().tokens.toLocaleString('en')}</div>
          <div class="muted ctl-small">${totals().input_tokens.toLocaleString('en')} in (${totals().cached_tokens.toLocaleString('en')} cached) · ${totals().output_tokens.toLocaleString('en')} out</div></div>
        <div class="ctl-tile"><div class="t-label"><span>Model requests</span></div><div class="t-val num">${totals().requests.toLocaleString('en')}</div>
          <div class="muted ctl-small">from ${plural(totals().runs, 'answer')}</div></div>
        <div class="ctl-tile"><div class="t-label"><span>Tool calls</span></div><div class="t-val num">${totals().tool_calls.toLocaleString('en')}</div>
          <div class="muted ctl-small">${totals().denied} refused by permissions</div></div>
      </div>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Cost by day</h2></div></div><div class="eva-chart-wrap">${chart()}</div>
        <div class="eva-legend"><span><i class="chats"></i>Chats</span><span><i class="tasks"></i>Scheduled tasks</span></div></section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>By person</h2></div></div>
        ${usage.get()!.people.length ? html`<div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Person</th><th class="r">Requests</th><th class="r">Tokens in</th><th class="r">Tokens out</th><th class="r">Cost (AED)</th><th>Allowance now</th></tr></thead><tbody>
          ${usage.get()!.people.map(person => html`<tr><td>${person.username ?? `User ${person.user_id}`}</td><td class="r num">${person.requests.toLocaleString('en')}</td>
            <td class="r num">${person.input_tokens.toLocaleString('en')}</td><td class="r num">${person.output_tokens.toLocaleString('en')}</td><td class="r num">${person.cost}</td>
            <td>${person.allowance.limit === null ? html`<span class="muted">No limit</span>` : html`<span class="num ctl-small-cell">${person.allowance.used} / ${person.allowance.limit} a ${person.allowance.period}</span>
              ${person.allowance.exhausted ? StatusPill({ label: 'Used up', tint: 'amber' }) : null}`}</td></tr>`)}</tbody></table></div>`
          : html`<div class="ctl-empty">Nobody used Eva in this period.</div>`}</section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Largest conversations</h2><p>Titles and messages stay private to their owner.</p></div></div>
        ${usage.get()!.conversations.length ? html`<div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Conversation</th><th>Owner</th><th class="r">Messages</th><th class="r">Tokens</th><th class="r">Cost (AED)</th></tr></thead><tbody>
          ${usage.get()!.conversations.map(item => html`<tr><td>${item.task ? 'Scheduled task run' : 'Conversation'} ${item.id}</td><td>${item.username}</td>
            <td class="r num">${item.messages}</td><td class="r num">${item.tokens.toLocaleString('en')}</td><td class="r num">${item.cost}</td></tr>`)}</tbody></table></div>`
          : html`<div class="ctl-empty">No conversations in this period.</div>`}</section>
      ${Callout('info', 'info', usage.get()!.note)}
    </div>`}
  </div>` as HTMLElement;
}
