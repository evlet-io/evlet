// HTML to the rich text model and back. The browser twin of evlet/richtext.py: the same allowlist, the
// same canonical output (tests share frontend/src/evlet/tests/richtext-cases.json with the server).
//
// Parsing goes through DOMParser, which never runs scripts or loads images. Nothing parsed here is put
// into the page as markup: the editor and the read-only view build their DOM from the model.

import { lineBreak, MARKS, normalise, paragraph, plainText, sortMarks, text, tidy, type Block, type Doc, type Inline, type ListKind, type Mark } from './model';

const ALIASES: Record<string, string> = { b: 'strong', i: 'em', strike: 's', del: 's', ins: 'u', tt: 'code', kbd: 'code', samp: 'code',
  h1: 'h2', h4: 'h3', h5: 'h3', h6: 'h3', div: 'p' };
const DROPPED = new Set(['script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'template', 'noscript', 'noembed', 'noframes',
  'textarea', 'select', 'title', 'head', 'frame', 'frameset', 'applet', 'audio', 'video', 'canvas', 'xmp', 'plaintext', 'button', 'map',
  'picture', 'img', 'meta', 'link', 'base', 'input']);
const BOUNDARIES = new Set(['section', 'article', 'header', 'footer', 'nav', 'aside', 'main', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'td',
  'th', 'caption', 'dl', 'dt', 'dd', 'address', 'center', 'details', 'summary', 'figure', 'figcaption', 'fieldset', 'legend', 'form', 'body', 'html']);
const MARK_TAGS = new Set<string>(MARKS);
const MAX_DEPTH = 3;

