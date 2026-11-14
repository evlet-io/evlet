// A navigation hint only, never a credential. The provider always returns to /login;
// the existing Controls/OAuth destination can survive that round trip in this tab.
const KEY = 'evlet.oidc-next';
const MAX_AGE = 5 * 60 * 1000;
export function safeSignInNext(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 2048 || /[\u0000-\u0020\u007f\\]/.test(value)) return null;
  return /^\/controls(\/[a-z]+)?$/.test(value) || /^\/_\/oauth\/authorize\?[^#]*$/.test(value) ? value : null;
}
export function rememberOidcNext(value: unknown) {
  try {
    sessionStorage.removeItem(KEY);
    const next = safeSignInNext(value);
    if (next) sessionStorage.setItem(KEY, JSON.stringify({ next, at: Date.now() }));
  } catch { /* Storage disabled: sign-in still works and lands at the normal start page. */ }
}
export function takeOidcNext(): string | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    if (!raw || raw.length > 2300) return null;
    const value = JSON.parse(raw), age = Date.now() - value.at;
    return typeof value.at === 'number' && age >= 0 && age <= MAX_AGE ? safeSignInNext(value.next) : null;
  } catch { return null; }
}
