// Light, dark or the device's setting. styles.css holds the tokens: light on :root, dark under
// prefers-color-scheme (unless the page says light) and under [data-theme="dark"]. "system" leaves
// data-theme off, so the stylesheet follows the device and changes with it live, without script.
//
// The choice is a Settings preference kept on the server (docs/settings.md). The server writes
// data-theme on <html> when it serves a page, so the first paint is already right; applyTheme keeps
// the page in step after that and leaves a small cookie hint (evlet_theme) so the sign-in page, which
// has no signed-in user, opens in the same theme. The cookie holds only light, dark or system.

import { cell, type Cell } from '../reactive';

export type Theme = 'system' | 'light' | 'dark';
export type Scheme = 'light' | 'dark';

export const THEMES: readonly Theme[] = ['light', 'dark', 'system'];
export const THEME_COOKIE = 'evlet_theme';
const QUERY = '(prefers-color-scheme: dark)';
// The browser bar colour of each theme (the sidebar ground).
const BAR: Record<Scheme, string> = { light: '#f4f5f8', dark: '#14171c' };

export const isTheme = (value: unknown): value is Theme => THEMES.includes(value as Theme);

function media(): MediaQueryList | null {
  try { return typeof window === 'undefined' ? null : window.matchMedia?.(QUERY) ?? null; } catch { return null; }
}

let system: Cell<Scheme> | null = null;

/** The device's scheme, updated live when the operating system setting changes. */
export function systemScheme(): Cell<Scheme> {
  if (system) return system;
  const query = media();
  const value = cell<Scheme>(query?.matches ? 'dark' : 'light', { name: 'theme.system' });
  const changed = (event: { matches: boolean }) => value.set(event.matches ? 'dark' : 'light');
  // Older Safari has only addListener.
  if (query?.addEventListener) query.addEventListener('change', changed); else query?.addListener?.(changed);
  return system = value;
}

/** The scheme a theme shows now: an explicit choice, or the device's for "system". */
export const resolveTheme = (theme: Theme, device: Scheme = systemScheme().get()): Scheme => theme === 'system' ? device : theme;

/** The theme this page was served with: the data-theme the server wrote, else "system". */
export function currentTheme(root: HTMLElement = document.documentElement): Theme {
  const value = root.dataset.theme;
  return value === 'light' || value === 'dark' ? value : 'system';
}

/** Show a theme on the page. An explicit choice wins over the device in both directions. */
export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement) {
  if (!isTheme(theme)) theme = 'system';
  if (theme === 'system') delete root.dataset.theme; else root.dataset.theme = theme;
  const doc = root.ownerDocument;
  for (const meta of Array.from(doc.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'))) {
    const scheme = theme === 'system' ? ((meta.getAttribute('media') ?? '').includes('dark') ? 'dark' : 'light') : theme;
    meta.content = BAR[scheme];
  }
  try {
    const secure = doc.location?.protocol === 'https:' ? '; secure' : '';
    doc.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=31536000; samesite=lax${secure}`;
  } catch { /* cookies blocked */ }
}
