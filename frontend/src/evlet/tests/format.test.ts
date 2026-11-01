import { describe, expect, it } from 'vitest';
import { formatDate, formatDateTime, formatMoney, formatNumber, parseDate, todayGst } from '../model/format';

describe('formatting', () => {
  it('formats money exactly in AED', () => {
    expect(formatMoney('25200')).toBe('AED 25,200.00');
    expect(formatMoney('1234567.875')).toBe('AED 1,234,567.88');
    expect(formatMoney('-0.005')).toBe('AED -0.01');
    expect(formatMoney('12345678901234567890.10')).toBe('AED 12,345,678,901,234,567,890.10');
    expect(formatMoney('99', { currency: 'USD', scale: 0 })).toBe('USD 99');
    expect(formatMoney(null)).toBe('');
    expect(formatNumber('5.5', 3)).toBe('5.500');
    expect(formatNumber('not a number')).toBe('not a number');
  });

  it('shows dates as DD-MM-YYYY and reads them back', () => {
    expect(formatDate('2026-11-01')).toBe('01-11-2026');
    expect(formatDate('2026-11-01T10:00:00Z')).toBe('01-11-2026');
    expect(formatDate(null)).toBe('');
    expect(parseDate('1-11-2026')).toBe('2026-11-01');
    expect(parseDate('01/11/2026')).toBe('2026-11-01');
    expect(parseDate('2026-11-01')).toBe('2026-11-01');
    expect(parseDate('31-02-2026')).toBeNull();
    expect(parseDate('soon')).toBeNull();
  });

  it('uses Gulf Standard Time for timestamps and today', () => {
    expect(formatDateTime('2026-11-01T20:30:00Z')).toBe('02-11-2026 00:30');
    expect(formatDateTime('nonsense')).toBe('');
    expect(todayGst(new Date('2026-10-31T19:59:00Z'))).toBe('2026-10-31');
    expect(todayGst(new Date('2026-10-31T20:00:00Z'))).toBe('2026-11-01');
  });
});
