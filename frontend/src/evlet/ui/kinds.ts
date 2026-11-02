// Field types: how each model field type reads at rest and how typed text becomes a value.
// The field components (fields.ts) share everything else through BaseField.
//
// Values keep the wire format the Python fields use: exact decimals as strings, dates as ISO
// dates, date-times in UTC, times as HH:MM:SS, durations as ISO 8601 (PT1H30M), tags as lists.

import { parse as parseNumber, quantize, text as numberText } from '../model/finite';
import { decimalMark, formatDate, formatDateTime, formatMoney, formatNumber, plainNumber as plain, wallToUtc } from '../model/format';
import { clockText, dateOrder, parseClock, parseDay, parseDayTime, parseLength } from './dateparse';

/** Typed number text as a plain decimal, or an error that names the person's decimal mark. */
function plainNumber(text: string): string {
  const value = plain(text);
  if (value === null) throw new InputError(`Use ${decimalMark()} for decimals`);
  return value;
}
import type { FieldSchema } from '../model/schema';

/** A message for the user when typed text is not a valid value. */
export class InputError extends Error {}

export interface Kind {
  /** Text at rest ('' when empty). */
  format(value: unknown, meta: FieldSchema): string;
  /** Text in the input while editing. */
  edit?(value: unknown, meta: FieldSchema): string;
  /** Typed text to a value; throws InputError with a message that says how to fix it. */
  parse?(text: string, meta: FieldSchema): unknown;
  prefix?(meta: FieldSchema): string | null;
  suffix?(meta: FieldSchema): string | null;
  align?: 'right';
  inputMode?: string;
  placeholder?: string;
  /** How the control is built: a text input (default) or a dedicated control. */
  control?: 'textarea' | 'switch' | 'select' | 'link' | 'tags' | 'multiselect' | 'readonly';
  /** A picker next to the text input (pickers.ts). */
  picker?: 'date' | 'datetime' | 'time' | 'duration';
}

const empty = (value: unknown) => value === null || value === undefined || value === '';
const humanize = (value: string) => value.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());

function bounds(value: number | string, meta: FieldSchema, show: (value: string) => string = String) {
  const min = meta.minimum, max = meta.maximum;
  const number = parseNumber(String(value));
  const below = min !== null && min !== undefined && Number(numberText(number)) < Number(min);
  const above = max !== null && max !== undefined && Number(numberText(number)) > Number(max);
  if (!below && !above) return;
  if (min !== null && min !== undefined && max !== null && max !== undefined) throw new InputError(`Use a number from ${show(String(min))} to ${show(String(max))}`);
  if (below) throw new InputError(`Use ${show(String(min))} or more`);
  throw new InputError(`Use ${show(String(max))} or less`);
}

function decimal(scale: (meta: FieldSchema) => number) {
  return (text: string, meta: FieldSchema) => {
    const clean = plainNumber(text);
    if (!clean) return null;
    if (!/^-?(\d+\.?\d*|\.\d+)$/.test(clean)) throw new InputError('Enter a number, for example 1250.50');
    const places = (clean.split('.')[1] ?? '').length, allowed = scale(meta);
    if (places > allowed) throw new InputError(allowed ? `Use at most ${allowed} decimal places` : 'Enter a whole number');
    const value = numberText(quantize(parseNumber(clean.startsWith('.') ? `0${clean}` : clean.startsWith('-.') ? clean.replace('-.', '-0.') : clean), allowed), allowed);
    bounds(value, meta);
    return value;
  };
}

const scaleOf = (meta: FieldSchema, fallback: number) => typeof meta.scale === 'number' ? meta.scale : fallback;
/** The fixed currency code of a Currency field (AED unless the schema names one). */
export const currencyOf = (meta: FieldSchema) => {
  const code = meta.currency;
  return typeof code === 'string' && /^[A-Z]{3}$/.test(code) ? code : 'AED';
};

// ---------- durations ----------

const ISO_DURATION = /^(-)?P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/;

/** Seconds from the wire value (ISO 8601 text or a number of seconds). */
export function durationSeconds(value: unknown): number | null {
  if (typeof value === 'number') return value;
  const match = typeof value === 'string' ? ISO_DURATION.exec(value) : null;
  if (!match) return null;
  const [, sign, days, hours, minutes, seconds] = match;
  const total = ((Number(days ?? 0) * 24 + Number(hours ?? 0)) * 60 + Number(minutes ?? 0)) * 60 + Number(seconds ?? 0);
  return sign ? -total : total;
}

