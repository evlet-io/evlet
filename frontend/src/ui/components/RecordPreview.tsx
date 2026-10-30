import { createMemo, createUniqueId, For, Show, splitProps, type JSX } from 'solid-js';
import { cx, type Action, type ElementProps, type MetadataItem, type PreviewTab } from '../types';
import { ActionMenu, Glyph, IconButton } from './primitives';
import { contentSlot } from './helpers';
export interface RecordPreviewProps extends Omit<ElementProps<HTMLElement>, 'title'> {
  title: JSX.Element;
  recordId?: JSX.Element;
  icon?: JSX.Element;
  badge?: JSX.Element;
  description?: JSX.Element;
  metadata?: readonly MetadataItem[];
  tabs?: readonly PreviewTab[];
  tab?: string;
  onTabChange?: (value: string) => void;
  onClose?: () => void;
  onEdit?: () => void;
  actions?: readonly Action[];
  header?: JSX.Element;
  headerActions?: JSX.Element;
  footer?: JSX.Element;
  headerProps?: ElementProps;
  bodyProps?: ElementProps;
  footerProps?: ElementProps;
}
export function RecordPreview(props: RecordPreviewProps) {
  const [p, rest] = splitProps(props, [
    'title',
    'recordId',
    'icon',
    'badge',
    'description',
    'metadata',
    'tabs',
    'tab',
    'onTabChange',
    'onClose',
    'onEdit',
    'actions',
    'header',
    'headerActions',
    'footer',
    'headerProps',
    'bodyProps',
    'footerProps',
    'class',
    'classList',
    'children',
  ]);
  const descriptionContent = contentSlot(() => p.description);
  const headerContent = contentSlot(() => p.header);
  const footerContent = contentSlot(() => p.footer);
  const metadata = createMemo(() => p.metadata);
  const tabs = createMemo(() => p.tabs);
  const id = createUniqueId();
  const active = createMemo(
    () => tabs()?.find((t) => t.value === p.tab) ?? tabs()?.find((t) => !t.disabled),
  );
  const tabKey: JSX.EventHandler<HTMLDivElement, KeyboardEvent> = (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const availableTabs = tabs()?.filter((t) => !t.disabled) ?? [];
    if (!availableTabs.length) return;
    e.preventDefault();
    const i = availableTabs.findIndex((t) => t.value === active()?.value);
    const next =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? availableTabs.length - 1
          : (i + (e.key === 'ArrowRight' ? 1 : -1) + availableTabs.length) % availableTabs.length;
    p.onTabChange?.(availableTabs[next].value);
    e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')[next]?.focus();
  };
  return (
    <aside
      {...rest}
      class={cx('ev-detail', p.class)}
      classList={p.classList}
      aria-labelledby={headerContent() ? rest['aria-labelledby'] : id + '-title'}
    >
      {headerContent() ?? (
        <div {...p.headerProps} class={cx('ev-detail-head', p.headerProps?.class)}>
          <div class="ev-detail-top">
            <div class="ev-detail-id">
              {p.icon}
              <span>{p.recordId}</span>
            </div>
            <div class="ev-preview-actions">
              {p.headerActions}
              <Show when={p.onEdit}>
                <IconButton aria-label="Edit record" onClick={p.onEdit}>
                  <svg class="ev-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="m15 5 4 4M4 20l4-1L20 7a3 3 0 0 0-4-4L4 15z" />
                  </svg>
                </IconButton>
              </Show>
              <Show when={p.actions?.length}>
                <ActionMenu iconOnly actions={p.actions ?? []} label="Record actions" />
              </Show>
              <Show when={p.onClose}>
                <IconButton aria-label="Close record preview" onClick={p.onClose}>
                  <Glyph name="close" />
                </IconButton>
              </Show>
            </div>
          </div>
          {p.badge}
          <h2 id={id + '-title'} class="ev-detail-title">
            {p.title}
          </h2>
          <Show when={descriptionContent()}>
            <p class="ev-detail-description">{descriptionContent()}</p>
          </Show>
          <Show when={metadata()?.length}>
            <div class="ev-detail-meta">
              <For each={metadata()}>
                {(item) => (
                  <div>
                    <div class="ev-meta-label">{item.label}</div>
                    <div class="ev-meta-value">{item.value}</div>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>
      )}
      <Show when={tabs()?.length}>
        <div class="ev-detail-tabs" role="tablist" aria-label="Preview sections" onKeyDown={tabKey}>
          <For each={tabs()}>
            {(tab) => (
              <button
                type="button"
                id={id + '-' + tab.value}
                class="ev-detail-tab"
                role="tab"
                aria-selected={active()?.value === tab.value}
                aria-controls={id + '-panel'}
                disabled={tab.disabled}
                tabIndex={active()?.value === tab.value ? 0 : -1}
                onClick={() => p.onTabChange?.(tab.value)}
              >
                {tab.label}
                <Show when={tab.count !== undefined}>
                  {' '}
                  <span class="ev-tab-count">{tab.count}</span>
                </Show>
              </button>
            )}
          </For>
        </div>
      </Show>
      <div
        {...p.bodyProps}
        class={cx('ev-detail-body', p.bodyProps?.class)}
        id={id + '-panel'}
        role={tabs()?.length ? 'tabpanel' : undefined}
        aria-labelledby={tabs()?.length ? id + '-' + active()?.value : undefined}
      >
        {active()?.content() ?? p.children}
      </div>
      <Show when={footerContent()}>
        <div {...p.footerProps} class={cx('ev-detail-footer', p.footerProps?.class)}>
          {footerContent()}
        </div>
      </Show>
    </aside>
  );
}
