// A child table adds, selects and places rows: an Add row placeholder as the table's last row (and the last row of each
// open section), new rows placed in the section they were added to (kept by the change set's order), and the selection
// bar's Duplicate and Delete. None of it on a locked table.
import { afterEach, describe, expect, it } from 'vitest';
import { ChildTable, type ChildTableControls } from '../ui/childtable';
import { RecordStore } from '../model/record';
import { tableChanges } from '../model/changes';
import type { AppSchema } from '../model/schema';
import { backend, bigOrder, cleanup, id, render, schema } from './ui-helpers';
import { copy } from './server';
import responses from './api-fixture.json';

afterEach(cleanup);

const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('tbody tr[data-key]:not(.g-sec)')];
const placeholders = (host: Element) => [...host.querySelectorAll<HTMLElement>('tbody tr.g-addrow')];
const finalAdd = (host: Element) => host.querySelector<HTMLElement>('tbody tr.g-addrow-end');
const press = (target: Element, key: string, options: KeyboardEventInit = {}) => target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options }));
const barButton = (host: Element, label: string) => [...host.querySelectorAll<HTMLButtonElement>('.g-bar button')].find(button => button.textContent!.includes(label));

/** The recorded order with sections: Hardware over its first two lines, Services over the other 118. */
async function sectionedOrder(detailOf?: (lines: Array<Record<string, unknown>>) => Array<Record<string, unknown>>) {
  const sectioned = structuredClone(schema) as AppSchema;
  const child = sectioned.models.find(model => model.name === 'order')!.children![0];
  child.sections = true;
  child.section_sums = ['amount'];
  const detail = copy(responses.detail);
  const lines: Array<Record<string, unknown>> = detail.body.children.lines;
  lines.splice(2, 0, { id: 's2', section: 'Services' });
  lines.splice(0, 0, { id: 's1', section: 'Hardware' });
  if (detailOf) detail.body.children.lines = detailOf(lines);
  const { api } = backend([['GET', /\/api\/order\/0000000000001$/, detail],
    ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, copy(responses.lines_page)]]);
  const store = new RecordStore({ api, schema: sectioned, model: 'order', today: () => '2026-11-01' });
  await store.open(id(1));
  return store;
}

