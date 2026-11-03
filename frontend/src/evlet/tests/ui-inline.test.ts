import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { localField } from '../ui/fields';
import { FormSection, InlineValue, KeyValueList } from '../ui/inline';
import { responses } from './server';
import { backend, change, cleanup, key, pick, render } from './ui-helpers';

afterEach(() => { vi.useRealTimers(); cleanup(); });
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

describe('InlineValue with the record store', () => {
  it('reads first, edits on click, saves one field on Enter with no Save button, and offers Undo', async () => {
    const { store, sent } = backend();
    await store.open(1);
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
    await store.open(1);
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
    await store.open(1);
    const host = render(() => InlineValue({ field: store.field('stage') }));
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    pick(host.querySelector<HTMLElement>('.in-select')!, 'Won');
    await flush();
    expect(sent.find(item => item.method === 'PATCH')!.body.stage).toBe('won');
  });

  it('stays in edit mode with the server message when the save is refused', async () => {
    const { store } = backend([['PATCH', /\/api\/order\/1$/, { status: 422, etag: null, body: { error: 'Validation failed', fields: { title: 'Use a shorter title' } } }]]);
    await store.open(1);
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
    await store.open(1);
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

  it('opens and closes from its heading when collapsible', () => {
    const collapsed = cell(false);
    const fields = [localField({ name: 'name', type: 'text', label: 'Name' }, 'Al Sahra')];
    const host = render(() => FormSection({ title: 'Details', fields, collapsible: { collapsed: () => collapsed.get(), toggle: () => collapsed.set(!collapsed.peek()) } }));
    const toggle = host.querySelector<HTMLButtonElement>('.fs-toggle')!;
    expect(toggle.textContent).toBe('Details');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const body = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    expect(body.hidden).toBe(false);
    toggle.click();
    expect(collapsed.peek()).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(body.hidden).toBe(true);
    expect(host.querySelector('section')!.classList.contains('closed')).toBe(true);
  });
});
