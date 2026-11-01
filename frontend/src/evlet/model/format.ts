// Display formatting for UAE defaults: money in AED, dates DD-MM-YYYY, time in GST (UTC+4).
// Money is exact: values stay decimal strings and use the calculation engine's arithmetic.

import { parse, quantize, text } from '../../runtime/FiniteEngine';

/** Gulf Standard Time has no daylight saving, so a fixed offset is exact. */
export const GST_OFFSET_MINUTES = 4 * 60;

function group(digits: string) { return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

/**
 * An exact decimal with thousands separators, rounded half up to `scale` places:
 * formatNumber('25200.5', 2) is '25,200.50'. Returns '' for empty values.
 */
export function formatNumber(value: string | number | null | undefined, scale = 2): string {
  if (value === null || value === undefined || value === '') return '';
  let exact: string;
  try { exact = text(quantize(parse(value), scale), scale); } catch { return String(value); }
  const negative = exact.startsWith('-'), [whole, fraction] = (negative ? exact.slice(1) : exact).split('.');
  return (negative ? '-' : '') + group(whole) + (fraction ? '.' + fraction : '');
}

/** Money with its currency: formatMoney('25200') is 'AED 25,200.00'. */
export function formatMoney(value: string | number | null | undefined, options: { currency?: string; scale?: number } = {}): string {
  const number = formatNumber(value, options.scale ?? 2);
  return number ? `${options.currency ?? 'AED'} ${number}` : '';
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const SHOWN_DATE = /^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/;

/** '2026-11-01' -> '01-11-2026'. */
export function formatDate(value: string | null | undefined): string {
  const match = value ? ISO_DATE.exec(value.slice(0, 10)) : null;
  return match ? `${match[3]}-${match[2]}-${match[1]}` : '';
}

/** '1-11-2026' or '01/11/2026' -> '2026-11-01'; null when it is not a real date. */
export function parseDate(value: string): string | null {
  const match = SHOWN_DATE.exec(value.trim());
  if (!match) return ISO_DATE.test(value.trim()) && validDate(value.trim()) ? value.trim() : null;
  const iso = `${match[3]}-${match[2].padStart(2, '0')}-${match[1].padStart(2, '0')}`;
  return validDate(iso) ? iso : null;
}

function validDate(iso: string) {
  const [year, month, day] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function inGst(moment: Date) { return new Date(moment.getTime() + GST_OFFSET_MINUTES * 60000); }
const pad = (number: number) => String(number).padStart(2, '0');

/** A timestamp shown in GST: '01-11-2026 14:05'. Accepts an ISO string or a Date. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '';
  const moment = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(moment.getTime())) return '';
  const local = inGst(moment);
  return `${pad(local.getUTCDate())}-${pad(local.getUTCMonth() + 1)}-${local.getUTCFullYear()} ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`;
}

/** Today's date in GST as YYYY-MM-DD, the value today() uses in calculations. */
export function todayGst(now: Date = new Date()): string {
  const local = inGst(now);
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())}`;
}
