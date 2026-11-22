// Install metadata, the worker with its offline notice, the install offer, and a device-local, explicit opt-in
// for public static assets only (docs/ui.md#installation-and-offline-notice).
import { html } from '../dom';
import { cell, onCleanup } from '../reactive';
import { Button, IconButton, StateBanner, Switch } from '../ui/base';
import { safeStorage } from './prefs';
import { readConfig, type RuntimeConfig } from './config';

const WORKER = '/assets/evlet-sw.js', OPT_IN = 'evlet.static-cache', DISMISSED = 'evlet.install-offer';
const CONTROL = 'evlet-static-control', STATE = '/assets/evlet-cache-state';
interface InstallEvent extends Event { prompt(): Promise<void>; userChoice: Promise<{ outcome: string }> }
const install = cell<InstallEvent | null>(null), cacheOn = cell(false), busy = cell(false), problem = cell('');
const standalone = cell(false), dismissed = cell(false);
let started = false;

export const supportsInstall = (config: RuntimeConfig = readConfig()) =>
  ['evlet.desk', 'evlet.settings'].includes(config.frontend) && config.realm === 'desk' && config.base === '/app' && window.isSecureContext;

export function startPwa(config: RuntimeConfig = readConfig()): void {
  if (started || !supportsInstall(config)) return;
  started = true;
  // The manifest, and for iOS (which reads no manifest icons) the home-screen icon and standalone launch.
  for (const [tag, attributes] of [['link', { rel: 'manifest', href: '/assets/evlet.webmanifest' }],
    ['link', { rel: 'apple-touch-icon', href: '/assets/evlet-apple-touch-icon.png' }],
    ['meta', { name: 'mobile-web-app-capable', content: 'yes' }], ['meta', { name: 'apple-mobile-web-app-capable', content: 'yes' }],
    ['meta', { name: 'apple-mobile-web-app-status-bar-style', content: 'default' }]] as const) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    document.head.append(element);
  }
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); install.set(event as InstallEvent); });
  window.addEventListener('appinstalled', () => { install.set(null); standalone.set(true); });
  try {
    const query = window.matchMedia?.('(display-mode: standalone)');
    standalone.set(Boolean(query?.matches || (navigator as { standalone?: boolean }).standalone));
    query?.addEventListener?.('change', event => standalone.set(event.matches || standalone.peek()));
  } catch { /* An old browser without display-mode simply shows the offer. */ }
  try { dismissed.set(safeStorage()?.getItem(DISMISSED) === 'dismissed'); } catch { /* Blocked storage shows the offer again. */ }
  // The worker gives navigations an offline notice and is what browsers look for before offering to install. Its
  // static-asset cache stays off until the device setting below turns it on.
  if ('serviceWorker' in navigator) void navigator.serviceWorker.register(WORKER, { scope: '/', updateViaCache: 'none' }).catch(() => {});
  try {
    if (safeStorage()?.getItem(OPT_IN) === 'yes') void (async () => {
      try { if (await (await (await caches.open(CONTROL)).match(STATE))?.text() === 'enabled') await setStaticCache(true); } catch { /* Optional storage may be blocked. */ }
    })();
  } catch { /* Blocked storage never prevents startup. */ }
}

async function setStaticCache(on: boolean): Promise<void> {
  if (busy.peek()) return;
  problem.set('');
  if (!('serviceWorker' in navigator)) { problem.set('This browser does not support the offline notice.'); return; }
  busy.set(true);
  try {
    if (on) {
      const storage = safeStorage();
      if (!storage) throw new Error('Device storage is unavailable');
      storage.setItem(OPT_IN, 'yes');
      await (await caches.open(CONTROL)).put(STATE, new Response('enabled'));
      const registration = await navigator.serviceWorker.register(WORKER, { scope: '/', updateViaCache: 'none' });
      await installed(registration);
      // A worker still running since the setting was turned off reads the marker again (it is never trusted from the message).
      for (const worker of [registration.active, registration.waiting, registration.installing])
        worker?.postMessage({ type: 'EVLET_ENABLE_STATIC_CACHE' });
    } else {
      await (await caches.open(CONTROL)).put(STATE, new Response('disabled'));
      try { safeStorage()?.removeItem(OPT_IN); } catch { /* The durable disabled marker still prevents startup opt-in. */ }
      // The worker stays for the offline notice and installation; it only stops keeping assets.
      for (const registration of await navigator.serviceWorker.getRegistrations()) {
        const workers = [registration.active, registration.waiting, registration.installing].filter(Boolean) as ServiceWorker[];
        if (!workers.some(worker => new URL(worker.scriptURL).pathname === WORKER)) continue;
        workers.forEach(worker => worker.postMessage({ type: 'EVLET_DISABLE_STATIC_CACHE' }));
      }
      if ('caches' in window) for (const name of await caches.keys()) if (name.startsWith('evlet-static-') && name !== CONTROL) await caches.delete(name);
    }
    cacheOn.set(on);
  } catch {
    if (on) {
      try { safeStorage()?.removeItem(OPT_IN); await (await caches.open(CONTROL)).put(STATE, new Response('disabled')); } catch { /* Storage may be blocked. */ }
      cacheOn.set(false);
    }
    problem.set('This device could not change the offline notice setting. Try again.');
  } finally { busy.set(false); }
}

