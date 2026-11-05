// Rich text model operations (richtext/model.ts, ops.ts) and undo grouping (history.ts): pure, no DOM.
import { describe, expect, it } from 'vitest';
import { History } from '../richtext/history';
import { parseHTML, toHTML } from '../richtext/html';
import { blockText, caret, isEmpty, marksAt, normalise, plainText, pos, slice, text, type Doc, type Selection } from '../richtext/model';
import {
  charStep, deleteBackward, deleteForward, indent, inputRule, insertBlocks, insertBreak, insertRule, insertText, joinBackward, selectionBlocks,
  setLink, splitBlock, toggleBlock, toggleChecked, toggleMark, wordStep, type State,
} from '../richtext/ops';

const doc = (markup: string): Doc => parseHTML(markup);
const at = (markup: string, block: number, offset: number): State => ({ doc: doc(markup), selection: caret(pos(block, offset)) });
const range = (markup: string, from: [number, number], to: [number, number]): State => ({ doc: doc(markup), selection: { anchor: pos(...from), head: pos(...to) } as Selection });
const out = (state: State) => toHTML(state.doc);
const plain = { marks: [], href: null };

describe('text', () => {
  it('inserts with the marks before the caret', () => {
    const state = at('<p><strong>bo</strong>ld</p>', 0, 2);
    expect(out(insertText(state, 'X', marksAt(state.doc.blocks[0].content, 2)))).toBe('<p><strong>boX</strong>ld</p>');
  });
  it('does not continue inline code or a link past its end', () => {
    const content = doc('<p><code>x</code><a href="https://a.io">y</a></p>').blocks[0].content;
    expect(marksAt(content, 1)).toEqual({ marks: [], href: null });
    expect(marksAt(content, 2)).toEqual({ marks: [], href: null });
  });
  it('replaces a selection across blocks and keeps the first block type', () => {
    const state = range('<h2>Title</h2><p>body text</p>', [0, 2], [1, 5]);
    const next = insertText(state, '!', plain);
    expect(out(next)).toBe('<h2>Ti!text</h2>');
    expect(next.selection.head).toEqual(pos(0, 3));
  });
  it('types newlines as breaks', () => {
    expect(out(insertText(at('<p>ab</p>', 0, 1), 'x\ny', plain))).toBe('<p>ax<br>yb</p>');
  });
  it('slices by UTF-16 offsets with breaks counting one', () => {
    const content = doc('<p>a<br>bc</p>').blocks[0].content;
    expect(slice(content, 1, 3)).toEqual([{ kind: 'break' }, text('b')]);
  });
  it('keeps spaces exactly, through storage', () => {
    const state = insertText(at('<p>a</p>', 0, 1), '  b ', plain);
    expect(out(state)).toBe('<p>a &nbsp;b&nbsp;</p>');
    expect(plainText(parseHTML(out(state)))).toBe('a  b ');
  });
});

