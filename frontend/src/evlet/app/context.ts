// What every screen gets: the signed-in user, the schema, the API client, the router, the
// per-user preferences and the shell's shared state.

import type { Session, User } from '../api';
import { cell, type Cell } from '../reactive';
import { createRouter, type Router } from '../router';
import type { SchemaIndex } from '../model/schema';
import type { LinkSource } from '../ui/fields';
import type { AppClient } from './client';
import { readConfig } from './config';
import type { Prefs } from './prefs';

/** The route table. These are the paths the Python server serves the app for (evlet/ui/routes.py).
 * The model screens live under the frontend's base from the config block (/app for the desk).
 * <base>/:model/new comes before <base>/:model/:id so "new" is never taken for an id. <base>/:model/:id/view
 * is an older link to a record and opens the record. Reports are not part of this UI; their
 * addresses still open and say so. Accounts moved to Controls (/controls/users), a separate page. */
const FIXED = {
  home: '/',
  login: '/login',
  docs: '/docs',
  topic: '/docs/:topic',
  report: '/reports/:report',
} as const;
const MODELS = {
  create: '/:model/new',
  record: '/:model/:id',
  view: '/:model/:id/view',
  list: '/:model',
} as const;
export type Routes = typeof FIXED & typeof MODELS;
export type AppRouter = Router<Routes>;

/** The route table for a base (where the model screens are mounted) and a Docs path. */
export function appRoutes(base: string, docs = '/docs'): Routes {
  const prefix = base.replace(/\/$/, '');
  return {
    ...FIXED, docs, topic: `${docs}/:topic`,
    ...Object.fromEntries(Object.entries(MODELS).map(([name, path]) => [name, prefix + path])),
  } as unknown as Routes;
}

/** A router over the page's config block, unless base and docs are given. */
export function appRouter(options: { base?: string; docs?: string; window?: Window } = {}): AppRouter {
  const config = readConfig(options.window?.document);
  return createRouter(appRoutes(options.base ?? config.base, options.docs ?? config.docs ?? '/docs'), { window: options.window });
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

/** Docs are on unless the server's session says they are off (application.docs is null). */
export const docsOn = (session: Session | null | undefined) => session?.application?.docs !== null;

export const newPageState =() => ({ crumbs: cell<Crumb[]>([]), createHere: cell<(() => void) | null>(null), pageSearch: cell<HTMLInputElement | null>(null) });
