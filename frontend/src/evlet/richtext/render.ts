// The rich text model as DOM, and DOM points as model positions and back.
//
// The editing surface renders one element per block (flat, like the model); list markers and numbers
// are computed here. The read-only view (renderView) builds proper nested lists for screen readers.
// Everything is built with createElement from the model: stored HTML is never put into the page.

import { inlineTree, toTree, type Tree } from './html';
import { Icon } from '../ui/base';
import { BREAK, blockText, type Block, type Doc, type Pos } from './model';

export interface RenderOptions {
  /** The address of an image attachment; without it, images show their alt text. */
  imageUrl?: (attachment: string) => string;
  /** Show the size, alt text and remove tools on images (the editing surface). */
  tools?: boolean;
}

const SIZE_LABELS: Array<[string, string]> = [['rt-sm', 'Small'], ['rt-md', 'Medium'], ['rt-full', 'Full width']];

/** An image element for an attachment id. Its address is built from the id only; a failed load says so. */
function imageElement(attachment: string, alt: string, options: RenderOptions): HTMLElement {
  if (!options.imageUrl || !/^[1-9][0-9]{0,18}$/.test(attachment)) {
    const missing = document.createElement('span');
    missing.className = 'rt-img-alt';
    missing.textContent = alt || 'Image';
    return missing;
  }
  const img = document.createElement('img');
  img.alt = alt;
  img.loading = 'lazy';
  img.decoding = 'async';
  img.addEventListener('error', () => {
    img.closest('figure')?.classList.add('rt-missing');
    img.alt = alt ? `${alt} (image no longer available)` : 'Image no longer available';
  });
  img.src = options.imageUrl(attachment);
  return img;
}

function renderImage(block: Extract<Block, { type: 'image' }>, options: RenderOptions): HTMLElement {
  const figure = document.createElement('figure');
  figure.className = `rt-b rt-img ${block.size}`;
  figure.contentEditable = 'false';
  if (block.attachment.startsWith('pending:')) {
    figure.dataset.upload = block.attachment.slice(8);
    figure.classList.add('rt-pending');
    const label = document.createElement('span');
    label.className = 'rt-img-alt';
    label.textContent = 'Uploading image';
    const bar = document.createElement('progress');
    bar.max = 1; bar.value = 0;
    bar.setAttribute('aria-label', 'Uploading image');
    figure.append(label, bar);
    return figure;
  }
  figure.append(imageElement(block.attachment, block.alt, options));
  if (block.caption) { const caption = document.createElement('figcaption'); caption.textContent = block.caption; figure.append(caption); }
  if (options.tools) {
    const tools = document.createElement('div');
    tools.className = 'rt-img-tools';
    tools.setAttribute('role', 'group');
    tools.setAttribute('aria-label', 'Image');
    for (const [size, label] of SIZE_LABELS) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'rt-img-tool'; button.textContent = label;
      button.dataset.imageAction = size;
      button.setAttribute('aria-pressed', String(block.size === size));
      tools.append(button);
    }
    for (const [action, label, icon] of [['alt', 'Alt text and caption', 'pencil'], ['remove', 'Remove image', 'trash-2']]) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'rt-img-tool'; button.dataset.imageAction = action;
      button.setAttribute('aria-label', label); button.title = label;
      button.append(Icon(icon, { size: 'sm' }));
      tools.append(button);
    }
    figure.append(tools);
  }
  return figure;
}

const BULLETS = ['•', '◦', '▪'];
export const FILLER = 'data-rt-filler';

function build(tree: Tree, options: { links: boolean; imageUrl?: (attachment: string) => string }): Node {
  if (typeof tree === 'string') return document.createTextNode(tree);
  if (tree.tag === 'img') return imageElement(tree.attrs?.['data-attachment'] ?? '', tree.attrs?.alt ?? '', options);
  const tag = tree.tag === 'a' && !options.links ? 'span' : tree.tag;
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(tree.attrs ?? {})) {
    if (name === 'href' && !options.links) { element.className = 'rt-a'; continue; }
    element.setAttribute(name, value);
  }
  if (tag === 'a') { element.setAttribute('rel', 'noopener noreferrer nofollow'); element.setAttribute('target', '_blank'); }
  for (const child of tree.children) element.append(build(child, options));
  return element;
}

