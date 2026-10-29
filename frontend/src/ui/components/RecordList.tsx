import { createEffect, createMemo, For, Show, splitProps, type JSX } from 'solid-js';
import { cx, type Column, type ElementProps } from '../types';
export interface RecordListProps<T> extends Omit<ElementProps, 'onSelectionChange'> {
  records: readonly T[];
  columns: readonly Column<T>[];
  getKey: (record: T) => string | number;
  getLabel: (record: T) => string;
  selectedKey?: string | number | null;
  selection?: ReadonlySet<string | number>;
  onSelectionChange?: (keys: Set<string | number>) => void;
  rowActions?: (record: T) => JSX.Element;
  selectionActions?: JSX.Element;
  selectionInToolbar?: boolean;
  empty?: JSX.Element;
  footer?: JSX.Element;
  tableProps?: JSX.IntrinsicElements['table'];
  rowClass?: (record: T) => string | undefined;
}
export function RecordList<T>(props: RecordListProps<T>) {
  const [p, rest] = splitProps(props, [
    'records',
    'columns',
    'getKey',
    'getLabel',
    'selectedKey',
    'selection',
    'onSelectionChange',
    'rowActions',
    'selectionActions',
    'selectionInToolbar',
    'empty',
    'footer',
    'tableProps',
    'rowClass',
    'class',
    'classList',
  ]);
  let all: HTMLInputElement | undefined;
  const chosen = createMemo(() => p.records.filter((r) => p.selection?.has(p.getKey(r))).length);
  createEffect(() => {
    if (all) all.indeterminate = chosen() > 0 && chosen() < p.records.length;
  });
  const toggle = (key: string | number, checked: boolean) => {
    const next = new Set(p.selection);
    checked ? next.add(key) : next.delete(key);
    p.onSelectionChange?.(next);
  };
  const toggleAll = (checked: boolean) => {
    const next = new Set(p.selection);
    for (const record of p.records) {
      checked ? next.add(p.getKey(record)) : next.delete(p.getKey(record));
    }
    p.onSelectionChange?.(next);
  };
  return (
    <div {...rest} class={cx('ev-table-card', p.class)} classList={p.classList}>
      <Show when={!p.selectionInToolbar && p.selection?.size}>
        <div class="ev-selection-bar">
          <span>{p.selection?.size} selected</span>
          {p.selectionActions}
        </div>
      </Show>
      <div class="ev-table-scroll">
        <table
          {...p.tableProps}
          class={cx(
            'ev-projects-table',
            !p.onSelectionChange && 'ev-no-checkbox',
            !p.rowActions && 'ev-no-row-actions',
            p.tableProps?.class,
          )}
        >
          <thead>
            <tr>
              <Show when={p.onSelectionChange}>
                <th scope="col">
                  <input
                    ref={all}
                    class="ev-check"
                    type="checkbox"
                    aria-label="Select all visible records"
                    disabled={!p.records.length}
                    checked={p.records.length > 0 && chosen() === p.records.length}
                    onChange={(e) => toggleAll(e.currentTarget.checked)}
                  />
                </th>
              </Show>
              <For each={p.columns}>
                {(column) => (
                  <th
                    scope="col"
                    class={column.class}
                    style={{ width: column.width, 'text-align': column.align }}
                  >
                    {column.header}
                  </th>
                )}
              </For>
              <Show when={p.rowActions}>
                <th scope="col" aria-label="Actions" />
              </Show>
            </tr>
          </thead>
          <tbody>
            <For each={p.records}>
              {(record) => (
                <tr
                  class={cx(
                    p.selectedKey === p.getKey(record) && 'ev-is-selected',
                    p.rowClass?.(record),
                  )}
                >
                  <Show when={p.onSelectionChange}>
                    <td>
                      <input
                        class="ev-check"
                        type="checkbox"
                        aria-label={'Select ' + p.getLabel(record)}
                        checked={p.selection?.has(p.getKey(record)) ?? false}
                        onChange={(e) => toggle(p.getKey(record), e.currentTarget.checked)}
                      />
                    </td>
                  </Show>
                  <For each={p.columns}>
                    {(column) => (
                      <td class={column.class} style={{ 'text-align': column.align }}>
                        {column.cell(record)}
                      </td>
                    )}
                  </For>
                  <Show when={p.rowActions}>
                    <td>{p.rowActions?.(record)}</td>
                  </Show>
                </tr>
              )}
            </For>
          </tbody>
        </table>
        <Show when={!p.records.length}>
          {p.empty ?? <div class="ev-empty">No records to show.</div>}
        </Show>
      </div>
      {p.footer}
    </div>
  );
}
