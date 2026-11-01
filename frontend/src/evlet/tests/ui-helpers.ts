// Helpers for the component tests: mount into the page, count DOM changes, press keys, and a
// record store over the recorded backend responses (api-fixture.json).
import { mount } from '../dom';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import type { AppSchema } from '../model/schema';
import { closeDialogs, closePopover } from '../ui/layers';
import { hideTip } from '../ui/tooltip';
import { responses, server } from './server';

const disposers: Array<() => void> = [];
const stores: RecordStore[] = [];

export function render(view: () => Node): HTMLElement {
  const host = document.createElement('div');
  document.body.append(host);
  disposers.push(mount(host, view));
  return host;
}

export function cleanup() {
  closePopover();
  closeDialogs();
  hideTip();
  disposers.splice(0).forEach(dispose => dispose());
  stores.splice(0).forEach(store => store.dispose());
  document.body.innerHTML = '';
}

/** Count DOM changes under a node, synchronously. */
export function watch(target: Node) {
  const observer = new MutationObserver(() => {});
  observer.observe(target, { childList: true, subtree: true, characterData: true, attributes: true });
  return () => {
    const records = observer.takeRecords();
    observer.disconnect();
    return records;
  };
}

export function key(target: Element, key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

export function type(input: HTMLInputElement | HTMLTextAreaElement, text: string) {
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

export function change(input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, text: string) {
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

export const schema = responses.schema.body as AppSchema;

/** The recorded order backend: a 120-line order, its customer and product. */
export function backend(overrides: Array<[string, RegExp, any]> = []) {
  let saved = false;
  const stub = server([
    ...overrides,
    ['GET', /\/api\/order\/1$/, 'detail'],
    ['GET', /\/api\/order\/1\/lines\/lines\?limit=100&offset=100$/, () => responses[saved ? 'lines_page_after_update' : 'lines_page']],
    ['GET', /\/api\/customer\/1$/, { ...responses.customer, status: 200 }],
    ['GET', /\/api\/product\/1$/, { ...responses.product, status: 200 }],
    ['GET', /\/api\/product(\?.*)?$/, { status: 200, etag: null, body: { items: [{ id: 1, name: 'Support hours', price: '250.00' }, { id: 2, name: 'Licence <b>bold</b>', price: '90.00' }], total: 2, limit: 8, offset: 0 } }],
    ['PATCH', /\/api\/order\/1$/, () => { saved = true; return responses.update; }],
  ]);
  const api = createClient({ fetch: stub.fetch });
  const store = new RecordStore({ api, schema, model: 'order', today: () => '2026-11-01' });
  stores.push(store);
  return { store, sent: stub.sent, api };
}

/** A new order with n lines, built in the browser (no request needed). */
export async function bigOrder(lines: number) {
  const { store, sent, api } = backend();
  const rows = Array.from({ length: lines }, (_, index) => ({ _key: `l${index}`, qty: String(1 + (index % 3)), rate: '2.50' }));
  await store.create({ title: 'Big order', children: { lines: rows } });
  return { store, sent, api };
}
