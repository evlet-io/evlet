// Going to the linked record: a read-only Link value is a link to its record; an editable one
// keeps its picker and shows the ↗ arrow over the record's mark on hover. Only for a value whose
// target this person can open (its model is in their schema); the record route still decides.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cell } from '../reactive';
import { createRouter } from '../router';
import { SchemaIndex, type AppSchema, type FieldSchema, type ModelSchema } from '../model/schema';
import { apiLinks, BaseField, localField, type LinkSource } from '../ui/fields';
import { InlineValue } from '../ui/inline';
import { createClient } from '../api';
import { cleanup, render } from './ui-helpers';

// jsdom does not load stylesheets, so the hover rules are read from disk (as avatar-centring does).
const node = (globalThis as any).process;
const readFileSync = (path: string, encoding: string): string => node.getBuiltinModule('node:fs').readFileSync(path, encoding);

const routes = { home: '/', list: '/m/:model', record: '/m/:model/:id' } as const;
let stops: Array<() => void> = [];
beforeEach(() => { history.replaceState(null, '', '/'); });
afterEach(() => { stops.splice(0).forEach(stop => stop()); cleanup(); });

function started() {
  const router = createRouter(routes);
  stops.push(router.start());
  return router;
}

const titles: Record<string, string> = { 'c-harbor': 'Harbor Trading' };
// What the app's LinkSource answers: titles, and hrefs only for models the role can read.
const links = (readable = new Set(['customer'])): LinkSource => ({
  search: async () => [{ id: 'c-summit', title: 'Summit Services' }],
  title: (_model, id) => cell<string | null>(titles[id] ?? null),
  href: (model, id) => readable.has(model) ? `/m/${model}/${id}` : null,
});
const customer = (value: unknown, extra: Partial<FieldSchema> = {}) =>
  localField({ name: 'customer', label: 'Customer', type: 'link', target: 'customer', ...extra }, value);
