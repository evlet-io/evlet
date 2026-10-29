import { createEffect, For, onCleanup, onMount, Show, splitProps, type JSX } from 'solid-js';
import { cx, type ElementProps } from '../types';
import { OverlayContext } from './context';
import { callHandler, contentSlot, forwardRef, mergeStyle, withoutRef } from './helpers';

export interface AppShellProps extends ElementProps {
  sidebar?: JSX.Element;
  topBar?: JSX.Element;
  statusBar?: JSX.Element;
  overlays?: JSX.Element;
  sidebarWidth?: string;
  sidebarOpen?: boolean;
  onSidebarClose?: () => void;
  viewHostProps?: ViewHostProps;
  overlayProps?: ElementProps;
}
export function AppShell(props: AppShellProps) {
  const [p, rest] = splitProps(props, [
    'class',
    'classList',
    'style',
    'children',
    'sidebar',
    'topBar',
    'statusBar',
    'overlays',
    'sidebarWidth',
    'sidebarOpen',
    'onSidebarClose',
    'viewHostProps',
    'overlayProps',
    'ref',
    'onKeyDown',
  ]);
  let overlay: HTMLDivElement | undefined,
    shell!: HTMLDivElement,
    nav!: HTMLDivElement,
    main!: HTMLElement;
  let media: MediaQueryList;
  let previousFocus: HTMLElement | undefined;
  let wasOpen = false;
  const sync = () => {
    const small = media?.matches ?? false;
    nav.inert = small && !p.sidebarOpen;
    main.inert = small && !!p.sidebarOpen;
  };
  onMount(() => {
    media = matchMedia('(max-width:760px)');
    sync();
    media.addEventListener('change', sync);
    onCleanup(() => media.removeEventListener('change', sync));
  });
  createEffect(() => {
    const open = !!p.sidebarOpen;
    sync();
    if (open && !wasOpen) {
      previousFocus = document.activeElement as HTMLElement;
      queueMicrotask(() =>
        (
          nav.querySelector<HTMLElement>('[aria-current="page"]') ??
          nav.querySelector<HTMLElement>('button,a')
        )?.focus(),
      );
    } else if (!open && wasOpen) {
      previousFocus?.focus();
    }
    wasOpen = open;
  });
  const keys: JSX.EventHandler<HTMLDivElement, KeyboardEvent> = (e) => {
    if (!p.sidebarOpen || !media?.matches) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      p.onSidebarClose?.();
    }
    if (e.key === 'Tab') {
      const nodes = Array.from(
        nav.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],[tabindex="0"]'),
      );
      if (!nodes.length) return;
      if (e.shiftKey && document.activeElement === nodes[0]) {
        e.preventDefault();
        nodes.at(-1)?.focus();
      } else if (!e.shiftKey && document.activeElement === nodes.at(-1)) {
        e.preventDefault();
        nodes[0].focus();
      }
    }
  };
  const ShellContent = () => {
    const sidebarContent = contentSlot(() => p.sidebar);
    return (
      <div
        {...rest}
        ref={(el) => {
          shell = el;
          forwardRef(p.ref, el);
        }}
        class={cx(
          'ev-layout',
          p.sidebarOpen && 'ev-navigation-open',
          !sidebarContent() && 'ev-without-sidebar',
          p.class,
        )}
        classList={p.classList}
        style={mergeStyle({ '--ev-sidebar-width': p.sidebarWidth }, p.style)}
        onKeyDown={(e) => {
          keys(e);
          callHandler(p.onKeyDown, e);
        }}
      >
        <div ref={nav} class="ev-sidebar-slot">
          {sidebarContent()}
        </div>
        <Show when={p.sidebarOpen}>
          <button
            class="ev-sidebar-backdrop"
            aria-label="Close navigation"
            onClick={p.onSidebarClose}
          />
        </Show>
        <main ref={main} class="ev-main">
          {p.topBar}
          <ViewHost {...p.viewHostProps}>
            {p.children}
            {p.statusBar}
          </ViewHost>
        </main>
        <OverlayLayer
          {...withoutRef(p.overlayProps)}
          ref={(el) => {
            overlay = el;
            forwardRef(p.overlayProps?.ref, el);
          }}
        >
          {p.overlays}
        </OverlayLayer>
      </div>
    );
  };
  return (
    <OverlayContext.Provider value={() => overlay}>
      <ShellContent />
    </OverlayContext.Provider>
  );
}
export interface SidebarProps extends ElementProps<HTMLElement> {
  brand?: JSX.Element;
  workspaceSwitcher?: JSX.Element;
  navigation?: JSX.Element;
  savedViews?: JSX.Element;
  footer?: JSX.Element;
}
export function Sidebar(props: SidebarProps) {
  const [p, rest] = splitProps(props, [
    'brand',
    'workspaceSwitcher',
    'navigation',
    'savedViews',
    'footer',
    'children',
    'class',
    'classList',
  ]);
  return (
    <aside
      aria-label="Workspace navigation"
      {...rest}
      class={cx('ev-sidebar', p.class)}
      classList={p.classList}
    >
      {p.brand}
      <div class="ev-sidebar-scroll">
        {p.workspaceSwitcher}
        {p.navigation}
        {p.savedViews}
        {p.children}
      </div>
      {p.footer}
    </aside>
  );
}
export interface BrandAreaProps extends ElementProps {
  logo?: JSX.Element;
  name?: JSX.Element;
}
export function BrandArea(props: BrandAreaProps) {
  const [p, rest] = splitProps(props, ['logo', 'name', 'children', 'class', 'classList']);
  return (
    <div {...rest} class={cx('ev-brand', p.class)} classList={p.classList}>
      {p.children ?? (
        <>
          {p.logo}
          <span>{p.name}</span>
        </>
      )}
    </div>
  );
}
export interface WorkspaceSwitcherProps extends Omit<
  JSX.ButtonHTMLAttributes<HTMLButtonElement>,
  'name'
