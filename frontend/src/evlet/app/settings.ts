// Settings (/settings): the personal pages, each app's settings and the settings search. The desk
// shows it inside the shell; a portal host shows the portal subset (profile, password and
// notifications) on its own. The server checks every change; this page only shows its answers.

import { html, match, when, type Child } from '../dom';
import { ApiError, ValidationError, type ApiClient, type User } from '../api';
import { cell, derive, getOwner, onCleanup, runWithOwner, untrack, type Cell } from '../reactive';
import { formatDate, formatDateTime, setRegion, type DateFormat, type NumberFormat } from '../model/format';
import type { FieldSchema } from '../model/schema';
import { Avatar, Button, Checkbox, Icon, Switch } from '../ui/base';
import { DialogFooter, copyText, openModal, toast } from '../ui/layers';
import { Dropdown } from '../ui/pickers';
import { applyTheme, type Theme } from '../ui/theme';
import { humanize, InputError, kindOf } from '../ui/kinds';
import type { UnsavedInfo } from './client';
import './settings.css';
import { PwaSettings } from './pwa';

export interface Me {
  user: User;
  portal: boolean;
  pages: string[];
  apps: Array<{ id: string; title: string; can_edit: boolean; labels: string[] }>;
  last_page: string;
  controls: boolean;
  /** May use the workspace override routes (/api/_controls/workspaces): the same check those routes make. */
  customize_workspaces: boolean;
  development: boolean;
  /** The built-in System Administrator: only its email address can change in its profile. */
  builtin?: boolean;
  profile: { full_name: string; short_name: string; title: string; phone: string; email: string; signature: string; photo: boolean };
  prefs: { theme: Theme; density: 'comfortable' | 'compact'; text: 'default' | 'large'; start: string; counts: boolean; lang: string; tz: string; date: DateFormat; num: NumberFormat; week: string;
    /** Enter moves to the next field in forms (off by default). */ enter_next?: boolean;
    /** Keep unsaved changes when I leave a record; missing means on. */
    keep_unsaved?: boolean };
  /** Unsaved changes kept on leave: the person's limits and how many entries they have (app/unsaved.ts). */
  unsaved?: UnsavedInfo;
  notifications: { events: Array<{ group: string; items: Array<{ key: string; label: string; locked: boolean; channels: [boolean, boolean] }> }>; quiet: { on: boolean; from: string; to: string } };
  security: {
    password_min: number;
    two_step: { on: boolean; since: number | null; agent: string; codes: number; required: boolean; available: boolean; verified: boolean };
    sessions: Array<{ id: string; current: boolean; started: number | null; expires: number; agent: string; realm: string }>;
    /** Active API keys, for the choice to revoke them with a password change. */
    api_keys?: number;
  };
}

interface AppField extends FieldSchema { value: unknown; default: unknown }
interface AppState { id: string; title: string; editors: string[]; can_edit: boolean; sections: Array<{ title: string; fields: AppField[] }>; preferences: AppField[] }

export interface SettingsContext {
  api: ApiClient;
  me: Cell<Me>;
  /** Pages the start page can be: [value, label]. */
  starts: Array<[string, string]>;
  /** The address of a settings page ('profile', 'app/billing'). */
  href(page: string): string;
  go(page: string): void;
  /** Called after a change that the shell shows (name, photo, density, region). */
  changed(): void;
  photo: Cell<number>;
}

export const PAGES: Record<string, [title: string, icon: string, group: string]> = {
  profile: ['Profile', 'user', 'Your account'], appearance: ['Appearance & region', 'sun', 'Your account'],
  security: ['Sign-in & security', 'lock', 'Your account'], notifications: ['Notifications', 'bell', 'Your account'],
  email: ['Your email', 'mail', 'Connections'], ai: ['AI', 'sparkles', 'Connections'], keys: ['API keys', 'hash', 'Connections'],
};
const INDEX: Record<string, string[]> = {
  profile: ['Photo', 'Full name', 'Short name', 'Job title', 'Mobile', 'Email', 'Email signature', 'Username'],
  appearance: ['Theme', 'Density', 'Text size', 'Start page', 'Record counts', 'Enter moves to the next field', 'Keep unsaved changes', 'Language', 'Time zone', 'Date format', 'Numbers', 'Week starts on'],
  security: ['Password', 'Two-step sign-in', 'Backup codes', 'Sessions', 'Sign out everywhere'],
  notifications: ['Notifications', 'Quiet hours'],
  email: ['Your email', 'Mailbox'],
  ai: ['Eva', 'MCP', 'Claude', 'AI assistants', 'Approvals', 'Connectors'],
  keys: ['API keys', 'Scripts', 'Bearer token'],
};
const ZONES: Array<[string, string]> = [['Asia/Dubai', 'Gulf Standard Time (UTC+4)'], ['Asia/Riyadh', 'Arabia Standard Time (UTC+3)'],
  ['Asia/Kolkata', 'India Standard Time (UTC+5:30)'], ['Europe/London', 'London (UTC+0)'], ['UTC', 'UTC']];

/** Read the signed-in person's settings; null when the server has none (an older server). */
export async function loadMe(api: ApiClient): Promise<Me | null> {
  try { return (await api.request<Me>('GET', '/api/_settings/me')).data; }
  catch { return null; }
}

/** Apply the theme, region and language everywhere: lists, forms and the page direction. Without a
 * person (signed out) the theme stays as it is, so the sign-in page keeps this device's theme. */
export function applyMe(me: Me | null) {
  if (me) applyTheme(me.prefs.theme);
  setRegion(me ? { date: me.prefs.date, number: me.prefs.num, zone: me.prefs.tz } : {});
  const root = document.documentElement;
  root.lang = me?.prefs.lang ?? 'en';
  if (me?.prefs.lang === 'ar') root.dir = 'rtl'; else root.removeAttribute('dir');
}

export const displayName = (me: Me | null, fallback: string) => me?.profile.short_name || me?.profile.full_name || fallback;
export const photoUrl = (version: number) => `/api/_settings/me/photo?v=${version}`;

/** The person's photo, or their initials. */
export function PersonMark(name: () => string, photo: () => boolean, version: Cell<number>, size: 'md' | 'lg' | 'xl' = 'md'): Node {
  return html`${() => photo() ? html`<img class=${`avatar ${size === 'md' ? '' : size} set-photo`} alt="" src=${photoUrl(version.get())}>` : Avatar({ name: name(), size })}`;
}

/** Choose a theme from the account menu, the sidebar or the palette: it shows at once and is saved like
 * the Appearance page saves it. A refused save puts the earlier theme back. */
export async function chooseTheme(api: ApiClient, me: Cell<Me | null>, theme: Theme) {
  const before = me.peek();
  if (!before || before.prefs.theme === theme) return;
  const next = { ...before, prefs: { ...before.prefs, theme } };
  applyMe(next);
  me.set(next);
  try { await api.request('PATCH', '/api/_settings/me/prefs', { body: { theme } }); }
  catch (failure) {
    if (me.peek() === next) { applyMe(before); me.set(before); }
    fail(failure);
  }
}

const fail = (failure: unknown, fallback = 'That did not save. Try again.') =>
  toast(failure instanceof ValidationError ? Object.values(failure.fields ?? {})[0] ?? failure.message : failure instanceof ApiError ? failure.message : fallback, { icon: 'info' });

// ---------- layout pieces ----------

const head = (title: string, text: string, extra: Child = null) =>
  html`<div class="set-head">${extra}<div class="grow"><h1>${title}</h1>${text ? html`<p>${text}</p>` : null}</div></div>`;
const section = (title: string, text: string, body: Child, pad = false) =>
  html`<section class="set-sec"><div class="set-sec-h"><h2>${title}</h2>${text ? html`<p>${text}</p>` : null}</div><div class=${pad ? 'set-sec-b' : 'set-rows'}>${body}</div></section>`;
