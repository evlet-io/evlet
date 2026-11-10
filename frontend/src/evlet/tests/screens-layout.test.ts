// Page layout: the shell never scrolls, only its view does (record pages as well as lists), and a
// list shows a loading state, not its empty state, until a load has finished.
import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, invoice, recorded, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Sent } from './server';

afterEach(stopAll);

// Vitest hands CSS imports over empty, so the stylesheets are read from disk (Vitest runs from frontend/).
const node = (globalThis as any).process;
const fs = node.getBuiltinModule('node:fs');
const css = (path: string): string => fs.readFileSync(`${node.cwd()}/src/evlet/${path}`, 'utf8');
/** The declarations of the first rule whose selector is exactly `selector`. */
const rule = (sheet: string, selector: string): string => {
  const found = new RegExp(`(?:^|[}\\s])${selector.replace(/[.*+?^${}()|[\]\\>]/g, '\\$&')}\\{([^}]*)\\}`, 'm').exec(sheet);
  if (!found) throw new Error(`No ${selector} rule`);
  return found[1];
};

describe('the shell scrolls only its view', () => {
  const orderRoutes = (): Route[] => [
    ...adminRoutes(),
    ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
    ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
    ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
    ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
  ];

  it('puts a record page inside the view, beside the sidebar and under the top bar', async () => {
    const { host } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(host.querySelector('.doc-head .doc-mark .ph-icon')?.getAttribute('aria-label')).toBe('Order'));
    const app = host.querySelector('.app')!, view = host.querySelector('#ev-view')!;
    // The top bar runs across the window; under it the sidebar and the main column, which holds only the view.
    expect([...app.children].filter(child => child.matches('.topbar, .sidebar, .main')).map(child => child.className)).toEqual(['topbar', 'sidebar', 'main']);
    expect([...host.querySelector('.app > .main')!.children].map(child => child.tagName.toLowerCase() + '.' + child.className)).toEqual(['main.view']);
    // The whole record, its footer and its save bar included, lives in the view.
    expect(view.contains(host.querySelector('.ev-record'))).toBe(true);
    await until(() => expect(view.contains(host.querySelector('.ev-rec-foot'))).toBe(true));
    expect(document.scrollingElement?.scrollTop ?? 0).toBe(0);
  });

  it('keeps the window and the shell from scrolling, and the view as the one scroll container', () => {
    const styles = css('ui/styles.css'), screens = css('app/screens.css');
    expect(rule(styles, 'html,body')).toContain('height:100%');
    expect(rule(styles, 'body')).toContain('overflow:hidden');
    // The top bar's row, then one row for the rest of the window, clipped, so content, focus or scrollIntoView
    // cannot grow or scroll the shell. The top bar spans both columns; the Library, without one, keeps one row.
    const app = rule(styles, '.app');
    expect(app).toContain('grid-template-rows:var(--topbar-h) minmax(0,1fr)');
    expect(rule(styles, '.topbar')).toContain('grid-column:1/-1');
    expect(rule(styles, '.lib-app')).toContain('grid-template-rows:minmax(0,1fr)');
    expect(app).toContain('height:100%');
    expect(app).toContain('overflow:clip');
    expect(rule(styles, '.main')).toContain('min-height:0');
    expect(rule(styles, '.view')).toMatch(/flex:1;overflow:auto;min-height:0/);
    expect(rule(styles, '.nav')).toContain('overflow-y:auto');
    // A record page's side column stays put while the main column scrolls, and scrolls on its own.
    const side = rule(screens, '.view>.ev-record .doc:not(.no-side)>.doc-side');
    expect(side).toContain('position:sticky');
    expect(side).toContain('top:0');
    expect(side).toContain('overflow-y:auto');
    expect(side).toContain('height:calc(100dvh - var(--topbar-h))');
    // In a narrow frame (its own width, page or side panel alike) it leaves the main column and opens as a drawer on
    // the right, from the end of the breadcrumb row. No window media query decides it.
    expect(rule(screens, '.doc-narrow:not(.no-side) .ev-side-toggle')).toContain('display:grid');
    expect(rule(screens, '.ev-record .doc.doc-narrow:not(.no-side)>.doc-side')).toMatch(/^position:fixed;top:0;right:0;bottom:0/);
    expect(rule(screens, '.ev-record .doc.doc-narrow.side-open:not(.no-side)>.doc-side')).toContain('transform:none;visibility:visible');
    expect(screens).not.toContain('@media (max-width:980px)');
  });
});

