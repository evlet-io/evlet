import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { ChildRows, ChildTable } from '../ui/childtable';
import { emptyPrefs, type TablePrefs } from '../ui/table';
import { RecordStore } from '../model/record';
import { backend, id, bigOrder, change, cleanup, key, pick, render, schema, watch } from './ui-helpers';

afterEach(cleanup);

const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('tbody tr[data-key]')];
const cellOf = (row: Element, col: string) => [...row.querySelectorAll<HTMLElement>('td[data-col]')].find(td => td.dataset.col === col)!;
const footer = (host: Element) => [...host.querySelectorAll('tfoot td')].map(td => td.textContent!.trim());

describe('ChildTable over a record store', () => {
  it('renders lines, a locked calculated column and totals from the parent', async () => {
    const { store } = await bigOrder(3);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'rate', 'amount'] }));
    expect(rowsIn(host)).toHaveLength(3);
    const headers = [...host.querySelectorAll('thead th[data-key]')].map(th => th.textContent!.trim());
    expect(headers).toEqual(['Product', 'Qty', 'Rate', 'Amount']);
    expect(host.querySelector('th[data-key="amount"] svg[aria-label="Calculated"]')).not.toBeNull();
    expect(cellOf(rowsIn(host)[1], 'amount').textContent!.trim()).toBe('AED 5.00');
    expect(cellOf(rowsIn(host)[1], 'amount').classList.contains('calc')).toBe(true);
    expect(footer(host)).toEqual(['', 'Total · 3 lines', '', '', 'AED 15.00']);
    expect(host.querySelector('.g-sum')!.textContent).toContain('AED 15.00');
    expect(host.querySelector('table')!.getAttribute('role')).toBe('grid');
    expect(host.querySelector('.g-t .n')!.textContent).toBe('3');
  });

  it('starts the Link search when the user types in a Link cell', async () => {
    const { store } = await bigOrder(3);
    const searched: string[] = [];
    const links = { search: async (_meta: unknown, query: string) => { searched.push(query); return [{ id: id(2), title: 'Licence' }]; },
      title: (_model: string, row: string) => cell<string | null>(row === id(2) ? 'Licence' : 'Support hours') };
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'rate', 'amount'], links }));
    // Typing on a cell that is not being edited opens the search with that letter.
    key(cellOf(rowsIn(host)[0], 'product'), 'L');
    await new Promise(resolve => setTimeout(resolve, 0));
    const input = document.querySelector<HTMLInputElement>('.link-search input')!;
    expect(input.value).toBe('L');
    expect(searched.at(-1)).toBe('L');
    key(input, 'Enter');
    expect(store.table('lines').rows.peek()[0].field('product').value.peek()).toBe(id(2));
    expect(document.querySelector('.link-search')).toBeNull();
    // Typing on the cell's open control does the same.
    cellOf(rowsIn(host)[1], 'product').click();
    key(cellOf(rowsIn(host)[1], 'product').querySelector('button')!, 'l');
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(document.querySelector<HTMLInputElement>('.link-search input')!.value).toBe('l');
    expect(searched.at(-1)).toBe('l');
  });

  it('edits a cell: the line amount and the total update, and nothing else is touched', async () => {
    const { store } = await bigOrder(3);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'rate', 'amount'] }));
    const qty = cellOf(rowsIn(host)[0], 'qty');
    qty.click();
    const input = qty.querySelector('input')!;
    expect(document.activeElement).toBe(input);
    input.value = '4';
    const done = watch(host);
    key(input, 'Enter');
    const changes = done();
    expect(store.table('lines').rows.peek()[0].field('qty').value.peek()).toBe('4.0000');
    expect(store.field('total').value.peek()).toBe('22.50');
    expect(cellOf(rowsIn(host)[0], 'amount').textContent!.trim()).toBe('AED 10.00');
    expect(footer(host)[4]).toBe('AED 22.50');
    // Enter moved to the cell below and started editing it.
    expect(cellOf(rowsIn(host)[1], 'qty').classList.contains('editing')).toBe(true);
    // Other rows' cells were not rebuilt.
    const touched = new Set<Element | null>(changes.map(record => (record.target as Element).closest?.('tr') ?? record.target.parentElement?.closest('tr') ?? null));
    expect(touched.has(rowsIn(host)[2])).toBe(false);
  });

  it('moves with Tab and Shift Tab across editable cells, wrapping rows, and Esc cancels', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate', 'amount'] }));
    cellOf(rowsIn(host)[0], 'rate').click();
    let input = host.querySelector<HTMLInputElement>('td.editing input')!;
    input.value = '3';
    key(input, 'Tab');
    // amount is calculated, so Tab wraps to the next row's qty.
    expect(cellOf(rowsIn(host)[1], 'qty').classList.contains('editing')).toBe(true);
    expect(store.table('lines').rows.peek()[0].field('rate').value.peek()).toBe('3.00');
    input = host.querySelector<HTMLInputElement>('td.editing input')!;
    key(input, 'Tab', { shiftKey: true });
    expect(cellOf(rowsIn(host)[0], 'rate').classList.contains('editing')).toBe(true);
    input = host.querySelector<HTMLInputElement>('td.editing input')!;
    input.value = '99';
    key(input, 'Escape');
    expect(host.querySelector('td.editing')).toBeNull();
    expect(store.table('lines').rows.peek()[0].field('rate').value.peek()).toBe('3.00');
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'rate'));
  });

  it('navigates cells with the arrow keys and starts editing on Enter or typing', async () => {
    const { store } = await bigOrder(3);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate', 'amount'] }));
    const first = cellOf(rowsIn(host)[0], 'qty');
    expect(first.getAttribute('tabindex')).toBe('0');
    first.focus();
    key(first, 'ArrowDown');
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[1], 'qty'));
    key(document.activeElement!, 'ArrowRight');
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[1], 'rate'));
    expect(cellOf(rowsIn(host)[1], 'rate').getAttribute('tabindex')).toBe('0');
    expect(first.getAttribute('tabindex')).toBe('-1');
    key(document.activeElement!, '7');
    const input = host.querySelector<HTMLInputElement>('td.editing input')!;
    expect(input.value).toBe('7');
    key(input, 'Enter');
    expect(store.table('lines').rows.peek()[1].field('rate').value.peek()).toBe('7.00');
  });

  it('shows a parse error on the cell and keeps editing', async () => {
    const { store } = await bigOrder(1);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'amount'] }));
    cellOf(rowsIn(host)[0], 'qty').click();
    const input = host.querySelector<HTMLInputElement>('td.editing input')!;
    input.value = 'lots';
    key(input, 'Enter');
    const td = cellOf(rowsIn(host)[0], 'qty');
    expect(td.classList.contains('editing')).toBe(true);
    expect(td.getAttribute('aria-invalid')).toBe('true');
    expect(store.table('lines').rows.peek()[0].field('qty').error.peek()).toBe('Enter a number, for example 1250.50');
  });

  it('adds, duplicates, reorders and removes rows, with Undo', async () => {
    vi.useFakeTimers();
    try {
      const { store } = await bigOrder(2);
      const lines = store.table('lines');
      const host = render(() => ChildTable({ table: lines, columns: ['qty', 'rate', 'amount'] }));
      [...host.querySelectorAll<HTMLButtonElement>('.g-under button')].find(button => button.textContent!.includes('Add row'))!.click();
      expect(rowsIn(host)).toHaveLength(3);
      expect(cellOf(rowsIn(host)[2], 'qty').classList.contains('editing')).toBe(true);
      key(host.querySelector('td.editing input')!, 'Escape');
      // Alt+ArrowUp moves the active row up.
      const third = lines.rows.peek()[2];
      cellOf(rowsIn(host)[2], 'rate').focus();
      key(cellOf(rowsIn(host)[2], 'rate'), 'ArrowUp', { altKey: true });
      expect(lines.rows.peek()[1]).toBe(third);
      // Select the first row, duplicate it, then delete two rows and undo.
      rowsIn(host)[0].querySelector<HTMLButtonElement>('[role="checkbox"]')!.click();
      expect(host.querySelector('.g-selinfo')!.textContent).toBe('1 selected');
      [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes('Duplicate'))!.click();
      expect(lines.rows.peek()).toHaveLength(4);
      expect(lines.rows.peek()[1].field('qty').value.peek()).toBe(lines.rows.peek()[0].field('qty').value.peek());
      host.querySelector<HTMLButtonElement>('thead [role="checkbox"]')!.click();
      expect(host.querySelector('.g-selinfo')!.textContent).toBe('4 selected');
      [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes('Delete'))!.click();
      expect(lines.rows.peek()).toHaveLength(0);
      expect(host.querySelector('.g-empty')!.textContent).toContain('No rows yet');
      expect(store.field('total').value.peek()).toBe('0.00');
      const toast = [...document.querySelectorAll('.toast')].pop()!;
      expect(toast.textContent).toContain('Deleted 4 rows');
      toast.querySelector('button')!.click();
      expect(lines.rows.peek()).toHaveLength(4);
      expect(rowsIn(host)).toHaveLength(4);
    } finally { vi.useRealTimers(); }
  });

  it('locks with the record: no editing, no add, and a plain explanation', async () => {
    const { store } = await bigOrder(2);
    const locked = cell(false);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'amount'], locked }));
    locked.set(true);
    cellOf(rowsIn(host)[0], 'qty').click();
    expect(host.querySelector('td.editing')).toBeNull();
    expect(host.querySelector('.g-lock')!.textContent).toBe('Locked. Lines can’t change after the record is issued.');
    expect([...host.querySelectorAll('.g-under button')]).toHaveLength(0);
    expect(cellOf(rowsIn(host)[0], 'qty').getAttribute('aria-readonly')).toBe('true');
  });

  it('virtualizes 1,000 rows and renders more as it scrolls', async () => {
    const { store } = await bigOrder(1000);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate', 'amount'], height: 380 }));
    const shown = rowsIn(host).length;
    expect(shown).toBeGreaterThan(5);
    expect(shown).toBeLessThan(40);
    expect(host.querySelector('table')!.getAttribute('aria-rowcount')).toBe('1001');
    const scroller = host.querySelector<HTMLElement>('.g-scroll')!;
    scroller.scrollTop = 38 * 500;
    scroller.dispatchEvent(new Event('scroll'));
    const numbers = rowsIn(host).map(row => Number(row.querySelector('.g-exp .num')!.textContent));
    expect(numbers).toContain(501);
    expect(numbers.length).toBeLessThan(40);
    expect(footer(host)[1]).toBe('Total · 1000 lines');
    expect(store.field('total').value.peek()).toBe('4997.50');
  });

  it('resizes, hides and reorders columns into the prefs cell', async () => {
    const { store } = await bigOrder(1);
    const prefs = cell(emptyPrefs());
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'rate', 'amount'], prefs }));
    const handle = host.querySelector<HTMLElement>('th[data-key="qty"] .rz')!;
    handle.dispatchEvent(new PointerEvent('pointerdown', { clientX: 100, bubbles: true }));
    handle.dispatchEvent(new PointerEvent('pointermove', { clientX: 160, bubbles: true }));
    handle.dispatchEvent(new PointerEvent('pointerup', { clientX: 160, bubbles: true }));
    expect(prefs.peek().widths.qty).toBe(170);
    expect(host.querySelectorAll('col')[2].getAttribute('style')).toContain('170px');
    handle.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(prefs.peek().widths.qty).toBeUndefined();
    // Columns menu: hide Rate; pin and unpin with the pin buttons.
    [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes('Columns'))!.click();
    const menu = document.querySelector('.cols-pop')!;
    menu.querySelector<HTMLButtonElement>('[aria-label="Hide Rate"]')!.click();
    expect(prefs.peek().hidden).toEqual(['rate']);
    expect([...host.querySelectorAll('thead th[data-key]')].map(th => th.getAttribute('data-key'))).toEqual(['product', 'qty', 'amount']);
    // Pinning Amount moves it to the end of the pinned range, after Product.
    menu.querySelector<HTMLButtonElement>('[aria-label="Pin Amount"]')!.click();
    expect(prefs.peek()).toMatchObject({ order: ['product', 'amount', 'qty', 'rate'], pinned: 2 });
    expect([...host.querySelectorAll('thead th[data-key]')].map(th => th.getAttribute('data-key'))).toEqual(['product', 'amount', 'qty']);
    // Unpinning Product moves it to the start of the rest.
    document.querySelector<HTMLButtonElement>('.cols-pop [aria-label="Unpin Product"]')!.click();
    expect(prefs.peek()).toMatchObject({ order: ['amount', 'product', 'qty', 'rate'], pinned: 1 });
  });

  it('opens a row in place with every field and closes it with Done', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'amount'] }));
    const toggle = rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    toggle.click();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const panel = host.querySelector('.g-x')!;
    expect(panel.textContent).toContain('Row 1 of 2');
    expect([...panel.querySelectorAll('.fld label')].map(label => label.firstChild!.textContent)).toEqual(['Qty', 'Amount']);
    [...panel.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'Done')!.click();
    expect(host.querySelector('.g-x')).toBeNull();
  });

  it('keeps editing out until every row is loaded', async () => {
    const { store } = backend();
    await store.open(id(1), { loadAll: false });
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'amount'] }));
    cellOf(rowsIn(host)[0], 'qty').click();
    expect(host.querySelector('td.editing')).toBeNull();
    await store.loadAll();
    cellOf(rowsIn(host)[0], 'qty').click();
    expect(host.querySelector('td.editing')).not.toBeNull();
  });
});

