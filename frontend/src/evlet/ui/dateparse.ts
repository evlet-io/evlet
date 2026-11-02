// Typed dates, times and lengths of time, read the forgiving way Frappe reads them: 120627,
// 12/6/27, 1206, today, 935, 12.35pm, 1h 30m. Pure functions with no DOM, used by the date,
// date-time, time and duration fields, their pickers and the date range filter.
//
// The order of day, month and year follows the person's date format (Settings, Appearance &
// region): MM/dd/yyyy reads month first, yyyy-MM-dd year first, every other format day first.
// Text that can't be read, or names a day that doesn't exist (31-02), gives null: the caller
// shows its error and leaves the text as typed. Nothing is guessed, and the server checks again.

import { currentRegion, todayGst, type DateFormat } from '../model/format';

export type DateOrder = 'DMY' | 'MDY' | 'YMD';

/** Day, month and year order of a date format: MM/dd/yyyy is MDY, yyyy-MM-dd is YMD, others DMY. */
export function dateOrder(format: DateFormat = currentRegion().date): DateOrder {
  return format === 'MM/dd/yyyy' ? 'MDY' : format === 'yyyy-MM-dd' ? 'YMD' : 'DMY';
}

export interface DayOptions {
  /** Today as YYYY-MM-DD (GST by default): the base for words, short forms and two-digit years. */
  today?: string;
  order?: DateOrder;
}

const pad = (number: number) => String(number).padStart(2, '0');
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

function iso(year: number, month: number, day: number): string | null {
  if (!Number.isInteger(year) || year < 1 || year > 9999 || month < 1 || month > 12 || day < 1) return null;
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return day > last ? null : `${String(year).padStart(4, '0')}-${pad(month)}-${pad(day)}`;
}

function shift(day: string, count: number): string {
  const [year, month, date] = day.split('-').map(Number);
  const at = new Date(Date.UTC(year, month - 1, date + count));
  return `${at.getUTCFullYear()}-${pad(at.getUTCMonth() + 1)}-${pad(at.getUTCDate())}`;
}

/**
 * A two-digit year as the year ending in those digits that is nearest this year: within 50 years
 * either way, and 50 years ahead still counts as ahead. In 2026, 27 is 2027, 76 is 2076, 77 is 1977.
 */
export function fullYear(short: number, thisYear: number): number {
  let year = thisYear - (thisYear % 100) + short;
  if (year > thisYear + 50) year -= 100;
  else if (year < thisYear - 49) year += 100;
  return year;
}

/**
 * A typed date as YYYY-MM-DD, or null. In day-first order:
 * - 12062027, 120627, 1206 (this year), 12 (this month)
 * - 12/06/27, 12-06-2027, 12.06.2027, 12 06 2027, 1/6/27, 12/06 (this year)
 * - 12 Jun 2027, 12 June 27, Jun 12 2027, 2027-06-12 (ISO always reads year first)
 * - today or t, tomorrow, yesterday, +3 and -3 (days from today)
 */
