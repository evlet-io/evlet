// How a record's lifecycle reads (stateLook in app/meta.ts): cancelled records struck through and greyed in lists,
// with a red "Cancelled" and a soft red strip on the record; locked ones with a lock and grey text; the starting state
// as a dashed Draft pill. Decided from the State's steps (final, lock, colour) and default, never from names.
import { afterEach, describe, expect, it } from 'vitest';
import { invoice, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';
import { stateLook, type ScreenModel } from '../app/meta';
import { StatusBadge } from '../ui/doc';
import { html } from '../dom';

afterEach(stopAll);

const step = (key: string, color: string | null = null, lock = false, final = false) => ({ key, label: key, color, lock, release: false, final });
const project = (cancelled: string | null = 'rose') => ({
  name: 'project', fields: [{ name: 'stage', type: 'select', default: 'draft' }],
  state: { field: 'stage', default: 'draft', rules: [], steps: [step('draft'), step('active'), step('done', 'green', true), step('cancelled', cancelled, true, true)] },
}) as unknown as ScreenModel;

describe('stateLook', () => {
  it('reads its steps: a final rose step is cancelled, another locked or final step is locked, the default is draft', () => {
    expect(['draft', 'active', 'done', 'cancelled', '', null].map(value => stateLook(project(), value))).toEqual(['draft', 'open', 'locked', 'cancelled', null, null]);
    // Without rose a final step is only locked: the schema can't tell a cancellation from a closed record.
    expect(stateLook(project(null), 'cancelled')).toBe('locked');
    expect(stateLook(project('green'), 'cancelled')).toBe('locked');
    // A rose step that is not final (a rule leaves it) is locked, not cancelled.
    const lost = { ...project(), state: { ...project().state!, steps: [step('draft'), step('lost', 'rose', true)] } } as ScreenModel;
    expect(stateLook(lost, 'lost')).toBe('locked');
    // The default step reads as draft, wherever it is.
    expect(stateLook({ ...project(), state: { ...project().state!, default: 'active' } } as ScreenModel, 'active')).toBe('draft');
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
function schema(panel = false, recolor?: Record<string, string | null>): Recorded {
  const body = copy(invoice.schema).body;
  const model = body.models.find((item: { name: string }) => item.name === 'invoice');
  if (panel) model.open_in = 'panel';
  for (const [key, color] of Object.entries(recolor ?? {})) model.state.steps.find((item: { key: string }) => item.key === key).color = color;
  return { ...invoice.schema, body };
}
// The void invoice's journal: the void move, by sara, with its remark.
const journal: Recorded = { status: 200, etag: null, body: { items: [{ id: 9, kind: 'moved', actor: 2, at: '2026-11-09T08:00:00.000Z',
  data: { from: 'issued', to: 'void', move: 'Void' }, rule: 'void', remark: 'Customer cancelled the order' }], total: 1, limit: 50, offset: 0, users: { 2: 'sara' } } };
const routes = (panel = false, recolor?: Record<string, string | null>): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, schema(panel, recolor)],
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
    expect(row.querySelector('.ev-first .ev-state-ic[aria-label="Cancelled"] svg')).not.toBeNull();
    expect(row.querySelector('.ev-first .ev-first-v')).not.toBeNull();
    // The status badge stays the coloured pill in the list.
    expect(row.querySelector('.ev-status')!.classList.contains('pill')).toBe(true);
    const other = host.querySelector(`tr[data-key="${draft}"]`)!;
    expect(other.classList.contains('ev-row-cancelled')).toBe(false);
    expect(other.querySelector('.ev-state-ic[aria-label="Cancelled"]')).toBeNull();
  });

  it('marks a draft with a pencil and a locked record with a lock, neither struck through, keeping the icon room on every row', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector(`tr[data-key="${draft}"]`)).not.toBeNull());
    const icon = (id: string) => host.querySelector(`tr[data-key="${id}"] .ev-first > .ev-state-ic`)!;
    expect(icon(draft).getAttribute('aria-label')).toBe('Draft');
    expect(icon(draft).querySelector('svg')).not.toBeNull();
    // The issued invoice is locked.
    const issued = String(invoice.invoices.body.items.find((item: { status: string }) => item.status === 'issued').id);
    expect(icon(issued).getAttribute('aria-label')).toBe('Locked');
    expect(host.querySelector(`tr[data-key="${issued}"]`)!.classList.contains('ev-row-cancelled')).toBe(false);
    // Every row of a model with a State has the slot, so the first column lines up.
    expect([...host.querySelectorAll('tr[data-key] .ev-first')].every(cell => cell.firstElementChild?.classList.contains('ev-state-ic'))).toBe(true);
  });

  it('puts the same icons on board cards, an open state without one', async () => {
    const { StateIcon } = await import('../app/list-page');
    expect(StateIcon('open').getAttribute('aria-hidden')).toBe('true');
    expect(StateIcon('open').querySelector('svg')).toBeNull();
    expect(StateIcon('locked').getAttribute('aria-label')).toBe('Locked');
    const { BoardCard } = await import('../ui/list');
    const card = BoardCard({ title: 'Deal', mark: () => StateIcon('draft') });
    expect(card.querySelector('.t > .ev-state-ic')!.getAttribute('aria-label')).toBe('Draft');
  });

  it('follows the step colours the schema sends, not the step names', async () => {
    const { host } = start(routes(false, { void: 'slate' }), '/app/invoice');
    await until(() => expect(host.querySelector(`tr[data-key="${voided}"] .ev-state-ic[aria-label="Locked"]`)).not.toBeNull());
    expect(host.querySelector(`tr[data-key="${voided}"]`)!.classList.contains('ev-row-cancelled')).toBe(false);
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
