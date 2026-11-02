import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { DataTable, emptyPrefs, type Column, type Sort } from '../ui/table';
import { cleanup, key, render, watch } from './ui-helpers';

afterEach(cleanup);

interface Item { id: number; name: string; amount: string; city: string }
const items = (count: number): Item[] => Array.from({ length: count }, (_, index) => ({ id: index + 1, name: `Deal ${String(index + 1).padStart(4, '0')}`, amount: String((index * 37) % 101) + '.50', city: ['Dubai', 'Sharjah', 'Ajman'][index % 3] }));
const columns: Column<Item>[] = [
  { key: 'name', label: 'Deal', width: 200 },
  { key: 'amount', label: 'Amount', width: 120, align: 'right' },
  { key: 'city', label: 'City', width: 140 },
];
const bodyRows = (host: Element) => [...host.querySelectorAll<HTMLElement>('tbody tr[data-key]')];
const headerKeys = (host: Element) => [...host.querySelectorAll('thead th[data-key]')].map(th => th.getAttribute('data-key'));

describe('DataTable', () => {
  it('renders a grid with a sticky header and the first column pinned', () => {
    const host = render(() => DataTable({ columns, rows: items(5), key: item => item.id, label: 'Deals', selectable: true }));
    const table = host.querySelector('table')!;
    expect(table.getAttribute('role')).toBe('grid');
    expect(table.getAttribute('aria-label')).toBe('Deals');
    expect(headerKeys(host)).toEqual(['name', 'amount', 'city']);
    const first = host.querySelector<HTMLElement>('th[data-key="name"]')!;
    expect(first.classList.contains('stk')).toBe(true);
    expect(first.style.left).toBe('40px');
    expect(host.querySelector('th[data-key="amount"]')!.classList.contains('stk')).toBe(false);
    expect(table.style.width).toBe('500px');
    expect(bodyRows(host)).toHaveLength(5);
  });

  it('sorts by clicking a header: ascending, descending, then off, with exact number order', () => {
    const sort = cell<Sort>(null);
    const host = render(() => DataTable({ columns, rows: items(12), key: item => item.id, label: 'Deals', sort }));
    const amount = host.querySelector<HTMLElement>('th[data-key="amount"]')!;
    amount.click();
    expect(sort.peek()).toEqual({ key: 'amount', dir: 'asc' });
    expect(amount.getAttribute('aria-sort')).toBe('ascending');
    const values = () => bodyRows(host).map(row => Number(row.querySelectorAll('td')[1].textContent));
    expect(values()).toEqual([...values()].sort((a, b) => a - b));
    amount.click();
    expect(amount.getAttribute('aria-sort')).toBe('descending');
    expect(values()).toEqual([...values()].sort((a, b) => b - a));
    key(amount, 'Enter');
    expect(sort.peek()).toBeNull();
    expect(bodyRows(host)[0].dataset.key).toBe('1');
  });

  it('resizes from the header edge, resets on double click, and saves to prefs', () => {
    const prefs = cell(emptyPrefs());
    const host = render(() => DataTable({ columns, rows: items(3), key: item => item.id, label: 'Deals', prefs }));
    const handle = host.querySelector<HTMLElement>('th[data-key="city"] .rz')!;
    handle.dispatchEvent(new PointerEvent('pointerdown', { clientX: 10, bubbles: true }));
    handle.dispatchEvent(new PointerEvent('pointermove', { clientX: 70, bubbles: true }));
    handle.dispatchEvent(new PointerEvent('pointerup', { clientX: 70, bubbles: true }));
    expect(prefs.peek().widths).toEqual({ city: 200 });
    expect(host.querySelector('table')!.style.width).toBe('520px');
    handle.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(prefs.peek().widths).toEqual({});
    // A click on the handle does not sort.
    const sort = cell<Sort>(null);
    const other = render(() => DataTable({ columns, rows: items(3), key: item => item.id, label: 'Other', sort }));
    other.querySelector<HTMLElement>('th[data-key="city"] .rz')!.click();
    expect(sort.peek()).toBeNull();
  });

  it('reorders columns with Alt and an arrow key, and by dropping a header; the pinned column stays first', () => {
    const prefs = cell(emptyPrefs());
    const host = render(() => DataTable({ columns, rows: items(3), key: item => item.id, label: 'Deals', prefs }));
    key(host.querySelector('th[data-key="city"]')!, 'ArrowLeft', { altKey: true });
    expect(headerKeys(host)).toEqual(['name', 'city', 'amount']);
    key(host.querySelector('th[data-key="city"]')!, 'ArrowLeft', { altKey: true });
    expect(headerKeys(host)).toEqual(['name', 'city', 'amount']);
    const data = new Map<string, string>();
    const transfer = { setData: (type: string, value: string) => data.set(type, value), getData: (type: string) => data.get(type) ?? '', get types() { return [...data.keys()]; } };
    const drag = (target: Element, type: string) => { const event = new Event(type, { bubbles: true, cancelable: true }); Object.defineProperty(event, 'dataTransfer', { value: transfer }); target.dispatchEvent(event); };
    drag(host.querySelector('th[data-key="amount"]')!, 'dragstart');
    drag(host.querySelector('th[data-key="city"]')!, 'drop');
    expect(headerKeys(host)).toEqual(['name', 'amount', 'city']);
    expect(prefs.peek().order).toEqual(['name', 'amount', 'city']);
    // Cells follow the header order.
    expect([...bodyRows(host)[0].querySelectorAll('td')].map(td => td.textContent)).toEqual(['Deal 0001', '0.50', 'Dubai']);
  });

  it('selects rows, and moves, opens and selects with the keyboard', () => {
    const selection = cell(new Set<string | number>());
    const open = vi.fn();
    const host = render(() => DataTable({ columns, rows: items(4), key: item => item.id, label: 'Deals', selectable: true, selection, onOpen: open }));
    const rows = bodyRows(host);
    expect(rows.map(row => row.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1']);
    rows[0].querySelector<HTMLElement>('[role="checkbox"]')!.click();
    expect([...selection.peek()]).toEqual([1]);
    expect(rows[0].classList.contains('sel')).toBe(true);
    expect(rows[0].getAttribute('aria-selected')).toBe('true');
    const all = host.querySelector<HTMLElement>('thead [role="checkbox"]')!;
    expect(all.getAttribute('aria-checked')).toBe('mixed');
    rows[0].focus();
    key(rows[0], 'ArrowDown');
    return Promise.resolve().then(() => {
      expect(document.activeElement).toBe(rows[1]);
      expect(rows[1].getAttribute('tabindex')).toBe('0');
      key(rows[1], ' ');
      expect([...selection.peek()].sort()).toEqual([1, 2]);
      key(rows[1], 'Enter');
      expect(open).toHaveBeenCalledWith(items(4)[1]);
      rows[2].querySelector('td:nth-child(2)')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      expect(open).toHaveBeenCalledTimes(2);
      key(rows[1], 'Escape');
      expect(selection.peek().size).toBe(0);
    });
  });

  it('virtualizes 1,000 rows and renders what scrolls into view', () => {
    const host = render(() => DataTable({ columns, rows: items(1000), key: item => item.id, label: 'Deals', height: 440 }));
    expect(bodyRows(host).length).toBeLessThan(30);
    expect(host.querySelector('table')!.getAttribute('aria-rowcount')).toBe('1001');
    const wrap = host.querySelector<HTMLElement>('.table-wrap')!;
    wrap.scrollTop = 36 + 44 * 600;
    wrap.dispatchEvent(new Event('scroll'));
    const keys = bodyRows(host).map(row => Number(row.dataset.key));
    expect(keys).toContain(601);
    expect(keys.length).toBeLessThan(30);
    const spacer = host.querySelector<HTMLElement>('tr.spacer td')!;
    expect(parseInt(spacer.style.height)).toBe(44 * (keys[0] - 1));
  });

  it('updates one cell in place when a row changes, and renders data as text', () => {
    const rows = cell(items(3));
    const host = render(() => DataTable({ columns, rows, key: item => item.id, label: 'Deals', totals: { amount: () => String(rows.get().length) }, totalsLabel: 'Total' }));
    const before = bodyRows(host)[1];
    const done = watch(host);
    rows.set(rows.peek().map(item => item.id === 2 ? { ...item, city: '<img src=x onerror="window.pwned=1">' } : item));
    const changes = done();
    expect(bodyRows(host)[1]).toBe(before);
    expect(changes.filter(record => !before.contains(record.target))).toEqual([]);
    expect(host.querySelector('tbody img')).toBeNull();
    expect(before.textContent).toContain('<img');
    expect([...host.querySelectorAll('tfoot td')].map(td => td.textContent)).toEqual(['Total', '3', '']);
  });

  it('shows an empty state when there are no rows', () => {
    const host = render(() => DataTable({ columns, rows: [], key: (item: Item) => item.id, label: 'Deals', empty: () => 'No deals yet. Create the first one.' }));
    expect(host.querySelector('.dt-empty')!.textContent).toBe('No deals yet. Create the first one.');
  });
});
