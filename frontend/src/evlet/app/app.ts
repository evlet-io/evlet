// The model-driven app: sign in (or first-run setup), then the shell with Docs, a list and a
// record view for every model the role can read, and the command palette. It needs no per-app
// frontend code: everything comes from /api/schema, record details and the list routes.
//
//   const router = appRouter();
//   router.start();
//   mount(document.getElementById('app')!, () => App({ router }));

import { html, match, type Child } from '../dom';
import { ApiError, type Session } from '../api';
import { cell, derive, onCleanup } from '../reactive';
import { SchemaIndex, type AppSchema } from '../model/schema';
import { setCurrencyDecimals, setCurrencyDisplays } from '../model/format';
import { Button, EmptyState } from '../ui/base';
import { apiLinks, setLinkSource } from '../ui/fields';
import { closeDialogs, closePopover, DialogFooter, openModal } from '../ui/layers';
import { AppClient } from './client';
import { docsOn, goSettings, newPageState, settingsHref, type AppContext, type AppRouter } from './context';
import { DocsPage } from './docs';
import { ExplorerPage, isExplorer } from './explorer';
import { ListPage } from './list-page';
import { Connecting, LoginScreen, SetPasswordScreen, TwoStepScreen, type Challenge } from './login';
import { rootModels } from './meta';
import { openPalette } from './palette';
import { Prefs } from './prefs';
import { resolveTheme } from '../ui/theme';
import { RecordPage } from './record-page';
import { ReportPage, ReportsPage, reportsOf } from './report-page';
import { applyMe, chooseTheme, loadMe, PAGES as SETTINGS_PAGES, SettingsView, showCodes, type Me } from './settings';
import { Shell } from './shell';
import { useSlips } from './slips';
import { evaStatus, type EvaStatus } from '../eva/client';
import { EvaPage } from '../eva/chat';
import { instanceKey, LocalUnsaved } from './unsaved';
import { TasksPage } from '../eva/tasks';

export interface AppOptions {
  router: AppRouter;
  api?: AppClient;
  /** Development build: shows the Library link in the sidebar. */
  dev?: boolean;
  libraryHref?: string;
  /** Where preferences are kept; null keeps them in memory. */
  storage?: Storage | null;
}

type Phase = 'connecting' | 'signin' | 'password' | 'twostep' | 'ready';