/** 5400 -> '1h 30m'. */
export function durationText(seconds: number): string {
  const sign = seconds < 0 ? '-' : '';
  let rest = Math.abs(seconds);
  const days = Math.floor(rest / 86400); rest -= days * 86400;
  const hours = Math.floor(rest / 3600); rest -= hours * 3600;
  const minutes = Math.floor(rest / 60), secs = rest - minutes * 60;
  const parts = [[days, 'd'], [hours, 'h'], [minutes, 'm'], [secs, 's']].filter(([amount]) => amount).map(([amount, unit]) => `${amount}${unit}`);
  return sign + (parts.join(' ') || '0m');
}

function isoDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60), secs = seconds % 60;
  const clock = [[hours, 'H'], [minutes, 'M'], [secs, 'S']].filter(([amount]) => amount).map(([amount, unit]) => `${amount}${unit}`).join('');
  return days || clock ? `P${days ? `${days}D` : ''}${clock ? `T${clock}` : ''}` : 'PT0S';
}

/** '1h 30m', '90m', '1:30', '1.5h' or '2d 4h' to seconds. A bare number is minutes (dateparse.ts). */
export function parseDuration(text: string): number {
  const seconds = parseLength(text);
  if (seconds === null) throw new InputError('Type a length of time such as 1h 30m');
  return seconds;
}

// ---------- dates and times ----------

const pad = (number: number) => String(number).padStart(2, '0');

/** The person's date format, as typed in a message: DD-MM-YYYY, MM/DD/YYYY or YYYY-MM-DD. */
const dayHint = () => ({ DMY: 'DD-MM-YYYY', MDY: 'MM/DD/YYYY', YMD: 'YYYY-MM-DD' })[dateOrder()];

/** A typed date (12062027, 12/6/27, today, +3: see dateparse.ts) as YYYY-MM-DD. */
export function parseDateText(text: string): string {
  const value = parseDay(text);
  if (!value) throw new InputError(`Enter a date as ${dayHint()}`);
  return value;
}

/** A typed date and time ('12062027 1235', '12/06/27 9am', 'today 14:30') in the person's zone to a UTC ISO timestamp. */
export function parseDateTime(text: string): string {
  const value = parseDayTime(text);
  if (!value) throw new InputError(`Enter a date and time as ${dayHint()} HH:MM`);
  const [year, month, date] = value.day.split('-').map(Number);
  return wallToUtc(year, month, date, value.hours, value.minutes).toISOString().replace('.000Z', 'Z');
}

/** '14:05', '1405', '935', '2:05 pm', '12am' or '14:05:30' to 'HH:MM:SS'. */
export function parseTime(text: string): string {
  const value = parseClock(text);
  if (!value) throw new InputError('Enter a time as HH:MM, for example 14:30');
  return clockText(value);
}

const timeText = (value: unknown) => {
  const match = typeof value === 'string' ? /^(\d{2}):(\d{2})(?::(\d{2}))?/.exec(value) : null;
  return match ? `${match[1]}:${match[2]}${match[3] && match[3] !== '00' ? `:${match[3]}` : ''}` : '';
};

// ---------- tags ----------

