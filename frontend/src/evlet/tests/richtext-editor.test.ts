// The rich text editor in the DOM (richtext/editor.ts): every beforeinput type, composition and phone
// keyboards, selection mapping across renders, undo grouping, shortcuts, paste, the toolbar and phones.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { RichTextEditor, type RichTextEditor as Editor } from '../richtext/editor';
import { caret, pos, type Selection } from '../richtext/model';
import { pointToPos, posToPoint } from '../richtext/render';
import { cleanup, render } from './ui-helpers';

afterEach(() => { cleanup(); vi.useRealTimers(); });

function setup(initial: string | null = '<p></p>', options: { disabled?: boolean; maxLength?: number } = {}) {
  const value = cell<string | null>(initial);
  const changes: Array<string | null> = [];
  let editor!: Editor;
  const disabled = cell(Boolean(options.disabled));
  render(() => {
    editor = RichTextEditor({ value: () => value.get(), onChange: next => { changes.push(next); value.set(next); }, label: 'Brief', disabled: () => disabled.get(), maxLength: options.maxLength });
    return editor.element;
  });
  editor.focus();
  return { editor, value, changes, disabled, surface: editor.surface };
}

/** Put the DOM selection at model positions (as a user's click would). */
function select(editor: Editor, anchor: [number, number], head: [number, number] = anchor) {
  const blocks = Array.from(editor.surface.children);
  const [a, ao] = posToPoint(blocks[anchor[0]], anchor[1]);
  const [h, ho] = posToPoint(blocks[head[0]], head[1]);
  window.getSelection()!.setBaseAndExtent(a, ao, h, ho);
  document.dispatchEvent(new Event('selectionchange'));
}

function input(editor: Editor, inputType: string, init: Partial<InputEventInit> & { dataTransfer?: DataTransfer | null; cancelable?: boolean } = {}) {
  const event = new InputEvent('beforeinput', { inputType, data: init.data ?? null, bubbles: true, cancelable: init.cancelable ?? true });
  if (init.dataTransfer) Object.defineProperty(event, 'dataTransfer', { value: init.dataTransfer });
  const allowed = editor.surface.dispatchEvent(event);
  return allowed;
}

function type(editor: Editor, text: string) { for (const char of text) input(editor, 'insertText', { data: char }); }

function transfer(data: Record<string, string>): DataTransfer {
  return { getData: (kind: string) => data[kind] ?? '', setData: (kind: string, value: string) => { data[kind] = value; }, types: Object.keys(data) } as unknown as DataTransfer;
}

const sel = (editor: Editor): Selection => editor.state().selection;

describe('the surface', () => {
  it('opens with the caret at the end of the text', () => {
    const { editor } = setup('<p>one</p><p>two</p>');
    expect(editor.state().selection).toEqual(caret(pos(1, 3)));
  });
  it('is an accessible multi-line textbox', () => {
    const { surface } = setup();
    expect(surface.getAttribute('role')).toBe('textbox');
    expect(surface.getAttribute('aria-multiline')).toBe('true');
    expect(surface.getAttribute('aria-label')).toBe('Brief');
    expect(surface.getAttribute('contenteditable')).toBe('true');
  });
  it('renders stored HTML from the model, never as markup', () => {
    const { surface } = setup('<p>a<img src=x onerror="window.hit=1"><script>window.hit=2</script><a href="javascript:x()">b</a></p>');
    expect(surface.innerHTML).not.toMatch(/img|script|javascript|onerror/);
    expect(surface.textContent).toBe('ab');
    expect((window as any).hit).toBeUndefined();
  });
  it('is read only when disabled', () => {
    const { surface, editor, changes } = setup('<p>x</p>', { disabled: true });
    expect(surface.getAttribute('contenteditable')).toBe('false');
    expect(surface.getAttribute('aria-readonly')).toBe('true');
    select(editor, [0, 1]);
    expect(input(editor, 'insertText', { data: 'y' })).toBe(false);
    expect(changes).toEqual([]);
  });
});

