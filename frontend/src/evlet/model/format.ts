// Display formatting, with UAE defaults: money in AED, dates DD-MM-YYYY, time in GST (UTC+4).
// Each person's region preferences (Settings, Appearance & region) change the date format, number
// separators and time zone of every list and form through setRegion; screens never format on their own.
// Money is exact: values are decimal strings (as the API sends them) or safe integers, and use
// the calculation engine's arithmetic. Anything else shows a placeholder instead of a guess.

import { parse, quantize, text } from './finite';

/** Gulf Standard Time (Asia/Dubai) has no daylight saving, so a fixed offset is exact. */
export const GST_OFFSET_MINUTES = 4 * 60;
export const INVALID_AMOUNT = 'Invalid amount';
export const INVALID_DATE = 'Invalid date';

const DECIMAL = /^-?\d+(\.\d+)?$/;

export type DateFormat = 'dd-MM-yyyy' | 'd MMM yyyy' | 'yyyy-MM-dd' | 'MM/dd/yyyy';
export type NumberFormat = '1,234.56' | '1.234,56' | '1 234.56';
export interface Region { date: DateFormat; number: NumberFormat; zone: string }
export const DEFAULT_REGION: Region = { date: 'dd-MM-yyyy', number: '1,234.56', zone: 'Asia/Dubai' };
let region: Region = { ...DEFAULT_REGION };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Use a person's region for every formatted value from now on. Missing or unknown parts keep the defaults. */
export function setRegion(next: Partial<Region>) {
  const zone = next.zone && validZone(next.zone) ? next.zone : DEFAULT_REGION.zone;
  region = { date: next.date ?? DEFAULT_REGION.date, number: next.number ?? DEFAULT_REGION.number, zone };
}
export const currentRegion = (): Region => ({ ...region });

function validZone(zone: string) { try { new Intl.DateTimeFormat('en', { timeZone: zone }); return true; } catch { return false; } }

const separators = () => region.number === '1.234,56' ? ['.', ','] : region.number === '1 234.56' ? [' ', '.'] : [',', '.'];
function group(digits: string) { return digits.replace(/\B(?=(\d{3})+(?!\d))/g, separators()[0]); }

/** Typed number text in the person's format as a plain decimal: '1.234,5' -> '1234.5' with 1.234,56. */
export function plainNumber(text: string): string {
  const [thousands, decimal] = separators();
  const clean = text.replace(/\s/g, '');
  return decimal === ',' ? clean.split(thousands).join('').replace(/,/g, '.') : clean.replace(/,/g, '');
}

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
  return (negative && !zero ? '-' : '') + group(whole) + (fraction ? separators()[1] + fraction : '');
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

// YYYY-MM-DD with an optional time and zone. A time with a zone (Z or +hh:mm) is converted to the
// person's time zone (GST unless they chose another); a time without a zone is shown as it is.
// The browser's own time zone is never used.
const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

interface Parts { year: number; month: number; day: number; hour: number; minute: number }

const zoned = new Map<string, Intl.DateTimeFormat>();

/** Minutes the person's time zone is ahead of UTC at a moment. */
export function zoneOffset(utc: number, zone = region.zone): number {
  if (zone === 'Asia/Dubai') return GST_OFFSET_MINUTES;
  let format = zoned.get(zone);
  if (!format) zoned.set(zone, format = new Intl.DateTimeFormat('en-GB', { timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }));
  const part = Object.fromEntries(format.formatToParts(new Date(utc)).map(item => [item.type, item.value]));
  return Math.round((Date.UTC(+part.year, +part.month - 1, +part.day, +part.hour, +part.minute) - Math.floor(utc / 60000) * 60000) / 60000);
}

/** The UTC moment of a wall-clock time in the person's zone. */
export function wallToUtc(year: number, month: number, day: number, hour: number, minute: number): Date {
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  return new Date(wall - zoneOffset(wall - zoneOffset(wall) * 60000) * 60000);
}

function partsInGst(value: string): Parts | null {
  const match = TIMESTAMP.exec(value.trim());
  if (!match) return null;
  const [year, month, day, hour, minute, second] = [1, 2, 3, 4, 5, 6].map(index => Number(match[index] ?? 0));
  if (!validDate(year, month, day) || hour > 23 || minute > 59 || second > 59) return null;
  const zone = match[7];
  if (!zone) return { year, month, day, hour, minute };
  const offset = zone === 'Z' ? 0 : (zone[0] === '-' ? -1 : 1) * (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(-2)));
  const utc = Date.UTC(year, month - 1, day, hour, minute) - offset * 60000;
  const moment = new Date(utc + zoneOffset(utc) * 60000);
  return { year: moment.getUTCFullYear(), month: moment.getUTCMonth() + 1, day: moment.getUTCDate(), hour: moment.getUTCHours(), minute: moment.getUTCMinutes() };
}

function dayText(parts: { year: number; month: number; day: number }) {
  const { year, month, day } = parts;
  switch (region.date) {
    case 'd MMM yyyy': return `${day} ${MONTHS[month - 1]} ${year}`;
    case 'yyyy-MM-dd': return `${year}-${pad(month)}-${pad(day)}`;
    case 'MM/dd/yyyy': return `${pad(month)}/${pad(day)}/${year}`;
    default: return `${pad(day)}-${pad(month)}-${year}`;
  }
}

/** '2026-11-01' -> '01-11-2026', or the person's date format. A timestamp shows its date in their zone. Not a real date: 'Invalid date'. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '';
  const parts = partsInGst(value);
  return parts ? dayText(parts) : INVALID_DATE;
}

/** '1-11-2026', '01/11/2026' (month first with MM/dd/yyyy), '1 Nov 2026' or '2026-11-01' -> '2026-11-01'; null when it is not a real date. */
export function parseDate(value: string): string | null {
  const text = value.trim();
  const shown = /^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/.exec(text), iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  const named = /^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})$/.exec(text);
  const order = region.date === 'MM/dd/yyyy' ? [2, 1, 3] : [1, 2, 3];
  const [day, month, year] = shown ? order.map(index => Number(shown[index]))
    : iso ? [iso[3], iso[2], iso[1]].map(Number)
    : named ? [Number(named[1]), MONTHS.findIndex(name => name.toLowerCase() === named[2].toLowerCase()) + 1, Number(named[3])] : [0, 0, 0];
  return validDate(year, month, day) ? `${year}-${pad(month)}-${pad(day)}` : null;
}

/** A timestamp in the person's zone (GST by default): '01-11-2026 14:05'. Accepts an ISO string or a Date. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '';
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return INVALID_DATE;
    value = value.toISOString();
  }
  const parts = partsInGst(value);
  return parts ? `${dayText(parts)} ${pad(parts.hour)}:${pad(parts.minute)}` : INVALID_DATE;
}

/** Today's date in GST as YYYY-MM-DD, the value today() uses in calculations. */
export function todayGst(now: Date = new Date()): string {
  const local = new Date(now.getTime() + GST_OFFSET_MINUTES * 60000);
  return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())}`;
}