/** A link's URL when it is http, https or mailto; null otherwise. Strips what a browser ignores first. */
export function safeHref(value: string | null | undefined): string | null {
  if (typeof value !== 'string') return null;
  // eslint-disable-next-line no-control-regex
  const url = value.replace(/[\t\n\r]/g, '').replace(/^[\x00-\x20]+|[\x00-\x20]+$/g, '');
  // eslint-disable-next-line no-control-regex
  if (!url || url.length > 2048 || /[\x00-\x20\x7f-\x9f]/.test(url)) return null;
  const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(url);
  if (!match) return null;
  const scheme = match[1].toLowerCase();
  if (scheme === 'mailto') return url.length > 7 ? url : null;
  if (scheme !== 'http' && scheme !== 'https') return null;
  try {
    const parsed = new URL(url);
    if (!parsed.hostname) return null;
  } catch { return null; }
  const port = /^[a-z]+:\/\/[^/?#]*:(\d+)/i.exec(url);
  if (port && Number(port[1]) > 65535) return null;
  return url;
}

/** Text a person typed or pasted as a link: adds https:// to a bare host, and refuses other schemes. */
export function linkFromInput(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (/^[^\s@/:]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) return safeHref(`mailto:${trimmed}`);
  return safeHref(/^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`);
}

interface Context {
  marks: readonly Mark[]; href: string | null; list: ListKind | null; depth: number;
  item: { list: ListKind; indent: number; checked: boolean } | null; quote: boolean; code: boolean; pre: boolean; firstInPre: boolean;
  paste: boolean;
  /** Reading the editing surface back: skip widgets and filler breaks. */
  raw: boolean;
}

interface Building { type: Block['type']; level?: 2 | 3; list?: ListKind; indent?: number; checked?: boolean; content: Inline[]; code: string }

const SPACES = /[ \t\n\r\f]+/g;

class Reader {
  blocks: Building[] = [];
  current: Building | null = null;

  end() { this.current = null; }
  start(block: Omit<Building, 'content' | 'code'>) { const made = { ...block, content: [], code: '' }; this.blocks.push(made); this.current = made; return made; }

  target(ctx: Context): Building {
    if (!this.current) {
      if (ctx.code) this.start({ type: 'code' });
      else if (ctx.item) this.start({ type: 'item', ...ctx.item });
      else this.start({ type: ctx.quote ? 'quote' : 'paragraph' });
    }
    return this.current!;
  }

  addText(value: string, ctx: Context) {
    // Whitespace between blocks (source formatting) is not a paragraph.
    if (!this.current && !ctx.pre && !ctx.code && !/[^ \t\n\r\f]/.test(value)) return;
    const block = this.target(ctx);
    if (block.type === 'code') {
      if (ctx.firstInPre && !block.code && value.startsWith('\n')) value = value.slice(1);
      block.code += value;
      return;
    }
    if (ctx.pre) {
      value.replace(/\r\n/g, '\n').split('\n').forEach((line, index) => {
        if (index) block.content.push(lineBreak());
        if (line) block.content.push(text(line.replace(/\t/g, ' '), ctx.marks, ctx.href));
      });
      return;
    }
    const collapsed = value.replace(SPACES, ' ');
    if (collapsed) block.content.push({ kind: 'text', text: collapsed, marks: ctx.marks, href: ctx.href });
  }

  lineBreak(ctx: Context) {
    const block = this.target(ctx);
    if (block.type === 'code') block.code += '\n';
    else block.content.push(lineBreak());
  }

  walk(node: Node, ctx: Context) {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) { this.addText((child as Text).data, ctx); ctx = { ...ctx, firstInPre: false }; continue; }
      if (child.nodeType !== 1) continue;
      const element = child as Element;
      const source = element.localName.toLowerCase();
      if (source !== 'pre') ctx = { ...ctx, firstInPre: false };
      if (DROPPED.has(source)) continue;
      if (ctx.raw && (element.getAttribute('contenteditable') === 'false' || element.hasAttribute('data-rt-filler'))) continue;
      if (ctx.paste && pasteSkips(element)) continue;
      const tag = ALIASES[source] ?? source;
      let inner = ctx;
      if (ctx.paste) inner = pasteStyle(element, source, inner);
      if (tag === 'br') { if (!(ctx.paste && element.classList.contains('Apple-interchange-newline'))) this.lineBreak(ctx); }
      else if (ctx.paste && source === 'b' && /font-weight:\s*(normal|[1-5]00)\b/.test(styleOf(element))) this.walk(element, inner);
      else if (tag === 'a') this.walk(element, { ...inner, href: safeHref(element.getAttribute('href')) ?? inner.href });
      else if (MARK_TAGS.has(tag)) this.walk(element, { ...inner, marks: sortMarks([...inner.marks, tag as Mark]) });
      else if (tag === 'hr') { if (!ctx.item && !ctx.code) { this.end(); this.start({ type: 'rule' }); this.end(); } }
      else if (tag === 'ul' || tag === 'ol') {
        this.end();
        const kind: ListKind = tag === 'ul' && element.getAttribute('data-type') === 'checklist' ? 'check' : tag === 'ul' ? 'bullet' : 'ordered';
        this.walk(element, { ...inner, list: kind, depth: ctx.depth + 1, item: null, code: false, pre: false });
        this.end();
      } else if (tag === 'li') {
        this.end();
        const list = ctx.list ?? 'bullet';
        let depth = Math.min(Math.max(ctx.depth, 1), MAX_DEPTH);
        // Google Docs writes flat lists with aria-level on each item.
        const level = ctx.paste ? Number(element.getAttribute('aria-level')) : NaN;
        if (Number.isInteger(level) && level >= 1) depth = Math.min(level, MAX_DEPTH);
        const item = { list, indent: depth - 1, checked: list === 'check' && element.getAttribute('data-checked') === 'true' };
        this.start({ type: 'item', ...item });
        this.walk(element, { ...inner, item, code: false, pre: false });
        this.end();
      } else if (ctx.paste && tag === 'p' && wordList(element)) {
        this.end();
        const info = wordList(element)!;
        const item = { list: info.list, indent: Math.min(info.level, MAX_DEPTH) - 1, checked: false };
        this.start({ type: 'item', ...item });
        this.walk(element, { ...inner, item });
        this.end();
      } else if (ctx.item) {
        if (['p', 'h2', 'h3', 'blockquote', 'pre'].includes(tag) || BOUNDARIES.has(source)) {
          if (this.current?.type === 'item' && this.current.content.length) this.current.content.push(lineBreak());
          this.walk(element, { ...inner, pre: ctx.pre || tag === 'pre' });
        } else this.walk(element, inner);
      } else if (ctx.code) {
        if ((tag === 'p' || BOUNDARIES.has(source)) && this.current?.code) this.current.code += '\n';
        this.walk(element, inner);
      } else if (tag === 'pre') {
        this.end(); this.walk(element, { ...inner, code: true, pre: true, firstInPre: true }); this.end();
      } else if (tag === 'blockquote') {
        this.end(); this.walk(element, { ...inner, quote: true }); this.end();
      } else if (tag === 'h2' || tag === 'h3') {
        this.end();
        this.start(ctx.quote ? { type: 'quote' } : { type: 'heading', level: tag === 'h2' ? 2 : 3 });
        this.walk(element, inner); this.end();
      } else if (tag === 'p') {
        this.end(); this.start({ type: ctx.quote ? 'quote' : 'paragraph' }); this.walk(element, inner); this.end();
      } else if (BOUNDARIES.has(source)) {
        this.end(); this.walk(element, inner); this.end();
      } else this.walk(element, inner);
    }
  }

  doc(): Doc {
    const blocks: Block[] = [];
    for (const block of this.blocks) {
      if (block.type === 'code') {
        const code = block.code.replace(/\r\n?/g, '\n').replace(/\0/g, '');
        if (code) blocks.push({ type: 'code', content: [text(code)] });
        continue;
      }
      if (block.type === 'rule') { blocks.push({ type: 'rule', content: [] }); continue; }
      const content = tidyWhitespace(block.content);
      if (block.type === 'heading') blocks.push({ type: 'heading', level: block.level!, content });
      else if (block.type === 'item') blocks.push({ type: 'item', list: block.list!, indent: block.indent!, checked: block.list === 'check' && Boolean(block.checked), content });
      else blocks.push({ type: block.type as 'paragraph' | 'quote', content });
    }
    return normalise({ blocks });
  }
}

/** Collapse whitespace across pieces as HTML shows it, and turn no-break spaces into spaces. */
function tidyWhitespace(pieces: readonly Inline[]): Inline[] {
  const out: Inline[] = [];
  let afterSpace = true;
  const trimLast = () => { const last = out[out.length - 1]; if (last?.kind === 'text') out[out.length - 1] = { ...last, text: last.text.replace(/ +$/, '') }; };
  for (const piece of pieces) {
    if (piece.kind === 'break') { trimLast(); out.push(piece); afterSpace = true; continue; }
    let value = piece.text;
    if (afterSpace) value = value.replace(/^ +/, '');
    if (!value) continue;
    afterSpace = value.endsWith(' ');
    out.push({ ...piece, text: value });
  }
  trimLast();
  return tidy(out.map(piece => piece.kind === 'text' ? { ...piece, text: piece.text.replace(/ /g, ' ').replace(/\0/g, '') } : piece));
}

// ---------- paste: Word, Outlook, Google Docs, web pages ----------

const styleOf = (element: Element) => (element.getAttribute('style') ?? '').toLowerCase();

function pasteSkips(element: Element): boolean {
  // Word's list markers (the bullet and its spacing) and conditional comments' leftovers.
  return /mso-list:\s*ignore/.test(styleOf(element)) || element.localName === 'o:p' && !element.textContent?.trim();
}

/** Marks a pasted element carries in its style (Google Docs, Word and web pages style spans). */
function pasteStyle(element: Element, source: string, ctx: Context): Context {
  const style = styleOf(element);
  if (!style) return ctx;
  let marks = [...ctx.marks];
  const weight = /font-weight:\s*([a-z0-9]+)/.exec(style)?.[1];
  // Google Docs wraps a whole paste in <b style="font-weight:normal">: not bold.
  if (weight && (weight === 'normal' || weight === '400' || Number(weight) < 600) && source === 'b') return ctx;
  if (weight === 'bold' || weight === 'bolder' || Number(weight) >= 600) marks.push('strong');
  if (/font-style:\s*italic/.test(style)) marks.push('em');
  if (/text-decoration[a-z-]*:[^;]*underline/.test(style)) marks.push('u');
  if (/text-decoration[a-z-]*:[^;]*line-through/.test(style)) marks.push('s');
  if (weight === 'normal' || weight === '400') marks = marks.filter(mark => mark !== 'strong');
  return { ...ctx, marks: sortMarks(marks) };
}

/** A Word or Outlook list paragraph: <p class="MsoListParagraph" style="mso-list:l0 level2 lfo1">. */
function wordList(element: Element): { list: ListKind; level: number } | null {
  const style = styleOf(element);
  const list = /mso-list:\s*l\d+\s+level(\d)/.exec(style);
  if (!list && !/^MsoListParagraph/.test(element.getAttribute('class') ?? '')) return null;
  const marker = Array.from(element.querySelectorAll('span')).find(span => /mso-list:\s*ignore/.test(styleOf(span)))?.textContent?.trim() ?? '';
  const ordered = /^([0-9]+|[a-z]{1,2}|[ivxlcdm]+)[.)]$/i.test(marker);
  return { list: ordered ? 'ordered' : 'bullet', level: list ? Number(list[1]) : 1 };
}

// ---------- reading ----------

const base: Context = { marks: [], href: null, list: null, depth: 0, item: null, quote: false, code: false, pre: false, firstInPre: false, paste: false, raw: false };

function parseWith(markup: string, paste: boolean): Doc {
  const body = new DOMParser().parseFromString(markup.replace(/\0/g, ''), 'text/html').body;
  const reader = new Reader();
  reader.walk(body, { ...base, paste });
  return reader.doc();
}

const plainCache = new Map<string, string>();

/** The plain text of stored HTML, for list cells and other one-line places. Cached by value. */
export function plainOf(markup: string): string {
  let found = plainCache.get(markup);
  if (found === undefined) {
    found = plainText(parseHTML(markup));
    if (plainCache.size >= 500) plainCache.delete(plainCache.keys().next().value!);
    plainCache.set(markup, found);
  }
  return found;
}

/** Stored HTML (or any HTML) as a document, cleaned to the schema. */
export function parseHTML(markup: string | null | undefined): Doc {
  return parseWith(markup ?? '', false);
}

/** Pasted HTML as blocks, cleaned to the schema: also reads Word list paragraphs and styled spans. */
export function parsePastedHTML(markup: string): Block[] {
  // Word wraps a paste in <!--StartFragment--> comments and conditional comments; DOMParser drops them.
  return trimEmpty(parseWith(markup, true).blocks);
}

const URL_PATTERN = /\b(?:https?:\/\/|www\.)[^\s<>"]+[^\s<>".,;:!?)\]'’]/gi;

/** Pasted plain text: a blank line separates paragraphs, a single newline is a line break, and web
 * addresses become links. */
export function parsePlainText(value: string): Block[] {
  const paragraphs = value.replace(/\r\n?/g, '\n').replace(/\0/g, '').replace(/\n+$/, '').split(/\n[ \t]*\n/);
  return trimEmpty(paragraphs.map(chunk => {
    const content: Inline[] = [];
    chunk.split('\n').forEach((line, index) => {
      if (index) content.push(lineBreak());
      let at = 0;
      for (const match of line.matchAll(URL_PATTERN)) {
        const href = safeHref(match[0].startsWith('www.') ? `https://${match[0]}` : match[0]);
        if (!href) continue;
        if (match.index! > at) content.push(text(line.slice(at, match.index)));
        content.push(text(match[0], [], href));
        at = match.index! + match[0].length;
      }
      if (at < line.length) content.push(text(line.slice(at)));
    });
    return paragraph(content);
  }));
}

