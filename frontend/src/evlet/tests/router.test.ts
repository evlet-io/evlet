import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effect, scope } from '../reactive';
import { createRouter, type Params } from '../router';

const routes = { home: '/', list: '/m/:model', record: '/m/:model/:id', settings: '/settings' } as const;
let stops: Array<() => void> = [];
beforeEach(() => { history.replaceState(null, '', '/'); });
afterEach(() => { stops.splice(0).forEach(stop => stop()); document.body.innerHTML = ''; });

function started() {
  const router = createRouter(routes);
  stops.push(router.start());
  return router;
}

describe('router', () => {
  it('matches params and query, decoding values', () => {
    const router = createRouter(routes);
    expect(router.match('/m/invoice/42?tab=lines')).toEqual({ name: 'record', params: { model: 'invoice', id: '42' }, query: { tab: 'lines' }, url: '/m/invoice/42?tab=lines' });
    expect(router.match('/m/a%20b')).toMatchObject({ name: 'list', params: { model: 'a b' } });
    expect(router.match('/')?.name).toBe('home');
    expect(router.match('/nowhere')).toBeNull();
    expect(router.match('/m/%E0%A4%A')).toBeNull();
  });

  it('builds typed hrefs and encodes params', () => {
    const router = createRouter(routes);
    const params: Params<'/m/:model/:id'> = { model: 'sales order', id: '7' };
    expect(router.href('record', params, { tab: 'lines' })).toBe('/m/sales%20order/7?tab=lines');
    expect(() => router.href('record', { model: 'x', id: '' })).toThrow('needs id');
  });

  it('supports a base path', () => {
    const router = createRouter(routes, { base: '/app/' });
    expect(router.href('settings', {})).toBe('/app/settings');
    expect(router.match('/app/m/deal')?.params).toEqual({ model: 'deal' });
    expect(router.match('/m/deal')).toBeNull();
  });

  it('starts from the current location and updates the route cell on navigation', async () => {
    history.replaceState(null, '', '/m/deal');
    const router = started();
    const seen: Array<string | null> = [];
    stops.push(scope(() => effect(() => { seen.push(router.route.get()?.name ?? null); })));
    expect(await router.go('record', { model: 'deal', id: '3' })).toBe(true);
    expect(location.pathname).toBe('/m/deal/3');
    expect(seen).toEqual(['list', 'record']);
  });

  it('intercepts same-origin links that match a route, and only those', async () => {
    const router = started();
    document.body.innerHTML = `
      <a id="inside" href="/m/lead">Leads</a>
      <a id="api" href="/api/schema">API</a>
      <a id="blank" href="/m/lead" target="_blank">New tab</a>
      <a id="away" href="https://example.com/m/lead">Away</a>`;
    // Record whether the router handled the click, then stop jsdom from following the link.
    let handled = false;
    const record = (event: Event) => { handled = event.defaultPrevented; event.preventDefault(); };
    window.addEventListener('click', record);
    stops.push(() => window.removeEventListener('click', record));
    const click = (id: string, init: MouseEventInit = {}) => {
      document.getElementById(id)!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init }));
      return handled;
    };
    expect(click('inside')).toBe(true);
    await Promise.resolve();
    expect(router.route.get()?.params).toEqual({ model: 'lead' });
    expect(click('api')).toBe(false);
    expect(click('blank')).toBe(false);
    expect(click('away')).toBe(false);
    expect(click('inside', { metaKey: true })).toBe(false);
  });

  it('guards can stop a navigation, and confirmLeave asks only while dirty', async () => {
    const router = started();
    let dirty = true;
    const ask = vi.fn(async () => false);
    stops.push(router.confirmLeave(() => dirty, ask));
    expect(await router.go('settings', {})).toBe(false);
    expect(location.pathname).toBe('/');
    expect(ask).toHaveBeenCalledWith(expect.objectContaining({ name: 'settings' }));
    dirty = false;
    expect(await router.go('settings', {})).toBe(true);
    expect(ask).toHaveBeenCalledTimes(1);
  });

  it('confirmLeave does not ask when only the query changes, such as a record tab', async () => {
    const router = started();
    await router.go('record', { model: 'deal', id: '1' });
    const ask = vi.fn(async () => false);
    stops.push(router.confirmLeave(() => true, ask));
    expect(await router.go('record', { model: 'deal', id: '1' }, { tab: 'lines' }, { replace: true })).toBe(true);
    expect(location.search).toBe('?tab=lines');
    expect(ask).not.toHaveBeenCalled();
    expect(await router.go('record', { model: 'deal', id: '2' })).toBe(false);
    expect(await router.go('list', { model: 'deal' })).toBe(false);
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it('confirmLeave can say which navigations keep the record, such as a list with it in a side panel', async () => {
    const router = started();
    await router.go('list', { model: 'deal' }, { open: '1' });
    const ask = vi.fn(async () => false);
    stops.push(router.confirmLeave(() => true, ask, { stays: to => to?.name === 'list' && to.query.open === '1' }));
    expect(await router.go('list', { model: 'deal' }, { open: '1', q: 'acme' }, { replace: true })).toBe(true);
    expect(ask).not.toHaveBeenCalled();
    expect(await router.go('list', { model: 'deal' }, { open: '2' })).toBe(false);
    expect(await router.go('list', { model: 'deal' })).toBe(false);
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it('confirmLeave blocks closing the tab while dirty', () => {
    const router = started();
    stops.push(router.confirmLeave(() => true, () => false));
    const event = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent;
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('a guard registered in a scope is removed with it', async () => {
    const router = started();
    const dispose = scope(() => { router.guard(() => false); });
    expect(await router.go('settings', {})).toBe(false);
    dispose();
    expect(await router.go('settings', {})).toBe(true);
  });

  it('a throwing guard stops the navigation', async () => {
    const router = started();
    stops.push(router.guard(() => { throw new Error('nope'); }));
    expect(await router.go('settings', {})).toBe(false);
  });

  it('aborts the previous page signal when the route changes', async () => {
    const router = started();
    const signal = router.signal;
    await router.go('list', { model: 'deal' });
    expect(signal.aborted).toBe(true);
    expect(router.signal.aborted).toBe(false);
  });

  it('the latest of two concurrent navigations wins', async () => {
    const router = started();
    let release!: (value: boolean) => void;
    const slow = new Promise<boolean>(resolve => { release = resolve; });
    const remove = router.guard(to => to?.name === 'settings' ? slow : true);
    const first = router.go('settings', {});
    const second = router.go('list', { model: 'deal' });
    release(true);
    expect(await first).toBe(false);
    expect(await second).toBe(true);
    expect(router.route.get()?.name).toBe('list');
    remove();
  });

  it('follows the back button, and steps forward again when a guard refuses', async () => {
    const router = started();
    let pops = 0;
    const count = () => { pops++; };
    window.addEventListener('popstate', count);
    await router.go('list', { model: 'deal' });
    await router.go('record', { model: 'deal', id: '1' });
    history.back();
    await vi.waitFor(() => expect(pops).toBe(1));
    await vi.waitFor(() => expect(router.route.get()?.name).toBe('list'));

    const remove = router.guard(() => false);
    history.back();
    // The guard refused: the router steps forward again to the page it kept.
    await vi.waitFor(() => expect(pops).toBe(3));
    expect(location.pathname).toBe('/m/deal');
    expect(router.route.get()?.name).toBe('list');
    remove();
    window.removeEventListener('popstate', count);
  });
});
