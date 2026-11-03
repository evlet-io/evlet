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
import { Button, EmptyState } from '../ui/base';
import { apiLinks, setLinkSource } from '../ui/fields';
import { closeDialogs, closePopover, DialogFooter, openModal } from '../ui/layers';
import { AppClient } from './client';
import { docsOn, goSettings, newPageState, settingsHref, type AppContext, type AppRouter } from './context';
import { DocsPage } from './docs';
import { ListPage } from './list-page';
import { Connecting, LoginScreen, TwoStepScreen, type Challenge } from './login';
import { rootModels } from './meta';
import { openPalette } from './palette';
import { Prefs } from './prefs';
import { resolveTheme } from '../ui/theme';
import { RecordPage } from './record-page';
import { applyMe, chooseTheme, loadMe, PAGES as SETTINGS_PAGES, SettingsView, showCodes, type Me } from './settings';
import { Shell } from './shell';
import { useSlips } from './slips';
import { evaStatus, type EvaStatus } from '../eva/client';
import { EvaPage } from '../eva/chat';
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

type Phase = 'connecting' | 'signin' | 'twostep' | 'ready';

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
    context.peek()?.prefs.dispose();
    const prefs = options.storage === undefined ? new Prefs(current.user!.username) : new Prefs(current.user!.username, options.storage);
    applyMe(settings);
    const links = apiLinks(api, schema);
    setLinkSource(links);
    const ctx: AppContext = {
      api, router, schema, session: current, user: current.user!, prefs, links, dev: Boolean(options.dev), libraryHref: options.libraryHref ?? '/evlet.html',
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
    if (!now || now.name === 'login' || now.name === 'home') await goStart(ctx, true);
  };

  const connect = async () => {
    phase.set('connecting');
    error.set('');
    try {
      const current = await api.session();
      if (!alive) return;
      session.set(current);
      if (current.user) await enter(current);
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
    await enter({ ...(session.peek() ?? {}), ...current, setup: false });
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
    if (!(await router.go(docsOn(session.peek()) ? 'docs' : 'home', {}))) return;
    try { await api.logout(); }
    catch (failure) {
      // 401: the server already ended the session. Anything else: the session may still be
      // valid, so the user stays signed in and can try again.
      if (!(failure instanceof ApiError && failure.kind === 'auth')) { signOutFailed(failure instanceof ApiError ? failure.message : 'The server did not answer.'); return; }
    }
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
    twostep: () => TwoStepScreen({ session: session.peek(), challenge: challenge.peek()!, error, busy, onSubmit: code => void sendCode(code),
      onCancel: () => { challenge.set(null); error.set(''); phase.set('signin'); } }),
    signin: () => LoginScreen({ session: session.peek(), error, busy, onSubmit: (username, password, setup) => void signIn(username, password, setup), onRetry: () => void connect() }),
    ready: () => Shell(context.peek()!, () => Routes(context.peek()!)),
  });
}