function trimEmpty(blocks: readonly Block[]): Block[] {
  const empty = (block: Block) => block.type === 'paragraph' && !block.content.length;
  let start = 0, end = blocks.length;
  while (start < end && empty(blocks[start])) start++;
  while (end > start && empty(blocks[end - 1])) end--;
  return blocks.slice(start, end);
}

/** Read the editing surface back after the browser changed its structure (a phone keyboard split a
 * block, an IME left its own nodes): each top-level element is a block, spaces are kept as typed. */
export function readSurface(surface: Element): Doc {
  const blocks: Block[] = [];
  for (const element of Array.from(surface.children)) {
    const tag = element.tagName.toLowerCase();
    if (element.classList.contains('rt-rule') || tag === 'hr') { blocks.push({ type: 'rule', content: [] }); continue; }
    if (tag === 'pre') { const code = (element.textContent ?? '').replace(/\r\n?/g, '\n'); if (code) blocks.push({ type: 'code', content: [text(code)] }); continue; }
    const reader = new Reader();
    const item = { list: 'bullet' as ListKind, indent: 0, checked: false };
    reader.walk(element, { ...base, raw: true, pre: true, item });
    const content = tidy(reader.blocks.flatMap((block, index) => index ? [lineBreak(), ...block.content] : block.content));
    const data = (element as HTMLElement).dataset ?? {};
    if (tag === 'h2' || tag === 'h3') blocks.push({ type: 'heading', level: tag === 'h2' ? 2 : 3, content });
    else if (tag === 'blockquote') blocks.push({ type: 'quote', content });
    else if (element.classList.contains('rt-li') && (data.list === 'bullet' || data.list === 'ordered' || data.list === 'check'))
      blocks.push({ type: 'item', list: data.list, indent: Number(data.indent) || 0, checked: data.checked === 'true', content });
    else blocks.push({ type: 'paragraph', content });
  }
  return normalise({ blocks });
}

