// UI framework benchmarks in jsdom, with no extra dependencies.
//   npm run bench:ui
// Node runs the TypeScript sources directly (--experimental-transform-types); a resolve hook
// adds the .ts extension that the bundler-style imports leave out.
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { JSDOM } from 'jsdom';

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('.') && !/\.\w+$/.test(specifier)) {
      const candidate = new URL(specifier + '.ts', context.parentURL);
      if (existsSync(fileURLToPath(candidate))) return next(candidate.href, context);
    }
    return next(specifier, context);
  },
});

const { window } = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1/' });
for (const name of ['window', 'document', 'Node', 'NodeFilter', 'Element', 'HTMLElement', 'DocumentFragment', 'Text', 'Comment', 'MutationObserver', 'Event', 'MouseEvent',
  'KeyboardEvent', 'FocusEvent', 'HTMLInputElement', 'HTMLButtonElement', 'HTMLSelectElement', 'HTMLTextAreaElement', 'SVGElement', 'getComputedStyle'])
  globalThis[name] = name === 'window' ? window : window[name];

const { batch, cell, setDev } = await import('../src/evlet/reactive.ts');
const { each, html, mount } = await import('../src/evlet/dom.ts');
const { createClient } = await import('../src/evlet/api.ts');
const { RecordStore } = await import('../src/evlet/model/record.ts');
const { ChildTable } = await import('../src/evlet/ui/childtable.ts');
const { DataTable } = await import('../src/evlet/ui/table.ts');
const { StatusPill } = await import('../src/evlet/ui/base.ts');
setDev(false); // measure what production runs

const fixture = JSON.parse(readFileSync(new URL('../src/evlet/tests/api-fixture.json', import.meta.url), 'utf8'));
const results = [];
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
function report(name, samples, budget) {
  const value = median(samples);
  results.push({ name, median: value, budget });
  console.log(`${name.padEnd(58)} median ${value.toFixed(2).padStart(8)} ms  (min ${Math.min(...samples).toFixed(2)}, budget ${budget ?? '-'} ms)`);
}
function changes(target) {
  const observer = new window.MutationObserver(() => {});
  observer.observe(target, { childList: true, subtree: true, characterData: true, attributes: true });
  return () => { const records = observer.takeRecords(); observer.disconnect(); return records; };
}

// 1. A 1,000-row keyed list, first render.
{
  const rows = Array.from({ length: 1000 }, (_, index) => ({ id: index, name: `Item ${index}`, qty: cell(String(index % 7)), rate: '2.50' }));
  const samples = [];
  for (let run = 0; run < 15; run++) {
    const host = document.createElement('table');
    document.body.append(host);
    const start = performance.now();
    const dispose = mount(host, () => html`<tbody>${each(rows, row => row.id, row => html`
      <tr><td>${() => row().name}</td><td class="num">${row().qty}</td><td class="num">${() => row().rate}</td></tr>`)}</tbody>`);
    samples.push(performance.now() - start);
    dispose();
    host.remove();
  }
  report('1,000-row keyed list: first render', samples, 100);
}

