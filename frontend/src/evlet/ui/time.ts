// RelativeTime: a moment as "2 hours ago", with the exact time in the person's zone on hover or
// focus ("04-11-2026 14:05 GST"). Every RelativeTime on the page shares one minute clock, which runs
// only while some are shown and the tab is visible, so "just now" becomes "1 minute ago" on its own.

import { html } from '../dom';
import { cell, onCleanup, type Readable } from '../reactive';
import { currentRegion, formatDate, formatDateTime } from '../model/format';
import { read, type Prop } from './core';
import { tooltip } from './tooltip';

const MINUTE = 60_000, HOUR = 60 * MINUTE, DAY = 24 * HOUR, WEEK = 7 * DAY;

/**
 * The wording thresholds, in order: under `below` milliseconds a moment reads in `unit`. Past the last
 * one it shows the plain date. "yesterday" and "last week" are Intl's words for one day and one week.
 */
export const RELATIVE_STEPS: ReadonlyArray<{ below: number; unit: 'now' | Intl.RelativeTimeFormatUnit; size: number }> = [
  { below: MINUTE, unit: 'now', size: 1 },
  { below: HOUR, unit: 'minute', size: MINUTE },
  { below: DAY, unit: 'hour', size: HOUR },
  { below: WEEK, unit: 'day', size: DAY },
  { below: 5 * WEEK, unit: 'week', size: WEEK },
];

const words = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });

const moment = (value: string | Date) => value instanceof Date ? value.getTime() : Date.parse(value);

/** "just now", "1 minute ago", "3 hours ago", "yesterday", "4 days ago", "last week", "3 weeks ago", then the date. */
export function relativeTime(value: string | Date, now: number = Date.now()): string {
  const at = moment(value);
  if (Number.isNaN(at)) return '';
  // A clock a little ahead of this one still reads as now.
  const elapsed = Math.max(0, now - at);
  const step = RELATIVE_STEPS.find(item => elapsed < item.below);
  if (!step) return formatDate(new Date(at).toISOString());
  if (step.unit === 'now') return 'just now';
  return words.format(-Math.floor(elapsed / step.size), step.unit);
}

/** The person's time zone as a short label: GST for Asia/Dubai, otherwise what Intl calls it (GMT+5:30, UTC). */
export function zoneLabel(zone: string = currentRegion().zone, at: number = Date.now()): string {
  if (zone === 'Asia/Dubai') return 'GST';
  try {
    const part = new Intl.DateTimeFormat('en-GB', { timeZone: zone, timeZoneName: 'short' }).formatToParts(at).find(item => item.type === 'timeZoneName');
    return part?.value ?? zone;
  } catch { return zone; }
}

/** The exact time in the person's date format and zone: "04-11-2026 14:05 GST". */
export function exactTime(value: string | Date): string {
  const at = moment(value);
  if (Number.isNaN(at)) return '';
  return `${formatDateTime(new Date(at).toISOString())} ${zoneLabel(currentRegion().zone, at)}`;
}

// ---------- the shared minute clock ----------

const tick = cell(Date.now(), { name: 'time.minute' });
let users = 0;
let timer: ReturnType<typeof setInterval> | null = null;
let listening = false;

const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';

function schedule() {
  const wanted = users > 0 && !hidden();
  if (wanted && timer === null) timer = setInterval(() => tick.set(Date.now()), MINUTE);
  if (!wanted && timer !== null) { clearInterval(timer); timer = null; }
}

function onVisibility() {
  // Coming back after a while: bring every time up to date at once, then keep the minute beat.
  if (!hidden() && users > 0) tick.set(Date.now());
  schedule();
}

/** The current time, updated once a minute while something on the page reads it. Call inside a scope. */
export function minuteClock(): Readable<number> {
  if (!listening && typeof document !== 'undefined') { document.addEventListener('visibilitychange', onVisibility); listening = true; }
  users += 1;
  tick.set(Date.now());
  schedule();
  onCleanup(() => { users -= 1; schedule(); });
  return tick;
}

/** Whether the shared clock is running and how many times use it, for tests. */
export const clockState = () => ({ running: timer !== null, users });

// ---------- the component ----------

export interface RelativeTimeProps {
  /** An ISO time (UTC with Z, as the API sends it) or a Date. Nothing is shown for an empty value. */
  at: Prop<string | Date | null | undefined>;
  /** Show the exact time instead of the relative wording. */
  exact?: Prop<boolean>;
  /** Makes the value a button that switches between relative and exact. */
  onToggle?: () => void;
  className?: string;
}

/**
 * A `<time datetime="…">` showing a moment relative to now, with the exact time (or, when `exact`
 * is on, the relative wording) in a tooltip on hover or focus. With `onToggle` it sits in a button
 * that switches the wording.
 */
export function RelativeTime(props: RelativeTimeProps): HTMLElement {
  const clock = minuteClock();
  const value = () => read(props.at) ?? null;
  const exact = () => Boolean(read(props.exact ?? false));
  const iso = () => { const at = value(); if (at === null) return null; const time = moment(at); return Number.isNaN(time) ? null : new Date(time).toISOString(); };
  const relative = () => { const at = iso(); return at ? relativeTime(at, clock.get()) : ''; };
  const precise = () => { const at = iso(); return at ? exactTime(at) : ''; };
  const shown = () => exact() ? precise() : relative();
  const other = () => exact() ? relative() : precise();
  const label = () => [shown(), other()].filter(Boolean).join(', ');
  if (!props.onToggle) {
    // Focusable, so the tooltip opens from the keyboard too; the name holds both forms.
    const time = html`<time class=${['ev-time', props.className].filter(Boolean).join(' ')} tabindex="0" aria-label=${label}
      datetime=${() => iso() ?? ''}>${shown}</time>` as HTMLElement;
    tooltip(time, other);
    return time;
  }
  const toggle = props.onToggle;
  const button = html`<button type="button" class=${['ev-time', 'ev-time-toggle', props.className].filter(Boolean).join(' ')}
    aria-label=${() => `${label()}. ${exact() ? 'Show relative times' : 'Show exact times'}`} aria-pressed=${() => String(exact())}
    on:click=${() => toggle()}><time datetime=${() => iso() ?? ''}>${shown}</time></button>` as HTMLElement;
  tooltip(button, other);
  return button;
}
