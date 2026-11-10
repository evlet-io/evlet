import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { localField } from '../ui/fields';
import { Folds } from '../ui/fold';
import { FormSection, InlineValue, KeyValueList } from '../ui/inline';
import { responses } from './server';
import { backend, id, change, cleanup, key, pick, render } from './ui-helpers';

afterEach(() => { vi.useRealTimers(); cleanup(); });
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

describe('InlineValue with the record store', () => {
  it('reads first, edits on click, saves one field on Enter with no Save button, and offers Undo', async () => {
    const { store, sent } = backend();
    await store.open(id(1));
    const host = render(() => InlineValue({ field: store.field('title') }));
    const rest = host.querySelector<HTMLButtonElement>('button.fval')!;
    expect(rest.textContent).toContain('Big order');
    expect(rest.getAttribute('aria-label')).toBe('Edit Title');
    rest.click();
    const input = host.querySelector('input')!;
    expect(document.activeElement).toBe(input);
    input.value = 'Bigger order';
    key(input, 'Enter');
    await flush();
    const patch = sent.find(item => item.method === 'PATCH')!;
    expect(patch.body.title).toBe('Bigger order');
    expect(patch.headers['if-match']).toBe(responses.detail.etag);
    expect(host.querySelector('input')).toBeNull();
    expect(host.querySelector('button.fval')!.textContent).toContain(responses.update.body.title);
    const toast = document.querySelector('.toast')!;
    expect(toast.textContent).toContain('Title saved');
    expect(toast.querySelector('button')!.textContent).toBe('Undo');
  });

  it('cancels with Esc and restores the value, without a request', async () => {
    const { store, sent } = backend();
    await store.open(id(1));
    const host = render(() => InlineValue({ field: store.field('title') }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    const input = host.querySelector('input')!;
    input.value = 'Something else';
    key(input, 'Escape');
    expect(host.querySelector('input')).toBeNull();
    expect(store.field('title').value.peek()).toBe('Big order');
    expect(sent.some(item => item.method === 'PATCH')).toBe(false);
    expect(document.activeElement).toBe(host.querySelector('button.fval'));
  });

  it('saves a select as soon as it changes', async () => {
    const { store, sent } = backend();
    await store.open(id(1));
    const host = render(() => InlineValue({ field: store.field('stage') }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    pick(host.querySelector<HTMLElement>('.in-select')!, 'Won');
    await flush();
    expect(sent.find(item => item.method === 'PATCH')!.body.stage).toBe('won');
  });

  it('stays in edit mode with the server message when the save is refused', async () => {
    const { store } = backend([['PATCH', /\/api\/order\/0000000000001$/, { status: 422, etag: null, body: { error: 'Validation failed', fields: { title: 'Use a shorter title' } } }]]);
    await store.open(id(1));
    const host = render(() => InlineValue({ field: store.field('title') }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    const input = host.querySelector('input')!;
    input.value = 'x'.repeat(40);
    key(input, 'Enter');
    await flush();
    expect(host.querySelector('input')).not.toBeNull();
    expect(host.querySelector('[role="alert"]')!.textContent).toBe('Use a shorter title');
    expect(document.querySelector('.toast')).toBeNull();
  });

  it('shows calculated and locked values read only, with the reason on hover', async () => {
    const { store } = backend();
    await store.open(id(1));
    const host = render(() => KeyValueList([store.field('total')]));
    const value = host.querySelector<HTMLElement>('.fval.ro')!;
    expect(host.querySelector('button.fval')).toBeNull();
    value.dispatchEvent(new FocusEvent('focus'));
    expect(document.querySelector('.tip')!.textContent).toBe('Calculated from a rule');
    expect(host.querySelector('dt')!.id).toBe(value.getAttribute('aria-labelledby'));
  });
});

describe('FormSection', () => {
  it('lays out fields with labels, hints and inline values; long text spans both columns', () => {
    const save = vi.fn(() => true);
    const fields = [localField({ name: 'name', type: 'text', label: 'Name', required: true }, 'Al Sahra'), localField({ name: 'notes', type: 'longtext', label: 'Notes' }, 'Line one')];
    const host = render(() => FormSection({ title: 'Details', note: 'Saved as you change them.', fields, save }));
    expect(host.querySelector('section')!.getAttribute('aria-label')).toBe('Details');
    expect(host.querySelectorAll('.ff')).toHaveLength(2);
    expect(host.querySelector('[data-field="notes"]')!.classList.contains('full')).toBe(true);
    host.querySelector<HTMLButtonElement>('[data-field="name"] button.fval')!.click();
    const input = host.querySelector<HTMLInputElement>('[data-field="name"] input')!;
    input.value = 'Al Sahra Trading';
    key(input, 'Enter');
    expect(fields[0].value.peek()).toBe('Al Sahra Trading');
    expect(save).toHaveBeenCalledOnce();
  });

  // Chrome fires focusout (with no relatedTarget) on a focused element as it leaves the page; jsdom
  // does not. A mouse click also focuses the button on mousedown, before the click. Both together are
  // what a person's click does in the browser.
  it('turns each value into its input on a mouse click, field after field and back again', async () => {
    const removeChild = Node.prototype.removeChild;
    Node.prototype.removeChild = function <T extends Node>(this: Node, child: T): T {
      const active = document.activeElement;
      if (active && active !== document.body && child.contains(active)) active.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
      return removeChild.call(this, child) as T;
    };
    try {
      const { store } = backend();
      await store.open(id(1));
      const host = render(() => FormSection({ title: 'Details', fields: [store.field('title'), store.field('stage'), store.field('remark')] }));
      const click = (name: string) => {
        const button = host.querySelector<HTMLButtonElement>(`[data-field="${name}"] button.fval`)!;
        button.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        button.focus();
        button.click();
      };
      const editing = (name: string) => Boolean(host.querySelector(`[data-field="${name}"] .fval-edit`));
      for (const name of ['stage', 'title', 'remark', 'title', 'stage', 'remark']) {
        click(name);
        await flush();
        expect(editing(name), `${name} opens for editing`).toBe(true);
      }
    } finally {
      Node.prototype.removeChild = removeChild;
    }
  });

  it('opens and closes from its heading when collapsible', () => {
    const saved = cell<Record<string, boolean>>({});
    const fields = [localField({ name: 'name', type: 'text', label: 'Name' }, 'Al Sahra')];
    const host = render(() => FormSection({ title: 'Details', fields, fold: { folds: new Folds(saved, false, false), collapsible: true, collapsed: false, index: 0 } }));
    const toggle = host.querySelector<HTMLButtonElement>('.fs-toggle')!;
    expect(toggle.textContent!.trim()).toBe('Details');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const body = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    expect(body.hidden).toBe(false);
    toggle.click();
    expect(saved.peek()).toEqual({ Details: false });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(body.hidden).toBe(true);
    expect(host.querySelector('section')!.classList.contains('closed')).toBe(true);
  });
});

describe('one click opens the picker', () => {
  const choice = () => localField({ name: 'kind', type: 'select', label: 'Kind', choices: ['visit', 'repair'] }, 'visit');
  const day = () => localField({ name: 'due', type: 'date', label: 'Due' }, '2026-11-10');
  const text = () => localField({ name: 'note', type: 'text', label: 'Note' }, 'Hello');

  it('opens a Select list and a date picker on the click that starts editing; Esc closes the picker and keeps editing', async () => {
    for (const make of [choice, day]) {
      const host = render(() => InlineValue({ field: make(), save: () => true, confirm: false }));
      host.querySelector<HTMLButtonElement>('button.fval')!.click();
      await flush();
      expect(host.querySelector('.fval-edit')).not.toBeNull();
      const pop = document.querySelector<HTMLElement>('.pop')!;
      expect(pop).not.toBeNull();
      key(pop.querySelector<HTMLElement>('input, button, [tabindex]') ?? pop, 'Escape');
      await flush();
      expect(document.querySelector('.pop')).toBeNull();
      expect(host.querySelector('.fval-edit')).not.toBeNull();
      cleanup();
    }
  });

  it('only focuses a text input, and Tab onto a value opens nothing', async () => {
    const host = render(() => InlineValue({ field: text(), save: () => true, confirm: false }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    await flush();
    expect(document.activeElement).toBe(host.querySelector('.fval-edit input'));
    expect(document.querySelector('.pop')).toBeNull();
    cleanup();
    const other = render(() => InlineValue({ field: choice(), save: () => true, confirm: false }));
    other.querySelector<HTMLButtonElement>('button.fval')!.focus();
    await flush();
    expect(other.querySelector('.fval-edit')).toBeNull();
    expect(document.querySelector('.pop')).toBeNull();
  });
});
