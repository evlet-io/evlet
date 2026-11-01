// Evlet router: a typed route table over the History API.
//
//   const router = createRouter({ home: '/', list: '/m/:model', record: '/m/:model/:id' });
//   router.start();
//   router.href('record', { model: 'invoice', id: '42' }) // '/m/invoice/42'
//   effect(() => console.log(router.route.get()?.name));
//
// Same-origin links that match a route are handled in the page. Guards can stop a
// navigation, for example to confirm leaving a record with unsaved changes.

import { cell, derive, onCleanup, type Readable } from './reactive';

type ParamNames<P extends string> =
  P extends `${string}:${infer Name}/${infer Rest}` ? Name | ParamNames<`/${Rest}`>
  : P extends `${string}:${infer Name}` ? Name : never;
/** The parameters of a route pattern: Params<'/m/:model/:id'> is { model: string; id: string }. */
export type Params<P extends string> = { [K in ParamNames<P>]: string };
export type Query = Record<string, string>;

export interface Route<R extends Record<string, string> = Record<string, string>> {
  name: keyof R & string;
  params: Record<string, string>;
  query: Query;
  /** Path with the query string, without the base. */
  url: string;
}

/** Return false (or resolve to false) to stop the navigation. */
export type Guard<R extends Record<string, string>> = (to: Route<R> | null, from: Route<R> | null) => boolean | Promise<boolean>;

export interface RouterOptions { base?: string; window?: Window }

interface Compiled { name: string; pattern: RegExp; keys: string[]; parts: string[] }

const PARAM = /^:([A-Za-z_][A-Za-z0-9_]*)$/;

function compile(name: string, path: string): Compiled {
  if (!path.startsWith('/')) throw new Error(`Route ${name} must start with /`);
  const keys: string[] = [], parts = path.split('/').filter(Boolean);
  const source = parts.map(part => {
    const param = PARAM.exec(part);
    if (param) { keys.push(param[1]); return '/([^/]+)'; }
    return '/' + part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }).join('');
  return { name, pattern: new RegExp(`^${source || '/'}/?$`), keys, parts };
}

export class Router<R extends Record<string, string>> {
  private readonly table: Compiled[];
  private readonly current = cell<Route<R> | null>(null, { name: 'router.route', equals: (a, b) => a?.url === b?.url && a?.name === b?.name });
  private readonly guards = new Set<Guard<R>>();
  private readonly window: Window;
  private readonly base: string;
  private controller = new AbortController();
  private position = 0;
  private ticket = 0;
  private stop: (() => void) | null = null;
  /** The matched route, or null when the location matches no route. */
  readonly route: Readable<Route<R> | null>;

  constructor(readonly routes: R, options: RouterOptions = {}) {
    this.table = Object.entries(routes).map(([name, path]) => compile(name, path));
    this.window = options.window ?? window;
    this.base = (options.base ?? '').replace(/\/$/, '');
    this.route = derive(() => this.current.get(), { name: 'router.route (read only)' });
  }

  /** Aborted when the route changes; pass it to requests that belong to the current page. */
  get signal(): AbortSignal { return this.controller.signal; }

  /** Match a path (with optional query string) against the table. */
  match(url: string): Route<R> | null {
    const parsed = new URL(url, 'http://local');
    let path = parsed.pathname;
    if (this.base) {
      if (path !== this.base && !path.startsWith(this.base + '/')) return null;
      path = path.slice(this.base.length) || '/';
    }
    for (const route of this.table) {
      const found = route.pattern.exec(path);
      if (!found) continue;
      const params: Record<string, string> = {};
      try {
        route.keys.forEach((key, index) => { params[key] = decodeURIComponent(found[index + 1]); });
      } catch { return null; }
      return { name: route.name, params, query: Object.fromEntries(parsed.searchParams), url: path + parsed.search };
    }
    return null;
  }

  /** The URL for a named route. Parameters are encoded; missing ones are an error. */
  href<N extends keyof R & string>(name: N, params: Params<R[N]>, query: Query = {}): string {
    const route = this.table.find(item => item.name === name);
    if (!route) throw new Error(`Unknown route ${name}`);
    const values = params as Record<string, string>;
    const path = '/' + route.parts.map(part => {
      const param = PARAM.exec(part);
      if (!param) return part;
      const value = values[param[1]];
      if (value === undefined || value === '') throw new Error(`Route ${name} needs ${param[1]}`);
      return encodeURIComponent(value);
    }).join('/');
    const search = new URLSearchParams(query).toString();
    return this.base + path + (search ? '?' + search : '');
  }

