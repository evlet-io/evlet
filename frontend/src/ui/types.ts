import type { JSX } from 'solid-js';
export type ElementProps<T extends HTMLElement = HTMLDivElement> = JSX.HTMLAttributes<T> & {
  [key: `data-${string}`]: string | number | boolean | undefined;
};
export type Content = JSX.Element;
export interface Action {
  id: string;
  label: string;
  icon?: JSX.Element;
  onSelect: () => void;
  disabled?: boolean;
  hidden?: boolean;
  destructive?: boolean;
}
export interface SummaryItem {
  id: string;
  label: string;
  value: JSX.Element;
  compactLabel?: string;
  caption?: JSX.Element;
  icon?: JSX.Element;
  tone?: 'primary' | 'neutral' | 'success' | 'warning';
  onSelect?: () => void;
  class?: string;
  style?: JSX.CSSProperties;
}
export interface Category {
  value: string;
  label: string;
  count?: number;
  disabled?: boolean;
}
export interface ViewOption {
  value: string;
  label: string;
  icon?: JSX.Element;
  disabled?: boolean;
}
export interface MetadataItem {
  id: string;
  label: JSX.Element;
  value: JSX.Element;
}
export interface PreviewTab {
  value: string;
  label: string;
  count?: number;
  content: () => JSX.Element;
  disabled?: boolean;
}
export interface Column<T> {
  key: string;
  header: JSX.Element;
  cell: (record: T) => JSX.Element;
  class?: string;
  width?: string;
  align?: 'left' | 'right' | 'center';
}
export const cx = (...values: (string | undefined | false)[]) => values.filter(Boolean).join(' ');
export const ALL_CATEGORIES = '__all__';
export function buildCategories<T>(
  records: readonly T[],
  options: readonly Omit<Category, 'count'>[],
  getValue: (record: T) => string,
  allLabel: string | false = 'All',
): Category[] {
  const counts = new Map<string, number>();
  for (const record of records) {
    const value = getValue(record);
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  const result = options.map((option) => ({ ...option, count: counts.get(option.value) ?? 0 }));
  return allLabel === false
    ? result
    : [{ value: ALL_CATEGORIES, label: allLabel, count: records.length }, ...result];
}
