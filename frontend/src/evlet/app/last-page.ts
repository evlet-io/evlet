// The last main-app page a person had open in this browser, so Controls can link straight back to it.
// The app shell writes it on every page change and Controls reads it. It lives in localStorage under
// `evlet.last_app.<user>` and holds only a same-origin path and the app's name. When there is none,
// the link goes to `/`, which opens the person's start page.
//
//   rememberAppPage('owner', '/app/Customer?q=acme', 'People');
//   backToApp('owner');  // { href: '/app/Customer?q=acme', app: 'People' }

export interface AppPage { href: string; app: string | null }

const KEY = 'evlet.last_app.';
// A path on this origin: one leading slash, no backslashes or spaces, and not Controls or the API.
const SAFE = /^\/(?![/\\])[^\s\\]*$/;
const OUTSIDE = /^\/(controls|api|_)(\/|$|\?|#)/i;

function store(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

const usable = (path: unknown): path is string => typeof path === 'string' && path.length < 2048 && SAFE.test(path) && !OUTSIDE.test(path);

/** Remember the page the app shows now. Paths outside the app are ignored. */
export function rememberAppPage(user: string, path: string, app: string) {
  if (!user || !usable(path)) return;
  try { store()?.setItem(KEY + user, JSON.stringify({ path, app })); } catch { /* storage full or blocked */ }
}

/** Where "Back to app" goes for this person: their last app page, or the start page. */
export function backToApp(user: string): AppPage {
  let saved: { path?: unknown; app?: unknown } = {};
  try { saved = JSON.parse(store()?.getItem(KEY + user) || '{}') ?? {}; } catch { /* the start page applies */ }
  const app = typeof saved.app === 'string' && saved.app.trim() ? saved.app.trim().slice(0, 60) : null;
  return { href: usable(saved.path) ? saved.path : '/', app };
}
