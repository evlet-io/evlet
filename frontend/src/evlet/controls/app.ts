// Controls (/controls): the locked admin pages of the instance, served on the primary host to roles
// that open Controls. Overrides (permissions, security) are drafts behind the change bar; instance
// data (users, roles) saves at once. The server checks access on every route.
//
//   const router = controlsRouter();
//   router.start();
//   mount(element, () => Controls({ router }));

import { html, match, when, type Child } from '../dom';
import { ApiError } from '../api';
import { Lockup } from '../app/login';
import { cell, derive, onCleanup, type Cell } from '../reactive';
import { createRouter, type Router } from '../router';
import { Avatar, Button, EmptyState, Icon, StatusPill } from '../ui/base';
import { DialogFooter, mountToasts, openModal, toast } from '../ui/layers';
import { formatDateTime } from '../model/format';
import { ControlsClient, type ControlsState } from './client';
import './controls.css';
import { describe, Drafts, ipAllowed } from './drafts';
import { RolesPage, UsersPage } from './people';
import { PermissionsPage, SecurityPage } from './access';
import { ModelsPage } from './models';
import { NamingPage } from './naming';
import { AppsPage, AuditPage, AuditRows, JobsPage, OverridesPage, RoutingPage } from './system';
import { ConnectorsPage, EmailPage } from './connections';
import { ApiKeysPage } from './keys';
import { McpPage } from './ai';
import { ToolLogPage } from './log';

export const ROUTES = {
  overview: '/controls', users: '/controls/users', roles: '/controls/roles',
  permissions: '/controls/permissions', security: '/controls/security',
  models: '/controls/models', naming: '/controls/naming', apps: '/controls/apps', routing: '/controls/routing',
  jobs: '/controls/jobs', overrides: '/controls/overrides', audit: '/controls/audit',
  connectors: '/controls/connectors', email: '/controls/email', mcp: '/controls/mcp', keys: '/controls/keys',
  log: '/controls/log',
} as const;
export type ControlsRouter = Router<typeof ROUTES>;
export const controlsRouter = (options: { window?: Window } = {}): ControlsRouter => createRouter(ROUTES, options);

export interface ControlsContext {
  api: ControlsClient;
  router: ControlsRouter;
  state: Cell<ControlsState>;
  drafts: Drafts;
  /** Read the state and the saved overrides again; drafts that still differ stay. */
  reload(): Promise<void>;
  saveDrafts(): Promise<void>;
}

const NAV: Array<[string, Array<[keyof typeof ROUTES, string, string]>]> = [
  ['', [['overview', 'Overview', 'layout-grid']]],
  ['People', [['users', 'Users', 'users'], ['roles', 'Roles', 'user-cog']]],
  ['Access', [['permissions', 'Permissions', 'list-checks'], ['security', 'Security', 'lock']]],
  ['Connections', [['connectors', 'Connectors', 'link'], ['email', 'Email', 'mail'], ['keys', 'API keys', 'hash']]],
  ['AI', [['mcp', 'MCP', 'sparkles'], ['log', 'Tool log', 'activity']]],
  ['Build', [['apps', 'Apps', 'package'], ['models', 'Models', 'layers'], ['naming', 'Naming', 'hash'], ['routing', 'Routing', 'globe']]],
  ['System', [['jobs', 'Jobs', 'clock'], ['overrides', 'Overrides', 'file-text'], ['audit', 'Audit log', 'activity']]],
];
const TITLES = Object.fromEntries(NAV.flatMap(([group, items]) => items.map(([id, label]) => [id, [group, label]])));

/** "01-11-2026 19:52" in GST, from a Unix time. */
export const stamp = (seconds: number | null | undefined) => seconds ? formatDateTime(new Date(seconds * 1000).toISOString()) : '';
export const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

export function Head(title: string, sub: string, actions: Child = null, extra: Child = null): HTMLElement {
  return html`<div class="ctl-head"><div class="grow"><h1>${title}${extra}</h1>${sub ? html`<p>${sub}</p>` : null}</div>
    <div class="ctl-head-actions">${actions}</div></div>` as HTMLElement;
}