// 2. A 1,000-line record: one quantity edit recalculates the line and the totals.
{
  const order = fixture.schema.body.models.find(model => model.name === 'order');
  const base = fixture.detail.body.children.lines[1];
  const lines = Array.from({ length: 1000 }, (_, index) => ({ ...base, id: index + 1, children: { parts: [] } }));
  const detail = { ...fixture.detail.body, children: { lines }, total: '2500.00', line_count: 1000 };
  delete detail.children_pages;
  const reply = body => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json', etag: '"bench"' } });
  const fetch = async url => String(url).endsWith('/api/order/1') ? reply(detail)
    : String(url).includes('/api/customer/') ? reply({ ...fixture.customer.body }) : reply({ ...fixture.product.body });
  const store = new RecordStore({ api: createClient({ fetch }), schema: fixture.schema.body, model: 'order', today: () => '2026-11-01' });
  let start = performance.now();
  await store.open(1);
  await store.idle();
  const openTime = performance.now() - start;
  const host = document.createElement('div');
  document.body.append(host);
  const table = store.table('lines');
  start = performance.now();
  const dispose = mount(host, () => html`<table><tbody>${each(table.rows, row => row.key, row => html`
    <tr><td>${row().field('qty').value}</td><td>${row().field('rate').value}</td><td>${row().field('amount').value}</td></tr>`)}</tbody>
    <tfoot><tr><td>${store.field('line_count').value}</td><td>${store.field('total').value}</td></tr></tfoot></table>`);
  const renderTime = performance.now() - start;
  console.log(`1,000-line record: open with calculations ${openTime.toFixed(1)} ms, first render ${renderTime.toFixed(1)} ms`);
  const samples = [];
  let touched = [];
  const rows = table.rows.peek();
  for (let run = 0; run < 25; run++) {
    const done = changes(host);
    start = performance.now();
    rows[(run * 37) % 1000].field('qty').set(String(2 + (run % 5)));
    samples.push(performance.now() - start);
    touched = done();
  }
  report('1,000-line record: edit one qty, recalculate totals', samples, 16);
  console.log(`  DOM changes for the last edit: ${touched.length} (${touched.map(record => record.type).join(', ')}), total ${store.field('total').value.peek()}`);
  dispose();
  store.dispose();
  host.remove();
}

// 4. ChildTable over a 1,000-line record: first render, then one cell edit through the grid.
{
  const base = fixture.detail.body.children.lines[1];
  const lines = Array.from({ length: 1000 }, (_, index) => ({ ...base, id: index + 1, children: { parts: [] } }));
  const detail = { ...fixture.detail.body, children: { lines }, total: '2500.00', line_count: 1000 };
  delete detail.children_pages;
  const reply = body => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json', etag: '"bench"' } });
  const fetch = async url => String(url).endsWith('/api/order/1') ? reply(detail)
    : String(url).includes('/api/customer/') ? reply({ ...fixture.customer.body }) : reply({ ...fixture.product.body });
  const store = new RecordStore({ api: createClient({ fetch }), schema: fixture.schema.body, model: 'order', today: () => '2026-11-01' });
  await store.open(1);
  await store.idle();
  const table = store.table('lines');
  const renders = [];
  for (let run = 0; run < 15; run++) {
    const host = document.createElement('div');
    document.body.append(host);
    const start = performance.now();
    const dispose = mount(host, () => ChildTable({ table, columns: ['product', 'qty', 'rate', 'amount'], height: 560 }));
    renders.push(performance.now() - start);
    dispose();
    host.remove();
  }
  report('ChildTable, 1,000 rows: first render', renders, 100);
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => ChildTable({ table, columns: ['product', 'qty', 'rate', 'amount'], height: 560 }));
  console.log(`  rows in the DOM: ${host.querySelectorAll('tbody tr[data-key]').length} of 1000`);
  const edits = [];
  let touched = [], changed = 0;
  for (let run = 0; run < 25; run++) {
    const row = host.querySelectorAll('tbody tr[data-key]')[run % 10];
    const qty = [...row.querySelectorAll('td[data-col]')].find(td => td.dataset.col === 'qty');
    qty.click();
    const input = qty.querySelector('input');
    input.value = String(2 + (run % 7));
    const totalBefore = store.field('total').value.peek();
    // Enter commits, recalculates the line and the totals, and opens the cell below.
    const enter = new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    const done = changes(host);
    const start = performance.now();
    input.dispatchEvent(enter);
    edits.push(performance.now() - start);
    touched = done();
    if (store.field('total').value.peek() !== totalBefore) changed++;
    host.querySelector('td.editing input')?.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  }
  report('ChildTable, 1,000 rows: commit one cell, recalculate totals', edits, 16);
  console.log(`  ${changed} of ${edits.length} edits changed the total; DOM changes for the last edit: ${touched.length}, total ${store.field('total').value.peek()}`);
  dispose();
  host.remove();
  store.dispose();
}

