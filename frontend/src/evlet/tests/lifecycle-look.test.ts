// How a record's lifecycle reads (stateLook in app/meta.ts): cancelled records struck through and greyed in lists,
// with a red "Cancelled" and a soft red strip on the record; locked ones with a lock and grey text; the starting state
// as a dashed Draft pill. Decided from the schema's State (final, locked, voidable, default, colours), never names.
import { afterEach, describe, expect, it } from 'vitest';
import { invoice, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';
import { stateLook, type ScreenModel } from '../app/meta';
import { StatusBadge } from '../ui/doc';
import { html } from '../dom';

afterEach(stopAll);

const project = (colors?: Record<string, string>) => ({
  name: 'project', fields: [{ name: 'stage', type: 'select', default: 'draft', ...(colors ? { colors } : {}) }],
  state: { field: 'stage', states: ['draft', 'active', 'done', 'cancelled'], moves: [], locked: ['done', 'cancelled'], final: ['cancelled'] },
}) as unknown as ScreenModel;

describe('stateLook', () => {
  it('reads the State from the schema: final in red is cancelled, locked or final is locked, the default is draft', () => {
    const model = project({ cancelled: 'rose', done: 'green' });
    expect(['draft', 'active', 'done', 'cancelled', '', null].map(value => stateLook(model, value))).toEqual(['draft', 'open', 'locked', 'cancelled', null, null]);
    // Without a red colour a final state is only locked: the schema can't tell a cancellation from a closed record.
    expect(stateLook(project(), 'cancelled')).toBe('locked');
    // The framework's void state of a voidable State is a cancellation.
    const voidable = { ...project(), state: { ...project().state, states: ['draft', 'issued', 'void'], final: ['void'], locked: ['issued', 'void'], voidable: ['issued'] } } as unknown as ScreenModel;
    expect(stateLook(voidable, 'void')).toBe('cancelled');
    expect(stateLook({ ...project(), state: undefined } as unknown as ScreenModel, 'draft')).toBeNull();
  });

  it('draws a status badge as red text, a lock and grey text, or a dashed Draft pill', () => {
    const badge = (look: 'cancelled' | 'locked' | 'draft' | 'pill') => {
      const host = html`<div>${StatusBadge({ value: 'x', label: () => 'Label', tint: () => 'rose', look: () => look })}</div>` as HTMLElement;
      return host.firstElementChild as HTMLElement;
    };
    expect(badge('cancelled').className).toBe('ev-status ev-status-cancelled');
    expect(badge('cancelled').querySelector('svg')).not.toBeNull();
    expect(badge('locked').className).toBe('ev-status ev-status-locked');
    expect(badge('draft').className).toBe('ev-status ev-status-draft pill');
    expect(badge('pill').className).toBe('pill ev-status b-rose');
    expect(text(badge('locked'))).toBe('Label');
  });
});

const voided = String(invoice.invoice_void.body.id), draft = String(invoice.invoice_draft.body.id);
function schema(panel = false): Recorded {
  const body = copy(invoice.schema).body;
  if (panel) body.models.find((model: { name: string }) => model.name === 'invoice').open_in = 'panel';
  return { ...invoice.schema, body };
}
// The void invoice's journal: the void, by sara, with its reason.
const journal: Recorded = { status: 200, etag: null, body: { items: [{ id: 9, kind: 'voided', actor: 2, at: '2026-11-09T08:00:00.000Z',
  data: { from: 'issued', to: 'void', reason: 'Customer cancelled the order' } }], total: 1, limit: 50, offset: 0, users: { 2: 'sara' } } };
const routes = (panel = false): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, schema(panel)],
  ['GET', /\/api\/invoice\?/, invoice.invoices],
  ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
  ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
  ['GET', /\/api\/invoice\/\w+\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/\w+\/related\//, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/\w+\/journal/, journal],
  ['GET', /\/api\/invoice\/\w+\/attachments$/, invoice.invoice_attachments],
  ['GET', new RegExp(`/api/invoice/${voided}$`), invoice.invoice_void],
  ['GET', new RegExp(`/api/invoice/${draft}$`), invoice.invoice_draft],
  ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
];

describe('a cancelled record', () => {
  it('reads struck through and greyed in the list, with a ban icon before its first cell; other rows are unchanged', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector(`tr[data-key="${voided}"]`)).not.toBeNull());
    const row = host.querySelector(`tr[data-key="${voided}"]`)!;
    expect(row.classList.contains('ev-row-cancelled')).toBe(true);
    expect(row.querySelector('.ev-first .ev-cancel-ic[aria-label="Cancelled"] svg')).not.toBeNull();
    expect(row.querySelector('.ev-first .ev-first-v')).not.toBeNull();
    // The status badge stays the coloured pill in the list.
    expect(row.querySelector('.ev-status')!.classList.contains('pill')).toBe(true);
    const other = host.querySelector(`tr[data-key="${draft}"]`)!;
    expect(other.classList.contains('ev-row-cancelled')).toBe(false);
    expect(other.querySelector('.ev-cancel-ic')).toBeNull();
  });

  it('has its title struck through, red Cancelled text and a soft red strip with when, who and why', async () => {
    const { host } = start(routes(true), `/app/invoice?open=${voided}`);
    await until(() => expect(host.querySelector('.ev-panel .doc-head h1')).not.toBeNull());
    const head = host.querySelector('.ev-panel .doc-head')!;
    await until(() => expect(head.classList.contains('is-cancelled')).toBe(true));
    expect(head.querySelector('.doc-status .ev-status-cancelled')).not.toBeNull();
    // No second lock: the cancelled text says it already.
    expect(head.querySelector('.doc-lock')).toBeNull();
    const strip = () => host.querySelector('.ev-panel .inv-banner.b-rose');
    await until(() => expect(text(strip()!.querySelector(':scope > div'))).toMatch(/^Void on 09-11-2026 by sara\. Reason: Customer cancelled the order\./));
  });

  it('shows a draft as a dashed Draft pill in the header', async () => {
    const { host } = start(routes(true), `/app/invoice?open=${draft}`);
    await until(() => expect(host.querySelector('.ev-panel .doc-head .doc-status .ev-status-draft')).not.toBeNull());
    expect(host.querySelector('.ev-panel .doc-head')!.classList.contains('is-cancelled')).toBe(false);
  });
});
