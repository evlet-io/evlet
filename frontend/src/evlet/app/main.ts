// Entry of the production app (index.html). The Python server serves it for every UI route;
// in development Vite serves it and proxies /api to the backend. Controls (/controls) loads as its
// own chunk. The component Library (evlet.html) is a development page and is not part of the build.

import '../ui/styles.css';
import './screens.css';
import { mount } from '../dom';
import { App } from './app';
import { appRouter } from './context';

const root = document.getElementById('evlet-app')!;
if (/^\/controls(\/|$)/.test(window.location.pathname)) {
  void import('../controls/app').then(({ Controls, controlsRouter }) => {
    const router = controlsRouter();
    router.start();
    mount(root, () => Controls({ router }));
  });
} else {
  const router = appRouter();
  router.start();
  mount(root, () => App({ router, dev: import.meta.env.DEV, libraryHref: '/evlet.html' }));
}
