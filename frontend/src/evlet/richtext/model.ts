// The rich text document: a flat list of blocks, each with inline content (docs/internal/rich-text.md).
//
// Documents are immutable: every operation returns new arrays and keeps the block objects it did not
// change, so the renderer can reuse their DOM. The server's evlet/richtext.py uses the same model.

export type Mark = 'strong' | 'em' | 'u' | 's' | 'code';
/** Mark order, outermost first (after the link). The HTML nests in this order. */
export const MARKS: readonly Mark[] = ['strong', 'em', 'u', 's', 'code'];

export interface TextPiece { kind: 'text'; text: string; marks: readonly Mark[]; href: string | null }
export interface BreakPiece { kind: 'break' }
export type Inline = TextPiece | BreakPiece;

export type ListKind = 'bullet' | 'ordered' | 'check';
export type Block =
  | { type: 'paragraph'; content: readonly Inline[] }
  | { type: 'heading'; level: 2 | 3; content: readonly Inline[] }
  | { type: 'item'; list: ListKind; indent: number; checked: boolean; content: readonly Inline[] }
  | { type: 'quote'; content: readonly Inline[] }
  | { type: 'code'; content: readonly Inline[] }
  | { type: 'rule'; content: readonly Inline[] };
export type BlockType = Block['type'];

export interface Doc { readonly blocks: readonly Block[] }
/** A place in the document: a block and an offset in its content (UTF-16 units; a break counts one). */
export interface Pos { block: number; offset: number }
export interface Selection { anchor: Pos; head: Pos }

export const MAX_INDENT = 2;
/** The character a break stands for in block text (for diffs and searches). */
export const BREAK = '';

export const text = (value: string, marks: readonly Mark[] = [], href: string | null = null): TextPiece => ({ kind: 'text', text: value, marks: sortMarks(marks), href });
export const lineBreak = (): BreakPiece => ({ kind: 'break' });
export const paragraph = (content: readonly Inline[] = []): Block => ({ type: 'paragraph', content: tidy(content) });
export const emptyDoc = (): Doc => ({ blocks: [paragraph()] });

export function sortMarks(marks: readonly Mark[]): Mark[] {
  return MARKS.filter(mark => marks.includes(mark));
}

export const pieceLength = (piece: Inline) => piece.kind === 'text' ? piece.text.length : 1;
export const blockLength = (block: Block) => block.content.reduce((sum, piece) => sum + pieceLength(piece), 0);
/** Block content as text, with BREAK for each break. */
export const blockText = (block: Block) => block.content.map(piece => piece.kind === 'text' ? piece.text : BREAK).join('');
/** Blocks that hold text the caret can be in (everything but a rule). */
export const editable = (block: Block) => block.type !== 'rule';

const sameMarks = (a: readonly Mark[], b: readonly Mark[]) => a.length === b.length && a.every((mark, index) => mark === b[index]);
export const sameStyle = (a: TextPiece, b: TextPiece) => a.href === b.href && sameMarks(a.marks, b.marks);

/** Merge neighbours with the same marks and drop empty text: one canonical form for equal content. */
export function tidy(content: readonly Inline[]): Inline[] {
  const out: Inline[] = [];
  for (const piece of content) {
    if (piece.kind === 'text') {
      if (!piece.text) continue;
      const last = out[out.length - 1];
      if (last && last.kind === 'text' && sameStyle(last, piece)) { out[out.length - 1] = { ...last, text: last.text + piece.text }; continue; }
    }
    out.push(piece);
  }
  return out;
}

/** The content between two offsets of a block. */
export function slice(content: readonly Inline[], from: number, to: number = Infinity): Inline[] {
  const out: Inline[] = [];
  let at = 0;
  for (const piece of content) {
    const start = at, end = at + pieceLength(piece);
    at = end;
    if (piece.kind === 'break') { if (start >= from && end <= to) out.push(piece); continue; }
    const a = Math.max(from, start), b = Math.min(to, end);
    if (a < b) out.push({ ...piece, text: piece.text.slice(a - start, b - start) });
  }
  return out;
}

/** Content with pieces inserted at an offset. */
export function insertAt(content: readonly Inline[], offset: number, pieces: readonly Inline[]): Inline[] {
  return tidy([...slice(content, 0, offset), ...pieces, ...slice(content, offset)]);
}

/** Marks to give typed text at an offset: those of the text before it (or after it, at the start of a
 * line). Inline code and links do not continue past their end, nor into the start of a line. */
