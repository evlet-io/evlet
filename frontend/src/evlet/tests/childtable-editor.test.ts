// A child table's row editor (a row's Edit opens the table in focus view with the rows as a list beside one form) and
// its section rows (a Table whose schema has "sections": true): headings with their own sums, closing, renaming,
// moving and deleting, read from and saved to the record's change set.
import { afterEach, describe, expect, it } from 'vitest';
import { ChildTable, rowSummary, type ChildTableControls } from '../ui/childtable';
import { cell } from '../reactive';
import { RecordStore } from '../model/record';
import { tableChanges } from '../model/changes';
import type { AppSchema } from '../model/schema';
import { backend, bigOrder, cleanup, id, render, schema } from './ui-helpers';
import { copy } from './server';
import responses from './api-fixture.json';

afterEach(cleanup);

const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('tbody tr[data-key]:not(.g-sec)')];
/** The rows of the outer table only (the row editor's card can hold a nested table). */
const outerRows = (host: Element) => [...host.querySelectorAll<HTMLElement>(':scope > .grid > .g-body > .g-scroll > table > tbody > tr[data-key]:not(.g-sec)')];
const press = (target: Element, key: string, options: KeyboardEventInit = {}) => target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options }));
const buttonIn = (root: Element, label: string) => [...root.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.trim() === label || button.getAttribute('aria-label') === label)!;
const position = (host: Element) => host.querySelector('.g-ed-pos')!.textContent;

describe('the row editor', () => {
  it('opens from a row\'s Edit in focus view, the table cut down to its first column beside the row\'s card, and Esc closes both', async () => {
    const { store } = await bigOrder(3);
    let controls: ChildTableControls | null = null;
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'rate', 'amount'], controls: value => { controls = value; } }));
    const edit = rowsIn(host)[1].querySelector<HTMLButtonElement>('.g-exp')!;
    expect(edit.getAttribute('aria-label')).toBe('Edit row 2');
    expect(edit.querySelector('svg')).not.toBeNull();
    edit.click();
    const grid = host.querySelector('.grid')!;
    expect(grid.classList.contains('max')).toBe(true);
    expect(controls!.focused.peek()).toBe(true);
    // The table stays, with its first column only, the open row marked; the card is beside it.
    expect(grid.classList.contains('g-editing')).toBe(true);
    expect(rowsIn(host)).toHaveLength(3);
    expect([...host.querySelectorAll(':scope > .grid > .g-body > .g-scroll thead th[data-key]')].map(th => th.getAttribute('data-key'))).toEqual(['product']);
    expect(rowsIn(host).map(row => row.classList.contains('open'))).toEqual([false, true, false]);
    expect(host.querySelector('.g-body > .g-scroll + .g-ed')).not.toBeNull();
    // The bar is a plain title line: no record strip.
    expect(host.querySelector('.g-rec')).toBeNull();
    expect(position(host)).toBe('2 of 3');
    expect(host.querySelector('.g-ed-badge')!.textContent).toBe('2');
    // The summary: the two values typed in times each other, and the calculated one.
    expect(host.querySelector('.g-ed-sum')!.textContent).toBe('2 × AED 2.50 = AED 5.00');
    // Every field of the row, as inputs (a row is saved with the record).
    expect([...host.querySelectorAll('.g-ed-body .fld')].length).toBeGreaterThanOrEqual(4);
    press(host.querySelector('.g-ed-main')!, 'Escape');
    expect(host.querySelector('.g-ed')).toBeNull();
    // Opened by Edit, focus view closes with the editor.
    expect(grid.classList.contains('max')).toBe(false);
  });

  it('steps with previous and next, Alt and an arrow, and a click on a row\'s title; Done closes it', async () => {
    const { store } = await bigOrder(3);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'amount'] }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    expect(buttonIn(host.querySelector('.g-ed-h')!, 'Previous row').disabled).toBe(true);
    buttonIn(host.querySelector('.g-ed-h')!, 'Next row').click();
    expect(position(host)).toBe('2 of 3');
    press(host.querySelector('.g-ed-body input')!, 'ArrowDown', { altKey: true });
    expect(position(host)).toBe('3 of 3');
    expect(buttonIn(host.querySelector('.g-ed-h')!, 'Next row').disabled).toBe(true);
    // A row's title opens it (not an inline edit), Enter on a title too; the open row's title closes the editor.
    rowsIn(host)[0].querySelector<HTMLElement>('td[data-col="product"]')!.click();
    expect(position(host)).toBe('1 of 3');
    expect(host.querySelector('td.editing')).toBeNull();
    press(rowsIn(host)[1].querySelector('td[data-col="product"]')!, 'Enter');
    expect(position(host)).toBe('2 of 3');
    rowsIn(host)[1].querySelector<HTMLElement>('td[data-col="product"]')!.click();
    expect(host.querySelector('.g-ed')).toBeNull();
    rowsIn(host)[1].querySelector<HTMLButtonElement>('.g-exp')!.click();
    buttonIn(host.querySelector('.g-ed-f')!, 'Done').click();
    expect(host.querySelector('.g-ed')).toBeNull();
  });

  it('inserts a row below and deletes a line, moving to the next one', async () => {
    const { store } = await bigOrder(3);
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    buttonIn(host.querySelector('.g-ed-f')!, 'Insert below').click();
    expect(table.rows.peek()).toHaveLength(4);
    expect(position(host)).toBe('2 of 4');
    expect(table.rows.peek()[1].isNew).toBe(true);
    const second = table.rows.peek()[2].key;
    buttonIn(host.querySelector('.g-ed-f')!, 'Delete line').click();
    expect(table.rows.peek()).toHaveLength(3);
    expect(host.querySelector('tbody tr.open')!.getAttribute('data-key')).toBe(second);
  });

  it('draws the caller\'s form for the row, and offers no Delete or Insert while locked', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty'], locked: true,
      rowForm: (row, field) => { const qty = field('qty'); return Object.assign(document.createElement('p'), { className: 'mine', textContent: `${row.key}:${qty.editable.peek()}` }); } }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    expect(host.querySelector('.g-ed-body .mine')!.textContent).toBe(`${store.table('lines').rows.peek()[0].key}:false`);
    expect(buttonIn(host.querySelector('.g-ed-f')!, 'Delete line')).toBeUndefined();
    expect(buttonIn(host.querySelector('.g-ed-f')!, 'Done')).not.toBeUndefined();
  });

  it('sums a row up from its values when there is nothing to multiply', async () => {
    const { store } = await bigOrder(1);
    const row = store.table('lines').rows.peek()[0];
    const columns = ['qty', 'rate'].map(name => { const meta = row.field(name).meta; return { key: name, label: meta.label, width: 100, meta, calculated: false, align: 'right' as const }; });
    expect(rowSummary(row, columns)).toBe('AED 2.50');
  });
});

