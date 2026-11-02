// Entry of the production app (index.html). The Python server serves it for every UI route
// with a config block saying where the app is mounted and where the API is; in development
// Vite serves it and proxies /api to the backend. Controls (/controls) loads as its own chunk.
// The component Library (evlet.html) is a development page and is not part of the build.

import '../ui/styles.css';
import './screens.css';
import { mount } from '../dom';
import { App } from './app';
import { AppClient } from './client';
import { readConfig } from './config';
import { appRouter } from './context';
import { PortalSettings } from './settings';

const root = document.getElementById('evlet-app')!;
if (/^\/controls(\/|$)/.test(window.location.pathname)) {
  void import('../controls/app').then(({ Controls, controlsRouter }) => {
    const router = controlsRouter();
    router.start();
    mount(root, () => Controls({ router }));
  });
} else if (readConfig().frontend === 'evlet.settings' && readConfig().realm === 'portal') {
  mount(root, () => PortalSettings(new AppClient({ api: readConfig().api })));
} else {
  const config = readConfig();
  const router = appRouter({ base: config.base, docs: config.docs ?? undefined });
  router.start();
  mount(root, () => App({ router, api: new AppClient({ api: config.api }), dev: import.meta.env.DEV, libraryHref: '/evlet.html' }));
}