export function marksAt(content: readonly Inline[], offset: number): { marks: Mark[]; href: string | null } {
  let at = 0;
  for (const piece of content) {
    const start = at, end = at + pieceLength(piece);
    at = end;
    if (piece.kind === 'text' && offset > start && offset <= end) {
      const atEnd = offset === end;
      return { marks: atEnd ? piece.marks.filter(mark => mark !== 'code') : [...piece.marks], href: atEnd ? null : piece.href };
    }
    if (start === offset && piece.kind === 'text') return { marks: piece.marks.filter(mark => mark !== 'code'), href: null };
    if (start >= offset) break;
  }
  return { marks: [], href: null };
}

/** The link around an offset (inside it, or at either end), with its range in the block. */
export function linkAt(content: readonly Inline[], offset: number): { href: string; from: number; to: number } | null {
  const runs: Array<{ href: string; from: number; to: number }> = [];
  let at = 0, run: { href: string; from: number; to: number } | null = null;
  for (const piece of content) {
    const start = at, end = at + pieceLength(piece);
    at = end;
    const href = piece.kind === 'text' ? piece.href : null;
    if (href && run && run.href === href && run.to === start) run.to = end;
    else { run = href ? { href, from: start, to: end } : null; if (run) runs.push(run); }
  }
  return runs.find(item => offset > item.from && offset < item.to) ?? runs.find(item => offset >= item.from && offset <= item.to) ?? null;
}

export function withContent(block: Block, content: readonly Inline[]): Block {
  if (block.type === 'code') return { ...block, content: tidy(content.map(piece => piece.kind === 'text' ? { ...piece, marks: [], href: null } : text('\n'))) };
  if (block.type === 'rule') return block;
  return { ...block, content: tidy(content) } as Block;
}

// ---------- positions ----------

export const pos = (block: number, offset: number): Pos => ({ block, offset });
export const caret = (at: Pos): Selection => ({ anchor: at, head: at });
export const compare = (a: Pos, b: Pos) => a.block - b.block || a.offset - b.offset;
export const samePos = (a: Pos, b: Pos) => a.block === b.block && a.offset === b.offset;
export const collapsed = (selection: Selection) => samePos(selection.anchor, selection.head);
export function ordered(selection: Selection): [Pos, Pos] {
  return compare(selection.anchor, selection.head) <= 0 ? [selection.anchor, selection.head] : [selection.head, selection.anchor];
}

/** A position inside the document: clamped, and moved off a rule onto the nearest text block. */
export function clampPos(doc: Doc, at: Pos): Pos {
  const last = doc.blocks.length - 1;
  let block = Math.max(0, Math.min(last, at.block));
  if (!editable(doc.blocks[block])) {
    const after = doc.blocks.findIndex((item, index) => index > block && editable(item));
    const before = doc.blocks.map((item, index) => editable(item) ? index : -1).filter(index => index >= 0 && index < block).pop();
    if (after >= 0) return pos(after, 0);
    if (before !== undefined) return pos(before, blockLength(doc.blocks[before]));
  }
  block = Math.max(0, Math.min(last, block));
  return pos(block, Math.max(0, Math.min(blockLength(doc.blocks[block]), at.offset)));
}

export function clampSelection(doc: Doc, selection: Selection): Selection {
  return { anchor: clampPos(doc, selection.anchor), head: clampPos(doc, selection.head) };
}

export const docStart = (): Pos => pos(0, 0);
export const docEnd = (doc: Doc): Pos => pos(doc.blocks.length - 1, blockLength(doc.blocks[doc.blocks.length - 1]));

// ---------- normalising a document ----------

/** One canonical document: at least one block, list indents that never skip a level, a text block
 * after a final rule, tidy content. Unchanged blocks keep their identity. */
export function normalise(doc: Doc): Doc {
  const blocks: Block[] = [];
  let previousIndent = -1;
  for (const block of doc.blocks) {
    let next = block;
    if (next.type === 'item') {
      const indent = Math.max(0, Math.min(next.indent, previousIndent + 1, MAX_INDENT));
      const checked = next.list === 'check' ? next.checked : false;
      if (indent !== next.indent || checked !== next.checked) next = { ...next, indent, checked };
      previousIndent = indent;
    } else previousIndent = -1;
    blocks.push(next);
  }
  if (!blocks.length || blocks[blocks.length - 1].type === 'rule') blocks.push(paragraph());
  return { blocks };
}

/** True when the document has nothing to store: no text, no rule. */
export function isEmpty(doc: Doc): boolean {
  return doc.blocks.every(block => block.type !== 'rule' && block.content.every(piece => piece.kind === 'break'));
}

/** Plain text: a newline between blocks and for each break. What search and list cells show. */
export function plainText(doc: Doc): string {
  return doc.blocks.filter(block => block.type !== 'rule').map(block => blockText(block).split(BREAK).join('\n')).join('\n').replace(/^\n+|\n+$/g, '');
}

/** Plain-text length, as the server counts it for max_length. */
export const textLength = (doc: Doc) => plainText(doc).length;