describe('Focus and Columns from outside the table', () => {
  it('hands its Focus and Columns to the caller; Esc leaves focus view', async () => {
    const { store } = await bigOrder(2);
    let controls: ChildTableControls | null = null;
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty'], controls: value => { controls = value; } }));
    expect(controls!.sectioned).toBe(false);
    controls!.toggleFocus();
    const grid = host.querySelector('.grid')!;
    expect(grid.classList.contains('max')).toBe(true);
    press(grid, 'Escape');
    expect(grid.classList.contains('max')).toBe(false);
    const anchor = document.createElement('button');
    document.body.append(anchor);
    controls!.columns(anchor);
    expect(document.querySelector('.cols-pop')).not.toBeNull();
  });
});

/** The recorded order with its lines able to take sections (summing amount), two section rows on the first page, and
 * one on the second page before its first line, as the lines route pages them. */
async function sectionedOrder(options: { sums?: string[] | null; page?: boolean } = {}) {
  const sectioned = structuredClone(schema) as AppSchema;
  const child = sectioned.models.find(model => model.name === 'order')!.children![0];
  child.sections = true;
  if (options.sums !== null) child.section_sums = options.sums ?? ['amount'];
  const detail = copy(responses.detail);
  const lines: Array<Record<string, unknown>> = detail.body.children.lines;
  lines.splice(2, 0, { id: 's2', section: 'Services' });
  lines.splice(0, 0, { id: 's1', section: 'Hardware' });
  const page = copy(responses.lines_page);
  if (options.page) (page.body.items as Array<Record<string, unknown>>).unshift({ id: 's3', section: 'Spares' });
  const asked: string[] = [];
  const { api } = backend([['GET', /\/api\/order\/0000000000001$/, detail],
    ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, (sent: { url: string }) => { asked.push(sent.url); return page; }]]);
  const store = new RecordStore({ api, schema: sectioned, model: 'order', today: () => '2026-11-01' });
  await store.open(id(1));
  return Object.assign(store, { asked });
}

