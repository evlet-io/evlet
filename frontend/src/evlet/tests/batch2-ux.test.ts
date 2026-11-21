import { afterEach, describe, expect, it, vi } from 'vitest';
import { setRegion, todayDate, todayGst, todayIn } from '../model/format';
import { toast } from '../ui/layers';
import { Calendar, rangePresets } from '../ui/pickers';
import { cleanup, render } from './ui-helpers';

afterEach(() => { setRegion({}); cleanup(); });

describe("today in the person's time zone", () => {
  it('is the date in the given zone, not always GST', () => {
    const late = new Date('2026-11-20T21:30:00Z');
    expect(todayIn('UTC', late)).toBe('2026-11-20');
    expect(todayIn('Asia/Dubai', late)).toBe('2026-11-21');
    expect(todayGst(late)).toBe('2026-11-21');
    setRegion({ zone: 'UTC' });
    expect(todayDate(late)).toBe('2026-11-20');
    setRegion({ zone: 'America/New_York' });
    expect(todayDate(new Date('2026-11-21T02:00:00Z'))).toBe('2026-11-20');
  });
});

describe('week starts on', () => {
  const heads = () => [...document.querySelectorAll('.cal-head [role="columnheader"]')].map(cell => cell.getAttribute('aria-label'));

  it('orders the calendar and This week by the chosen first day', () => {
    render(() => Calendar({ value: () => '2026-11-04', onPick: () => undefined }));
    expect(heads()[0]).toBe('Monday');
    expect(Object.fromEntries(rangePresets('2026-11-04').map(item => [item.label, item.range]))['This week']).toEqual(['2026-11-02', '2026-11-08']);
    cleanup();
    setRegion({ week: 'sun' });
    render(() => Calendar({ value: () => '2026-11-04', onPick: () => undefined }));
    expect(heads()).toEqual(['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']);
    // The first row starts on the Sunday on or before the 1st (1 Nov 2026 is a Sunday).
    expect(document.querySelector('.cal-day')!.getAttribute('data-day')).toBe('2026-11-01');
    expect(Object.fromEntries(rangePresets('2026-11-04').map(item => [item.label, item.range]))['This week']).toEqual(['2026-11-01', '2026-11-07']);
    setRegion({ week: 'sat' });
    expect(Object.fromEntries(rangePresets('2026-11-04').map(item => [item.label, item.range]))['This week']).toEqual(['2026-10-31', '2026-11-06']);
  });
});

describe('toast timer', () => {
  it('pauses while hovered or focused and restarts after', () => {
    vi.useFakeTimers();
    try {
      toast('Moved to Discovery', { undo: () => undefined });
      const element = document.querySelector<HTMLElement>('.toast')!;
      element.dispatchEvent(new MouseEvent('mouseenter'));
      vi.advanceTimersByTime(20000);
      expect(element.isConnected).toBe(true);
      element.dispatchEvent(new MouseEvent('mouseleave'));
      element.querySelector('button')!.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
      vi.advanceTimersByTime(20000);
      expect(element.isConnected).toBe(true);
      element.querySelector('button')!.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
      vi.advanceTimersByTime(5300);
      expect(element.isConnected).toBe(false);
    } finally { vi.useRealTimers(); }
  });

  it('gives a hint a neutral info icon, not the green tick', () => {
    toast('Type an option first', { tone: 'info' });
    const icon = document.querySelector('.toast svg')!;
    expect(icon.classList.contains('note')).toBe(true);
    expect(icon.classList.contains('ok')).toBe(false);
  });
});

describe('create checks required fields first', () => {
  it('names the empty required fields and sends nothing', async () => {
    const { adminRoutes, byText, click, lastToast, requests, start, stopAll, text, until } = await import('./screens-helpers');
    try {
      const { host, sent } = start(adminRoutes(), '/app/contact/new');
      await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New contact'));
      click(byText(host, '.ev-savebar button', /^Create/));
      await until(() => expect(lastToast()).toMatch(/^Not saved\. .+ needs attention\.$/));
      const errors = [...host.querySelectorAll('.fld .err')].map(item => text(item)).filter(Boolean);
      expect(errors).toHaveLength(1);
      expect(errors[0]).toMatch(/ is required$/);
      expect(host.querySelector('.fld .err[role="alert"]')).toBeNull();
      await new Promise(resolve => setTimeout(resolve, 20));
      expect(requests(sent, 'POST', /\/api\/contact$/)).toHaveLength(0);
    } finally { stopAll(); }
  });
});