describe('blocks', () => {
  it('splits a paragraph and a list item', () => {
    expect(out(splitBlock(at('<p>abcd</p>', 0, 2)))).toBe('<p>ab</p><p>cd</p>');
    expect(out(splitBlock(at('<ul data-type="checklist"><li data-checked="true">ab</li></ul>', 0, 1))))
      .toBe('<ul data-type="checklist"><li data-checked="true">a</li><li>b</li></ul>');
  });
  it('starts a paragraph after a heading', () => {
    const next = splitBlock(at('<h2>Title</h2>', 0, 5));
    expect(next.doc.blocks[1].type).toBe('paragraph');
  });
  it('leaves a list from an empty item, outdenting first', () => {
    const nested = at('<ul><li>a<ul><li></li></ul></li></ul>', 1, 0);
    expect(splitBlock(nested).doc.blocks[1]).toMatchObject({ type: 'item', indent: 0 });
    expect(splitBlock(splitBlock(nested)).doc.blocks[1].type).toBe('paragraph');
  });
  it('adds lines in a code block and leaves it on an empty last line', () => {
    const one = splitBlock(at('<pre><code>a</code></pre>', 0, 1));
    expect(blockText(one.doc.blocks[0])).toBe('a\n');
    const two = splitBlock(one);
    expect(out(two)).toBe('<pre><code>a</code></pre>');
    expect(two.doc.blocks[1].type).toBe('paragraph');
    expect(two.selection.head).toEqual(pos(1, 0));
  });
  it('Backspace at a block start outdents, unformats, then joins', () => {
    let state = at('<p>a</p><ul><li>b<ul><li>c</li></ul></li></ul>', 2, 0);
    state = joinBackward(state);
    expect(state.doc.blocks[2]).toMatchObject({ type: 'item', indent: 0 });
    state = joinBackward(state);
    expect(state.doc.blocks[2].type).toBe('paragraph');
    state = joinBackward(state);
    expect(out(state)).toBe('<p>a</p><ul><li>bc</li></ul>');
    expect(state.selection.head).toEqual(pos(1, 1));
  });
  it('Backspace after a divider removes it; Delete before one too', () => {
    expect(out(deleteBackward(at('<p>a</p><hr><p>b</p>', 2, 0)))).toBe('<p>a</p><p>b</p>');
    expect(out(deleteForward(at('<p>a</p><hr><p>b</p>', 0, 1)))).toBe('<p>a</p><p>b</p>');
  });
  it('toggles block types and turns several blocks into one code block', () => {
    expect(out(toggleBlock(at('<p>a</p>', 0, 0), { type: 'heading', level: 2 }))).toBe('<h2>a</h2>');
    expect(out(toggleBlock(at('<h2>a</h2>', 0, 0), { type: 'heading', level: 2 }))).toBe('<p>a</p>');
    expect(out(toggleBlock(range('<p>a</p><p>b<br>c</p>', [0, 0], [1, 1]), { type: 'item', list: 'ordered' }))).toBe('<ol><li>a</li><li>b<br>c</li></ol>');
    expect(out(toggleBlock(range('<p>a</p><p>b</p>', [0, 0], [1, 1]), { type: 'code' }))).toBe('<pre><code>a\nb</code></pre>');
    expect(out(toggleBlock(at('<pre><code>a\nb</code></pre>', 0, 0), { type: 'code' }))).toBe('<p>a<br>b</p>');
  });
  it('indents list items up to three levels, never skipping one', () => {
    let state = range('<ul><li>a</li><li>b</li></ul>', [1, 0], [1, 0]);
    state = indent(state, 1);
    expect(out(state)).toBe('<ul><li>a<ul><li>b</li></ul></li></ul>');
    state = indent(indent(state, 1), 1);
    expect(state.doc.blocks[1]).toMatchObject({ indent: 1 });
    expect(out(indent(at('<ul><li>a</li></ul>', 0, 0), 1))).toBe('<ul><li>a</li></ul>');
    expect(normalise({ blocks: [{ type: 'item', list: 'bullet', indent: 2, checked: false, content: [] }] }).blocks[0]).toMatchObject({ indent: 0 });
  });
  it('ticks a checklist item', () => {
    expect(out(toggleChecked(at('<ul data-type="checklist"><li>a</li></ul>', 0, 0), 0))).toBe('<ul data-type="checklist"><li data-checked="true">a</li></ul>');
  });
  it('inserts a divider with a paragraph after it', () => {
    const next = insertRule(at('<p>ab</p>', 0, 1));
    expect(out(next)).toBe('<p>a</p><hr><p>b</p>');
    expect(next.selection.head).toEqual(pos(2, 0));
  });
});

