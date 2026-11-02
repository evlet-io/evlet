// Evlet DOM layer: templates parsed once and cloned per use, one effect per dynamic binding.
//
//   const row = (item: () => Line) => html`
//     <tr class:selected=${() => item().selected}>
//       <td>${() => item().name}</td>
//       <td><button on:click=${() => remove(item())}>Remove</button></td>
//     </tr>`;
//
// Values are never parsed as HTML: text goes through Text nodes, attributes through
// setAttribute, and URLs through safeUrl(). A function, cell or derive in a hole is
// reactive; anything else is set once. Dynamic content always sits between two comment
// markers, so replacing, moving or removing it takes everything it added later too.

import { cell, effect, getOwner, isReadable, onCleanup, runWithOwner, scope, untrack, type Cell, type Readable, derive } from './reactive';

export type Child = Node | string | number | bigint | boolean | null | undefined | Child[] | (() => Child) | Readable<Child>;
type Value<T> = T | (() => T) | Readable<T>;

/** The current value of a plain value, accessor function or cell/derive. */
export function read<T>(value: Value<T>): T {
  if (typeof value === 'function') return (value as () => T)();
  if (isReadable(value)) return value.get() as T;
  return value as T;
}
const dynamic = (value: unknown) => typeof value === 'function' || isReadable(value);

// ---------- URLs ----------

const ALLOWED_SCHEMES = new Set(['http', 'https', 'mailto', 'tel']);
const BLOCKED = 'about:blank';

/**
 * A URL that is safe to put in href, src and similar: http, https, mailto and tel URLs, and
 * relative or fragment URLs. Anything else becomes about:blank. The check strips what the
 * browser's URL parser ignores (leading and trailing controls and spaces, tabs and newlines
 * anywhere), so "java\tscript:" and "\x01javascript:" are caught.
 */