export function App(options: AppOptions): Node {
  const { router } = options;
  const api = options.api ?? new AppClient();
  const phase = cell<Phase>('connecting', { name: 'app.phase' });
  const session = cell<Session | null>(null, { name: 'app.session' });
  const context = cell<AppContext | null>(null, { name: 'app.context' });
  const error = cell(''), busy = cell(false);
  const challenge = cell<Challenge | null>(null, { name: 'app.challenge' });
  let alive = true;
  onCleanup(() => { alive = false; context.peek()?.prefs.dispose(); });

  const enter = async (current: Session) => {
    const [schemaData, settings, eva] = await Promise.all([api.schema<AppSchema>(), loadMe(api), evaStatus(api).catch(() => null)]);
    const schema = new SchemaIndex(schemaData);
    if (!alive) return;
    // How this site shows each currency code (Controls, Currencies), before anything formats money.
    setCurrencyDisplays(schemaData.currency_display);
    // Decimal places of every code that does not have 2 (ISO 4217 with this site's changes): KWD 3, JPY 0.
    setCurrencyDecimals(schemaData.currency_decimals);
    context.peek()?.prefs.dispose();
    const prefs = options.storage === undefined ? new Prefs(current.user!.username) : new Prefs(current.user!.username, options.storage);
    const unsaved = options.storage === undefined ? new LocalUnsaved(instanceKey(current), current.user!.username)
      : new LocalUnsaved(instanceKey(current), current.user!.username, options.storage);
    // Another person signed in on this browser last: their unsaved new records go. Expired ones go too.
    unsaved.signedIn();
    unsaved.purge(settings?.unsaved?.limits);
    applyMe(settings);
    const links = apiLinks(api, schema, { recordHref: (model, id) => router.href('record', { model, id }) });
    setLinkSource(links);
    const ctx: AppContext = {
      api, router, schema, session: current, user: current.user!, prefs, unsaved, links, dev: Boolean(options.dev), libraryHref: options.libraryHref ?? '/evlet.html',
      me: cell<Me | null>(settings, { name: 'app.me' }), photo: cell(0), eva: cell<EvaStatus | null>(eva, { name: 'app.eva' }),
      ...newPageState(),
      openPalette: () => openPalette(ctx),
      toggleSidebar: () => prefs.collapsed.set(!prefs.collapsed.peek()),
      toggleTheme: () => void chooseTheme(api, ctx.me, resolveTheme(ctx.me.peek()?.prefs.theme ?? 'system') === 'dark' ? 'light' : 'dark'),
      signOut,
    };
    session.set(current);
    context.set(ctx);
    useSlips(ctx);
    error.set('');
    phase.set('ready');
    const now = router.route.peek();
    // Controls and the AI consent page are their own pages: after signing in from /login?next=..., open them there.
    const next = now?.name === 'login' ? now.query.next ?? '' : '';
    if (/^\/controls(\/[a-z]+)?$/.test(next) || /^\/_\/oauth\/authorize\?[^#]*$/.test(next)) { window.location.assign(next); return; }
    // Python page routes match no route here; they, sign-in and the root open the start page.
    if (!now || now.name === 'login' || now.name === 'home' && !isExplorer(now)) await goStart(ctx, true);
  };

  const connect = async () => {
    phase.set('connecting');
    error.set('');
    try {
      const current = await api.session();
      if (!alive) return;
      session.set(current);
      // Signed in by a development link without a password yet: choose one before anything else.
      if (current.user && current.password_needed) phase.set('password');
      else if (current.user) await enter(current);
      else phase.set('signin');
    } catch (failure) {
      if (!alive) return;
      error.set(failure instanceof ApiError ? failure.message : 'Unable to connect.');
      phase.set('signin');
    }
  };

  const signedIn = async (current: Session & { backup_codes?: string[] }) => {
    challenge.set(null);
    // The login answer carries the user; the session adds the application details.
    await enter({ ...(session.peek() ?? {}), ...current, setup: false, password_needed: false });
    if (current.backup_codes?.length) showCodes(current.backup_codes);
  };

  const signIn = async (username: string, password: string, setup: boolean) => {
    busy.set(true);
    error.set('');
    try {
      if (setup) await api.setup(username, password);
      const current = await api.login(username, password) as Session & Partial<Challenge>;
      if (current.two_step && current.ticket) { challenge.set(current as Challenge); phase.set('twostep'); return; }
      await signedIn(current);
    } catch (failure) {
      error.set(failure instanceof ApiError ? failure.message : 'Unable to sign in.');
      // A finished setup should not be offered again.
      if (setup && failure instanceof ApiError && failure.kind !== 'network') session.set({ ...(session.peek() ?? { user: null }), setup: false });
    } finally {
      busy.set(false);
    }
  };

  /** The first password; then two-step set-up when the role requires it, or straight in. */
  const setPassword = async (password: string) => {
    busy.set(true);
    error.set('');
    try {
      const answer = await api.firstPassword(password);
      if (answer.two_step && answer.ticket) { challenge.set(answer as Challenge); phase.set('twostep'); return; }
      const current = { ...(session.peek() ?? { user: null }), password_needed: false };
      await enter(current);
    } catch (failure) {
      error.set(failure instanceof ApiError ? failure.message : 'Unable to save the password.');
    } finally { busy.set(false); }
  };

  /** The second step: a code from the authenticator app or a backup code, or the set-up when the role requires it. */
  const sendCode = async (code: string) => {
    const pending = challenge.peek();
    if (!pending) return;
    busy.set(true);
    error.set('');
    try { await signedIn(await api.secondStep(pending.ticket, code)); }
    catch (failure) {
      const message = failure instanceof ApiError ? failure.message : 'Unable to sign in.';
      if (/Sign in again/.test(message)) { challenge.set(null); phase.set('signin'); }
      error.set(message);
    } finally { busy.set(false); }
  };

  async function signOut() {
    // Leaving the page first lets an unsaved record ask.
    if (!(await router.go('login', {}))) return;
    try { await api.logout(); }
    catch (failure) {
      // 401: the server already ended the session. Anything else: the session may still be
      // valid, so the user stays signed in and can try again.
      if (!(failure instanceof ApiError && failure.kind === 'auth')) { signOutFailed(failure instanceof ApiError ? failure.message : 'The server did not answer.'); return; }
    }
    // Unsaved new records kept in this browser go with the session; server entries stay the person's.
    context.peek()?.unsaved.clearInstance();
    endSession('');
    // Cached record titles and other session data are dropped (apiLinks listens for this).
    window.dispatchEvent(new Event('evlet-session-ended'));
  }

  const signOutFailed = (message: string) => {
    let busy = false;
    openModal({ title: 'You are still signed in', icon: 'info', tint: 'amber', narrow: true,
      body: () => html`<p>Signing out did not finish, so your session is still open. ${message}</p>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Try again', icon: 'log-out', onClick: () => {
        if (busy) return;
        busy = true;
        close();
        void signOut();
      } } }) });
  };

  const endSession = (message: string) => {
    closeDialogs();
    closePopover();
    context.peek()?.prefs.dispose();
    context.set(null);
    api.forget();
    // The sign-in screen does not keep the last user's region or language; it keeps this device's theme.
    applyMe(null);
    error.set(message);
    session.set({ ...(session.peek() ?? { user: null }), user: null, setup: false });
    phase.set('signin');
    void router.go('login', {}, {}, { replace: true });
  };
  const ended = () => { if (phase.peek() === 'ready') endSession('Your session ended. Sign in again.'); };
  window.addEventListener('evlet-session-ended', ended);
  onCleanup(() => window.removeEventListener('evlet-session-ended', ended));
  void connect();

  return match(phase, {
    connecting: () => Connecting(),
    password: () => SetPasswordScreen({ session: session.peek(), error, busy, onSubmit: value => void setPassword(value), onCancel: () => void signOut() }),
    twostep: () => TwoStepScreen({ session: session.peek(), challenge: challenge.peek()!, error, busy, onSubmit: code => void sendCode(code),
      onCancel: () => { challenge.set(null); error.set(''); phase.set('signin'); } }),
    signin: () => LoginScreen({ session: session.peek(), error, busy, onSubmit: (username, password, setup) => void signIn(username, password, setup), onRetry: () => void connect() }),
    ready: () => Shell(context.peek()!, () => Routes(context.peek()!)),
  });
}

/** The page for the current route. A page is built again only when its path changes, not its query. */
function Routes(ctx: AppContext): Node {
  const { router } = ctx;
  // The Explorer is the root path with ?explorer=<view>: it is its own page, and changing its view keeps it.
  const path = derive(() => { const route = router.route.get(); return route ? `${route.name}:${JSON.stringify(route.params)}${isExplorer(route) ? ':explorer' : ''}` : 'none'; });
  return match(path, {}, () => {
    closePopover();
    ctx.crumbs.set([]);
    ctx.createHere.set(null);
    ctx.pageSearch.set(null);
    document.getElementById('ev-view')?.scrollTo?.(0, 0);
    return page(ctx);
  });
}

function page(ctx: AppContext): Child {
  const route = ctx.router.route.peek();
  const known = (name: string) => rootModels(ctx.schema).find(model => model.name === name);
  switch (route?.name) {
    // The root opens the start page. /login only shows while signing out, as the page is left.
    case 'home': {
      if (isExplorer(route)) return ExplorerPage(ctx);
      if (startPage(ctx).name === 'home') return nothingToOpen(ctx);
      queueMicrotask(() => { if (ctx.router.route.peek()?.name === 'home') void goStart(ctx, true); });
      return html`<div class="page"></div>`;
    }
    case 'login': return html`<div class="page"></div>`;
    case 'docs': return docsOn(ctx.session) ? DocsPage(ctx) : unavailable(ctx);
    case 'topic': return docsOn(ctx.session) ? DocsPage(ctx, route.params.topic) : unavailable(ctx);
    case 'list': return known(route.params.model) ? ListPage(ctx, route.params.model) : unavailable(ctx);
    case 'record': case 'view': return known(route.params.model) ? RecordPage(ctx, route.params.model, route.params.id) : unavailable(ctx);
    case 'create': {
      const model = known(route.params.model);
      return model && model.api !== 'read' && model.permissions?.create !== false ? RecordPage(ctx, model.name, 'new') : unavailable(ctx);
    }
    case 'settings': case 'appSettings': {
      const me = ctx.me.peek();
      const target = route.name === 'settings' ? route.params.page : `app/${route.params.app}`;
      if (!me || !(me.pages.includes(target) || me.apps.some(app => `app/${app.id}` === target))) return unavailable(ctx);
      ctx.crumbs.set([{ label: 'Settings', href: settingsHref(ctx.router, me.last_page || 'profile') }, { label: route.name === 'settings' ? SETTINGS_PAGES[target][0] : `${me.apps.find(app => `app/${app.id}` === target)!.title} settings` }]);
      const models = rootModels(ctx.schema);
      return html`<div class="page wide">${ctx.crumbBar?.()}${SettingsView({ api: ctx.api, me: ctx.me as any, photo: ctx.photo, href: page => settingsHref(ctx.router, page), go: page => void goSettings(ctx.router, page),
        starts: [...(docsOn(ctx.session) ? [['docs', 'Docs'] as [string, string]] : []), ...models.map(model => [model.name, model.plural] as [string, string])],
        changed: () => { const next = ctx.me.peek(); if (next) ctx.prefs.density.set(next.prefs.density); } }, target)}</div>`;
    }
    case 'eva': return ctx.eva.peek() ? EvaPage(ctx, ctx.eva) : unavailable(ctx);
    case 'evaTasks': return ctx.eva.peek()?.tasks ? TasksPage(ctx, ctx.eva) : unavailable(ctx);
    case 'evaChat': {
      const id = Number(route.params.conversation);
      return ctx.eva.peek() && Number.isSafeInteger(id) && id > 0 ? EvaPage(ctx, ctx.eva, id) : unavailable(ctx);
    }
    case 'reports': return reportsOf(ctx).length ? ReportsPage(ctx) : unavailable(ctx);
    case 'report': return reportsOf(ctx).some(report => report.name === route.params.report) ? ReportPage(ctx, route.params.report) : unavailable(ctx);
    default: return unavailable(ctx);
  }
}

function unavailable(ctx: AppContext): HTMLElement {
  ctx.crumbs.set([{ label: 'Not available' }]);
  return html`<div class="page">${ctx.crumbBar?.()}${EmptyState({ icon: 'search', tint: 'slate', title: 'This page is not available',
    text: 'The address may be wrong, or your role cannot open it.', action: startButton(ctx) })}</div>` as HTMLElement;
}

/** The start page when the role can open no list and Docs are off. */
function nothingToOpen(ctx: AppContext): HTMLElement {
  ctx.crumbs.set([]);
  return html`<div class="page">${EmptyState({ icon: 'layers', tint: 'slate', title: 'Nothing to open yet',
    text: 'Your role cannot open any list here. Ask an administrator for access.' })}</div>` as HTMLElement;
}

/** Where a signed-in user starts: the start page saved in Settings, else the first list the role
 * can open. Docs are the start only when chosen, or when there is no list to open. */
function startPage(ctx: AppContext): { name: 'docs' | 'list' | 'home'; model?: string } {
  const start = ctx.me.peek()?.prefs.start;
  const models = rootModels(ctx.schema);
  if (start === 'docs' && docsOn(ctx.session)) return { name: 'docs' };
  if (start && models.some(model => model.name === start)) return { name: 'list', model: start };
  if (models[0]) return { name: 'list', model: models[0].name };
  return docsOn(ctx.session) ? { name: 'docs' } : { name: 'home' };
}

function goStart(ctx: AppContext, replace = false) {
  const target = startPage(ctx);
  return target.name === 'list' ? ctx.router.go('list', { model: target.model! }, {}, { replace }) : ctx.router.go(target.name, {}, {}, { replace });
}

/** The button a page that is not available offers: the start page. */
function startButton(ctx: AppContext): Node | undefined {
  const target = startPage(ctx);
  if (target.name === 'docs') return Button({ label: 'Open Docs', onClick: () => void ctx.router.go('docs', {}) });
  const model = target.model ? rootModels(ctx.schema).find(item => item.name === target.model) : undefined;
  return model ? Button({ label: `Open ${model.plural}`, onClick: () => void goStart(ctx) }) : undefined;
}

