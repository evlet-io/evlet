// Entry of the production app (index.html). The Python server serves it for every UI route
// with a config block saying where the app is mounted and where the API is; in development
// Vite serves it and proxies /api to the backend. The component Library (evlet.html) is a
// development page and is not part of the build.

import '../ui/styles.css';
import './screens.css';
import { mount } from '../dom';
import { App } from './app';
import { AppClient } from './client';
import { readConfig } from './config';
import { appRouter } from './context';

const config = readConfig();
const router = appRouter({ base: config.base, docs: config.docs ?? undefined });
router.start();
mount(document.getElementById('evlet-app')!, () => App({ router, api: new AppClient({ api: config.api }), dev: import.meta.env.DEV, libraryHref: '/evlet.html' }));
