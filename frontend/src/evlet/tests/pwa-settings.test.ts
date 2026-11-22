import { afterEach, describe, expect, it, vi } from 'vitest';

const stops: Array<() => void> = [];
afterEach(() => {
  stops.splice(0).forEach(stop => stop()); document.body.innerHTML = '';
  document.head.querySelectorAll('link[rel="manifest"],link[rel="apple-touch-icon"],meta[name$="web-app-capable"],meta[name^="apple-"]').forEach(link => link.remove());
  delete (navigator as any).serviceWorker; localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals();
});

async function page() {
  vi.resetModules(); vi.stubGlobal('isSecureContext', true);
  const marker = new Map<string, Response>();
  const cache = { put: vi.fn(async (key: string, value: Response) => marker.set(key, value)), match: vi.fn(async (key: string) => marker.get(key)?.clone()) };
  const caches = { open: vi.fn(async () => cache), keys: vi.fn(async () => ['evlet-static-control', 'evlet-static-v1', 'other-app']), delete: vi.fn() };
  vi.stubGlobal('caches', caches);
  const registration = { active: { scriptURL: 'https://evlet.test/assets/evlet-sw.js', postMessage: vi.fn() }, waiting: null, installing: null, unregister: vi.fn() };
  const serviceWorker = { register: vi.fn().mockResolvedValue(registration), getRegistrations: vi.fn().mockResolvedValue([registration]) };
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: serviceWorker });
  const pwa = await import('../app/pwa'), { mount } = await import('../dom');
  const host = document.createElement('div'); document.body.append(host);
  stops.push(mount(host, () => pwa.PwaSettings()));
  return { pwa, host, cache, marker, caches, serviceWorker, registration, toggle: () => host.querySelector<HTMLButtonElement>('[aria-label="Offline notice and static assets"]')! };
}

describe('optional device settings', () => {
  it('adds manifest and iOS metadata and registers the worker without turning on the asset cache', async () => {
    const made = await page(); made.pwa.startPwa();
    expect(document.querySelector('link[rel="manifest"]')?.getAttribute('href')).toBe('/assets/evlet.webmanifest');
    expect(document.querySelector('link[rel="apple-touch-icon"]')?.getAttribute('href')).toBe('/assets/evlet-apple-touch-icon.png');
    expect(document.querySelector('meta[name="apple-mobile-web-app-capable"]')?.getAttribute('content')).toBe('yes');
    expect(made.serviceWorker.register).toHaveBeenCalledExactlyOnceWith('/assets/evlet-sw.js', { scope: '/', updateViaCache: 'none' });
    expect(made.marker.size).toBe(0); expect(localStorage.getItem('evlet.static-cache')).toBeNull();
    expect(made.toggle().getAttribute('aria-checked')).toBe('false');
    expect(made.pwa.supportsInstall({ frontend: 'evlet.desk', realm: 'desk', base: '/another' } as any)).toBe(false);
    expect(made.pwa.supportsInstall({ frontend: 'evlet.settings', realm: 'portal', base: '/app' } as any)).toBe(false);
  });

  it('keeps device controls on a direct bundled desk settings load, excluding portal and custom frontends', async () => {
    // Actual /settings/appearance config uses evlet.settings, unlike SPA entry from /app.
    const config = document.createElement('script'); config.id = 'evlet-config'; config.type = 'application/json';
    config.textContent = JSON.stringify({ frontend: 'evlet.settings', realm: 'desk', base: '/app', api: '/api' }); document.body.append(config);
    const made = await page(); made.pwa.startPwa();
    expect(made.toggle()).not.toBeNull(); expect(document.querySelector('link[rel="manifest"]')).not.toBeNull();
    expect(made.pwa.supportsInstall({ frontend: 'evlet.settings', realm: 'portal', base: '/app' } as any)).toBe(false);
    expect(made.pwa.supportsInstall({ frontend: 'custom.frontend', realm: 'desk', base: '/app' } as any)).toBe(false);
    expect(made.serviceWorker.register).toHaveBeenCalledOnce(); expect(made.marker.size).toBe(0);
  });

  it('does not install a worker when the device cannot persist explicit opt-in', async () => {
    const made = await page(); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    made.toggle().click();
    await vi.waitFor(() => expect(made.host.querySelector('[role="alert"]')).not.toBeNull());
    expect(made.serviceWorker.register).not.toHaveBeenCalled();
    expect(made.toggle().getAttribute('aria-checked')).toBe('false');
  });

  it('does not report the offline notice enabled when worker installation fails', async () => {
    const made = await page();
    const worker = Object.assign(new EventTarget(), { state: 'installing' });
    made.serviceWorker.register.mockResolvedValueOnce({ active: null, waiting: null, installing: worker });
    made.toggle().click();
    await vi.waitFor(() => expect(made.serviceWorker.register).toHaveBeenCalledOnce());
    worker.state = 'redundant'; worker.dispatchEvent(new Event('statechange'));
    await vi.waitFor(() => expect(made.host.querySelector('[role="alert"]')).not.toBeNull());
    expect(made.toggle().getAttribute('aria-checked')).toBe('false');
    expect(localStorage.getItem('evlet.static-cache')).toBeNull();
    expect(await made.marker.get('/assets/evlet-cache-state')!.text()).toBe('disabled');
  });

  it('serializes toggles and keeps a durable opt-out marker, keeping the worker for the offline notice', async () => {
    const made = await page(); let finish!: (value: unknown) => void;
    made.serviceWorker.register.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    made.toggle().click();
    await vi.waitFor(() => expect(made.serviceWorker.register).toHaveBeenCalledTimes(1));
    expect(made.toggle().disabled).toBe(true); made.toggle().click();
    expect(made.serviceWorker.getRegistrations).not.toHaveBeenCalled();
    finish(made.registration);
    await vi.waitFor(() => expect(made.toggle().getAttribute('aria-checked')).toBe('true'));
    expect(made.registration.active.postMessage).toHaveBeenCalledExactlyOnceWith({ type: 'EVLET_ENABLE_STATIC_CACHE' });
    made.toggle().click();
    await vi.waitFor(() => expect(made.toggle().getAttribute('aria-checked')).toBe('false'));
    expect(await made.marker.get('/assets/evlet-cache-state')!.text()).toBe('disabled');
    expect(made.registration.active.postMessage).toHaveBeenLastCalledWith({ type: 'EVLET_DISABLE_STATIC_CACHE' });
    expect(made.registration.unregister).not.toHaveBeenCalled();
    expect(made.caches.delete).toHaveBeenCalledExactlyOnceWith('evlet-static-v1');
    expect(localStorage.getItem('evlet.static-cache')).toBeNull();
  });
});