describe('ChildRows: a child table in a narrow column', () => {
  it('shows a line per row with its amount, edits a row in place, adds rows and opens the full table', async () => {
    const { store } = await bigOrder(3);
    const full = vi.fn();
    const host = render(() => ChildRows({ table: store.table('lines'), title: 'Lines', onOpenFull: full, totals: [{ label: 'Total', value: 'AED 15.00' }] }));
    const lines = () => [...host.querySelectorAll<HTMLElement>('.crow')];
    expect(lines()).toHaveLength(3);
    expect(host.querySelector('.crows-h h3')!.textContent).toBe('3 rows');
    expect(lines()[1].querySelector('.crow-amt')!.textContent!.trim()).toBe('AED 5.00');
    expect(lines()[1].querySelector('.crow-sub')!.textContent).toContain('Qty');
    const head = lines()[0].querySelector<HTMLButtonElement>('.crow-h')!;
    expect(head.getAttribute('aria-expanded')).toBe('false');
    head.click();
    expect(head.getAttribute('aria-expanded')).toBe('true');
    expect(lines()[0].querySelectorAll('.crow-ed .ff, .crow-ed .field, .crow-ed input').length).toBeGreaterThan(0);
    [...host.querySelectorAll<HTMLButtonElement>('.crows-h button')].find(button => button.textContent!.includes('Add row'))!.click();
    expect(lines()).toHaveLength(4);
    expect(lines()[3].classList.contains('open')).toBe(true);
    expect(store.dirty.peek()).toBe(true);
    [...host.querySelectorAll<HTMLButtonElement>('.crows-h button')].find(button => button.textContent!.includes('Open full table'))!.click();
    expect(full).toHaveBeenCalledOnce();
    expect(host.querySelector('.crows-t')!.textContent).toBe('TotalAED 15.00');
  });

  it('shows values without inputs or Add row while locked', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildRows({ table: store.table('lines'), title: 'Lines', locked: true, lockedText: 'Locked. Lines can’t change now.' }));
    expect([...host.querySelectorAll('.crows-h button')].some(button => button.textContent!.includes('Add row'))).toBe(false);
    expect(host.querySelector('.crows-lock')!.textContent).toBe('Locked. Lines can’t change now.');
    host.querySelector<HTMLButtonElement>('.crow-h')!.click();
    expect(host.querySelector('.crow-ed dl.fields')).not.toBeNull();
    expect(host.querySelector('.crow-ed input')).toBeNull();
  });
});