// 5. DataTable: 100 list rows, first render (all rows in view).
{
  const tints = ['blue', 'violet', 'amber', 'green'];
  const rows = Array.from({ length: 100 }, (_, index) => ({ id: index + 1, title: `Deal ${index + 1}`, company: `Company ${index % 17}`, stage: tints[index % 4], amount: `${1000 + index * 37}.00`, close: '05-11-2026', owner: 'Aslam K' }));
  const columns = [
    { key: 'title', label: 'Deal', width: 220 }, { key: 'company', label: 'Organization', width: 180 },
    { key: 'stage', label: 'Stage', width: 130, render: row => StatusPill({ label: () => row().stage, tint: () => row().stage }) },
    { key: 'amount', label: 'Amount', width: 130, align: 'right' }, { key: 'close', label: 'Closes', width: 120 }, { key: 'owner', label: 'Owner', width: 150 },
  ];
  const samples = [];
  for (let run = 0; run < 15; run++) {
    const host = document.createElement('div');
    document.body.append(host);
    const start = performance.now();
    const dispose = mount(host, () => DataTable({ columns, rows, key: row => row.id, label: 'Deals', selectable: true, height: 100 * 44 }));
    samples.push(performance.now() - start);
    if (run === 0) console.log(`  DataTable rows in the DOM: ${host.querySelectorAll('tbody tr[data-key]').length}`);
    dispose();
    host.remove();
  }
  report('DataTable, 100 rows: first render', samples, 50);
}

// 3. 10,000 bound cells updated in one batch.
{
  const values = Array.from({ length: 10000 }, (_, index) => cell(index));
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => html`<ul>${each(values, (_, index) => index, item => html`<li>${item()}</li>`)}</ul>`);
  const samples = [];
  for (let run = 1; run <= 10; run++) {
    const start = performance.now();
    batch(() => { for (const value of values) value.set(value.peek() + 1); });
    samples.push(performance.now() - start);
  }
  report('10,000 cells bound to text, one batch update', samples, null);
  dispose();
  host.remove();
}

