// Batch 3 frontend review fixes: one permission helper, setting rows, report Select labels, phone cards, the boot
// shell with parallel requests, Help for people who use the app, and today() in the server's zone.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from '../dom';
import { cell } from '../reactive';
import { App } from '../app/app';
import { AppClient } from '../app/client';
import { appRouter } from '../app/context';
import type { AppContext } from '../app/context';
import { cellText } from '../app/report-page';
import { PHONE_LIST } from '../app/list-page';
import { calculationToday } from '../model/record';
import { todayDate } from '../model/format';
import { can, SchemaIndex, type AppSchema, type ReportColumn } from '../model/schema';
import { SettingRow } from '../ui/settings-helpers';
import { adminRoutes, invoice, recorded, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, server } from './server';

const realMatchMedia = window.matchMedia;
const extra: Array<() => void> = [];
afterEach(() => { window.matchMedia = realMatchMedia; stopAll(); extra.splice(0).forEach(stop => stop()); });

// Vitest hands CSS imports over empty, so the sources are read from disk (vitest runs from frontend/).
const node = (globalThis as any).process;
const read = (path: string): string => node.getBuiltinModule('node:fs').readFileSync(`${node.cwd()}/src/evlet/${path}`, 'utf8');

describe('can (CQ-07)', () => {
  it('offers a write only when the server granted it, never on a read-only model', () => {
    expect(can({ permissions: { create: true, update: false, delete: false } }, 'create')).toBe(true);
    expect(can({ permissions: { create: true, update: false, delete: false } }, 'update')).toBe(false);
    expect(can({ api: 'read', permissions: { create: true, update: true, delete: true } }, 'create')).toBe(false);
    // One default everywhere: no permissions means no.
    expect(can({}, 'create')).toBe(false);
    expect(can(undefined, 'delete')).toBe(false);
  });
});

describe('SettingRow (CQ-13)', () => {
  it('marks a changed draft with a reset button and shows its error', () => {
    const errors = cell(new Map([['setting:x', 'Too large']]));
    let reset = '';
    const drafts = { overridden: (id: string) => id === 'setting:x', reset: (id: string) => { reset = id; }, errors };
    const host = document.createElement('div');
    extra.push(mount(host, () => SettingRow({ drafts, id: 'setting:x', title: 'Limit', text: 'How many', control: 'ctl' })));
    expect(host.querySelector('.ctl-setrow .ctl-tag')?.textContent).toBe('Changed');
    expect(text(host.querySelector('.ctl-err'))).toBe('Too large');
    (host.querySelector('.ctl-setrow strong button') as HTMLButtonElement).click();
    expect(reset).toBe('setting:x');
    const plain = document.createElement('div');
    extra.push(mount(plain, () => SettingRow({ title: 'Mailbox', control: 'x' })));
    expect(plain.querySelector('.ctl-tag, .ctl-err, p')).toBeNull();
  });
});

describe('report cells (UX-15)', () => {
  it('show a Select value by its label, from the column source or the report model', () => {
    const schema = new SchemaIndex({ version: 1, ui_version: 1, models: [{ name: 'invoice', label: 'Invoice', plural: 'Invoices',
      fields: [{ name: 'status', type: 'select', label: 'Status', choices: ['draft', 'partly_sent'] }] }] } as unknown as AppSchema);
    const ctx = { schema } as unknown as AppContext;
    const column: ReportColumn = { name: 'status', type: 'text', label: null, link: null, total: false, unit: null };
    expect(cellText(ctx, column, 'partly_sent', 'invoice')).toBe('Partly sent');
    expect(cellText(ctx, { ...column, name: 'state', source: 'invoice.status' }, 'draft')).toBe('Draft');
    // A plain text column keeps its value.
    expect(cellText(ctx, { ...column, name: 'reference' }, 'po-1', 'invoice')).toBe('po-1');
    expect(cellText(ctx, column, 'draft')).toBe('draft');
  });
});

describe('phones (UX-05)', () => {
  const routes = (): Route[] => [
    ['GET', /\/api\/auth\/session$/, invoice.session], ['GET', /\/api\/schema$/, invoice.schema],
    ['GET', /\/api\/invoice\?/, invoice.invoices], ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
    ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
  ];
  const phone = (on: boolean) => {
    window.matchMedia = ((query: string) => ({ matches: on && query === PHONE_LIST, media: query, addEventListener() {}, removeEventListener() {} })) as any;
  };

  it('shows a list as stacked cards instead of the table', async () => {
    phone(true);
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector('.ev-list .ev-compact[data-layout="cards"] .ev-crow')).not.toBeNull());
    expect(host.querySelector('.ev-list .dt')).toBeNull();
    expect(host.querySelector('.ev-crow .l1 b')?.textContent).toBeTruthy();
  });

  it('keeps the desktop table on wider screens', async () => {
    phone(false);
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector('.ev-list tbody tr[data-key]')).not.toBeNull());
    expect(host.querySelector('[data-layout="cards"]')).toBeNull();
  });

  it('reads a record in one column under 640px', () => {
    const css = read('app/screens.css').replace(/\/\*[\s\S]*?\*\//g, '');
    const block = /@media \(max-width:640px\)\{([\s\S]*?)\n\}/.exec(css.slice(css.indexOf('.ev-cards-list')))?.[1] ?? '';
    expect(block).toMatch(/\.doc\[class\] \.ev-cmp-grid \.fs-g[^{]*\{grid-template-columns:minmax\(0,1fr\)\}/);
    expect(block).toMatch(/\.doc\[class\] \.ev-more-row\{grid-template-columns:minmax\(0,1fr\)\}/);
  });
});

