// The RichText field type in forms: BaseField, InlineValue (read first, edit in place), the read-only
// view, list cells, filters, and phone width.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { conditionsFor } from '../app/filters';
import { BaseField, displayValue, FieldControl, localField, RichTextView } from '../ui/fields';
import { InlineValue } from '../ui/inline';
import { formatValue } from '../ui/kinds';
import { cleanup, render } from './ui-helpers';

afterEach(cleanup);

const meta = { name: 'brief', type: 'richtext', label: 'Brief', max_length: 20000 };
const beforeinput = (target: Element, inputType: string, data: string | null = null) =>
  target.dispatchEvent(new InputEvent('beforeinput', { inputType, data, bubbles: true, cancelable: true }));

describe('RichText field', () => {
  it('BaseField edits with the editor, labelled by the field label, and writes the field', () => {
    const field = localField(meta, '<p>Hi</p>');
    const host = render(() => BaseField({ field }));
    const surface = host.querySelector<HTMLElement>('.rt-surface')!;
    expect(surface.getAttribute('aria-labelledby')).toBe(host.querySelector('label')!.id);
    expect(host.querySelector('.fld')!.classList.contains('full')).toBe(true);
    surface.focus();
    const text = surface.querySelector('p')!.firstChild!;
    window.getSelection()!.collapse(text, 2);
    beforeinput(surface, 'insertText', '!');
    expect(field.value.get()).toBe('<p>Hi!</p>');
  });
  it('writes null for an empty document and says when it is required', () => {
    const field = localField({ ...meta, required: true }, '<p>x</p>');
    const control = FieldControl(field, { commitOn: 'manual' });
    render(() => control.element);
    const surface = control.element.querySelector<HTMLElement>('.rt-surface')!;
    control.focus();
    window.getSelection()!.collapse(surface.querySelector('p')!.firstChild!, 1);
    beforeinput(surface, 'deleteContentBackward');
    expect(field.value.get()).toBe('<p>x</p>'); // manual: nothing written until commit
    expect(control.commit()).toBe(false);
    expect(field.error.get()).toBe('Brief is required');
  });
  it('is read only when locked', () => {
    const field = localField(meta, '<p>x</p>', { locked: true });
    const host = render(() => BaseField({ field }));
    expect(host.querySelector('.rt-surface')!.getAttribute('contenteditable')).toBe('false');
  });
  it('shows formatted text at rest, with nested lists and safe links', () => {
    const view = RichTextView('<h2>Scope</h2><ul><li>a<ul><li>b</li></ul></li></ul><ul data-type="checklist"><li data-checked="true">done</li></ul>'
      + '<p><a href="https://evlet.io">site</a> <a href="javascript:alert(1)">bad</a></p>');
    document.body.append(view);
    expect(view.querySelector('h2')!.textContent).toBe('Scope');
    expect(view.querySelectorAll('ul ul li')).toHaveLength(1);
    expect(view.querySelector('li[data-checked="true"] .rt-check')!.getAttribute('aria-label')).toBe('Done');
    const links = view.querySelectorAll('a');
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('rel')).toBe('noopener noreferrer nofollow');
    expect(view.innerHTML).not.toMatch(/javascript/);
  });
  it('displayValue updates when the value changes', () => {
    const field = localField(meta, '<p>one</p>');
    const host = render(() => displayValue(field));
    expect(host.textContent).toBe('one');
    field.value.set('<p>two</p>');
    expect(host.textContent).toBe('two');
    field.value.set(null);
    expect(host.textContent).toBe('Not set');
  });
  it('InlineValue: Enter adds a line, Ctrl or Cmd Enter saves, links at rest are text', async () => {
    const field = localField(meta, '<p>see <a href="https://evlet.io">docs</a></p>');
    const save = vi.fn(async () => true);
    const host = render(() => InlineValue({ field, save, confirm: false }));
    expect(host.querySelector('button.fval a')).toBeNull();
    expect(host.querySelector('button.fval .rt-a')!.textContent).toBe('docs');
    host.querySelector<HTMLButtonElement>('button.fval')!.click();
    const surface = host.querySelector<HTMLElement>('.rt-surface')!;
    surface.focus();
    window.getSelection()!.collapse(surface.querySelector('p')!.firstChild!, 3);
    beforeinput(surface, 'insertParagraph');
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    surface.dispatchEvent(enter);
    expect(save).not.toHaveBeenCalled();
    surface.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, metaKey: true, bubbles: true, cancelable: true }));
    await Promise.resolve();
    expect(field.value.get()).toBe('<p>see</p><p>&nbsp;<a href="https://evlet.io">docs</a></p>');
    expect(save).toHaveBeenCalledTimes(1);
  });
  it('lists show plain text', () => {
    expect(formatValue(meta as any, '<h2>Scope</h2><ul><li>Survey &amp; plan</li></ul>')).toBe('Scope\nSurvey & plan');
    expect(formatValue(meta as any, null)).toBe('');
  });
  it('filters offer only text matching', () => {
    const conditions = conditionsFor({ ...meta, required: false, read_only: false, operators: ['contains', 'icontains', 'nicontains', 'isnull'] } as any);
    expect(conditions.length).toBeGreaterThan(0);
    expect(conditions.every(item => item.needs.every(op => ['contains', 'icontains', 'nicontains', 'isnull'].includes(op)))).toBe(true);
  });
});
