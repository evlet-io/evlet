// Install metadata and a device-local, explicit opt-in for public static assets only.
import { html } from '../dom';
import { cell, onCleanup } from '../reactive';
import { Button, Switch } from '../ui/base';
import { safeStorage } from './prefs';
import { readConfig, type RuntimeConfig } from './config';

const WORKER = '/assets/evlet-sw.js', OPT_IN = 'evlet.static-cache';
const CONTROL = 'evlet-static-control', STATE = '/assets/evlet-cache-state';
interface InstallEvent extends Event { prompt(): Promise<void>; userChoice: Promise<{ outcome: string }> }
const install = cell<InstallEvent | null>(null), cacheOn = cell(false), busy = cell(false), problem = cell('');
let started = false;

export const supportsInstall = (config: RuntimeConfig = readConfig()) =>
  ['evlet.desk', 'evlet.settings'].includes(config.frontend) && config.realm === 'desk' && config.base === '/app' && window.isSecureContext;

export function startPwa(config: RuntimeConfig = readConfig()): void {
  if (started || !supportsInstall(config)) return;
  started = true;
  const link = document.createElement('link');
  link.rel = 'manifest'; link.href = '/assets/evlet.webmanifest'; document.head.append(link);
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); install.set(event as InstallEvent); });
  window.addEventListener('appinstalled', () => install.set(null));
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
    } else {
      await (await caches.open(CONTROL)).put(STATE, new Response('disabled'));
      try { safeStorage()?.removeItem(OPT_IN); } catch { /* The durable disabled marker still prevents startup opt-in. */ }
      for (const registration of await navigator.serviceWorker.getRegistrations()) {
        const workers = [registration.active, registration.waiting, registration.installing].filter(Boolean) as ServiceWorker[];
        if (!workers.some(worker => new URL(worker.scriptURL).pathname === WORKER)) continue;
        workers.forEach(worker => worker.postMessage({ type: 'EVLET_DISABLE_STATIC_CACHE' }));
        await registration.unregister();
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