const row = (title: Child, text: Child, control: Child, badge: Child = null) =>
  html`<div class="set-row"><div class="set-row-t"><strong>${title}${badge}</strong>${text ? html`<p>${text}</p>` : null}</div><div class="set-row-c">${control}</div></div>`;
const callout = (tone: 'info' | 'warn', icon: string, text: Child) => html`<div class=${`set-callout ${tone}`}>${Icon(icon, { size: 'sm' })}<span>${text}</span></div>`;
const badge = (label: string, tone = '') => html`<span class=${`set-badge ${tone}`}>${label}</span>`;
function select(value: string, options: Array<[string, string]>, onChange: (value: string) => void, label: string): HTMLButtonElement {
  const chosen = cell(value);
  return Dropdown({ label, ariaLabel: label, className: 'set-select', value: () => chosen.get(), choices: () => options.map(([key, text]) => ({ value: key, label: text })),
    onChange: next => { if (next === null) return; chosen.set(next); onChange(next); } });
}
const choice = (on: () => boolean, title: string, text: string, pick: () => void, disabled = false, preview: Child = null) =>
  html`<button type="button" class="set-choice" class:on=${on} aria-pressed=${() => String(on())} disabled=${disabled} on:click=${pick}>
    ${preview}<strong>${title}</strong>${text ? html`<span>${text}</span>` : null}${when(on, () => html`<span class="tick">${Icon('check', { size: 'xs' })}</span>`)}</button>`;

/** Save a part of the person's settings at once and keep the page in step. */
async function savePrefs(ctx: SettingsContext, changes: Record<string, unknown>, quiet = false) {
  try {
    await ctx.api.request('PATCH', '/api/_settings/me/prefs', { body: changes });
    const me = ctx.me.peek();
    const prefs = { ...me.prefs }, notifications = { ...me.notifications };
    for (const [key, value] of Object.entries(changes)) {
      if (key === 'quiet') notifications.quiet = value as Me['notifications']['quiet'];
      else if (key.startsWith('notify.')) notifications.events = notifications.events.map(group => ({ ...group, items: group.items.map(item => item.key === key.slice(7) ? { ...item, channels: value as [boolean, boolean] } : item) }));
      else if (key !== 'last_page') (prefs as any)[key] = value;
    }
    const next = { ...me, prefs, notifications, last_page: (changes.last_page as string) ?? me.last_page };
    applyMe(next);
    ctx.me.set(next);
    if (!quiet) { toast('Saved'); ctx.changed(); }
  } catch (failure) { fail(failure); }
}

// ---------- the page ----------

/** One settings page with the settings navigation and search. page is 'profile' or 'app/<app>'. */
export function SettingsView(ctx: SettingsContext, page: string): HTMLElement {
  const me = ctx.me.peek();
  const query = cell('');
  if (me.last_page !== page) void savePrefs(ctx, { last_page: page }, true);
  const index = () => [
    ...me.pages.flatMap(id => (INDEX[id] ?? []).map(label => ({ page: id, label, where: PAGES[id][0] }))),
    ...me.apps.flatMap(app => app.labels.map(label => ({ page: `app/${app.id}`, label: `${app.title}: ${label}`, where: `${app.title} settings` }))),
  ];
  const hits = derive(() => {
    const text = query.get().trim().toLowerCase();
    return text ? index().filter(item => item.label.toLowerCase().includes(text)).slice(0, 12) : null;
  });
  const groups = [...new Set(me.pages.map(id => PAGES[id][2]))];
  const link = (target: string, label: string, icon: string, extra: Child = null) =>
    html`<a class="set-nav-item" class:on=${target === page} aria-current=${target === page ? 'page' : null} href=${ctx.href(target)}>${Icon(icon, { size: 'sm' })}<span>${label}</span>${extra}</a>`;
  const nav = html`<nav class="set-nav" aria-label="Settings">
    <label class="set-search">${Icon('search', { size: 'sm' })}<input placeholder="Find a setting…" aria-label="Find a setting" prop:value=${query}
      on:input=${(event: Event) => query.set((event.target as HTMLInputElement).value)}></label>
    ${() => {
      const found = hits.get();
      if (found) return found.length ? found.map(item => html`<a class="set-nav-item hit" href=${ctx.href(item.page)} on:click=${() => query.set('')}>${Icon('arrow-right', { size: 'sm' })}
        <span>${item.label}<small>${item.where}</small></span></a>`) : html`<p class="set-none">No setting matches.</p>`;
      return [
        ...groups.map(group => [html`<div class="set-nav-label">${group}</div>`, me.pages.filter(id => PAGES[id][2] === group).map(id => link(id, PAGES[id][0], PAGES[id][1]))]),
        me.apps.length ? [html`<div class="set-nav-label">Apps</div>`, me.apps.map(app => link(`app/${app.id}`, app.title, 'layers',
          app.can_edit ? null : html`<span class="set-lock" title="View only">${Icon('lock', { size: 'xs' })}</span>`))] : null,
        me.controls ? [html`<div class="set-nav-label">Workspace</div>`, html`<a class="set-nav-item" href="/controls" target="_blank" rel="noopener">${Icon('sliders-horizontal', { size: 'sm' })}<span>Controls</span>${Icon('arrow-up-right', { size: 'xs' })}</a>`,
          html`<p class="set-none">Users, access and the rest of the workspace.</p>`] : null,
      ];
    }}</nav>`;
  const options: Array<[string, string]> = [...me.pages.map(id => [id, PAGES[id][0]] as [string, string]), ...me.apps.map(app => [`app/${app.id}`, `${app.title} settings`] as [string, string])];
  const content = page.startsWith('app/') ? AppSettingsPage(ctx, page.slice(4)) : ({ profile: ProfilePage, appearance: AppearancePage, security: SecurityPage,
    notifications: NotificationsPage, email: EmailPage, ai: AiPage, keys: (ctx: SettingsContext) => later(() => import('../settings/keys').then(module => () => module.MyKeysPage({ api: ctx.api, twoStep: ctx.me.peek().security.two_step.on }))) } as Record<string, (ctx: SettingsContext) => Node>)[page]?.(ctx) ?? null;
  return html`<div class="set-wrap">${nav}<div class="set-main">${select(page, options, value => ctx.go(value), 'Settings page')}${content}</div></div>` as HTMLElement;
}

/** The portal subset on a portal host: profile, password and notifications, with a way back to the portal. */
export function PortalSettings(api: ApiClient): Node {
  const me = cell<Me | null>(null), failed = cell(false), photo = cell(0);
  const route = cell(window.location.pathname.replace(/^\/settings\/?/, '') || 'profile');
  const go = (page: string) => { window.history.pushState(null, '', `/settings/${page}`); route.set(page); };
  window.addEventListener('popstate', () => route.set(window.location.pathname.replace(/^\/settings\/?/, '') || 'profile'));
  void (async () => {
    try {
      const session = await api.request<{ user: User | null; csrf?: string }>('GET', '/api/auth/session');
      if (!session.data.user) { window.location.assign('/'); return; }
      (api as ApiClient).csrf = session.data.csrf ?? '';
      const loaded = await loadMe(api);
      if (!loaded) throw new Error();
      applyMe(loaded);
      me.set(loaded);
    } catch { failed.set(true); }
  })();
  return html`<div class="set-portal">${() => {
    const current = me.get();
    if (failed.get()) return html`<p class="set-p">Settings could not load. <a href="/">Back to the portal</a></p>`;
    if (!current) return html`<p class="set-p">Loading…</p>`;
    return html`<header class="set-portal-top"><a href="/">${Icon('arrow-left', { size: 'sm' })}Back to the portal</a><span class="grow"></span>
      ${PersonMark(() => current.profile.full_name || current.user.username, () => Boolean(me.get()?.profile.photo), photo)}</header>
      ${() => SettingsView({ api, me: me as Cell<Me>, photo, starts: [], href: page => `/settings/${page}`, go, changed: () => {} }, current.pages.includes(route.get()) ? route.get() : 'profile')}`;
  }}</div>`;
}