// ---------- focus, click and Tab across every cell kind ----------

/** Lines with a text, a select, a phone, a date and a currency column next to the order's Link, so each cell editor kind is covered. */
async function entryLines(lines = 2) {
  const extended = structuredClone(schema) as typeof schema;
  const model = extended.models.find(item => item.name === 'order_lines')!;
  const base = model.fields.find(field => field.name === 'qty')!;
  const field = (name: string, type: string, extra: Record<string, unknown> = {}) =>
    ({ ...base, name, label: name[0].toUpperCase() + name.slice(1), type, default: null, default_kind: 'none', minimum: null, scale: null, ...extra });
  model.fields.push(field('note', 'text'), field('kind', 'select', { choices: ['spare', 'service', 'other'] }),
    field('phone', 'phone'), field('due', 'date'), field('code', 'text', { required: true }));
  const { api } = backend();
  const store = new RecordStore({ api, schema: extended, model: 'order', today: () => '2026-11-01' });
  const rows = Array.from({ length: lines }, (_, index) => ({ _key: `e${index}`, qty: '1', rate: '2.50' }));
  await store.create({ title: 'Entry', children: { lines: rows } });
  return store;
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

/** A mouse click as a browser delivers it: mousedown focuses the cell, focus moves, then click. */
async function press(target: HTMLElement) {
  const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
  target.dispatchEvent(down);
  if (!down.defaultPrevented) (target.closest<HTMLElement>('button, input, [tabindex]') ?? target).focus();
  await Promise.resolve();
  target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
  target.click();
  await Promise.resolve();
}

const activeCells = (host: Element) => [...host.querySelectorAll('td.editing, td.active')];
const focusedCell = () => (document.activeElement as Element | null)?.closest?.('td[data-col]') as HTMLElement | null;

describe('ChildTable focus, click and Tab', () => {
  const columns = ['note', 'kind', 'phone', 'due', 'rate', 'product', 'amount'];

  it('moves focus to a clicked text cell while another text cell is being edited', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns }));
    const row = rowsIn(host)[0];
    await press(cellOf(row, 'phone'));
    expect(document.activeElement).toBe(cellOf(row, 'phone').querySelector('input'));
    await press(cellOf(rowsIn(host)[0], 'note'));
    const note = cellOf(rowsIn(host)[0], 'note');
    expect(note.classList.contains('editing')).toBe(true);
    expect(document.activeElement).toBe(note.querySelector('input'));
    expect(activeCells(host)).toEqual([note]);
  });

  it('keeps one active cell, holding focus, when the edited cell cannot be left', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns }));
    await press(cellOf(rowsIn(host)[0], 'phone'));
    const phone = cellOf(rowsIn(host)[0], 'phone');
    phone.querySelector('input')!.value = '12';
    await press(cellOf(rowsIn(host)[0], 'note'));
    // The phone number is too short, so the phone cell keeps editing and keeps focus.
    expect(phone.classList.contains('editing')).toBe(true);
    expect(document.activeElement).toBe(phone.querySelector('input'));
    expect(activeCells(host)).toEqual([phone]);
    expect(focusedCell()).toBe(phone);
  });

  it('lets the user click past an empty required cell they did not change', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['note', 'code', 'phone'] }));
    await press(cellOf(rowsIn(host)[0], 'code'));
    await press(cellOf(rowsIn(host)[0], 'note'));
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'note').querySelector('input'));
    key(document.activeElement!, 'Tab');
    key(document.activeElement!, 'Tab');
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'phone').querySelector('input'));
    expect(activeCells(host)).toHaveLength(1);
  });

  it('Tabs from a text cell to the select, then the next text cell, then the next row', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['note', 'kind', 'phone'] }));
    await press(cellOf(rowsIn(host)[0], 'note'));
    key(document.activeElement!, 'Tab');
    expect(focusedCell()).toBe(cellOf(rowsIn(host)[0], 'kind'));
    key(document.activeElement!, 'Tab');
    expect(focusedCell()).toBe(cellOf(rowsIn(host)[0], 'phone'));
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'phone').querySelector('input'));
    key(document.activeElement!, 'Tab');
    expect(focusedCell()).toBe(cellOf(rowsIn(host)[1], 'note'));
    key(document.activeElement!, 'Tab', { shiftKey: true });
    expect(focusedCell()).toBe(cellOf(rowsIn(host)[0], 'phone'));
    expect(activeCells(host)).toHaveLength(1);
  });

  it('Tabs on from a cell in navigation mode, and out of the table to Add row after the last cell', async () => {
    const store = await entryLines(1);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['note', 'kind', 'phone'] }));
    const note = cellOf(rowsIn(host)[0], 'note');
    note.focus();
    key(note, 'Tab');
    expect(focusedCell()).toBe(cellOf(rowsIn(host)[0], 'kind'));
    key(document.activeElement!, 'Tab');
    key(document.activeElement!, 'Tab');
    expect(document.activeElement).toBe(host.querySelector('.g-add'));
    expect(host.querySelector('td.editing')).toBeNull();
  });

  it('keeps focus in the table after a dropdown choice; Tab then reaches the next cell and Enter the cell below', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['note', 'kind', 'phone'] }));
    await press(cellOf(rowsIn(host)[0], 'kind'));
    const button = cellOf(rowsIn(host)[0], 'kind').querySelector<HTMLElement>('button')!;
    pick(button, 'Service');
    await tick();
    expect(store.table('lines').rows.peek()[0].field('kind').value.peek()).toBe('service');
    const kind = cellOf(rowsIn(host)[0], 'kind');
    expect(document.activeElement).toBe(kind);
    expect(kind.classList.contains('editing')).toBe(false);
    key(document.activeElement!, 'Tab');
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'phone').querySelector('input'));

    // Enter after a choice goes to the cell below, as the footer says.
    await press(cellOf(rowsIn(host)[1], 'kind'));
    pick(cellOf(rowsIn(host)[1], 'kind').querySelector<HTMLElement>('button')!, 'Other');
    await tick();
    await press(cellOf(rowsIn(host)[0], 'kind'));
    const again = cellOf(rowsIn(host)[0], 'kind').querySelector<HTMLElement>('button')!;
    again.click();
    const option = [...document.querySelectorAll<HTMLElement>('.pop [role="option"]')].find(item => item.textContent?.trim() === 'Spare')!;
    key(document.querySelector('.pop input') ?? option, 'ArrowUp');
    option.click();
    await tick();
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'kind'));
    key(document.activeElement!, 'Enter');
    expect(focusedCell()).toBe(cellOf(rowsIn(host)[1], 'kind'));
  });

  it('closes an open dropdown on the first Esc and cancels the cell edit on the second', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['note', 'kind', 'phone'] }));
    await press(cellOf(rowsIn(host)[0], 'kind'));
    const button = cellOf(rowsIn(host)[0], 'kind').querySelector<HTMLElement>('button')!;
    button.click();
    expect(document.querySelector('.pop')).not.toBeNull();
    key(document.activeElement!, 'Escape');
    expect(document.querySelector('.pop')).toBeNull();
    expect(document.activeElement).toBe(button);
    expect(cellOf(rowsIn(host)[0], 'kind').classList.contains('editing')).toBe(true);
    key(document.activeElement!, 'Escape');
    expect(host.querySelector('td.editing')).toBeNull();
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'kind'));
  });

  it('commits and returns focus to a link cell after a choice', async () => {
    const store = await entryLines();
    const links = { search: async () => [{ id: id(2), title: 'Licence' }], title: () => cell<string | null>('Licence') };
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['note', 'product', 'phone'], links }));
    await press(cellOf(rowsIn(host)[0], 'product'));
    key(cellOf(rowsIn(host)[0], 'product').querySelector('button')!, 'L');
    await tick();
    key(document.querySelector<HTMLInputElement>('.link-search input')!, 'Enter');
    await tick();
    expect(store.table('lines').rows.peek()[0].field('product').value.peek()).toBe(id(2));
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'product'));
    key(document.activeElement!, 'Tab');
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'phone').querySelector('input'));
  });

  it('Enter goes to the cell below and Esc cancels, for text, phone, date and currency cells', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns }));
    const typed: Record<string, [string, unknown]> = { note: ['Left side', 'Left side'], phone: ['+971 50 123 4567', null], due: ['05-11-2026', '2026-11-05'], rate: ['7', '7.00'] };
    for (const [col, [text, stored]] of Object.entries(typed)) {
      await press(cellOf(rowsIn(host)[0], col));
      const input = cellOf(rowsIn(host)[0], col).querySelector('input')!;
      expect(document.activeElement).toBe(input);
      input.value = text;
      key(input, 'Enter');
      const value = store.table('lines').rows.peek()[0].field(col).value.peek();
      if (stored !== null) expect(value).toBe(stored);
      else expect(value).toBeTruthy();
      expect(focusedCell()).toBe(cellOf(rowsIn(host)[1], col));
      const below = document.activeElement as HTMLInputElement;
      below.value = 'x';
      key(below, 'Escape');
      expect(document.activeElement).toBe(cellOf(rowsIn(host)[1], col));
      expect(host.querySelector('td.editing')).toBeNull();
    }
  });

  it('moves focus with a click in focus view and on a phone-width layout', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns }));
    host.querySelector<HTMLElement>('.g-bar .btn.sm:last-child')!.click();
    expect(host.querySelector('.grid.max')).not.toBeNull();
    await press(cellOf(rowsIn(host)[0], 'phone'));
    await press(cellOf(rowsIn(host)[1], 'note'));
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[1], 'note').querySelector('input'));
    expect(activeCells(host)).toHaveLength(1);
    // A tap on a phone: pointer events without a mouse, then click.
    const tap = cellOf(rowsIn(host)[0], 'kind');
    tap.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch' }));
    tap.focus();
    await Promise.resolve();
    tap.click();
    expect(focusedCell()).toBe(cellOf(rowsIn(host)[0], 'kind'));
    expect(activeCells(host)).toHaveLength(1);
  });

  it('marks the grid for assistive technology and shows the active cell', async () => {
    const store = await entryLines();
    const host = render(() => ChildTable({ table: store.table('lines'), columns }));
    expect(host.querySelector('table')!.getAttribute('role')).toBe('grid');
    await press(cellOf(rowsIn(host)[0], 'kind'));
    const tabbable = host.querySelectorAll('td[data-col][tabindex="0"]');
    expect(tabbable).toHaveLength(1);
    expect(tabbable[0]).toBe(cellOf(rowsIn(host)[0], 'kind'));
    expect([...host.querySelectorAll('td.active')]).toEqual([cellOf(rowsIn(host)[0], 'kind')]);
    // Esc returns to navigation: the cell itself holds the focus and stays the one active cell.
    key(document.activeElement!, 'Escape');
    expect(document.activeElement).toBe(cellOf(rowsIn(host)[0], 'kind'));
    key(document.activeElement!, 'ArrowDown');
    expect([...host.querySelectorAll('td.active')]).toEqual([document.activeElement]);
    expect(host.querySelectorAll('td[data-col][tabindex="0"]')).toHaveLength(1);
  });
});

