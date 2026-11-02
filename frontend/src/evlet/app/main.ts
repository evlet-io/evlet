// Entry of the model-driven app, served by Vite at /ui (npm run dev). It talks to the Python
// backend through the dev server's /api proxy. It is not part of the production build yet: the
// SolidJS app stays the installed UI until the switch (docs/handoff-plan.md, phase 15).

import '../ui/styles.css';
import './screens.css';
import { mount } from '../dom';
import { App } from './app';
import { appRouter } from './context';

const router = appRouter({ base: '/ui' });
router.start();
mount(document.getElementById('evlet-app')!, () => App({ router, dev: import.meta.env.DEV, libraryHref: '/evlet.html' }));