describe('the Add row placeholder', () => {
  it('is the last row, above the total line, and adds a row there with the cursor in its first editable cell', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate', 'amount'] }));
    const add = finalAdd(host)!;
    expect(add).not.toBeNull();
    // The last row of the body, after the rows (the total line is the footer).
    expect([...host.querySelectorAll('tbody tr:not(.spacer)')].at(-1)).toBe(add);
    expect(add.querySelector('.g-add')!.textContent).toBe('Add row');
    expect(add.querySelector('.g-addrow-plus')).not.toBeNull();
    // No sections in this schema, so no "or add a section"; no Add row button under the table either.
    expect(add.querySelector('.g-add-sec')).toBeNull();
    expect(host.querySelector('.g-under .g-add')).toBeNull();
    add.querySelector<HTMLButtonElement>('.g-add')!.click();
    expect(rowsIn(host)).toHaveLength(3);
    const cell = rowsIn(host)[2].querySelector('td[data-col="qty"]')!;
    expect(cell.classList.contains('editing')).toBe(true);
    expect(cell.contains(document.activeElement)).toBe(true);
  });

  it('a click anywhere on the placeholder adds a row, and Enter in a new last row with a value adds the next', async () => {
    const { store } = await bigOrder(1);
    const lines = store.table('lines');
    const host = render(() => ChildTable({ table: lines, columns: ['qty', 'rate'] }));
    finalAdd(host)!.querySelector<HTMLElement>('td')!.click();
    expect(lines.rows.peek()).toHaveLength(2);
    const input = host.querySelector<HTMLInputElement>('td.editing input')!;
    input.value = '4';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    press(input, 'Enter');
    expect(lines.rows.peek()).toHaveLength(3);
    expect(lines.rows.peek()[1].field('qty').value.peek()).toBe('4.0000');
    expect(rowsIn(host)[2].querySelector('td[data-col="qty"]')!.classList.contains('editing')).toBe(true);
    // Enter in a new row without typing anything does not pile up blank rows.
    press(host.querySelector('td.editing input')!, 'Enter');
    expect(lines.rows.peek()).toHaveLength(3);
  });

  it('Enter in a saved last row moves on without adding a row', async () => {
    const store = await sectionedOrder();
    const lines = store.table('lines');
    const host = render(() => ChildTable({ table: lines, columns: ['product', 'qty'] }));
    rowsIn(host).at(-1)!.querySelector<HTMLElement>('td[data-col="qty"]')!.click();
    press(host.querySelector('td.editing input')!, 'Enter');
    expect(lines.rows.peek()).toHaveLength(120);
  });

  it('is hidden on a locked table and at max_rows', async () => {
    const { store } = await bigOrder(2);
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['qty', 'rate'], locked: true }));
    expect(placeholders(host)).toHaveLength(0);
    expect(host.querySelector('.g-add')).toBeNull();
    cleanup();
    const full = await bigOrder(2);
    // The table's schema entry is shared with the other tests: put max_rows back after.
    const meta = full.store.table('lines').meta, max = meta.max_rows;
    meta.max_rows = 3;
    try {
      const other = render(() => ChildTable({ table: full.store.table('lines'), columns: ['qty', 'rate'] }));
      finalAdd(other)!.querySelector<HTMLButtonElement>('.g-add')!.click();
      expect(full.store.table('lines').rows.peek()).toHaveLength(3);
      expect(finalAdd(other)).toBeNull();
    } finally { meta.max_rows = max; }
  });
});

