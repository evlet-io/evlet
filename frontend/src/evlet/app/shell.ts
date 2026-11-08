// The app shell (docs/ui-design/README.md section 4): one top bar across the window with the Evlet brand,
// a pipe and the Workspace switcher at its left (as wide as the sidebar), then recent records, breadcrumbs,
// one box with search and the Explorer (docs/ui.md#top-bar-search-and-explorer), notifications and
// shortcuts; under it the sidebar with the workspace's collapsible sections of shortcuts (app/workspaces.ts),
// the account area and an edge button that collapses it, beside one scrolling view. Under 760 px the
// sidebar is a drawer opened from the top bar.

import { html, listen, when, type Child } from '../dom';
import { cell, derive, effect } from '../reactive';
import { Icon, IconButton, IconTile, Key } from '../ui/base';
import { tooltip } from '../ui/tooltip';
import { mountToasts, openPopover, Menu, closePopover, type MenuItem } from '../ui/layers';
import { Logo } from './login';
import { resolveTheme, systemScheme } from '../ui/theme';
import { docsOn, goSettings, type AppContext } from './context';
import { canChangePassword, displayName, openPasswordDialog, PersonMark } from './settings';
import { InboxButton } from './inbox';
import { RecentsButton } from './recents';
import { rememberAppPage } from './last-page';
import { rootModels } from './meta';
import { activeShortcut, modelCounts, workspaceNav, workspacesVersion, type NavSection, type NavShortcut, type Workspace } from './workspaces';
import { presentSlip } from './slips';
import { customizeAccess, openWorkspaceEditor } from './workspace-editor';
import { isExplorer, openExplorer } from './explorer';
import { SHELL_ICONS } from './nav-icons';
import { openShortcutSheet } from './shortcuts';
import { formatKeys, withShortcut } from '../ui/shortcuts';

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
  // The site beside the workspace name: the tenant's label, else the application's own name ("Lab",
  // "Playground"); nothing for an unbranded single site.
  const site = tenant?.label ?? tenant?.name ?? (appName === 'Evlet' ? '' : appName);
  // The theme button flips what the page shows now; from "system" it becomes an explicit choice.
  const device = systemScheme();
  const dark = () => resolveTheme(ctx.me.get()?.prefs.theme ?? 'system', device.get()) === 'dark';
  const flipTheme = () => ctx.toggleTheme();
  // Close the drawer and any menu when the page changes.
  effect(() => { route.get(); mobileOpen.set(false); closePopover(); });
  // Remember the page, so Back to app in Controls returns to it.
  effect(() => { route.get(); rememberAppPage(ctx.user.username, window.location.pathname + window.location.search, appName); });

  ctx.toggleSidebar = () => { if (isPhone()) mobileOpen.set(!mobileOpen.peek()); else collapsed.set(!collapsed.peek()); };

  const navItem = (props: { href: string; icon: string; tint?: any; label: string; active: () => boolean; count?: Child }) => {
    const item = html`<a class="nav-item" class:active=${props.active} aria-current=${() => props.active() ? 'page' : null} href=${props.href}>
      ${Icon(props.icon, { tint: props.tint })}<span class="lbl">${props.label}</span>${props.count ?? null}</a>` as HTMLElement;
    tooltip(item, () => collapsed.get() ? props.label : null, 'right');
    return item;
  };
  // Record counts beside each model, when the person turned them on in Settings.
  const counts = modelCounts(ctx);
  // Only turning counts on fetches them; saving other preferences does not.
  const showCounts = derive(() => Boolean(ctx.me.get()?.prefs.counts));
  effect(() => {
    if (!showCounts.get()) return;
    for (const model of models) void ctx.api.list(model.name, { limit: 1 }).then(page => counts.set({ ...counts.peek(), [model.name]: page.total }), () => {});
  });
  const countOf = (item: NavShortcut) => () => {
    const value = item.model && ctx.me.get()?.prefs.counts ? counts.get()[item.model] : undefined;
    return value === undefined ? null : html`<span class="count">${value > 999 ? `${(value / 1000).toFixed(1)}k` : String(value)}</span>`;
  };

  // Workspaces: the switcher picks one, and the sidebar shows its sections and shortcuts.
  // Saving a workspace override hands the shell the schema's new list (workspacesVersion), and the sidebar follows.
  const version = workspacesVersion(ctx);
  const nav = derive(() => { version.get(); return workspaceNav(ctx.schema.schema, { list: (model, query) => router.href('list', { model }, query ?? {}), report: report => router.href('report', { report }) }, appName); });
  const chosen = prefs.cell<string>('nav.workspace', nav.peek().workspaces[0]?.key ?? '');
  const here = derive(() => { const now = route.get(); return now ? activeShortcut(nav.get(), now.url, chosen.get()) : null; });
  // Opening a shortcut of another workspace (a link, Back, the palette) shows that workspace.
  effect(() => { const found = here.get(); if (found && found.workspace.key !== chosen.peek()) chosen.set(found.workspace.key); });
  const current = derive(() => nav.get().workspaces.find(workspace => workspace.key === chosen.get()) ?? nav.get().workspaces[0] ?? null);
  // Open or closed sections, per person on this device; a section is open until closed.
  const closed = prefs.cell<Record<string, boolean>>('nav.closed', {});
  const sectionId = (workspace: Workspace, section: NavSection) => `${workspace.key}.${section.key}`;
  const isOpen = (workspace: Workspace, section: NavSection) => !closed.get()[sectionId(workspace, section)];
  const toggleSection = (workspace: Workspace, section: NavSection) => {
    const id = sectionId(workspace, section), next = { ...closed.peek() };
    if (next[id]) delete next[id]; else next[id] = true;
    closed.set(next);
  };
  const shortcut = (item: NavShortcut) => {
    const active = () => here.get()?.item.key === item.key && here.get()?.workspace.key === current.get()?.key;
    // The kind's icon (app/nav-icons.ts), small and muted, in place of a dot; white on the active row.
    const inside = [Icon(item.icon, { size: 'sm', className: 'nav-ic' }), html`<span class="lbl">${item.label}</span>`, countOf(item)];
    // A menu slip opens over the page; an http(s) address opens in a new tab.
    const link = (item.slip ? html`<button type="button" class="nav-item nav-sc" data-kind=${item.kind} on:click=${() => void presentSlip(item.slip!)}>${inside}</button>`
      : item.external ? html`<a class="nav-item nav-sc" data-kind=${item.kind} href=${item.href} target="_blank" rel="noopener external">${inside}</a>`
      : html`<a class="nav-item nav-sc" data-kind=${item.kind} class:active=${active} aria-current=${() => active() ? 'page' : null} href=${item.href}>${inside}</a>`) as HTMLElement;
    tooltip(link, () => collapsed.get() ? item.label : null, 'right');
    return link;
  };
  const section = (workspace: Workspace, entry: NavSection) => {
    const id = `ev-nav-${sectionId(workspace, entry).replace(/[^a-z0-9_-]/gi, '-')}`;
    // The section holding the page's shortcut is tinted and its title bolder, open or closed.
    const holdsPage = () => here.get()?.section.key === entry.key && here.get()?.workspace.key === workspace.key && current.get()?.key === workspace.key;
    return html`<div class="nav-sec" class:open=${() => isOpen(workspace, entry)} class:on=${holdsPage}>
      <button type="button" class="nav-sec-h" aria-expanded=${() => String(isOpen(workspace, entry))} aria-controls=${id} on:click=${() => toggleSection(workspace, entry)}>
        ${Icon(entry.icon, { size: 'sm', className: 'nav-sec-ic' })}<span class="lbl">${entry.label}</span>
        ${entry.count === undefined ? null : html`<span class="count">${String(entry.count)}</span>`}${Icon('chevron-right', { size: 'xs', className: 'nav-sec-chev' })}</button>
      <div class="nav-sec-items" id=${id} role="group" aria-label=${entry.label}>${entry.items.map(shortcut)}</div></div>`;
  };
  // The one account area, at the bottom of the sidebar. Its menu follows the Settings design order.
  const settings = ctx.me;
  const fullName = () => settings.get()?.profile.full_name || ctx.user.username;
  const userMenu = (anchor: HTMLElement) => openPopover(anchor, close => {
    const me = settings.peek();
    const page = (label: string, icon: string, target: string): MenuItem => ({ label, icon, onSelect: () => void goSettings(router, target) });
    const items: MenuItem[] = me ? [
      page('Your profile', SHELL_ICONS.account, 'profile'), ...(me.portal ? [] : [page('Appearance', 'sun', 'appearance')]), page('Sign-in & security', 'lock', 'security'),
      ...(canChangePassword(me) ? [{ label: 'Change password', icon: 'lock', onSelect: () => openPasswordDialog(ctx.api, settings) }] : []),
      ...(me.portal ? [] : [{ label: dark() ? 'Light theme' : 'Dark theme', icon: dark() ? 'sun' : 'moon', onSelect: flipTheme }]),
      'separator', page('All settings', SHELL_ICONS.settings, me.last_page || 'profile'),
      ...(me.controls ? ['separator' as const, { label: 'Controls', icon: SHELL_ICONS.controls, hint: '↗', onSelect: () => { window.open('/controls', '_blank', 'noopener'); } }] : []),
      'separator',
    ] : [];
    return html`<div class="me-head">${PersonMark(fullName, () => Boolean(settings.get()?.profile.photo), ctx.photo, 'lg')}<span class="me-who"><strong>${fullName}</strong>
      <small>${() => settings.get()?.profile.email || ctx.user.role}</small></span></div>${Menu([...items, { label: 'Sign out', icon: 'log-out', onSelect: () => void ctx.signOut() }], close)}`;
  }, { label: 'Account' });

  const me = html`<button type="button" class="me" aria-haspopup="menu" aria-expanded="false" aria-label=${`Account: ${ctx.user.username}`}
    on:click=${(event: MouseEvent) => userMenu(event.currentTarget as HTMLElement)}>${PersonMark(() => displayName(settings.get(), ctx.user.username), () => Boolean(settings.get()?.profile.photo), ctx.photo)}
    <div style="min-width:0"><div class="n">${() => displayName(settings.get(), ctx.user.username)}</div><div class="e">${() => settings.get()?.profile.title || ctx.user.role}</div></div>
    ${Icon('chevrons-up-down', { size: 'xs', className: 'me-chev' })}</button>` as HTMLElement;

  // The Workspace switcher, in the top bar after the brand: the current workspace; it opens a card below it,
  // left-aligned, with every workspace as a tile.
  const pickWorkspace = (workspace: Workspace) => {
    // The workspace is chosen once its first page is open: until then the old page would pick its own workspace back.
    const first = workspace.sections.flatMap(entry => entry.items).find(item => item.href && !item.slip && !item.external);
    if (first) void router.navigate(first.href).then(opened => { if (opened) chosen.set(workspace.key); });
    else chosen.set(workspace.key);
  };
  const workspaceMenu = (anchor: HTMLElement) => openPopover(anchor, close => {
    const access = customizeAccess(ctx);
    const tiles = nav.peek().workspaces.map(workspace => html`<button type="button" class="ws-tile" class:on=${() => current.get() === workspace}
      aria-current=${() => current.get() === workspace ? 'true' : null} on:click=${() => { close(); pickWorkspace(workspace); }}>
      ${IconTile(workspace.icon, { tint: workspace.tint, size: 32 })}<span>${workspace.label}</span></button>` as HTMLButtonElement);
    const row = html`<div class="ws-row" role="group" aria-label="Workspaces">${tiles}</div>` as HTMLElement;
    row.addEventListener('keydown', event => {
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      const at = tiles.indexOf(document.activeElement as HTMLButtonElement);
      let next = step === undefined ? (event.key === 'Home' ? 0 : event.key === 'End' ? tiles.length - 1 : -1) : (at + step + tiles.length) % tiles.length;
      if (next < 0) return;
      event.preventDefault();
      tiles[next].focus();
    });
    queueMicrotask(() => (tiles.find(tile => tile.classList.contains('on')) ?? tiles[0])?.focus());
    // Admins and roles with "Customize workspaces" edit the site's workspaces from here.
    const customize = () => access.get() ? html`<button type="button" class="ws-edit" on:click=${() => { close(); openWorkspaceEditor(ctx, current.peek()?.key ?? null); }}>
      ${Icon(SHELL_ICONS.customize, { size: 'xs' })}<span>Customize</span></button>` : null;
    return html`<div class="ws-head"><span>Workspace</span>${customize}</div>${row}`;
  }, { label: 'Workspace', role: 'dialog', className: 'ws-pop', align: 'left' });
  const wsLabel = () => current.get()?.label ?? appName;
  // The site shows after the workspace name, unless it says the same thing.
  const wsSite = () => site && site !== wsLabel() ? site : '';
  const wsTip = () => wsSite() ? `Workspace: ${wsLabel()} · ${wsSite()}` : `Workspace: ${wsLabel()}`;
  const appSwitch = html`<button type="button" class="app-switch" aria-haspopup="dialog" aria-expanded="false" aria-label=${() => `${wsTip()}. Switch workspace`}
    on:click=${(event: MouseEvent) => workspaceMenu(event.currentTarget as HTMLElement)}
    ref=${(el: HTMLElement) => tooltip(el, wsTip)}><span class="as-text"><b>${wsLabel}</b>${() => wsSite() ? html`<small>${wsSite()}</small>` : null}</span>${Icon('chevron-down', { size: 'xs', className: 'as-chev' })}</button>` as HTMLElement;
  // The brand at the top bar's left: the Evlet mark (white on a blue square) and the word; the collapsed
  // sidebar and phones show the mark alone.
  const brand = html`<span class="top-brand"><span class="tb-mark">${Logo('Evlet')}</span><b class="tb-name" aria-hidden="true">Evlet</b></span>`;

  const sidebar = html`<aside class="sidebar" id="ev-sidebar" aria-label="Main navigation">
    <div class="side-body">
      <nav class="nav">
        ${docsOn(ctx.session) ? navItem({ href: router.href('docs', {}), icon: SHELL_ICONS.docs, tint: 'slate', label: 'Docs', active: () => ['docs', 'topic'].includes(route.get()?.name ?? '') }) : null}
        ${() => ctx.eva.get() ? navItem({ href: router.href('eva', {}), icon: SHELL_ICONS.eva, tint: 'violet', label: 'Eva', active: () => ['eva', 'evaChat', 'evaTasks'].includes(route.get()?.name ?? ''),
          count: () => ctx.eva.get()?.pending_approvals ? html`<span class="badge" title="Waiting for your approval">${ctx.eva.get()!.pending_approvals}</span>` : null }) : null}
        ${() => { const workspace = current.get(); return workspace ? html`<div class="nav-secs">${workspace.sections.map(entry => section(workspace, entry))}</div>` : null; }}
      </nav>
      ${ctx.dev ? html`<div class="side-bottom"><a class="nav-item" href=${ctx.libraryHref} rel="external" ref=${(el: HTMLElement) => tooltip(el, () => collapsed.get() ? 'Library' : null, 'right')}>
        ${Icon('layers', { tint: 'violet' })}<span class="lbl">Library</span></a></div>` : null}
      <div class="side-foot">${me}${when(() => Boolean(ctx.me.get() && !ctx.me.get()!.portal), () => html`<button type="button" class="icon-btn theme-btn"
        aria-label=${() => dark() ? 'Switch to light theme' : 'Switch to dark theme'} on:click=${flipTheme}
        ref=${(el: HTMLElement) => tooltip(el, () => dark() ? 'Light theme' : 'Dark theme')}>${() => Icon(dark() ? 'sun' : 'moon')}</button>`)}</div>
    </div></aside>` as HTMLElement;

  const crumbs = () => ctx.crumbs.get().map((crumb, index, all) => index === all.length - 1
    ? html`<span class="cur" aria-current="page">${crumb.label}</span>`
    : [html`<a class="hide-sm" href=${crumb.href ?? '#'}>${crumb.label}</a>`, Icon('chevron-right', { size: 'xs', className: 'hide-sm' })]);
  // Search and the Explorer: two buttons in one box (docs/ui.md#top-bar-search-and-explorer). The search part
  // opens the palette; the Explorer part opens the Explorer on its last view and is pressed while it is open.
  const onExplorer = () => isExplorer(route.get());
  const searchPart = html`<button type="button" class="ts-search" aria-label="Search" aria-keyshortcuts="Meta+K Control+K" on:click=${() => ctx.openPalette()}
    ref=${(el: HTMLElement) => tooltip(el, withShortcut('Search', 'Mod+K'))}>${Icon(SHELL_ICONS.search, { size: 'sm' })}<span class="ts-text">Search</span>${Key(formatKeys('Mod+K'))}</button>` as HTMLElement;
  const explorerPart = html`<button type="button" class="ts-explorer" class:on=${onExplorer} aria-label="Explorer" aria-pressed=${() => String(onExplorer())}
    on:click=${() => openExplorer(ctx)} ref=${(el: HTMLElement) => tooltip(el, 'Explorer  G E')}>
    ${Icon(SHELL_ICONS.explorer, { size: 'sm' })}<span class="ts-text">Explorer</span></button>` as HTMLElement;
  const searchBox = html`<div class="top-search" role="group" aria-label="Search and Explorer">${searchPart}<span class="ts-div" aria-hidden="true"></span>${explorerPart}</div>`;
  const topbar = html`<header class="topbar">
    <span class="mobile-only">${IconButton({ icon: SHELL_ICONS.menu, tip: 'Menu', onClick: () => ctx.toggleSidebar() })}</span>
    <div class="top-left">${brand}<span class="top-pipe" aria-hidden="true"></span>${appSwitch}</div>
    ${RecentsButton(ctx, models)}
    <nav class="crumbs" aria-label="Breadcrumbs">${crumbs}</nav>
    <div class="top-actions">
      ${searchBox}
      ${when(() => Boolean(ctx.me.get() && !ctx.me.get()!.portal), () => InboxButton(ctx))}
      <span class="desktop-only">${IconButton({ icon: SHELL_ICONS.help, tip: 'Shortcuts  ?', onClick: () => openShortcuts(docsOn(ctx.session)) })}</span>
    </div></header>` as HTMLElement;

  // Collapse and expand from a small button on the sidebar's edge, just above the account card.
  const edge = html`<button type="button" class="side-edge desktop-only" aria-controls="ev-sidebar" aria-expanded=${() => String(!collapsed.get())}
    aria-label=${() => collapsed.get() ? 'Expand sidebar' : 'Collapse sidebar'} on:click=${() => ctx.toggleSidebar()}
    ref=${(el: HTMLElement) => tooltip(el, () => collapsed.get() ? 'Expand  [' : 'Collapse  [', 'right')}>${() => Icon(collapsed.get() ? 'chevron-right' : 'chevron-left', { size: 'xs' })}</button>` as HTMLElement;

  const shell = html`<div class="app" class:collapsed=${collapsed} class:mobile-open=${mobileOpen} class:compact=${() => (ctx.me.get()?.prefs.density ?? prefs.density.get()) === 'compact'}
    class:large=${() => ctx.me.get()?.prefs.text === 'large'}>
    ${topbar}${sidebar}${edge}<div class="main"><main class="view" id="ev-view" tabindex="-1">${view}</main></div>
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
    else if (key === 'e') { event.preventDefault(); void openExplorer(ctx); }
    return;
  }
  if (key === 'g') { pendingG = Date.now(); return; }
  if (event.key === '[') { event.preventDefault(); ctx.toggleSidebar(); return; }
  if (event.key === '?') { event.preventDefault(); openShortcuts(docsOn(ctx.session)); return; }
  if (event.key === '/') { const input = ctx.pageSearch.peek(); if (input?.isConnected) { event.preventDefault(); input.focus(); } return; }
  if (key === 'c') { const create = ctx.createHere.peek(); if (create) { event.preventDefault(); create(); } }
}

const SHORTCUTS: Array<[string, string]> = [
  ['⌘K or Ctrl K', 'Search and commands'], ['C', 'Create in the current list'], ['/', 'Focus the page search'], ['G then D', 'Go to Docs'], ['G then E', 'Open the Explorer'],
  ['[', 'Toggle the sidebar'], ['?', 'Shortcuts'], ['⌘ Enter', 'Save a form or dialog'], ['Esc', 'Close the top layer, then clear the selection'],
];

export function openShortcuts(docs = true) {
  const shown = SHORTCUTS.filter(([, what]) => docs || what !== 'Go to Docs');
  return openShortcutSheet(shown);
}
