// Recent records in the top bar: the history button and its list, newest first with when each was
// opened, keyboard use, Escape, outside clicks, Clear, and entries from earlier builds without a time.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RecentsButton } from '../app/recents';
import { Prefs } from '../app/prefs';
import type { AppContext } from '../app/context';
import type { ScreenModel } from '../app/meta';
import { setRegion } from '../model/format';
import { tipText } from '../ui/tooltip';
import { cleanup, render } from './ui-helpers';

const NOW = Date.UTC(2026, 10, 4, 10, 0, 0); // 04-11-2026 14:00 GST
const M = 60_000, D = 24 * 60 * M;
const models = [{ name: 'order', label: 'Order', plural: 'Orders' }, { name: 'customer', label: 'Customer', plural: 'Customers' }] as unknown as ScreenModel[];

let prefs: Prefs;
const mountButton = () => {
  const ctx = { prefs, router: { href: (_: string, params: Record<string, string>) => `/app/${params.model}/${params.id}` } } as unknown as AppContext;
  const host = render(() => RecentsButton(ctx, models));
  return host.querySelector<HTMLButtonElement>('.rec-btn')!;
};
const pop = () => document.querySelector<HTMLElement>('.rec-pop');
const rows = () => [...document.querySelectorAll<HTMLElement>('.rec-item')];
const key = (target: Element, name: string) => target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
const flush = () => new Promise<void>(resolve => queueMicrotask(resolve));

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  vi.setSystemTime(NOW);
  prefs = new Prefs('tester', null);
});
afterEach(() => { cleanup(); prefs.dispose(); setRegion({}); vi.useRealTimers(); });

describe('recent records button', () => {
  it('is a named history button with a menu', () => {
    const button = mountButton();
    expect(button.getAttribute('aria-label')).toBe('Recent records');
    expect(button.getAttribute('aria-haspopup')).toBe('dialog');
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(button.querySelector('svg')).not.toBeNull();
  });

  it('lists records newest first with the model and when each was opened', async () => {
    prefs.recentRecords.set([
      { model: 'order', id: '1', title: 'Big order', at: NOW - 5 * M },
      { model: 'customer', id: '2', title: 'Acme', at: NOW - D },
      { model: 'gone', id: '3', title: 'Unknown model' },
    ]);
    const button = mountButton();
    button.click();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(rows().map(row => [row.querySelector('.rec-title')!.textContent, row.querySelector('.rec-model')!.textContent, row.querySelector('.rec-at')?.textContent]))
      .toEqual([['Big order', 'Order', '5 minutes ago'], ['Acme', 'Customer', 'yesterday']]);
    expect(rows()[0].getAttribute('href')).toBe('/app/order/1');
    expect(rows()[0].querySelector('svg')).not.toBeNull();
    const time = rows()[0].querySelector('time')!;
    time.dispatchEvent(new Event('mouseenter'));
    expect(tipText()).toBe('04-11-2026 13:55 GST');
    await flush();
    expect(document.activeElement).toBe(rows()[0]);
  });

  it('moves with the arrow keys and closes on Escape, back to the button', async () => {
    prefs.recentRecords.set([{ model: 'order', id: '1', title: 'One', at: NOW }, { model: 'order', id: '2', title: 'Two', at: NOW }]);
    const button = mountButton();
    button.click();
    await flush();
    key(document.activeElement!, 'ArrowDown');
    expect(document.activeElement).toBe(rows()[1]);
    key(document.activeElement!, 'ArrowDown');
    expect(document.activeElement).toBe(rows()[0]);
    key(document.activeElement!, 'ArrowUp');
    expect(document.activeElement).toBe(rows()[1]);
    key(document.activeElement!, 'Escape');
    expect(pop()).toBeNull();
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('closes on a click outside and when a record is chosen', () => {
    prefs.recentRecords.set([{ model: 'order', id: '1', title: 'One', at: NOW }]);
    const button = mountButton();
    button.click();
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(pop()).toBeNull();
    button.click();
    rows()[0].addEventListener('click', event => event.preventDefault());
    rows()[0].click();
    expect(pop()).toBeNull();
  });

  it('shows one line when empty and clears the list', () => {
    const button = mountButton();
    button.click();
    expect(pop()!.textContent).toContain('No recent records yet.');
    expect(pop()!.querySelector('.rec-clear')).toBeNull();
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    prefs.recentRecords.set([{ model: 'order', id: '1', title: 'One', at: NOW }]);
    button.click();
    pop()!.querySelector<HTMLButtonElement>('.rec-clear')!.click();
    expect(prefs.recentRecords.peek()).toEqual([]);
    expect(pop()).toBeNull();
  });

  it('still lists entries from earlier builds that have no time', () => {
    prefs.recentRecords.set([{ model: 'order', id: '9', title: 'Old entry' }]);
    mountButton().click();
    expect(rows()).toHaveLength(1);
    expect(rows()[0].querySelector('.rec-title')!.textContent).toBe('Old entry');
    expect(rows()[0].querySelector('time')).toBeNull();
    expect(rows()[0].getAttribute('aria-label')).toBe('Old entry, Order');
  });
});
