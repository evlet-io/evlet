import {
  createEffect,
  createSignal,
  createUniqueId,
  For,
  onCleanup,
  onMount,
  Show,
  splitProps,
  type JSX,
} from 'solid-js';
import {
  cx,
  type Action,
  type Category,
  type ElementProps,
  type SummaryItem,
  type ViewOption,
} from '../types';
import { PrimaryActions } from './primitives';
import { callHandler, contentSlot, forwardRef, mergeStyle, withoutRef } from './helpers';

export interface PageHeaderProps extends Omit<ElementProps<HTMLElement>, 'title'> {
  title: JSX.Element;
  subtitle?: JSX.Element;
  badge?: JSX.Element;
  summary?: JSX.Element;
  actions?: readonly Action[];
  actionsLabel?: string;
  trailing?: JSX.Element;
}
export function PageHeader(props: PageHeaderProps) {
  const [p, rest] = splitProps(props, [
    'title',
    'subtitle',
    'badge',
    'summary',
    'actions',
    'actionsLabel',
    'trailing',
    'class',
    'classList',
    'children',
  ]);
  const subtitleContent = contentSlot(() => p.subtitle);
  return (
    <header {...rest} class={cx('ev-page-heading', p.class)} classList={p.classList}>
      <div>
        <div class="ev-title-line">
          <h1>{p.title}</h1>
          {p.badge}
          {p.summary}
        </div>
        <Show when={subtitleContent()}>
          <p class="ev-page-subtitle">{subtitleContent()}</p>
        </Show>
        {p.children}
      </div>
      <div class="ev-heading-actions">
        {p.trailing}
        <PrimaryActions actions={p.actions} label={p.actionsLabel} />
      </div>
    </header>
  );
}
export interface SummaryCardProps extends ElementProps {
  item: SummaryItem;
}
export function SummaryCard(props: SummaryCardProps) {
  const [p, rest] = splitProps(props, ['item', 'class', 'classList', 'children', 'style']);
  const iconContent = contentSlot(() => p.item.icon);
  return (
    <div
      {...rest}
      class={cx('ev-metric', p.item.tone === 'primary' && 'ev-primary', p.item.class, p.class)}
      classList={p.classList}
      style={mergeStyle(p.item.style ?? {}, p.style)}
    >
      <div>
        <span class="ev-metric-title">{p.item.label}</span>
        <div class="ev-metric-value">
          {p.item.value}
          <Show when={p.item.caption}>
            <span class="ev-metric-caption">{p.item.caption}</span>
          </Show>
        </div>
      </div>
      <Show when={iconContent()}>
        <span class="ev-metric-icon">{iconContent()}</span>
      </Show>
      {p.children}
      <Show when={p.item.onSelect}>
        <button
          type="button"
          class="ev-card-action"
          aria-label={p.item.label}
          onClick={() => p.item.onSelect?.()}
        />
      </Show>
    </div>
  );
}
export interface SummaryRowProps extends ElementProps {
  items: readonly SummaryItem[];
  renderItem?: (item: SummaryItem) => JSX.Element;
}
export function SummaryRow(props: SummaryRowProps) {
  const [p, rest] = splitProps(props, ['items', 'renderItem', 'class', 'classList', 'style']);
  return (
    <div
      {...rest}
      class={cx('ev-metrics', p.class)}
      classList={p.classList}
      style={mergeStyle({ '--ev-summary-count': p.items.length }, p.style)}
    >
      <For each={p.items}>
        {(item) => (p.renderItem ? p.renderItem(item) : <SummaryCard item={item} />)}
      </For>
    </div>
  );
}
export function CompactSummary(props: ElementProps & { items: readonly SummaryItem[] }) {
  const [p, rest] = splitProps(props, ['items', 'class', 'classList']);
  return (
    <div {...rest} class={cx('ev-compact-summary', p.class)} classList={p.classList}>
      <For each={p.items}>
        {(item) => (
          <span>
            <strong>{item.value}</strong>
            {item.compactLabel ?? item.label}
          </span>
        )}
      </For>
    </div>
  );
}

