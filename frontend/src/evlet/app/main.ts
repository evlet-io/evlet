// Entry of the production app (index.html). The Python server serves it for every UI route;
// in development Vite serves it and proxies /api to the backend. The component Library
// (evlet.html) is a development page and is not part of the build.

import '../ui/styles.css';
import './screens.css';
import { mount } from '../dom';
import { App } from './app';
import { appRouter } from './context';

const router = appRouter();
router.start();
mount(document.getElementById('evlet-app')!, () => App({ router, dev: import.meta.env.DEV, libraryHref: '/evlet.html' }));
