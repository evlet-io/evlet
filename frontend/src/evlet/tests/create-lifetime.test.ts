import { afterEach, expect, it } from 'vitest';
import { byText, click, lastToast, recorded, screen, start, stopAll, typeInto, until, requests, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';

afterEach(() => { stopAll(); localStorage.clear(); });
const created: Recorded = { status: 201, etag: '"1"', body: { id: '0000000000001', title: 'Late create', email: null, phone: null, notes: null } };
function deferredCreate(panel = false) {
  let resolve!: (reply: Recorded) => void;
  const schema = copy(screen.schema);
  schema.body.models.find((model: any) => model.name === 'contact').open_in = panel ? 'panel' : 'page';
  const routes: Route[] = [
    ['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema],
    ['POST', /\/api\/contact$/, () => new Promise(done => { resolve = done; })],
    ['GET', /\/api\/contact\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }],
    ['GET', /\/api\/contact\/0000000000001$/, created],
  ];
  const app = start(routes, panel ? '/app/contact?open=new' : '/app/contact/new', { storage: localStorage });
  return { ...app, resolve: (reply = created) => resolve(copy(reply)) };
}
async function submit(host: Element, sent: ReturnType<typeof start>['sent']) {
  await until(() => expect(host.querySelector('input[aria-required="true"]')).not.toBeNull());
  typeInto(host.querySelector<HTMLInputElement>('input[aria-required="true"]')!, 'Late create');
  click(byText(host, '.ev-savebar button', 'Create contact'));
  await until(() => expect(requests(sent, 'POST', /contact$/)).toHaveLength(1));
}

it('acknowledges a late create without leaving the chosen page or deleting its kept draft', async () => {
  const { host, sent, router, resolve } = deferredCreate();
  await submit(host, sent);
  await router.go('docs', {});
  const draftKey = Object.keys(localStorage).find(key => key.startsWith('evlet.unsaved.') && !key.endsWith('._user'))!;
  const kept = localStorage.getItem(draftKey);
  expect(kept).toContain('Late create');
  resolve();
  await until(() => expect(lastToast()).toContain('Contact created (0000000000001)'));
  expect(location.pathname).toBe('/docs');
  expect(localStorage.getItem(draftKey)).toBe(kept);
  expect(requests(sent, 'GET', /contact\/null/)).toHaveLength(0);
});

it('reports a refused late create without touching a closed view', async () => {
  const { host, sent, router, resolve } = deferredCreate();
  await submit(host, sent);
  await router.go('docs', {});
  resolve({ status: 422, etag: null, body: { error: 'validation', message: 'Review the form', fields: { title: 'This title already exists' } } });
  await until(() => expect(lastToast()).toContain('Contact was not saved.'));
  expect(location.pathname).toBe('/docs');
});

it('hands a pending panel create to its live full-page view', async () => {
  const { host, sent, resolve } = deferredCreate(true);
  await submit(host, sent);
  click(host.querySelector('.doc-tools button[aria-label="Open full page"]'));
  await until(() => expect(location.pathname).toBe('/app/contact/new'));
  await until(() => expect(host.querySelector('.ev-panel')).toBeNull());
  resolve();
  await until(() => expect(location.pathname).toBe('/app/contact/0000000000001'));
  expect(requests(sent, 'POST', /contact$/)).toHaveLength(1);
});