// 6. and 7. Screens: a generated list page of 100 rows, and a record view opening on its
// 1,000-line table. Measured from building the page to its rows in the DOM, with the record
// opened (rows parsed, calculations attached) inside the measurement.
{
  const { AppClient } = await import('../src/evlet/app/client.ts');
  const { appRouter, newPageState } = await import('../src/evlet/app/context.ts');
  const { Prefs } = await import('../src/evlet/app/prefs.ts');
  const { ListPage } = await import('../src/evlet/app/list-page.ts');
  const { RecordPage } = await import('../src/evlet/app/record-page.ts');
  const { SchemaIndex } = await import('../src/evlet/model/schema.ts');
  const { apiLinks } = await import('../src/evlet/ui/fields.ts');
  const { scope } = await import('../src/evlet/reactive.ts');
  globalThis.location = window.location; globalThis.history = window.history;
  const base = fixture.list.body.items[0];
  const listRows = Array.from({ length: 100 }, (_, index) => ({ ...base, id: index + 1, title: `Order ${index + 1}`, stage: ['open', 'won', 'lost'][index % 3] }));
  const line = fixture.detail.body.children.lines[1];
  const detail = { ...fixture.detail.body, children: { lines: Array.from({ length: 1000 }, (_, index) => ({ ...line, id: index + 1, children: { parts: [] } })) }, total: '2500.00', line_count: 1000 };
  delete detail.children_pages;
  const reply = body => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json', etag: '"bench"' } });
  const fetch = async url => {
    const path = String(url);
    if (path.includes('/api/order?')) return reply({ items: listRows, total: 100, limit: 100, offset: 0 });
    if (path.endsWith('/api/order/1')) return reply(detail);
    return reply(path.includes('/api/customer') ? fixture.customer.body : fixture.product.body);
  };
  const api = new AppClient({ fetch });
  const schema = new SchemaIndex(fixture.schema.body);
  const router = appRouter({ window });
  const ctx = { api, router, schema, session: fixture.session.body, user: fixture.session.body.user, prefs: new Prefs('bench', null),
    links: apiLinks(api, schema), dev: false, libraryHref: '/evlet.html', ...newPageState(), openPalette() {}, toggleSidebar() {}, async signOut() {} };
  const until = async (check, host) => {
    const limit = performance.now() + 5000;
    while (!check()) {
      if (performance.now() > limit) throw new Error(`The page did not render: ${host.textContent.slice(0, 300)}`);
      await new Promise(resolve => setTimeout(resolve, 0));
    }
  };
  const measure = async (path, build, ready) => {
    window.history.replaceState(null, '', path);
    const stopRouter = router.start();
    const host = document.createElement('div');
    document.body.append(host);
    const start = performance.now();
    let dispose;
    dispose = scope(() => { host.append(build()); });
    await until(() => ready(host), host);
    const time = performance.now() - start;
    const rows = host.querySelectorAll('tbody tr[data-key]').length;
    dispose();
    stopRouter();
    host.remove();
    return { time, rows };
  };
  const lists = [], records = [];
  let listRowsShown = 0, recordRowsShown = 0;
  for (let run = 0; run < 15; run++) {
    const list = await measure('/app/order', () => ListPage(ctx, 'order'), host => host.querySelectorAll('tbody tr[data-key]').length > 0);
    lists.push(list.time); listRowsShown = list.rows;
    const record = await measure('/app/order/1?tab=t-lines', () => RecordPage(ctx, 'order', '1'), host => host.querySelectorAll('.grid tbody tr[data-key]').length > 0);
    records.push(record.time); recordRowsShown = record.rows;
  }
  report('List page, 100 rows: build, load and first render', lists, 50);
  console.log(`  list rows in the DOM: ${listRowsShown} of 100 (virtualized at a 640 px viewport)`);
  report('Record view, 1,000-line table tab: open and first screen', records, 100);
  console.log(`  grid rows in the DOM: ${recordRowsShown} of 1000`);
  ctx.prefs.dispose();
}