// ---------- profile ----------

/** What a password shorter than the workspace's minimum gets told. */
export const tooShort = (minimum: number) => minimum <= 1 ? 'Enter a password.' : `Your workspace needs at least ${minimum} characters.`;

/** The built-in System Administrator's profile: fixed, except the email address used for notifications. */
function BuiltinProfile(ctx: SettingsContext): Node {
  const me = ctx.me.peek();
  const profile = derive(() => ctx.me.get().profile);
  return html`${head('Profile', 'The System Administrator is built into every instance.')}
    ${section('About this account', '', html`<div class="set-form">
      <div class="set-field"><span class="lbl">Full name</span><input class="input" disabled aria-label="Full name" value=${me.profile.full_name || 'System Administrator'}>
        <span class="hint">Fixed. The username and name of this account never change.</span></div>
      <div class="set-field"><span class="lbl">Username</span><input class="input mono" disabled aria-label="Username" value=${me.user.username}></div>
      <div class="set-field"><span class="lbl">Email for notifications</span><div class="set-inline"><input class="input" disabled aria-label="Email" prop:value=${() => profile.get().email || 'Not set'}>
        ${Button({ label: profile.peek().email ? 'Change' : 'Add', size: 'small', onClick: () => changeEmail(ctx) })}</div>
        <span class="hint">An extra address where this account gets notifications. Saving it asks for your current password.</span></div>
    </div>`, true)}`;
}

function ProfilePage(ctx: SettingsContext): Node {
  const me = ctx.me.peek();
  const profile = derive(() => ctx.me.get().profile);
  const confirm = new URLSearchParams(window.location.search).get('confirm');
  if (confirm) void (async () => {
    try {
      const { data } = await ctx.api.request<{ email: string }>('POST', '/api/_settings/me/email/confirm', { body: { token: confirm } });
      ctx.me.set({ ...ctx.me.peek(), profile: { ...ctx.me.peek().profile, email: data.email } });
      toast(`Your email is now ${data.email}`);
    } catch (failure) { fail(failure); }
    window.history.replaceState(window.history.state, '', window.location.pathname);
  })();
  if (me.builtin) return BuiltinProfile(ctx);
  const save = async (key: keyof Me['profile'], value: string) => {
    if (value === profile.peek()[key]) return;
    try {
      const { data } = await ctx.api.request<Me['profile']>('PATCH', '/api/_settings/me/profile', { body: { [key]: value } });
      ctx.me.set({ ...ctx.me.peek(), profile: data });
      toast('Saved');
      ctx.changed();
    } catch (failure) { fail(failure); ctx.me.set({ ...ctx.me.peek() }); }
  };
  const input = (key: 'full_name' | 'short_name' | 'title' | 'phone', label: string, hint = '') => html`<div class="set-field"><label for=${`pf-${key}`}>${label}</label>
    <input class="input" id=${`pf-${key}`} prop:value=${() => profile.get()[key]} inputmode=${key === 'phone' ? 'tel' : null}
      on:change=${(event: Event) => void save(key, (event.target as HTMLInputElement).value)}>${hint ? html`<span class="hint">${hint}</span>` : null}</div>`;
  const upload = (event: Event) => {
    const file = (event.target as HTMLInputElement).files?.[0];
    (event.target as HTMLInputElement).value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { toast('Use a PNG, JPG or WebP image.', { icon: 'info' }); return; }
    if (file.size > 2 * 1024 * 1024) { toast('That photo is over 2 MB. Choose a smaller one.', { icon: 'info' }); return; }
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await ctx.api.request('POST', '/api/_settings/me/photo', { body: { type: file.type, data: String(reader.result).split(',')[1] ?? '' } });
        ctx.me.set({ ...ctx.me.peek(), profile: { ...ctx.me.peek().profile, photo: true } });
        ctx.photo.set(ctx.photo.peek() + 1);
        toast('Photo updated');
      } catch (failure) { fail(failure); }
    };
    reader.readAsDataURL(file);
  };
  const remove = async () => {
    try {
      await ctx.api.request('DELETE', '/api/_settings/me/photo');
      ctx.me.set({ ...ctx.me.peek(), profile: { ...ctx.me.peek().profile, photo: false } });
      toast('Photo removed');
    } catch (failure) { fail(failure); }
  };
  const name = () => profile.get().full_name || me.user.username;
  return html`${head('Profile', me.portal ? 'Your details for this portal. Changes save as you go.' : 'How you appear to people in this workspace. Changes save as you go.')}
    ${section('Photo', 'Shown next to your name in the sidebar.', html`<div class="set-photo-row">${PersonMark(name, () => profile.get().photo, ctx.photo, 'xl')}
      <div class="set-stack"><div class="set-btns"><label class="btn sm" for="photo-in">${Icon('upload', { size: 'sm' })}<span>${() => profile.get().photo ? 'Change photo' : 'Upload photo'}</span></label>
        <input type="file" id="photo-in" accept="image/png,image/jpeg,image/webp" hidden on:change=${upload}>
        ${when(() => profile.get().photo, () => Button({ label: 'Remove', variant: 'ghost', size: 'small', onClick: () => void remove() }))}</div>
        <span class="hint">PNG, JPG or WebP, up to 2 MB. It's cropped to a circle.</span></div></div>`, true)}
    ${section('About you', '', html`<div class="set-form">
      ${input('full_name', 'Full name')}${input('short_name', 'Short name', 'Used in the sidebar and mentions.')}
      ${input('title', 'Job title')}${input('phone', 'Mobile')}
      <div class="set-field"><span class="lbl">Email</span><div class="set-inline"><input class="input" disabled aria-label="Email" prop:value=${() => profile.get().email || 'Not set'}>
        ${Button({ label: 'Change', size: 'small', onClick: () => changeEmail(ctx) })}</div><span class="hint">Used to sign in. A change needs a confirmation link.</span></div>
      <div class="set-field"><span class="lbl">Username</span><input class="input mono" disabled aria-label="Username" value=${me.user.username}><span class="hint">Set by your admin.</span></div>
      <div class="set-field full"><label for="pf-signature">Email signature</label><textarea class="input" id="pf-signature" rows="3" prop:value=${() => profile.get().signature}
        on:change=${(event: Event) => void save('signature', (event.target as HTMLTextAreaElement).value)}></textarea><span class="hint">Added to emails you send from records.</span></div>
    </div>`, true)}
    ${me.portal ? null : section('Your role', '', row(badge(me.user.role, 'violet'), `Your admin decides what you can see and change. ${me.controls ? 'You can open Controls to manage users and access.' : 'Ask an admin if you need more access.'}`, null))}`;
}

/** The System Administrator's email for notifications: saved at once on any instance, after its current password (and a code with two-step on). */
function administratorEmail(ctx: SettingsContext) {
  const address = cell(ctx.me.peek().profile.email), password = cell(''), code = cell(''), error = cell(''), busy = cell(false);
  const enrolled = ctx.me.peek().security.two_step.on;
  const field = (id: string, label: string, value: Cell<string>, type: string, auto: string) => html`<div class="set-field"><label for=${id}>${label}</label>
    <input class="input" id=${id} type=${type} autocomplete=${auto} prop:value=${value} on:input=${(event: Event) => { value.set((event.target as HTMLInputElement).value); error.set(''); }}></div>`;
  openModal({ title: 'Email for notifications', narrow: true, icon: 'mail',
    body: () => html`${field('ae-email', 'Email', address, 'email', 'email')}${field('ae-password', 'Your current password', password, 'password', 'current-password')}
      ${enrolled ? field('ae-code', 'Code from your app, or a backup code', code, 'text', 'one-time-code') : null}
      ${() => error.get() ? html`<span class="err" role="alert">${error.get()}</span>` : null}`,
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Save email', disabled: busy, onClick: async () => {
      const email = address.peek().trim();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { error.set('Enter a valid email address.'); return; }
      if (!password.peek()) { error.set('Enter your current password.'); return; }
      if (enrolled && !code.peek().trim()) { error.set('Enter a code from your app, or a backup code.'); return; }
      busy.set(true);
      try {
        const { data } = await ctx.api.request<{ email: string }>('POST', '/api/_settings/me/email',
          { body: { email, password: password.peek(), ...(enrolled ? { code: code.peek().trim() } : {}) } });
        ctx.me.set({ ...ctx.me.peek(), profile: { ...ctx.me.peek().profile, email: data.email } });
        close();
        toast(`Notifications now go to ${data.email}`);
      } catch (failure) {
        error.set(failure instanceof ValidationError ? Object.values(failure.fields ?? {})[0] ?? failure.message : failure instanceof ApiError ? failure.message : 'Try again.');
      } finally { busy.set(false); }
    } } }) });
}

function changeEmail(ctx: SettingsContext) {
  if (ctx.me.peek().builtin) { administratorEmail(ctx); return; }
  if (!ctx.me.peek().development) { toast("Email is not set up yet, so your address can't be changed here. Ask your admin.", { icon: 'info' }); return; }
  const value = cell('');
  confirmIdentity<{ sent: string; link?: string }>(ctx, { title: 'Change your email', icon: 'mail', yes: 'Send link', path: '/api/_settings/me/email',
    before: error => html`<div class="set-field"><label for="ce">New email</label><input class="input" id="ce" type="email" prop:value=${value}
      on:input=${(event: Event) => { value.set((event.target as HTMLInputElement).value); error.set(''); }}>
      <span class="hint">We'll send a link to the new address. Your email changes when you open it.</span></div>`,
    body: () => {
      const address = value.peek().trim();
      return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(address) ? { email: address } : 'Enter a valid email address.';
    },
    done: data => { if (data.link) showLink(data.link); else toast(`Link sent to ${data.sent}. Your email changes when you open it.`); } });
}

