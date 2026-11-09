// The list page's layout (docs/ui.md#list-card): a white sidebar beside a grey main area, the tabs, search row
// and table in one sticky card whose table alone scrolls (down and sideways), and a record opened from the list
// as its own card beside it, with a gap.
import { afterEach, describe, expect, it } from 'vitest';
import { click, invoice, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';

afterEach(stopAll);

// Vitest hands CSS imports over empty, so the sources are read from disk (vitest runs from frontend/).
const node = (globalThis as any).process;
const fs = node.getBuiltinModule('node:fs');
const read = (path: string): string => fs.readFileSync(`${node.cwd()}/src/evlet/${path}`, 'utf8');
const bare = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');
const STYLES = bare(read('ui/styles.css')), SCREENS = bare(read('app/screens.css'));
/** The declarations of the last rule whose selector list is exactly `selector`. */
function rule(css: string, selector: string): string {
  const found = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(match => match[1].trim().split(/\s*,\s*/).includes(selector));
  if (!found.length) throw new Error(`No rule for ${selector}`);
  return found.map(match => match[2]).join(';');
}

const three = String(invoice.invoice_draft.body.id);
function schema(): Recorded {
  const body = copy(invoice.schema).body;
  body.models.find((model: { name: string }) => model.name === 'invoice').open_in = 'panel';
  return { ...invoice.schema, body };
}
const routes = (): Route[] => [
  ['GET', /\/api\/auth\/session$/, invoice.session],
  ['GET', /\/api\/schema$/, schema()],
  ['GET', /\/api\/invoice\?/, invoice.invoices],
  ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
  ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
  ['GET', /\/api\/invoice\/\w+\/measures$/, invoice.invoice_record_measures],
  ['GET', /\/api\/invoice\/\w+\/related\/payment\.invoice/, invoice.invoice_related_payments],
  ['GET', /\/api\/invoice\/\w+\/related\/invoice\.revises/, invoice.invoice_related_revisions],
  ['GET', /\/api\/invoice\/\w+\/journal/, invoice.invoice_journal],
  ['GET', /\/api\/invoice\/\w+\/attachments$/, invoice.invoice_attachments],
  ['GET', /\/api\/invoice\/\w{13}$/, invoice.invoice_draft],
  ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
];

describe('the list card', () => {
  it('puts the tabs, the search row and the table in one card after the title and the stat cards', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector('.ev-list tbody tr[data-key]')).not.toBeNull());
    const list = host.querySelector<HTMLElement>('.ev-list')!;
    const card = list.querySelector<HTMLElement>(':scope > .ev-list-card')!;
    expect(card).not.toBeNull();
    // The breadcrumb, the title and the stat cards come before the card and scroll away; the card holds the rest.
    const before = [...list.children].slice(0, [...list.children].indexOf(card)).map(child => child.className);
    expect(before.slice(0, 2)).toEqual(['crumbs page-crumbs', 'ph']);
    expect([...card.children].map(child => child.getAttribute('role') === 'tablist' ? 'tabs' : child.classList[0])).toEqual(
      [...(card.querySelector(':scope > [role="tablist"]') ? ['tabs'] : []), 'toolbar', 'ev-list-body']);
    expect(card.querySelector('.toolbar input[type="search"], .toolbar input')).not.toBeNull();
    // The table, its pager, and the empty and loading states are inside the card's body.
    const body = card.querySelector<HTMLElement>(':scope > .ev-list-body')!;
    expect(body.querySelector(':scope > .table-wrap .dt')).not.toBeNull();
    expect(body.querySelector(':scope > .ev-pager')).not.toBeNull();
  });

  it('sticks to the top of the list\'s scrolling area and fills its height; only the table scrolls inside', () => {
    const card = rule(SCREENS, '.ev-list-card');
    expect(card).toMatch(/position:sticky/);
    expect(card).toMatch(/top:var\(--ev-card-top\)/);
    expect(card).toMatch(/height:calc\(100% \+ var\(--ev-pad-top\) - var\(--ev-card-top\)\)/);
    expect(card).toMatch(/background:var\(--surface\)/);
    expect(card).toMatch(/border:1px solid var\(--border\)/);
    expect(card).toMatch(/border-radius:var\(--r-lg\)/);
    // The card clips what is wider than it; the table scrolls down and sideways in its own frame.
    expect(card).toMatch(/overflow:hidden/);
    expect(card).toMatch(/min-width:0/);
    expect(rule(SCREENS, '.ev-list-body > .table-wrap')).toMatch(/overflow:auto/);
    expect(rule(SCREENS, '.ev-list-body > .table-wrap')).toMatch(/min-height:0/);
    expect(rule(SCREENS, '.ev-list-body')).toMatch(/flex:1/);
    // The page itself never scrolls sideways.
    expect(rule(SCREENS, '.ev-list-row > .ev-list')).toMatch(/overflow-x:hidden/);
    // The table's header row stays put inside the card.
    expect(rule(STYLES, '.dt th')).toMatch(/position:sticky;top:0/);
  });
});