const click = (element: Element, init: MouseEventInit = {}) => element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init }));
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe('go to the linked record', () => {
  it('shows a read-only Link as a link that opens the record in the page', async () => {
    const router = started();
    const host = render(() => BaseField({ field: customer('c-harbor', { read_only: true }), links: links() }));
    expect(host.querySelector('button.in-link')).toBeNull();
    const link = host.querySelector<HTMLAnchorElement>('a.link-to')!;
    expect(link.getAttribute('href')).toBe('/m/customer/c-harbor');
    expect(link.textContent).toContain('Harbor Trading');
    expect(host.querySelector('.link-go')).toBeNull();
    click(link);
    await settle();
    expect(router.route.peek()).toMatchObject({ name: 'record', params: { model: 'customer', id: 'c-harbor' } });
  });

  it('leaves Cmd or Ctrl click and a middle click to the browser, for a new tab', () => {
    started();
    const host = render(() => BaseField({ field: customer('c-harbor', { read_only: true }), links: links() }));
    const link = host.querySelector<HTMLAnchorElement>('a.link-to')!;
    let prevented = 0;
    document.addEventListener('click', event => { if (event.defaultPrevented) prevented++; });
    click(link, { metaKey: true });
    click(link, { ctrlKey: true });
    click(link, { button: 1 });
    expect(prevented).toBe(0);
    expect(location.pathname).toBe('/');
  });

  it('turns into a link when an editable Link becomes locked, and back', () => {
    const field = customer('c-harbor');
    const host = render(() => BaseField({ field, links: links() }));
    expect(host.querySelector('button.in-link')).not.toBeNull();
    field.rules.set({ ...field.rules.peek(), locked: true });
    expect(host.querySelector('button.in-link')).toBeNull();
    expect(host.querySelector('a.link-to')?.getAttribute('href')).toBe('/m/customer/c-harbor');
    field.rules.set({ ...field.rules.peek(), locked: false });
    expect(host.querySelector('a.link-to')).toBeNull();
    expect(host.querySelector('button.in-link')).not.toBeNull();
  });

  it('keeps the picker when editable and adds the arrow with an Open hint', async () => {
    const router = started();
    const host = render(() => BaseField({ field: customer('c-harbor'), links: links() }));
    const button = host.querySelector<HTMLButtonElement>('button.in-link')!;
    const arrow = host.querySelector<HTMLAnchorElement>('a.link-go')!;
    expect(arrow.getAttribute('href')).toBe('/m/customer/c-harbor');
    expect(arrow.getAttribute('aria-label')).toBe('Open Harbor Trading');
    expect(arrow.querySelector('.link-go-hint')?.textContent).toBe('Open Harbor Trading');
    expect(arrow.querySelector('svg')?.outerHTML).toContain('M7 7h10v10');
    // The arrow is its own focusable link, not inside the picker's button.
    expect(button.contains(arrow)).toBe(false);
    expect(arrow.tabIndex).toBe(0);
    // A click on the field still edits it.
    click(button);
    expect(document.querySelector('.link-pop')).not.toBeNull();
    expect(router.route.peek()?.name).toBe('home');
    // A click on the arrow opens the record.
    click(arrow);
    await settle();
    expect(router.route.peek()).toMatchObject({ name: 'record', params: { model: 'customer', id: 'c-harbor' } });
  });

  it('shows the arrow and hint on hover only, nudged unless motion is reduced', () => {
    const css = readFileSync(`${node.cwd()}/src/evlet/ui/styles.css`, 'utf8');
    expect(css).toMatch(/\.link-go\{[^}]*opacity:0;pointer-events:none/);
    expect(css).toContain('.in-link-box:hover>.link-go,.link-go:focus-visible{opacity:1;pointer-events:auto}');
    expect(css).toContain('.in-link-box:has(>.link-go):hover .org-mark,.in-link-box:has(>.link-go:focus-visible) .org-mark{visibility:hidden}');
    expect(css).toContain('.link-go:hover>.link-go-hint,.link-go:focus-visible>.link-go-hint{opacity:1}');
    expect(css).toMatch(/\.link-go:hover>svg,\.link-go:focus-visible>svg\{transform:translate\(2px,-2px\)\}/);
    expect(css).toMatch(/prefers-reduced-motion:reduce\)\{\.link-go,\.link-go>svg,\.link-go-hint\{transition:none\}\.link-go:hover>svg,\.link-go:focus-visible>svg\{transform:none\}\}/);
  });

  it('shows no arrow or link without a value', () => {
    const field = customer(null);
    const host = render(() => [BaseField({ field, links: links() }), BaseField({ field: customer(null, { read_only: true }), links: links() })]);
    expect(host.querySelector('.link-go')).toBeNull();
    expect(host.querySelector('a.link-to')).toBeNull();
    // Choosing a record shows it.
    field.set('c-harbor');
    expect(host.querySelector('a.link-go')?.getAttribute('aria-label')).toBe('Open Harbor Trading');
  });

  it('shows no arrow or link when the target model cannot be read', () => {
    const none = links(new Set());
    const host = render(() => [BaseField({ field: customer('c-harbor'), links: none }), BaseField({ field: customer('c-harbor', { read_only: true }), links: none }),
      InlineValue({ field: customer('c-harbor', { read_only: true }), links: none }), InlineValue({ field: customer('c-harbor'), links: none })]);
    expect(host.querySelector('.link-go')).toBeNull();
    expect(host.querySelector('a')).toBeNull();
    expect(host.textContent).toContain('Harbor Trading');
  });

  it('links a read-only InlineValue, not one inside its edit button', () => {
    const host = render(() => [InlineValue({ field: customer('c-harbor', { read_only: true }), links: links() }),
      InlineValue({ field: customer('c-harbor'), links: links() })]);
    const [readOnly, editable] = [...host.querySelectorAll('.iv')];
    expect(readOnly.querySelector('a.link-to')?.getAttribute('href')).toBe('/m/customer/c-harbor');
    expect(editable.querySelector('button.fval a')).toBeNull();
    // Editable at rest: the arrow sits beside the edit button, which still edits on click.
    const arrow = editable.querySelector<HTMLAnchorElement>('.iv-go > a.link-go')!;
    expect(arrow.getAttribute('href')).toBe('/m/customer/c-harbor');
    expect(arrow.getAttribute('aria-label')).toBe('Open Harbor Trading');
    click(editable.querySelector('button.fval')!);
    expect(editable.querySelector('.fval-edit button.in-link')).not.toBeNull();
  });

  it('gives hrefs from apiLinks only for models in the role\'s schema with a page of their own', () => {
    const model = (name: string, extra: Partial<ModelSchema> = {}): ModelSchema =>
      ({ name, label: name, plural: name, title_field: 'title', fields: [], list_fields: [], ...extra });
    const schema = new SchemaIndex({ models: [model('customer'), model('order_line', { child_only: true })] } as AppSchema);
    const source = apiLinks(createClient({ fetch: async () => new Response('{}') }), schema, { recordHref: (name, id) => `/app/${name}/${id}` });
    expect(source.href?.('customer', '7')).toBe('/app/customer/7');
    expect(source.href?.('order_line', '7')).toBeNull();
    expect(source.href?.('supplier', '7')).toBeNull();
    expect(apiLinks(createClient({ fetch: async () => new Response('{}') }), schema).href).toBeUndefined();
  });
});