> {
  name: JSX.Element;
  description?: JSX.Element;
  icon?: JSX.Element;
  indicator?: JSX.Element;
}
export function WorkspaceSwitcher(props: WorkspaceSwitcherProps) {
  const [p, rest] = splitProps(props, [
    'name',
    'description',
    'icon',
    'indicator',
    'class',
    'classList',
    'children',
  ]);
  const descriptionContent = contentSlot(() => p.description);
  return (
    <button
      type="button"
      {...rest}
      class={cx('ev-workspace-switch', p.class)}
      classList={p.classList}
    >
      {p.children ?? (
        <>
          {p.icon}
          <span>
            <strong>{p.name}</strong>
            <Show when={descriptionContent()}>
              <small>{descriptionContent()}</small>
            </Show>
          </span>
          {p.indicator}
        </>
      )}
    </button>
  );
}
export interface NavigationItem {
  value: string;
  label: string;
  icon?: JSX.Element;
  count?: number;
  disabled?: boolean;
  hidden?: boolean;
}
export interface NavigationGroupProps extends Omit<
  ElementProps<HTMLElement>,
  'onChange' | 'title'
> {
  title?: JSX.Element;
  items: readonly NavigationItem[];
  value?: string;
  onChange: (value: string) => void;
}
export function NavigationGroup(props: NavigationGroupProps) {
  const [p, rest] = splitProps(props, [
    'title',
    'items',
    'value',
    'onChange',
    'class',
    'classList',
  ]);
  const titleContent = contentSlot(() => p.title);
  return (
    <nav aria-label={typeof p.title === 'string' ? p.title : undefined} {...rest} class={cx('ev-navigation-group', p.class)} classList={p.classList}>
      <Show when={titleContent()}>
        <p class="ev-nav-label">{titleContent()}</p>
      </Show>
      <For each={p.items.filter((item) => !item.hidden)}>
        {(item) => (
          <button
            type="button"
            class={cx('ev-nav-item', p.value === item.value && 'ev-active')}
            aria-current={p.value === item.value ? 'page' : undefined}
            disabled={item.disabled}
            onClick={() => p.onChange(item.value)}
          >
            {item.icon}
            {item.label}
            <Show when={item.count !== undefined}>
              <span class="ev-count">{item.count}</span>
            </Show>
          </button>
        )}
      </For>
    </nav>
  );
}
export interface SavedViewsProps extends Omit<ElementProps, 'title'> {
  title?: JSX.Element;
  items: readonly { id: string; label: string; icon?: JSX.Element; onSelect: () => void }[];
}
export function SavedViews(props: SavedViewsProps) {
  const [p, rest] = splitProps(props, ['title', 'items', 'class', 'classList']);
  return (
    <div {...rest} class={cx('ev-saved-views', p.class)} classList={p.classList}>
      <div class="ev-nav-divider" />
      <p class="ev-nav-label">{p.title ?? 'Saved views'}</p>
      <For each={p.items}>
        {(item) => (
          <button class="ev-collection" type="button" onClick={item.onSelect}>
            {item.icon}
            {item.label}
          </button>
        )}
      </For>
    </div>
  );
}
export function SidebarFooter(props: ElementProps) {
  const [p, rest] = splitProps(props, ['class', 'classList', 'children']);
  return (
    <div {...rest} class={cx('ev-sidebar-bottom', p.class)} classList={p.classList}>
      {p.children}
    </div>
  );
}
export interface TopBarProps extends ElementProps<HTMLElement> {
  leading?: JSX.Element;
  center?: JSX.Element;
  trailing?: JSX.Element;
}
export function TopBar(props: TopBarProps) {
  const [p, rest] = splitProps(props, [
    'leading',
    'center',
    'trailing',
    'class',
    'classList',
    'children',
  ]);
  const centerContent = contentSlot(() => p.center);
  return (
    <header {...rest} class={cx('ev-topbar', p.class)} classList={p.classList}>
      <div class="ev-topbar-leading">{p.leading}</div>
      <Show when={centerContent()}>
        <div class="ev-topbar-center">{centerContent()}</div>
      </Show>
      <div class="ev-top-actions">{p.trailing}</div>
      {p.children}
    </header>
  );
}
export interface BreadcrumbsProps extends ElementProps<HTMLElement> {
  items: readonly { label: JSX.Element; onSelect?: () => void }[];
  separator?: JSX.Element;
  leading?: JSX.Element;
}
export function Breadcrumbs(props: BreadcrumbsProps) {
  const [p, rest] = splitProps(props, ['items', 'separator', 'leading', 'class', 'classList']);
  return (
    <nav
      aria-label="Breadcrumb"
      {...rest}
      class={cx('ev-breadcrumbs', p.class)}
      classList={p.classList}
    >
      {p.leading}
      <For each={p.items}>
        {(item, i) => (
          <>
            <Show when={i() > 0}>{p.separator ?? <span aria-hidden="true">/</span>}</Show>
            <Show
              when={item.onSelect}
              fallback={
                <span aria-current={i() === p.items.length - 1 ? 'page' : undefined}>
                  {item.label}
                </span>
              }
            >
              <button type="button" onClick={() => item.onSelect?.()}>
                {item.label}
              </button>
            </Show>
          </>
        )}
      </For>
    </nav>
  );
}
export interface ViewHostProps extends ElementProps {
  width?: string;
  padding?: string;
}
export function ViewHost(props: ViewHostProps) {
  const [p, rest] = splitProps(props, [
    'class',
    'classList',
    'style',
    'width',
    'padding',
    'children',
  ]);
  return (
    <div
      {...rest}
      class={cx('ev-content', p.class)}
      classList={p.classList}
      style={mergeStyle({ 'max-width': p.width, padding: p.padding }, p.style)}
    >
      {p.children}
    </div>
  );
}
export interface StatusBarProps extends ElementProps<HTMLElement> {
  leading?: JSX.Element;
  trailing?: JSX.Element;
}
export function StatusBar(props: StatusBarProps) {
  const [p, rest] = splitProps(props, ['leading', 'trailing', 'class', 'classList', 'children']);
  return (
    <footer {...rest} class={cx('ev-canvas-footer', p.class)} classList={p.classList}>
      <span>{p.leading}</span>
      {p.children}
      {p.trailing}
    </footer>
  );
}
export function OverlayLayer(props: ElementProps) {
  const [p, rest] = splitProps(props, ['class', 'classList', 'children']);
  return (
    <div {...rest} class={cx('ev-overlay-layer', p.class)} classList={p.classList}>
      {p.children}
    </div>
  );
}