export function safeUrl(value: unknown): string {
  const text = String(value).replace(/^[\u0000- ]+|[\u0000- ]+$/g, '').replace(/[\t\n\r]/g, '');
  // A colon before any / ? # starts a scheme (or something the parser could read as one).
  const colon = text.search(/[:/?#]/);
  if (colon >= 0 && text[colon] === ':' && !ALLOWED_SCHEMES.has(text.slice(0, colon).toLowerCase())) return BLOCKED;
  return text;
}

// Attributes and properties that the browser loads or navigates to, by lowercase name.
const URL_SINKS = new Set(['href', 'src', 'action', 'formaction', 'xlink:href', 'poster', 'background', 'data', 'cite', 'codebase', 'longdesc', 'manifest']);
// Values that are parsed as HTML or as a document: never filled from data.
const HTML_SINKS = new Set(['innerhtml', 'outerhtml', 'srcdoc']);
// SVG animation elements can set href from their values.
const ANIMATIONS = new Set(['animate', 'set', 'animatemotion', 'animatetransform']);

function cleanUrlValue(name: string, value: string): string {
  if (name === 'srcset') {
    return value.split(',').map(candidate => candidate.trim()).filter(candidate => {
      const url = candidate.split(/\s+/)[0];
      return url && safeUrl(url) !== BLOCKED;
    }).join(', ');
  }
  if (name === 'ping') return value.split(/\s+/).filter(url => url && safeUrl(url) !== BLOCKED).join(' ');
  return URL_SINKS.has(name) ? safeUrl(value) : value;
}

// ---------- template compilation ----------

interface AttributeHole { name: string; tag: string; statics: string[]; indexes: number[] }
interface Part { path: number[]; node?: number; attribute?: AttributeHole }
interface Compiled { template: HTMLTemplateElement; parts: Part[] }

const NODE_MARK = 'evlet:';
const ATTRIBUTE_MARK = 'evlet-a';
const cache = new WeakMap<TemplateStringsArray, Compiled>();
const SPACE = /\s/;
// Elements whose content the parser reads as raw text: a comment marker cannot sit there.
const RAW_TEXT = new Set(['textarea', 'title', 'style', 'script', 'xmp', 'iframe', 'noembed', 'noframes', 'noscript', 'plaintext']);

// Checks on where a value may go, made once when a template is compiled.
function checkAttribute(hole: AttributeHole) {
  const name = hole.name.toLowerCase(), tag = hole.tag;
  const colon = name.indexOf(':');
  const prefix = colon > 0 ? name.slice(0, colon) : '';
  if (prefix === 'on' || prefix === 'class' || prefix === 'style' || name === 'ref') return;
  const key = prefix === 'prop' ? name.slice(5) : name;
  if (HTML_SINKS.has(key)) throw new Error(`${hole.name} is not allowed; data is never parsed as HTML`);
  if (!prefix && name.startsWith('on')) throw new Error(`Use on:${name.slice(2)} for event handlers, not the ${hole.name} attribute`);
  if (!prefix && name === 'style') throw new Error('Values cannot go into the style attribute; use style:property=${value} for each property');
  if (tag === 'meta' && (key === 'content' || key === 'http-equiv')) throw new Error(`Values cannot go into <meta ${hole.name}>: it can redirect the page`);
  if (ANIMATIONS.has(tag)) throw new Error(`Values cannot go into <${tag}> attributes: SVG animations can set links`);
}

// A small scanner that finds where each hole sits: as a child, or inside an attribute value.
// Child holes become <!--evlet:N--> comments; dynamic attributes become evlet-aK markers.
// Attribute names keep their written case (prop:textContent), which the HTML parser would lose.
function compile(strings: readonly string[]): Compiled {
  type State = 'text' | 'comment' | 'raw' | 'tagName' | 'tag' | 'name' | 'afterName' | 'beforeValue' | 'value';
  let out = '', state = 'text' as State, name = '', quote = '', tag = '', raw = '';
  let statics: string[] = [], indexes: number[] = [];
  const attributes: AttributeHole[] = [];
  const finishAttribute = (hasValue: boolean) => {
    if (indexes.length) {
      const hole = { name, tag: tag.toLowerCase(), statics, indexes };
      checkAttribute(hole);
      out += `${ATTRIBUTE_MARK}${attributes.length}`;
      attributes.push(hole);
    } else out += hasValue ? `${name}="${statics[0].replace(/"/g, '&quot;')}"` : name;
    name = ''; statics = []; indexes = [];
  };
  // After a start tag of a raw-text element, the content runs to its end tag.
  const closeTag = () => {
    const lower = tag.toLowerCase();
    state = !lower.startsWith('/') && RAW_TEXT.has(lower) ? 'raw' : 'text';
    raw = lower;
  };
  for (let s = 0; s < strings.length; s++) {
    const text = strings[s];
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      switch (state) {
        case 'text':
          if (text.startsWith('<!--', i)) { state = 'comment'; out += '<!--'; i += 3; }
          else { if (ch === '<' && /[A-Za-z/]/.test(text[i + 1] ?? '')) { state = 'tagName'; tag = ''; } out += ch; }
          break;
        case 'comment':
          if (text.startsWith('-->', i)) { state = 'text'; out += '-->'; i += 2; } else out += ch;
          break;
        case 'raw':
          if (ch === '<' && text.slice(i + 2, i + 2 + raw.length).toLowerCase() === raw && text[i + 1] === '/') { state = 'text'; i--; }
          else out += ch;
          break;
        case 'tagName':
          if (SPACE.test(ch)) state = 'tag';
          else if (ch === '>') { out += ch; closeTag(); break; }
          else tag += ch;
          out += ch;
          break;
        case 'tag':
          if (ch === '>') { out += ch; closeTag(); }
          else if (SPACE.test(ch) || ch === '/') out += ch;
          else { state = 'name'; name = ch; }
          break;
        case 'name':
          if (ch === '=') { state = 'beforeValue'; statics = ['']; }
          else if (SPACE.test(ch)) state = 'afterName';
          else if (ch === '>' || ch === '/') { finishAttribute(false); state = 'tag'; i--; }
          else name += ch;
          break;
        case 'afterName':
          if (ch === '=') { state = 'beforeValue'; statics = ['']; }
          else if (!SPACE.test(ch)) { finishAttribute(false); out += ' '; state = 'tag'; i--; }
          break;
        case 'beforeValue':
          if (SPACE.test(ch)) break;
          state = 'value';
          if (ch === '"' || ch === "'") quote = ch;
          else { quote = ''; i--; }
          break;
        case 'value':
          // An unquoted value ends at a space, at > or at the / of />.
          if (quote ? ch === quote : SPACE.test(ch) || ch === '>' || (ch === '/' && text[i + 1] === '>')) {
            finishAttribute(true);
            state = 'tag';
            if (!quote) i--;
          } else statics[statics.length - 1] += ch;
          break;
      }
    }
    if (s === strings.length - 1) break;
    // A hole follows this string.
    const near = strings[s].slice(-30);
    if (state === 'text') {
      if (text.endsWith('<')) throw new Error(`Template value ${s + 1} cannot be a tag name, near: ${near}`);
      out += `<!--${NODE_MARK}${s}-->`;
    } else if (state === 'value' || state === 'beforeValue') {
      if (state === 'beforeValue') { state = 'value'; quote = ''; }
      indexes.push(s);
      statics.push('');
    } else if (state === 'raw') {
      throw new Error(raw === 'textarea'
        ? 'Template values cannot go inside <textarea>; bind its value with prop:value=${...}'
        : `Template values cannot go inside <${raw}>`);
    } else throw new Error(`Template value ${s + 1} must be a child or an attribute value, near: ${near}`);
  }
  if (state === 'value' || state === 'name' || state === 'afterName') finishAttribute(state === 'value');
  const template = document.createElement('template');
  template.innerHTML = out.trim();
  const parts: Part[] = [];
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_COMMENT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeType === Node.COMMENT_NODE) {
      const data = (node as Comment).data;
      if (data.startsWith(NODE_MARK)) parts.push({ path: pathOf(node, template.content), node: Number(data.slice(NODE_MARK.length)) });
      continue;
    }
    const element = node as Element;
    for (const attribute of [...element.attributes]) {
      if (!attribute.name.startsWith(ATTRIBUTE_MARK)) continue;
      parts.push({ path: pathOf(element, template.content), attribute: attributes[Number(attribute.name.slice(ATTRIBUTE_MARK.length))] });
      element.removeAttribute(attribute.name);
    }
  }
  return { template, parts };
}