// ---------- writing ----------

/** A tree of allowed elements: the shared shape of the stored HTML and the read-only DOM. */
export type Tree = string | { tag: string; attrs?: Record<string, string>; children: Tree[] };

/** The blocks to write: trailing empty paragraphs are left out, as the server does. */
function written(doc: Doc): readonly Block[] {
  let end = doc.blocks.length;
  while (end > 0 && doc.blocks[end - 1].type === 'paragraph' && !doc.blocks[end - 1].content.length) end--;
  return doc.blocks.slice(0, end);
}

/** The document as a tree. With nbsp, spaces HTML would collapse become no-break spaces (storage). */
export function toTree(doc: Doc, options: { nbsp?: boolean } = {}): Tree[] {
  const blocks = written(doc), out: Tree[] = [];
  const inline = (content: readonly Inline[]) => group(options.nbsp ? spaced(content) : content, 0);
  for (let index = 0; index < blocks.length;) {
    const block = blocks[index];
    if (block.type === 'item') {
      let end = index;
      while (end < blocks.length && blocks[end].type === 'item') end++;
      out.push(...lists(blocks.slice(index, end) as Array<Extract<Block, { type: 'item' }>>, inline));
      index = end;
      continue;
    }
    if (block.type === 'quote') {
      let end = index;
      while (end < blocks.length && blocks[end].type === 'quote') end++;
      out.push({ tag: 'blockquote', children: blocks.slice(index, end).map(item => ({ tag: 'p', children: inline(item.content) })) });
      index = end;
      continue;
    }
    if (block.type === 'paragraph') out.push({ tag: 'p', children: inline(block.content) });
    else if (block.type === 'heading') out.push({ tag: `h${block.level}`, children: inline(block.content) });
    else if (block.type === 'code') out.push({ tag: 'pre', children: [{ tag: 'code', children: [block.content.map(piece => piece.kind === 'text' ? piece.text : '\n').join('')] }] });
    else if (block.type === 'rule') out.push({ tag: 'hr', children: [] });
    index++;
  }
  return out;
}

