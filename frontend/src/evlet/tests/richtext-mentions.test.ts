// Mentions in the activity composer (richtext/editor.ts with mentions, app/composer.ts): the @ picker
// by keyboard and touch, storage as a mention node, the current name on display and the warning for
// someone who cannot open the record. The notification centre is in notifications.test.ts.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { RichTextEditor, type Person, type RichTextEditor as Editor } from '../richtext/editor';
import { RichTextView } from '../ui/fields';
import { cleanup, render } from './ui-helpers';

afterEach(() => { cleanup(); vi.useRealTimers(); });

const PEOPLE: Person[] = [
  { id: '2', name: 'Priya Nair', username: 'priya', can_read: false },
  { id: '3', name: 'Omar Farouk', username: 'omar', can_read: true },
];

function setup(initial: string | null = null) {
  vi.useFakeTimers();
  const value = cell<string | null>(initial);
  const changes: Array<string | null> = [];
  const search = vi.fn(async (query: string) => PEOPLE.filter(person => `${person.name} ${person.username}`.toLowerCase().includes(query.toLowerCase())));
  let editor!: Editor;
  render(() => {
    editor = RichTextEditor({ value: () => value.get(), onChange: next => { changes.push(next); value.set(next); }, label: 'Note',
      mentions: { search, name: id => ({ '3': 'Omar F.' } as Record<string, string>)[id] } });
    return editor.element;
  });
  editor.focus();
  return { editor, changes, search };
}

const type = (editor: Editor, text: string) => { for (const char of text) editor.surface.dispatchEvent(new InputEvent('beforeinput', { inputType: 'insertText', data: char, bubbles: true, cancelable: true })); };
const key = (editor: Editor, name: string) => editor.surface.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
async function settle() { await vi.advanceTimersByTimeAsync(200); }
const options = (editor: Editor) => Array.from(editor.element.querySelectorAll<HTMLElement>('.rt-mentions [role="option"]'));

describe('the @ picker', () => {
  it('opens on @, searches by name and username, and mentions by keyboard', async () => {
    const { editor, changes, search } = setup();
    type(editor, 'Ask @om');
    await settle();
    expect(search).toHaveBeenLastCalledWith('om');
    expect(options(editor).map(option => option.querySelector('.rt-person-name')!.textContent)).toEqual(['Omar Farouk']);
    key(editor, 'Enter');
    // Stored as a mention node; Enter did not start a new paragraph.
    expect(changes.at(-1)).toBe('<p>Ask <span data-mention="3">Omar Farouk</span>&nbsp;</p>');
    expect(editor.element.querySelector('.rt-mentions')!.hasAttribute('hidden')).toBe(true);
    // On the surface it reads as @Name (current name) and is one position wide.
    expect(editor.surface.querySelector('.rt-mention')!.textContent).toBe('@Omar F.');
    type(editor, 'x');
    expect(changes.at(-1)).toBe('<p>Ask <span data-mention="3">Omar Farouk</span> x</p>');
  });
  it('moves with the arrow keys, picks by touch, and closes on Escape without leaving the form', async () => {
    const { editor, changes } = setup();
    type(editor, '@');
    await settle();
    expect(options(editor)).toHaveLength(2);
    key(editor, 'ArrowDown');
    expect(options(editor)[1].getAttribute('aria-selected')).toBe('true');
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    const outer = vi.fn();
    editor.element.parentElement!.addEventListener('keydown', outer);
    editor.surface.dispatchEvent(escape);
    expect(outer).not.toHaveBeenCalled();
    expect(editor.element.querySelector('.rt-mentions')!.hasAttribute('hidden')).toBe(true);
    type(editor, 'p');
    await settle();
    options(editor)[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(changes.at(-1)).toBe('<p><span data-mention="2">Priya Nair</span>&nbsp;</p>');
  });
  it('warns while composing when the person cannot open the record', async () => {
    const { editor } = setup();
    type(editor, '@pri');
    await settle();
    expect(options(editor)[0].querySelector('.rt-person-warn')!.textContent).toBe("Can't open this record");
    key(editor, 'Enter');
    expect(editor.element.querySelector('.rt-warn')!.textContent).toContain("Priya Nair can't open this record");
  });
  it('does not open in the middle of a word (an email address)', async () => {
    const { editor, search } = setup();
    type(editor, 'mail me@x');
    await settle();
    expect(search).not.toHaveBeenCalled();
  });
  it('Backspace after a mention removes it whole', async () => {
    const { editor, changes } = setup('<p>Hi <span data-mention="3">Omar</span></p>');
    editor.surface.dispatchEvent(new InputEvent('beforeinput', { inputType: 'deleteContentBackward', bubbles: true, cancelable: true }));
    expect(changes.at(-1)).toBe('<p>Hi&nbsp;</p>');
  });
});

describe('showing mentions', () => {
  it('re-renders with the current name; a field without mentions shows plain text', () => {
    const view = RichTextView('<p>Thanks <span data-mention="3">Omar</span></p>', { mentions: id => id === '3' ? 'Omar Farouk' : undefined });
    expect(view.querySelector('.rt-mention')!.textContent).toBe('@Omar Farouk');
    const fallback = RichTextView('<p><span data-mention="9">Old name</span></p>', { mentions: () => undefined });
    expect(fallback.textContent).toBe('@Old name');
    expect(RichTextView('<p><span data-mention="3">Omar</span></p>').querySelector('.rt-mention')).toBeNull();
  });
});
