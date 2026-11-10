// How a measure's value reads (measureText in app/meta.ts): a count is a plain number, never money, and money whose
// code each record takes from its own fields totals without a symbol, since no one code covers many records.
import { describe, expect, it } from 'vitest';
import { measureText, type MeasureSchema, type ScreenModel } from '../app/meta';
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
});