/** The page for the current route. A page is built again only when its path changes, not its query. */
function Routes(ctx: AppContext): Node {
  const { router } = ctx;
  const path = derive(() => { const route = router.route.get(); return route ? `${route.name}:${JSON.stringify(route.params)}` : 'none'; });
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
    case 'home': case 'login': return docsOn(ctx.session) ? DocsPage(ctx) : nothingToOpen(ctx);
    case 'docs': return docsOn(ctx.session) ? DocsPage(ctx) : unavailable(ctx);
    case 'topic': return docsOn(ctx.session) ? DocsPage(ctx, route.params.topic) : unavailable(ctx);
    case 'list': return known(route.params.model) ? ListPage(ctx, route.params.model) : unavailable(ctx);
    case 'record': case 'view': return known(route.params.model) ? RecordPage(ctx, route.params.model, route.params.id) : unavailable(ctx);
    case 'create': {
      const model = known(route.params.model);
      return model && model.permissions?.create !== false ? RecordPage(ctx, model.name, 'new') : unavailable(ctx);
    }
    case 'settings': case 'appSettings': {
      const me = ctx.me.peek();
      const target = route.name === 'settings' ? route.params.page : `app/${route.params.app}`;
      if (!me || !(me.pages.includes(target) || me.apps.some(app => `app/${app.id}` === target))) return unavailable(ctx);
      ctx.crumbs.set([{ label: 'Settings', href: settingsHref(ctx.router, me.last_page || 'profile') }, { label: route.name === 'settings' ? SETTINGS_PAGES[target][0] : `${me.apps.find(app => `app/${app.id}` === target)!.title} settings` }]);
      const models = rootModels(ctx.schema);
      return html`<div class="page wide">${SettingsView({ api: ctx.api, me: ctx.me as any, photo: ctx.photo, href: page => settingsHref(ctx.router, page), go: page => void goSettings(ctx.router, page),
        starts: [...(docsOn(ctx.session) ? [['docs', 'Docs'] as [string, string]] : []), ...models.map(model => [model.name, model.plural] as [string, string])],
        changed: () => { const next = ctx.me.peek(); if (next) ctx.prefs.density.set(next.prefs.density); } }, target)}</div>`;
    }
    case 'eva': return ctx.eva.peek() ? EvaPage(ctx, ctx.eva) : unavailable(ctx);
    case 'evaTasks': return ctx.eva.peek()?.tasks ? TasksPage(ctx, ctx.eva) : unavailable(ctx);
    case 'evaChat': {
      const id = Number(route.params.conversation);
      return ctx.eva.peek() && Number.isSafeInteger(id) && id > 0 ? EvaPage(ctx, ctx.eva, id) : unavailable(ctx);
    }
    case 'report': return notHere(ctx, 'Reports', 'Reports are not shown in this interface',
      'Python reports keep their API and permissions. Model lists, boards and measures cover the data in this interface.');
    default: return unavailable(ctx);
  }
}

/** An address the server still serves for a screen this interface does not have. */
function notHere(ctx: AppContext, crumb: string, title: string, text: string): HTMLElement {
  ctx.crumbs.set([{ label: crumb }]);
  return html`<div class="page">${EmptyState({ icon: 'info', tint: 'slate', title, text, action: startButton(ctx) })}</div>` as HTMLElement;
}

function unavailable(ctx: AppContext): HTMLElement {
  ctx.crumbs.set([{ label: 'Not available' }]);
  return html`<div class="page">${EmptyState({ icon: 'search', tint: 'slate', title: 'This page is not available',
    text: 'The address may be wrong, or your role cannot open it.', action: startButton(ctx) })}</div>` as HTMLElement;
}

/** The start page when Docs are off and the role can open no list. */
function nothingToOpen(ctx: AppContext): HTMLElement {
  ctx.crumbs.set([]);
  return html`<div class="page">${EmptyState({ icon: 'layers', tint: 'slate', title: 'Nothing to open yet',
    text: 'Your role cannot open any list here. Ask an administrator for access.' })}</div>` as HTMLElement;
}

/** Where a signed-in user starts: Docs, or the first list they can open when Docs are off. */
function goStart(ctx: AppContext, replace = false) {
  const start = ctx.me.peek()?.prefs.start;
  if (start === 'docs' && docsOn(ctx.session)) return ctx.router.go('docs', {}, {}, { replace });
  if (start && rootModels(ctx.schema).some(model => model.name === start)) return ctx.router.go('list', { model: start }, {}, { replace });
  if (docsOn(ctx.session)) return ctx.router.go('docs', {}, {}, { replace });
  const first = rootModels(ctx.schema)[0];
  return first ? ctx.router.go('list', { model: first.name }, {}, { replace }) : ctx.router.go('home', {}, {}, { replace });
}

function startButton(ctx: AppContext): Node | undefined {
  if (docsOn(ctx.session)) return Button({ label: 'Open Docs', onClick: () => void ctx.router.go('docs', {}) });
  const first = rootModels(ctx.schema)[0];
  return first ? Button({ label: `Open ${first.plural}`, onClick: () => void goStart(ctx) }) : undefined;
}