// 8. testCRM (examples/testcrm), from responses recorded from the engine: the deals list with 100
// deals, a deal opening on 1,000 lines (products looked up, VAT and totals calculated), and one
// line edit through the grid.
{
  const crm = JSON.parse(readFileSync(new URL('../src/evlet/tests/testcrm-fixture.json', import.meta.url), 'utf8'));
  const { AppClient } = await import('../src/evlet/app/client.ts');
  const { appRouter, newPageState } = await import('../src/evlet/app/context.ts');
  const { Prefs } = await import('../src/evlet/app/prefs.ts');
  const { ListPage } = await import('../src/evlet/app/list-page.ts');
  const { RecordPage } = await import('../src/evlet/app/record-page.ts');
  const { SchemaIndex } = await import('../src/evlet/model/schema.ts');
  const { apiLinks } = await import('../src/evlet/ui/fields.ts');
  const { scope } = await import('../src/evlet/reactive.ts');
  const reads = Object.assign({}, ...crm.sara_big);
  const big = Object.entries(reads).find(([key, value]) => /^GET \/api\/deal\/\d+$/.test(key) && value.body?.children_pages)[1].body;
  const page = Object.entries(reads).find(([key]) => /\/lines\/lines\?limit=100&offset=100$/.test(key))[1].body.items;
  const rows = [...big.children.lines, ...page];
  const lines = Array.from({ length: 1000 }, (_, index) => ({ ...rows[index % rows.length], id: 100000 + index, description: `Site ${index + 1}` }));
  const detail = { ...big, children: { lines }, line_count: 1000 };
  delete detail.children_pages;
  const listed = Object.entries(reads).find(([key]) => key === 'GET /api/deal?limit=50&offset=0')?.[1].body
    ?? Object.assign({}, ...crm.sara_list)['GET /api/deal?limit=50&offset=0'].body;
  const deals = Array.from({ length: 100 }, (_, index) => ({ ...listed.items[index % listed.items.length], id: index + 1, title: `Deal ${index + 1}` }));
  const products = Object.fromEntries(Object.entries(reads).filter(([key]) => /^GET \/api\/(product|organization|contact)\/\d+$/.test(key)).map(([key, value]) => [key.slice(4), value.body]));
  const reply = body => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json', etag: '"bench"' } });
  const fetch = async url => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, '');
    if (path.startsWith('/api/deal?')) return reply({ items: deals, total: 100, limit: 100, offset: 0 });
    if (path === '/api/deal/1') return reply(detail);
    if (products[path]) return reply(products[path]);
    if (path.includes('/measures')) return reply({ measures: {} });
    if (path.includes('/aggregate')) return reply({ group_by: ['stage'], measures: ['n'], items: [] });
    return reply({ items: [], total: 0, limit: 10, offset: 0 });
  };
  const api = new AppClient({ fetch });
  const schema = new SchemaIndex(crm.schemas.sara.body);
  const router = appRouter({ window });
  const session = Object.assign({}, ...crm.sara_big)['GET /api/auth/session'].body;
  const ctx = { api, router, schema, session, user: session.user, prefs: new Prefs('bench-crm', null),
    links: apiLinks(api, schema), dev: false, libraryHref: '/evlet.html', ...newPageState(), openPalette() {}, toggleSidebar() {}, async signOut() {} };
  const wait = async check => { const limit = performance.now() + 5000; while (!check()) { if (performance.now() > limit) throw new Error('testCRM page did not render'); await new Promise(resolve => setTimeout(resolve, 0)); } };
  const open = async (path, build, ready) => {
    window.history.replaceState(null, '', path);
    const stopRouter = router.start();
    const host = document.createElement('div');
    document.body.append(host);
    const start = performance.now();
    const dispose = scope(() => { host.append(build()); });
    await wait(() => ready(host));
    return { time: performance.now() - start, host, close: () => { dispose(); stopRouter(); host.remove(); } };
  };
  const lists = [], records = [], edits = [];
  for (let run = 0; run < 15; run++) {
    const list = await open('/app/deal', () => ListPage(ctx, 'deal'), host => host.querySelectorAll('tbody tr[data-key]').length > 0);
    lists.push(list.time); list.close();
    const record = await open('/app/deal/1?tab=t-lines', () => RecordPage(ctx, 'deal', '1'), host => host.querySelectorAll('.grid tbody tr[data-key]').length > 0);
    records.push(record.time);
    if (run === 14) {
      for (let edit = 0; edit < 25; edit++) {
        const qty = record.host.querySelectorAll('.grid tbody tr[data-key]')[edit % 10].querySelector('td[data-col="qty"]');
        qty.click();
        const input = qty.querySelector('input');
        input.value = String(2 + (edit % 7));
        const start = performance.now();
        input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
        edits.push(performance.now() - start);
        record.host.querySelector('td.editing input')?.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      }
      console.log(`  testCRM deal after the edits: ${record.host.querySelector('.g-sum')?.textContent}`);
    }
    record.close();
  }
  report('testCRM deals list, 100 deals: build, load and first render', lists, 50);
  report('testCRM deal, 1,000 lines: open and first screen', records, 100);
  report('testCRM deal, 1,000 lines: commit one qty, VAT and totals', edits, 16);
  ctx.prefs.dispose();
}

console.log(JSON.stringify({ environment: `jsdom ${JSON.parse(readFileSync(new URL('../node_modules/jsdom/package.json', import.meta.url), 'utf8')).version}, node ${process.version}`, results }));
