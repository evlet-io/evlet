// ChildTable columns fit a narrow table before it scrolls sideways (fitColumns).
import { describe, expect, it } from 'vitest';
import { fitColumns } from '../ui/childtable';

const col = (key: string, type: string, extra: { required?: boolean; calculated?: boolean } = {}) =>
  ({ key, meta: { name: key, type, label: key, required: Boolean(extra.required) } as never, calculated: Boolean(extra.calculated) });
const natural: Record<string, number> = { text: 200, decimal: 110, longtext: 260, currency: 140, date: 120 };
const options = (keep: string[] = [], sized: string[] = []) => ({
  width: (column: ReturnType<typeof col>) => natural[(column.meta as { type: string }).type],
  sized: (column: ReturnType<typeof col>) => sized.includes(column.key), keep: (column: ReturnType<typeof col>) => keep.includes(column.key) });

describe('fitColumns', () => {
  const list = [col('item', 'text'), col('qty', 'decimal'), col('notes', 'longtext'), col('amount', 'currency', { calculated: true }), col('due', 'date')];

  it('keeps natural widths when they fit or the room is unknown', () => {
    expect(fitColumns(list, 0, options()).hidden).toEqual([]);
    const wide = fitColumns(list, 2000, options());
    expect(wide.widths.get('item')).toBe(200);
  });

  it('shrinks columns toward their minimum before hiding any', () => {
    const fitted = fitColumns(list, 700, options());
    expect(fitted.hidden).toEqual([]);
    expect([...fitted.widths.values()].reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(700);
    expect(fitted.widths.get('item')!).toBeLessThan(200);
  });

  it('hides long text first, then calculated, then from the right, never kept or first columns', () => {
    const fitted = fitColumns(list, 380, options(['amount']));
    expect(fitted.hidden.map(column => column.key)).toEqual(['notes', 'due']);
    expect(fitted.shown.map(column => column.key)).toEqual(['item', 'qty', 'amount']);
  });

  it('scrolls only when even the kept columns at their minimum do not fit', () => {
    const fitted = fitColumns(list, 150, options(['qty', 'notes', 'amount', 'due']));
    expect(fitted.hidden).toEqual([]);
    expect(fitted.widths.get('item')).toBe(120);
  });

  it('leaves a column the person sized at its width', () => {
    expect(fitColumns(list, 700, options([], ['item'])).widths.get('item')).toBe(200);
  });
});
