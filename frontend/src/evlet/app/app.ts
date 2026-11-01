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
import { newPageState, type AppContext, type AppRouter } from './context';
import { DocsPage } from './docs';
import { ListPage } from './list-page';
import { Connecting, LoginScreen } from './login';
import { rootModels } from './meta';
import { openPalette } from './palette';
import { applyTheme, Prefs } from './prefs';
import { RecordPage } from './record-page';
import { Shell } from './shell';

export interface AppOptions {
  router: AppRouter;
  api?: AppClient;
  /** Development build: shows the Library link in the sidebar. */
  dev?: boolean;
  libraryHref?: string;
  /** Where preferences are kept; null keeps them in memory. */
  storage?: Storage | null;
}

type Phase = 'connecting' | 'signin' | 'ready';

export function App(options: AppOptions): Node {
  const { router } = options;
  const api = options.api ?? new AppClient();
  const phase = cell<Phase>('connecting', { name: 'app.phase' });
  const session = cell<Session | null>(null, { name: 'app.session' });
  const context = cell<AppContext | null>(null, { name: 'app.context' });
  const error = cell(''), busy = cell(false);
  let alive = true;
  onCleanup(() => { alive = false; context.peek()?.prefs.dispose(); });

  const enter = async (current: Session) => {
    const schema = new SchemaIndex(await api.schema<AppSchema>());
    if (!alive) return;
    context.peek()?.prefs.dispose();
    const prefs = options.storage === undefined ? new Prefs(current.user!.username) : new Prefs(current.user!.username, options.storage);
    applyTheme(prefs.theme.peek());
    const links = apiLinks(api, schema);
    setLinkSource(links);
    const ctx: AppContext = {
      api, router, schema, session: current, user: current.user!, prefs, links, dev: Boolean(options.dev), libraryHref: options.libraryHref ?? '/evlet.html',
      ...newPageState(),
      openPalette: () => openPalette(ctx),
      toggleSidebar: () => prefs.collapsed.set(!prefs.collapsed.peek()),
      signOut,
    };
    session.set(current);
    context.set(ctx);
    error.set('');
    phase.set('ready');
    const now = router.route.peek();
    if (!now || now.name === 'login' || now.name === 'home') await router.go('docs', {}, {}, { replace: true });
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

  const signIn = async (username: string, password: string, setup: boolean) => {
    busy.set(true);
    error.set('');
    try {
      if (setup) await api.setup(username, password);
      const current = await api.login(username, password);
      // The login answer carries the user; the session adds the application details.
      await enter({ ...(session.peek() ?? {}), ...current, setup: false });
    } catch (failure) {
      error.set(failure instanceof ApiError ? failure.message : 'Unable to sign in.');
      // A finished setup should not be offered again.
      if (setup && failure instanceof ApiError && failure.kind !== 'network') session.set({ ...(session.peek() ?? { user: null }), setup: false });
    } finally {
      busy.set(false);
    }
  };

  async function signOut() {
    // Leaving the page first lets an unsaved record ask.
    if (!(await router.go('docs', {}))) return;
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
    // The sign-in screen does not keep the last user's theme.
    applyTheme('system');
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
    case 'home': case 'login': case 'docs': return DocsPage(ctx);
    case 'topic': return DocsPage(ctx, route.params.topic);
    case 'list': return known(route.params.model) ? ListPage(ctx, route.params.model) : unavailable(ctx);
    case 'record': case 'view': return known(route.params.model) ? RecordPage(ctx, route.params.model, route.params.id) : unavailable(ctx);
    case 'create': {
      const model = known(route.params.model);
      return model && model.permissions?.create !== false ? RecordPage(ctx, model.name, 'new') : unavailable(ctx);
    }
    case 'accounts': return notHere(ctx, 'Accounts', 'Accounts are not managed in this interface',
      'Administrators manage accounts through the /api/auth/users routes. Your role and permissions are unchanged.');
    case 'report': return notHere(ctx, 'Reports', 'Reports are not shown in this interface',
      'Python reports keep their API and permissions. Model lists, boards and measures cover the data in this interface.');
    default: return unavailable(ctx);
  }
}

/** An address the server still serves for a screen this interface does not have. */
function notHere(ctx: AppContext, crumb: string, title: string, text: string): HTMLElement {
  ctx.crumbs.set([{ label: crumb }]);
  return html`<div class="page">${EmptyState({ icon: 'info', tint: 'slate', title, text,
    action: Button({ label: 'Open Docs', onClick: () => void ctx.router.go('docs', {}) }) })}</div>` as HTMLElement;
}

function unavailable(ctx: AppContext): HTMLElement {
  ctx.crumbs.set([{ label: 'Not available' }]);
  return html`<div class="page">${EmptyState({ icon: 'search', tint: 'slate', title: 'This page is not available',
    text: 'The address may be wrong, or your role cannot open it.', action: Button({ label: 'Open Docs', onClick: () => void ctx.router.go('docs', {}) }) })}</div>` as HTMLElement;
}