function pathOf(node: Node, root: Node): number[] {
  const path: number[] = [];
  for (let item = node; item !== root; item = item.parentNode!) path.unshift(Array.prototype.indexOf.call(item.parentNode!.childNodes, item));
  return path;
}

function resolve(root: Node, path: number[]): Node {
  let node = root;
  for (const index of path) node = node.childNodes[index];
  return node;
}

/**
 * Build DOM from a template literal. The markup is parsed once per call site and cloned.
 * Returns the single top-level node, or a DocumentFragment when there are several.
 */
export function html(strings: TemplateStringsArray, ...values: unknown[]): Node {
  let compiled = cache.get(strings);
  if (!compiled) cache.set(strings, compiled = compile(strings));
  const fragment = compiled.template.content.cloneNode(true) as DocumentFragment;
  // Find every target before binding, since inserting children shifts positions.
  const targets = compiled.parts.map(part => resolve(fragment, part.path));
  compiled.parts.forEach((part, index) => {
    const target = targets[index];
    if (part.attribute) bindAttribute(target as Element, part.attribute, values);
    else {
      const value = values[part.node!] as Child;
      insert(target.parentNode!, value, target);
      if (!dynamic(value)) target.parentNode!.removeChild(target);
    }
  });
  return fragment.childNodes.length === 1 ? fragment.firstChild! : fragment;
}

/** A static template: parsed once, returns a function that clones it. Find nodes with querySelector. */
export function template(markup: string): () => Node {
  const element = document.createElement('template');
  element.innerHTML = markup.trim();
  return () => {
    const fragment = element.content.cloneNode(true) as DocumentFragment;
    return fragment.childNodes.length === 1 ? fragment.firstChild! : fragment;
  };
}

// ---------- attributes, properties, classes, styles and events ----------

/**
 * Set an attribute safely: null/false removes it, true sets it empty, URLs go through
 * safeUrl(). Event handler, style, srcdoc and similar attributes are refused.
 */
