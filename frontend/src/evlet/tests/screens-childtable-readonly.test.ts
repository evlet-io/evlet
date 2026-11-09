// A child table that is read only on the record page stays read only when it is expanded: in focus
// view and in the row editor. Replays the recorded invoice (a draft, and the same invoice issued and
// locked by its State).
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, start, stopAll, text, until } from './screens-helpers';
import { copy, type Recorded } from './server';

afterEach(stopAll);

const id = invoice.invoice_draft.body.id;
const detailPath = new RegExp(`/api/invoice/${id}$`);

type Setup = { detail?: Recorded; schema?: Recorded };

async function openLines({ detail = invoice.invoice_draft, schema = invoice.schema }: Setup = {}) {
  const { host } = start([
    ['GET', /\/api\/auth\/session$/, invoice.session],
    ['GET', /\/api\/schema$/, schema],
    ['GET', detailPath, detail],
    ['GET', /\/api\/invoice\/[0-9a-z]{13}\/(measures|related|journal|attachments)/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }],
    ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
  ], `/app/invoice/${id}`);
  // The Lines table is in the record's primary area.
  await until(() => expect(host.querySelectorAll('.grid tbody tr[data-key]')).toHaveLength(2));
  return host;
}

const grid = (host: Element) => host.querySelector<HTMLElement>('.grid')!;
const enterFocus = (host: Element) => {
  click(byText(grid(host), '.g-bar button', /Focus/));
  expect(grid(host).classList.contains('max')).toBe(true);
};
const openRow = (host: Element) => {
  click(grid(host).querySelector('tbody tr[data-key] .g-exp')!);
  const panel = grid(host).querySelector<HTMLElement>('.g-ed-main');
  expect(panel).not.toBeNull();
  return panel!;
};
const buttons = (root: Element) => [...root.querySelectorAll('button')].map(text);
/** The row editor's value controls: inputs, and the buttons that open a choice or a link. */
const controls = (panel: Element, field: string) =>
  [...panel.querySelectorAll<HTMLInputElement | HTMLButtonElement>(`.fld[data-field="${field}"] :is(input, textarea, select, button.input)`)];
const cellOf = (host: Element, col: string) => grid(host).querySelector<HTMLElement>(`tbody tr[data-key] td[data-col="${col}"]`)!;

/** Everything the expanded views show for a read-only table: no add, move or remove, no editable value. */
function expectReadOnly(host: Element) {
  for (const focus of [false, true]) {
    if (focus) enterFocus(host);
    expect(grid(host).querySelector('.g-add')).toBeNull();
    expect(cellOf(host, 'qty').getAttribute('aria-readonly')).toBe('true');
    click(cellOf(host, 'qty'));
    expect(grid(host).querySelector('td.editing')).toBeNull();
    // Rows still open, to read them.
    const panel = openRow(host);
    expect(text(panel.querySelector('.g-ed-pos'))).toBe('1 of 2');
    for (const label of ['Delete line', 'Insert below']) expect(buttons(panel)).not.toContain(label);
    for (const field of ['item', 'qty', 'rate']) {
      expect(controls(panel, field).length).toBeGreaterThan(0);
      expect(controls(panel, field).every(control => control.disabled)).toBe(true);
    }
    click(byText(panel, 'button', 'Done'));
  }
}

describe('a read-only child table, expanded', () => {
  it('stays read only in focus view and the row editor when a State locks the record', async () => {
    const host = await openLines({ detail: invoice.invoice_issued_detail });
    expect(text(grid(host).querySelector('.g-lock'))).toContain('Locked.');
    expectReadOnly(host);
    // The row editor says why, as the form does.
    expect(text(openRow(host).querySelector('.fld[data-field="qty"] label'))).toContain('locked');
  });

  it('stays read only for a record the server reports locked, as a cancelled one is', async () => {
    const cancelled = copy(invoice.invoice_draft);
    cancelled.body._locked = true;
    const host = await openLines({ detail: cancelled });
    expectReadOnly(host);
  });

  it('stays read only without write permission on the record', async () => {
    const schema = copy(invoice.schema);
    schema.body.models.find((model: { name: string }) => model.name === 'invoice').permissions.update = false;
    const host = await openLines({ schema });
    expectReadOnly(host);
  });

  it('keeps a read-only line field read only in the row editor, and the rest editable', async () => {
    const schema = copy(invoice.schema);
    const lines = schema.body.models.find((model: { name: string }) => model.name === 'invoice_lines');
    lines.fields.find((field: { name: string }) => field.name === 'qty').read_only = true;
    const host = await openLines({ schema });
    enterFocus(host);
    expect(cellOf(host, 'qty').getAttribute('aria-readonly')).toBe('true');
    const panel = openRow(host);
    expect(controls(panel, 'qty').every(control => control.disabled)).toBe(true);
    expect(controls(panel, 'rate').every(control => !control.disabled)).toBe(true);
    expect(buttons(panel)).toEqual(expect.arrayContaining(['Delete line', 'Insert below']));
  });

  it('stays editable in focus view and the row editor when the record is editable', async () => {
    const host = await openLines();
    enterFocus(host);
    expect(grid(host).querySelector('.g-add')).not.toBeNull();
    const panel = openRow(host);
    expect(buttons(panel)).toEqual(expect.arrayContaining(['Delete line', 'Insert below', 'Done']));
    for (const field of ['item', 'qty', 'rate']) expect(controls(panel, field).every(control => !control.disabled)).toBe(true);
    const qty = controls(panel, 'qty')[0] as HTMLInputElement;
    qty.value = '5';
    qty.dispatchEvent(new Event('input', { bubbles: true }));
    qty.dispatchEvent(new Event('change', { bubbles: true }));
    qty.dispatchEvent(new FocusEvent('blur'));
    await until(() => expect(text(cellOf(host, 'qty'))).toContain('5'));
  });
});
