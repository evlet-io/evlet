// The note composer wraps the rich text editor. Both boxes once drew a focus ring, so a focused note
// showed two stacked blue rings with the Save note footer in a strip between them. The composer owns
// the one ring; the editor inside it has no border or ring. Vitest hands CSS imports over empty and
// jsdom does not load stylesheets, so the sources are read from disk and checked.
import { describe, expect, it } from 'vitest';

const node = (globalThis as any).process;
const fs = node.getBuiltinModule('node:fs');
const SRC = `${node.cwd()}/src/evlet/`;
const rules = ['ui/styles.css', 'richtext/richtext.css'].flatMap(file => [...fs.readFileSync(SRC + file, 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}@]+)\{([^{}]*)\}/g)].map(m => ({ selector: m[1].trim(), body: m[2] })));
const body = (selector: string) => rules.filter(r => r.selector.split(',').map((s: string) => s.trim()).includes(selector))
  .map(r => r.body).join(';');
const RING = /border-color:var\(--focus-border\);box-shadow:var\(--shadow-xs\),0 0 0 4px var\(--focus-ring\)/;

describe('note composer focus', () => {
  it('the composer draws the same ring as form fields', () => {
    expect(body('.composer:focus-within')).toMatch(RING);
    expect(body('.fld .input:focus')).toMatch(RING);
    expect(body('.rt:focus-within')).toMatch(/--focus-border/);
    expect(body('.rt:focus-within')).toMatch(/4px var\(--focus-ring/);
  });

  it('the editor inside the composer has no border or ring of its own', () => {
    const inner = body('.composer>.rt:focus-within');
    expect(inner).toMatch(/border:0/);
    expect(inner).toMatch(/box-shadow:none/);
    expect(body('.composer>.rt')).toMatch(/border:0/);
  });

  it('toolbar buttons keep a visible keyboard focus ring', () => {
    expect(body('.rt-tool:focus-visible')).toMatch(/outline:2px solid var\(--accent\)/);
  });
});
