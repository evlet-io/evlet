import { describe, expect, it } from 'vitest';
import { formatDate, formatDateTime, formatMoney, formatNumber, parseDate, plainNumber, setRegion, todayGst } from '../model/format';
import { kindOf, parseDateTime } from '../ui/kinds';

describe('formatting', () => {
  it('formats money exactly in AED', () => {
    expect(formatMoney('25200')).toBe('AED 25,200.00');
    expect(formatMoney('1234567.875')).toBe('AED 1,234,567.88');
    expect(formatMoney('-0.005')).toBe('AED -0.01');
    expect(formatMoney('12345678901234567890.10')).toBe('AED 12,345,678,901,234,567,890.10');
    expect(formatMoney('99', { currency: 'USD', scale: 0 })).toBe('USD 99');
    expect(formatMoney(null)).toBe('');
    expect(formatNumber('5.5', 3)).toBe('5.500');
    expect(formatNumber('not a number')).toBe('Invalid amount');
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

  it("follows the person's region for dates, numbers and the time zone", () => {
    try {
      setRegion({ date: 'd MMM yyyy', number: '1.234,56', zone: 'Asia/Kolkata' });
      expect(formatDate('2026-11-01')).toBe('1 Nov 2026');
      expect(parseDate('1 Nov 2026')).toBe('2026-11-01');
      expect(formatMoney('25200.5')).toBe('AED 25.200,50');
      expect(plainNumber('25.200,5')).toBe('25200.5');
      // The other decimal mark is refused, not dropped.
      expect(plainNumber('12.50')).toBeNull();
      expect(() => kindOf('decimal').parse!('12.50', { name: 'x', type: 'decimal', label: 'X', required: false, read_only: false, scale: 2 })).toThrow('Use , for decimals');
      expect(formatDateTime('2026-11-01T20:30:00Z')).toBe('2 Nov 2026 02:00');
      expect(kindOf('decimal').parse!('1.234,5', { name: 'x', type: 'decimal', label: 'X', required: false, read_only: false, scale: 2 })).toBe('1234.50');
      expect(parseDateTime('2 Nov 2026 02:00')).toBe('2026-11-01T20:30:00Z');
      setRegion({ date: 'MM/dd/yyyy', number: '1 234.56', zone: 'Europe/London' });
      expect(parseDate('11/02/2026')).toBe('2026-11-02');
      expect(formatNumber('1234567.5', 1)).toBe('1 234 567.5');
      expect(plainNumber('12,50')).toBeNull();
      setRegion({});
      expect(plainNumber('1,234.5')).toBe('1234.5');
      expect(plainNumber('12,50')).toBeNull();
      setRegion({ date: 'MM/dd/yyyy', number: '1 234.56', zone: 'Europe/London' });
      expect(formatDateTime('2026-07-01T12:00:00Z')).toBe('07/01/2026 13:00');
      expect(formatDateTime('2026-12-01T12:00:00Z')).toBe('12/01/2026 12:00');
      setRegion({ zone: 'Not/AZone' });
      expect(formatDateTime('2026-11-01T20:30:00Z')).toBe('02-11-2026 00:30');
    } finally { setRegion({}); }
  });

  it('uses Gulf Standard Time for timestamps and today', () => {
    expect(formatDateTime('2026-11-01T20:30:00Z')).toBe('02-11-2026 00:30');
    expect(formatDateTime('nonsense')).toBe('Invalid date');
    expect(todayGst(new Date('2026-10-31T19:59:00Z'))).toBe('2026-10-31');
    expect(todayGst(new Date('2026-10-31T20:00:00Z'))).toBe('2026-11-01');
  });
});