describe('beforeinput', () => {
  it('cancels typing and applies it to the model', () => {
    const { editor, changes, surface } = setup();
    expect(input(editor, 'insertText', { data: 'H' })).toBe(false);
    type(editor, 'i!');
    expect(changes.at(-1)).toBe('<p>Hi!</p>');
    expect(surface.textContent).toBe('Hi!');
    expect(sel(editor).head).toEqual(pos(0, 3));
  });
  it('keeps the caret in place across renders (selection mapping)', () => {
    const { editor } = setup('<p>ab<strong>cd</strong>ef</p>');
    select(editor, [0, 3]);
    type(editor, 'X');
    const selection = window.getSelection()!;
    expect(pointToPos(editor.surface, selection.focusNode, selection.focusOffset)).toEqual(pos(0, 4));
    expect(editor.state().doc.blocks[0].content).toMatchObject([{ text: 'ab' }, { text: 'cXd', marks: ['strong'] }, { text: 'ef' }]);
  });
  it('replaces a selection, and insertReplacementText (spellcheck, autocorrect) uses its data transfer', () => {
    const { editor, changes } = setup('<p>teh cat</p>');
    select(editor, [0, 0], [0, 3]);
    input(editor, 'insertReplacementText', { dataTransfer: transfer({ 'text/plain': 'the' }) });
    expect(changes.at(-1)).toBe('<p>the cat</p>');
  });
  it('insertParagraph and insertLineBreak', () => {
    const { editor, changes } = setup('<p>ab</p>');
    select(editor, [0, 1]);
    input(editor, 'insertParagraph');
    expect(changes.at(-1)).toBe('<p>a</p><p>b</p>');
    input(editor, 'insertLineBreak');
    expect(changes.at(-1)).toBe('<p>a</p><p><br>b</p>');
  });
  it('every delete type', () => {
    const { editor, changes } = setup('<p>one two three</p><p>next</p>');
    select(editor, [0, 13]);
    input(editor, 'deleteContentBackward');
    expect(changes.at(-1)).toBe('<p>one two thre</p><p>next</p>');
    input(editor, 'deleteWordBackward');
    expect(changes.at(-1)).toBe('<p>one two&nbsp;</p><p>next</p>');
    select(editor, [0, 0]);
    input(editor, 'deleteWordForward');
    expect(changes.at(-1)).toBe('<p>&nbsp;two&nbsp;</p><p>next</p>');
    input(editor, 'deleteContentForward');
    expect(changes.at(-1)).toBe('<p>two&nbsp;</p><p>next</p>');
    select(editor, [0, 4]);
    input(editor, 'deleteContentForward');
    expect(changes.at(-1)).toBe('<p>two next</p>');
    input(editor, 'deleteSoftLineBackward');
    expect(changes.at(-1)).toBe('<p>next</p>');
    input(editor, 'deleteHardLineForward');
    expect(changes.at(-1)).toBe(null);
    for (const kind of ['deleteSoftLineForward', 'deleteHardLineBackward', 'deleteByCut', 'deleteContent', 'deleteByDrag', 'deleteEntireSoftLine']) {
      expect(input(editor, kind)).toBe(false);
    }
  });
  it('deleteByCut and deleteContent remove the selection', () => {
    const { editor, changes } = setup('<p>abcdef</p>');
    select(editor, [0, 1], [0, 3]);
    input(editor, 'deleteByCut');
    expect(changes.at(-1)).toBe('<p>adef</p>');
    select(editor, [0, 0], [0, 1]);
    input(editor, 'deleteContent');
    expect(changes.at(-1)).toBe('<p>def</p>');
  });
  it('format and history input types', () => {
    const { editor, changes } = setup('<p>word</p>');
    select(editor, [0, 0], [0, 4]);
    input(editor, 'formatBold');
    expect(changes.at(-1)).toBe('<p><strong>word</strong></p>');
    input(editor, 'formatItalic'); input(editor, 'formatUnderline'); input(editor, 'formatStrikeThrough');
    expect(changes.at(-1)).toBe('<p><strong><em><u><s>word</s></u></em></strong></p>');
    input(editor, 'historyUndo');
    expect(changes.at(-1)).toBe('<p><strong><em><u>word</u></em></strong></p>');
    input(editor, 'historyRedo');
    expect(changes.at(-1)).toBe('<p><strong><em><u><s>word</s></u></em></strong></p>');
  });
  it('insertLink, insertFromPaste, insertFromDrop and insertFromYank', () => {
    const { editor, changes } = setup('<p>docs</p>');
    select(editor, [0, 0], [0, 4]);
    input(editor, 'insertLink', { data: 'evlet.io' });
    expect(changes.at(-1)).toBe('<p><a href="https://evlet.io">docs</a></p>');
    select(editor, [0, 4]);
    input(editor, 'insertFromPaste', { dataTransfer: transfer({ 'text/html': '<b>!</b>', 'text/plain': '!' }) });
    expect(changes.at(-1)).toBe('<p><a href="https://evlet.io">docs</a><strong>!</strong></p>');
    input(editor, 'insertFromDrop', { dataTransfer: transfer({ 'text/plain': '?' }) });
    input(editor, 'insertFromYank', { dataTransfer: transfer({ 'text/plain': '.' }) });
    expect(changes.at(-1)).toBe('<p><a href="https://evlet.io">docs</a><strong>!</strong>?.</p>');
  });
  it('cancels input types it does not know, so the DOM never drifts', () => {
    const { editor, changes } = setup('<p>a</p>');
    expect(input(editor, 'formatJustifyCenter')).toBe(false);
    expect(input(editor, 'formatFontColor')).toBe(false);
    expect(changes).toEqual([]);
  });
});

