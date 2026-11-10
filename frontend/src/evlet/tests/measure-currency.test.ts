// Measure values in the currency the measures route names (docs/measures.md#currencies): a count has none, a sum of
// a field whose currency comes from each record takes the route's code, and without one it is a plain amount.
import { describe, expect, it } from 'vitest';
import { measureText, type MeasureSchema, type ScreenModel } from '../app/meta';
import { SchemaIndex, type AppSchema } from '../model/schema';

const contract = {
  name: 'contract', label: 'Contract', plural: 'Contracts',
  fields: [
    { name: 'fee', label: 'Fee', type: 'currency', scale: 2, currency_source: { field: 'currency_code' } },
    { name: 'list_price', label: 'List price', type: 'currency', scale: 2, currency: 'USD' },
  ],
} as unknown as ScreenModel;
const customer = { name: 'customer', label: 'Customer', plural: 'Customers', fields: [] } as unknown as ScreenModel;
const index = new SchemaIndex({ models: [contract, customer] } as unknown as AppSchema);
const measure = (name: string, fn: string, field: string | null) =>
  ({ name, function: fn, field, per_record: true, model: 'contract', link: 'customer' }) as MeasureSchema;

describe('measure currencies', () => {
  it('shows a count without a currency', () => {
    expect(measureText(index, customer, measure('contracts', 'count', null), 3, { contracts: 'SAR' })).toBe('3');
  });

  it('shows a sum of a per-record currency in the code the route names', () => {
    const text = measureText(index, customer, measure('fees', 'sum', 'fee'), '150.00', { fees: 'SAR' });
    expect(text).toContain('150.00');
    expect(text).toMatch(/SAR|﷼|ر\.س/);
    expect(text).not.toContain('AED');
  });

  it('shows a plain amount when the rows are in more than one currency', () => {
    expect(measureText(index, customer, measure('fees', 'sum', 'fee'), '150.00', {})).toBe('150.00');
  });

  it('keeps a fixed currency as declared', () => {
    expect(measureText(index, customer, measure('listed', 'sum', 'list_price'), '9.50', {})).toContain('9.50');
    expect(measureText(index, customer, measure('listed', 'sum', 'list_price'), '9.50', {})).not.toBe('9.50');
  });
});