/** Spaces at the start or end of a line, or after another space, become no-break spaces. */
function spaced(content: readonly Inline[]): Inline[] {
  const chars: Array<{ index: number; char: string | null }> = [];
  content.forEach((piece, index) => {
    if (piece.kind === 'text') for (let at = 0; at < piece.text.length; at++) chars.push({ index, char: piece.text[at] });
    else chars.push({ index, char: null });
  });
  const encoded: string[][] = content.map(() => []);
  let previousPlain = true; // the start of a line
  chars.forEach((item, position) => {
    if (item.char === null) { previousPlain = true; return; }
    if (item.char === ' ') {
      const following = chars[position + 1]?.char ?? null;
      if (previousPlain || following === null) { encoded[item.index].push('\u00a0'); previousPlain = false; }
      else { encoded[item.index].push(' '); previousPlain = true; }
    } else { encoded[item.index].push(item.char); previousPlain = false; }
  });
  return content.map((piece, index) => piece.kind === 'text' ? { ...piece, text: encoded[index].join('') } : piece);
}

const keyOf = (piece: Inline, level: number): string | null => {
  if (piece.kind !== 'text') return null;
  if (level === 0) return piece.href;
  const mark = MARKS[level - 1];
  return piece.marks.includes(mark) ? mark : null;
};