describe('composition and phone keyboards', () => {
  it('lets the IME edit the text node and reads it back at compositionend', () => {
    const { editor, changes, surface } = setup('<p>ka</p>');
    select(editor, [0, 2]);
    surface.dispatchEvent(new CompositionEvent('compositionstart', { data: '' }));
    // The browser edits the text node itself while composing; the editor does not render.
    const node = surface.querySelector('p')!.firstChild as Text;
    expect(input(editor, 'insertCompositionText', { data: 'ん', cancelable: false })).toBe(true);
    node.data = 'kaん';
    surface.dispatchEvent(new InputEvent('input', { inputType: 'insertCompositionText', data: 'ん', isComposing: true }));
    expect(changes).toEqual([]);
    node.data = 'ka日本';
    window.getSelection()!.collapse(node, 4);
    surface.dispatchEvent(new CompositionEvent('compositionend', { data: '日本' }));
    expect(changes.at(-1)).toBe('<p>ka日本</p>');
    expect(sel(editor).head).toEqual(pos(0, 4));
    // Our own nodes replace the browser's.
    expect(surface.querySelector('p')!.textContent).toBe('ka日本');
  });
  it('keeps marks when a composition happens inside bold text', () => {
    const { editor, surface } = setup('<p><strong>ab</strong></p>');
    select(editor, [0, 1]);
    surface.dispatchEvent(new CompositionEvent('compositionstart'));
    (surface.querySelector('strong')!.firstChild as Text).data = 'aéb';
    surface.dispatchEvent(new CompositionEvent('compositionend'));
    expect(editor.state().doc.blocks[0].content).toMatchObject([{ text: 'aéb', marks: ['strong'] }]);
  });
  it('reconciles uncancellable Android input (autocorrect, replacement) from the DOM', () => {
    const { editor, changes, surface } = setup('<p>helo world</p>');
    select(editor, [0, 4]);
    expect(input(editor, 'insertText', { data: 'l', cancelable: false })).toBe(true);
    (surface.querySelector('p')!.firstChild as Text).data = 'hello world';
    window.getSelection()!.collapse(surface.querySelector('p')!.firstChild!, 5);
    surface.dispatchEvent(new InputEvent('input', { inputType: 'insertText', data: 'l' }));
    expect(changes.at(-1)).toBe('<p>hello world</p>');
    expect(sel(editor).head).toEqual(pos(0, 5));
  });
  it('reads the whole surface back when the browser split a block itself', () => {
    const { editor, changes, surface } = setup('<h2>Title</h2><p>body</p>');
    const extra = document.createElement('div');
    extra.textContent = 'made by the browser';
    surface.insertBefore(extra, surface.children[1]);
    surface.dispatchEvent(new InputEvent('input', { inputType: 'insertParagraph' }));
    expect(changes.at(-1)).toBe('<h2>Title</h2><p>made by the browser</p><p>body</p>');
    expect(surface.children[1].tagName).toBe('P');
    expect(editor.state().doc.blocks).toHaveLength(3);
  });
});

