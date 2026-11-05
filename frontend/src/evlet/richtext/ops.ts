// Pure editing operations on the rich text model (model.ts). Each takes a state and returns a new one;
// nothing here touches the DOM, so every edit the editor makes is testable on its own.

import {
  BREAK, blockLength, blockText, caret, clampSelection, collapsed, compare, editable, insertAt, lineBreak, linkAt, MAX_INDENT,
  normalise, ordered, paragraph, pos, slice, sortMarks, text, tidy, withContent,
  type Block, type Doc, type Inline, type ListKind, type Mark, type Pos, type Selection,
} from './model';

export interface State { doc: Doc; selection: Selection }

const setBlocks = (doc: Doc, from: number, to: number, blocks: readonly Block[]): Doc =>
  normalise({ blocks: [...doc.blocks.slice(0, from), ...blocks, ...doc.blocks.slice(to)] });
const replaceBlock = (doc: Doc, index: number, ...blocks: Block[]) => setBlocks(doc, index, index + 1, blocks);

/** Code blocks hold plain text with newlines; other blocks hold breaks. */
function codeToLines(content: readonly Inline[]): Inline[] {
  const out: Inline[] = [];
  for (const piece of content) {
    if (piece.kind !== 'text') { out.push(piece); continue; }
    piece.text.split('\n').forEach((part, index) => { if (index) out.push(lineBreak()); if (part) out.push({ ...piece, text: part }); });
  }
  return tidy(out);
}

/** Replace the range between two positions with inline content. The first block keeps its type. */
export function replaceRange(doc: Doc, from: Pos, to: Pos, pieces: readonly Inline[]): { doc: Doc; end: Pos } {
  if (compare(from, to) > 0) [from, to] = [to, from];
  const first = doc.blocks[from.block], last = doc.blocks[to.block];
  const inserted = first.type === 'code' ? pieces.map(piece => piece.kind === 'break' ? text('\n') : { ...piece, marks: [], href: null }) : pieces;
  const tail = first.type === 'code' && last.type !== 'code' ? slice(last.content, to.offset).map(piece => piece.kind === 'break' ? text('\n') : piece)
    : first.type !== 'code' && last.type === 'code' ? codeToLines(slice(last.content, to.offset)) : slice(last.content, to.offset);
  const content = [...slice(first.content, 0, from.offset), ...inserted, ...tail];
  const length = inserted.reduce((sum, piece) => sum + (piece.kind === 'text' ? piece.text.length : 1), 0);
  return { doc: setBlocks(doc, from.block, to.block + 1, [withContent(first, content)]), end: pos(from.block, from.offset + length) };
}

/** Type text over the selection. */
export function insertText(state: State, value: string, style: { marks: readonly Mark[]; href: string | null }): State {
  const [from, to] = ordered(state.selection);
  const lines = value.replace(/\r\n?/g, '\n').split('\n');
  const pieces: Inline[] = [];
  lines.forEach((line, index) => { if (index) pieces.push(lineBreak()); if (line) pieces.push(text(line, style.marks, style.href)); });
  const { doc, end } = replaceRange(state.doc, from, to, pieces);
  return { doc, selection: caret(end) };
}

export function insertBreak(state: State): State {
  const [from, to] = ordered(state.selection);
  const { doc, end } = replaceRange(state.doc, from, to, [lineBreak()]);
  return { doc, selection: caret(end) };
}

export function deleteSelection(state: State): State {
  const [from, to] = ordered(state.selection);
  const { doc } = replaceRange(state.doc, from, to, []);
  return { doc, selection: caret(from) };
}

/** Enter: split the block at the caret. An empty list item outdents or leaves the list; Enter at the
 * end of a heading starts a paragraph; in a code block Enter adds a line, and Enter on an empty last
 * line leaves the code block. */