describe('section rows', () => {
  it('reads section rows from the payload, apart from the lines, each before the line that follows it', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    expect(table.rows.peek()).toHaveLength(120);
    expect(table.sections.peek().map(section => [section.label, section.before])).toEqual([['Hardware', table.rows.peek()[0].key], ['Services', table.rows.peek()[2].key]]);
    expect(table.changed.peek()).toBe(false);
  });

  it('draws a section row as a heading with its lines count and its own sums, and closes it', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    const sections = () => [...host.querySelectorAll<HTMLElement>('tr.g-sec')];
    expect(sections().map(row => row.querySelector('.g-sec-name')!.textContent)).toEqual(['Hardware', 'Services']);
    expect(sections()[0].querySelector('.g-sec-n')!.textContent).toBe('2 lines');
    expect(sections()[1].querySelector('.g-sec-n')!.textContent).toBe('118 lines');
    // Hardware sums the amounts of its two lines, in the Amount column (the column the total line sums).
    const amounts = table.rows.peek().slice(0, 2).map(row => Number(row.field('amount').value.peek()));
    // The cells after the control column: Product, Qty, Amount.
    const cells = sections()[0].querySelectorAll('td');
    expect(cells[2].textContent).toBe('');
    expect(cells[3].textContent!.replace(/[^\d.]/g, '')).toBe((amounts[0] + amounts[1]).toFixed(2));
    // Every line is drawn once there are sections, numbered across the sections.
    expect(rowsIn(host)).toHaveLength(120);
    expect(rowsIn(host)[2].querySelector('.g-exp')!.getAttribute('aria-label')).toBe('Edit row 3');
    sections()[0].querySelector<HTMLButtonElement>('.g-sec-tog')!.click();
    expect(rowsIn(host)).toHaveLength(118);
    expect(sections()[0].querySelector('.g-sec-tog')!.getAttribute('aria-expanded')).toBe('false');
  });

  it('renames, adds and deletes sections, keeping their lines, and saves them in the change set', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    let controls: ChildTableControls | null = null;
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'], controls: value => { controls = value; } }));
    expect(controls!.sectioned).toBe(true);
    host.querySelector<HTMLButtonElement>('tr.g-sec .g-sec-name')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    const input = host.querySelector<HTMLInputElement>('tr.g-sec .g-sec-input')!;
    input.value = 'Equipment';
    press(input, 'Enter');
    expect(table.sections.peek()[0].label).toBe('Equipment');
    // The add row offers a section too; a new one starts being renamed.
    expect(host.querySelector('.g-add-sec')!.textContent).toContain('add a section');
    controls!.addSection();
    expect(table.sections.peek().map(section => section.label)).toEqual(['Equipment', 'Services', 'New section']);
    await Promise.resolve();
    expect(host.querySelector('tr.g-sec .g-sec-input')).not.toBeNull();
    press(host.querySelector('tr.g-sec .g-sec-input')!, 'Escape');
    // Deleting Services keeps its 118 lines.
    buttonIn(host.querySelectorAll('tr.g-sec')[1], 'Delete section Services (its lines stay)').click();
    expect(table.rows.peek()).toHaveLength(120);
    const changes = tableChanges(table)!;
    expect(changes.create).toEqual([{ _key: table.sections.peek()[1].key, section: 'New section' }]);
    expect(changes.update).toEqual([{ id: 's1', section: 'Equipment' }]);
    expect(changes.delete).toEqual(['s2']);
    const order = changes.order as string[];
    expect(order[0]).toBe('s1');
    expect(order.at(-1)).toBe(table.sections.peek()[1].key);
    expect(order).toHaveLength(122);
  });

  it('moves a section to the line it is dropped before, and a line dropped on a section\'s first line joins it', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    const [first, second, third] = table.rows.peek();
    // Services moves up to start at the second line.
    table.moveSection(table.sections.peek()[1].key, second.key);
    expect(table.sections.peek()[1].before).toBe(second.key);
    // Removing the line a section starts at moves the section to the next line.
    table.remove(second);
    expect(table.sections.peek()[1].before).toBe(third.key);
    expect(first.key).toBe(table.sections.peek()[0].before);
  });

  it('puts a section dropped on another section above it', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    const names = () => [...host.querySelectorAll('tr.g-sec .g-sec-name')].map(node => node.textContent);
    // Services lifted by its handle and moved up past Hardware's two lines lands on Hardware: it goes above it.
    const handle = host.querySelectorAll<HTMLButtonElement>('tr.g-sec [data-drag-handle]')[1];
    handle.focus();
    press(handle, ' ');
    for (let step = 0; step < 3; step++) press(handle, 'ArrowUp');
    press(handle, ' ');
    expect(names()).toEqual(['Services', 'Hardware']);
    expect(table.sections.peek().map(section => section.before)).toEqual([table.rows.peek()[0].key, table.rows.peek()[0].key]);
    // The store's own call, with the section to go above.
    table.moveSection(table.sections.peek()[1].key, table.rows.peek()[0].key, table.sections.peek()[0].key);
    expect(table.sections.peek().map(section => section.label)).toEqual(['Hardware', 'Services']);
  });

  it('pages in lines only: a later page brings the section rows before its first line, and asks once', async () => {
    const store = await sectionedOrder({ page: true });
    const table = store.table('lines');
    expect(table.rows.peek()).toHaveLength(120);
    expect(store.asked).toHaveLength(1);
    expect(table.sections.peek().map(section => [section.label, section.before])).toEqual([
      ['Hardware', table.rows.peek()[0].key], ['Services', table.rows.peek()[2].key], ['Spares', table.rows.peek()[100].key]]);
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    expect([...host.querySelectorAll('tr.g-sec .g-sec-n')].map(node => node.textContent)).toEqual(['2 lines', '98 lines', '20 lines']);
  });

  it('sums only the fields section_sums names, and none without it', async () => {
    const store = await sectionedOrder({ sums: null });
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'amount'] }));
    const cells = host.querySelectorAll('tr.g-sec')[0].querySelectorAll('td');
    expect([...cells].slice(2).map(cell => cell.textContent)).toEqual(['', '']);
    cleanup();
    const qty = await sectionedOrder({ sums: ['qty'] });
    const other = render(() => ChildTable({ table: qty.table('lines'), columns: ['product', 'qty', 'amount'] }));
    const row = other.querySelectorAll('tr.g-sec')[0].querySelectorAll('td');
    expect(row[2].textContent).not.toBe('');
    expect(row[3].textContent).toBe('');
  });

  it('offers no section edits on a locked record', async () => {
    const store = await sectionedOrder();
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty', 'amount'], locked: true }));
    const section = host.querySelector('tr.g-sec')!;
    // The label still shows or hides the lines, but a double click does not rename it.
    expect(section.querySelector('.g-sec-name')!.textContent).toBe('Hardware');
    section.querySelector('.g-sec-name')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(section.querySelector('.g-sec-input')).toBeNull();
    expect(section.querySelector('.g-drag')).toBeNull();
    expect(section.querySelector('.icon-btn')).toBeNull();
    expect(host.querySelector('.g-add-sec')).toBeNull();
    // Closing a section only changes the view, so it stays.
    expect(section.querySelector('.g-sec-tog')).not.toBeNull();
  });

  it('keeps the sections in a duplicate, as {section} entries in their places', async () => {
    const store = await sectionedOrder();
    const { duplicateOf } = await import('../app/duplicate');
    const model = store.index.model('order') as never;
    const copied = duplicateOf(model, store.root, 'Big order').values.children as Record<string, Array<Record<string, unknown>>>;
    expect(copied.lines[0]).toEqual({ section: 'Hardware' });
    expect(copied.lines[3]).toEqual({ section: 'Services' });
    expect(copied.lines.filter(item => 'section' in item)).toHaveLength(2);
    const { api } = backend();
    const fresh = new RecordStore({ api, schema: (store as unknown as { index: { schema: AppSchema } }).index.schema, model: 'order', today: () => '2026-11-01' });
    await fresh.create({ ...duplicateOf(model, store.root, 'Big order').values });
    const table = fresh.table('lines');
    expect(table.rows.peek()).toHaveLength(120);
    expect(table.sections.peek().map(section => [section.label, section.before])).toEqual([['Hardware', table.rows.peek()[0].key], ['Services', table.rows.peek()[2].key]]);
    fresh.dispose();
  });

  it('works as before when the schema has no sections', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty'] }));
    expect(host.querySelector('tr.g-sec')).toBeNull();
    expect(host.querySelector('.g-add-sec')).toBeNull();
    expect(tableChanges(store.table('lines'))).not.toHaveProperty('order');
  });
});