export function setAttribute(element: Element, name: string, value: unknown) {
  const lower = name.toLowerCase();
  if (lower.startsWith('on')) throw new Error(`Use on:${name.slice(2)} for event handlers, not the ${name} attribute`);
  if (HTML_SINKS.has(lower)) throw new Error(`${name} is not allowed; data is never parsed as HTML`);
  if (value === null || value === undefined || value === false) { element.removeAttribute(name); return; }
  element.setAttribute(name, value === true ? '' : cleanUrlValue(lower, String(value)));
}

function bindAttribute(element: Element, hole: AttributeHole, values: unknown[]) {
  const { name, statics, indexes } = hole;
  const whole = statics.length === 2 && statics[0] === '' && statics[1] === '';
  if (!whole) {
    // Text around the values, for example class="row ${kind}".
    const compose = () => statics.map((text, index) => text + (index < indexes.length ? stringify(read(values[indexes[index]] as Value<unknown>)) : '')).join('');
    if (indexes.some(index => dynamic(values[index]))) effect(() => setAttribute(element, name, compose()));
    else setAttribute(element, name, compose());
    return;
  }
  const value = values[indexes[0]];
  const colon = name.indexOf(':');
  const prefix = colon > 0 ? name.slice(0, colon) : '', key = name.slice(colon + 1);
  if (prefix === 'on') { listen(element, key, value as EventListener); return; }
  if (name === 'ref') { (value as (element: Element) => void)(element); return; }
  let apply: (next: unknown) => void;
  if (prefix === 'prop') {
    const lower = key.toLowerCase();
    if (HTML_SINKS.has(lower)) throw new Error(`prop:${key} is not allowed; data is never parsed as HTML`);
    const url = URL_SINKS.has(lower) || lower === 'srcset' || lower === 'ping';
    apply = next => { (element as any)[key] = url && next !== null && next !== undefined ? cleanUrlValue(lower, String(next)) : next; };
  } else if (prefix === 'class') apply = next => element.classList.toggle(key, Boolean(next));
  else if (prefix === 'style') {
    apply = next => {
      const style = (element as HTMLElement).style;
      if (next === null || next === undefined || next === false) style.removeProperty(key); else style.setProperty(key, String(next));
    };
  } else apply = next => setAttribute(element, name, next);
  if (dynamic(value)) effect(() => apply(read(value)));
  else apply(value);
}

const stringify = (value: unknown) => value === null || value === undefined || value === false ? '' : String(value);

/** Add an event listener that is removed when the current scope is disposed. */
export function listen(target: EventTarget, type: string, handler: EventListener, options?: AddEventListenerOptions) {
  if (typeof handler !== 'function') throw new Error(`on:${type} needs a function`);
  target.addEventListener(type, handler, options);
  onCleanup(() => target.removeEventListener(type, handler, options));
}

// ---------- children and ranges ----------

// Turn a child value into nodes. Functions and cells are read (and tracked by the caller).
function nodesOf(value: Child, into: Node[] = []): Node[] {
  if (value === null || value === undefined || value === false || value === true) return into;
  if (Array.isArray(value)) { for (const item of value) nodesOf(item, into); return into; }
  if (typeof value === 'function' || isReadable(value)) return nodesOf(read(value as Value<Child>), into);
  if (value instanceof DocumentFragment) { into.push(...value.childNodes); return into; }
  if (value instanceof Node) { into.push(value); return into; }
  into.push(document.createTextNode(String(value)));
  return into;
}

// A child value as a fragment; a reactive value stays reactive (between its own markers).
function fragmentOf(value: Child): DocumentFragment {
  const fragment = document.createDocumentFragment();
  insert(fragment, value);
  return fragment;
}

const marker = () => document.createComment('');

// Remove everything strictly between two markers.
function clear(start: Node, end: Node) {
  const parent = start.parentNode;
  if (!parent) return;
  while (start.nextSibling && start.nextSibling !== end) parent.removeChild(start.nextSibling);
}

// Remove two markers and everything between them.
function removeRange(start: Node, end: Node) {
  const parent = start.parentNode;
  if (!parent) return;
  clear(start, end);
  parent.removeChild(start);
  if (end.parentNode === parent) parent.removeChild(end);
}

/**
 * Insert a child before `before` (or at the end). A reactive child keeps its place between
 * two markers: text updates in place, anything else replaces what is between them.
 */
