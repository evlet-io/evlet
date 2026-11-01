// What every screen gets: the signed-in user, the schema, the API client, the router, the
// per-user preferences and the shell's shared state.

import type { Session, User } from '../api';
import { cell, type Cell } from '../reactive';
import { createRouter, type Router } from '../router';
import type { SchemaIndex } from '../model/schema';
import type { LinkSource } from '../ui/fields';
import type { AppClient } from './client';
import type { Prefs } from './prefs';

/** The route table. /m/:model/new comes before /m/:model/:id so "new" is never taken for an id. */
export const ROUTES = {
  home: '/',
  login: '/login',
  docs: '/docs',
  topic: '/docs/:topic',
  create: '/m/:model/new',
  record: '/m/:model/:id',
  list: '/m/:model',
} as const;
export type Routes = typeof ROUTES;
export type AppRouter = Router<Routes>;

export function appRouter(options: { base?: string; window?: Window } = {}): AppRouter {
  return createRouter(ROUTES, options);
}

export interface Crumb { label: string; href?: string }

export interface AppContext {
  api: AppClient;
  router: AppRouter;
  schema: SchemaIndex;
  session: Session;
  user: User;
  prefs: Prefs;
  links: LinkSource;
  /** Development build: shows the Library link. */
  dev: boolean;
  /** Where the Library page is served in development. */
  libraryHref: string;
  /** Breadcrumbs the current page sets for the top bar. */
  crumbs: Cell<Crumb[]>;
  /** The page's own create action for the C key, when it has one. */
  createHere: Cell<(() => void) | null>;
  /** The page's search input for the / key. */
  pageSearch: Cell<HTMLInputElement | null>;
  openPalette(): void;
  toggleSidebar(): void;
  signOut(): Promise<void>;
}

export const newPageState = () => ({ crumbs: cell<Crumb[]>([]), createHere: cell<(() => void) | null>(null), pageSearch: cell<HTMLInputElement | null>(null) });