  /** Add a guard. Inside a scope it is removed when the scope is disposed. */
  guard(check: Guard<R>): () => void {
    this.guards.add(check);
    const remove = () => { this.guards.delete(check); };
    onCleanup(remove);
    return remove;
  }

  /**
   * Ask before leaving while `when()` is true, for example a record with unsaved changes.
   * `ask` shows an in-page confirmation and resolves to true to leave. While `when()` is
   * true, closing or reloading the tab also asks (the browser shows its own prompt there).
   */
  confirmLeave(when: () => boolean, ask: (to: Route<R> | null) => boolean | Promise<boolean>): () => void {
    const unload = (event: BeforeUnloadEvent) => { if (when()) { event.preventDefault(); event.returnValue = ''; } };
    this.window.addEventListener('beforeunload', unload);
    const remove = this.guard(to => !when() || ask(to));
    const stop = () => { remove(); this.window.removeEventListener('beforeunload', unload); };
    onCleanup(stop);
    return stop;
  }

  /** Navigate to a URL inside the application. Resolves to false when a guard stopped it. */
  async navigate(url: string, options: { replace?: boolean } = {}): Promise<boolean> {
    const to = this.match(url);
    const ticket = ++this.ticket;
    if (!(await this.allowed(to))) return false;
    // A later navigation started while the guards were deciding: it wins.
    if (ticket !== this.ticket) return false;
    const history = this.window.history;
    if (options.replace) history.replaceState({ evlet: this.position }, '', url);
    else history.pushState({ evlet: ++this.position }, '', url);
    this.commit(to);
    return true;
  }

  /** Navigate to a named route. */
  go<N extends keyof R & string>(name: N, params: Params<R[N]>, query: Query = {}, options: { replace?: boolean } = {}) {
    return this.navigate(this.href(name, params, query), options);
  }

  /** Read the current location and start handling links and the back button. Returns stop. */
  start(): () => void {
    if (this.stop) return this.stop;
    const history = this.window.history;
    this.position = typeof history.state?.evlet === 'number' ? history.state.evlet : 0;
    if (history.state?.evlet !== this.position) history.replaceState({ ...history.state, evlet: this.position }, '');
    this.commit(this.match(this.locationUrl()));
    const click = (event: MouseEvent) => this.click(event);
    const pop = () => { void this.pop(); };
    this.window.document.addEventListener('click', click);
    this.window.addEventListener('popstate', pop);
    this.stop = () => {
      this.window.document.removeEventListener('click', click);
      this.window.removeEventListener('popstate', pop);
      this.stop = null;
    };
    onCleanup(this.stop);
    return this.stop;
  }

  private locationUrl() {
    const location = this.window.location;
    return location.pathname + location.search;
  }

  private async allowed(to: Route<R> | null) {
    const from = this.current.peek();
    for (const check of [...this.guards]) {
      try {
        if (!(await check(to, from))) return false;
      } catch {
        return false;
      }
    }
    return true;
  }

  private commit(to: Route<R> | null) {
    const before = this.current.peek();
    if (before?.url !== to?.url || before?.name !== to?.name) {
      this.controller.abort();
      this.controller = new AbortController();
    }
    this.current.set(to);
  }

  // Back and forward: the URL has already changed. If a guard says no, step back to where we were.
  private restoring = false;
  private async pop() {
    if (this.restoring) { this.restoring = false; return; }
    const state = this.window.history.state;
    const target = typeof state?.evlet === 'number' ? state.evlet : 0;
    const to = this.match(this.locationUrl());
    const ticket = ++this.ticket;
    if (await this.allowed(to)) {
      if (ticket !== this.ticket) return;
      this.position = target;
      this.commit(to);
      return;
    }
    const delta = this.position - target;
    if (delta) { this.restoring = true; this.window.history.go(delta); }
  }

  private click(event: MouseEvent) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as Element | null)?.closest?.('a');
    if (!anchor || !anchor.hasAttribute('href')) return;
    if ((anchor.target && anchor.target !== '_self') || anchor.hasAttribute('download') || anchor.getAttribute('rel')?.includes('external')) return;
    const url = new URL(anchor.href, this.window.location.href);
    if (url.origin !== this.window.location.origin) return;
    // Links to places the table does not know (the API, downloads) stay ordinary links.
    if (!this.match(url.pathname + url.search)) return;
    event.preventDefault();
    void this.navigate(url.pathname + url.search + url.hash);
  }
}

/** Create a router for a route table of name -> path pattern. */
export function createRouter<const R extends Record<string, string>>(routes: R, options: RouterOptions = {}): Router<R> {
  return new Router(routes, options);
}