describe('adding rows to a table with sections', () => {
  it('ends each open section with its own placeholder; the last one adds to the last section and offers a section', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    const [hardware, services] = table.sections.peek();
    // Hardware's placeholder sits after its two lines, before the Services section row.
    const own = placeholders(host).filter(row => !row.classList.contains('g-addrow-end'));
    expect(own.map(row => row.dataset.add)).toEqual([hardware.key]);
    expect(own[0].previousElementSibling).toBe(rowsIn(host)[1]);
    expect(own[0].nextElementSibling!.classList.contains('g-sec')).toBe(true);
    expect(own[0].querySelector('.g-add-sec')).toBeNull();
    // The last placeholder carries "or add a section", in the next column.
    const tds = finalAdd(host)!.querySelectorAll('td');
    expect(tds[1].textContent).toBe('Add row');
    expect(tds[2].textContent).toBe('or add a section');
    // A closed section shows no placeholder.
    host.querySelector<HTMLButtonElement>('tr.g-sec .g-sec-tog')!.click();
    expect(placeholders(host).filter(row => !row.classList.contains('g-addrow-end'))).toHaveLength(0);
    expect(services.key).toBe(table.sections.peek()[1].key);
  });

  it('adds at the end of the section, and the change set\'s order keeps it there after a save and reload', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    const [first, second, third] = table.rows.peek();
    placeholders(host)[0].querySelector<HTMLButtonElement>('.g-add')!.click();
    const added = table.rows.peek()[2];
    expect(added.isNew).toBe(true);
    expect(table.rows.peek().slice(0, 4)).toEqual([first, second, added, third]);
    // Services still starts at its first line, so the new row is Hardware's third.
    expect(table.sections.peek()[1].before).toBe(third.key);
    expect(host.querySelector('tr.g-sec .g-sec-n')!.textContent).toBe('3 lines');
    // The cursor is in the new row's first editable cell (the product picker).
    expect(host.querySelector('td.editing')!.closest('tr')!.dataset.key).toBe(added.key);
    const order = tableChanges(table)!.order as string[];
    expect(order.slice(0, 5)).toEqual(['s1', first.id, second.id, added.key, 's2']);
    expect(order).toHaveLength(123);
    // The server stores rows in that order; reading it back puts the row under Hardware again.
    const reloaded = await sectionedOrder(lines => {
      const byId = new Map(lines.map(line => [String(line.id), line]));
      const copyOf = (({ children: _nested, ...line }) => ({ ...line, id: id(950) }))(byId.get(String(first.id))!);
      // The detail carries the first page of lines; the rest come from the lines route as before.
      return order.flatMap(item => item === added.key ? [copyOf] : byId.has(item) ? [byId.get(item)!] : []);
    });
    const again = reloaded.table('lines');
    expect(again.rows.peek()[2].id).toBe(id(950));
    expect(again.sections.peek().map(section => [section.label, section.before])).toEqual([
      ['Hardware', again.rows.peek()[0].key], ['Services', again.rows.peek()[3].key]]);
    reloaded.dispose();
  });

  it('the last placeholder adds to the end of the last section, also a new empty one, and opens a closed one', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    // "or add a section" adds a section at the end, its label being renamed.
    finalAdd(host)!.querySelector<HTMLButtonElement>('.g-add-sec')!.click();
    await Promise.resolve();
    const input = host.querySelector<HTMLInputElement>('tr.g-sec .g-sec-input')!;
    expect(input).not.toBeNull();
    input.value = 'Delivery';
    press(input, 'Enter');
    const delivery = table.sections.peek()[2];
    expect(delivery.label).toBe('Delivery');
    // The new empty section now has the last placeholder, and Services ends with its own.
    expect(placeholders(host).filter(row => !row.classList.contains('g-addrow-end')).map(row => row.dataset.add)).toEqual(
      [table.sections.peek()[0].key, table.sections.peek()[1].key]);
    finalAdd(host)!.querySelector<HTMLButtonElement>('.g-add')!.click();
    const added = table.rows.peek().at(-1)!;
    expect(table.sections.peek()[2].before).toBe(added.key);
    const order = tableChanges(table)!.order as string[];
    expect(order.slice(-2)).toEqual([delivery.key, added.key]);
    // Into a closed last section: it opens to show the new row.
    host.querySelectorAll<HTMLButtonElement>('tr.g-sec .g-sec-tog')[2].click();
    press(host.querySelector('td.editing') ?? host, 'Escape');
    finalAdd(host)!.querySelector<HTMLButtonElement>('.g-add')!.click();
    expect(host.querySelectorAll('tr.g-sec .g-sec-tog')[2].getAttribute('aria-expanded')).toBe('true');
    expect(rowsIn(host).at(-1)!.dataset.key).toBe(table.rows.peek().at(-1)!.key);
  });
});