const filler = () => { const br = document.createElement('br'); br.setAttribute(FILLER, ''); return br; };

/** One block's element on the editing surface. */
export function renderBlock(block: Block, options: RenderOptions = {}): HTMLElement {
  let element: HTMLElement;
  switch (block.type) {
    case 'image': return renderImage(block, options);
    case 'heading': element = document.createElement(`h${block.level}`); break;
    case 'quote': element = document.createElement('blockquote'); break;
    case 'code': {
      element = document.createElement('pre');
      const code = document.createElement('code');
      const value = blockText(block);
      code.textContent = value;
      element.append(code);
      if (!value || value.endsWith('\n')) code.append(filler());
      element.className = 'rt-b rt-code';
      return element;
    }
    case 'rule': {
      element = document.createElement('div');
      element.className = 'rt-b rt-rule';
      element.contentEditable = 'false';
      element.append(document.createElement('hr'));
      return element;
    }
    case 'item': {
      element = document.createElement('div');
      element.dataset.list = block.list;
      element.dataset.indent = String(block.indent);
      if (block.list === 'check') {
        element.dataset.checked = String(block.checked);
        const box = document.createElement('span');
        box.className = 'rt-check';
        box.contentEditable = 'false';
        box.setAttribute('role', 'checkbox');
        box.setAttribute('aria-checked', String(block.checked));
        box.setAttribute('aria-label', block.checked ? 'Done' : 'Not done');
        element.append(box);
      }
      break;
    }
    default: element = document.createElement('p');
  }
  element.classList.add('rt-b');
  if (block.type === 'item') element.classList.add('rt-li');
  for (const tree of inlineTree(block.content)) element.append(build(tree, { links: true }));
  const last = block.content[block.content.length - 1];
  if (!last || last.kind === 'break') element.append(filler());
  return element;
}

/** Render a document into the surface, reusing the element of every block that did not change
 * (blocks are immutable, so an unchanged block is the same object). Returns the elements in order. */
export function renderSurface(surface: HTMLElement, doc: Doc, previous: ReadonlyMap<Block, HTMLElement>, options: RenderOptions = {}): HTMLElement[] {
  const elements = doc.blocks.map(block => previous.get(block) ?? renderBlock(block, { ...options, tools: true }));
  const used = new Set(elements);
  for (const child of Array.from(surface.childNodes)) if (!used.has(child as HTMLElement)) child.remove();
  let cursor: ChildNode | null = surface.firstChild;
  for (const element of elements) {
    if (element === cursor) { cursor = cursor.nextSibling; continue; }
    surface.insertBefore(element, cursor);
  }
  markers(doc, elements);
  return elements;
}

/** List markers: bullets by depth, numbers counted per list and depth. */
function markers(doc: Doc, elements: HTMLElement[]) {
  const counts: Array<{ list: string; n: number } | undefined> = [];
  doc.blocks.forEach((block, index) => {
    if (block.type !== 'item') { counts.length = 0; return; }
    counts.length = block.indent + 1;
    const current = counts[block.indent];
    const n = current && current.list === block.list ? current.n + 1 : 1;
    counts[block.indent] = { list: block.list, n };
    const marker = block.list === 'ordered' ? `${n}.` : block.list === 'bullet' ? BULLETS[block.indent] ?? '•' : '';
    if (elements[index].dataset.marker !== marker) elements[index].dataset.marker = marker;
  });
}

/** The read-only view: semantic HTML (nested lists, real links), built from the model. */
export function renderView(doc: Doc, options: { links?: boolean; imageUrl?: (attachment: string) => string } = {}): DocumentFragment {
  const fragment = document.createDocumentFragment();
  // Without links (inside a button that starts editing), a link reads as styled text.
  for (const tree of toTree(doc)) fragment.append(build(tree, { links: options.links !== false, imageUrl: options.imageUrl }));
  for (const item of Array.from(fragment.querySelectorAll('ul[data-type="checklist"] > li'))) {
    const box = document.createElement('span');
    box.className = 'rt-check';
    box.setAttribute('role', 'img');
    box.setAttribute('aria-label', item.getAttribute('data-checked') === 'true' ? 'Done' : 'Not done');
    item.prepend(box);
  }
  for (const paragraph of Array.from(fragment.querySelectorAll('p, li, h2, h3'))) if (!paragraph.firstChild || paragraph.lastChild?.nodeName === 'BR') paragraph.append(document.createElement('br'));
  return fragment;
}