describe('the record beside the list', () => {
  it('opens as its own card beside the list card, with a gap, its own border and the card radius', async () => {
    const { host } = start(routes(), `/app/invoice?open=${three}`);
    await until(() => expect(text(host.querySelector('.ev-panel .doc-head h1'))).toBe('Draft invoice'));
    const split = host.querySelector<HTMLElement>('.ev-list-split')!;
    const side = split.querySelector<HTMLElement>(':scope > .split-side')!;
    expect(side.querySelector(':scope > .ev-panel')).not.toBeNull();
    // Its header (the title and status, then Expand and Close on the title row) is at the top of the card.
    const head = side.querySelector<HTMLElement>('.doc-main > .ev-panel-head')!;
    expect(head.querySelector('.doc-head h1')).not.toBeNull();
    expect(head.querySelector('.doc-head .doc-tools button[aria-label="Open full page"]')).not.toBeNull();
    expect(head.parentElement!.firstElementChild).toBe(head);
    // No breadcrumb in the panel: the list page has the one.
    expect(side.querySelector('.page-crumbs')).toBeNull();
    expect(host.querySelectorAll('.page-crumbs')).toHaveLength(1);

    const card = rule(SCREENS, '.ev-list-split > .split-side');
    expect(card).toMatch(/border:1px solid var\(--border\)/);
    expect(card).toMatch(/border-radius:var\(--r-lg\)/);
    expect(card).toMatch(/background:var\(--surface\)/);
    // The gap: the list's 8 px right padding, the 1 px divider and the panel's 4 px left margin.
    expect(card).toMatch(/margin:12px 16px 16px 4px/);
    expect(rule(SCREENS, '.ev-list-narrow.page')).toMatch(/padding:var\(--ev-pad-top\) 8px 16px 18px/);
    // Covering the list (narrow windows, phones), it is full size as before.
    expect(rule(SCREENS, '.ev-list-split.split-over > .split-side')).toMatch(/margin:0;border:0;border-radius:0/);
    expect(rule(SCREENS, '.ev-panel')).toMatch(/overflow:auto/);
  });
});

describe('the shell grounds', () => {
  it('draws a white sidebar and top bar beside a grey main area, in both themes', () => {
    expect(rule(STYLES, '.side-body')).toMatch(/background:var\(--surface\)/);
    expect(rule(STYLES, '.topbar')).toMatch(/background:var\(--surface\)/);
    expect(rule(STYLES, '.main')).toMatch(/background:var\(--page-bg\)/);
    expect(rule(STYLES, '.sidebar')).toMatch(/border-right:1px solid var\(--border\)/);
    const value = (block: string, name: string) => new RegExp(`${name}:(#[0-9a-f]{6})`).exec(block)![1];
    const light = STYLES.slice(STYLES.indexOf(':root{'), STYLES.indexOf('}', STYLES.indexOf(':root{')));
    const dark = STYLES.slice(STYLES.indexOf(':root[data-theme="dark"]{'), STYLES.indexOf('}', STYLES.indexOf(':root[data-theme="dark"]{')));
    for (const block of [light, dark]) expect(value(block, '--page-bg')).not.toBe(value(block, '--surface'));
  });
});

// The edge of pinned columns while a table scrolls sideways is one even line: unblurred 1px steps every pinned cell
// draws at its full height (a blur fades at each cell's ends, so the edge waved at every row line), and the current,
// hovered or edited rows keep it.
describe('the pinned column edge', () => {
  it('is drawn without blur, the same in every row, in the list and in record tables', () => {
    const shade = /--pin-shade:([^;]+)/.exec(rule(STYLES, 'table.dt'))![1];
    // Four steps, each offset 1 px further with no blur or spread.
    expect([...shade.matchAll(/(\d)px (\S+) (\S+) /g)].map(step => step.slice(1).join(' '))).toEqual(['1 0 0', '2 0 0', '3 0 0', '4 0 0']);
    expect(rule(STYLES, '.table-wrap.scrolled .dt .stk-last')).toContain('var(--pin-shade)');
    expect(rule(STYLES, '.grid.scrolled .g .s2')).toContain('var(--pin-shade)');
    // Rows that set their own box shadow add the edge to it rather than replacing it.
    for (const selector of ['.dt tbody tr.cur td:first-child', '.g td.ed:hover', '.g td.invalid', '.g td.noted']) expect(rule(STYLES, selector)).toContain('var(--edge,0 0 transparent)');
    expect(STYLES).not.toMatch(/6px 0 10px -6px/);
  });
});