describe('deleting', () => {
  it('deletes a whole grapheme, emoji included', () => {
    expect(out(deleteBackward(at('<p>a👍🏽</p>', 0, 5)))).toBe('<p>a</p>');
    expect(charStep('e\u0301x', 0, true)).toBe(2);
    expect(out(deleteForward(at('<p>👍b</p>', 0, 0)))).toBe('<p>b</p>');
  });
  it('deletes words and lines', () => {
    expect(wordStep('one two  three', 14, false)).toBe(9);
    expect(out(deleteBackward(at('<p>one two</p>', 0, 7), 'word'))).toBe('<p>one&nbsp;</p>');
    expect(out(deleteForward(at('<p>one two</p>', 0, 0), 'word'))).toBe('<p>&nbsp;two</p>');
    expect(out(deleteBackward(at('<p>a<br>bc</p>', 0, 4), 'line'))).toBe('<p>a<br></p>');
  });
  it('a break deletes as one character', () => {
    expect(out(deleteBackward(at('<p>a<br>b</p>', 0, 2)))).toBe('<p>ab</p>');
    expect(out(insertBreak(at('<p>ab</p>', 0, 1)))).toBe('<p>a<br>b</p>');
  });
});

describe('marks and links', () => {
  it('toggles a mark over a range and back', () => {
    const state = range('<p>hello world</p>', [0, 0], [0, 5]);
    const bold = toggleMark(state, 'strong');
    expect(out(bold)).toBe('<p><strong>hello</strong> world</p>');
    expect(out(toggleMark(bold, 'strong'))).toBe('<p>hello world</p>');
  });
  it('adds a mark when only part of the range has it', () => {
    expect(out(toggleMark(range('<p><em>ab</em>cd</p>', [0, 0], [0, 4]), 'em'))).toBe('<p><em>abcd</em></p>');
  });
  it('leaves code blocks unformatted', () => {
    expect(out(toggleMark(range('<pre><code>x</code></pre>', [0, 0], [0, 1]), 'strong'))).toBe('<pre><code>x</code></pre>');
  });
  it('links a selection and removes the link around the caret', () => {
    const linked = setLink(range('<p>see docs</p>', [0, 4], [0, 8]), 'https://evlet.io');
    expect(out(linked)).toBe('<p>see <a href="https://evlet.io">docs</a></p>');
    expect(out(setLink({ doc: linked.doc, selection: caret(pos(0, 6)) }, null))).toBe('<p>see docs</p>');
  });
});

describe('input rules', () => {
  const rule = (typed: string) => {
    const state = insertText(at('<p></p>', 0, 0), typed, plain);
    const next = inputRule(state);
    return next ? out(next) || '(empty)' : null;
  };
  it('turns Markdown starts into blocks', () => {
    expect(inputRule(insertText(at('<p></p>', 0, 0), '# ', plain))!.doc.blocks[0]).toMatchObject({ type: 'heading', level: 2 });
    expect(inputRule(insertText(at('<p></p>', 0, 0), '### ', plain))!.doc.blocks[0]).toMatchObject({ type: 'heading', level: 3 });
    expect(inputRule(insertText(at('<p></p>', 0, 0), '- ', plain))!.doc.blocks[0]).toMatchObject({ type: 'item', list: 'bullet' });
    expect(inputRule(insertText(at('<p></p>', 0, 0), '1. ', plain))!.doc.blocks[0]).toMatchObject({ type: 'item', list: 'ordered' });
    expect(inputRule(insertText(at('<p></p>', 0, 0), '> ', plain))!.doc.blocks[0]).toMatchObject({ type: 'quote' });
    expect(inputRule(insertText(at('<p></p>', 0, 0), '[] ', plain))!.doc.blocks[0]).toMatchObject({ type: 'item', list: 'check', checked: false });
    expect(inputRule(insertText(at('<p></p>', 0, 0), '[x] ', plain))!.doc.blocks[0]).toMatchObject({ type: 'item', list: 'check', checked: true });
    expect(inputRule(insertText(at('<p></p>', 0, 0), '```', plain))!.doc.blocks[0]).toMatchObject({ type: 'code' });
    expect(rule('---')).toBe('<hr>');
    expect(rule('hello ')).toBeNull();
  });
  it('keeps the text after the caret', () => {
    const state = insertText(at('<p>Title</p>', 0, 0), '## ', plain);
    expect(out(inputRule(state)!)).toBe('<h2>Title</h2>');
  });
  it('only applies at the start of a paragraph', () => {
    expect(inputRule(insertText(at('<p>a</p>', 0, 1), '- ', plain))).toBeNull();
    expect(inputRule(insertText(at('<h2></h2>', 0, 0), '- ', plain))).toBeNull();
  });
  it('makes inline code from backticks', () => {
    const state = insertText(at('<p>run `npm test</p>', 0, 13), '`', plain);
    const next = inputRule(state)!;
    expect(out(next)).toBe('<p>run <code>npm test</code></p>');
    expect(next.selection.head).toEqual(pos(0, 12));
  });
});