/**
 * Sensitive changes ask who you are again: with two-step sign-in on, only a code from the app or a
 * backup code; without it, the password. body() adds the change's own values, or returns a message to show.
 */
function confirmIdentity<T>(ctx: SettingsContext, options: { title: string; icon: string; yes: string; path: string; danger?: boolean; text?: string;
  before?: (error: Cell<string>) => Child; body?: () => Record<string, unknown> | string; done: (data: T) => void }) {
  const secret = cell(''), error = cell(''), busy = cell(false);
  const enrolled = ctx.me.peek().security.two_step.on;
  const input = (event: Event) => { secret.set((event.target as HTMLInputElement).value); error.set(''); };
  openModal({ title: options.title, narrow: true, icon: options.icon,
    body: () => html`${options.text ? html`<p class="set-p">${options.text}</p>` : null}${options.before?.(error) ?? null}
      ${enrolled
        ? html`<div class="set-field"><label for="ra-code">Code from your app, or a backup code</label><input class="input" id="ra-code" autocomplete="one-time-code" maxlength="9"
          prop:value=${secret} on:input=${input}></div>`
        : html`<div class="set-field"><label for="ra-password">Your password</label><input class="input" id="ra-password" type="password" autocomplete="current-password"
          prop:value=${secret} on:input=${input}></div>`}
      ${() => error.get() ? html`<span class="err" role="alert">${error.get()}</span>` : null}`,
    footer: close => DialogFooter({ cancel: close, submit: { label: options.yes, variant: options.danger ? 'danger' : 'primary', disabled: busy, onClick: async () => {
      const extra = options.body?.() ?? {};
      if (typeof extra === 'string') { error.set(extra); return; }
      if (!secret.peek().trim()) { error.set(enrolled ? 'Enter a code from your app, or a backup code.' : 'Enter your password.'); return; }
      busy.set(true);
      try {
        const { data } = await ctx.api.request<T>('POST', options.path, { body: { ...extra, ...(enrolled ? { code: secret.peek().trim() } : { password: secret.peek() }) } });
        close();
        options.done(data);
      } catch (failure) {
        error.set(failure instanceof ValidationError ? Object.values(failure.fields ?? {})[0] ?? failure.message : failure instanceof ApiError ? failure.message : 'Try again.');
      } finally { busy.set(false); }
    } } }) });
}