describe('the selection bar', () => {
  it('shows the count, then Duplicate and Delete; Duplicate copies the rows without ids after the last selected', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    let controls: ChildTableControls | null = null;
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'], controls: value => { controls = value; } }));
    const [first, second, third] = table.rows.peek();
    rowsIn(host)[0].querySelector<HTMLElement>('[role="checkbox"]')!.click();
    rowsIn(host)[2].querySelector<HTMLElement>('[role="checkbox"]')!.click();
    expect(host.querySelector('.g-selinfo')!.textContent).toBe('2 selected');
    expect(controls!.selected.peek()).toBe(2);
    barButton(host, 'Duplicate')!.click();
    const rows = table.rows.peek();
    expect(rows).toHaveLength(122);
    // Both copies directly after the last selected row (the third line, in Services), in their order.
    expect(rows.slice(0, 5).map(row => row === first || row === second || row === third ? row.key : 'copy')).toEqual([first.key, second.key, third.key, 'copy', 'copy']);
    expect(rows[3].isNew && rows[4].isNew).toBe(true);
    expect(rows[3].field('qty').value.peek()).toBe(first.field('qty').value.peek());
    expect(rows[4].field('qty').value.peek()).toBe(third.field('qty').value.peek());
    expect(tableChanges(table)!.create).toHaveLength(2);
    expect((tableChanges(table)!.create as Array<Record<string, unknown>>).every(item => !('id' in item))).toBe(true);
  });

  it('a section\'s checkbox selects the section alone; Duplicate copies it with its lines, Delete keeps its lines', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    const [first, second, third] = table.rows.peek();
    host.querySelector<HTMLElement>('tr.g-sec [role="checkbox"]')!.click();
    expect(host.querySelector('.g-selinfo')!.textContent).toBe('1 selected');
    expect(host.querySelector('tr.g-sec')!.classList.contains('sel')).toBe(true);
    expect(rowsIn(host).some(row => row.classList.contains('sel'))).toBe(false);
    barButton(host, 'Duplicate')!.click();
    expect(table.sections.peek().map(section => section.label)).toEqual(['Hardware', 'Hardware', 'Services']);
    const rows = table.rows.peek();
    expect(rows.slice(0, 2)).toEqual([first, second]);
    expect(rows[2].isNew && rows[3].isNew).toBe(true);
    expect(rows[4]).toBe(third);
    expect(table.sections.peek()[1].before).toBe(rows[2].key);
    expect(table.sections.peek()[2].before).toBe(third.key);
    const order = tableChanges(table)!.order as string[];
    expect(order.slice(0, 7)).toEqual(['s1', first.id, second.id, table.sections.peek()[1].key, rows[2].key, rows[3].key, 's2']);
    // Delete the copied section: its two lines stay, now under the first Hardware.
    host.querySelectorAll<HTMLElement>('tr.g-sec [role="checkbox"]')[0].click();
    host.querySelectorAll<HTMLElement>('tr.g-sec [role="checkbox"]')[1].click();
    expect(host.querySelector('.g-selinfo')!.textContent).toBe('1 selected');
    barButton(host, 'Delete')!.click();
    expect(table.sections.peek().map(section => section.label)).toEqual(['Hardware', 'Services']);
    expect(table.rows.peek()).toHaveLength(122);
    expect(host.querySelector('tr.g-sec .g-sec-n')!.textContent).toBe('4 lines');
  });

  it('Delete removes the selected rows and sections through the change set', async () => {
    const store = await sectionedOrder();
    const table = store.table('lines');
    const host = render(() => ChildTable({ table, columns: ['product', 'qty', 'amount'] }));
    const [first] = table.rows.peek();
    rowsIn(host)[0].querySelector<HTMLElement>('[role="checkbox"]')!.click();
    host.querySelectorAll<HTMLElement>('tr.g-sec [role="checkbox"]')[1].click();
    expect(host.querySelector('.g-selinfo')!.textContent).toBe('2 selected');
    barButton(host, 'Delete')!.click();
    expect(table.rows.peek()).toHaveLength(119);
    expect(table.sections.peek().map(section => section.label)).toEqual(['Hardware']);
    const changes = tableChanges(table)!;
    expect(changes.delete).toEqual([first.id, 's2']);
    expect(host.querySelector('.g-selinfo')).toBeNull();
  });

  it('is hidden on a locked table, with rows selected', async () => {
    const store = await sectionedOrder();
    let controls: ChildTableControls | null = null;
    const host = render(() => ChildTable({ table: store.table('lines'), columns: ['product', 'qty'], locked: true, controls: value => { controls = value; } }));
    rowsIn(host)[0].querySelector<HTMLElement>('[role="checkbox"]')!.click();
    expect(controls!.selected.peek()).toBe(1);
    expect(controls!.editable.peek()).toBe(false);
    expect(host.querySelector('.g-selinfo')).toBeNull();
    expect(barButton(host, 'Duplicate')).toBeUndefined();
    controls!.duplicateSelected();
    controls!.deleteSelected();
    expect(store.table('lines').rows.peek()).toHaveLength(120);
    expect(placeholders(host)).toHaveLength(0);
  });
});
