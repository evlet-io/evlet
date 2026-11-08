// Light, dark and system themes: the tokens (both dark blocks the same, every pair readable in both
// themes, no colour literals in component styles), applying a theme, following the device live, and
// the choice saved as a Settings preference from the sidebar, the account menu and Settings.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyTheme, currentTheme, resolveTheme } from '../ui/theme';
import { readConfig } from '../app/config';
import { adminRoutes, byText, click, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy } from './server';

// Vitest hands CSS imports over empty, so the sources are read from disk (Node's own fs, no typings needed).
const node = (globalThis as any).process;
const fs = node.getBuiltinModule('node:fs');
// Vitest runs from frontend/.
const SRC = `${node.cwd()}/src/evlet/`;
const read = (path: string): string => fs.readFileSync(SRC + path, 'utf8');
const STYLES = read('ui/styles.css');
// Every stylesheet and every module outside the tests, as text.
const SOURCES: Record<string, string> = Object.fromEntries((fs.readdirSync(SRC, { recursive: true }) as string[])
  .map(path => path.split('\\').join('/')).filter(path => /\.(css|ts)$/.test(path) && !path.startsWith('tests/')).map(path => [path, read(path)]));

// ---------- tokens ----------

function block(css: string, opener: string): string {
  const start = css.indexOf(opener);
  if (start < 0) throw new Error(`No ${opener} block`);
  return css.slice(start + opener.length, css.indexOf('}', start));
}
const tokens = (body: string) => Object.fromEntries([...body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+?)\s*(?:;|$)/g)].map(match => [match[1], match[2]]));
const LIGHT = tokens(block(STYLES, ':root{'));
const SYSTEM_DARK = tokens(block(STYLES, '@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){'));
const DARK = tokens(block(STYLES, ':root[data-theme="dark"]{'));
const THEMES = { light: LIGHT, dark: { ...LIGHT, ...DARK } } as const;

