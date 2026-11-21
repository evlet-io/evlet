import { describe, expect, it } from 'vitest';
import { formatDate, formatDateTime, formatMoney, formatNumber, formatTrimmed, parseDate, plainNumber, setRegion, todayGst } from '../model/format';
import { kindOf, parseDateTime } from '../ui/kinds';

describe('formatting', () => {
  it('shows an Integer with grouping=False, such as a year, without thousands separators', () => {
    const integer = kindOf('integer');
    expect(integer.format(2023, { name: 'year_made', type: 'integer', grouping: false } as never)).toBe('2023');
    expect(integer.format(2023, { name: 'count', type: 'integer' } as never)).toBe('2,023');
  });

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
    // Years 1-99 stay as typed: Date.UTC would read them as 1900-1999.
    expect(formatDate('0050-01-01')).toBe('01-01-0050');
    expect(parseDate('01-01-0050')).toBe('0050-01-01');
    expect(formatDate('0026-02-29')).not.toBe('29-02-0026');
    expect(parseDate('01/11/2026')).toBe('2026-11-01');
    expect(parseDate('2026-11-01')).toBe('2026-11-01');
    expect(parseDate('31-02-2026')).toBeNull();
    expect(parseDate('soon')).toBeNull();
  });

  it('shows a Decimal(display_scale=) with at most that many places and no trailing zeros, never changing its value', () => {
    const counter = { name: 'credited_qty', type: 'decimal', label: 'Credited', required: false, read_only: false, scale: 6, display_scale: 6 };
    expect(formatTrimmed('0.000000', 6)).toBe('0');
    expect(formatTrimmed('1.500000', 6)).toBe('1.5');
    expect(formatTrimmed('1234.250000', 6)).toBe('1,234.25');
    expect(formatTrimmed('-0.000001', 2)).toBe('0');
    expect(formatTrimmed('2.345', 2)).toBe('2.35');
    expect(kindOf('decimal').format('0.000000', counter)).toBe('0');
    expect(kindOf('decimal').format('1.500000', counter)).toBe('1.5');
    expect(kindOf('decimal').format('1.123456', { ...counter, display_scale: 2 })).toBe('1.12');
    // The input shows every place the value holds, without the trailing zeros; what it parses is stored at scale 6.
    expect(kindOf('decimal').edit!('1.500000', counter)).toBe('1.5');
    expect(kindOf('decimal').parse!('1.5', counter)).toBe('1.500000');
    // Without display_scale the value shows at its scale, as before.
    expect(kindOf('decimal').format('0.000000', { ...counter, display_scale: undefined })).toBe('0.000000');
    try {
      setRegion({ number: '1.234,56' });
      expect(formatTrimmed('1234.500000', 6)).toBe('1.234,5');
      expect(formatTrimmed('1000', 6)).toBe('1.000');
    } finally { setRegion({}); }
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