/** In development there is no mail yet: the confirmation link shows here. */
function showLink(link: string) {
  openModal({ title: 'Confirmation link', narrow: true, icon: 'link',
    body: () => html`<p class="set-p">Mail is not set up on this development server, so here is the link that would be sent. Open it to confirm the new address.</p>
      <div class="set-secret"><a href=${link}>${link}</a></div>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Copy link', icon: 'copy', onClick: () => void copyText(link, 'the link') } }) });
}

// ---------- appearance and region ----------

function AppearancePage(ctx: SettingsContext): Node {
  const prefs = derive(() => ctx.me.get().prefs);
  const set = (key: keyof Me['prefs'], value: unknown) => {
    if (key === 'lang' && value === 'ar') toast('Arabic is in beta. The layout switches to right-to-left.');
    void savePrefs(ctx, { [key]: value }, key === 'lang' && value === 'ar');
    if (key === 'lang') ctx.changed();
  };
  const today = new Date().toISOString().slice(0, 10);
  const sample = (format: DateFormat) => { setRegion({ date: format }); const text = formatDate(today); applyMe(ctx.me.peek()); return text; };
  const pick = (key: keyof Me['prefs'], value: string, title: string, text: string, disabled = false, preview: Child = null) =>
    choice(() => prefs.get()[key] === value, title, text, () => set(key, value), disabled, preview);
  // A small picture of each theme, drawn in that theme's colours whatever the page shows now.
  const thumb = (scheme: Theme): Node => scheme === 'system'
    ? html`<span class="set-thumb system" aria-hidden="true">${thumb('light')}${thumb('dark')}</span>` as Node
    : html`<span class=${`set-thumb ${scheme}`} aria-hidden="true"><i></i><i></i></span>` as Node;
  return html`${head('Appearance & region', 'How Evlet looks and shows dates and numbers for you. Other people are not affected.')}
    ${section('Theme', '', html`<div class="set-choices">${pick('theme', 'light', 'Light', '', false, thumb('light'))}
      ${pick('theme', 'system', 'Match my device', 'Changes when your device does.', false, thumb('system'))}${pick('theme', 'dark', 'Dark', '', false, thumb('dark'))}</div>`, true)}
    ${section('Layout', '', html`<div class="set-choices c2">${pick('density', 'comfortable', 'Comfortable', 'More space between rows.')}${pick('density', 'compact', 'Compact', 'Fits more rows on screen.')}</div>
      <div class="set-choices c2">${pick('text', 'default', 'Default text size', '')}${pick('text', 'large', 'Larger text', 'Everything 10% bigger.')}</div>`, true)}
    ${section('Workspace', '', [
      row('Open this page after sign-in', '', select(prefs.peek().start, [['', 'The usual start page'], ...ctx.starts], value => set('start', value), 'Start page')),
      row('Show record counts in the sidebar', '', Switch({ on: () => prefs.get().counts, ariaLabel: 'Record counts', onChange: next => set('counts', next) })),
      row('Enter moves to the next field', 'In forms, Enter goes to the next field and Shift+Enter to the one before. Not in long text.',
        Switch({ on: () => Boolean(prefs.get().enter_next), ariaLabel: 'Enter moves to the next field', onChange: next => set('enter_next', next) })),
      row('Keep unsaved changes when I leave a record', 'Your edits wait for you to restore or discard them. Only Save changes the record. Off asks before discarding them, and forgets what was kept.',
        Switch({ on: () => prefs.get().keep_unsaved !== false, ariaLabel: 'Keep unsaved changes', onChange: next => set('keep_unsaved', next) })),
    ])}
    ${PwaSettings()}
    ${section('Language and region', '', [
      row('Language', 'Arabic switches the layout to right-to-left.', select(prefs.peek().lang, [['en', 'English'], ['ar', 'العربية (Arabic)']], value => set('lang', value), 'Language')),
      row('Time zone', '', select(prefs.peek().tz, ZONES.some(([zone]) => zone === prefs.peek().tz) ? ZONES : [...ZONES, [prefs.peek().tz, prefs.peek().tz]], value => set('tz', value), 'Time zone')),
      row('Date format', html`Today shows as <strong>${() => { prefs.get(); return formatDate(today); }}</strong>`,
        select(prefs.peek().date, (['d MMM yyyy', 'dd-MM-yyyy', 'yyyy-MM-dd', 'MM/dd/yyyy'] as DateFormat[]).map(format => [format, sample(format)]), value => set('date', value), 'Date format')),
      row('Numbers', '', select(prefs.peek().num, ['1,234.56', '1.234,56', '1 234.56'].map(format => [format, format]), value => set('num', value), 'Numbers')),
      row('Week starts on', '', select(prefs.peek().week, [['mon', 'Monday'], ['sun', 'Sunday'], ['sat', 'Saturday']], value => set('week', value), 'Week starts on')),
    ])}`;
}

// ---------- sign-in and security ----------

function SecurityPage(ctx: SettingsContext): Node {
  const security = derive(() => ctx.me.get().security);
  const me = ctx.me.peek();
  const open = cell(false);
  const reload = async () => { try { ctx.me.set((await ctx.api.request<Me>('GET', '/api/_settings/me')).data); } catch { /* the page keeps what it shows */ } };
  const twoStep = () => {
    const state = security.get().two_step;
    const text = state.required ? `Required for the ${me.user.role} role in this workspace.` : 'A code from an authenticator app, on top of your password.';
    if (!state.available && !state.on) return section('Two-step sign-in', text, row('Authenticator app', 'Two-step sign-in needs the cryptography package and a secret key on the server. Ask your admin.', null, badge('Not available', 'grey')));
    if (state.on) return section('Two-step sign-in', text, row('Authenticator app', `Set up on ${state.since ? formatDate(new Date(state.since * 1000).toISOString()) : 'an earlier day'} from ${state.agent || 'a browser'}. ${state.codes} backup code${state.codes === 1 ? '' : 's'} left.`,
      [Button({ label: 'Make new codes', size: 'small', onClick: () => newCodes(ctx, reload) }),
        Button({ label: 'Turn off', size: 'small', variant: state.required ? 'default' : 'soft-danger', disabled: state.required, tip: state.required ? 'Required for your role' : null, onClick: () => turnOff(ctx, reload) })],
      badge('On', 'green')));
    return section('Two-step sign-in', text, row('Authenticator app', state.required ? "You'll be asked to set it up at your next sign-in." : 'Recommended for everyone.',
      Button({ label: 'Set up', size: 'small', variant: 'primary', onClick: () => setUp(ctx, reload) }), state.required ? badge('Required', 'red') : badge('Off', 'grey')));
  };
  const agent = (text: string) => /evlet-connector/.test(text) ? 'Command line (evlet login)' : /iPhone|Android/i.test(text) ? 'Phone browser' : text ? 'Browser' : 'Unknown device';
  const sessions = () => {
    const list = security.get().sessions;
    return section("Where you're signed in", '', [
      ...list.map(item => row(html`${agent(item.agent)}${item.current ? badge('This device', 'blue') : null}`,
        item.current ? 'Active now' : `Signed in ${item.started ? formatDateTime(new Date(item.started * 1000).toISOString()) : 'earlier'}`,
        item.current ? null : Button({ label: 'Sign out', size: 'small', onClick: async () => {
          try { await ctx.api.request('DELETE', `/api/_settings/me/sessions/${item.id}`); toast('Signed out on that device'); await reload(); } catch (failure) { fail(failure); }
        } }))),
      list.length > 1 ? html`<div class="set-row end">${Button({ label: 'Sign out everywhere else', size: 'small', variant: 'soft-danger', onClick: () => confirmDialog('Sign out everywhere else?',
        'Every other browser, phone and command line session is signed out. You stay signed in here.', 'Sign out', async () => {
          try { await ctx.api.request('DELETE', '/api/_settings/me/sessions'); toast('Signed out everywhere else'); await reload(); } catch (failure) { fail(failure); }
        }) })}</div>` : null,
    ]);
  };
  return html`${head('Sign-in & security', me.portal ? 'Keep your account safe.' : 'Keep your account safe. Your workspace sets the minimum rules.')}
    ${section('Password', '', match(() => open.get() ? 'form' : 'row', {
      row: () => row('Password', '', Button({ label: 'Change password', size: 'small', onClick: () => open.set(true) })),
      form: () => PasswordForm(ctx, () => open.set(false), reload),
    }))}
    ${me.portal ? null : twoStep}${sessions}`;
}

/** Settings > Sign-in & security: the shared password form inside the page, with its own buttons. */
function PasswordForm(ctx: SettingsContext, close: () => void, reload: () => Promise<void>): Node {
  const form = passwordForm(ctx.api, ctx.me.peek(), () => { close(); void reload(); });
  return html`<div class="set-sec-b">${form.fields}
    <div class="set-actions"><span class="hint grow">Other devices are signed out after the change.</span>${Button({ label: 'Cancel', variant: 'ghost', onClick: close })}
      ${Button({ label: 'Update password', variant: 'primary', disabled: form.busy, onClick: () => void form.submit() })}</div></div>`;
}

/** Whether this person can change their password in Settings, so the account menu offers it too. */
export const canChangePassword = (me: Me | null) => Boolean(me?.pages.includes('security'));

/** Change password from the account menu: the same form as Settings, in a small dialog. */
export function openPasswordDialog(api: ApiClient, me: Cell<Me | null>) {
  const current = me.peek();
  if (!current) return;
  let form!: ReturnType<typeof passwordForm>;
  openModal({ title: 'Change password', icon: 'lock', narrow: true, className: 'set-pw-dialog', onSubmit: () => void form.submit(),
    body: close => {
      form = passwordForm(api, current, () => { close(); void loadMe(api).then(fresh => { if (fresh && me.peek() === current) me.set(fresh); }); });
      return form.fields;
    },
    footer: close => DialogFooter({ note: 'Other devices are signed out after the change.', cancel: close,
      submit: { label: 'Change password', disabled: form.busy, onClick: () => void form.submit() } }) });
}

const PASSWORD_FIELDS: Record<string, [id: string, label: string]> = {
  current: ['pw-cur', 'Current password'], new: ['pw-new', 'New password'], again: ['pw-again', 'Type it again'], code: ['pw-code', 'Code from your app'],
};

/**
 * The change-password form Settings and the account menu share: current, new and again, a code when
 * two-step sign-in is on, and the choice to revoke API keys. It checks the workspace's minimum length
 * and the repeat before sending, and shows the server's answer under the field it names.
 */
function passwordForm(api: ApiClient, me: Me, done: () => void) {
  const minimum = me.security.password_min;
  const enrolled = me.security.two_step.on, keys = me.security.api_keys ?? 0;
  const current = cell(''), next = cell(''), again = cell(''), code = cell(''), revoke = cell(false), busy = cell(false);
  // An error names the field it came from ('' for the form as a whole, or the server without a field).
  const error = cell<{ field: string; text: string } | null>(null);
  const score = derive(() => { const value = next.get(); return Math.min(4, Number(value.length >= minimum) + Number(value.length >= 20) + Number(/[A-Z]/.test(value) && /[a-z]/.test(value)) + Number(/[0-9\W]/.test(value))); });
  const errorId = (field: string) => `${PASSWORD_FIELDS[field][0]}-err`;
  const fieldError = (field: string) => () => error.get()?.field === field ? html`<span class="err" id=${errorId(field)} role="alert">${error.get()!.text}</span>` : null;
  const input = (field: string, value: Cell<string>, auto: string, extra: Record<string, string> = {}) => {
    const [id, label] = PASSWORD_FIELDS[field] ?? [`pw-${field}`, ''];
    return html`<input class="input" id=${id} type=${field === 'code' ? 'text' : 'password'} autocomplete=${auto} aria-label=${label || null} maxlength=${extra.maxlength ?? null}
      aria-invalid=${() => error.get()?.field === field ? 'true' : null} aria-describedby=${() => error.get()?.field === field ? errorId(field) : null} prop:value=${value}
      on:input=${(event: Event) => { value.set((event.target as HTMLInputElement).value); error.set(null); }}>`;
  };
  const show = (field: string, text: string) => {
    error.set({ field, text });
    const id = PASSWORD_FIELDS[field]?.[0];
    if (id) queueMicrotask(() => document.getElementById(id)?.focus());
  };
  const submit = async () => {
    if (busy.peek()) return;
    if (!current.peek()) return show('current', 'Enter your current password.');
    if (next.peek().length < minimum) return show('new', tooShort(minimum));
    if (next.peek() !== again.peek()) return show('again', "The two new passwords don't match.");
    if (enrolled && !code.peek().trim()) return show('code', 'Enter a code from your app, or a backup code.');
    busy.set(true);
    try {
      await api.request('POST', '/api/_settings/me/password', { body: { current: current.peek(), new: next.peek(),
        ...(enrolled ? { code: code.peek().trim() } : {}), ...(keys ? { revoke_keys: revoke.peek() } : {}) } });
      toast(revoke.peek() ? 'Password changed. Other devices were signed out and your API keys revoked.' : 'Password changed. Other devices were signed out.');
      done();
    } catch (failure) {
      if (failure instanceof ValidationError && failure.fields && Object.keys(failure.fields).length) {
        const [field, text] = Object.entries(failure.fields)[0];
        if (field in PASSWORD_FIELDS) show(field, text); else show('', `${humanize(field)}: ${text}`);
      } else show('', failure instanceof ApiError ? `The server said: ${failure.message}` : 'That did not reach the server. Try again.');
    } finally { busy.set(false); }
  };
  const fields = html`<div class="set-form">
    <div class="set-field full"><label for="pw-cur">Current password</label>${input('current', current, 'current-password')}${fieldError('current')}</div>
    <div class="set-field"><label for="pw-new">New password</label>${input('new', next, 'new-password')}
      <div class="set-meter"><i style:width=${() => next.get() ? `${(score.get() + 1) * 20}%` : '0'} class=${() => `s${score.get()}`}></i></div>
      <span class="hint" id="pw-rules">${() => next.get().length < minimum ? `${minimum <= 1 ? 'Any password will do.' : `At least ${minimum} characters.`}${next.get().length ? ` ${minimum - next.get().length} more to go.` : ''}` : 'Long enough. A short sentence is easy to remember.'}</span>
      ${fieldError('new')}</div>
    <div class="set-field"><label for="pw-again">Type it again</label><input class="input" id="pw-again" type="password" autocomplete="new-password" aria-label="Type it again"
      aria-invalid=${() => error.get()?.field === 'again' || (again.get() && again.get() !== next.get()) ? 'true' : null}
      aria-describedby=${() => error.get()?.field === 'again' ? errorId('again') : null} prop:value=${again}
      on:input=${(event: Event) => { again.set((event.target as HTMLInputElement).value); error.set(null); }}>
      ${() => error.get()?.field === 'again' ? fieldError('again')()
        : again.get() && again.get() !== next.get() ? html`<span class="err">The two passwords don't match.</span>` : null}</div>
    ${enrolled ? html`<div class="set-field"><label for="pw-code">Code from your app, or a backup code</label>${input('code', code, 'one-time-code', { maxlength: '9' })}${fieldError('code')}</div>` : null}
    ${keys ? html`<div class="set-field full">${Checkbox({ checked: revoke, label: `Also revoke API keys (${keys} active)` })}
      <span class="hint">Scripts and tools that use them stop working. Leave it unticked to keep them.</span></div>` : null}
    ${() => error.get()?.field === '' ? html`<div class="set-field full" role="alert">${callout('warn', 'info', error.get()!.text)}</div>` : null}</div>`;
  return { fields, submit, busy };
}

function confirmDialog(title: string, text: string, yes: string, run: () => void | Promise<void>) {
  openModal({ title, narrow: true, icon: 'info', tint: 'rose', body: () => html`<p class="set-p">${text}</p>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: yes, variant: 'danger', onClick: () => { close(); void run(); } } }) });
}