function rgb(value: string): [number, number, number] {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (!hex) throw new Error(`Not a solid colour: ${value}`);
  const full = hex[1].length === 3 ? [...hex[1]].map(ch => ch + ch).join('') : hex[1];
  return [0, 2, 4].map(at => parseInt(full.slice(at, at + 2), 16)) as [number, number, number];
}
function luminance(value: string) {
  const [r, g, b] = rgb(value).map(channel => { const c = channel / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a: string, b: string) {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

const GROUNDS = ['--bg', '--bg-subtle', '--bg-muted', '--side-bg', '--surface'];
const TINTS = ['blue', 'green', 'amber', 'rose', 'violet', 'teal', 'slate'];
// [text or mark, background, minimum ratio]: 4.5:1 for text, 3:1 for icons, focus rings and borders that carry meaning.
const PAIRS: Array<[string, string, number]> = [
  ...['--fg', '--fg-2', '--fg-3'].flatMap(fg => GROUNDS.map(bg => [fg, bg, 4.5] as [string, string, number])),
  ...['--fg', '--fg-2', '--fg-3', '--accent'].map(fg => [fg, '--accent-soft', 4.5] as [string, string, number]),
  ...GROUNDS.map(bg => ['--accent', bg, 4.5] as [string, string, number]),
  ['--accent-fg', '--accent-solid', 4.5], ['--accent-fg', '--accent-solid-hover', 4.5],
  ['--inverse-fg', '--inverse-bg', 4.5], ['--inverse-ok', '--inverse-bg', 4.5],
  ...TINTS.flatMap(tint => [[`--t-${tint}`, `--t-${tint}-bg`, 4.5], [`--t-${tint}`, '--surface', 4.5], [`--t-${tint}`, '--bg', 4.5],
    ['--on-tint', `--t-${tint}`, 4.5]] as Array<[string, string, number]>),
  ...['--bg', '--surface', '--bg-muted'].flatMap(bg => [['--fg-4', bg, 3], ['--accent', bg, 3], ['--t-rose', bg, 3]] as Array<[string, string, number]>),
];

describe('theme tokens', () => {
  it('keeps the two dark blocks the same and gives every colour token a dark value', () => {
    expect(SYSTEM_DARK).toEqual(DARK);
    expect(block(STYLES, ':root{')).toContain('color-scheme:light');
    expect(block(STYLES, ':root[data-theme="dark"]{')).toContain('color-scheme:dark');
    // Fonts, sizes, radii and the theme pictures are the same in both themes.
    const constant = /^--(font|mono|r|r-sm|r-lg|sidebar-w|topbar-h|swatch-.*)$/;
    const colour = Object.keys(LIGHT).filter((name: string) => !constant.test(name));
    expect(colour.filter(name => !(name in DARK))).toEqual([]);
    expect(Object.keys(DARK).filter(name => !(name in LIGHT))).toEqual([]);
  });

  for (const [theme, values] of Object.entries(THEMES)) {
    it(`meets WCAG AA for text and marks in the ${theme} theme`, () => {
      const failing = PAIRS.map(([fg, bg, min]) => ({ pair: `${fg} on ${bg}`, ratio: Math.round(contrast(values[fg], values[bg]) * 100) / 100, min }))
        .filter(item => item.ratio < item.min);
      expect(failing).toEqual([]);
    });
  }

  it('has no colour literals in component styles or inline styles', () => {
    const literal = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(|(?<![\w-])(?:white|black)(?![\w-])/gi;
    const found: string[] = [];
    for (const [path, source] of Object.entries(SOURCES)) {
      if (path.endsWith('.css')) {
        let css = source.replace(/\/\*[\s\S]*?\*\//g, '');
        // The token blocks are the one place colours are written.
        if (path === 'ui/styles.css') css = css.slice(css.indexOf('color-scheme:dark}', css.indexOf(':root[data-theme="dark"]{')));
        for (const match of css.matchAll(literal)) found.push(`${path}: ${match[0]}`);
      } else if (!['ui/icon-data.ts', 'ui/theme.ts'].includes(path)) {
        // Inline styles and style properties in templates.
        for (const match of source.matchAll(/style=\S*?["'`][^"'`]*["'`]|\.style\.[a-zA-Z]+\s*=\s*[^;\n]+/g)) {
          for (const colour of match[0].matchAll(literal)) found.push(`${path}: ${colour[0]}`);
        }
      }
    }
    // The check covers the component styles.
    expect(Object.keys(SOURCES)).toEqual(expect.arrayContaining(['ui/styles.css', 'app/screens.css', 'app/settings.css', 'controls/controls.css', 'app/shell.ts']));
    expect(found).toEqual([]);
  });
});

// ---------- applying and following the device ----------

function device(dark: boolean) {
  const listeners = new Set<(event: { matches: boolean }) => void>();
  const query = { matches: dark, media: '(prefers-color-scheme: dark)', addEventListener: (_: string, listener: any) => listeners.add(listener), removeEventListener: vi.fn() };
  window.matchMedia = vi.fn(() => query) as any;
  return { set(next: boolean) { query.matches = next; listeners.forEach(listener => listener({ matches: next })); } };
}

// The app reads the device setting once per page: give it one this file can change.
const os = device(false);

afterEach(() => {
  os.set(false);
  stopAll();
  document.head.querySelectorAll('meta[name="theme-color"]').forEach(meta => meta.remove());
  document.cookie = 'evlet_theme=; max-age=0; path=/';
});

describe('applyTheme', () => {
  it('sets data-theme for a choice, leaves it off for system, and keeps the bar colour and cookie in step', () => {
    document.head.insertAdjacentHTML('beforeend', '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f4f5f8"><meta name="theme-color" media="(prefers-color-scheme: dark)" content="#14171c">');
    const bars = () => [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map(meta => meta.content);
    applyTheme('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(currentTheme()).toBe('dark');
    expect(bars()).toEqual(['#14171c', '#14171c']);
    expect(document.cookie).toContain('evlet_theme=dark');
    applyTheme('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(bars()).toEqual(['#f4f5f8', '#f4f5f8']);
    applyTheme('system');
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(currentTheme()).toBe('system');
    expect(bars()).toEqual(['#f4f5f8', '#14171c']);
    expect(document.cookie).toContain('evlet_theme=system');
    // Anything else counts as system.
    applyTheme('sepia' as any);
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it('resolves system to the device scheme', () => {
    expect(resolveTheme('light', 'dark')).toBe('light');
    expect(resolveTheme('dark', 'light')).toBe('dark');
    expect(resolveTheme('system', 'dark')).toBe('dark');
    expect(resolveTheme('system', 'light')).toBe('light');
  });

  it('reads the theme the server painted the page in from the config block', () => {
    const page = document.implementation.createHTMLDocument();
    page.head.innerHTML = '<script id="evlet-config" type="application/json">{"theme":"dark"}</script>';
    expect(readConfig(page).theme).toBe('dark');
    page.head.innerHTML = '<script id="evlet-config" type="application/json">{"theme":"neon"}</script>';
    expect(readConfig(page).theme).toBe('system');
    expect(readConfig(document.implementation.createHTMLDocument()).theme).toBe('system');
  });

  it('follows the device live', async () => {
    vi.resetModules();
    const before = window.matchMedia;
    const other = device(false);
    const { systemScheme } = await import('../ui/theme');
    const scheme = systemScheme();
    expect(scheme.get()).toBe('light');
    other.set(true);
    expect(scheme.get()).toBe('dark');
    other.set(false);
    expect(scheme.get()).toBe('light');
    window.matchMedia = before;
  });
});

// ---------- the choice in the app ----------

const withTheme = (theme: string) => { const value = copy(screen.settings_me); (value.body as any).prefs.theme = theme; return value; };
const routes = (theme: string, extra: Route[] = []): Route[] => [...extra, ...adminRoutes(), ['GET', /\/api\/_settings\/me$/, withTheme(theme)],
  ['PATCH', /\/api\/_settings\/me\/prefs$/, screen.settings_prefs]];
const bodies = (sent: any[]) => requests(sent, 'PATCH', /\/me\/prefs$/).map(item => item.body).filter(body => 'theme' in body);

describe('choosing a theme', () => {
  it('opens in the saved theme and switches from the sidebar and the account menu', async () => {
    const { host, sent } = start(routes('dark'), '/docs');
    await until(() => expect(host.querySelector('.side-foot .theme-btn')).not.toBeNull());
    expect(document.documentElement.dataset.theme).toBe('dark');
    const button = host.querySelector<HTMLButtonElement>('.side-foot .theme-btn')!;
    expect(button.getAttribute('aria-label')).toBe('Switch to light theme');
    click(button);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(button.getAttribute('aria-label')).toBe('Switch to dark theme');
    await until(() => expect(bodies(sent)).toEqual([{ theme: 'light' }]));
    click(host.querySelector('.me'));
    click(byText(document.body, '.pop [role="menuitem"]', 'Dark theme'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    await until(() => expect(bodies(sent)).toEqual([{ theme: 'light' }, { theme: 'dark' }]));
  });

  it('follows the device on system, and the button picks the other side of what shows', async () => {
    const { host, sent } = start(routes('system'), '/docs');
    await until(() => expect(host.querySelector('.side-foot .theme-btn')).not.toBeNull());
    expect(document.documentElement.dataset.theme).toBeUndefined();
    const button = host.querySelector<HTMLButtonElement>('.side-foot .theme-btn')!;
    os.set(true);
    expect(button.getAttribute('aria-label')).toBe('Switch to light theme');
    os.set(false);
    expect(button.getAttribute('aria-label')).toBe('Switch to dark theme');
    click(button);
    expect(document.documentElement.dataset.theme).toBe('dark');
    await until(() => expect(bodies(sent)).toEqual([{ theme: 'dark' }]));
  });

  it('puts the earlier theme back when the server refuses the change', async () => {
    const refused = { status: 422, body: { error: 'Choose one of the options.', fields: { theme: 'Choose one of the options.' } } };
    const { host } = start(routes('light', [['PATCH', /\/api\/_settings\/me\/prefs$/, refused as any]]), '/docs');
    await until(() => expect(host.querySelector('.side-foot .theme-btn')).not.toBeNull());
    expect(document.documentElement.dataset.theme).toBe('light');
    click(host.querySelector('.side-foot .theme-btn'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    await until(() => expect(document.documentElement.dataset.theme).toBe('light'));
  });

  it('offers Light, Match my device and Dark in Settings and saves the choice', async () => {
    const { host, sent } = start(routes('light'), '/settings/appearance');
    await until(() => expect(byText(host, '.set-choice', /^Dark/)).not.toBeNull());
    const choices = [...host.querySelectorAll<HTMLButtonElement>('.set-choices')][0].querySelectorAll<HTMLButtonElement>('.set-choice');
    expect([...choices].map(choice => text(choice.querySelector('strong')))).toEqual(['Light', 'Match my device', 'Dark']);
    expect([...choices].some(choice => choice.disabled)).toBe(false);
    expect([...choices].map(choice => choice.querySelector('.set-thumb')?.className)).toEqual(['set-thumb light', 'set-thumb system', 'set-thumb dark']);
    // Match my device shows half of each.
    expect([...choices[1].querySelectorAll('.set-thumb.system > .set-thumb')].map(node => node.className)).toEqual(['set-thumb light', 'set-thumb dark']);
    click(choices[2]);
    await until(() => expect(document.documentElement.dataset.theme).toBe('dark'));
    expect(choices[2].getAttribute('aria-pressed')).toBe('true');
    click(choices[1]);
    await until(() => expect(document.documentElement.dataset.theme).toBeUndefined());
    expect(bodies(sent)).toEqual([{ theme: 'dark' }, { theme: 'system' }]);
  });

  it('switches from the command palette', async () => {
    const { host, sent } = start(routes('light'), '/docs');
    await until(() => expect(host.querySelector('.side-foot .theme-btn')).not.toBeNull());
    host.querySelector<HTMLButtonElement>('.top-search .ts-search')!.click();
    const input = document.querySelector<HTMLInputElement>('.modal input')!;
    input.value = 'dark theme';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await until(() => expect(byText(document.body, '.modal [role="option"], .modal button', /Switch to dark theme/)).not.toBeNull());
    click(byText(document.body, '.modal [role="option"], .modal button', /Switch to dark theme/));
    expect(document.documentElement.dataset.theme).toBe('dark');
    await until(() => expect(bodies(sent)).toEqual([{ theme: 'dark' }]));
  });
});