export function Callout(tone: 'info' | 'warn' | 'err', icon: string, body: Child): HTMLElement {
  return html`<div class=${`ctl-callout ${tone}`}>${Icon(icon, { size: 'sm' })}<span>${body}</span></div>` as HTMLElement;
}

type Phase = 'loading' | 'ready' | 'denied' | 'failed';

/**
 * Changes in Controls need a two-step code at most every 10 minutes. This dialog asks for one and resolves
 * once the server accepts it, so the change that asked is sent again; Cancel rejects and the change is not made.
 */
export function askForCode(api: ControlsClient): Promise<void> {
  return new Promise((resolve, reject) => {
    const code = cell(''), error = cell(''), busy = cell(false);
    let accepted = false;
    const submit = async (close: () => void) => {
      const value = code.peek().trim();
      if (!value) { error.set('Enter a code from your app, or a backup code.'); return; }
      busy.set(true);
      try {
        await api.verifyCode(value);
        accepted = true;
        close();
        resolve();
      } catch (failure) {
        error.set(failure instanceof ApiError ? Object.values(failure.fields ?? {})[0] ?? failure.message : 'Try again.');
      } finally { busy.set(false); }
    };
    let close = () => {};
    close = openModal({ title: 'Enter your two-step code', narrow: true, icon: 'lock',
      onClose: () => { if (!accepted) reject(new ApiError('The change was not made. It needs a code from your authenticator app.', 403, 'forbidden')); },
      onSubmit: () => void submit(close),
      body: () => html`<p class="muted ctl-small">Changes in Controls ask for a code from your authenticator app. One code covers your changes for the next 10 minutes.</p>
        <div class="ctl-field"><label for="ctl-verify-code">Code from your app, or a backup code</label>
          <input class="input" id="ctl-verify-code" autocomplete="one-time-code" inputmode="numeric" maxlength="9" prop:value=${code}
            on:input=${(event: Event) => { code.set((event.target as HTMLInputElement).value); error.set(''); }}
            on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') { event.preventDefault(); void submit(close); } }}>
          ${() => error.get() ? html`<span class="ctl-err" role="alert">${error.get()}</span>` : null}</div>`,
      footer: done => DialogFooter({ cancel: done, submit: { label: 'Confirm', disabled: busy, onClick: () => void submit(done) } }) });
  });
}

export function Controls(options: { router: ControlsRouter; api?: ControlsClient }): Node {
  const { router } = options;
  const api = options.api ?? new ControlsClient();
  api.verify ??= () => askForCode(api);
  const phase = cell<Phase>('loading'), message = cell('');
  const state = cell<ControlsState>(null as unknown as ControlsState, { name: 'controls.state' });
  const drafts = new Drafts(api);
  let alive = true;
  onCleanup(() => { alive = false; });

  const reload = async () => {
    const [next, overrides] = await Promise.all([api.state(), api.overrides()]);
    if (!alive) return;
    drafts.load(overrides.items);
    state.set(next);
  };
  const ctx: ControlsContext = { api, router, state, drafts, reload, saveDrafts: () => saveDrafts(ctx) };

  void (async () => {
    try {
      const session = await api.session();
      if (!session.user) { window.location.assign('/login?next=' + encodeURIComponent(window.location.pathname)); return; }
      await reload();
      if (alive) phase.set('ready');
    } catch (failure) {
      if (!alive) return;
      message.set(failure instanceof ApiError ? failure.message : 'Controls could not load.');
      phase.set(failure instanceof ApiError && ['not_found', 'forbidden'].includes(failure.kind) ? 'denied' : 'failed');
    }
  })();

  // Saved changes from another tab or person: reload the metadata when the config version moves.
  if (typeof EventSource !== 'undefined') {
    const source = new EventSource('/api/_events/stream');
    source.addEventListener('evlet', event => {
      try {
        const items = JSON.parse((event as MessageEvent).data).items as Array<{ type?: string; version?: number }>;
        if (items.some(item => item.type === 'config.changed' && item.version !== state.peek()?.instance.version)) void reload();
      } catch { /* a malformed event changes nothing */ }
    });
    onCleanup(() => source.close());
  }
  const leaving = (event: BeforeUnloadEvent) => { if (drafts.changes.peek().length) { event.preventDefault(); event.returnValue = ''; } };
  window.addEventListener('beforeunload', leaving);
  onCleanup(() => window.removeEventListener('beforeunload', leaving));

  return match(phase, {
    loading: () => html`<div class="ev-connecting">${Icon('lock')}<span>Opening Controls…</span></div>`,
    denied: () => html`<div class="ctl-blocked">${EmptyState({ icon: 'lock', tint: 'slate', title: 'This page is not available', text: message,
      action: Button({ label: 'Back to the workspace', onClick: () => window.location.assign('/') }) })}</div>`,
    failed: () => html`<div class="ctl-blocked">${EmptyState({ icon: 'info', tint: 'rose', title: 'Controls could not load', text: message,
      action: Button({ label: 'Try again', onClick: () => window.location.reload() }) })}</div>`,
    ready: () => Shell(ctx),
  });
}