// ---------- the Columns menu: drag, keyboard, pinned range ----------

describe('Columns menu', () => {
  const ROWH = 32;
  // jsdom lays nothing out: each menu entry is 32 px tall, in its order.
  const layout = () => {
    const original = HTMLElement.prototype.getBoundingClientRect;
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      if (!this.dataset.entry) return original.call(this);
      const at = [...this.parentElement!.querySelectorAll(':scope > [data-entry]')].indexOf(this);
      return { top: at * ROWH, bottom: at * ROWH + ROWH, height: ROWH, left: 0, right: 200, width: 200, x: 0, y: at * ROWH, toJSON: () => ({}) } as DOMRect;
    };
    // dragList finds the entry under the pointer.
    const hadPoint = 'elementFromPoint' in document, point = document.elementFromPoint;
    document.elementFromPoint = (_x: number, y: number) => document.querySelectorAll<HTMLElement>('.cols-pop [data-entry]')[Math.floor(y / ROWH)] ?? null;
    return () => {
      HTMLElement.prototype.getBoundingClientRect = original;
      if (hadPoint) document.elementFromPoint = point; else delete (document as any).elementFromPoint;
    };
  };
  let restore = () => {};
  afterEach(() => restore());

  const open = async (prefs = cell<TablePrefs>(emptyPrefs())) => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'rate', 'amount'], prefs }));
    [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes('Columns'))!.click();
    const menu = document.querySelector<HTMLElement>('.cols-pop')!;
    restore = layout();
    return { host, prefs, menu, store };
  };
  const entries = (menu: Element) => [...menu.querySelectorAll<HTMLElement>('[data-entry]')].map(row => row.dataset.entry);
  const headers = (host: Element) => [...host.querySelectorAll('thead th[data-key]')].map(th => th.getAttribute('data-key'));
  const gripOf = (menu: Element, entry: string) => menu.querySelector<HTMLElement>(`[data-entry="${entry}"] .col-grip`)!;
  // A press on the grip, a move to y and a release, as dragList reads them (events bubble from the grip).
  const drag = (menu: Element, entry: string, toY: number, pointerType = 'mouse') => {
    const grip = gripOf(menu, entry), from = entries(menu).indexOf(entry) * ROWH + ROWH / 2;
    const at = (type: string, y: number) => grip.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, button: 0, pointerId: 1, pointerType, clientX: 20, clientY: y }));
    at('pointerdown', from);
    at('pointermove', toY);
    at('pointerup', toY);
  };
  // The menu's own status (pins, refusals) and the drag announcements.
  const said = async (menu: Element) => { await Promise.resolve(); await Promise.resolve(); return menu.querySelector('.col-said')!.textContent; };
  const announced = (menu: Element) => menu.querySelector('[data-drag-live="column"]')!.textContent;

  it('shows the pinned range above a divider, with labelled grips, pins and eyes', async () => {
    const { menu } = await open();
    expect(entries(menu)).toEqual(['product', 'edge', 'qty', 'rate', 'amount']);
    expect(menu.querySelector('[role="list"]')!.getAttribute('aria-label')).toBe('Columns in order');
    expect(gripOf(menu, 'qty').getAttribute('aria-label')).toBe('Move Qty');
    expect(gripOf(menu, 'qty').getAttribute('aria-describedby')).toBe(menu.querySelector('.col-hint')!.id);
    expect(gripOf(menu, 'edge').getAttribute('aria-label')).toBe('Move the end of the pinned columns');
    expect(menu.querySelector('[aria-label="Unpin Product"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(menu.querySelector('[aria-label="Pin Qty"]')!.getAttribute('aria-pressed')).toBe('false');
    expect(menu.querySelector('[aria-label="Hide Qty"]')).not.toBeNull();
    expect(menu.querySelector('.col-said')!.getAttribute('aria-live')).toBe('polite');
  });

  it('reorders by dragging a grip with the mouse, and the table follows at once', async () => {
    const { host, prefs, menu } = await open();
    // Amount (entry 4) dropped between Qty and Rate.
    drag(menu, 'amount', 3 * ROWH);
    expect(entries(menu)).toEqual(['product', 'edge', 'qty', 'amount', 'rate']);
    expect(prefs.peek()).toMatchObject({ order: ['product', 'qty', 'amount', 'rate'], pinned: 1 });
    expect(headers(host)).toEqual(['product', 'qty', 'amount', 'rate']);
    expect(announced(menu)).toBe('Amount (not pinned) dropped. Amount (not pinned), position 4 of 5.');
  });

  it('pins by dragging a column above the divider, by touch too, and unpins by dragging below it', async () => {
    const { host, prefs, menu } = await open();
    drag(menu, 'rate', ROWH + 4, 'touch');
    expect(entries(menu)).toEqual(['product', 'rate', 'edge', 'qty', 'amount']);
    expect(prefs.peek().pinned).toBe(2);
    const rate = host.querySelector<HTMLElement>('th[data-key="rate"]')!;
    expect(rate.classList.contains('stk')).toBe(true);
    expect(rate.classList.contains('s2')).toBe(true);
    expect(host.querySelector('th[data-key="product"]')!.classList.contains('s2')).toBe(false);
    drag(menu, 'product', 3 * ROWH);
    expect(entries(menu)).toEqual(['rate', 'edge', 'product', 'qty', 'amount']);
    expect(prefs.peek().pinned).toBe(1);
    // Dragging the divider itself pins everything above it.
    drag(menu, 'edge', 4 * ROWH);
    expect(entries(menu)).toEqual(['rate', 'product', 'qty', 'edge', 'amount']);
    expect(prefs.peek().pinned).toBe(3);
    // and to the top unpins all.
    drag(menu, 'edge', 0);
    expect(prefs.peek().pinned).toBe(0);
    expect([...host.querySelectorAll('thead th.stk[data-key]')].map(th => th.getAttribute('data-key'))).toEqual([]);
  });

  it('keeps one column unpinned, by drag and by the pin button', async () => {
    const { prefs, menu } = await open(cell<TablePrefs>({ ...emptyPrefs(), pinned: 3 }));
    expect(entries(menu)).toEqual(['product', 'qty', 'rate', 'edge', 'amount']);
    const pinAmount = menu.querySelector<HTMLButtonElement>('[data-entry="amount"] .col-pin')!;
    expect(pinAmount.disabled).toBe(true);
    expect(pinAmount.getAttribute('aria-label')).toBe('Amount stays unpinned');
    drag(menu, 'edge', 4 * ROWH + 20);
    expect(prefs.peek().pinned).toBe(3);
    expect(await said(menu)).toContain('Keep at least one column unpinned');
  });

  it('moves with the keyboard: Space lifts, arrows move, Space drops, Esc cancels', async () => {
    const { host, prefs, menu } = await open();
    let grip = gripOf(menu, 'rate');
    grip.focus();
    key(grip, ' ');
    expect(grip.getAttribute('aria-pressed')).toBe('true');
    expect(announced(menu)).toContain('Rate (not pinned) lifted. Rate (not pinned), position 4 of 5.');
    key(grip, 'ArrowUp');
    await Promise.resolve();
    expect(entries(menu)).toEqual(['product', 'edge', 'rate', 'qty', 'amount']);
    expect(document.activeElement).toBe(gripOf(menu, 'rate'));
    key(document.activeElement!, 'ArrowUp');
    await Promise.resolve();
    expect(entries(menu)).toEqual(['product', 'rate', 'edge', 'qty', 'amount']);
    expect(announced(menu)).toBe('Rate (pinned), position 2 of 5');
    key(document.activeElement!, 'Enter');
    expect(prefs.peek()).toMatchObject({ order: ['product', 'rate', 'qty', 'amount'], pinned: 2 });
    expect(gripOf(menu, 'rate').getAttribute('aria-pressed')).toBe('false');
    // Esc puts a lifted column back and keeps the menu open.
    await Promise.resolve();
    grip = gripOf(menu, 'amount');
    grip.focus();
    key(grip, ' ');
    for (let step = 0; step < 4; step++) key(document.activeElement!, 'ArrowUp');
    await Promise.resolve();
    expect(headers(host)[0]).toBe('amount');
    key(document.activeElement!, 'Escape');
    expect(document.querySelector('.cols-pop')).not.toBeNull();
    expect(prefs.peek()).toMatchObject({ order: ['product', 'rate', 'qty', 'amount'], pinned: 2 });
    expect(headers(host)).toEqual(['product', 'rate', 'qty', 'amount']);
    expect(announced(menu)).toContain('Move cancelled');
  });

  it('sets sticky offsets for two and three pinned columns, in header, cells and totals', async () => {
    const prefs = cell<TablePrefs>({ ...emptyPrefs(), widths: { product: 150, qty: 80 }, pinned: 2 });
    const { host } = await open(prefs);
    const left = (selector: string) => host.querySelector<HTMLElement>(selector)!.style.left;
    // The row control column is 84 px wide.
    expect(left('th[data-key="product"]')).toBe('84px');
    expect(left('th[data-key="qty"]')).toBe('234px');
    expect(left('tbody tr[data-key] td[data-col="qty"]')).toBe('234px');
    expect(host.querySelector('tbody tr[data-key] td[data-col="qty"]')!.classList.contains('s2')).toBe(true);
    expect(host.querySelectorAll('tfoot td.stk').length).toBe(3);
    expect(left('th[data-key="rate"]')).toBe('');
    prefs.set({ ...prefs.peek(), pinned: 3 });
    expect(left('th[data-key="rate"]')).toBe('314px');
    expect(left('tbody tr[data-key] td[data-col="rate"]')).toBe('314px');
    expect(host.querySelector('th[data-key="qty"]')!.classList.contains('s2')).toBe(false);
    expect(host.querySelector('th[data-key="rate"]')!.classList.contains('s2')).toBe(true);
    // Editing a pinned cell keeps it pinned.
    host.querySelector<HTMLElement>('tbody tr[data-key] td[data-col="rate"]')!.click();
    expect(host.querySelector('td.editing')!.classList.contains('stk')).toBe(true);
  });

  it('reads prefs saved before pinning existed, and round-trips the new ones', async () => {
    const old = { widths: {}, order: ['product', 'rate', 'qty', 'amount'], hidden: ['qty'] };
    const { host, prefs, menu } = await open(cell(old as any));
    expect(headers(host)).toEqual(['product', 'rate', 'amount']);
    expect(host.querySelector('th[data-key="product"]')!.classList.contains('stk')).toBe(true);
    expect(host.querySelector('th[data-key="rate"]')!.classList.contains('stk')).toBe(false);
    expect(entries(menu)).toEqual(['product', 'edge', 'rate', 'qty', 'amount']);
    menu.querySelector<HTMLButtonElement>('[aria-label="Pin Rate"]')!.click();
    const saved = JSON.parse(JSON.stringify(prefs.peek()));
    expect(saved).toEqual({ widths: {}, order: ['product', 'rate', 'qty', 'amount'], hidden: ['qty'], pinned: 2 });
  });

  it('warns, without blocking, when pinned columns take most of the table', async () => {
    const { menu, prefs } = await open(cell<TablePrefs>({ ...emptyPrefs(), widths: { product: 500 } }));
    const scroller = document.querySelector<HTMLElement>('.g-scroll')!;
    Object.defineProperty(scroller, 'clientWidth', { value: 700, configurable: true });
    menu.querySelector<HTMLButtonElement>('[aria-label="Pin Qty"]')!.click();
    expect(prefs.peek().pinned).toBe(2);
    expect(menu.querySelector('.col-warn')!.textContent).toContain('Pinned columns take most');
  });

  it('keeps one column shown', async () => {
    const { prefs, menu } = await open(cell<TablePrefs>({ ...emptyPrefs(), hidden: ['qty', 'rate'] }));
    const amount = menu.querySelector<HTMLButtonElement>('[data-entry="amount"] .col-eye')!;
    amount.click();
    expect(prefs.peek().hidden).toEqual(['qty', 'rate', 'amount']);
    const product = menu.querySelector<HTMLButtonElement>('[data-entry="product"] .col-eye')!;
    expect(product.disabled).toBe(true);
    expect(product.getAttribute('aria-label')).toBe('Product stays shown');
  });
});

