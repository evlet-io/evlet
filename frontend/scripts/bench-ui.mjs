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
for (const name of ['window', 'document', 'Node', 'NodeFilter', 'Element', 'HTMLElement', 'DocumentFragment', 'Text', 'Comment', 'MutationObserver', 'Event', 'MouseEvent'])
  globalThis[name] = name === 'window' ? window : window[name];

const { batch, cell, setDev } = await import('../src/evlet/reactive.ts');
const { each, html, mount } = await import('../src/evlet/dom.ts');
const { createClient } = await import('../src/evlet/api.ts');
const { RecordStore } = await import('../src/evlet/model/record.ts');
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

console.log(JSON.stringify({ environment: `jsdom ${JSON.parse(readFileSync(new URL('../node_modules/jsdom/package.json', import.meta.url), 'utf8')).version}, node ${process.version}`, results }));
