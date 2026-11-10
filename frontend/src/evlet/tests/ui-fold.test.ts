// Collapsible form sections (ui/fold.ts): heading, summary, errors, saved state, phone default,
// revealing a field, and print.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { html } from '../dom';
import { cell } from '../reactive';
import { localField } from '../ui/fields';
import { FoldSection, Folds, foldSummary, isBlank, revealField, revealFirstError } from '../ui/fold';
import { FormSection } from '../ui/inline';
import { Prefs } from '../app/prefs';
import { cleanup, render } from './ui-helpers';

afterEach(cleanup);

const fields = () => [
  localField({ name: 'title', type: 'text', label: 'Title', required: true }, 'North site'),
  localField({ name: 'city', type: 'text', label: 'City' }, null),
  localField({ name: 'notes', type: 'longtext', label: 'Notes' }, ''),
];
const body = (list: ReturnType<typeof fields>) => html`<div class="fs-g">${list.map(field => html`<div class="ff" data-field=${field.meta.name}><input aria-label=${field.meta.label}></div>`)}</div>`;
const section = (folds: Folds, list = fields(), options: { collapsed?: boolean | 'empty'; index?: number; collapsible?: boolean } = {}) =>
  FoldSection({ title: 'Place', fields: list, folds, collapsible: options.collapsible ?? true, collapsed: options.collapsed ?? false, index: options.index ?? 0, body: body(list) });
const saved = () => cell<Record<string, boolean>>({});