describe('ChildTable focus view', () => {
  const heading = { type: 'Order', icon: 'file-text', title: () => 'Big order', number: () => 'ORD-0042',
    mark: () => { const span = document.createElement('span'); span.className = 'org-mark'; span.textContent = 'B'; return span; },
    footer: () => { const bar = document.createElement('div'); bar.className = 'ev-savebar'; bar.textContent = 'Unsaved changes'; return bar; } };
  const focusButton = (host: Element) => host.querySelector<HTMLButtonElement>('.g-bar button[aria-pressed]')!;

  it('keeps the record header and the save bar around the table, outside what scrolls', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate', 'amount'], heading }));
    expect(host.querySelector('.g-rec')).toBeNull();
    focusButton(host).click();
    const head = host.querySelector<HTMLElement>('.grid.max > .g-rec')!;
    expect(head.querySelector('.g-rec-title')!.textContent).toBe('Big order');
    expect(head.querySelector('.g-rec-no')!.textContent).toBe('ORD-0042');
    expect(head.querySelector('.g-rec-type')!.textContent).toBe('Order');
    expect(head.querySelector('.org-mark')).not.toBeNull();
    expect(head.getAttribute('aria-label')).toBe('Order Big order');
    // The header and the save bar sit outside the scrolling rows, so they stay in view.
    const scroller = host.querySelector('.g-scroll')!;
    expect(scroller.contains(head)).toBe(false);
    expect(head.compareDocumentPosition(scroller) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    scroller.scrollTop = 400;
    scroller.dispatchEvent(new Event('scroll'));
    expect(host.querySelector('.grid.max > .g-rec')).toBe(head);
    expect(host.querySelector('.grid.max > .g-foot .ev-savebar')!.textContent).toBe('Unsaved changes');
    expect(host.querySelector('.g-crumb')).toBeNull();
  });

  it('goes back to the form with Esc or Back to form', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate', 'amount'], heading }));
    focusButton(host).click();
    focusButton(host).focus();
    key(document.activeElement!, 'Escape');
    expect(host.querySelector('.grid.max')).toBeNull();
    expect(host.querySelector('.g-rec')).toBeNull();
    expect(host.querySelector('.g-foot')).toBeNull();
    focusButton(host).click();
    [...host.querySelectorAll<HTMLButtonElement>('.g-rec button')].find(button => button.textContent!.includes('Back to form'))!.click();
    expect(host.querySelector('.grid.max')).toBeNull();
    expect(document.activeElement).toBe(focusButton(host));
    // Esc while a cell is being edited cancels the edit first and keeps focus view.
    focusButton(host).click();
    cellOf(rowsIn(host)[0], 'qty').click();
    key(document.activeElement!, 'Escape');
    expect(host.querySelector('.grid.max')).not.toBeNull();
    key(document.activeElement!, 'Escape');
    expect(host.querySelector('.grid.max')).toBeNull();
  });
});