// register() resolves before a new worker has fetched its offline notice. Do not report it ready
// if installation then fails; an existing active/waiting version remains usable without takeover.
async function installed(registration: ServiceWorkerRegistration): Promise<void> {
  if (registration.active || registration.waiting) return;
  const worker = registration.installing;
  if (!worker) throw new Error('Worker unavailable');
  await new Promise<void>((resolve, reject) => {
    const finish = (error?: Error) => { clearTimeout(timer); worker.removeEventListener('statechange', changed); error ? reject(error) : resolve(); };
    const changed = () => {
      if (worker.state === 'installed' || worker.state === 'activated') finish();
      else if (worker.state === 'redundant') finish(new Error('Worker installation failed'));
    };
    const timer = setTimeout(() => finish(new Error('Worker installation timed out')), 15000);
    worker.addEventListener('statechange', changed); changed();
  });
}

/** The optional device features in Appearance; business records always require the server. */
export function PwaSettings(): Node | null {
  if (!supportsInstall()) return null;
  const online = cell(navigator.onLine);
  const changed = () => online.set(navigator.onLine);
  window.addEventListener('online', changed); window.addEventListener('offline', changed);
  onCleanup(() => { window.removeEventListener('online', changed); window.removeEventListener('offline', changed); });
  return html`<section class="set-sec"><div class="set-sec-h"><h2>On this device</h2>
    <p>${() => online.get() ? 'Records and changes need a connection to your workspace.' : 'You are offline. Keep editors with unsaved work open until the connection returns.'}</p></div><div class="set-rows">
    <div class="set-row"><div class="set-row-t"><strong>Install Evlet</strong><p>Open it from your home screen or desktop. If no button appears, use your browser’s Install or Add to Home Screen menu.</p></div>
      <div class="set-row-c">${() => install.get() ? Button({ label: 'Install', icon: 'download', onClick: async () => {
        const event = install.peek(); if (!event) return;
        try { await event.prompt(); await event.userChoice; install.set(null); }
        catch { problem.set('Use your browser’s Install or Add to Home Screen menu.'); }
      } }) : null}</div></div>
    <div class="set-row"><div class="set-row-t"><strong>Offline notice and static assets</strong><p>Optional on this device. Keeps only public application files and an offline notice. Records, sign-in pages and API responses are never stored; changes are never queued.</p></div>
      <div class="set-row-c">${Switch({ on: cacheOn, disabled: busy, ariaLabel: 'Offline notice and static assets', onChange: value => void setStaticCache(value) })}</div></div></div>
    <div class="set-sec-b"><p class="set-p">Updates take effect after you close existing Evlet tabs. Finish or keep your work first.</p>
    ${() => problem.get() ? html`<p class="set-p" role="alert">${problem}</p>` : null}</div></section>`;
}

export type InstallOfferKind = 'install' | 'ios' | null;

/** Which install offer the shell shows: the browser's own prompt where it has one, a one-line Add to Home Screen
 * hint on iOS (which has none), and nothing once dismissed on this device or when Evlet already runs installed. */
export function installOffer(state: { prompt: boolean; standalone: boolean; dismissed: boolean; ios: boolean }): InstallOfferKind {
  if (state.standalone || state.dismissed) return null;
  if (state.prompt) return 'install';
  return state.ios ? 'ios' : null;
}

export const isIos = (agent = navigator.userAgent, touch = navigator.maxTouchPoints) =>
  /iPad|iPhone|iPod/.test(agent) || (/Macintosh/.test(agent) && touch > 1);

/** The small "Install Evlet" offer at the top of the desk; dismissing it is remembered on this device. */
export function InstallOffer(): Node | null {
  if (!supportsInstall()) return null;
  const ios = isIos();
  const dismiss = () => {
    dismissed.set(true);
    try { safeStorage()?.setItem(DISMISSED, 'dismissed'); } catch { /* Hidden for this visit only. */ }
  };
  const close = IconButton({ icon: 'x', tip: 'Dismiss', size: 'small', onClick: dismiss });
  return html`<div class="ev-install">${() => {
    const kind = installOffer({ prompt: Boolean(install.get()), standalone: standalone.get(), dismissed: dismissed.get(), ios });
    if (kind === null) return null;
    if (kind === 'ios') return StateBanner({ tone: 'info', icon: 'download', title: 'Install Evlet', text: 'Tap Share, then Add to Home Screen.', actions: [close] });
    return StateBanner({ tone: 'info', icon: 'download', title: 'Install Evlet', text: 'Open it in its own window from your desktop or home screen.', actions: [
      Button({ label: 'Install', variant: 'primary', size: 'small', onClick: async () => {
        const event = install.peek(); if (!event) return;
        try { await event.prompt(); await event.userChoice; } catch { /* The browser menu still installs. */ }
        // A prompt can be shown once; the browser offers a new one on a later visit if Evlet was not installed.
        install.set(null);
      } }), close] });
  }}</div>`;
}
