// The side panel's Related block: ordering, leaving out empty relations, the five-row limit with Show all,
// the name and count as separate elements, and accessible names.
import { describe, expect, it } from 'vitest';
import { cell } from '../reactive';
import { RELATED_LIMIT, RelatedPanel, arrangeRelated, type RelatedItem } from '../app/related-panel';

const item = (label: string, count: number | null): RelatedItem & { set: (n: number | null) => void } => {
  const c = cell<number | null>(count);
  return { key: label, label, href: `/m/x/1?tab=related#${label}`, icon: 'users', tint: 'blue', count: c, set: n => c.set(n) };
};
const mount = (items: RelatedItem[], expanded = cell(false), opened: string[] = []) => {
  const host = document.createElement('div');
  const out = RelatedPanel({ items, expanded, onOpen: it => opened.push(it.key) });
  host.append(...(Array.isArray(out) ? out : [out]) as Node[]);
  document.body.append(host);
  return host;
};
const names = (host: Element) => [...host.querySelectorAll('.rel-row .rel-nm')].map(n => n.textContent);

describe('related panel', () => {
  it('orders by count high to low, then label; loading rows follow; empty ones are kept apart', () => {
    const { rows, empty } = arrangeRelated([item('Zeta', 2), item('Alpha', 2), item('Big', 9), item('Wait', null), item('Nada', 0), item('Also', 0)]);
    expect(rows.map(r => r.label)).toEqual(['Big', 'Alpha', 'Zeta', 'Wait']);
    expect(empty.map(r => r.label)).toEqual(['Also', 'Nada']);
  });

  it('lists only relations with records: empty ones stay in the Related tab', () => {
    const host = mount([item('Employees', 0), item('Departments', 0), item('Tasks', 3)]);
    expect(names(host)).toEqual(['Tasks']);
    expect(host.textContent).not.toContain('Employees');
    expect(host.textContent).not.toContain('No records');
  });

  it('shows five rows, then Show all expands in place and reports aria-expanded', () => {
    const items = Array.from({ length: 8 }, (_, i) => item(`R${i}`, 10 - i));
    const expanded = cell(false);
    const host = mount(items, expanded);
    expect(names(host)).toHaveLength(RELATED_LIMIT);
    const button = host.querySelector<HTMLButtonElement>('.rel-all')!;
    expect(button.textContent).toBe('Show all (8)');
    expect(button.getAttribute('aria-expanded')).toBe('false');
    button.click();
    expect(names(host)).toHaveLength(8);
    expect(host.querySelector('.rel-all')!.getAttribute('aria-expanded')).toBe('true');
    expect(expanded.get()).toBe(true);
  });

  it('has no Show all for five rows or fewer', () => {
    expect(mount([item('A', 1), item('B', 2)]).querySelector('.rel-all')).toBeNull();
  });

  it('keeps the name and count in separate elements and puts both in the accessible name', () => {
    const host = mount([item('Employees', 3), item('Payments', 1), item('Loading', null)]);
    const rows = [...host.querySelectorAll<HTMLAnchorElement>('ul.rel-list > li > a.rel-row')];
    expect(rows.map(r => r.getAttribute('aria-label'))).toEqual(['Employees, 3 records', 'Payments, 1 record', 'Loading']);
    expect(rows[0].querySelector('.rel-nm')!.textContent).toBe('Employees');
    expect(rows[0].querySelector('.rel-ct')!.textContent).toBe('3');
    expect(rows[0].querySelector('.rel-nm')).not.toBe(rows[0].querySelector('.rel-ct'));
    expect(rows[0].getAttribute('href')).toContain('tab=related');
  });

  it('counts in the relation\'s own noun when it has one', () => {
    const host = mount([{ ...item('Invoices', 1), noun: { one: 'invoice', many: 'invoices' } }, { ...item('Payments', 2), noun: { one: 'payment', many: 'payments' } }]);
    expect([...host.querySelectorAll('a.rel-row')].map(r => r.getAttribute('aria-label'))).toEqual(['Payments, 2 payments', 'Invoices, 1 invoice']);
  });

  it('re-sorts when a count arrives', () => {
    const a = item('A', null), b = item('B', 1);
    const host = mount([a, b]);
    expect(names(host)).toEqual(['B', 'A']);
    a.set(5);
    expect(names(host)).toEqual(['A', 'B']);
  });
});