describe('a list loading its rows', () => {
  /** A list route that answers only when `release` is called. */
  const held = () => {
    const waiting: Array<() => void> = [];
    return { reply: (body: unknown) => () => new Promise(resolve => waiting.push(() => resolve(copy(body as any)))), release: () => waiting.splice(0).forEach(go => go()) };
  };
  const withTabs = () => {
    const schema = copy(invoice.schema);
    schema.body.models.find((model: { name: string }) => model.name === 'invoice').list_tabs = { field: 'status', kind: 'state', default: 'issued' };
    return schema;
  };
  const empty = { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } };
  const routes = (list: (sent: Sent) => unknown, schema: unknown = invoice.schema) => [
    ['GET', /\/api\/auth\/session$/, invoice.session],
    ['GET', /\/api\/schema$/, schema],
    ['GET', /\/api\/invoice\?/, list],
    ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
    ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
    ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
  ] as any[];

  it('shows loading lines, not "No invoices in this tab", until the first load finishes', async () => {
    const gate = held();
    const { host } = start(routes(gate.reply(empty), withTabs()), '/app/invoice');
    await until(() => expect(host.querySelector('.ev-list .ev-list-loading')).not.toBeNull());
    const loading = host.querySelector('.ev-list .ev-list-loading')!;
    expect(loading.getAttribute('role')).toBe('status');
    expect(loading.getAttribute('aria-label')).toBe('Loading invoices');
    expect(host.querySelector('.ev-list .empty')).toBeNull();
    expect(text(host.querySelector('.ev-list'))).not.toContain('in this tab');
    // Once the load finds nothing, the empty state for the tab shows.
    gate.release();
    await until(() => expect(text(host.querySelector('.ev-list .dt-empty'))).toContain('No invoices in this tab'));
    expect(host.querySelector('.ev-list .ev-list-loading')).toBeNull();
  });

  it('shows loading lines while another tab loads after an empty one', async () => {
    const gate = held();
    let first = true;
    const { host } = start(routes(sent => first ? (first = false, copy(empty)) : gate.reply(invoice.invoices)(), withTabs()), '/app/invoice');
    await until(() => expect(text(host.querySelector('.ev-list .dt-empty'))).toContain('No invoices in this tab'));
    click(byText(host, '.ev-list [role="tab"]', /^Draft/));
    await until(() => expect(host.querySelector('.ev-list .ev-list-loading')).not.toBeNull());
    expect(text(host.querySelector('.ev-list'))).not.toContain('in this tab');
    gate.release();
    await until(() => expect(host.querySelectorAll('.ev-list tbody tr[data-key]').length).toBe(3));
  });

  it('shows loading lines on the board before its first load, then its columns', async () => {
    const gate = held();
    const { host } = start(routes(gate.reply(invoice.invoices)), '/app/invoice?view=board');
    await until(() => expect(host.querySelector('.ev-list .ev-list-loading')).not.toBeNull());
    expect(host.querySelector('.board')).toBeNull();
    expect(host.querySelector('.col-empty')).toBeNull();
    gate.release();
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(3));
    expect(host.querySelector('.ev-list .ev-list-loading')).toBeNull();
  });
});

describe('field hints', () => {
  it('follow the label and wrap under it as one piece, so a hint never squeezes the label', () => {
    const styles = css('ui/styles.css');
    expect(rule(styles, '.ff label')).toContain('flex-wrap:wrap');
    expect(rule(styles, '.ff-hint')).toContain('white-space:nowrap');
    expect(rule(styles, '.ff-hint.init')).toContain('color:var(--fg-4)');
  });
});

describe('section descriptions', () => {
  it('read smaller than the section title, in the muted colour', () => {
    const desc = rule(css('app/screens.css'), '.ev-more-desc');
    expect(desc).toContain('font-size:12px');
    expect(desc).toContain('color:var(--fg-3)');
  });
});

describe('a board card\'s state icon', () => {
  it('keeps the same 6 px gap before the title as a list row, centred on the title', () => {
    const card = rule(css('ui/styles.css'), '.card .t>.ev-state-ic');
    expect(card).toContain('margin-right:6px');
    expect(card).toContain('vertical-align:-1px');
    expect(rule(css('ui/styles.css'), '.card .t>.ev-state-ic:empty')).toContain('display:none');
  });
});

describe('reordering table rows on a phone', () => {
  it('keeps a narrow drag handle under 480 px instead of hiding it', () => {
    const styles = css('ui/styles.css');
    const phone = /@media \(max-width:480px\)\{\.g-exp\{[^}]*\}[^@]*?\}\}/.exec(styles)![0];
    expect(phone).toContain('.g-drag{width:12px}');
    expect(phone).not.toContain('.g-drag{display:none}');
  });
});