export function parseDay(text: string, options: DayOptions = {}): string | null {
  const today = options.today ?? todayGst(), order = options.order ?? dateOrder();
  const [thisYear, thisMonth] = today.split('-').map(Number);
  const value = text.trim().toLowerCase().replace(/,/g, ' ');
  if (!value) return null;
  if (value === 'today' || value === 't') return today;
  if (value === 'tomorrow') return shift(today, 1);
  if (value === 'yesterday') return shift(today, -1);
  const relative = /^([+-])\s*(\d{1,4})$/.exec(value);
  if (relative) return shift(today, Number(relative[2]) * (relative[1] === '-' ? -1 : 1));

  const year = (part: string) => part.length === 2 ? fullYear(Number(part), thisYear) : part.length === 4 ? Number(part) : NaN;
  const named = /^(?:(\d{1,2})\s*([a-z]{3,9})\.?|([a-z]{3,9})\.?\s*(\d{1,2}))(?:\s+(\d{2}|\d{4}))?$/.exec(value);
  if (named) {
    const word = named[2] ?? named[3], month = MONTHS.indexOf(word.slice(0, 3)) + 1;
    if (!month || !'january february march april may june july august september october november december'.split(' ')[month - 1].startsWith(word)) return null;
    return iso(named[5] ? year(named[5]) : thisYear, month, Number(named[1] ?? named[4]));
  }

  // Digits only: the length says which parts are there.
  if (/^\d+$/.test(value)) {
    const two = (from: number) => Number(value.slice(from, from + 2));
    if (value.length <= 2) return iso(thisYear, thisMonth, Number(value));
    if (value.length === 4) return order === 'DMY' ? iso(thisYear, two(2), two(0)) : iso(thisYear, two(0), two(2));
    if (value.length === 6) {
      if (order === 'YMD') return iso(fullYear(two(0), thisYear), two(2), two(4));
      return order === 'DMY' ? iso(year(value.slice(4)), two(2), two(0)) : iso(year(value.slice(4)), two(0), two(2));
    }
    if (value.length === 8) {
      if (order === 'YMD') return iso(Number(value.slice(0, 4)), two(4), two(6));
      return order === 'DMY' ? iso(Number(value.slice(4)), two(2), two(0)) : iso(Number(value.slice(4)), two(0), two(2));
    }
    return null;
  }

  // Parts separated by / - . or spaces.
  const parts = value.split(/\s*[-/.]\s*|\s+/);
  if (parts.some(part => !/^\d+$/.test(part))) return null;
  if (parts.length === 3) {
    // A four-digit first part is a year whatever the person's order (2027-06-12).
    if (parts[0].length === 4) return iso(Number(parts[0]), Number(parts[1]), Number(parts[2]));
    if (parts.some(part => part.length > 2) && parts[2].length !== 4) return null;
    if (order === 'YMD') return iso(year(parts[0]), Number(parts[1]), Number(parts[2]));
    const [day, month] = order === 'DMY' ? [parts[0], parts[1]] : [parts[1], parts[0]];
    return iso(year(parts[2]), Number(month), Number(day));
  }
  if (parts.length === 2 && parts.every(part => part.length <= 2)) {
    const [day, month] = order === 'DMY' ? [parts[0], parts[1]] : [parts[1], parts[0]];
    return iso(thisYear, Number(month), Number(day));
  }
  return null;
}

export interface Clock { hours: number; minutes: number; seconds: number }

/**
 * A typed time of day, or null. 1235 is 12:35, 120627 is 12:06:27, 935 is 09:35, 9 is 09:00;
 * 12:35, 12.35, 14:05:30; with am or pm: 9am, 12.35pm, 12:35 pm, 2:05 p.m. 12am is 00:00 and
 * 12pm is 12:00. Minutes always have two digits, so 9.5 is not read.
 */
export function parseClock(text: string): Clock | null {
  const match = /^(\d{1,6}|\d{1,2}[:.]\d{2}(?:[:.]\d{2})?)\s*(a|p|am|pm|a\.m\.|p\.m\.)?$/.exec(text.trim().toLowerCase());
  if (!match) return null;
  const body = match[1], half = match[2]?.[0] as 'a' | 'p' | undefined;
  let hours: number, minutes = 0, seconds = 0;
  if (/[:.]/.test(body)) [hours, minutes, seconds = 0] = body.split(/[:.]/).map(Number);
  else if (body.length <= 2) hours = Number(body);
  else if (body.length === 3) [hours, minutes] = [Number(body[0]), Number(body.slice(1))];
  else if (body.length === 4) [hours, minutes] = [Number(body.slice(0, 2)), Number(body.slice(2))];
  else if (body.length === 6) [hours, minutes, seconds] = [Number(body.slice(0, 2)), Number(body.slice(2, 4)), Number(body.slice(4))];
  else return null;
  if (half) {
    if (hours < 1 || hours > 12) return null;
    hours = hours % 12 + (half === 'p' ? 12 : 0);
  }
  return hours > 23 || minutes > 59 || seconds > 59 ? null : { hours, minutes, seconds };
}