describe('paste and copy', () => {
  it('merges the first and last pasted blocks into the text around the caret', () => {
    const pasted = doc('<p>one</p><h2>two</h2><p>three</p>').blocks;
    const next = insertBlocks(at('<p>[]</p>', 0, 1), pasted);
    expect(out(next)).toBe('<p>[one</p><h2>two</h2><p>three]</p>');
    expect(next.selection.head).toEqual(pos(2, 5));
  });
  it('pastes one block inline', () => {
    expect(out(insertBlocks(at('<p>ab</p>', 0, 1), doc('<p><strong>X</strong></p>').blocks))).toBe('<p>a<strong>X</strong>b</p>');
  });
  it('takes the pasted block type into an empty paragraph', () => {
    expect(out(insertBlocks(at('<p></p>', 0, 0), doc('<ul><li>a</li><li>b</li></ul>').blocks))).toBe('<ul><li>a</li><li>b</li></ul>');
  });
  it('copies a selection as blocks', () => {
    const blocks = selectionBlocks(range('<p>abc</p><h2>def</h2>', [0, 1], [1, 2]));
    expect(toHTML({ blocks })).toBe('<p>bc</p><h2>de</h2>');
  });
});

describe('documents', () => {
  it('knows when a document is empty', () => {
    expect(isEmpty(doc('<p></p><p><br></p>'))).toBe(true);
    expect(isEmpty(doc('<hr>'))).toBe(false);
    expect(toHTML(doc('<p></p>'))).toBe('');
  });
  it('round-trips every block and mark', () => {
    const markup = '<h2>Plan</h2><p><strong>b</strong> <em>i</em> <u>u</u> <s>s</s> <code>c</code> <a href="mailto:a@b.io">m</a><br>line</p>'
      + '<ul><li>a<ol><li>b<ul data-type="checklist"><li data-checked="true">c</li></ul></li></ol></li></ul>'
      + '<blockquote><p>q</p></blockquote><pre><code>x = 1\n  y</code></pre><hr><h3>End</h3>';
    expect(toHTML(parseHTML(markup))).toBe(markup);
  });
});

describe('history', () => {
  const s = (value: string): State => at(`<p>${value}</p>`, 0, value.length);
  it('groups a typing burst into one step', () => {
    const history = new History();
    history.record(s(''), 'type', 0);
    history.record(s('a'), 'type', 300);
    history.record(s('ab'), 'type', 900);
    expect(out(history.undo(s('abc'))!)).toBe('');
    expect(history.canUndo).toBe(false);
  });
  it('starts a new step after a pause, a different kind of change, or close()', () => {
    const history = new History();
    history.record(s(''), 'type', 0);
    history.record(s('a'), 'type', 1500);
    history.record(s('ab'), 'delete', 1600);
    history.close();
    history.record(s('a'), 'type', 1700);
    expect(out(history.undo(s('ax'))!)).toBe('<p>a</p>');
    expect(out(history.undo(s('a'))!)).toBe('<p>ab</p>');
    expect(out(history.undo(s('ab'))!)).toBe('<p>a</p>');
    expect(out(history.redo(s('a'))!)).toBe('<p>ab</p>');
  });
  it('keeps at most 200 steps', () => {
    const history = new History();
    for (let index = 0; index < 250; index++) history.record(s(String(index)), null, index);
    let count = 0;
    while (history.undo(s('x'))) count++;
    expect(count).toBe(200);
  });
});