/** Turn on two-step sign-in: the key for the authenticator app, then a code from it, then the backup codes. */
export function setUp(ctx: SettingsContext, reload: () => Promise<void>) {
  confirmIdentity<{ secret: string; uri: string }>(ctx, { title: 'Set up two-step sign-in', icon: 'lock', yes: 'Continue', path: '/api/_settings/me/two-step',
    text: 'Enter your password to start.', done: secret => enterCode(ctx, secret, reload) });
}

function enterCode(ctx: SettingsContext, secret: { secret: string; uri: string }, reload: () => Promise<void>) {
  const code = cell(''), error = cell(''), busy = cell(false);
  openModal({ title: 'Set up two-step sign-in', icon: 'lock', narrow: true,
    body: () => TwoStepSetup(secret.secret, code, error),
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Turn on', disabled: busy, onClick: async () => {
      if (!/^\d{6}$/.test(code.peek())) { error.set('Enter the 6 digits from the app.'); return; }
      busy.set(true);
      try {
        const { data } = await ctx.api.request<{ codes: string[] }>('POST', '/api/_settings/me/two-step/confirm', { body: { code: code.peek() } });
        close();
        toast('Two-step sign-in is on');
        showCodes(data.codes);
        await reload();
      } catch (failure) { error.set(failure instanceof ValidationError ? failure.fields?.code ?? failure.message : failure instanceof ApiError ? failure.message : 'Try again.'); }
      finally { busy.set(false); }
    } } }) });
}

/** The set-up steps, shared with sign-in when a role requires two-step sign-in. */
export function TwoStepSetup(secret: string, code: Cell<string>, error: Cell<string>): Node {
  const grouped = secret.replace(/(.{4})/g, '$1 ').trim();
  return html`<ol class="set-steps"><li>Open an authenticator app, such as Microsoft Authenticator or Google Authenticator.</li>
    <li>Add an account with this key: <span class="set-key"><code>${grouped}</code>${Button({ label: 'Copy', size: 'small', icon: 'copy', onClick: () => void copyText(secret, 'the key') })}</span></li>
    <li>Type the 6-digit code it shows.</li></ol>
    <div class="set-field"><label for="tfa-code">Code</label><input class="input set-code" id="tfa-code" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="123456"
      prop:value=${code} on:input=${(event: Event) => { code.set((event.target as HTMLInputElement).value.replace(/\D/g, '').slice(0, 6)); error.set(''); }}>
      ${() => error.get() ? html`<span class="err">${error.get()}</span>` : null}</div>`;
}