describe('undo', () => {
  it('groups a typing burst, and a new word starts a new step', () => {
    const { editor, changes } = setup();
    type(editor, 'one two');
    input(editor, 'historyUndo');
    expect(changes.at(-1)).toBe('<p>one&nbsp;</p>');
    input(editor, 'historyUndo');
    expect(changes.at(-1)).toBe(null);
    input(editor, 'historyRedo');
    input(editor, 'historyRedo');
    expect(changes.at(-1)).toBe('<p>one two</p>');
  });
  it('a pause starts a new step', () => {
    vi.useFakeTimers();
    const { editor, changes } = setup();
    type(editor, 'ab');
    vi.advanceTimersByTime(1500);
    type(editor, 'cd');
    input(editor, 'historyUndo');
    expect(changes.at(-1)).toBe('<p>ab</p>');
  });
  it('moving the caret starts a new step', () => {
    const { editor, changes } = setup('<p>xy</p>');
    select(editor, [0, 2]);
    type(editor, 'a');
    select(editor, [0, 0]);
    type(editor, 'b');
    input(editor, 'historyUndo');
    expect(changes.at(-1)).toBe('<p>xya</p>');
  });
  it('Backspace right after an input rule gives the typed text back', () => {
    const { editor, changes } = setup();
    type(editor, '- ');
    expect(changes.at(-1)).toBe(null);
    expect(editor.state().doc.blocks[0]).toMatchObject({ type: 'item', list: 'bullet' });
    input(editor, 'deleteContentBackward');
    expect(changes.at(-1)).toBe('<p>-&nbsp;</p>');
  });
  it('Ctrl+Z after an input rule keeps the typed characters', () => {
    const { editor, changes } = setup();
    type(editor, '## ');
    expect(editor.state().doc.blocks[0]).toMatchObject({ type: 'heading', level: 2 });
    editor.surface.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, metaKey: true, bubbles: true, cancelable: true }));
    expect(changes.at(-1)).toBe('<p>##&nbsp;</p>');
  });
});

describe('keys', () => {
  const key = (editor: Editor, init: KeyboardEventInit) => editor.surface.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }));
  // jsdom reports no platform, so Ctrl is the modifier; set both to cover Mac and others.
  const mod = { ctrlKey: true, metaKey: true };
  it('Ctrl or Cmd with B, I and U toggle marks, at a caret for the next typing', () => {
    const { editor, changes } = setup('<p>x</p>');
    select(editor, [0, 1]);
    key(editor, { key: 'b', ...mod });
    type(editor, 'y');
    key(editor, { key: 'b', ...mod });
    key(editor, { key: 'i', ...mod });
    type(editor, 'z');
    expect(changes.at(-1)).toBe('<p>x<strong>y</strong><em>z</em></p>');
    select(editor, [0, 0], [0, 3]);
    key(editor, { key: 'u', ...mod });
    // Marks nest in one canonical order (bold outside underline), so the underline splits.
    expect(changes.at(-1)).toBe('<p><u>x</u><strong><u>y</u></strong><em><u>z</u></em></p>');
  });
  it('announces formatting changes', () => {
    const { editor } = setup('<p>x</p>');
    vi.useFakeTimers();
    select(editor, [0, 0], [0, 1]);
    key(editor, { key: 'b', ...mod });
    vi.advanceTimersByTime(50);
    expect(editor.element.querySelector('.rt-sr')!.textContent).toBe('Bold on');
  });
  it('Shift+Ctrl+7 and 8 make lists', () => {
    const { editor, changes } = setup('<p>a</p>');
    select(editor, [0, 0]);
    key(editor, { key: '&', code: 'Digit7', shiftKey: true, ...mod });
    expect(changes.at(-1)).toBe('<ol><li>a</li></ol>');
    key(editor, { key: '*', code: 'Digit8', shiftKey: true, ...mod });
    expect(changes.at(-1)).toBe('<ul><li>a</li></ul>');
  });
  it('Tab indents in a list and leaves the editor elsewhere', () => {
    const { editor, changes } = setup('<p>p</p><ul><li>a</li><li>b</li></ul>');
    select(editor, [2, 0]);
    expect(key(editor, { key: 'Tab' })).toBe(false);
    expect(changes.at(-1)).toBe('<p>p</p><ul><li>a<ul><li>b</li></ul></li></ul>');
    expect(key(editor, { key: 'Tab', shiftKey: true })).toBe(false);
    expect(changes.at(-1)).toBe('<p>p</p><ul><li>a</li><li>b</li></ul>');
    select(editor, [0, 0]);
    expect(key(editor, { key: 'Tab' })).toBe(true); // not prevented: focus moves on
  });
  it('Ctrl+Enter is left for the form (save), not a new line', () => {
    const { editor, changes } = setup('<p>a</p>');
    expect(key(editor, { key: 'Enter', ...mod })).toBe(false);
    expect(changes).toEqual([]);
  });
  it('Ctrl+K opens the link dialog, which refuses unsafe links and links the selection', async () => {
    const { editor, changes } = setup('<p>docs</p>');
    select(editor, [0, 0], [0, 4]);
    const palette = vi.fn();
    document.addEventListener('keydown', palette);
    key(editor, { key: 'k', ...mod });
    document.removeEventListener('keydown', palette);
    expect(palette).not.toHaveBeenCalled(); // the app's command palette does not open
    const field = document.querySelector<HTMLInputElement>('.rt-link-in')!;
    expect(field).toBeTruthy();
    field.value = 'javascript:alert(1)';
    document.querySelector<HTMLFormElement>('.rt-link')!.dispatchEvent(new Event('submit', { cancelable: true }));
    expect(document.querySelector('.rt-link .err')!.textContent).toMatch(/web address/);
    field.value = 'evlet.io';
    document.querySelector<HTMLFormElement>('.rt-link')!.dispatchEvent(new Event('submit', { cancelable: true }));
    expect(changes.at(-1)).toBe('<p><a href="https://evlet.io">docs</a></p>');
    // Typing carries on after the link, not over it and not inside it.
    type(editor, '!');
    expect(changes.at(-1)).toBe('<p><a href="https://evlet.io">docs</a>!</p>');
  });
});

