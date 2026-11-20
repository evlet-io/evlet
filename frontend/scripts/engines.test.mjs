import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// The frontend builds on the Node LTS before the current line (finance_v1 builds on 22), and .npmrc has
// engine-strict=true, so `engines` decides where `npm ci` runs. These checks keep the declared range, the lockfile
// and every locked package in step: each line the range opens must satisfy the packages npm installs.
const read = path => readFileSync(`${process.cwd()}/${path}`, 'utf8');
const pkg = JSON.parse(read('package.json')), lock = JSON.parse(read('package-lock.json'));
const workflow = read('../.github/workflows/ci.yml');

const parse = text => (text.replace(/^v/, '').match(/^(\d+)(?:\.(\d+))?(?:\.(\d+))?$/) || []).slice(1).map(Number);
const compare = (a, b) => a.reduce((found, part, index) => found || part - (b[index] ?? 0), 0);
function admits(range, version) {
  // The npm range forms the locked packages use: "a || b", space-joined comparators, ^x.y.z and >=, >, <=, <, =.
  const at = parse(version);
  return range.split('||').some(part => part.trim().replace(/([<>]=?|=|\^)\s+/g, '$1').split(/\s+/).every(term => {
    const [, op, text] = term.match(/^(\^|[<>]=?|=)?(v?[\d.]+)$/) || [];
    if (!text) return false;
    const want = parse(text).map(value => value || 0), order = compare(at, want);
    if (op === '^') return at[0] === want[0] && order >= 0;
    return { '>=': order >= 0, '>': order > 0, '<=': order <= 0, '<': order < 0 }[op] ?? order === 0;
  }));
}
// The lowest version of each line the range opens: "^22.22.2 || ^24.15.0" gives 22.22.2 and 24.15.0.
const floors = pkg.engines.node.split('||').map(part => part.trim().replace(/^\^/, ''));

describe('the Node versions the frontend builds on', () => {
  it('opens the 22 LTS line and the 24 line', () => {
    expect(floors.map(floor => parse(floor)[0])).toEqual([22, 24]);
    expect(lock.packages[''].engines.node).toBe(pkg.engines.node);
  });

  it('admits each floor in every locked package that names a Node range', () => {
    const refused = Object.entries(lock.packages).flatMap(([name, entry]) => {
      const range = entry.engines?.node;
      if (!name || typeof range !== 'string' || !/\d/.test(range)) return [];
      return floors.filter(floor => !admits(range, floor)).map(floor => `${name} (${range}) refuses ${floor}`);
    });
    expect(refused).toEqual([]);
  });

  it('builds on the lowest 22 release in CI', () => {
    expect(workflow).toContain(`node-version: '${floors[0]}'`);
  });

  it('reads npm ranges as npm does', () => {
    expect(admits('^22.22.2 || ^24.15.0', '22.22.2')).toBe(true);
    expect(admits('^22.22.2 || ^24.15.0', '23.0.0')).toBe(false);
    expect(admits('>= 12.0.0', '22.22.2')).toBe(true);
    expect(admits('>=24.15 <25', '22.22.2')).toBe(false);
    expect(admits('^20.19.0 || >=22.12.0', '22.0.0')).toBe(false);
  });
});
