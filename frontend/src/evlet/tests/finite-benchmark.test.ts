// Opt-in measurements: FINITE_BENCH=1 FINITE_BENCH_OUT=bench.json npx vitest run src/tests/finite-benchmark.test.ts
import { it } from 'vitest';
import fixtures from './finite-fixtures.json';
import { FiniteDocument } from '../model/finite';

const plans = fixtures.plans as Record<string, any>;
const process: any = (globalThis as any).process;
const enabled = process?.env?.FINITE_BENCH === '1';

function measure(label: string, plan: any, data: any, edit: (document: FiniteDocument, index: number) => void, repeats = 50) {
  let start = performance.now();
  const document = new FiniteDocument(plan, data);
  const cold = performance.now() - start, timings: number[] = [];
  for (let index = 0; index < repeats; index++) { start = performance.now(); edit(document, index); timings.push(performance.now() - start); }
  timings.sort((a, b) => a - b);
  return {case: label, rows: document.rows.size - 1, nodes: document.deps.size, cold_ms: +cold.toFixed(2),
    p50_ms: +timings[Math.floor(timings.length / 2)].toFixed(3), p95_ms: +timings[Math.ceil(timings.length * 0.95) - 1].toFixed(3),
    evaluated_last: document.trace.evaluated, steps_last: document.trace.steps};
}

it.skipIf(!enabled)('reports engine timings', () => {
  const invoice = {inclusive: true, children: {items: Array.from({length: 100}, (_, i) => ({_key: 'r' + i, qty: String(i % 7 + 1), rate: '12.35'}))}};
  const project = {children: {phases: Array.from({length: 10}, (_, p) => ({_key: 'p' + p, children: {tasks: Array.from({length: 99}, (_, t) => ({_key: 't' + t, hours: String(t % 5 + 1)}))}}))}};
  const results = [
    measure('invoice-100 row edit', plans.invoice, invoice, (d, i) => d.set(d.find('items.r' + (i % 100)), 'qty', String(i % 9 + 1))),
    measure('invoice-100 parent toggle', plans.invoice, invoice, (d, i) => d.set(d.root.handle, 'inclusive', i % 2 === 0)),
    measure('project-1000 task edit', plans.project, project, (d, i) => d.set(d.find(`phases.p${i % 10}.tasks.t${i % 99}`), 'hours', String(i % 9 + 1))),
    measure('project-1000 rate fan-out', plans.project, project, (d, i) => d.set(d.root.handle, 'hourly_rate', String(100 + i)), 20),
  ];
  const output = JSON.stringify({node: process.version, results}, null, 1);
  const target = process.env.FINITE_BENCH_OUT;
  if (target) return import('node:fs' as string).then((fs: any) => fs.writeFileSync(target, output));
  console.log(output);
});
