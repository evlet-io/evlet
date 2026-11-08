// The app shell (docs/ui-design/README.md section 4): sidebar with the Evlet brand, the app switcher,
// the models grouped, and an edge button that collapses it; the top bar with breadcrumbs, recent
// records, search, shortcuts and New; one scrolling view. Under 760 px the sidebar is a drawer
// opened from the top bar.

import { html, listen, when, type Child } from '../dom';
import { cell, derive, effect } from '../reactive';
import { Icon, IconButton, IconTile, Key } from '../ui/base';
import { tooltip } from '../ui/tooltip';
import { MenuButton, mountToasts, openModal, openPopover, Menu, closePopover, type MenuItem } from '../ui/layers';
import { Lockup, Logo } from './login';
import { resolveTheme, systemScheme } from '../ui/theme';
import { docsOn, goSettings, type AppContext } from './context';
import { canChangePassword, displayName, openPasswordDialog, PersonMark } from './settings';
import { presentSlip } from './slips';
import { InboxButton } from './inbox';
import { RecentsButton } from './recents';
import { rememberAppPage } from './last-page';
import { groupOf, identity, rootModels, type ScreenModel } from './meta';
import { REPORT_ICON, reportGroups, reportsOf } from './report-page';

const PHONE = '(max-width: 760px)';
const isPhone = () => { try { return Boolean(window.matchMedia?.(PHONE)?.matches); } catch { return false; } };