/** Backup codes are shown once, right after they are made. */
export function showCodes(codes: string[]) {
  openModal({ title: 'Backup codes', icon: 'lock', narrow: true,
    body: () => html`<p class="set-p">Each code works once, if you lose your phone. Keep them somewhere safe, like a password manager. They won't be shown again.</p>
      <div class="set-codes">${codes.map(code => html`<span>${code}</span>`)}</div>`,
    footer: close => html`${Button({ label: 'Copy', icon: 'copy', onClick: () => void copyText(codes.join(' '), 'the codes') })}${Button({ label: 'Done', variant: 'primary', onClick: close })}` });
}

function newCodes(ctx: SettingsContext, reload: () => Promise<void>) {
  confirmIdentity<{ codes: string[] }>(ctx, { title: 'Make new backup codes', icon: 'lock', yes: 'Make new codes', path: '/api/_settings/me/two-step/codes',
    text: 'The old codes stop working.', done: data => { toast('New codes made. The old ones no longer work.'); showCodes(data.codes); void reload(); } });
}

function turnOff(ctx: SettingsContext, reload: () => Promise<void>) {
  confirmIdentity(ctx, { title: 'Turn off two-step sign-in?', icon: 'lock', yes: 'Turn off', danger: true, path: '/api/_settings/me/two-step/off',
    text: 'Anyone with your password could then sign in as you.', done: () => { toast('Two-step sign-in is off'); void reload(); } });
}

// ---------- notifications ----------

function NotificationsPage(ctx: SettingsContext): Node {
  const notes = derive(() => ctx.me.get().notifications);
  const toggle = (key: string, channel: 0 | 1) => {
    const item = notes.peek().events.flatMap(group => group.items).find(entry => entry.key === key)!;
    const channels: [boolean, boolean] = [...item.channels] as [boolean, boolean];
    channels[channel] = !channels[channel];
    void savePrefs(ctx, { [`notify.${key}`]: channels });
  };
  const quiet = (changes: Partial<Me['notifications']['quiet']>) => void savePrefs(ctx, { quiet: { ...notes.peek().quiet, ...changes } });
  const zone = ctx.me.peek().prefs.tz.split('/').pop()!.replace(/_/g, ' ');
  return html`${head('Notifications', 'Choose what you hear about and where.')}
    <div class="set-table"><table><thead><tr><th>When</th><th>In Evlet</th><th>Email</th></tr></thead><tbody>${() => notes.get().events.map(group => [
      html`<tr class="group"><td colspan="3">${group.group}</td></tr>`,
      group.items.map(item => html`<tr><td>${item.label}</td>
        <td>${Switch({ on: item.channels[0], ariaLabel: `${item.label} in Evlet`, onChange: () => toggle(item.key, 0) })}</td>
        <td>${item.locked ? html`<span class="set-lock" title="Always sent, for your safety">${Icon('lock', { size: 'sm' })}</span>`
          : Switch({ on: item.channels[1], ariaLabel: `${item.label} by email`, onChange: () => toggle(item.key, 1) })}</td></tr>`)])}</tbody></table></div>
    ${section('Quiet hours', 'Notifications wait until quiet hours end. Security emails still arrive.', row('Pause notifications',
      () => notes.get().quiet.on ? `From ${notes.get().quiet.from} to ${notes.get().quiet.to}, ${zone} time` : 'Off',
      [() => notes.get().quiet.on ? [select(notes.get().quiet.from, ['19:00', '20:00', '21:00', '22:00'].map(time => [time, time]), value => quiet({ from: value }), 'From'),
        html`<span class="hint">to</span>`, select(notes.get().quiet.to, ['06:00', '07:00', '08:00', '09:00'].map(time => [time, time]), value => quiet({ to: value }), 'To')] : null,
      Switch({ on: () => notes.get().quiet.on, ariaLabel: 'Quiet hours', onChange: next => quiet({ on: next }) })]))}`;
}

// ---------- connections ----------

/** A page that shares the Controls pages' code, loaded with them on first use so the app chunk stays small. */
function later(load: () => Promise<() => Node>): Node {
  const owner = getOwner();
  let alive = true;
  onCleanup(() => { alive = false; });
  const holder = html`<div><p class="set-p">Loading…</p></div>` as HTMLElement;
  load().then(create => { if (alive) runWithOwner(owner, () => holder.replaceChildren(create())); }).catch(() => {
    if (alive) runWithOwner(owner, () => holder.replaceChildren(callout('warn', 'info', 'This page could not load. Reload and try again.')));
  });
  return holder;
}

/** /settings/email: the person's own mailbox (MyEmailPage). */
function EmailPage(ctx: SettingsContext): Node {
  return later(() => import('../settings/email').then(module => () => module.MyEmailPage({ api: ctx.api })));
}

/** /settings/ai: remote assistants (MyAIPage) with the local `evlet mcp` steps and its signed-in command line sessions. */
function AiPage(ctx: SettingsContext): Node {
  const origin = window.location.origin;
  const steps = [`evlet login --url ${origin} --username ${ctx.me.peek().user.username} --session ~/.evlet/session.json`, 'evlet mcp --session ~/.evlet/session.json'];
  const clients = derive(() => ctx.me.get().security.sessions.filter(item => /evlet-connector/.test(item.agent)));
  const command = (text: string) => html`<div class="set-key"><code>${text}</code>${Button({ label: 'Copy', size: 'small', icon: 'copy', onClick: () => void copyText(text, 'the command') })}</div>`;
  const local = () => html`<p class="muted ctl-small">Assistants that run on your computer can use the local connector instead. It acts as you and follows the same workspace rules.</p>
    <ol class="set-steps"><li>Sign in from a terminal on your computer. Your password is not stored.${command(steps[0])}</li>
      <li>Add Evlet to your assistant as an MCP server that runs this command.${command(steps[1])}</li>
      <li>Ask your assistant about your records. It shows up below while it is signed in.</li></ol>
    ${() => clients.get().length ? clients.get().map(item => row('Command line session', `Signed in ${item.started ? formatDateTime(new Date(item.started * 1000).toISOString()) : 'earlier'}`,
      Button({ label: 'Disconnect', size: 'small', variant: 'soft-danger', onClick: async () => {
        try { await ctx.api.request('DELETE', `/api/_settings/me/sessions/${item.id}`); toast('Disconnected'); ctx.me.set({ ...ctx.me.peek(), security: { ...ctx.me.peek().security, sessions: ctx.me.peek().security.sessions.filter(each => each.id !== item.id) } }); }
        catch (failure) { fail(failure); }
      } }))) : html`<div class="set-empty">No command line session is signed in.</div>`}`;
  return later(() => import('../settings/ai').then(module => () => module.MyAIPage({ api: ctx.api, local })));
}

// ---------- app settings ----------

