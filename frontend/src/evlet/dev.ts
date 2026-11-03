// Development entry for the Evlet UI framework, served by Vite at /evlet.html (npm run dev).
// It is not part of the production build. The Library page runs against a sample order
// replayed from the recorded backend responses, so record-bound components work offline.

import './ui/styles.css';
import { createClient } from './api';
import { html, mount } from './dom';
import { RecordStore } from './model/record';
import { SchemaIndex, type AppSchema } from './model/schema';
import { cell, effect } from './reactive';
import { createRouter } from './router';
import { apiLinks, setLinkSource } from './ui/fields';
import { LibraryPage } from './ui/library';
import { mountToasts } from './ui/layers';
import { applyTheme, type Theme } from './ui/theme';
import { copy, responses, server } from './tests/server';

const schema = responses.schema.body as AppSchema;

// The sample order: field changes are kept in memory; lines stay as recorded.
function sampleApi() {
  const detail = copy(responses.detail.body) as Record<string, any>;
  let version = 1;
  const stub = server([
    ['GET', /\/api\/order\/1$/, () => ({ status: 200, etag: `"dev-${version}"`, body: copy(detail) })],
    ['GET', /\/api\/order\/1\/lines\/lines\?/, 'lines_page'],
    ['GET', /\/api\/customer\/1$/, { ...responses.customer, status: 200 }],
    ['GET', /\/api\/product\/1$/, { ...responses.product, status: 200 }],
    ['GET', /\/api\/customer(\?.*)?$/, { status: 200, etag: null, body: { items: [responses.customer.body], total: 1, limit: 8, offset: 0 } }],
    ['GET', /\/api\/product(\?.*)?$/, { status: 200, etag: null, body: { items: [responses.product.body], total: 1, limit: 8, offset: 0 } }],
    ['PATCH', /\/api\/order\/1$/, sent => {
      for (const [name, value] of Object.entries(sent.body ?? {})) if (!name.startsWith('_') && name !== 'children') detail[name] = value;
      version++;
      return { status: 200, etag: `"dev-${version}"`, body: copy(detail) };
    }],
  ]);
  return createClient({ fetch: stub.fetch });
}

const api = sampleApi();
setLinkSource(apiLinks(api, new SchemaIndex(schema)));
mountToasts();
const record = new RecordStore({ api, schema, model: 'order' });
void record.open('0000000000001');

const theme = cell<Theme>('system');
effect(() => applyTheme(theme.get()));

const router = createRouter({ library: '/evlet.html' });
router.start();
mount(document.getElementById('evlet')!, () => () => router.route.get()?.name === 'library'
  ? LibraryPage({ record, theme })
  : html`<p style="padding:24px">This page is not part of the framework preview. Open the <a href="/evlet.html">Library</a>.</p>`);