/** Inline content as a tree (marks nested canonically), for the editing surface. */
export const inlineTree = (content: readonly Inline[]): Tree[] => group(content, 0);

/** Wrap runs that share the link, then each mark in MARKS order, so the nesting is canonical. */
function group(pieces: readonly Inline[], level: number): Tree[] {
  if (level > MARKS.length) return pieces.map(piece => piece.kind === 'text' ? piece.text : { tag: 'br', children: [] });
  const out: Tree[] = [];
  for (let index = 0; index < pieces.length;) {
    const key = keyOf(pieces[index], level);
    let end = index + 1;
    while (end < pieces.length && keyOf(pieces[end], level) === key && (key !== null || (pieces[end].kind === 'text' && pieces[index].kind === 'text'))) end++;
    if (pieces[index].kind !== 'text') out.push({ tag: 'br', children: [] });
    else if (key === null) out.push(...group(pieces.slice(index, end), level + 1));
    else if (level === 0) out.push({ tag: 'a', attrs: { href: key }, children: group(pieces.slice(index, end), 1) });
    else out.push({ tag: key, children: group(pieces.slice(index, end), level + 1) });
    index = end;
  }
  return out;
}

function lists(items: Array<Extract<Block, { type: 'item' }>>, inline: (content: readonly Inline[]) => Tree[]): Tree[] {
  const roots: Tree[] = [];
  // Open lists by depth, each with its kind and its last item (where a deeper list goes).
  const stack: Array<{ kind: ListKind; list: Exclude<Tree, string>; last: Exclude<Tree, string> | null }> = [];
  for (const item of items) {
    const depth = Math.min(item.indent, stack.length);
    while (stack.length > depth + 1) stack.pop();
    if (stack.length === depth + 1 && stack[depth].kind !== item.list) stack.pop();
    if (stack.length === depth) {
      const list: Exclude<Tree, string> = { tag: item.list === 'ordered' ? 'ol' : 'ul', attrs: item.list === 'check' ? { 'data-type': 'checklist' } : undefined, children: [] };
      if (depth === 0) roots.push(list); else stack[depth - 1].last!.children.push(list);
      stack.push({ kind: item.list, list, last: null });
    }
    const li: Exclude<Tree, string> = { tag: 'li', attrs: item.list === 'check' && item.checked ? { 'data-checked': 'true' } : undefined, children: inline(item.content) };
    stack[depth].list.children.push(li);
    stack[depth].last = li;
  }
  return roots;
}

const escapeText = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/ /g, '&nbsp;');
const escapeAttribute = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const VOID = new Set(['br', 'hr']);

export function treeToHTML(trees: readonly Tree[]): string {
  return trees.map(tree => {
    if (typeof tree === 'string') return escapeText(tree);
    const attrs = Object.entries(tree.attrs ?? {}).map(([name, value]) => ` ${name}="${escapeAttribute(value)}"`).join('');
    return VOID.has(tree.tag) ? `<${tree.tag}${attrs}>` : `<${tree.tag}${attrs}>${treeToHTML(tree.children)}</${tree.tag}>`;
  }).join('');
}

/** The canonical stored HTML of a document; '' when it is empty. */
export function toHTML(doc: Doc): string {
  return treeToHTML(toTree(doc, { nbsp: true }));
}

/** Stored HTML as canonical HTML (what the server will store). */
export const canonical = (markup: string) => toHTML(parseHTML(markup));

export { tidy };
