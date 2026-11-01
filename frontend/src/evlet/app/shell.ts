// The app shell (docs/ui-design/README.md section 4): sidebar with the workspace row, the app
// bar, the models grouped and recent records; the top bar with the sidebar toggle, breadcrumbs,
// search, shortcuts and New; one scrolling view. Under 760 px the sidebar is a drawer.

import { html, listen, when, type Child } from '../dom';
import { cell, derive, effect } from '../reactive';
import { Avatar, Icon, IconButton, Key } from '../ui/base';
import { tooltip } from '../ui/tooltip';
import { MenuButton, mountToasts, openModal, openPopover, Menu, closePopover, type MenuItem } from '../ui/layers';
import { applyTheme, type Theme } from './prefs';
import { docsOn, type AppContext } from './context';
import { groupOf, identity, rootModels, type ScreenModel } from './meta';

const PHONE = '(max-width: 760px)';
const isPhone = () => { try { return Boolean(window.matchMedia?.(PHONE)?.matches); } catch { return false; } };

export function Shell(ctx: AppContext, view: () => Child): HTMLElement {
  const { prefs, router } = ctx;
  const collapsed = prefs.collapsed, theme = prefs.theme;
  const mobileOpen = cell(false, { name: 'shell.mobileOpen' });
  const route = router.route;
  const models = rootModels(ctx.schema);
  const application = ctx.session.application ?? {};
  const appName = application.playground ? 'Playground' : application.name || 'Evlet';
  const tenant = (ctx.session.tenant as { label?: string; name?: string } | undefined);
  const workspace = tenant?.label ?? tenant?.name ?? 'Workspace';
  effect(() => applyTheme(theme.get()));
  // Close the drawer and any menu when the page changes.
  effect(() => { route.get(); mobileOpen.set(false); closePopover(); });

  ctx.toggleSidebar = () => { if (isPhone()) mobileOpen.set(!mobileOpen.peek()); else collapsed.set(!collapsed.peek()); };

  const activeModel = derive(() => { const now = route.get(); return now && ['list', 'record', 'create'].includes(now.name) ? now.params.model : null; });
  const navItem = (props: { href: string; icon: string; tint?: any; label: string; active: () => boolean; count?: Child }) => {
    const item = html`<a class="nav-item" class:active=${props.active} aria-current=${() => props.active() ? 'page' : null} href=${props.href}>
      ${Icon(props.icon, { tint: props.tint })}<span class="lbl">${props.label}</span>${props.count ?? null}</a>` as HTMLElement;
    tooltip(item, () => collapsed.get() ? props.label : null, 'right');
    return item;
  };
  const groups = ['Documents', 'Records'].map(name => [name, models.filter(model => groupOf(model) === name)] as const).filter(([, list]) => list.length);
  const modelItem = (model: ScreenModel) => {
    const { icon, tint } = identity(model);
    return navItem({ href: router.href('list', { model: model.name }), icon, tint, label: model.plural, active: () => activeModel.get() === model.name });
  };
  const recents = derive(() => prefs.recentRecords.get().filter(item => models.some(model => model.name === item.model)).slice(0, 5));

  const themeItems = (): MenuItem[] => ([['system', 'Use system theme', 'monitor'], ['light', 'Light theme', 'sun'], ['dark', 'Dark theme', 'moon']] as Array<[Theme, string, string]>)
    .map(([value, label, icon]) => ({ label: theme.peek() === value ? `${label} (on)` : label, icon: icon === 'monitor' ? 'settings' : icon, onSelect: () => theme.set(value) }));
  const userMenu = (anchor: HTMLElement) => openPopover(anchor, close => Menu([
    { heading: `${ctx.user.username} · ${ctx.user.role}` },
    ...themeItems(),
    'separator',
    { label: prefs.density.peek() === 'compact' ? 'Comfortable density' : 'Compact density', icon: 'rows-3', onSelect: () => prefs.density.set(prefs.density.peek() === 'compact' ? 'comfortable' : 'compact') },
    'separator',
    { label: 'Sign out', icon: 'log-out', onSelect: () => void ctx.signOut() },
  ], close), { label: 'Account' });
  const isDark = () => theme.get() === 'dark' || (theme.get() === 'system' && (() => { try { return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ?? false; } catch { return false; } })());

  const me = html`<button type="button" class="me" aria-haspopup="menu" aria-expanded="false" aria-label=${`Account: ${ctx.user.username}`}
    on:click=${(event: MouseEvent) => userMenu(event.currentTarget as HTMLElement)}>${Avatar({ name: ctx.user.username, size: 'md' })}
    <div style="min-width:0"><div class="n">${ctx.user.username}</div><div class="e">${ctx.user.role}</div></div></button>` as HTMLElement;

  const sidebar = html`<aside class="sidebar" id="ev-sidebar" aria-label="Main navigation">
    <div class="ws"><div class="ws-btn"><span class="logo">${appName.slice(0, 1).toUpperCase()}</span><span class="ws-text" style="min-width:0;flex:1">
      <div class="ws-name">${appName}</div><div class="ws-sub">${workspace}</div></span></div></div>
    <div class="appbar" role="tablist" aria-label="Apps"><button type="button" class="ab-app on" role="tab" aria-selected="true" aria-label=${appName}
      ref=${(el: HTMLElement) => tooltip(el, appName)}><span class="b-blue">${Icon('layers', { size: 'sm' })}</span></button><span class="grow"></span></div>
    <div class="side-body">
      <div class="side-app"><b>${appName}</b><span>${workspace}</span></div>
      <nav class="nav">
        ${docsOn(ctx.session) ? navItem({ href: router.href('docs', {}), icon: 'file-text', tint: 'slate', label: 'Docs', active: () => ['docs', 'topic', 'home'].includes(route.get()?.name ?? '') }) : null}
        ${groups.map(([name, list]) => html`<div class="nav-group"><div class="nav-label">${name}</div>${list.map(modelItem)}</div>`)}
        ${when(() => recents.get().length > 0, () => html`<div class="nav-group"><div class="nav-label">Recent</div>${() => recents.get().map(item => {
          const model = models.find(entry => entry.name === item.model)!;
          return navItem({ href: router.href('record', { model: item.model, id: String(item.id) }), icon: identity(model).icon, tint: 'slate', label: item.title,
            active: () => route.get()?.name === 'record' && route.get()?.params.model === item.model && route.get()?.params.id === String(item.id) });
        })}</div>`)}
      </nav>
      ${ctx.dev ? html`<div class="side-bottom"><a class="nav-item" href=${ctx.libraryHref} rel="external" ref=${(el: HTMLElement) => tooltip(el, () => collapsed.get() ? 'Library' : null, 'right')}>
        ${Icon('layers', { tint: 'violet' })}<span class="lbl">Library</span></a></div>` : null}
      <div class="side-foot">${me}${IconButton({ icon: 'moon', tip: () => isDark() ? 'Light theme' : 'Dark theme', onClick: () => theme.set(isDark() ? 'light' : 'dark') })}</div>
    </div></aside>` as HTMLElement;

  const creatable = models.filter(model => model.permissions?.create !== false);
  const crumbs = () => ctx.crumbs.get().map((crumb, index, all) => index === all.length - 1
    ? html`<span class="cur" aria-current="page">${crumb.label}</span>`
    : [html`<a class="hide-sm" href=${crumb.href ?? '#'}>${crumb.label}</a>`, Icon('chevron-right', { size: 'xs', className: 'hide-sm' })]);
  const topbar = html`<header class="topbar">
    ${IconButton({ icon: 'panel-left', tip: 'Toggle sidebar  [', onClick: () => ctx.toggleSidebar() })}
    <span class="sep desktop-only" style="margin:0 2px"></span>
    <nav class="crumbs" aria-label="Breadcrumbs">${crumbs}</nav>
    <div class="top-actions">
      <button type="button" class="top-search desktop-only" on:click=${() => ctx.openPalette()}>${Icon('search', { size: 'sm' })}<span>Search</span>${Key('⌘K')}</button>
      <span class="mobile-only">${IconButton({ icon: 'search', tip: 'Search', onClick: () => ctx.openPalette() })}</span>
      <span class="desktop-only">${IconButton({ icon: 'circle-help', tip: 'Shortcuts  ?', onClick: () => openShortcuts(docsOn(ctx.session)) })}</span>
      ${creatable.length ? [html`<span class="sep desktop-only"></span>`, MenuButton({ label: 'New', icon: 'plus', variant: 'primary', size: 'small', hideLabelOnPhone: true, menuLabel: 'New',
        items: () => creatable.map(model => ({ label: `New ${model.label.toLowerCase()}`, icon: identity(model).icon, tint: identity(model).tint,
          onSelect: () => void router.go('create', { model: model.name }) })) })] : null}
    </div></header>` as HTMLElement;

  const shell = html`<div class="app" class:collapsed=${collapsed} class:mobile-open=${mobileOpen} class:compact=${() => prefs.density.get() === 'compact'}>
    ${sidebar}<div class="main">${topbar}<main class="view" id="ev-view" tabindex="-1">${view}</main></div>
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