async function saveDrafts(ctx: ControlsContext, confirmed = false) {
  const count = ctx.drafts.changes.peek().length;
  const ips = ctx.drafts.changes.peek().find(change => change.item.key === 'security.controls_ips');
  const client = ctx.state.peek().client;
  if (ips && !confirmed && !ipAllowed(client, (ips.to ?? ips.item.file) as string[])) {
    openModal({ title: 'Lock yourself out of Controls?', icon: 'lock', tint: 'rose', narrow: true,
      body: () => html`<p class="ctl-confirm">Your address, <code>${client || 'unknown'}</code>, is not in the allowed networks. After saving you can't open Controls from here. Someone on an allowed network, or <code>evlet controls grant</code> on the server, can open it again.</p>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Save anyway', variant: 'danger', onClick: () => { close(); void saveDrafts(ctx, true); } } }) });
    return;
  }
  try {
    if (await ctx.drafts.save()) {
      toast(`${plural(count, 'change')} saved`);
      ctx.state.set(await ctx.api.state());
    } else if (ctx.drafts.errors.peek().size) {
      toast('Some changes could not be saved', { icon: 'info' });
      openReview(ctx);
    }
  } catch (failure) {
    toast(failure instanceof ApiError ? failure.message : 'Saving failed. Try again.', { icon: 'info' });
  }
}

function Shell(ctx: ControlsContext): HTMLElement {
  const { router, state } = ctx;
  const route = derive(() => router.route.get()?.name ?? 'overview');
  const open = cell(false);
  const navCount = (id: string): [number, boolean] | null => {
    const s = state.get(), count = id === 'users' ? [s.users.active, false] : id === 'overrides' ? [s.overrides, false]
      : id === 'jobs' ? [s.jobs.failed, true] : id === 'apps' ? [s.apps.filter(app => app.update).length, true] : null;
    return count && (count[0] || id === 'users') ? count as [number, boolean] : null;
  };
  const s = state.peek();
  const side = html`<aside class="ctl-side" class:open=${open} aria-label="Controls navigation">
    <div class="ctl-brand">${Lockup()}<small>Controls</small></div>
    <div class="ctl-ws"><span class="ctl-ws-letter">${(s.instance.name || 'E').slice(0, 1).toUpperCase()}</span>
      <span><strong>${s.instance.name}</strong><small>${s.instance.host}</small></span></div>
    <nav class="ctl-nav">${NAV.map(([group, items]) => [group ? html`<div class="ctl-nav-label">${group}</div>` : null,
      items.map(([id, label, icon]) => html`<a class="ctl-nav-item" class:on=${() => route.get() === id} aria-current=${() => route.get() === id ? 'page' : null}
        href=${router.href(id, {})} on:click=${() => open.set(false)}>${Icon(icon, { size: 'sm' })}<span>${label}</span>
        ${() => { const count = navCount(id); return count === null ? null : html`<span class="ctl-count" class:warn=${count[1]}>${count[0]}</span>`; }}</a>`)])}</nav>
    <div class="ctl-side-foot">${Avatar({ name: s.me.username, size: 'sm' })}<span><strong>${s.me.username}</strong><small>${s.me.role}</small></span>
      <a class="icon-btn" href="/" rel="external" aria-label="Back to the workspace" title="Back to the workspace">${Icon('log-out', { size: 'sm' })}</a></div>
  </aside>` as HTMLElement;
  const top = html`<header class="ctl-top">
    <button type="button" class="icon-btn ctl-menu" aria-label="Open menu" on:click=${() => open.set(!open.peek())}>${Icon('panel-left', { size: 'sm' })}</button>
    <nav class="ctl-crumbs" aria-label="Breadcrumbs"><span class="hide-sm">Controls</span>${() => {
      const [group, label] = TITLES[route.get()] ?? ['', 'Overview'];
      return [group ? [html`<span class="sep hide-sm">/</span>`, html`<span class="hide-sm">${group}</span>`] : null, html`<span class="sep hide-sm">/</span>`, html`<span class="cur">${label}</span>`];
    }}</nav>
    <span class="ctl-lock" title="Locked route. Primary host only, roles that open Controls.">${Icon('lock', { size: 'xs' })}/controls</span>
  </header>` as HTMLElement;
  const page = (): Child => {
    switch (route.get()) {
      case 'users': return UsersPage(ctx);
      case 'roles': return RolesPage(ctx);
      case 'permissions': return PermissionsPage(ctx);
      case 'security': return SecurityPage(ctx);
      case 'models': return ModelsPage(ctx);
      case 'naming': return NamingPage(ctx);
      case 'apps': return AppsPage(ctx);
      case 'routing': return RoutingPage(ctx);
      case 'jobs': return JobsPage(ctx);
      case 'overrides': return OverridesPage(ctx);
      case 'audit': return AuditPage(ctx);
      case 'connectors': return ConnectorsPage(ctx);
      case 'email': return EmailPage(ctx);
      case 'mcp': return McpPage(ctx);
      case 'log': return ToolLogPage(ctx);
      case 'keys': return ApiKeysPage(ctx);
      default: return Overview(ctx);
    }
  };
  mountToasts();
  return html`<div class="ctl">${side}<div class="ctl-scrim" class:show=${open} on:click=${() => open.set(false)}></div>
    <div class="ctl-main">${top}<div class="ctl-host" id="ctl-host">
      <div class="ctl-page" class:wide=${() => ['permissions', 'models'].includes(route.get())}>${match(route, {}, page)}</div>${ChangeBar(ctx)}</div></div></div>` as HTMLElement;
}

export function ChangeBar(ctx: ControlsContext): Node {
  const count = derive(() => ctx.drafts.changes.get().length);
  return when(count, () => html`<div class="ctl-changebar" role="status"><span class="dot"></span>
    <strong>${() => `${plural(count.get(), 'unsaved change')}`}</strong><span class="m">Saved to this instance as overrides. App files stay as they are.</span>
    <span class="grow"></span>
    ${Button({ label: 'Review', onClick: () => openReview(ctx) })}
    ${Button({ label: 'Discard', onClick: () => { ctx.drafts.discard(); toast('Changes discarded'); } })}
    ${Button({ label: () => ctx.drafts.saving.get() ? 'Saving…' : 'Save changes', variant: 'primary', disabled: ctx.drafts.saving, onClick: () => void ctx.saveDrafts() })}</div>`);
}

export function openReview(ctx: ControlsContext) {
  const rows = ctx.drafts.changes.peek();
  if (!rows.length) return;
  openModal({ title: `${plural(rows.length, 'unsaved change')}`, icon: 'list-checks', tint: 'blue', narrow: true, className: 'ctl-review',
    onSubmit: () => void ctx.saveDrafts(),
    body: () => html`<div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Area</th><th>What</th><th>Change</th></tr></thead><tbody>
      ${rows.map(change => {
        const text = describe(change, ctx.state.peek().scopes);
        const error = () => ctx.drafts.errors.get().get(change.id);
        return html`<tr><td>${StatusPill({ label: text.area, tint: 'slate' })}</td><td class="mono">${text.target}</td>
          <td><span class="ctl-from">${text.from}</span><br><span class="ctl-to">${text.to}</span>
          ${when(error, () => html`<div class="ctl-err">${error}</div>`)}</td></tr>`;
      })}</tbody></table></div>
      <p class="muted ctl-small">Saved changes apply to new requests straight away. People who are signed in pick them up on their next page load.</p>`,
    footer: close => html`${Button({ label: 'Discard all', onClick: () => { ctx.drafts.discard(); close(); toast('Changes discarded'); } })}<span class="grow"></span>
      ${DialogFooter({ cancel: close, submit: { label: 'Save changes', onClick: async () => { await ctx.saveDrafts(); if (!ctx.drafts.changes.peek().length) close(); } } })}`,
  });
}

// ---------- overview ----------

function Overview(ctx: ControlsContext): HTMLElement {
  const s = () => ctx.state.get();
  const go = (page: keyof typeof ROUTES) => void ctx.router.go(page, {});
  const tile = (label: string, icon: string, value: () => Child, note: () => string, page: keyof typeof ROUTES, hero = false) =>
    html`<button type="button" class="ctl-tile" class:hero=${hero} on:click=${() => go(page)}><span class="t-label">${label}${Icon(icon, { size: 'sm' })}</span>
      <span class="t-val">${value}<small>${note}</small></span></button>`;
  return html`<div>
    ${Head('Controls', 'Users, access, apps and models for this instance. Defaults come from the installed apps. What you change here is saved to this instance only.', null,
      html` <span class="ctl-badge violet">Admin only</span>`)}
    <div class="ctl-grid g4">
      ${tile('Overrides on this instance', 'file-text', () => s().overrides, () => 'changed from app files', 'overrides', true)}
      ${tile('Active users', 'users', () => s().users.active, () => `of ${s().users.total}`, 'users')}
      ${tile('Installed apps', 'package', () => s().apps.length, () => plural(s().models.length, 'model'), 'apps')}
      ${tile('Roles', 'user-cog', () => s().roles.length, () => `${s().roles.filter(role => role.kind === 'custom').length} custom`, 'roles')}
    </div>
    <div class="ctl-grid main">
      <div class="ctl-stack">
        <section class="ctl-card"><div class="ctl-card-head"><div><h2>Needs attention</h2><p>${() => s().attention.length ? plural(s().attention.length, 'thing') + ' to look at' : 'Nothing right now'}</p></div></div>
          ${() => s().attention.length ? s().attention.map(item => html`<button type="button" class="ctl-row-btn" on:click=${() => go(item.page as keyof typeof ROUTES)}>
            <span class="ph-icon b-amber ctl-ic">${Icon(item.icon, { size: 'sm' })}</span><span class="ctl-lr"><strong>${item.title}</strong><span>${item.text}</span>${item.line ? html`<code class="ctl-attn-line">${item.line}</code>` : null}</span>${Icon('chevron-right', { size: 'sm' })}</button>`)
            : html`<div class="ctl-empty">Everything is working.</div>`}
        </section>
        <section class="ctl-card"><div class="ctl-card-head"><div><h2>Recent changes</h2><p>Every change made in Controls is logged</p></div>
          <a class="ctl-link" href=${ctx.router.href('audit', {})}>Audit log ${Icon('arrow-right', { size: 'xs' })}</a></div>
          ${() => s().audit.length ? AuditRows(s().audit) : html`<div class="ctl-empty">No changes yet.</div>`}
        </section>
      </div>
      <div class="ctl-stack">
        <section class="ctl-card pad"><h2>Instance</h2>
          <dl class="ctl-kv"><dt>Name</dt><dd>${() => s().instance.name}</dd><dt>Primary host</dt><dd>${() => s().instance.host}</dd>
            <dt>Evlet</dt><dd>${() => s().instance.evlet || 'Development'}</dd><dt>Config version</dt><dd class="num">${() => s().instance.version}</dd></dl></section>
        <section class="ctl-card pad"><h2>How Controls works</h2>
          <p class="muted ctl-small">Apps, models and permissions are read from the installed apps each time the instance starts. A change made here is stored in this instance's database as an override. App files are never edited.</p>
          <p class="muted ctl-small">Reset an override to go back to the file value, or export it as code to move it into the app.</p>
          <div class="ctl-legend"><span class="ctl-src s-file"><i></i>Framework default</span><span class="ctl-src s-app"><i></i>App rule</span><span class="ctl-src s-ov"><i></i>Override</span></div></section>
      </div>
    </div></div>` as HTMLElement;
}
