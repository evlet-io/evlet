// Helpers for the component tests: mount into the page, count DOM changes, press keys, and a
// record store over the recorded backend responses (api-fixture.json).
import { mount, type Child } from '../dom';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import type { AppSchema } from '../model/schema';
import { closeDialogs, closePopover } from '../ui/layers';
import { hideTip } from '../ui/tooltip';
import { id, responses, server } from './server';
export { id };

const disposers: Array<() => void> = [];
const stores: RecordStore[] = [];

export function render(view: () => Child): HTMLElement {
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

/** Open a Dropdown's list and pick the choice with this value. */
export function choose(button: HTMLElement, value: string) {
  button.click();
  const option = document.querySelector<HTMLElement>(`.pop [role="option"][data-value="${CSS.escape(value)}"]`);
  if (!option) throw new Error(`No choice ${value}`);
  option.click();
}

/** The values a Dropdown offers, in order (opens and closes its list). */
export function choiceValues(button: HTMLElement): string[] {
  button.click();
  const values = [...document.querySelectorAll<HTMLElement>('.pop [role="option"]')].map(option => option.dataset.value!);
  button.click();
  return values;
}

/** Open a Select's list and pick the choice with this label. */
export function pick(button: HTMLElement, label: string) {
  button.click();
  const option = [...document.querySelectorAll<HTMLElement>('.pop [role="option"]')].find(item => item.textContent?.trim() === label);
  if (!option) throw new Error(`No choice ${label}`);
  option.click();
}

export const schema = responses.schema.body as AppSchema;

/** The recorded order backend: a 120-line order, its customer and product. */
export function backend(overrides: Array<[string, RegExp, any]> = []) {
  let saved = false;
  const stub = server([
    ...overrides,
    ['GET', /\/api\/order\/0000000000001$/, 'detail'],
    ['GET', /\/api\/order\/0000000000001\/lines\/lines\?limit=100&offset=100$/, () => responses[saved ? 'lines_page_after_update' : 'lines_page']],
    ['GET', /\/api\/customer\/0000000000001$/, { ...responses.customer, status: 200 }],
    ['GET', /\/api\/product\/0000000000001$/, { ...responses.product, status: 200 }],
    ['GET', /\/api\/product(\?.*)?$/, { status: 200, etag: null, body: { items: [{ id: id(1), title: 'Support hours', price: '250.00' }, { id: id(2), title: 'Licence <b>bold</b>', price: '90.00' }], total: 2, limit: 8, offset: 0 } }],
    ['PATCH', /\/api\/order\/0000000000001$/, () => { saved = true; return responses.update; }],
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