export function splitBlock(state: State): State {
  let current = collapsed(state.selection) ? state : deleteSelection(state);
  const at = current.selection.head, doc = current.doc, block = doc.blocks[at.block];
  const length = blockLength(block);
  if (block.type === 'code') {
    const value = blockText(block);
    if (at.offset === length && value.endsWith('\n')) {
      const kept = withContent(block, [text(value.slice(0, -1))]);
      return { doc: setBlocks(doc, at.block, at.block + 1, value.length > 1 ? [kept, paragraph()] : [paragraph()]), selection: caret(pos(at.block + (value.length > 1 ? 1 : 0), 0)) };
    }
    current = insertText(current, '\n', { marks: [], href: null });
    return current;
  }
  if (block.type === 'item' && length === 0) {
    if (block.indent > 0) return { doc: replaceBlock(doc, at.block, { ...block, indent: block.indent - 1 }), selection: caret(at) };
    return { doc: replaceBlock(doc, at.block, paragraph()), selection: caret(at) };
  }
  const left = withContent(block, slice(block.content, 0, at.offset));
  let right: Block = withContent(block, slice(block.content, at.offset));
  if (block.type === 'heading' && at.offset === length) right = paragraph();
  if (right.type === 'item') right = { ...right, checked: false };
  if (block.type === 'rule') return state;
  return { doc: setBlocks(doc, at.block, at.block + 1, [left, right]), selection: caret(pos(at.block + 1, 0)) };
}

/** Backspace at the start of a block: outdent, turn into a paragraph, then join with the block before. */
export function joinBackward(state: State): State {
  const at = state.selection.head, doc = state.doc, block = doc.blocks[at.block];
  if (block.type === 'item') {
    const next: Block = block.indent > 0 ? { ...block, indent: block.indent - 1 } : paragraph(block.content);
    return { doc: replaceBlock(doc, at.block, next), selection: caret(at) };
  }
  if (block.type !== 'paragraph') {
    return { doc: replaceBlock(doc, at.block, paragraph(block.type === 'code' ? codeToLines(block.content) : block.content)), selection: caret(at) };
  }
  if (at.block === 0) return state;
  const before = doc.blocks[at.block - 1];
  if (!editable(before)) return { doc: setBlocks(doc, at.block - 1, at.block, []), selection: caret(pos(at.block - 1, 0)) };
  const offset = blockLength(before);
  if (!block.content.length && before.type !== 'paragraph') {
    // An empty paragraph after a list or heading just goes.
    return { doc: setBlocks(doc, at.block, at.block + 1, []), selection: caret(pos(at.block - 1, offset)) };
  }
  const { doc: joined } = replaceRange(doc, pos(at.block - 1, offset), pos(at.block, 0), []);
  return { doc: joined, selection: caret(pos(at.block - 1, offset)) };
}

/** Delete at the end of a block: join the next block into this one. */
export function joinForward(state: State): State {
  const at = state.selection.head, doc = state.doc;
  if (at.block >= doc.blocks.length - 1) return state;
  const next = doc.blocks[at.block + 1];
  if (!editable(next)) return { doc: setBlocks(doc, at.block + 1, at.block + 2, []), selection: caret(at) };
  const { doc: joined } = replaceRange(doc, at, pos(at.block + 1, 0), []);
  return { doc: joined, selection: caret(at) };
}

export type Unit = 'char' | 'word' | 'line';

const segmenter = typeof Intl !== 'undefined' && 'Segmenter' in Intl ? new (Intl as any).Segmenter(undefined, { granularity: 'grapheme' }) : null;

/** Offset one character (grapheme cluster) before or after an offset in a block's text. */
export function charStep(value: string, offset: number, forward: boolean): number {
  if (segmenter) {
    let previous = 0;
    for (const { index, segment } of segmenter.segment(value) as Iterable<{ index: number; segment: string }>) {
      const end = index + segment.length;
      if (forward && index >= offset) return end;
      if (!forward && end >= offset) return index < offset ? index : previous;
      previous = index;
    }
    return forward ? value.length : previous;
  }
  if (forward) return Math.min(value.length, offset + (/[\uD800-\uDBFF]/.test(value[offset] ?? '') ? 2 : 1));
  return Math.max(0, offset - (/[\uDC00-\uDFFF]/.test(value[offset - 1] ?? '') ? 2 : 1));
}

