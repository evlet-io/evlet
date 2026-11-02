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
import type { Me } from './settings';
import type { EvaStatus } from '../eva/client';

/** The route table. These are the paths the Python server serves the app for (evlet/ui/routes.py).
 * The model screens live under the frontend's base from the config block (/app for the desk).
 * <base>/:model/new comes before <base>/:model/:id so "new" is never taken for an id. <base>/:model/:id/view
 * is an older link to a record and opens the record. Reports are not part of this UI; their
 * addresses still open and say so. Accounts moved to Controls (/controls/users), a separate page.
 * Settings are /settings/<page> and /settings/app/<app>. */
const FIXED = {
  home: '/',
  login: '/login',
  docs: '/docs',
  topic: '/docs/:topic',
  report: '/reports/:report',
  settings: '/settings/:page',
  appSettings: '/settings/app/:app',
  // Eva, when the server has it for this person (GET /api/_eva). Tasks come before a chat id.
  eva: '/eva',
  evaTasks: '/eva/tasks',
  evaChat: '/eva/:conversation',
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
  /** The person's settings from /api/_settings/me; null on a server without them. */
  me: Cell<Me | null>;
  /** Bumped when the photo changes, so its address changes. */
  photo: Cell<number>;
  /** Eva for this person, or null: off in config.py or Controls, or no Allow Eva. Then nothing of Eva shows. */
  eva: Cell<EvaStatus | null>;
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

/** The address of a settings page: 'profile' or 'app/<app>'. */
export const settingsHref = (router: AppRouter, page: string) =>
  page.startsWith('app/') ? router.href('appSettings', { app: page.slice(4) }) : router.href('settings', { page });
export const goSettings = (router: AppRouter, page: string) =>
  page.startsWith('app/') ? router.go('appSettings', { app: page.slice(4) }) : router.go('settings', { page });

/** Docs are on unless the server's session says they are off (application.docs is null). */
export const docsOn = (session: Session | null | undefined) => session?.application?.docs !== null;

export const newPageState =() => ({ crumbs: cell<Crumb[]>([]), createHere: cell<(() => void) | null>(null), pageSearch: cell<HTMLInputElement | null>(null) });
