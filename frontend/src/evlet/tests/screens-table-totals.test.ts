// Table(totals=...): the declared fields of the record under its table, as a stack in the declared order, over the
// responses recorded for the Bill model and its Quick bill prompt (totals_* in screens-fixture.json).
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { change } from './ui-helpers';
import { sections, type ScreenModel } from '../app/meta';

afterEach(stopAll);

const bill = screen.totals_bill.body.id as string, empty = screen.totals_empty.body.id as string;
const routes: Route[] = [
  ['GET', /\/api\/auth\/session$/, screen.totals_session],
  ['GET', /\/api\/schema$/, screen.totals_schema],
  ['GET', new RegExp(`/api/bill/${bill}$`), screen.totals_bill],
  ['GET', new RegExp(`/api/bill/${empty}$`), screen.totals_empty],
  ['POST', new RegExp(`/api/bill/${bill}/actions/quick_bill$`), screen.totals_prompt],
];
/** The stack's shown lines as [label, value] ("" for a line edited in place), and whether the last is emphasized. */
const lines = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('.g-totals .g-tl')].filter(line => !line.hidden)
  .map(line => [text(line.querySelector('span')), text(line.querySelector('b'))]);
const line = (root: ParentNode, name: string) => root.querySelector<HTMLElement>(`.g-totals .g-tl[data-field="${name}"]`)!;
const switchOf = (host: Element, name: string) => host.querySelector<HTMLElement>(`[data-field="${name}"] button[role=switch]`)!;

async function open(id = bill) {
  const started = start(routes, `/app/bill/${id}`);
  await until(() => expect(started.host.querySelector('[data-table-block="lines"]')).not.toBeNull());
  return started.host;
}

describe('declared totals under a table', () => {
  it('shows the declared fields in order, the last emphasized, and leaves them out of the form', async () => {
    const host = await open();
    await until(() => expect(host.querySelector('.g-totals')).not.toBeNull());
    // Freight applies only once shipped; the discount is edited in its line.
    expect(lines(host)).toEqual([['Net total', 'AED 250.00'], ['Discount %', ''], ['Discount', 'AED 0.00'], ['Grand total', 'AED 250.00']]);
    expect(line(host, 'discount_percent').querySelector('input')).not.toBeNull();
    expect(line(host, 'grand_total').classList.contains('gt')).toBe(true);
    expect(host.querySelectorAll('.g-sum')).toHaveLength(0);
    // Each shows once: not among the record's fields, not under Other.
    for (const name of ['net_total', 'discount_percent', 'discount', 'freight', 'grand_total'])
      expect(host.querySelectorAll(`[data-field="${name}"]:not(.g-tl)`)).toHaveLength(0);
    expect(host.querySelector('.ev-more-row[data-section="Other"]')).toBeNull();
    // The total line under the columns stays as it is.
    expect(text(host.querySelector('tfoot'))).toContain('AED 250.00');
  });

  it('recalculates the lines below at once as the discount is typed', async () => {
    const host = await open();
    await until(() => expect(line(host, 'discount_percent')?.querySelector('input')).toBeTruthy());
    change(line(host, 'discount_percent').querySelector('input')!, '10');
    await until(() => expect(lines(host).slice(2)).toEqual([['Discount', 'AED 25.00'], ['Grand total', 'AED 225.00']]));
  });

  it('adds and drops a line as its applies_when turns', async () => {
    // A new bill: nothing is saved while the switches change. Its first row brings the totals.
    const { host } = start(routes, '/app/bill/new');
    await until(() => expect(host.querySelector('[data-table-block="lines"] .g-addrow')).not.toBeNull());
    click(host.querySelector('[data-table-block="lines"] .g-addrow'));
    await until(() => expect(host.querySelector('.g-totals')).not.toBeNull());
    expect(line(host, 'freight').hidden).toBe(true);
    click(switchOf(host, 'shipped'));
    await until(() => expect(line(host, 'freight').hidden).toBe(false));
    expect(lines(host).map(([label]) => label)).toEqual(['Net total', 'Discount %', 'Discount', 'Freight', 'Grand total']);
    click(switchOf(host, 'shipped'));
    await until(() => expect(line(host, 'freight').hidden).toBe(true));
  });

  it('shows a locked field as a read-only line', async () => {
    const host = await open();
    await until(() => expect(line(host, 'discount_percent')?.querySelector('input')).toBeTruthy());
    click(switchOf(host, 'approved'));
    await until(() => expect(line(host, 'discount_percent').querySelector('input')).toBeNull());
    expect(text(line(host, 'discount_percent').querySelector('b'))).toBe('0.00%');
  });

  it('shows no totals while the table has no rows', async () => {
    const host = await open(empty);
    expect(host.querySelector('.g-totals')).toBeNull();
  });

  it('shows the same stack under a prompt table', async () => {
    const host = await open();
    click(byText(host, '.doc-head button', 'Quick bill'));
    await until(() => expect(document.querySelector('.modal.prompt .prompt-table')).not.toBeNull());
    const sheet = document.querySelector<HTMLElement>('.modal.prompt')!;
    expect(sheet.querySelector('.g-totals')).toBeNull();
    click(sheet.querySelector('.prompt-table .g-addrow'));
    await until(() => expect(sheet.querySelector('.g-totals')).not.toBeNull());
    expect(lines(sheet).map(([label]) => label)).toEqual(['Net total', 'Discount %', 'Grand total']);
    expect(line(sheet, 'grand_total').classList.contains('gt')).toBe(true);
    expect(sheet.querySelectorAll('.ff[data-field="discount_percent"], .fld[data-field="discount_percent"]')).toHaveLength(0);
  });

  it('places the totals fields as usual for a role that cannot read the table', () => {
    const bill = (screen.totals_schema.body.models as ScreenModel[]).find(model => model.name === 'bill')!;
    const placed = (model: ScreenModel) => sections(model).map(section => [section.label, section.fields.map(field => field.name)]);
    // A block that names a totals field keeps it for that role; the browser drops it while the table shows.
    const named = { ...bill, layout: [...bill.layout!, { label: 'Money', fields: ['discount_percent'] }] } as ScreenModel;
    expect(placed(named)).toEqual([['Details', ['title', 'shipped', 'approved']]]);
    // Without the table (the role cannot read its rows), the field shows where the block puts it, the rest under Other.
    expect(placed({ ...named, children: [] })).toEqual([['Details', ['title', 'shipped', 'approved']], ['Money', ['discount_percent']],
      ['Other', ['net_total', 'discount', 'freight', 'grand_total']]]);
  });
});
