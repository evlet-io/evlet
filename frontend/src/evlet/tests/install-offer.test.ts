import { afterEach, describe, expect, it, vi } from 'vitest';

const stops: Array<() => void> = [];
afterEach(() => {
  stops.splice(0).forEach(stop => stop()); document.body.innerHTML = ''; document.head.innerHTML = '';
  delete (navigator as any).serviceWorker; localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals();
});

async function desk(options: { standalone?: boolean; agent?: string } = {}) {
  vi.resetModules(); vi.stubGlobal('isSecureContext', true);
  const config = document.createElement('script'); config.id = 'evlet-config'; config.type = 'application/json';
  config.textContent = JSON.stringify({ frontend: 'evlet.desk', realm: 'desk', base: '/app', api: '/api' }); document.body.append(config);
  vi.spyOn(window, 'matchMedia').mockImplementation(query => ({ matches: query === '(display-mode: standalone)' && Boolean(options.standalone),
    media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }) as any);
  if (options.agent) vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(options.agent);
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { register: vi.fn().mockResolvedValue({}) } });
  const pwa = await import('../app/pwa'), { mount } = await import('../dom');
  pwa.startPwa();
  const host = document.createElement('div'); document.body.append(host);
  stops.push(mount(host, () => pwa.InstallOffer()));
  const offer = () => host.querySelector('.inv-banner');
  const prompt = () => {
    const event = Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
      prompt: vi.fn().mockResolvedValue(undefined), userChoice: Promise.resolve({ outcome: 'accepted' }) });
    window.dispatchEvent(event);
    return event;
  };
  return { pwa, host, offer, prompt };
}

describe('install offer', () => {
  it('decides between the browser prompt, the iOS hint and nothing', async () => {
    const { installOffer, isIos } = await import('../app/pwa');
    const base = { prompt: true, standalone: false, dismissed: false, ios: false };
    expect(installOffer(base)).toBe('install');
    expect(installOffer({ ...base, standalone: true })).toBeNull();
    expect(installOffer({ ...base, dismissed: true })).toBeNull();
    expect(installOffer({ ...base, prompt: false })).toBeNull();
    expect(installOffer({ ...base, prompt: false, ios: true })).toBe('ios');
    expect(installOffer({ ...base, prompt: false, ios: true, standalone: true })).toBeNull();
    expect(isIos('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', 5)).toBe(true);
    expect(isIos('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 5)).toBe(true);  // iPadOS asks for the desktop site
    expect(isIos('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 0)).toBe(false);
  });

  it('appears only after the browser offers to install, and prompts once', async () => {
    const made = await desk();
    expect(made.offer()).toBeNull();
    const event = made.prompt();
    expect(event.defaultPrevented).toBe(true);
    expect(made.offer()?.textContent).toContain('Install Evlet');
    made.host.querySelector<HTMLButtonElement>('.btn.primary')!.click();
    await vi.waitFor(() => expect(made.offer()).toBeNull());
    expect(event.prompt).toHaveBeenCalledOnce();
  });

  it('remembers a dismissal on this device', async () => {
    let made = await desk(); made.prompt();
    made.host.querySelector<HTMLButtonElement>('[aria-label="Dismiss"]')!.click();
    expect(made.offer()).toBeNull();
    expect(localStorage.getItem('evlet.install-offer')).toBe('dismissed');
    stops.splice(0).forEach(stop => stop());
    made = await desk(); made.prompt();
    expect(made.offer()).toBeNull();
  });

  it('hides once Evlet runs installed or is installed from the browser menu', async () => {
    let made = await desk({ standalone: true }); made.prompt();
    expect(made.offer()).toBeNull();
    stops.splice(0).forEach(stop => stop());
    made = await desk(); made.prompt();
    expect(made.offer()).not.toBeNull();
    window.dispatchEvent(new Event('appinstalled'));
    expect(made.offer()).toBeNull();
  });

  it('shows iOS a one-line Add to Home Screen hint without an install button', async () => {
    const made = await desk({ agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1' });
    expect(made.offer()?.textContent).toContain('Add to Home Screen');
    expect(made.host.querySelector('.btn.primary')).toBeNull();
  });

  it('is not offered outside the bundled desk', async () => {
    vi.resetModules(); vi.stubGlobal('isSecureContext', true);
    const config = document.createElement('script'); config.id = 'evlet-config'; config.type = 'application/json';
    config.textContent = JSON.stringify({ frontend: 'evlet.settings', realm: 'portal', base: '/app', api: '/api' }); document.body.append(config);
    const pwa = await import('../app/pwa');
    expect(pwa.InstallOffer()).toBeNull();
  });
});
