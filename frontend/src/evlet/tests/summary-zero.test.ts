// A collapsed "More information" row summarises its fields on one line (record-compact.ts, fieldsSummary). Only an
// empty value (null or '') reads "Not set": a paid invoice's outstanding 0.00, a zero quantity and No are values.
import { describe, expect, it } from 'vitest';
import { fieldsSummary } from '../app/record-compact';
import { localField } from '../ui/fields';

describe('a collapsed section summary', () => {
  it('shows zero and false as values, and only empty values as Not set', () => {
    const fields = [
      localField({ name: 'outstanding', type: 'currency', label: 'Outstanding', currency: 'AED', compute: { $expr: 'literal' } } as any, '0.00'),
      localField({ name: 'qty', type: 'decimal', label: 'Qty', scale: 2, default: '0' } as any, '0.00'),
      localField({ name: 'paid', type: 'boolean', label: 'Paid' }, false),
      localField({ name: 'note', type: 'text', label: 'Note' }, ''),
      localField({ name: 'due', type: 'date', label: 'Due' }, null),
    ];
    const summary = fieldsSummary(fields, null);
    expect(summary).toContain('Outstanding: AED 0.00');
    expect(summary).toContain('Qty: 0.00');
    expect(summary).toContain('Paid: No');
    expect(summary).toContain('Note: Not set');
    expect(summary).toContain('Due: Not set');
    expect(summary).not.toMatch(/Outstanding: Not set|Qty: Not set|Paid: Not set/);
  });
});