const WORD = /[\p{L}\p{N}_'’]/u;

/** Offset of the word boundary before or after an offset, as Option or Ctrl Backspace would delete. */
export function wordStep(value: string, offset: number, forward: boolean): number {
  let at = offset;
  const char = (index: number) => value[index] ?? '';
  if (forward) {
    while (at < value.length && /\s/.test(char(at)) && char(at) !== BREAK) at++;
    if (char(at) === BREAK) return at === offset ? at + 1 : at;
    if (WORD.test(char(at))) while (at < value.length && WORD.test(char(at))) at++;
    else if (at < value.length) at++;
    return at;
  }
  while (at > 0 && /\s/.test(char(at - 1)) && char(at - 1) !== BREAK) at--;
  if (char(at - 1) === BREAK) return at === offset ? at - 1 : at;
  if (WORD.test(char(at - 1))) while (at > 0 && WORD.test(char(at - 1))) at--;
  else if (at > 0) at--;
  return at;
}

function lineStep(value: string, offset: number, forward: boolean): number {
  if (forward) { const next = value.indexOf(BREAK, offset); return next < 0 ? value.length : next === offset ? offset + 1 : next; }
  const previous = value.lastIndexOf(BREAK, offset - 1);
  return previous < 0 ? 0 : previous + 1 === offset ? previous : previous + 1;
}

export function deleteBackward(state: State, unit: Unit = 'char'): State {
  if (!collapsed(state.selection)) return deleteSelection(state);
  const at = state.selection.head;
  if (at.offset === 0) return joinBackward(state);
  const value = blockText(state.doc.blocks[at.block]);
  const from = unit === 'char' ? charStep(value, at.offset, false) : unit === 'word' ? wordStep(value, at.offset, false) : lineStep(value, at.offset, false);
  return deleteSelection({ doc: state.doc, selection: { anchor: pos(at.block, from), head: at } });
}

export function deleteForward(state: State, unit: Unit = 'char'): State {
  if (!collapsed(state.selection)) return deleteSelection(state);
  const at = state.selection.head, value = blockText(state.doc.blocks[at.block]);
  if (at.offset >= value.length) return joinForward(state);
  const to = unit === 'char' ? charStep(value, at.offset, true) : unit === 'word' ? wordStep(value, at.offset, true) : lineStep(value, at.offset, true);
  return deleteSelection({ doc: state.doc, selection: { anchor: at, head: pos(at.block, to) } });
}

// ---------- marks and links ----------

/** Apply fn to the text pieces inside a range (code blocks are left alone). */
function mapRange(doc: Doc, from: Pos, to: Pos, fn: (piece: Inline) => Inline): Doc {
  const blocks = doc.blocks.map((block, index) => {
    if (index < from.block || index > to.block || block.type === 'code' || block.type === 'rule') return block;
    const start = index === from.block ? from.offset : 0, end = index === to.block ? to.offset : blockLength(block);
    if (start >= end) return block;
    return withContent(block, [...slice(block.content, 0, start), ...slice(block.content, start, end).map(fn), ...slice(block.content, end)]);
  });
  return normalise({ blocks });
}

function textIn(doc: Doc, from: Pos, to: Pos) {
  const pieces: Inline[] = [];
  for (let index = from.block; index <= to.block; index++) {
    const block = doc.blocks[index];
    if (block.type === 'code' || block.type === 'rule') continue;
    pieces.push(...slice(block.content, index === from.block ? from.offset : 0, index === to.block ? to.offset : Infinity));
  }
  return pieces.filter(piece => piece.kind === 'text');
}

/** True when every character in the range has the mark. */
export function hasMark(state: State, mark: Mark): boolean {
  const [from, to] = ordered(state.selection);
  const pieces = textIn(state.doc, from, to);
  return pieces.length > 0 && pieces.every(piece => piece.kind === 'text' && piece.marks.includes(mark));
}

export function toggleMark(state: State, mark: Mark): State {
  const [from, to] = ordered(state.selection);
  const on = !hasMark(state, mark);
  const doc = mapRange(state.doc, from, to, piece => piece.kind !== 'text' ? piece
    : { ...piece, marks: sortMarks(on ? [...piece.marks, mark] : piece.marks.filter(item => item !== mark)) });
  return { doc, selection: clampSelection(doc, state.selection) };
}

/** Link the selection (or the link around the caret); href null removes the link. */
export function setLink(state: State, href: string | null): State {
  let [from, to] = ordered(state.selection);
  if (compare(from, to) === 0) {
    const found = linkAt(state.doc.blocks[from.block].content, from.offset);
    if (!found) return state;
    from = pos(from.block, found.from); to = pos(from.block, found.to);
  }
  const doc = mapRange(state.doc, from, to, piece => piece.kind === 'text' ? { ...piece, href } : piece);
  return { doc, selection: clampSelection(doc, state.selection) };
}

/** Insert linked text at the caret (a link typed into an empty selection). */
export function insertLink(state: State, label: string, href: string): State {
  const [from, to] = ordered(state.selection);
  const { doc, end } = replaceRange(state.doc, from, to, [text(label, [], href)]);
  return { doc, selection: caret(end) };
}

// ---------- blocks ----------

export type BlockSpec =
  | { type: 'paragraph' } | { type: 'heading'; level: 2 | 3 } | { type: 'item'; list: ListKind } | { type: 'quote' } | { type: 'code' };

export function blockMatches(block: Block, spec: BlockSpec): boolean {
  if (block.type !== spec.type) return false;
  if (spec.type === 'heading') return (block as { level: number }).level === spec.level;
  if (spec.type === 'item') return (block as { list: ListKind }).list === spec.list;
  return true;
}

/** The text blocks a selection touches. */
export function selectedBlocks(state: State): number[] {
  const [from, to] = ordered(state.selection);
  const out: number[] = [];
  for (let index = from.block; index <= to.block; index++) if (editable(state.doc.blocks[index])) out.push(index);
  return out;
}

/** Turn the selected blocks into a type; when they all are that type already, back into paragraphs. */
export function toggleBlock(state: State, spec: BlockSpec): State {
  const indexes = selectedBlocks(state);
  if (!indexes.length) return state;
  const all = indexes.every(index => blockMatches(state.doc.blocks[index], spec));
  const target: BlockSpec = all ? { type: 'paragraph' } : spec;
  if (target.type === 'code' && indexes.length > 1) {
    // Several blocks become one code block, a line each.
    const first = indexes[0], last = indexes[indexes.length - 1];
    const lines = indexes.map(index => blockText(state.doc.blocks[index]).split(BREAK).join('\n'));
    const code: Block = { type: 'code', content: [text(lines.join('\n'))] };
    const doc = setBlocks(state.doc, first, last + 1, [code]);
    return { doc, selection: caret(pos(first, blockLength(doc.blocks[first]))) };
  }
  const blocks = state.doc.blocks.map((block, index) => indexes.includes(index) ? convert(block, target) : block);
  const doc = normalise({ blocks });
  return { doc, selection: clampSelection(doc, state.selection) };
}

function convert(block: Block, spec: BlockSpec): Block {
  const content = block.type === 'code' && spec.type !== 'code' ? codeToLines(block.content) : block.content;
  switch (spec.type) {
    case 'paragraph': return { type: 'paragraph', content };
    case 'heading': return { type: 'heading', level: spec.level, content };
    case 'quote': return { type: 'quote', content };
    case 'code': return withContent({ type: 'code', content: [] }, content);
    case 'item': return { type: 'item', list: spec.list, indent: block.type === 'item' ? block.indent : 0, checked: block.type === 'item' && spec.list === 'check' ? block.checked : false, content };
  }
}

/** Tab and Shift+Tab: indent or outdent the selected list items. */
export function indent(state: State, delta: 1 | -1): State {
  const indexes = selectedBlocks(state).filter(index => state.doc.blocks[index].type === 'item');
  if (!indexes.length) return state;
  const blocks = state.doc.blocks.map((block, index) => indexes.includes(index) && block.type === 'item'
    ? { ...block, indent: Math.max(0, Math.min(MAX_INDENT, block.indent + delta)) } : block);
  const doc = normalise({ blocks });
  return { doc, selection: clampSelection(doc, state.selection) };
}

export function toggleChecked(state: State, index: number): State {
  const block = state.doc.blocks[index];
  if (block?.type !== 'item' || block.list !== 'check') return state;
  return { doc: replaceBlock(state.doc, index, { ...block, checked: !block.checked }), selection: state.selection };
}

/** A horizontal rule at the caret, with a paragraph after it for the caret. */
export function insertRule(state: State): State {
  const current = collapsed(state.selection) ? state : deleteSelection(state);
  const at = current.selection.head, block = current.doc.blocks[at.block];
  const rule: Block = { type: 'rule', content: [] };
  if (!block.content.length) {
    const doc = setBlocks(current.doc, at.block, at.block + 1, [rule, paragraph()]);
    return { doc, selection: caret(pos(at.block + 1, 0)) };
  }
  const left = withContent(block, slice(block.content, 0, at.offset)), right = withContent(block, slice(block.content, at.offset));
  const parts = [...(left.content.length ? [left] : []), rule, right.content.length ? right : paragraph()];
  const doc = setBlocks(current.doc, at.block, at.block + 1, parts);
  return { doc, selection: caret(pos(at.block + parts.length - 1, 0)) };
}

/** Insert blocks (a paste) at the selection: the first merges into the text before the caret, the last
 * into the text after it. */
export function insertBlocks(state: State, pasted: readonly Block[]): State {
  if (!pasted.length) return state;
  const current = collapsed(state.selection) ? state : deleteSelection(state);
  const at = current.selection.head, doc = current.doc, target = doc.blocks[at.block];
  if (pasted.length === 1 && editable(pasted[0])) {
    const { doc: next, end } = replaceRange(doc, at, at, target.type === 'code' || pasted[0].type !== 'code' ? pasted[0].content : codeToLines(pasted[0].content));
    return { doc: next, selection: caret(end) };
  }
  const left = withContent(target, slice(target.content, 0, at.offset)), right = withContent(target, slice(target.content, at.offset));
  const out: Block[] = [];
  const first = pasted[0], last = pasted[pasted.length - 1];
  if (!left.content.length && left.type === 'paragraph') out.push(first);
  else if (!editable(first)) out.push(left, first);
  else out.push(withContent(left, [...left.content, ...(left.type === 'code' ? first.content : first.type === 'code' ? codeToLines(first.content) : first.content)]));
  out.push(...pasted.slice(1, -1));
  let caretBlock: number, caretOffset: number;
  if (!editable(last)) {
    out.push(last, right.content.length ? right : paragraph());
    caretBlock = out.length - 1; caretOffset = 0;
  } else {
    caretOffset = blockLength(last);
    out.push(right.content.length ? withContent(last, [...last.content, ...(last.type === 'code' && right.type !== 'code' ? right.content : right.content)]) : last);
    caretBlock = out.length - 1;
  }
  const next = setBlocks(doc, at.block, at.block + 1, out);
  return { doc: next, selection: caret(pos(at.block + caretBlock, caretOffset)) };
}

/** The content of a selection, as blocks (for copy). */
export function selectionBlocks(state: State): Block[] {
  const [from, to] = ordered(state.selection);
  const out: Block[] = [];
  for (let index = from.block; index <= to.block; index++) {
    const block = state.doc.blocks[index];
    if (!editable(block)) { out.push(block); continue; }
    out.push(withContent(block, slice(block.content, index === from.block ? from.offset : 0, index === to.block ? to.offset : Infinity)));
  }
  return out;
}

// ---------- input rules ----------

const BLOCK_RULES: Array<[RegExp, BlockSpec | 'rule' | 'checked']> = [
  [/^#{1,2} $/, { type: 'heading', level: 2 }], [/^### $/, { type: 'heading', level: 3 }],
  [/^[-*+] $/, { type: 'item', list: 'bullet' }], [/^1[.)] $/, { type: 'item', list: 'ordered' }],
  [/^> $/, { type: 'quote' }], [/^\[ ?\] $/, { type: 'item', list: 'check' }], [/^\[[xX]\] $/, 'checked'],
  [/^```$/, { type: 'code' }], [/^(---|—-|___)$/, 'rule'],
];

/** Markdown-style shortcuts, after typing at the caret: `# `, `- `, `1. `, `> `, `[] `, three backticks,
 * `---`, and `code` between backticks. Null when no rule applies. */
export function inputRule(state: State): State | null {
  if (!collapsed(state.selection)) return null;
  const at = state.selection.head, block = state.doc.blocks[at.block];
  if (block.type === 'code' || block.type === 'rule') return null;
  const before = blockText(block).slice(0, at.offset);
  if (block.type === 'paragraph') {
    for (const [pattern, spec] of BLOCK_RULES) {
      if (!pattern.test(before)) continue;
      const rest = slice(block.content, at.offset);
      if (spec === 'rule') {
        const cleared = { doc: replaceBlock(state.doc, at.block, paragraph(rest)), selection: caret(pos(at.block, 0)) };
        return insertRule(cleared);
      }
      const next: Block = spec === 'checked' ? { type: 'item', list: 'check', indent: 0, checked: true, content: rest } : convert(paragraph(rest), spec);
      return { doc: replaceBlock(state.doc, at.block, next), selection: caret(pos(at.block, 0)) };
    }
  }
  const code = /`([^`]+)`$/.exec(before);
  if (code && !(slice(block.content, at.offset - code[0].length, at.offset).some(piece => piece.kind === 'text' && piece.marks.includes('code')))) {
    const start = at.offset - code[0].length;
    const inner = slice(block.content, start + 1, at.offset - 1).map(piece => piece.kind === 'text' ? { ...piece, marks: sortMarks([...piece.marks, 'code']), href: null } : piece);
    const content = [...slice(block.content, 0, start), ...inner, ...slice(block.content, at.offset)];
    const doc = replaceBlock(state.doc, at.block, withContent(block, content));
    return { doc, selection: caret(pos(at.block, at.offset - 2)) };
  }
  return null;
}

export { insertAt };
