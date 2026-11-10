// How a measure's value reads (measureText in app/meta.ts): a count is a plain number, never money, and money whose
// code each record takes from its own fields totals without a symbol, since no one code covers many records.
import { describe, expect, it } from 'vitest';
import { displayOf, measureText, type MeasureSchema, type ScreenModel } from '../app/meta';
import { SchemaIndex, type AppSchema } from '../model/schema';

const field = (name: string, type: string, extra: Record<string, unknown> = {}) => ({ name, type, label: name, required: false, ...extra });
const visit = { name: 'visit', label: 'Visit', fields: [field('amount', 'currency', { scale: 2, currency_source: { field: 'currency_code', link: 'customer', model: 'customer' } }),
  field('fee', 'currency', { scale: 2, currency: 'AED' }), field('customer', 'link')] };
const index = new SchemaIndex({ models: [visit], currency: 'AED' } as unknown as AppSchema);
const model = index.schema.models[0] as unknown as ScreenModel;
const measure = (fn: string, name: string | null): MeasureSchema => ({ name: 'm', function: fn, field: name, model: null, link: null, per_record: true, where: null } as unknown as MeasureSchema);

describe('measureText', () => {
  it('reads a count as a plain number, even a count of a money or link field', () => {
    expect(measureText(index, model, measure('count', null), 1200)).toBe('1,200');
    expect(measureText(index, model, measure('count_distinct', 'customer'), 3)).toBe('3');
  });

  it('totals money with a code from each record without a symbol, and keeps a fixed code', () => {
    expect(measureText(index, model, measure('sum', 'amount'), '4117.05')).toBe('4,117.05');
    expect(measureText(index, model, measure('sum', 'fee'), '10.5')).toContain('10.50');
    expect(measureText(index, model, measure('sum', 'fee'), '10.5')).not.toBe('10.50');
  });

  it('never shows money at the schema scale (the storage scale, 6) when it has no one code', () => {
    // As /api/schema sends a Currency since decimal places per currency: scale 6, the code from each record.
    const stored = { name: 'visit', label: 'Visit', fields: [field('amount', 'currency', { scale: 6, currency_source: { field: 'currency_code', link: 'customer', model: 'customer' } }),
      field('loose', 'currency', { scale: 6 })] };
    const wide = new SchemaIndex({ models: [stored], currency: 'AED' } as unknown as AppSchema);
    const visits = wide.schema.models[0] as unknown as ScreenModel;
    expect(measureText(wide, visits, measure('sum', 'amount'), '0.00')).toBe('0.00');
    expect(measureText(wide, visits, measure('sum', 'amount'), '1250.500')).toBe('1,250.500');
    expect(measureText(wide, visits, measure('sum', 'amount'), 7)).toBe('7.00');
    // With the route's currencies (docs/measures.md): its code first, then the plain amount at the sent places.
    expect(measureText(wide, visits, measure('sum', 'amount'), '0.00', { m: 'SAR' })).toMatch(/0\.00$/);
    expect(measureText(wide, visits, measure('sum', 'amount'), '0.00', { m: 'SAR' })).not.toMatch(/0\.000/);
    expect(measureText(wide, visits, measure('sum', 'amount'), '1.234', { m: 'KWD' })).toContain('KWD');
    expect(measureText(wide, visits, measure('sum', 'amount'), '0.00', {})).toBe('0.00');
    expect(measureText(wide, visits, measure('count', null), 4, { m: 'SAR' })).toBe('4');
    // A Currency with no code at all shows 2 places, not 6.
    expect(String(displayOf(stored.fields[1] as any, '0.00'))).toMatch(/(^|\s)0\.00$/);
  });
});