/** A Storage kept in memory. */
function memory(seed: Record<string, string> = {}): Storage {
  const items = new Map(Object.entries(seed));
  return { get length() { return items.size; }, clear: () => items.clear(), getItem: key => items.get(key) ?? null, key: index => [...items.keys()][index] ?? null,
    removeItem: key => { items.delete(key); }, setItem: (key, value) => { items.set(key, String(value)); } } as Storage;
}

function mountApp(routes: Route[], path: string, options: { storage?: Storage | null; dev?: boolean } = {}) {
  window.history.replaceState(null, '', path);
  const stub = server(routes.map(([method, pattern, reply]) => [method, pattern, typeof reply === 'function' ? reply : () => copy(reply)]) as any);
  const router = appRouter();
  const stopRouter = router.start();
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => App({ router, api: new AppClient({ fetch: stub.fetch }), storage: options.storage ?? null, dev: options.dev ?? false }));
  extra.push(() => { dispose(); stopRouter(); host.remove(); });
  return { host, sent: stub.sent };
}

describe('cold load (UX-09)', () => {
  it('after a signed-in visit, draws the shell outline and asks for the schema with the session', async () => {
    const storage = memory({ 'evlet.signed-in': '1' });
    let answer!: () => void;
    const held = new Promise<void>(resolve => { answer = resolve; });
    const { host, sent } = mountApp([['GET', /\/api\/auth\/session$/, async () => { await held; return copy(recorded.session); }],
      ['GET', /\/api\/schema$/, screen.schema], ['GET', /\/api\/customer\?/, screen.customers_page1]], '/', { storage, dev: true });
    expect(host.querySelector('[data-boot="shell"]')).not.toBeNull();
    // The schema goes out while the session is still on its way.
    await until(() => expect(sent.some(item => item.url.endsWith('/api/schema'))).toBe(true));
    answer();
    await until(() => expect(location.pathname).toBe('/app/customer'));
    expect(sent.filter(item => item.url.endsWith('/api/schema'))).toHaveLength(1);
    expect(storage.getItem('evlet.signed-in')).toBe('1');
  });

  it('without the hint shows the splash and asks for nothing but the session; signed out, the hint goes', async () => {
    const { host, sent } = mountApp([['GET', /\/api\/auth\/session$/, recorded.session_anonymous ?? { status: 200, body: { user: null }, etag: null }]], '/login');
    expect(host.querySelector('.ev-connecting:not(.ev-boot)')).not.toBeNull();
    await until(() => expect(host.querySelector('#ev-password')).not.toBeNull());
    expect(sent.map(item => new URL(item.url, 'http://x').pathname)).toEqual(['/api/auth/session']);
    const storage = memory({ 'evlet.signed-in': '1' });
    const second = mountApp([['GET', /\/api\/auth\/session$/, { status: 200, body: { user: null, csrf: 'token' }, etag: null }],
      ['GET', /\/api\/schema$/, { status: 401, body: { error: 'Sign in' }, etag: null }],
      ['GET', /\/api\/_settings\/me$/, { status: 401, body: { error: 'Sign in' }, etag: null }]], '/login', { storage });
    await until(() => expect(second.host.querySelector('#ev-password')).not.toBeNull());
    expect(storage.getItem('evlet.signed-in')).toBeNull();
  });
});

describe('Docs (UX-10)', () => {
  it('shows people who use the app Help, not the developer guide', async () => {
    const { host } = mountApp(adminRoutes(), '/docs');
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Help'));
    expect(host.querySelector('.ev-docs')!.getAttribute('data-docs')).toBe('help');
    expect([...host.querySelectorAll('.ev-docs .tabs a')].map(text)).toEqual(['Help', 'Working with records', 'Keyboard shortcuts']);
    expect(host.querySelector('.ev-code')).toBeNull();
    expect(host.querySelectorAll('.ev-help-models li').length).toBeGreaterThan(0);
  });

  it('sends a developer topic address to Help for them', async () => {
    const { host } = mountApp(adminRoutes(), '/docs/getting-started');
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Help'));
  });

  it('keeps the developer guide, with Help topics, for developers; Getting started matches the README', async () => {
    const { host } = start(adminRoutes(), '/docs/getting-started');
    await until(() => expect(text(host.querySelector('.ph h1'))).toBe('Getting started'));
    expect(host.querySelector('.ev-docs')!.getAttribute('data-docs')).toBe('developer');
    const shown = text(host.querySelector('.ev-doc-article'));
    expect(shown).toContain('pip install --upgrade evlet');
    expect(shown).toContain('evlet setup myproject --app notes --instance acme --dev');
    expect(shown).not.toMatch(/whl|uvicorn/);
    const tabs = [...host.querySelectorAll('.ev-docs .tabs a')].map(text);
    expect(tabs[0]).toBe('Build with Evlet');
    expect(tabs).toContain('Keyboard shortcuts');
  });
});

describe('today() in calculations (BUG-02)', () => {
  it('is the day in the server zone the schema names', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-20T21:30:00Z'));
    try {
      expect(calculationToday('UTC')()).toBe('2026-11-20');
      expect(calculationToday('Asia/Tokyo')()).toBe('2026-11-21');
    } finally { vi.useRealTimers(); }
    // No zone, or one the browser does not know: the person's day.
    expect(calculationToday(undefined)()).toBe(todayDate());
    expect(calculationToday('Not/AZone')()).toBe(todayDate());
  });
});