describe('the row editor beside the table', () => {
  const links = { search: async () => [], title: (_model: string, key: string) => cell<string | null>(`Product ${key.slice(-1)}`) };

  it('keeps the table\'s structure on the left, by row title and never record ids', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'], links }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    expect([...host.querySelectorAll('tr.g-sec .g-sec-name')].map(node => node.textContent)).toEqual(['Hardware', 'Services']);
    const product = String(table.rows.peek()[0].field('product').value.peek());
    expect(rowsIn(host)[0].querySelector('td[data-col="product"]')!.textContent).toContain(`Product ${product.slice(-1)}`);
    expect(host.querySelector('.g-scroll')!.textContent).not.toMatch(/#[0-9a-z]{10,}/);
    expect(host.querySelector('.g-ed-ttl b')!.textContent).toBe(`Product ${product.slice(-1)}`);
    // A section row keeps its count, and the footer its line count.
    expect(host.querySelector('tr.g-sec .g-sec-n')!.textContent).toBe('2 lines');
  });

  it('a click on a section title hides or shows its rows, with the editor open or not; the open row\'s title closes the editor', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'], links }));
    const name = (at: number) => host.querySelectorAll<HTMLButtonElement>('tr.g-sec .g-sec-name')[at];
    // In the normal table.
    name(1).click();
    expect(outerRows(host)).toHaveLength(2);
    expect(name(1).getAttribute('aria-expanded')).toBe('false');
    name(1).click();
    expect(outerRows(host)).toHaveLength(120);
    // With the editor open.
    outerRows(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    name(1).click();
    expect(outerRows(host)).toHaveLength(2);
    name(1).click();
    outerRows(host)[1].querySelector<HTMLElement>('td[data-col="product"]')!.click();
    expect(position(host)).toBe('2 of 120');
    outerRows(host)[1].querySelector<HTMLElement>('td[data-col="product"]')!.click();
    expect(host.querySelector('.g-ed')).toBeNull();
  });

  it('shows a new row\'s empty title as its placeholder, and "Not set" in empty inputs', async () => {
    const { store } = await bigOrder(1);
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'rate', 'amount'], links }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    buttonIn(host.querySelector('.g-ed-f')!, 'Insert below').click();
    const label = table.rows.peek()[1].field('product').meta;
    expect(rowsIn(host)[1].querySelector('td[data-col="product"] .g-ph')!.textContent).toBe(label.placeholder || label.label);
    expect(host.querySelector('.g-ed-f .btn.g-ed-del')).not.toBeNull();
    expect(buttonIn(host.querySelector('.g-ed-f')!, 'Done').classList.contains('primary')).toBe(true);
  });

  it('shows the values of currency inputs in the form', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate', 'amount'] }));
    rowsIn(host)[0].querySelector<HTMLButtonElement>('.g-exp')!.click();
    expect(host.querySelector<HTMLInputElement>('.g-ed-body .fld[data-field="rate"] input')!.value).toBe('2.50');
    const empty = [...host.querySelectorAll<HTMLInputElement>('.g-ed-body input')].find(input => !input.value);
    if (empty) expect(empty.placeholder).toBe('Not set');
  });
});

describe('a derived table', () => {
  it('keeps only the row number: no selection, drag handles or Edit, and no lock line without a note', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty'], derived: true, locked: true, lockedText: '' }));
    expect(host.querySelector('.g-ctl input[type="checkbox"]')).toBeNull();
    expect(host.querySelector('.g-drag')).toBeNull();
    expect(host.querySelector('.g-exp')).toBeNull();
    expect(rowsIn(host).map(row => row.querySelector('.g-ci-n')!.textContent)).toEqual(['1', '2']);
    expect(host.querySelector('.g-lock')).toBeNull();
    expect(host.querySelector('.g-add')).toBeNull();
  });
});