export function Shell(ctx: AppContext, view: () => Child): HTMLElement {
  const { prefs, router } = ctx;
  const collapsed = prefs.collapsed;
  const mobileOpen = cell(false, { name: 'shell.mobileOpen' });
  const route = router.route;
  const models = rootModels(ctx.schema);
  const application = ctx.session.application ?? {};
  const appName = application.playground ? 'Playground' : application.name || 'Evlet';
  const tenant = (ctx.session.tenant as { label?: string; name?: string } | undefined);
  const workspace = tenant?.label ?? tenant?.name ?? 'Workspace';
  // The theme button flips what the page shows now; from "system" it becomes an explicit choice.
  const device = systemScheme();
  const dark = () => resolveTheme(ctx.me.get()?.prefs.theme ?? 'system', device.get()) === 'dark';
  const flipTheme = () => ctx.toggleTheme();
  // Close the drawer and any menu when the page changes.
  effect(() => { route.get(); mobileOpen.set(false); closePopover(); });
  // Remember the page, so Back to app in Controls returns to it.
  effect(() => { route.get(); rememberAppPage(ctx.user.username, window.location.pathname + window.location.search, appName); });

  ctx.toggleSidebar = () => { if (isPhone()) mobileOpen.set(!mobileOpen.peek()); else collapsed.set(!collapsed.peek()); };

  const activeModel = derive(() => { const now = route.get(); return now && ['list', 'record', 'create'].includes(now.name) ? now.params.model : null; });
  const navItem = (props: { href: string; icon: string; tint?: any; label: string; active: () => boolean; count?: Child }) => {
    const item = html`<a class="nav-item" class:active=${props.active} aria-current=${() => props.active() ? 'page' : null} href=${props.href}>
      ${Icon(props.icon, { tint: props.tint })}<span class="lbl">${props.label}</span>${props.count ?? null}</a>` as HTMLElement;
    tooltip(item, () => collapsed.get() ? props.label : null, 'right');
    return item;
  };
  const reports = reportsOf(ctx);
  const groups = ['Documents', 'Records'].map(name => [name, models.filter(model => groupOf(model) === name)] as const).filter(([, list]) => list.length);
  // Record counts beside each model, when the person turned them on in Settings.
  const counts = cell<Record<string, number>>({});
  // Only turning counts on fetches them; saving other preferences does not.
  const showCounts = derive(() => Boolean(ctx.me.get()?.prefs.counts));
  effect(() => {
    if (!showCounts.get()) return;
    for (const model of models) void ctx.api.list(model.name, { limit: 1 }).then(page => counts.set({ ...counts.peek(), [model.name]: page.total }), () => {});
  });
  const modelItem = (model: ScreenModel) => {
    const { icon, tint } = identity(model);
    const count = () => ctx.me.get()?.prefs.counts && counts.get()[model.name] !== undefined
      ? html`<span class="count">${(n => n > 999 ? `${(n / 1000).toFixed(1)}k` : String(n))(counts.get()[model.name])}</span>` : null;
    return navItem({ href: router.href('list', { model: model.name }), icon, tint, label: model.plural, active: () => activeModel.get() === model.name, count });
  };

  // The one account area, at the bottom of the sidebar. Its menu follows the Settings design order.
  const settings = ctx.me;
  const fullName = () => settings.get()?.profile.full_name || ctx.user.username;
  const userMenu = (anchor: HTMLElement) => openPopover(anchor, close => {
    const me = settings.peek();
    const page = (label: string, icon: string, target: string): MenuItem => ({ label, icon, onSelect: () => void goSettings(router, target) });
    const items: MenuItem[] = me ? [
      page('Your profile', 'user', 'profile'), ...(me.portal ? [] : [page('Appearance', 'sun', 'appearance')]), page('Sign-in & security', 'lock', 'security'),
      ...(canChangePassword(me) ? [{ label: 'Change password', icon: 'lock', onSelect: () => openPasswordDialog(ctx.api, settings) }] : []),
      ...(me.portal ? [] : [{ label: dark() ? 'Light theme' : 'Dark theme', icon: dark() ? 'sun' : 'moon', onSelect: flipTheme }]),
      'separator', page('All settings', 'settings', me.last_page || 'profile'),
      ...(me.controls ? ['separator' as const, { label: 'Controls', icon: 'sliders-horizontal', hint: '↗', onSelect: () => { window.open('/controls', '_blank', 'noopener'); } }] : []),
      'separator',
    ] : [];
    return html`<div class="me-head">${PersonMark(fullName, () => Boolean(settings.get()?.profile.photo), ctx.photo, 'lg')}<span class="me-who"><strong>${fullName}</strong>
      <small>${() => settings.get()?.profile.email || ctx.user.role}</small></span></div>${Menu([...items, { label: 'Sign out', icon: 'log-out', onSelect: () => void ctx.signOut() }], close)}`;
  }, { label: 'Account' });

  const me = html`<button type="button" class="me" aria-haspopup="menu" aria-expanded="false" aria-label=${`Account: ${ctx.user.username}`}
    on:click=${(event: MouseEvent) => userMenu(event.currentTarget as HTMLElement)}>${PersonMark(() => displayName(settings.get(), ctx.user.username), () => Boolean(settings.get()?.profile.photo), ctx.photo)}
    <div style="min-width:0"><div class="n">${() => displayName(settings.get(), ctx.user.username)}</div><div class="e">${() => settings.get()?.profile.title || ctx.user.role}</div></div>
    ${Icon('chevrons-up-down', { size: 'xs', className: 'me-chev' })}</button>` as HTMLElement;

  // The app switcher: the current app over its workspace; it opens a grid of the apps in this workspace.
  const apps = [{ name: appName, icon: 'layers', tint: 'blue' as const }];
  const appMenu = (anchor: HTMLElement) => openPopover(anchor, close => html`<div class="app-grid-h">Apps in ${workspace}</div>
    <div class="app-grid" role="list">${apps.map(app => html`<button type="button" role="listitem" class="app-tile" class:on=${app.name === appName}
      aria-current=${app.name === appName ? 'true' : null} on:click=${close}>${IconTile(app.icon, { tint: app.tint, size: 32 })}<span>${app.name}</span></button>`)}</div>`,
  { label: 'Apps', role: 'dialog', className: 'app-pop', align: 'left' });
  const appSwitch = html`<button type="button" class="app-switch" aria-haspopup="dialog" aria-label=${`App: ${appName}, ${workspace}. Switch app`}
    on:click=${(event: MouseEvent) => appMenu(event.currentTarget as HTMLElement)}
    ref=${(el: HTMLElement) => tooltip(el, () => collapsed.get() ? `${appName} · ${workspace}` : null, 'right')}>${IconTile('layers', { tint: 'blue', size: 28 })}
    <span class="as-text"><b>${appName}</b><small>${workspace}</small></span>${Icon('chevrons-up-down', { size: 'xs', className: 'as-chev' })}</button>` as HTMLElement;

  const sidebar = html`<aside class="sidebar" id="ev-sidebar" aria-label="Main navigation">
    <div class="brand">${Lockup()}${Logo('Evlet')}</div>
    <div class="side-body">
      ${appSwitch}
      <nav class="nav">
        ${docsOn(ctx.session) ? navItem({ href: router.href('docs', {}), icon: 'file-text', tint: 'slate', label: 'Docs', active: () => ['docs', 'topic'].includes(route.get()?.name ?? '') }) : null}
        ${() => ctx.eva.get() ? navItem({ href: router.href('eva', {}), icon: 'sparkles', tint: 'violet', label: 'Eva', active: () => ['eva', 'evaChat', 'evaTasks'].includes(route.get()?.name ?? ''),
          count: () => ctx.eva.get()?.pending_approvals ? html`<span class="badge" title="Waiting for your approval">${ctx.eva.get()!.pending_approvals}</span>` : null }) : null}
        ${groups.map(([name, list]) => html`<div class="nav-group"><div class="nav-label">${name}</div>${list.map(modelItem)}</div>`)}
        ${reports.length ? html`<div class="nav-group"><div class="nav-label">Reports</div>${reportGroups(reports).map(([group, list]) => [
          group && reportGroups(reports).length > 1 ? html`<div class="nav-sub">${group}</div>` : null,
          list.map(report => navItem({ href: router.href('report', { report: report.name }), icon: REPORT_ICON, tint: 'rose', label: report.label,
            active: () => route.get()?.name === 'report' && route.get()?.params.report === report.name }))])}</div>` : null}
      </nav>
      ${ctx.dev ? html`<div class="side-bottom"><a class="nav-item" href=${ctx.libraryHref} rel="external" ref=${(el: HTMLElement) => tooltip(el, () => collapsed.get() ? 'Library' : null, 'right')}>
        ${Icon('layers', { tint: 'violet' })}<span class="lbl">Library</span></a></div>` : null}
      <div class="side-foot">${me}${when(() => Boolean(ctx.me.get() && !ctx.me.get()!.portal), () => html`<button type="button" class="icon-btn theme-btn"
        aria-label=${() => dark() ? 'Switch to light theme' : 'Switch to dark theme'} on:click=${flipTheme}
        ref=${(el: HTMLElement) => tooltip(el, () => dark() ? 'Light theme' : 'Dark theme')}>${() => Icon(dark() ? 'sun' : 'moon')}</button>`)}</div>
    </div></aside>` as HTMLElement;

  const creatable = models.filter(model => model.api !== 'read' && model.permissions?.create !== false);
  // Slips declared with menu = True open from New too; they save nothing until handed in.
  const menuSlips = ctx.schema.schema.slips ?? [];
  const crumbs = () => ctx.crumbs.get().map((crumb, index, all) => index === all.length - 1
    ? html`<span class="cur" aria-current="page">${crumb.label}</span>`
    : [html`<a class="hide-sm" href=${crumb.href ?? '#'}>${crumb.label}</a>`, Icon('chevron-right', { size: 'xs', className: 'hide-sm' })]);
  const topbar = html`<header class="topbar">
    <span class="mobile-only">${IconButton({ icon: 'panel-left', tip: 'Menu', onClick: () => ctx.toggleSidebar() })}</span>
    <nav class="crumbs" aria-label="Breadcrumbs">${crumbs}</nav>
    <div class="top-actions">
      ${RecentsButton(ctx, models)}
      <button type="button" class="top-search desktop-only" on:click=${() => ctx.openPalette()}>${Icon('search', { size: 'sm' })}<span>Search</span>${Key('⌘K')}</button>
      <span class="mobile-only">${IconButton({ icon: 'search', tip: 'Search', onClick: () => ctx.openPalette() })}</span>
      ${when(() => Boolean(ctx.me.get() && !ctx.me.get()!.portal), () => InboxButton(ctx))}
      <span class="desktop-only">${IconButton({ icon: 'circle-help', tip: 'Shortcuts  ?', onClick: () => openShortcuts(docsOn(ctx.session)) })}</span>
      ${creatable.length || menuSlips.length ? [html`<span class="sep desktop-only"></span>`, MenuButton({ label: 'New', icon: 'plus', variant: 'primary', size: 'small', hideLabelOnPhone: true, menuLabel: 'New',
        items: (): MenuItem[] => [...creatable.map(model => ({ label: `New ${model.label.toLowerCase()}`, icon: identity(model).icon, tint: identity(model).tint,
          onSelect: () => void router.go('create', { model: model.name }) })), ...(creatable.length && menuSlips.length ? ['separator' as const] : []),
          ...menuSlips.map(slip => ({ label: slip.label, icon: 'file-text', onSelect: () => void presentSlip(slip.name) }))] })] : null}
    </div></header>` as HTMLElement;

  // Collapse and expand from a small button on the sidebar's edge, just above the account card.
  const edge = html`<button type="button" class="side-edge desktop-only" aria-controls="ev-sidebar" aria-expanded=${() => String(!collapsed.get())}
    aria-label=${() => collapsed.get() ? 'Expand sidebar' : 'Collapse sidebar'} on:click=${() => ctx.toggleSidebar()}
    ref=${(el: HTMLElement) => tooltip(el, () => collapsed.get() ? 'Expand  [' : 'Collapse  [', 'right')}>${() => Icon(collapsed.get() ? 'chevron-right' : 'chevron-left', { size: 'xs' })}</button>` as HTMLElement;

  const shell = html`<div class="app" class:collapsed=${collapsed} class:mobile-open=${mobileOpen} class:compact=${() => (ctx.me.get()?.prefs.density ?? prefs.density.get()) === 'compact'}
    class:large=${() => ctx.me.get()?.prefs.text === 'large'}>
    ${sidebar}${edge}<div class="main">${topbar}<main class="view" id="ev-view" tabindex="-1">${view}</main></div>
    <div class="scrim ev-side-scrim" class:show=${mobileOpen} on:click=${() => mobileOpen.set(false)}></div></div>` as HTMLElement;
  listen(document, 'keydown', event => keys(ctx, event as KeyboardEvent));
  mountToasts();
  return shell;
}

