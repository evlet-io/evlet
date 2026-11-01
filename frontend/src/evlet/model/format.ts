// Display formatting for UAE defaults: money in AED, dates DD-MM-YYYY, time in GST (UTC+4).
// Money is exact: values are decimal strings (as the API sends them) or safe integers, and use
// the calculation engine's arithmetic. Anything else shows a placeholder instead of a guess.

import { parse, quantize, text } from '../../runtime/FiniteEngine';

/** Gulf Standard Time (Asia/Dubai) has no daylight saving, so a fixed offset is exact. */
export const GST_OFFSET_MINUTES = 4 * 60;
export const INVALID_AMOUNT = 'Invalid amount';
export const INVALID_DATE = 'Invalid date';

const DECIMAL = /^-?\d+(\.\d+)?$/;

function group(digits: string) { return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

/**
 * An exact decimal with thousands separators, rounded half up to `scale` places:
 * formatNumber('25200.5', 2) is '25,200.50'. Takes a decimal string or a safe integer; a
 * JavaScript fraction such as 0.1 + 0.2 is not exact and shows 'Invalid amount'.
 * Returns '' for empty values.
 */
export function formatNumber(value: string | number | null | undefined, scale = 2): string {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'number' ? !Number.isSafeInteger(value) : typeof value !== 'string' || !DECIMAL.test(value)) return INVALID_AMOUNT;
  let exact: string;
  try { exact = text(quantize(parse(value), scale), scale); } catch { return INVALID_AMOUNT; }
  const negative = exact.startsWith('-'), [whole, fraction] = (negative ? exact.slice(1) : exact).split('.');
  // Rounding can reach zero ('-0.004' -> '0.00'): zero has no sign.
  const zero = /^[0.]+$/.test(whole + (fraction ?? ''));
  return (negative && !zero ? '-' : '') + group(whole) + (fraction ? '.' + fraction : '');
}

/** Money with its currency: formatMoney('25200') is 'AED 25,200.00'. */
export function formatMoney(value: string | number | null | undefined, options: { currency?: string; scale?: number } = {}): string {
  const number = formatNumber(value, options.scale ?? 2);
  return number && number !== INVALID_AMOUNT ? `${options.currency ?? 'AED'} ${number}` : number;
}

const pad = (number: number) => String(number).padStart(2, '0');

function validDate(year: number, month: number, day: number) {
  const date = new Date(Date.UTC(year, month - 1, day));
  return year >= 1 && date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

// YYYY-MM-DD with an optional time and zone. A time with a zone (Z or +hh:mm) is converted to
// GST; a time without a zone is taken to be GST already (Evlet stores local GST wall time when
// no zone is given). The browser's own time zone is never used.
const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

interface Parts { year: number; month: number; day: number; hour: number; minute: number }

function partsInGst(value: string): Parts | null {
  const match = TIMESTAMP.exec(value.trim());
  if (!match) return null;
  const [year, month, day, hour, minute, second] = [1, 2, 3, 4, 5, 6].map(index => Number(match[index] ?? 0));
  if (!validDate(year, month, day) || hour > 23 || minute > 59 || second > 59) return null;
  const zone = match[7];
  if (!zone) return { year, month, day, hour, minute };
  const offset = zone === 'Z' ? 0 : (zone[0] === '-' ? -1 : 1) * (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(-2)));
  const moment = new Date(Date.UTC(year, month - 1, day, hour, minute) + (GST_OFFSET_MINUTES - offset) * 60000);
  return { year: moment.getUTCFullYear(), month: moment.getUTCMonth() + 1, day: moment.getUTCDate(), hour: moment.getUTCHours(), minute: moment.getUTCMinutes() };
}

/** '2026-11-01' -> '01-11-2026'. A timestamp shows its GST date. Not a real date: 'Invalid date'. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '';
  const parts = partsInGst(value);
  return parts ? `${pad(parts.day)}-${pad(parts.month)}-${parts.year}` : INVALID_DATE;
}

/** '1-11-2026' or '01/11/2026' (or '2026-11-01') -> '2026-11-01'; null when it is not a real date. */
export function parseDate(value: string): string | null {
  const text = value.trim();
  const shown = /^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/.exec(text), iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  const [day, month, year] = shown ? [shown[1], shown[2], shown[3]].map(Number) : iso ? [iso[3], iso[2], iso[1]].map(Number) : [0, 0, 0];
  return validDate(year, month, day) ? `${year}-${pad(month)}-${pad(day)}` : null;
}

/** A timestamp shown in GST: '01-11-2026 14:05'. Accepts an ISO string or a Date. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '';
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return INVALID_DATE;
    value = value.toISOString();
  }
  const parts = partsInGst(value);
  return parts ? `${pad(parts.day)}-${pad(parts.month)}-${parts.year} ${pad(parts.hour)}:${pad(parts.minute)}` : INVALID_DATE;
}

/** Today's date in GST as YYYY-MM-DD, the value today() uses in calculations. */
export function todayGst(now: Date = new Date()): string {
  const local = new Date(now.getTime() + GST_OFFSET_MINUTES * 60000);
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())}`;
}
