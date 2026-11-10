// Regressions from the independent review of the component library.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { buildChanges } from '../model/changes';
import { SchemaIndex } from '../model/schema';
import { ChildTable } from '../ui/childtable';
import { apiLinks, FieldControl, localField, recordOf } from '../ui/fields';
import { InlineValue } from '../ui/inline';
import { Menu, mountToasts, openPopover, toast } from '../ui/layers';
import { Button } from '../ui/base';
import { DataTable } from '../ui/table';
import { backend, id, bigOrder, cleanup, key, render, schema } from './ui-helpers';

afterEach(() => { vi.useRealTimers(); cleanup(); });
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('tbody tr[data-key]')];
const lastToast = () => [...document.querySelectorAll('.toast')].pop()!;
const deleteSelected = (host: Element) => [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes('Delete'))!.click();

describe('unchanged values are not written', () => {
  it('a date-time with seconds survives Enter without a change', () => {
    const field = localField({ name: 'at', type: 'datetime', label: 'At' }, '2026-11-01T10:05:30.123456Z');
    const set = vi.spyOn(field, 'set');
    expect(FieldControl(field).commit()).toBe(true);
    expect(set).not.toHaveBeenCalled();
    expect(field.value.peek()).toBe('2026-11-01T10:05:30.123456Z');
  });

  it('the same instant written another way is not a change', () => {
    const field = localField({ name: 'at', type: 'datetime', label: 'At' }, '2026-11-01T10:05:00.000000Z');
    const set = vi.spyOn(field, 'set');
    const control = FieldControl(field);
    (control.element as HTMLInputElement).value = '01-11-2026 14:05';
    expect(control.commit()).toBe(true);
    expect(set).not.toHaveBeenCalled();
  });

  it('values the server normalized round-trip without writes', () => {
    for (const [type, value] of [['phone', '+971 50 123 4567'], ['url', 'example.com/x'], ['text', '  padded '], ['duration', 'P2DT4H'], ['time', '14:05:30']] as const) {
      const field = localField({ name: 'x', type, label: 'X' }, value);
      const set = vi.spyOn(field, 'set');
      expect(FieldControl(field).commit()).toBe(true);
      expect(set).not.toHaveBeenCalled();
    }
  });

  it('InlineValue does not save or confirm when Enter changes nothing', async () => {
    const field = localField({ name: 'at', type: 'datetime', label: 'At' }, '2026-11-01T10:05:00.000000Z');
    const save = vi.fn(async () => true);
    const host = render(() => InlineValue({ field, save }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    key(host.querySelector('input')!, 'Enter');
    await flush();
    expect(save).not.toHaveBeenCalled();
    expect(document.querySelector('.toast')).toBeNull();
  });
});

describe('InlineValue focus and conflicts', () => {
  const setup = () => {
    const field = localField({ name: 't', type: 'text', label: 'Title' }, 'A');
    const save = vi.fn(async () => true);
    const host = render(() => InlineValue({ field, save }));
    const other = document.createElement('button');
    document.body.append(other);
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    return { field, save, host, other };
  };

  it('leaving without a change lets focus go where the user sent it', async () => {
    const { other } = setup();
    other.focus();
    await flush();
    expect(document.activeElement).toBe(other);
  });

  it('leaving with a change saves and keeps focus where the user went', async () => {
    const { host, other, save } = setup();
    host.querySelector('input')!.value = 'B';
    other.focus();
    await flush();
    expect(save).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(other);
  });

  it('Enter returns focus to the value after saving', async () => {
    const { host } = setup();
    const input = host.querySelector('input')!;
    input.value = 'B';
    key(input, 'Enter');
    await flush();
    expect(document.activeElement).toBe(host.querySelector('button.fval'));
  });

  it('a conflict says so and offers Reload, keeping the typed value to save again', async () => {
    const { store, sent } = backend([['PATCH', /\/api\/order\/0000000000001$/, 'stale_if_match']]);
    await store.open(id(1));
    const host = render(() => InlineValue({ field: store.field('title') }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    const input = host.querySelector('input')!;
    input.value = 'Other';
    key(input, 'Enter');
    await flush(); await flush();
    expect(host.querySelector('[role="alert"]')!.textContent).toBe('Someone else changed this record. Reload to see their changes.');
    const reload = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'Reload')!;
    const reads = sent.filter(item => item.method === 'GET' && item.url.endsWith('/api/order/0000000000001')).length;
    reload.click();
    await flush(); await flush();
    expect(sent.filter(item => item.method === 'GET' && item.url.endsWith('/api/order/0000000000001')).length).toBe(reads + 1);
    expect(store.conflict.peek()).toBeNull();
    expect(host.querySelector('input')!.value).toBe('Other');
    expect(store.field('title').value.peek()).toBe('Other');
  });

  it('exposes the record of a field through a typed accessor', async () => {
    const { store } = backend();
    await store.open(id(1));
    expect(recordOf(store.field('title'))).toBe(store);
    expect(recordOf(localField({ name: 'x', type: 'text', label: 'X' }))).toBeNull();
  });
});

describe('ChildTable undo, duplicate and nested rows', () => {
  it('Undo restores a saved line with its id and nested rows, leaving no change to save', async () => {
    const { store } = backend();
    await store.open(id(1));
    const lines = store.table('lines');
    const first = lines.rows.peek()[0];
    const host = render(() => ChildTable({ table: lines, columns: ['qty', 'rate', 'amount'] }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('[role="checkbox"]')!.click();
    deleteSelected(host);
    expect(store.field('line_count').value.peek()).toBe(119);
    lastToast().querySelector('button')!.click();
    const restored = lines.rows.peek()[0];
    expect(restored).toBe(first);
    expect(restored.id).toBe(id(1));
    expect(restored.table('parts').rows.peek()).toHaveLength(2);
    expect(store.field('line_count').value.peek()).toBe(120);
    expect(buildChanges(store.root)).toEqual({});
    expect(store.dirty.peek()).toBe(false);
  });

  it('Duplicate copies nested rows as new rows', async () => {
    const { store } = backend();
    await store.open(id(1));
    const lines = store.table('lines');
    const host = render(() => ChildTable({ table: lines, columns: ['qty', 'amount'] }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('[role="checkbox"]')!.click();
    [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes('Duplicate'))!.click();
    const copy = lines.rows.peek()[1];
    expect(copy.isNew).toBe(true);
    expect(copy.table('parts').rows.peek().map(row => [row.isNew, row.field('hours').value.peek()])).toEqual([[true, '1.5000'], [true, '2.0000']]);
  });

  it('a row in the row editor shows its nested tables', async () => {
    const { store } = backend();
    await store.open(id(1));
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'amount'] }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    const nested = host.querySelector('.g-ed-body .grid[data-table="parts"]')!;
    expect(nested).not.toBeNull();
    expect(nested.querySelectorAll('tbody tr[data-key]')).toHaveLength(2);
  });

  it('Undo is refused with a message while the record saves, never thrown', async () => {
    const { store } = await bigOrder(3);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'amount'] }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('[role="checkbox"]')!.click();
    deleteSelected(host);
    store.saving.set(true);
    const errors: unknown[] = [];
    const onError = (event: ErrorEvent) => { errors.push(event.message); event.preventDefault(); };
    window.addEventListener('error', onError);
    lastToast().querySelector('button')!.click();
    window.removeEventListener('error', onError);
    expect(errors).toEqual([]);
    expect(store.table('lines').rows.peek()).toHaveLength(2);
    expect(lastToast().textContent).toContain('The record is being saved. Try Undo again in a moment.');
  });

  it('Undo is refused once the table locked', async () => {
    const { store } = await bigOrder(3);
    const locked = cell(false);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'amount'], locked }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('[role="checkbox"]')!.click();
    deleteSelected(host);
    locked.set(true);
    lastToast().querySelector('button')!.click();
    expect(store.table('lines').rows.peek()).toHaveLength(2);
    expect(lastToast().textContent).toContain('The lines are locked, so the rows can’t come back.');
  });

  it('gives the first column a narrow width on small phones', async () => {
    const media = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query === '(max-width: 480px)', addEventListener() {}, removeEventListener() {} })) as never;
    try {
      const { store } = await bigOrder(1);
      const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'amount'] }));
      const cols = [...host.querySelectorAll<HTMLElement>('col')];
      expect(parseInt(cols[0].style.width)).toBeLessThanOrEqual(72); // checkbox, a narrow drag handle and Edit
      expect(parseInt(cols[1].style.width)).toBeLessThanOrEqual(120);
    } finally { window.matchMedia = media; }
  });
});

