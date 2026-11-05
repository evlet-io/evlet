// RelativeTime: the wording at every threshold with a fixed clock, the exact time in the person's date
// format and zone, the <time datetime> element and its accessible name, the toggle, and the one shared
// minute clock that pauses while the tab is hidden.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { setRegion } from '../model/format';
import { clockState, exactTime, relativeTime, RelativeTime, RELATIVE_STEPS, zoneLabel } from '../ui/time';
import { tipText } from '../ui/tooltip';
import { cleanup, render } from './ui-helpers';

const NOW = Date.UTC(2026, 10, 4, 10, 0, 0); // 04-11-2026 14:00 GST
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const S = 1000, M = 60 * S, H = 60 * M, D = 24 * H, W = 7 * D;

let visibility: DocumentVisibilityState = 'visible';
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
});
afterEach(() => { cleanup(); setRegion({}); vi.useRealTimers(); });

describe('relativeTime', () => {
  it.each([
    [0, 'just now'],
    [59 * S, 'just now'],
    [60 * S, '1 minute ago'],
    [2 * M - 1, '1 minute ago'],
    [2 * M, '2 minutes ago'],
    [59 * M + 59 * S, '59 minutes ago'],
    [H, '1 hour ago'],
    [2 * H, '2 hours ago'],
    [D - 1, '23 hours ago'],
    [D, 'yesterday'],
    [2 * D - 1, 'yesterday'],
    [2 * D, '2 days ago'],
    [W - 1, '6 days ago'],
    [W, 'last week'],
    [2 * W - 1, 'last week'],
    [2 * W, '2 weeks ago'],
    [5 * W - 1, '4 weeks ago'],
    [5 * W, '30-09-2026'],
    [400 * D, '30-09-2025'],
  ])('%i ms ago reads "%s"', (elapsed, expected) => {
    expect(relativeTime(ago(elapsed), NOW)).toBe(expected);
  });

  it('keeps the thresholds in one ordered list', () => {
    expect(RELATIVE_STEPS.map(step => step.unit)).toEqual(['now', 'minute', 'hour', 'day', 'week']);
    expect(RELATIVE_STEPS.map(step => step.below)).toEqual([M, H, D, W, 5 * W]);
  });

  it('reads a time a little ahead of this clock as now, and nothing for bad input', () => {
    expect(relativeTime(new Date(NOW + 30 * S).toISOString(), NOW)).toBe('just now');
    expect(relativeTime('not a time', NOW)).toBe('');
  });

  it('shows an old date in the person\'s date format', () => {
    setRegion({ date: 'd MMM yyyy' });
    expect(relativeTime(ago(40 * D), NOW)).toBe('25 Sep 2026');
  });
});

describe('exactTime', () => {
  it('is DD-MM-YYYY HH:mm in GST by default', () => {
    expect(exactTime('2026-11-04T08:15:30.125Z')).toBe('04-11-2026 12:15 GST');
    expect(zoneLabel('Asia/Dubai')).toBe('GST');
  });

  it('follows the person\'s zone and names it', () => {
    setRegion({ zone: 'UTC' });
    expect(exactTime('2026-11-04T08:15:30.125Z')).toBe('04-11-2026 08:15 UTC');
    setRegion({ zone: 'Asia/Kolkata' });
    expect(exactTime('2026-11-04T08:15:30.125Z')).toBe(`04-11-2026 13:45 ${zoneLabel('Asia/Kolkata')}`);
  });
});

describe('RelativeTime', () => {
  it('renders <time datetime> with the relative words and both forms in its name', () => {
    const host = render(() => RelativeTime({ at: ago(2 * H) }));
    const time = host.querySelector('time')!;
    expect(time.getAttribute('datetime')).toBe(ago(2 * H));
    expect(time.textContent).toBe('2 hours ago');
    expect(time.getAttribute('aria-label')).toBe('2 hours ago, 04-11-2026 12:00 GST');
    expect(time.tabIndex).toBe(0);
  });

  it('shows the exact time in a tooltip on hover and on focus', () => {
    const host = render(() => RelativeTime({ at: ago(5 * M) }));
    const time = host.querySelector('time')!;
    time.dispatchEvent(new Event('mouseenter'));
    expect(tipText()).toBe('04-11-2026 13:55 GST');
    time.dispatchEvent(new Event('mouseleave'));
    expect(tipText()).toBeNull();
    time.dispatchEvent(new Event('focus'));
    expect(tipText()).toBe('04-11-2026 13:55 GST');
  });

  it('shows nothing for an empty value', () => {
    const host = render(() => RelativeTime({ at: null }));
    expect(host.querySelector('time')!.textContent).toBe('');
  });

  it('switches between relative and exact with its toggle', () => {
    const exact = cell(false);
    const toggle = vi.fn(() => exact.set(!exact.peek()));
    const host = render(() => RelativeTime({ at: ago(3 * D), exact, onToggle: toggle }));
    const button = host.querySelector('button')!;
    expect(button.querySelector('time')!.textContent).toBe('3 days ago');
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(button.getAttribute('aria-label')).toBe('3 days ago, 01-11-2026 14:00 GST. Show exact times');
    button.click();
    expect(toggle).toHaveBeenCalledOnce();
    expect(button.querySelector('time')!.textContent).toBe('01-11-2026 14:00 GST');
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.getAttribute('aria-label')).toBe('01-11-2026 14:00 GST, 3 days ago. Show relative times');
    button.dispatchEvent(new Event('focus'));
    expect(tipText()).toBe('3 days ago');
  });
});

describe('the shared minute clock', () => {
  it('runs one timer for every time on the page and moves "just now" on', () => {
    const host = render(() => [RelativeTime({ at: ago(0) }), RelativeTime({ at: ago(30 * S) }), RelativeTime({ at: ago(H) })]);
    expect(clockState()).toEqual({ running: true, users: 3 });
    expect(vi.getTimerCount()).toBe(1);
    const texts = () => [...host.querySelectorAll('time')].map(time => time.textContent);
    expect(texts()).toEqual(['just now', 'just now', '1 hour ago']);
    vi.advanceTimersByTime(60 * S);
    expect(texts()).toEqual(['1 minute ago', '1 minute ago', '1 hour ago']);
    cleanup();
    expect(clockState()).toEqual({ running: false, users: 0 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('pauses while the tab is hidden and catches up when it shows again', () => {
    const host = render(() => RelativeTime({ at: ago(0) }));
    visibility = 'hidden';
    document.dispatchEvent(new Event('visibilitychange'));
    expect(clockState().running).toBe(false);
    vi.advanceTimersByTime(10 * M);
    expect(host.querySelector('time')!.textContent).toBe('just now');
    visibility = 'visible';
    document.dispatchEvent(new Event('visibilitychange'));
    expect(clockState().running).toBe(true);
    expect(host.querySelector('time')!.textContent).toBe('10 minutes ago');
  });
});