// ---------- keyboard ----------

let pendingG = 0;
function keys(ctx: AppContext, event: KeyboardEvent) {
  const mod = event.metaKey || event.ctrlKey;
  if (mod && event.key.toLowerCase() === 'k') { event.preventDefault(); ctx.openPalette(); return; }
  const active = document.activeElement as HTMLElement | null;
  const typing = !!active && (/INPUT|TEXTAREA|SELECT/.test(active.tagName) || active.isContentEditable);
  if (typing || mod || event.altKey || event.defaultPrevented || document.querySelector('.modal-wrap.show,.drawer.show')) return;
  const key = event.key.toLowerCase();
  if (pendingG && Date.now() - pendingG < 900) {
    pendingG = 0;
    if ((key === 'd' || key === 'h') && docsOn(ctx.session)) { event.preventDefault(); void ctx.router.go('docs', {}); }
    return;
  }
  if (key === 'g') { pendingG = Date.now(); return; }
  if (event.key === '[') { event.preventDefault(); ctx.toggleSidebar(); return; }
  if (event.key === '?') { event.preventDefault(); openShortcuts(docsOn(ctx.session)); return; }
  if (event.key === '/') { const input = ctx.pageSearch.peek(); if (input?.isConnected) { event.preventDefault(); input.focus(); } return; }
  if (key === 'c') { const create = ctx.createHere.peek(); if (create) { event.preventDefault(); create(); } }
}

const SHORTCUTS: Array<[string, string]> = [
  ['⌘K or Ctrl K', 'Search and commands'], ['C', 'Create in the current list'], ['/', 'Focus the page search'], ['G then D', 'Go to Docs'],
  ['[', 'Toggle the sidebar'], ['?', 'Shortcuts'], ['⌘ Enter', 'Save a form or dialog'], ['Esc', 'Close the top layer, then clear the selection'],
];

export function openShortcuts(docs = true) {
  const shown = SHORTCUTS.filter(([, what]) => docs || what !== 'Go to Docs');
  return openModal({ title: 'Keyboard shortcuts', icon: 'command', tint: 'slate', narrow: true,
    body: () => html`<dl class="ev-keys">${shown.map(([keys, what]) => html`<dt>${keys.split(' or ').map((part, index) => [index ? ' or ' : '', Key(part)])}</dt><dd>${what}</dd>`)}</dl>` });
}