describe('paste and copy', () => {
  const paste = (editor: Editor, data: Record<string, string>) => {
    const event = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: transfer(data) });
    editor.surface.dispatchEvent(event);
    return event;
  };
  it('pastes cleaned HTML at the caret', () => {
    const { editor, changes } = setup('<p>[]</p>');
    select(editor, [0, 1]);
    const event = paste(editor, { 'text/html': '<p style="color:red" class="x">one <script>x</script><b>two</b></p><h1 onclick="x">three</h1>', 'text/plain': 'one two three' });
    expect(event.defaultPrevented).toBe(true);
    expect(changes.at(-1)).toBe('<p>[one <strong>two</strong></p><h2>three]</h2>');
  });
  it('pastes plain text with paragraphs and links', () => {
    const { editor, changes } = setup();
    paste(editor, { 'text/plain': 'a\nb\n\nhttps://evlet.io' });
    expect(changes.at(-1)).toBe('<p>a<br>b</p><p><a href="https://evlet.io">https://evlet.io</a></p>');
  });
  it('a web address pasted over a selection links it', () => {
    const { editor, changes } = setup('<p>docs</p>');
    select(editor, [0, 0], [0, 4]);
    paste(editor, { 'text/plain': 'https://evlet.io/docs' });
    expect(changes.at(-1)).toBe('<p><a href="https://evlet.io/docs">docs</a></p>');
  });
  it('pastes into a code block as plain text', () => {
    const { editor, changes } = setup('<pre><code>x</code></pre>');
    select(editor, [0, 1]);
    paste(editor, { 'text/html': '<b>y</b>', 'text/plain': '\ny' });
    expect(changes.at(-1)).toBe('<pre><code>x\ny</code></pre>');
  });
  it('copies canonical HTML and plain text, and cut deletes', () => {
    const { editor, changes } = setup('<p>a<strong>bc</strong></p><h2>de</h2>');
    select(editor, [0, 1], [1, 1]);
    const data: Record<string, string> = {};
    const event = new Event('cut', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: transfer(data) });
    editor.surface.dispatchEvent(event);
    expect(data['text/html']).toBe('<p><strong>bc</strong></p><h2>d</h2>');
    expect(data['text/plain']).toBe('bc\nd');
    expect(changes.at(-1)).toBe('<p>ae</p>');
  });
});