/** Validate one tag as the server does: 1 to 40 characters, no quotes, backslashes or commas. */
export function checkTag(tag: string): string {
  const value = tag.trim();
  if (!value || value.length > 40 || /["\\,\x00-\x1f\x7f]/.test(value)) throw new InputError('A tag has 1 to 40 characters and no quotes, backslashes or commas');
  return value;
}

// ---------- the kinds ----------

const text: Kind = {
  format: value => empty(value) ? '' : String(value),
  parse: (input, meta) => {
    const value = input.trim();
    if (meta.max_length && value.length > meta.max_length) throw new InputError(`Use ${meta.max_length} characters or fewer`);
    return value || null;
  },
};

export const KINDS: Record<string, Kind> = {
  text,
  longtext: { ...text, control: 'textarea', parse: (input, meta) => text.parse!(input.replace(/\s+$/, ''), meta) },
  integer: {
    format: value => empty(value) ? '' : formatNumber(String(value), 0),
    edit: value => empty(value) ? '' : String(value),
    parse: (input, meta) => {
      const clean = plainNumber(input);
      if (!clean) return null;
      if (!/^-?\d+$/.test(clean)) throw new InputError('Enter a whole number');
      const value = Number(clean);
      if (!Number.isSafeInteger(value)) throw new InputError('Enter a smaller number');
      bounds(value, meta);
      return value;
    },
    align: 'right', inputMode: 'numeric',
    suffix: meta => meta.suffix || ((meta as any).unit ?? null),
  },
  decimal: {
    format: (value, meta) => empty(value) ? '' : formatNumber(value as string, scaleOf(meta, 2)),
    edit: (value, meta) => empty(value) ? '' : formatNumber(value as string, scaleOf(meta, 2)),
    parse: decimal(meta => scaleOf(meta, 2)), align: 'right', inputMode: 'decimal', suffix: meta => meta.suffix || null,
  },
  currency: {
    format: (value, meta) => empty(value) ? '' : formatMoney(value as string, { currency: currencyOf(meta), scale: scaleOf(meta, 2) }),
    edit: (value, meta) => empty(value) ? '' : formatNumber(value as string, scaleOf(meta, 2)),
    parse: decimal(meta => scaleOf(meta, 2)), align: 'right', inputMode: 'decimal', prefix: currencyOf,
  },
  percent: {
    format: (value, meta) => empty(value) ? '' : `${formatNumber(value as string, scaleOf(meta, 2))}%`,
    edit: (value, meta) => empty(value) ? '' : formatNumber(value as string, scaleOf(meta, 2)),
    parse: decimal(meta => scaleOf(meta, 2)), align: 'right', inputMode: 'decimal', suffix: () => '%',
  },
  date: {
    format: value => formatDate(value as string),
    parse: input => input.trim() ? parseDateText(input) : null,
    placeholder: 'DD-MM-YYYY', picker: 'date',
  },
  datetime: {
    format: value => formatDateTime(value as string),
    parse: input => input.trim() ? parseDateTime(input) : null,
    placeholder: 'DD-MM-YYYY HH:MM', picker: 'datetime',
  },
  time: { format: timeText, parse: input => input.trim() ? parseTime(input) : null, placeholder: 'HH:MM', picker: 'time' },
  duration: {
    format: value => { const seconds = durationSeconds(value); return seconds === null ? '' : durationText(seconds); },
    parse: input => input.trim() ? isoDuration(parseDuration(input)) : null,
    placeholder: '1h 30m', picker: 'duration',
  },
  boolean: { format: value => value === true ? 'Yes' : value === false ? 'No' : '', control: 'switch' },
  select: { format: value => empty(value) ? '' : humanize(String(value)), control: 'select' },
  link: { format: value => empty(value) ? '' : `#${value}`, control: 'link' },
  email: {
    ...text, inputMode: 'email',
    parse: (input, meta) => {
      const value = text.parse!(input, meta) as string | null;
      if (value && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) throw new InputError('Enter an email address like name@example.com');
      return value;
    },
  },
  url: {
    ...text, inputMode: 'url',
    parse: (input, meta) => {
      let value = text.parse!(input, meta) as string | null;
      if (!value) return null;
      if (!/^[a-z][a-z0-9+.-]*:/i.test(value)) value = `https://${value}`;
      let valid = false;
      try { const url = new URL(value); valid = (url.protocol === 'http:' || url.protocol === 'https:') && Boolean(url.hostname) && !/\s/.test(value); } catch { valid = false; }
      if (!valid) throw new InputError('Enter a full web address, for example https://example.com');
      return value;
    },
  },
  phone: {
    ...text, inputMode: 'tel',
    parse: (input, meta) => {
      const value = (text.parse!(input, meta) as string | null)?.trim() ?? null;
      if (!value) return null;
      if (!/^\+?[0-9 ()-]+$/.test(value) || value.slice(1).includes('+')) throw new InputError('Use digits, spaces, +, - and brackets only');
      const digits = value.replace(/\D/g, '');
      if (digits.length < 3 || digits.length > 15) throw new InputError('A phone number has 3 to 15 digits');
      return value.startsWith('+') ? `+${digits}` : value;
    },
  },
  tags: { format: value => Array.isArray(value) ? value.join(', ') : '', control: 'tags' },
  multiselect: { format: value => Array.isArray(value) ? value.map(item => humanize(String(item))).join(', ') : '', control: 'multiselect' },
  state: { format: value => empty(value) ? '' : humanize(String(value)), control: 'readonly' },
  sequence: { format: value => empty(value) ? '' : String(value), control: 'readonly' },
  reference: {
    format: value => {
      const reference = Array.isArray(value) ? { model: value[0], id: value[1] } : value as { model?: string; id?: number } | null;
      return reference && reference.model ? `${humanize(String(reference.model))} #${reference.id}` : '';
    },
    control: 'readonly',
  },
};

/** The kind for a field type; unknown types read and edit as text. */
export const kindOf = (type: string): Kind => KINDS[type] ?? text;

/** A value as text at rest, for tables and read-only places. */
export const formatValue = (meta: FieldSchema, value: unknown): string => kindOf(meta.type).format(value, meta);

export { humanize };