describe('DataTable', () => {
  it('keeps empty values last in both directions', () => {
    const sort = cell<any>({ key: 'v', dir: 'desc' });
    const host = render(() => DataTable({ columns: [{ key: 'v', label: 'V' }], rows: [{ v: '1' }, { v: null }, { v: '3' }], key: (row: any) => String(row.v), label: 'T', sort }));
    expect(rowsIn(host).map(row => row.dataset.key)).toEqual(['3', '1', 'null']);
    sort.set({ key: 'v', dir: 'asc' });
    expect(rowsIn(host).map(row => row.dataset.key)).toEqual(['1', '3', 'null']);
  });

  it('lets a column that does not sort be reordered by keyboard', () => {
    const host = render(() => DataTable({ columns: [{ key: 'a', label: 'A' }, { key: 'b', label: 'B', sortable: false }, { key: 'c', label: 'C' }], rows: [], key: (row: any) => row.a, label: 'T' }));
    const th = host.querySelector<HTMLElement>('th[data-key="b"]')!;
    expect(th.getAttribute('tabindex')).toBe('0');
    key(th, 'ArrowRight', { altKey: true });
    expect([...host.querySelectorAll('thead th[data-key]')].map(item => item.getAttribute('data-key'))).toEqual(['a', 'c', 'b']);
  });
});

describe('layers and links', () => {
  it('keeps a popover open on resize while an input inside has focus (phone keyboards)', () => {
    const anchor = render(() => Button({ label: 'Pick' })).querySelector('button')!;
    openPopover(anchor, close => { const input = document.createElement('input'); queueMicrotask(() => input.focus()); return [input, Menu([], close)]; }, { role: 'dialog' });
    document.querySelector<HTMLInputElement>('.pop input')!.focus();
    window.dispatchEvent(new Event('resize'));
    expect(document.querySelector('.pop')).not.toBeNull();
  });

  it('creates the toast live region at mount, and toasts are not live regions themselves', () => {
    mountToasts();
    const region = document.querySelector('.toasts')!;
    expect(region.getAttribute('aria-live')).toBe('polite');
    toast('Saved');
    expect(document.querySelector('.toasts')).toBe(region);
    expect(region.querySelector('.toast')!.hasAttribute('role')).toBe(false);
  });

  it('forgets link titles when the session ends', async () => {
    const { api } = backend();
    const links = apiLinks(api, new SchemaIndex(schema));
    links.remember!('product', id(1), 'Old title');
    expect(links.title('product', id(1)).peek()).toBe('Old title');
    window.dispatchEvent(new Event('evlet-session-ended'));
    const title = links.title('product', id(1));
    expect(title.peek()).toBeNull();
    await flush();
    expect(title.peek()).toBe('Support hours');
  });
});