describe('FoldSection', () => {
  it('has a real button heading with aria-expanded, aria-controls and a chevron; clicking toggles it', () => {
    const host = render(() => section(new Folds(saved(), false, false)));
    const toggle = host.querySelector<HTMLButtonElement>('.fs-toggle')!;
    expect(toggle.tagName).toBe('BUTTON');
    expect(toggle.type).toBe('button');
    expect(toggle.querySelector('.fs-chev')).not.toBeNull();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const panel = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    expect(panel.hidden).toBe(false);
    toggle.click();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(panel.hidden).toBe(true);
    expect(host.querySelector('section')!.classList.contains('closed')).toBe(true);
    toggle.click();
    expect(panel.hidden).toBe(false);
  });

  it('takes the fields of a closed section out of the tab order', () => {
    const host = render(() => section(new Folds(saved(), false, false), fields(), { collapsed: true }));
    const panel = host.querySelector<HTMLElement>('.fs-body')!;
    // A hidden element and everything in it is skipped by Tab and can't take focus.
    expect(panel.hidden).toBe(true);
    host.querySelector<HTMLInputElement>('input')!.focus();
    expect(document.activeElement).not.toBe(host.querySelector('input'));
  });

  it('summarises a closed section: filled count, or Required missing', () => {
    const list = fields();
    const host = render(() => section(new Folds(saved(), false, false), list, { collapsed: true }));
    const summary = host.querySelector<HTMLElement>('.fs-sum')!;
    expect(summary.hidden).toBe(false);
    expect(summary.textContent).toBe('1 of 3 filled');
    list[1].set('Dubai');
    expect(summary.textContent).toBe('2 of 3 filled');
    list[0].set('');
    expect(summary.textContent).toBe('Required missing');
    expect(foldSummary(list)).toBe('Required missing');
  });

  it('counts a number still at a zero default as empty, in the summary and for collapsed="empty"', () => {
    const fee = () => localField({ name: 'fee', type: 'decimal', label: 'Fee', default: '0.00' }, '0.00');
    const count = () => localField({ name: 'count', type: 'integer', label: 'Count' }, 0);
    expect(isBlank(fee())).toBe(true);
    expect(isBlank(count())).toBe(true);
    const typed = localField({ name: 'fee', type: 'decimal', label: 'Fee', default: '5.00' }, '0.00');
    expect(isBlank(typed)).toBe(false);
    expect(isBlank(localField({ name: 'name', type: 'text', label: 'Name' }, '0'))).toBe(false);
    const list = [fee(), count()];
    expect(foldSummary(list)).toBe('0 of 2 filled');
    const host = render(() => section(new Folds(saved(), false, false), list, { collapsed: 'empty' }));
    expect(host.querySelector<HTMLButtonElement>('.fs-toggle')!.getAttribute('aria-expanded')).toBe('false');
    list[0].set('12.50');
    expect(foldSummary(list)).toBe('1 of 2 filled');
  });

  it('counts errors in a badge and opens when a field inside gets one', () => {
    const list = fields();
    const host = render(() => section(new Folds(saved(), false, false), list, { collapsed: true }));
    const badge = host.querySelector<HTMLElement>('.fs-err')!;
    expect(badge.hidden).toBe(true);
    list[1].error.set('Choose a city');
    expect(badge.hidden).toBe(false);
    expect(badge.textContent).toBe('1');
    expect(badge.getAttribute('aria-label')).toBe('1 error');
    expect(host.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('true');
  });

  it('starts closed when empty only on an existing record whose fields are all empty', () => {
    const empty = () => [localField({ name: 'city', type: 'text', label: 'City' }, null), localField({ name: 'notes', type: 'longtext', label: 'Notes' }, '')];
    const closed = render(() => section(new Folds(saved(), false, false), empty(), { collapsed: 'empty' }));
    expect(closed.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('false');
    const filled = render(() => section(new Folds(saved(), false, false), fields(), { collapsed: 'empty' }));
    expect(filled.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('true');
    const fresh = render(() => section(new Folds(saved(), true, false), empty(), { collapsed: 'empty' }));
    expect(fresh.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('true');
  });

  it('starts sections after the first closed at phone width, unless the person saved otherwise', () => {
    const choices = saved();
    const first = render(() => section(new Folds(choices, false, true), fields(), { index: 0 }));
    expect(first.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('true');
    const second = render(() => section(new Folds(choices, false, true), fields(), { index: 1 }));
    expect(second.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('false');
    choices.set({ Place: true });
    expect(second.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('true');
  });

  it('a section that cannot collapse has a plain heading and always shows', () => {
    const host = render(() => section(new Folds(saved(), false, true), fields(), { collapsible: false, index: 3 }));
    expect(host.querySelector('.fs-toggle')).toBeNull();
    expect(host.querySelector('h3')!.textContent).toBe('Place');
  });

  it('remembers open and closed per user and model by title, and Reset goes back to the start', () => {
    const storage = new Map<string, string>();
    const store = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) } as unknown as Storage;
    const prefs = new Prefs('sam', store);
    const folds = new Folds(prefs.cell<Record<string, boolean>>('form.Site.open', {}), false, false);
    const host = render(() => section(folds));
    host.querySelector<HTMLButtonElement>('.fs-toggle')!.click();
    expect(JSON.parse(storage.get('evlet.sam.form.Site.open')!)).toEqual({ Place: false });
    expect(folds.changed).toBe(true);
    // Read back by a new page for the same person.
    const again = new Prefs('sam', store);
    const later = render(() => section(new Folds(again.cell<Record<string, boolean>>('form.Site.open', {}), false, false)));
    expect(later.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('false');
    folds.reset();
    expect(JSON.parse(storage.get('evlet.sam.form.Site.open')!)).toEqual({});
    expect(host.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('true');
    prefs.dispose(); again.dispose();
  });

  it('carries sections closed in the side panel under the old key over once, for page and panel alike', () => {
    const storage = new Map<string, string>([['evlet.sam.panel.Site.closed', JSON.stringify(['Place', 'Notes'])], ['evlet.sam.form.Site.open', JSON.stringify({ Notes: true })]]);
    const store = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) } as unknown as Storage;
    const prefs = new Prefs('sam', store);
    // A choice already saved under the new key wins over the old panel list.
    expect(prefs.formSections('Site').peek()).toEqual({ Place: false, Notes: true });
    expect(storage.has('evlet.sam.panel.Site.closed')).toBe(false);
    expect(JSON.parse(storage.get('evlet.sam.form.Site.open')!)).toEqual({ Place: false, Notes: true });
    expect(prefs.formSections('Site')).toBe(prefs.formSections('Site'));
    prefs.dispose();
  });

  it('revealField opens the section that holds a field, scrolls to it and focuses it', () => {
    const choices = saved();
    const host = render(() => section(new Folds(choices, false, false), fields(), { collapsed: true }));
    expect(revealField(host, 'city')).toBe(true);
    expect(host.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(host.querySelector('[data-field="city"] input'));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    // Opening for a reveal is not a saved choice.
    expect(choices.peek()).toEqual({});
    expect(revealField(host, 'missing')).toBe(false);
  });

  it('after a refused save opens every section with an error and focuses the first error', () => {
    const one = fields(), two = [localField({ name: 'code', type: 'text', label: 'Code' }, null)];
    const folds = new Folds(saved(), false, false);
    const host = render(() => [
      FoldSection({ title: 'First', fields: one, folds, collapsible: true, collapsed: true, index: 0, body: body(one) }),
      FoldSection({ title: 'Second', fields: two, folds, collapsible: true, collapsed: true, index: 1, body: body(two as any) }),
    ]);
    // Errors set while closed open the sections on their own; a save that set them earlier is revealed here.
    folds.reset();
    one[1].error.set('Choose a city');
    two[0].error.set('Use a code');
    for (const toggle of host.querySelectorAll<HTMLButtonElement>('.fs-toggle')) if (toggle.getAttribute('aria-expanded') === 'true') toggle.click();
    expect(revealFirstError(host, [...one, ...two])).toBe(true);
    const toggles = [...host.querySelectorAll('.fs-toggle')].map(toggle => toggle.getAttribute('aria-expanded'));
    expect(toggles).toEqual(['true', 'true']);
    expect(document.activeElement).toBe(host.querySelector('[data-field="city"] input'));
  });

  it('animates the height only when reduced motion is not asked for', () => {
    const animate = vi.fn(() => ({ cancel() {}, onfinish: null, oncancel: null }));
    (HTMLElement.prototype as any).animate = animate;
    try {
      const host = render(() => section(new Folds(saved(), false, false)));
      host.querySelector<HTMLButtonElement>('.fs-toggle')!.click();
      expect(animate).toHaveBeenCalledOnce();
      const reduce = vi.mocked(window.matchMedia);
      reduce.mockImplementation(((query: string) => ({ matches: query.includes('reduced-motion'), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as any);
      host.querySelector<HTMLButtonElement>('.fs-toggle')!.click();
      expect(animate).toHaveBeenCalledOnce();
      expect(host.querySelector<HTMLElement>('.fs-body')!.hidden).toBe(false);
      reduce.mockImplementation(((query: string) => ({ matches: false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as any);
    } finally { delete (HTMLElement.prototype as any).animate; }
  });

  it('FormSection with fold uses the same section, with inline values', () => {
    const list = fields();
    const host = render(() => FormSection({ title: 'Place', fields: list, fold: { folds: new Folds(saved(), false, false), collapsible: true, collapsed: true, index: 0 } }));
    expect(host.querySelector('.fs-toggle')!.getAttribute('aria-expanded')).toBe('false');
    expect(host.querySelectorAll('.fs-body .ff')).toHaveLength(3);
  });
});

describe('print', () => {
  it('shows closed sections when printing', () => {
    // Vitest hands CSS imports over empty, so the stylesheet is read from disk (it runs from frontend/).
    const node = (globalThis as any).process;
    const css: string = node.getBuiltinModule('node:fs').readFileSync(`${node.cwd()}/src/evlet/ui/styles.css`, 'utf8');
    expect(css).toMatch(/@media print\{\.fs-fold \.fs-body\[hidden\]\{display:block!important\}/);
  });
});