// ---------- positions ----------

const skipped = (node: Node) => node.nodeType === 1 && (node as Element).getAttribute('contenteditable') === 'false';
const isFiller = (node: Node) => node.nodeType === 1 && (node as Element).hasAttribute(FILLER);

/** The text of a rendered block as the model counts it: BREAK for each break, nothing for widgets. */
export function domText(element: Element, code = false): string {
  let out = '';
  const visit = (node: Node) => {
    if (node.nodeType === 3) { out += (node as Text).data; return; }
    if (node.nodeType !== 1 || skipped(node)) return;
    if ((node as Element).tagName === 'BR') { if (!isFiller(node)) out += code ? '\n' : BREAK; return; }
    node.childNodes.forEach(visit);
  };
  element.childNodes.forEach(visit);
  return out;
}

/** The model offset of a DOM point inside a block element. */
export function offsetIn(element: Element, node: Node, offset: number): number {
  let count = 0, result = -1;
  const visit = (current: Node): boolean => {
    if (current === node && current.nodeType === 3) { result = count + Math.min(offset, (current as Text).data.length); return true; }
    if (current.nodeType === 3) { count += (current as Text).data.length; return false; }
    if (current.nodeType !== 1) return false;
    if (skipped(current) && current !== node) return current.contains(node) ? (result = count, true) : false;
    if ((current as Element).tagName === 'BR') { if (current === node) { result = count; return true; } if (!isFiller(current)) count += 1; return false; }
    if (current === node) {
      const children = Array.from(current.childNodes);
      for (let index = 0; index < Math.min(offset, children.length); index++) visit(children[index]);
      result = count;
      return true;
    }
    for (const child of Array.from(current.childNodes)) if (visit(child)) return true;
    return false;
  };
  if (element === node) {
    const children = Array.from(element.childNodes);
    for (let index = 0; index < Math.min(offset, children.length); index++) visit(children[index]);
    return count;
  }
  for (const child of Array.from(element.childNodes)) if (visit(child)) break;
  return result >= 0 ? result : count;
}

/** A DOM point on the surface as a model position; null when the point is outside it. */
export function pointToPos(surface: HTMLElement, node: Node | null, offset: number): Pos | null {
  if (!node || !surface.contains(node)) return null;
  const blocks = Array.from(surface.children);
  if (!blocks.length) return null;
  if (node === surface) {
    const index = Math.min(offset, blocks.length - 1);
    return offset >= blocks.length ? { block: index, offset: domText(blocks[index]).length } : { block: index, offset: 0 };
  }
  let element: Node = node;
  while (element.parentNode && element.parentNode !== surface) element = element.parentNode;
  const index = blocks.indexOf(element as Element);
  if (index < 0) return null;
  return { block: index, offset: offsetIn(blocks[index], node, offset) };
}

/** A model position as a DOM point inside a block element. */
export function posToPoint(element: Element, offset: number): [Node, number] {
  let count = 0;
  let found: [Node, number] | null = null;
  const visit = (node: Node): boolean => {
    if (node.nodeType === 3) {
      const length = (node as Text).data.length;
      if (offset <= count + length) { found = [node, offset - count]; return true; }
      count += length;
      return false;
    }
    if (node.nodeType !== 1 || skipped(node)) return false;
    if ((node as Element).tagName === 'BR') {
      if (offset <= count || isFiller(node)) { found = [node.parentNode!, Array.from(node.parentNode!.childNodes).indexOf(node as ChildNode)]; return true; }
      count += 1;
      return false;
    }
    for (const child of Array.from(node.childNodes)) if (visit(child)) return true;
    return false;
  };
  for (const child of Array.from(element.childNodes)) if (visit(child)) break;
  if (found) return found;
  const target = element.tagName === 'PRE' && element.firstChild ? element.firstChild : element;
  return [target, target.childNodes.length];
}
