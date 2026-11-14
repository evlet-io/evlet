// Explicitly enabled static-asset cache. Never store sessions, API results, authenticated HTML,
// arbitrary URLs or mutations. A new version waits for existing tabs to close normally.
const PREFIX = 'evlet-static-';
const CACHE = `${PREFIX}v1`;
const CONTROL = `${PREFIX}control`, STATE = '/assets/evlet-cache-state';
const OFFLINE = '/assets/evlet-offline.html';
const MAX_ASSETS = 64;
let enabled;
async function isEnabled() {
  if (enabled !== undefined) return enabled;
  try {
    const marker = await (await caches.open(CONTROL)).match(STATE);
    enabled = (await marker?.text()) === 'enabled';
  }
  catch { enabled = false; }
  return enabled;
}

const staticAsset = url => !url.search && (
  /^\/assets\/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(?:js|css|woff2)$/.test(url.pathname) ||
  url.pathname === '/assets/evlet-icon-blue.svg'
);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    // This one HTML file is fixed public content, served without runtime/session configuration.
    const response = await fetch(OFFLINE, { credentials: 'omit', cache: 'reload' });
    if (!response.ok || response.redirected) throw new Error('Offline notice unavailable');
    await (await caches.open(CACHE)).put(OFFLINE, response);
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) if (name.startsWith(PREFIX) && name !== CACHE && name !== CONTROL) await caches.delete(name);
  })());
});

self.addEventListener('message', event => {
  if (event.data?.type !== 'EVLET_DISABLE_STATIC_CACHE') return;
  enabled = false;
  event.waitUntil((async () => {
    await (await caches.open(CONTROL)).put(STATE, new Response('disabled'));
    for (const name of await caches.keys()) if (name.startsWith(PREFIX) && name !== CONTROL) await caches.delete(name);
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (enabled === false || request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    if (!/^\/(?:app|docs|settings|controls)(?:\/|$)/.test(url.pathname) && url.pathname !== '/login') return;
    // Successful HTML responses, including sign-in and error pages, are never copied to Cache API.
    event.respondWith((async () => {
      if (!await isEnabled()) return fetch(request);
      try { return await fetch(request); } catch {
        try { const notice = await caches.match(OFFLINE); if (notice) return notice; } catch { /* Storage may be unavailable. */ }
        return new Response('Connect to open Evlet.', { status: 503, headers: { 'Content-Type': 'text/plain' } });
      }
    })());
    return;
  }
  if (!staticAsset(url)) return;
  event.respondWith((async () => {
    if (!await isEnabled()) return fetch(request);
    let cache;
    try { cache = await caches.open(CACHE); const known = await cache.match(url.href); if (known) return known; } catch { /* Storage is optional. */ }
    // Assets need no cookies or Authorization header; never retain a redirected response.
    const response = await fetch(url.href, { credentials: 'omit' });
    if (cache && enabled && response.ok && !response.redirected && response.type !== 'opaque') {
      try {
        await cache.put(url.href, response.clone());
        const keys = await cache.keys();
        const assets = keys.filter(key => new URL(key.url).pathname !== OFFLINE);
        for (const key of assets.slice(0, Math.max(0, assets.length - MAX_ASSETS))) await cache.delete(key);
      } catch { /* Full/blocked storage must not prevent a successful online response. */ }
    }
    return response;
  })());
});