export interface CategoryTabsProps extends Omit<ElementProps, 'onChange'> {
  items: readonly Category[];
  value: string;
  onChange: (value: string) => void;
  label?: string;
}
export function CategoryTabs(props: CategoryTabsProps) {
  const [p, rest] = splitProps(props, [
    'items',
    'value',
    'onChange',
    'label',
    'class',
    'classList',
    'onKeyDown',
  ]);
  const keys: JSX.EventHandler<HTMLDivElement, KeyboardEvent> = (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const items = p.items.filter((item) => !item.disabled);
    if (!items.length) return;
    e.preventDefault();
    const current = items.findIndex((item) => item.value === p.value);
    const index =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? items.length - 1
          : (current + (e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length;
    p.onChange(items[index].value);
    e.currentTarget
      .querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)')
      [index]?.focus();
  };
  return (
    <div
      {...rest}
      role="tablist"
      aria-label={p.label ?? 'Categories'}
      class={cx('ev-tabs', p.class)}
      classList={p.classList}
      onKeyDown={(e) => {
        keys(e);
        callHandler(p.onKeyDown, e);
      }}
    >
      <For each={p.items}>
        {(item) => (
          <button
            type="button"
            role="tab"
            class="ev-tab"
            disabled={item.disabled}
            aria-selected={p.value === item.value}
            tabIndex={p.value === item.value ? 0 : -1}
            onClick={() => p.onChange(item.value)}
          >
            {item.label}
            <Show when={item.count !== undefined}>
              <span class="ev-tab-count">{item.count}</span>
            </Show>
          </button>
        )}
      </For>
    </div>
  );
}
export interface ViewSwitcherProps extends Omit<ElementProps, 'onChange'> {
  items: readonly ViewOption[];
  value: string;
  onChange: (value: string) => void;
  label?: string;
}
export function ViewSwitcher(props: ViewSwitcherProps) {
  const [p, rest] = splitProps(props, [
    'items',
    'value',
    'onChange',
    'label',
    'class',
    'classList',
  ]);
  return (
    <div
      {...rest}
      class={cx('ev-segmented', p.class)}
      classList={p.classList}
      role="group"
      aria-label={p.label ?? 'View'}
    >
      <For each={p.items}>
        {(item) => (
          <button
            type="button"
            class="ev-segment"
            aria-label={item.label}
            aria-pressed={p.value === item.value}
            disabled={item.disabled}
            onClick={() => p.onChange(item.value)}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        )}
      </For>
    </div>
  );
}
export interface ViewBarProps extends ElementProps {
  categories?: JSX.Element;
  viewSwitcher?: JSX.Element;
}
export function ViewBar(props: ViewBarProps) {
  const [p, rest] = splitProps(props, [
    'categories',
    'viewSwitcher',
    'children',
    'class',
    'classList',
  ]);
  return (
    <div {...rest} class={cx('ev-viewbar', p.class)} classList={p.classList}>
      {p.categories}
      {p.children}
      {p.viewSwitcher}
    </div>
  );
}
export interface ListToolbarProps extends ElementProps {
  leading?: JSX.Element;
  trailing?: JSX.Element;
}
export function ListToolbar(props: ListToolbarProps) {
  const [p, rest] = splitProps(props, ['leading', 'trailing', 'children', 'class', 'classList']);
  return (
    <div {...rest} class={cx('ev-toolbar', p.class)} classList={p.classList}>
      <div class="ev-toolbar-left">{p.leading}</div>
      {p.children}
      <div class="ev-toolbar-right">{p.trailing}</div>
    </div>
  );
}
export function ListFooter(props: ListToolbarProps) {
  const [p, rest] = splitProps(props, ['leading', 'trailing', 'children', 'class', 'classList']);
  return (
    <div {...rest} class={cx('ev-table-footer', p.class)} classList={p.classList}>
      <span>{p.leading}</span>
      {p.children}
      <span class="ev-footer-caption">{p.trailing}</span>
    </div>
  );
}

export interface ExplorerViewProps extends Omit<ElementProps<HTMLElement>, 'title'> {
  title: JSX.Element;
  subtitle?: JSX.Element;
  badge?: JSX.Element;
  actions?: readonly Action[];
  summaryItems?: readonly SummaryItem[];
  header?: JSX.Element;
  headerTrailing?: JSX.Element;
  summaryRow?: JSX.Element;
  viewBar?: JSX.Element;
  toolbar?: JSX.Element;
  preview?: JSX.Element;
  previewKey?: string | number;
  onPreviewClose?: () => void;
  listLabel?: string;
  previewLabel?: string;
  collapsible?: boolean;
  collapsed?: boolean;
  onCollapsedChange?: (value: boolean) => void;
  headerProps?: Partial<PageHeaderProps>;
  listAreaProps?: ElementProps;
  previewAreaProps?: ElementProps;
  previewWidth?: string;
}
export function ExplorerView(props: ExplorerViewProps) {
  const [p, rest] = splitProps(props, [
    'title',
    'subtitle',
    'badge',
    'actions',
    'summaryItems',
    'header',
    'headerTrailing',
    'summaryRow',
    'viewBar',
    'toolbar',
    'preview',
    'previewKey',
    'onPreviewClose',
    'listLabel',
    'previewLabel',
    'collapsible',
    'collapsed',
    'onCollapsedChange',
    'headerProps',
    'listAreaProps',
    'previewAreaProps',
    'previewWidth',
    'class',
    'classList',
    'children',
    'style',
  ]);
  const summaryContent = contentSlot(() => p.summaryRow);
  const previewContent = contentSlot(() => p.preview);
  const [internal, setInternal] = createSignal(false),
    [short, setShort] = createSignal(false);
  const canCollapse = () =>
    p.collapsible === true && Boolean(summaryContent() || p.summaryItems?.length);
  const folded = () => canCollapse() && (short() || (p.collapsed ?? internal()));
  let list!: HTMLDivElement,
    preview: HTMLDivElement | undefined,
    lock = 0,
    last = 0,
    direction = 0,
    distance = 0,
    touchY: number | null = null;
  const collapse = (value: boolean) => {
    if (!canCollapse()) return;
    const next = value || short();
    if (next === folded()) return;
    setInternal(next);
    p.onCollapsedChange?.(next);
    lock = performance.now() + 280;
  };
  const intent = (delta: number) => {
    if (!delta || performance.now() < lock || !canCollapse()) return false;
    const now = performance.now(),
      dir = Math.sign(delta);
    distance = (dir === direction && now - last < 180 ? distance : 0) + Math.abs(delta);
    direction = dir;
    last = now;
    if (dir > 0 && !folded() && distance >= 12) {
      collapse(true);
      distance = 0;
      return true;
    }
    if (dir < 0 && folded() && !short() && list.scrollTop <= 1 && distance >= 24) {
      collapse(false);
      distance = 0;
      return true;
    }
    return false;
  };
  onMount(() => {
    const resize = () => setShort(innerHeight < 560);
    resize();
    const wheel = (e: WheelEvent) => {
      if (e.ctrlKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      if (
        intent(e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? list.clientHeight : 1)) &&
        e.cancelable
      )
        e.preventDefault();
    };
    const scroll = () => {
      if (list.scrollTop > 12) collapse(true);
      else if (
        list.scrollTop <= 1 &&
        direction < 0 &&
        performance.now() - last < 400 &&
        performance.now() >= lock
      )
        collapse(false);
    };
    const start = (e: TouchEvent) => {
      touchY = e.touches.length === 1 ? e.touches[0].clientY : null;
    };
    const move = (e: TouchEvent) => {
      if (touchY === null || e.touches.length !== 1) return;
      const y = e.touches[0].clientY;
      intent(touchY - y);
      touchY = y;
    };
    const end = () => {
      touchY = null;
    };
    const keys = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).matches('input,textarea,select,[contenteditable="true"]'))
        return;
      if (
        ['ArrowDown', 'PageDown', 'End'].includes(e.key) ||
        (e.key === ' ' && !e.shiftKey && e.target === list)
      )
        collapse(true);
      if (
        ['ArrowUp', 'PageUp', 'Home'].includes(e.key) ||
        (e.key === ' ' && e.shiftKey && e.target === list)
      ) {
        direction = -1;
        last = performance.now();
        if (list.scrollTop <= 1) collapse(false);
      }
    };
    list.addEventListener('wheel', wheel, { passive: false });
    list.addEventListener('scroll', scroll, { passive: true });
    list.addEventListener('touchstart', start, { passive: true });
    list.addEventListener('touchmove', move, { passive: true });
    list.addEventListener('touchend', end);
    list.addEventListener('keydown', keys);
    window.addEventListener('resize', resize);
    onCleanup(() => {
      list.removeEventListener('wheel', wheel);
      list.removeEventListener('scroll', scroll);
      list.removeEventListener('touchstart', start);
      list.removeEventListener('touchmove', move);
      list.removeEventListener('touchend', end);
      list.removeEventListener('keydown', keys);
      window.removeEventListener('resize', resize);
    });
  });
  createEffect(() => {
    p.previewKey;
    if (preview) preview.scrollTop = 0;
  });
  createEffect(() => {
    if (p.collapsible === false) {
      setInternal(false);
      if (list) list.scrollTop = 0;
    }
  });
  return (
    <section
      {...rest}
      class={cx('ev-explorer', folded() && 'ev-workspace-collapsed', p.class)}
      classList={p.classList}
      style={mergeStyle({ '--ev-preview-width': p.previewWidth }, p.style)}
    >
      {p.header ?? (
        <PageHeader
          title={p.title}
          subtitle={p.subtitle}
          badge={p.badge}
          actions={p.actions}
          trailing={p.headerTrailing}
          summary={<CompactSummary items={p.summaryItems ?? []} aria-hidden={!folded()} />}
          {...p.headerProps}
        />
      )}
      <Show when={summaryContent() || p.summaryItems?.length}>
        <div class="ev-metrics-fold" aria-hidden={folded()} inert={folded()}>
          {summaryContent() ?? <SummaryRow items={p.summaryItems ?? []} />}
        </div>
      </Show>
      {p.viewBar}
      {p.toolbar}
      <div class={cx('ev-workarea', !previewContent() && 'ev-no-detail')}>
        <div
          {...withoutRef(p.listAreaProps)}
          ref={(el) => {
            list = el;
            forwardRef(p.listAreaProps?.ref, el);
          }}
          class={cx('ev-record-scroll', p.listAreaProps?.class)}
          role="region"
          aria-label={p.listLabel ?? 'Record list'}
          tabIndex={0}
        >
          {p.children}
        </div>
        <Show when={previewContent()}>
          <button
            type="button"
            class="ev-overlay-shade ev-detail-shade"
            aria-label="Close preview"
            onClick={p.onPreviewClose}
          />
          <div
            {...withoutRef(p.previewAreaProps)}
            ref={(el) => {
              preview = el;
              forwardRef(p.previewAreaProps?.ref, el);
            }}
            class={cx('ev-detail-scroll', p.previewAreaProps?.class)}
            role="region"
            aria-label={p.previewLabel ?? 'Record preview'}
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Escape') p.onPreviewClose?.();
              callHandler(p.previewAreaProps?.onKeyDown, e);
            }}
          >
            {previewContent()}
          </div>
        </Show>
      </div>
    </section>
  );
}