export function insert(parent: Node, value: Child, before: Node | null = null) {
  if (!dynamic(value)) {
    for (const node of nodesOf(value)) parent.insertBefore(node, before);
    return;
  }
  const end = before && before.nodeType === Node.COMMENT_NODE ? before : parent.insertBefore(marker(), before);
  const start = end.parentNode!.insertBefore(marker(), end);
  effect(() => {
    const next = read(value as Value<Child>);
    const host = end.parentNode!, only = start.nextSibling;
    if ((typeof next === 'string' || typeof next === 'number' || typeof next === 'bigint') && only !== end && only?.nextSibling === end && only.nodeType === Node.TEXT_NODE) {
      const text = String(next);
      if ((only as Text).data !== text) (only as Text).data = text;
      return;
    }
    const nodes = nodesOf(next);
    const keep = new Set(nodes);
    for (let node = start.nextSibling; node && node !== end; ) {
      const following: ChildNode | null = node.nextSibling;
      if (!keep.has(node)) host.removeChild(node);
      node = following;
    }
    for (const node of nodes) host.insertBefore(node, end);
  });
}

/** Render into a container. Returns dispose, which removes the nodes and stops their effects. */
export function mount(target: Element, render: () => Child): () => void {
  return scope(() => {
    const start = target.appendChild(marker()), end = target.appendChild(marker());
    insert(target, untrack(render), end);
    onCleanup(() => removeRange(start, end));
  });
}

// A region between two markers whose content is rendered by `choose` and replaced as a whole.
function region(choose: () => (() => Child) | null | undefined): Node {
  const fragment = document.createDocumentFragment();
  const start = fragment.appendChild(marker()), end = fragment.appendChild(marker());
  effect(() => {
    const render = choose();
    clear(start, end);
    // The effect owns this render, so its effects are disposed when the branch changes.
    if (render) end.parentNode!.insertBefore(fragmentOf(untrack(render)), end);
  });
  return fragment;
}

/** Show `yes` while the condition is truthy, otherwise `no`. Switches only when truthiness changes. */
export function when(condition: Value<unknown>, yes: () => Child, no?: () => Child): Node {
  const truthy = derive(() => Boolean(read(condition)));
  return region(() => truthy.get() ? yes : no);
}

/** Render the case for the current value, or the fallback. Re-renders only when the value changes. */
export function match<K extends string | number>(value: Value<K>, cases: Partial<Record<K, () => Child>>, fallback?: () => Child): Node {
  const key = derive(() => read(value));
  return region(() => cases[key.get()] ?? fallback);
}

// ---------- keyed lists ----------

// A row is one element, or everything between its two markers.
interface Row<T> { key: unknown; item: Cell<T>; index: Cell<number>; first: Node; last: Node; dispose: () => void }

export interface EachOptions { fallback?: () => Child }

/**
 * A keyed list. Rows are created once per key and reused when the list changes; reordering
 * moves only the rows outside the longest run that kept its order. render gets accessors,
 * so a row whose item changes updates in place. A duplicate key is an error and leaves the
 * list as it was.
 */
