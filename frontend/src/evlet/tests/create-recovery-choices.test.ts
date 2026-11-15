import { afterEach, expect, it } from 'vitest';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import { prepareCreate } from '../model/create-attempt';
import { cell, scope } from '../reactive';
import { LocalUnsaved, instanceKey } from '../app/unsaved';
import { UnsavedBar } from '../app/unsaved-bar';
import { adminRoutes, byText, click, lastToast, recorded, start, stopAll, text, until, typeInto, requests } from './screens-helpers';
import { id, responses } from './server';
const stops: Array<() => void> = [];
afterEach(() => { stops.splice(0).forEach(stop => stop()); stopAll(); localStorage.clear(); });
async function fixture(keep = true, duplicate = false) {
  const api = createClient(), unsaved = new LocalUnsaved('choices', 'editor', localStorage);
  const store = new RecordStore({ api, schema: responses.schema.body as any, model: 'customer' });
  await store.create({ title: duplicate ? 'Copied values' : '' }); stops.push(() => store.dispose());
  const ctx: any = { api, unsaved, me: cell({ prefs: { keep_unsaved: keep } }), router: { href: () => '/app/customer/new' } };
  return { store, unsaved, ctx, show: () => {
    let bar!: Node; stops.push(scope(() => { bar = UnsavedBar(ctx, store.meta as any, store, { isNew: true, draft: !duplicate }); })); document.body.append(bar);
  } };
}
const attempted = (title: string, principal: number | null = null, model = 'customer') => prepareCreate({ title }, null, principal, new URL(`/api/${model}`, location.href).href);

it('offers recovery of an existing local copy even when future keeping is off', async () => {
  const test = await fixture(false); test.unsaved.put('customer', { values: { title: 'Earlier work' } }); test.show();
  expect(byText(document, 'button', 'Restore')).not.toBeNull(); expect(byText(document, 'button', 'Start fresh')).not.toBeNull();
  click(byText(document, 'button', 'Restore'));
  expect(test.store.field('title').value.peek()).toBe('Earlier work'); expect(test.ctx.me.peek().prefs.keep_unsaved).toBe(false);
});

it.each([false, true])('does not let a stale recovery bar discard a newer pending receipt (old attempted=%s)', async oldAttempted => {
  const test = await fixture(); const first = attempted('Earlier work');
  test.unsaved.put('customer', { values: { title: 'Earlier work' } }, undefined, oldAttempted ? first : null); test.show();
  test.unsaved.remove('customer'); // an explicit decision in the other tab
  const newer = attempted('Newer pending'); test.unsaved.put('customer', { values: { title: 'Newer pending' } }, undefined, newer);
  click(byText(document, 'button', 'Start fresh'));
  expect(test.unsaved.get('customer')?.createAttempt?.key).toBe(newer.key);
  expect(text(document.querySelector('.ev-unsaved'))).toContain('previous create may have succeeded');
  expect(byText(document, 'button', 'Restore')).not.toBeNull();
});

it('lets a duplicate review previous work separately without mixing either draft', async () => {
  const test = await fixture(false, true); test.unsaved.put('customer', { values: { title: 'Earlier work' } }); test.show();
  const link = byText<HTMLAnchorElement>(document, 'a', 'Review previous unfinished draft')!;
  expect(link.getAttribute('href')).toBe('/app/customer/new'); expect(link.target).toBe('_blank'); expect(link.rel).toContain('noopener');
  expect(byText(document, 'button', 'Restore')).toBeNull(); expect(test.store.field('title').value.peek()).toBe('Copied values');
  expect(test.unsaved.get('customer')?.changes.values?.title).toBe('Earlier work');
});

it('cancels the current new form without removing another pending create', async () => {
  const session = recorded.session.body;
  const unsaved = new LocalUnsaved(instanceKey(session), session.user.username, localStorage);
  const pending = attempted('Another tab', session.user.id, 'contact');
  unsaved.put('contact', { values: { title: 'Another tab' } }, undefined, pending);
  const { host } = start(adminRoutes(), '/app/contact/new', { storage: localStorage });
  await until(() => expect(byText(host, '.ev-savebar button', 'Cancel')).not.toBeNull());
  click(byText(host, '.ev-savebar button', 'Cancel'));
  await until(() => expect(location.pathname).toBe('/app/contact'));
  expect(unsaved.get('contact')?.createAttempt?.key).toBe(pending.key);
});


