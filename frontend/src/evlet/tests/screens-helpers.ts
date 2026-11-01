// Helpers for the screen tests: the whole app mounted on jsdom's location, its router under /ui,
// and a fetch stub that replays responses recorded from the real ASGI routes (api-fixture.json,
// screens-fixture.json) or, for later engine phases, the provisional fixtures.
import { vi } from 'vitest';
import { mount } from '../dom';
import { App } from '../app/app';
import { AppClient } from '../app/client';
import { appRouter, type AppRouter } from '../app/context';
import { closeDialogs, closePopover } from '../ui/layers';
import { hideTip } from '../ui/tooltip';
import base from './api-fixture.json';
import screens from './screens-fixture.json';
import provisional from './provisional/invoice-fixture.json';
import { copy, server, type Recorded, type Sent } from './server';

export const recorded = base as Record<string, Recorded>;
export const screen = screens as Record<string, Recorded>;
export const invoice = provisional as Record<string, Recorded>;

type Reply = Recorded | ((sent: Sent) => Recorded | Promise<Recorded>);
export type Route = [method: string, path: RegExp, reply: Reply];

const running: Array<() => void> = [];

/** The routes every signed-in admin screen needs: session, schema with order, customer, contact. */
export const adminRoutes = (): Route[] => [
  ['GET', /\/api\/auth\/session$/, recorded.session],
  ['GET', /\/api\/schema$/, screen.schema],
];

export function start(routes: Route[], path = '/ui/docs', options: { storage?: Storage | null } = {}) {
  window.history.replaceState(null, '', path);
  const stub = server(routes.map(([method, pattern, reply]) => [method, pattern, typeof reply === 'function' ? reply : () => copy(reply)]) as any);
  const api = new AppClient({ fetch: stub.fetch });
  const router: AppRouter = appRouter({ base: '/ui' });
  const stopRouter = router.start();
  const host = document.createElement('div');
  host.id = 'evlet-app';
  document.body.append(host);
  const dispose = mount(host, () => App({ router, api, storage: options.storage ?? null, dev: true }));
  running.push(() => { dispose(); stopRouter(); });
  return { host, router, api, sent: stub.sent };
}

export function stopAll() {
  closePopover();
  closeDialogs();
  hideTip();
  running.splice(0).forEach(stop => stop());
  document.body.innerHTML = '';
  delete document.documentElement.dataset.theme;
}

/** Wait until the assertion passes (requests and timers settle). */
export const until = (check: () => void, timeout = 2000) => vi.waitFor(check, { timeout, interval: 5 });

export const text = (node: Element | null | undefined) => (node?.textContent ?? '').replace(/\s+/g, ' ').trim();
export const lastToast = () => text([...document.querySelectorAll('.toast')].pop());
export const requests = (sent: Sent[], method: string, pattern: RegExp) => sent.filter(item => item.method === method && pattern.test(item.url));
export const byText = <T extends Element = HTMLElement>(root: ParentNode, selector: string, wanted: string | RegExp) =>
  [...root.querySelectorAll<T>(selector)].find(node => typeof wanted === 'string' ? text(node) === wanted : wanted.test(text(node))) ?? null;

export function click(element: Element | null) {
  if (!element) throw new Error('Nothing to click');
  (element as HTMLElement).click();
}

export function press(target: Element | Document, key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

export function typeInto(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}