describe('the toolbar', () => {
  it('is one Tab stop; the arrow keys move between buttons', () => {
    const { editor } = setup();
    const toolbar = editor.element.querySelector<HTMLElement>('[role="toolbar"]')!;
    const buttons = Array.from(toolbar.querySelectorAll<HTMLButtonElement>('button'));
    expect(buttons.filter(button => button.tabIndex === 0)).toHaveLength(1);
    buttons[0].focus();
    toolbar.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(document.activeElement).toBe(buttons[1]);
    expect(buttons[1].tabIndex).toBe(0);
    expect(buttons[0].tabIndex).toBe(-1);
    toolbar.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    // Undo and Redo are disabled with nothing to undo, and skipped.
    expect(document.activeElement).toBe(buttons.filter(button => !button.disabled).at(-1));
    toolbar.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(document.activeElement).toBe(buttons[0]);
  });
  it('shows what is on, and its buttons format the selection', () => {
    const { editor, changes } = setup('<p>word</p>');
    const button = (label: string) => editor.element.querySelector<HTMLButtonElement>(`[role="toolbar"] button[aria-label^="${label}"]`)!;
    select(editor, [0, 0], [0, 4]);
    button('Bold').click();
    expect(changes.at(-1)).toBe('<p><strong>word</strong></p>');
    expect(button('Bold').getAttribute('aria-pressed')).toBe('true');
    button('Heading').click();
    expect(changes.at(-1)).toBe('<h2><strong>word</strong></h2>');
    expect(button('Heading').getAttribute('aria-pressed')).toBe('true');
    button('Checklist').click();
    expect(changes.at(-1)).toBe('<ul data-type="checklist"><li><strong>word</strong></li></ul>');
    button('Divider').click();
    expect(editor.state().doc.blocks.some(block => block.type === 'rule')).toBe(true);
    button('Undo').click();
    expect(changes.at(-1)).toBe('<ul data-type="checklist"><li><strong>word</strong></li></ul>');
  });
  it('ticks a checklist item with its box', () => {
    const { editor, changes } = setup('<ul data-type="checklist"><li>task</li></ul>');
    const box = editor.surface.querySelector('.rt-check')!;
    expect(box.getAttribute('role')).toBe('checkbox');
    box.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(changes.at(-1)).toBe('<ul data-type="checklist"><li data-checked="true">task</li></ul>');
    expect(editor.surface.querySelector('.rt-check')!.getAttribute('aria-checked')).toBe('true');
  });
  it('numbers ordered lists per level', () => {
    const { surface } = setup('<ol><li>a<ol><li>b</li><li>c</li></ol></li><li>d</li></ol><p>x</p><ol><li>e</li></ol>');
    expect(Array.from(surface.querySelectorAll<HTMLElement>('.rt-li')).map(item => item.dataset.marker)).toEqual(['1.', '1.', '2.', '2.', '1.']);
  });
});

describe('phones', () => {
  it('puts the toolbar above the on-screen keyboard at phone width', () => {
    const media = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query.includes('coarse'), media: query, addEventListener() {}, removeEventListener() {} })) as any;
    const width = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { value: 375, configurable: true });
    Object.defineProperty(window, 'visualViewport', { value: Object.assign(new EventTarget(), { height: 400, offsetTop: 0 }), configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 700, configurable: true });
    try {
      const { editor } = setup();
      editor.surface.blur();
      editor.surface.focus();
      editor.surface.dispatchEvent(new FocusEvent('focus'));
      expect(editor.element.classList.contains('rt-phone')).toBe(true);
      expect(editor.element.style.getPropertyValue('--rt-keyboard')).toBe('300px');
      editor.surface.dispatchEvent(new FocusEvent('blur'));
      expect(editor.element.classList.contains('rt-phone')).toBe(false);
    } finally {
      window.matchMedia = media;
      Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
      Object.defineProperty(window, 'visualViewport', { value: undefined, configurable: true });
    }
  });
});

describe('outside changes', () => {
  it('a new value from outside replaces the document; its own echo does not', () => {
    const { editor, value, changes, surface } = setup('<p>a</p>');
    select(editor, [0, 1]);
    type(editor, 'b');
    expect(changes).toEqual(['<p>ab</p>']);
    value.set('<p>ab</p>');
    expect(editor.state().selection.head).toEqual(pos(0, 2));
    value.set('<h2>reloaded</h2>');
    expect(surface.textContent).toBe('reloaded');
    expect(editor.state().selection).toEqual(caret(pos(0, 8)));
  });
  it('counts text against the limit', () => {
    const { editor } = setup('<p>' + 'x'.repeat(90) + '</p>', { maxLength: 100 });
    const count = editor.element.querySelector<HTMLElement>('.rt-count')!;
    expect(count.hidden).toBe(false);
    expect(count.textContent).toBe('90 / 100');
  });
});

describe('shortcuts through the phone path', () => {
  it('a space a phone keyboard commits by itself still applies a Markdown shortcut', () => {
    const { editor, surface } = setup('<p>-</p>');
    select(editor, [0, 1]);
    const node = surface.querySelector('p')!.firstChild as Text;
    input(editor, 'insertText', { data: ' ', cancelable: false });
    node.data = '- ';
    window.getSelection()!.collapse(node, 2);
    surface.dispatchEvent(new InputEvent('input', { inputType: 'insertText', data: ' ' }));
    expect(editor.state().doc.blocks[0]).toMatchObject({ type: 'item', list: 'bullet' });
  });
});
