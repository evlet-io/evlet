// Letter avatars and tiles stay centred and at their size wherever they sit. A broad descendant
// rule such as `.rec-meta span{display:inline-flex}` once beat `.avatar` and `.org-mark` on the
// spans nested in the record meta row and pushed the letter off-centre. Vitest hands CSS imports
// over empty and jsdom does not load stylesheets, so the sources are read from disk and checked.
import { describe, expect, it } from 'vitest';

const node = (globalThis as any).process;
const fs = node.getBuiltinModule('node:fs');
const SRC = `${node.cwd()}/src/evlet/`;
const files = (fs.readdirSync(SRC, { recursive: true }) as string[]).map(p => p.split('\\').join('/'))
  .filter(p => p.endsWith('.css') && !p.startsWith('tests/'));
const rules = files.flatMap(file => [...fs.readFileSync(SRC + file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  .matchAll(/([^{}@]+)\{([^{}]*)\}/g)].map(m => ({ file, selector: m[1].trim(), body: m[2] })));
const BOX = /(^|;)\s*(display|width|height|line-height|padding|text-align)\s*:/;

describe('avatar and tile styles', () => {
  it('the components own their box: grid, centred, fixed size, line-height 1', () => {
    const own = (sel: string) => rules.find(r => r.selector === sel)!.body;
    for (const sel of ['.avatar', '.org-mark']) {
      expect(own(sel)).toMatch(/display:inline-grid/);
      expect(own(sel)).toMatch(/place-items:center/);
      expect(own(sel)).toMatch(/width:\d+px/);
      expect(own(sel)).toMatch(/height:\d+px/);
      expect(own(sel)).toMatch(/line-height:1/);
    }
  });

  it('no rule reaches a nested span in the record meta row (the avatar and tile are spans)', () => {
    const reaching = rules.filter(r => r.selector.split(',').some((s: string) => /\.rec-meta\s+(span|\*)\s*$/.test(s.trim())) && BOX.test(r.body));
    expect(reaching.map(r => r.selector)).toEqual([]);
  });

  it('only the component blocks set display, width, height or padding on .avatar and .org-mark', () => {
    const SIZES = /^\.(avatar|org-mark)(\.(sm|lg|xl))?$/;
    const stray = rules.filter(r => r.selector.split(',').some((s: string) => /\.(avatar|org-mark)(\.[\w-]+)*$/.test(s.trim()) && !SIZES.test(s.trim()))
      && /(^|;)\s*(display|width|height|padding|line-height)\s*:/.test(r.body));
    // The settings photo row sizes its own large avatar; that is a deliberate size override.
    expect(stray.map(r => r.selector).filter(s => s !== '.set-photo-row .avatar.xl')).toEqual([]);
  });
});
