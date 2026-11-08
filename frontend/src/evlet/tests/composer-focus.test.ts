// The note composer wraps the rich text editor. Both boxes once drew a focus ring, so a focused note
// showed two stacked blue rings with the Save note footer in a strip between them. The composer owns
// the one ring; the editor inside it has no border or ring. Vitest hands CSS imports over empty and
// jsdom does not load stylesheets, so the sources are read from disk and checked.
import { describe, expect, it } from 'vitest';

const node = (globalThis as any).process;
const fs = node.getBuiltinModule('node:fs');
const SRC = `${node.cwd()}/src/evlet/`;
const FILES = ['ui/styles.css', 'richtext/richtext.css', 'app/screens.css', 'app/settings.css', 'controls/controls.css', 'eva/eva.css'];
const rules = FILES.flatMap(file => [...fs.readFileSync(SRC + file, 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}@]+)\{([^{}]*)\}/g)].map(m => ({ selector: m[1].trim(), body: m[2] })));
const body = (selector: string) => rules.filter(r => r.selector.split(',').map((s: string) => s.trim()).includes(selector))
  .map(r => r.body).join(';');
// A focused field shows a flat accent border and keeps its resting shadow: no glow around it.
const FLAT = /border-color:var\(--accent\);box-shadow:var\(--shadow-xs\)/;

describe('focus style', () => {
  const focusRules = () => rules.filter(r => /:focus/.test(r.selector));

  it('no focus rule draws a glow: no spread ring in a light tint, no blur', () => {
    for (const rule of focusRules()) {
      // A box-shadow on focus is the resting shadow, none, or a solid accent edge (no blur, spread at most 2px).
      for (const shadow of rule.body.match(/box-shadow:[^;]+/g) ?? []) {
        expect(shadow, rule.selector).toMatch(/^box-shadow:(none|var\(--shadow-xs\)|(inset )?0 0 0 [12]px var\(--accent\))$/);
      }
    }
  });

  it('fields, link pickers, selects, tags, the rich text frame and search boxes focus with an accent border', () => {
    for (const selector of ['.input:focus', '.in-affix:focus-within', '.in-tags:focus-within', '.in-link.input:focus-visible', '.in-select.input:focus-visible',
      '.rt:focus-within', '.rt-link-in:focus', '.set-search:focus-within', '.eva-compose:focus-within']) expect(body(selector), selector).toMatch(/border-color:var\(--accent\)/);
  });

  it('a field with an error keeps its rose border on focus, without a glow', () => {
    expect(body('.fld.invalid .input:focus')).toMatch(/border-color:var\(--t-rose\);box-shadow:var\(--shadow-xs\)/);
    expect(body('.rt.rt-invalid:focus-within')).toMatch(/border-color:var\(--t-rose\)/);
  });

  it('buttons and other controls keep a solid focus outline', () => {
    expect(body(':focus-visible')).toMatch(/outline:2px solid var\(--accent\)/);
  });

  it('the glow tokens are gone', () => {
    const all = FILES.map(file => fs.readFileSync(SRC + file, 'utf8')).join('\n');
    expect(all).not.toMatch(/--focus-ring|--focus-border|--error-ring/);
  });
});

describe('note composer focus', () => {
  it('the composer draws the same flat focus border as form fields', () => {
    expect(body('.composer:focus-within')).toMatch(FLAT);
    expect(body('.fld .input:focus')).toMatch(FLAT);
    expect(body('.rt:focus-within')).toMatch(FLAT);
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