it('offers explicit review after a newly retained create loses its response, preserving the current form', async () => {
  const { host, sent } = start([...adminRoutes(), ['POST', /\/api\/contact$/, () => { throw new TypeError('Lost response'); }]], '/app/contact/new', { storage: localStorage });
  await until(() => expect(host.querySelector('input[aria-required="true"]')).not.toBeNull());
  typeInto(host.querySelector<HTMLInputElement>('input[aria-required="true"]')!, 'Pending create');
  click(byText(host, '.ev-savebar button', 'Create contact'));
  await until(() => expect(byText(host, '.inv-banner a', 'Review previous unfinished draft')).not.toBeNull());
  const link = byText<HTMLAnchorElement>(host, '.inv-banner a', 'Review previous unfinished draft')!;
  expect(link.getAttribute('href')).toBe('/app/contact/new'); expect(link.target).toBe('_blank');
  expect(host.querySelector<HTMLInputElement>('input[aria-required="true"]')!.value).toBe('Pending create');
  expect(requests(sent, 'POST', /contact$/)).toHaveLength(1);
});

it('opens the record a lost create made when a corrected retry gets 409, and keeps the corrected values as a draft', async () => {
  // The server's rules (evlet/business.py repeated_create): one record per key; the same body replays it, other values get 409.
  let committed: string | null = null, lost = true;
  const created = { ...recorded.customer.body, id: id(56), title: 'Acme Trading', credit_days: 30 };
  const create = (sent: { body: any; headers: Record<string, string> }) => {
    const body = JSON.stringify(sent.body);
    if (committed === null) committed = body;
    else if (committed !== body) return { status: 409, etag: null, body: { error: 'This Idempotency-Key was already used with different values' } };
    if (lost) { lost = false; throw new TypeError('Lost response'); }
    return { status: 201, etag: '"created"', body: created };
  };
  const { host, sent, router } = start([...adminRoutes(), ['POST', /\/api\/customer$/, create],
    ['GET', /\/api\/customer\/000000000001r$/, { status: 200, etag: '"created"', body: created }],
    ['GET', /\/api\/customer\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }]], '/app/customer/new?title=Acme%20Trading', { storage: localStorage });
  await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New customer'));
  const days = () => host.querySelector<HTMLInputElement>('.fld[data-field="credit_days"] input')!;
  typeInto(days(), '30');
  click(byText(host, '.ev-savebar button', 'Create customer'));
  await until(() => expect(requests(sent, 'POST', /customer$/)).toHaveLength(1));
  await until(() => expect(byText(host, '.ev-savebar button', 'Create customer')?.hasAttribute('disabled')).toBe(false));
  // The answer was lost. The person corrects a value and retries under the same key.
  typeInto(days(), '60');
  click(byText(host, '.ev-savebar button', 'Create customer'));
  await until(() => expect(location.pathname).toBe('/app/customer/000000000001r'));
  const posts = requests(sent, 'POST', /customer$/);
  expect(posts).toHaveLength(3);
  expect(new Set(posts.map(post => post.headers['idempotency-key'])).size).toBe(1);
  expect(posts.map(post => post.body.credit_days)).toEqual([30, 60, 30]);
  await until(() => expect(lastToast()).toContain('Your later edits were not applied. They are kept as a draft of a new record'));
  // New offers the corrected values back.
  const session = recorded.session.body;
  const unsaved = new LocalUnsaved(instanceKey(session), session.user.username, localStorage);
  expect(unsaved.get('customer')?.createAttempt).toBeUndefined();
  expect(unsaved.get('customer')?.changes.values?.credit_days).toBe(60);
  await router.go('create', { model: 'customer' });
  await until(() => expect(byText(host, 'button', 'Restore')).not.toBeNull());
  click(byText(host, 'button', 'Restore'));
  await until(() => expect(days().value).toBe('60'));
});