function AppSettingsPage(ctx: SettingsContext, app: string): Node {
  const state = cell<AppState | null>(null), tab = cell<'everyone' | 'mine'>('everyone'), problem = cell('');
  const draft = cell<Record<string, unknown>>({});
  void (async () => {
    try { state.set((await ctx.api.request<AppState>('GET', `/api/_settings/app/${app}`)).data); }
    catch (failure) { problem.set(failure instanceof ApiError ? failure.message : 'These settings could not load.'); }
  })();
  return html`${() => {
    const current = state.get();
    if (!current) return problem.get() ? callout('warn', 'info', problem.get()) : html`<p class="set-p">Loading…</p>`;
    const editors = current.editors.filter(role => role !== 'admin');
    return [
      head(`${current.title} settings`, '', html`<span class="set-app">${Icon('layers')}</span>`),
      html`<p class="set-p">Who can change the shared settings: ${['admin', ...editors].map(role => badge(role, 'violet'))}</p>`,
      current.sections.length && current.preferences.length ? html`<div class="tabs set-tabs" role="tablist">${([['everyone', 'For everyone'], ['mine', 'Just for you']] as const).map(([id, label]) =>
        html`<button type="button" role="tab" class="tab" class:on=${() => tab.get() === id} aria-selected=${() => String(tab.get() === id)} on:click=${() => tab.set(id)}>${label}</button>`)}</div>` : null,
      () => (tab.get() === 'everyone' && current.sections.length) || !current.preferences.length ? Everyone(ctx, app, state, draft, editors) : Mine(ctx, app, current),
    ];
  }}${() => ChangeBar(ctx, app, state, draft)}`;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const shown = (field: AppField, value: unknown) => {
  const text = field.type === 'select' ? humanize(String(value ?? '')) : field.type === 'boolean' ? (value ? 'on' : 'off') : kindOf(field.type).format(value, field) || 'empty';
  return field.suffix && !['select', 'boolean', 'multiselect'].includes(field.type) ? `${text} ${field.suffix}` : text;
};

function Everyone(ctx: SettingsContext, app: string, state: Cell<AppState | null>, draft: Cell<Record<string, unknown>>, editors: string[]): Node {
  const current = state.peek()!, readOnly = !current.can_edit;
  const value = (field: AppField) => field.name in draft.get() ? draft.get()[field.name] : field.value;
  const set = (field: AppField, next: unknown) => {
    if (readOnly || field.read_only) return;
    const changes = { ...untrack(() => draft.get()) };
    if (same(next, field.value)) delete changes[field.name]; else changes[field.name] = next;
    draft.set(changes);
  };
  const ask = editors.map(role => `someone with the ${role} role`).concat(['an admin']).join(' or ');
  return html`${readOnly ? callout('warn', 'lock', `You can see these settings but not change them. They apply to everyone using ${current.title}. Ask ${ask}.`)
    : callout('info', 'users', `These apply to everyone using ${current.title}. Changes wait for you to save them below.`)}
    ${current.sections.map(group => html`<div class:set-ro=${readOnly}>${section(group.title, '', group.fields.map(field => {
      const differs = () => !same(value(field), field.default);
      return row(field.label, () => [field.help_text ? html`<span>${field.help_text}</span>` : null, differs() ? html`<span class="set-default">Default: ${shown(field, field.default)}</span>` : null],
        [Control(field, () => value(field), next => set(field, next), readOnly),
          () => differs() && !readOnly && !field.read_only ? Button({ icon: 'rotate-ccw', size: 'small', variant: 'ghost', tip: 'Back to default', onClick: () => set(field, field.default) }) : null],
        () => field.name in draft.get() ? html`<span class="set-chg">Unsaved</span>` : differs() ? html`<span class="set-chg">Changed</span>` : null);
    }))}</div>`)}`;
}

function Mine(ctx: SettingsContext, app: string, current: AppState): Node {
  const values = cell<Record<string, unknown>>(Object.fromEntries(current.preferences.map(field => [field.name, field.value])));
  const save = async (field: AppField, next: unknown) => {
    if (field.read_only) return;
    const before = values.peek()[field.name];
    values.set({ ...values.peek(), [field.name]: next });
    try { await ctx.api.request('PATCH', `/api/_settings/me/app/${app}`, { body: { [field.name]: next } }); toast('Saved'); }
    catch (failure) { values.set({ ...values.peek(), [field.name]: before }); fail(failure); }
  };
  return html`${callout('info', 'user', 'Only for you. These save as you change them.')}
    ${section('Your preferences', '', current.preferences.map(field => row(field.label, field.help_text, Control(field, () => values.get()[field.name], next => void save(field, next), false))))}`;
}

/** A settings field's control: a switch, a list, chips for several choices, or text with a unit. */
function Control(field: AppField, value: () => unknown, change: (value: unknown) => void, readOnly: boolean): Node {
  readOnly ||= Boolean(field.read_only);
  if (field.type === 'boolean') return Switch({ on: () => value() === true, ariaLabel: field.label, disabled: readOnly, onChange: next => { if (!readOnly) change(next); } });
  if (field.type === 'select') {
    return Dropdown({ label: field.label, ariaLabel: field.label, className: 'set-select', disabled: readOnly,
      value: () => value() === null || value() === undefined ? null : String(value()),
      choices: () => (field.choices ?? []).map(item => ({ value: item, label: humanize(item) })), onChange: next => { if (!readOnly && next !== null) change(next); } });
  }
  if (field.type === 'multiselect') {
    const chosen = () => (value() as string[] | null) ?? [];
    return html`<div class="chips set-multi" role="group" aria-label=${field.label}>${(field.choices ?? []).map(item => html`<button type="button" class="chip" class:on=${() => chosen().includes(item)}
      aria-pressed=${() => String(chosen().includes(item))} disabled=${readOnly} on:click=${() => {
        if (readOnly) return;
        const next = (field.choices ?? []).filter(each => each === item ? !chosen().includes(each) : chosen().includes(each));
        if (!next.length) { toast('Choose at least one', { icon: 'info' }); return; }
        change(next);
      }}>${() => chosen().includes(item) ? Icon('check', { size: 'xs' }) : null}${humanize(item)}</button>`)}</div>`;
  }
  const kind = kindOf(field.type);
  const input = html`<input class="input" aria-label=${field.label} disabled=${readOnly} inputmode=${kind.inputMode ?? null}
    prop:value=${() => (kind.edit ?? kind.format)(value(), field)} on:change=${(event: Event) => {
      if (readOnly) return;
      const target = event.target as HTMLInputElement;
      try { change(kind.parse ? kind.parse(target.value, field) : target.value); }
      catch (error) {
        if (!(error instanceof InputError)) throw error;
        toast(error.message, { icon: 'info' });
        target.value = (kind.edit ?? kind.format)(untrack(value), field);
      }
    }}>` as HTMLInputElement;
  return field.suffix ? html`<div class="in-affix set-affix">${input}<span class="suf">${field.suffix}</span></div>` : input;
}

function ChangeBar(ctx: SettingsContext, app: string, state: Cell<AppState | null>, draft: Cell<Record<string, unknown>>): Child {
  const changes = () => {
    const current = state.get(), values = draft.get();
    return Object.fromEntries((current?.can_edit ? current.sections.flatMap(group => group.fields) : [])
      .filter(field => !field.read_only && Object.hasOwn(values, field.name)).map(field => [field.name, values[field.name]]));
  };
  const count = Object.keys(changes()).length;
  if (!count) return null;
  const save = async () => {
    const values = changes(), saved = Object.keys(values).length;
    if (!saved) return;
    try {
      const { data } = await ctx.api.request<AppState & { saved: number }>('PATCH', `/api/_settings/app/${app}`, { body: { values } });
      draft.set({});
      state.set(data);
      toast(`${saved} setting${saved === 1 ? '' : 's'} saved for everyone`);
    } catch (failure) { fail(failure); }
  };
  return html`<div class="set-bar" role="region" aria-label="Unsaved changes"><span class="dot"></span><strong>${count} unsaved change${count === 1 ? '' : 's'}</strong>
    <span class="m">${state.peek()!.title} settings. These apply to everyone.</span><span class="grow"></span>
    ${Button({ label: 'Discard', onClick: () => { draft.set({}); toast('Changes discarded'); } })}${Button({ label: 'Save for everyone', variant: 'primary', onClick: () => void save() })}</div>`;
}