/** HH:MM:SS, as the server stores a time. */
export const clockText = (clock: Clock) => `${pad(clock.hours)}:${pad(clock.minutes)}:${pad(clock.seconds)}`;

export interface DayTime extends Clock { day: string }

/**
 * A typed date and time: a date part, then a time part after a space, a comma or T (12062027 1235,
 * 12/06/27 9am, today 14:30, 2027-06-12T09:30). A date alone is midnight. null when either part
 * can't be read.
 */
export function parseDayTime(text: string, options: DayOptions = {}): DayTime | null {
  const value = text.trim();
  // A date written with one kind of separator is a whole date (12 06 2027 is not 12-06 at 20:27).
  const separators = new Set((value.match(/\s*[-/.]\s*|\s+/g) ?? []).map(mark => mark.trim() || ' '));
  const whole = separators.size <= 1 ? parseDay(value, options) : null;
  if (whole) return { day: whole, hours: 0, minutes: 0, seconds: 0 };
  // Otherwise try each place the text could split, from the right, so "12 06 2027 12:35 pm" splits before 12:35.
  const splits = [...value.matchAll(/\s*,\s*|\s+|(?<=\d)T(?=\d)/g)].reverse();
  for (const split of splits) {
    const day = parseDay(value.slice(0, split.index), options), clock = day && parseClock(value.slice(split.index! + split[0].length));
    if (day && clock) return { day, ...clock };
  }
  const day = parseDay(value, options);
  return day ? { day, hours: 0, minutes: 0, seconds: 0 } : null;
}

const UNITS: Array<[RegExp, number]> = [[/^d(?:ays?)?$/, 86400], [/^h(?:ours?|rs?)?$/, 3600], [/^m(?:in(?:ute)?s?)?$/, 60], [/^s(?:ec(?:ond)?s?)?$/, 1]];

/**
 * A typed length of time in whole seconds, or null.
 * - Without a unit: a whole number is minutes (90 is 1h 30m); h:mm or h:mm:ss with : or . is
 *   hours, minutes and seconds (1:30, 1.30, 1.05, 1:30:20, 1.30.20). After a dot without a unit
 *   only two digits from 00 to 59 are read, so 1.5, 1.62 and 1.300 are refused, never guessed.
 * - With units, each number may have a fraction of its unit: 1h 30m, 1hr 30mins, 1 h 30 min,
 *   2 hours, 45 sec, 2d 4h, 1.5h (1h 30m), 1.30h (1h 18m), 2.25h, 1.5m (1m 30s). A total that is
 *   not a whole number of seconds is refused.
 */
export function parseLength(text: string): number | null {
  const value = text.trim().toLowerCase();
  if (!value) return null;
  if (/^\d+$/.test(value)) return Number(value) * 60;
  const clock = /^(\d+)([:.])([0-5]\d)(?:\2([0-5]\d))?$/.exec(value);
  if (clock) return Number(clock[1]) * 3600 + Number(clock[3]) * 60 + Number(clock[4] ?? 0);
  // Units: "1.5 h 30 min" is the tokens 1.5 h, 30 min. Each unit once.
  const tokens = [...value.matchAll(/(\d+)(?:\.(\d+))?\s*([a-z]+)\s*/g)];
  if (!tokens.length || tokens.map(token => token[0]).join('') !== value.replace(/^\s+/, '')) return null;
  let total = 0n, scale = 1n;
  const seen = new Set<number>();
  for (const [, whole, fraction = '', word] of tokens) {
    const unit = UNITS.find(([pattern]) => pattern.test(word))?.[1];
    if (!unit || seen.has(unit)) return null;
    seen.add(unit);
    // Exact: whole.fraction × unit seconds, kept as an integer over a power of ten.
    const places = 10n ** BigInt(fraction.length);
    const amount = (BigInt(whole) * places + BigInt(fraction || '0')) * BigInt(unit);
    total = total * places + amount * scale;
    scale *= places;
  }
  if (total % scale !== 0n) return null;
  const seconds = total / scale;
  return seconds > BigInt(Number.MAX_SAFE_INTEGER) ? null : Number(seconds);
}