export function each<T>(list: Value<readonly T[]>, key: (item: T, index: number) => unknown,
  render: (item: () => T, index: () => number) => Child, options: EachOptions = {}): Node {
  const fragment = document.createDocumentFragment();
  const end = fragment.appendChild(marker());
  // Rows belong to the scope that called each(), not to the effect, so a list change keeps them.
  const owner = getOwner();
  let rows = new Map<unknown, Row<T>>();
  let order: Row<T>[] = [];
  let fallback: { start: Node; end: Node; dispose: () => void } | null = null;

  const create = (value: T, at: number, rowKey: unknown): Row<T> => {
    const item = cell(value), index = cell(at);
    let content!: DocumentFragment;
    const dispose = runWithOwner(owner, () => scope(() => { content = fragmentOf(render(() => item.get(), () => index.get())); }));
    const only = content.firstChild;
    if (only && only === content.lastChild && only.nodeType === Node.ELEMENT_NODE) return { key: rowKey, item, index, first: only, last: only, dispose };
    // Anything else (text, several nodes, content that may change) sits between markers.
    content.insertBefore(marker(), content.firstChild);
    content.appendChild(marker());
    const first = content.firstChild!, last = content.lastChild!;
    (first as any).evletContent = content;
    return { key: rowKey, item, index, first, last, dispose };
  };
  const nodesOfRow = (row: Row<T>) => {
    const nodes: Node[] = [row.first];
    for (let node = row.first; node !== row.last && node.nextSibling; ) { node = node.nextSibling; nodes.push(node); }
    return nodes;
  };
  const place = (host: Node, row: Row<T>, before: Node) => {
    const pending = (row.first as any).evletContent as DocumentFragment | undefined;
    if (pending) { delete (row.first as any).evletContent; host.insertBefore(pending, before); return; }
    if (row.first === row.last) host.insertBefore(row.first, before);
    else for (const node of nodesOfRow(row)) host.insertBefore(node, before);
  };
  const remove = (host: Node, row: Row<T>) => {
    row.dispose();
    for (const node of nodesOfRow(row)) if (node.parentNode === host) host.removeChild(node);
  };

  effect(() => {
    const items = read(list) ?? [];
    untrack(() => {
      // Check every key first, so a duplicate changes nothing.
      const keys = items.map((item, at) => key(item, at));
      const seen = new Set<unknown>();
      for (const rowKey of keys) {
        if (seen.has(rowKey)) throw new Error(`each(): the key ${String(rowKey)} appears twice`);
        seen.add(rowKey);
      }
      const host = end.parentNode!;
      const nextRows = new Map<unknown, Row<T>>(), next: Row<T>[] = new Array(items.length);
      for (let at = 0; at < items.length; at++) {
        let row = rows.get(keys[at]);
        if (row) { row.item.set(items[at]); row.index.set(at); }
        else row = create(items[at], at, keys[at]);
        nextRows.set(keys[at], row);
        next[at] = row;
      }
      for (const [rowKey, row] of rows) if (!nextRows.has(rowKey)) remove(host, row);
      // Rows on the longest increasing run of old positions stay; the others move or are new.
      const previous = new Map(order.map((row, at) => [row, at]));
      const stay = longestRun(next.map(row => previous.get(row) ?? -1));
      let before: Node = fallback ? fallback.start : end;
      for (let at = next.length - 1; at >= 0; at--) {
        const row = next[at];
        if (!stay.has(at)) place(host, row, before);
        before = row.first;
      }
      rows = nextRows;
      order = next;
      if (options.fallback && !items.length && !fallback) {
        const start = host.insertBefore(marker(), end), stop = host.insertBefore(marker(), end);
        let content!: DocumentFragment;
        const dispose = runWithOwner(owner, () => scope(() => { content = fragmentOf(options.fallback!()); }));
        host.insertBefore(content, stop);
        fallback = { start, end: stop, dispose };
      } else if (items.length && fallback) {
        fallback.dispose();
        removeRange(fallback.start, fallback.end);
        fallback = null;
      }
    });
  });
  onCleanup(() => { for (const row of rows.values()) row.dispose(); fallback?.dispose(); });
  return fragment;
}

// Positions (in `sources`) of one longest strictly increasing subsequence, ignoring -1 (new rows).
function longestRun(sources: number[]): Set<number> {
  const tails: number[] = [], links = new Array<number>(sources.length);
  for (let at = 0; at < sources.length; at++) {
    const value = sources[at];
    if (value < 0) continue;
    let low = 0, high = tails.length;
    while (low < high) { const mid = (low + high) >> 1; if (sources[tails[mid]] < value) low = mid + 1; else high = mid; }
    links[at] = low > 0 ? tails[low - 1] : -1;
    tails[low] = at;
  }
  const result = new Set<number>();
  for (let at = tails.length ? tails[tails.length - 1] : -1; at >= 0; at = links[at]) result.add(at);
  return result;
}

// ---------- portal ----------

/**
 * Render somewhere else (for example document.body for a modal). The content is removed
 * when the current scope is disposed. Returns an empty placeholder to leave where it was called.
 */
export function portal(target: Element, render: () => Child): Node {
  scope(() => {
    const start = target.appendChild(marker()), end = target.appendChild(marker());
    insert(target, untrack(render), end);
    onCleanup(() => removeRange(start, end));
  });
  return document.createComment('portal');
}
