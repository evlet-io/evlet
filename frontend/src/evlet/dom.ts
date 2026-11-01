// Evlet DOM layer: templates parsed once and cloned per use, one effect per dynamic binding.
//
//   const row = (item: () => Line) => html`
//     <tr class:selected=${() => item().selected}>
//       <td>${() => item().name}</td>
//       <td><button on:click=${() => remove(item())}>Remove</button></td>
//     </tr>`;
//
// Values are never parsed as HTML: text goes through Text nodes, attributes through
// setAttribute. A function, cell or derive in a hole is reactive; anything else is set once.

import { cell, derive, effect, getOwner, isReadable, onCleanup, runWithOwner, scope, untrack, type Cell, type Readable } from './reactive';

export type Child = Node | string | number | bigint | boolean | null | undefined | Child[] | (() => Child) | Readable<Child>;
type Value<T> = T | (() => T) | Readable<T>;

/** The current value of a plain value, accessor function or cell/derive. */
export function read<T>(value: Value<T>): T {
  if (typeof value === 'function') return (value as () => T)();
  if (isReadable(value)) return value.get() as T;
  return value as T;
}
const dynamic = (value: unknown) => typeof value === 'function' || isReadable(value);

// ---------- template compilation ----------

interface AttributeHole { name: string; statics: string[]; indexes: number[] }
interface Part { path: number[]; node?: number; attribute?: AttributeHole }
interface Compiled { template: HTMLTemplateElement; parts: Part[] }

const NODE_MARK = 'evlet:';
const ATTRIBUTE_MARK = 'evlet-a';
const cache = new WeakMap<TemplateStringsArray, Compiled>();
const SPACE = /\s/;

// A small scanner that finds where each hole sits: as a child, or inside an attribute value.
// Child holes become <!--evlet:N--> comments; dynamic attributes become evlet-aK markers.
// Attribute names keep their written case (prop:textContent), which the HTML parser would lose.
function compile(strings: readonly string[]): Compiled {
  let out = '', state: 'text' | 'comment' | 'tagName' | 'tag' | 'name' | 'afterName' | 'beforeValue' | 'value' = 'text';
  let name = '', quote = '', statics: string[] = [], indexes: number[] = [];
  const attributes: AttributeHole[] = [];
  const finishAttribute = (hasValue: boolean) => {
    if (indexes.length) {
      out += `${ATTRIBUTE_MARK}${attributes.length}`;
      attributes.push({ name, statics, indexes });
    } else out += hasValue ? `${name}="${statics[0].replace(/"/g, '&quot;')}"` : name;
    name = ''; statics = []; indexes = [];
  };
  for (let s = 0; s < strings.length; s++) {
    const text = strings[s];
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      switch (state) {
        case 'text':
          if (text.startsWith('<!--', i)) { state = 'comment'; out += '<!--'; i += 3; }
          else { if (ch === '<' && /[A-Za-z/]/.test(text[i + 1] ?? '')) state = 'tagName'; out += ch; }
          break;
        case 'comment':
          if (text.startsWith('-->', i)) { state = 'text'; out += '-->'; i += 2; } else out += ch;
          break;
        case 'tagName':
          if (SPACE.test(ch)) state = 'tag'; else if (ch === '>') state = 'text';
          out += ch;
          break;
        case 'tag':
          if (ch === '>') { state = 'text'; out += ch; }
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
          if (quote ? ch === quote : SPACE.test(ch) || ch === '>') {
            finishAttribute(true);
            state = 'tag';
            if (!quote) i--;
          } else statics[statics.length - 1] += ch;
          break;
      }
    }
    if (s === strings.length - 1) break;
    // A hole follows this string.
    if (state === 'text') out += `<!--${NODE_MARK}${s}-->`;
    else if (state === 'value' || state === 'beforeValue') {
      if (state === 'beforeValue') { state = 'value'; quote = ''; }
      indexes.push(s);
      statics.push('');
    } else throw new Error(`Template value ${s + 1} must be a child or an attribute value, near: ${strings[s].slice(-30)}`);
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
      const value = values[part.node!];
      if (dynamic(value)) insert(target.parentNode!, value as Child, target);
      else { insert(target.parentNode!, value as Child, target); target.parentNode!.removeChild(target); }
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

const URL_ATTRIBUTES = new Set(['href', 'src', 'action', 'formaction', 'xlink:href', 'poster', 'cite', 'data']);
const UNSAFE_URL = /^\s*(javascript|vbscript|data):/i;
const HTML_PROPERTIES = new Set(['innerHTML', 'outerHTML', 'srcdoc']);

/** Set an attribute safely: null/false removes it, true sets it empty, script URLs are refused. */
export function setAttribute(element: Element, name: string, value: unknown) {
  if (/^on/i.test(name)) throw new Error(`Use on:${name.slice(2)} for event handlers, not the ${name} attribute`);
  if (value === null || value === undefined || value === false) { element.removeAttribute(name); return; }
  let text = value === true ? '' : String(value);
  if (URL_ATTRIBUTES.has(name.toLowerCase()) && UNSAFE_URL.test(text)) text = 'about:blank';
  element.setAttribute(name, text);
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
    if (HTML_PROPERTIES.has(key)) throw new Error(`prop:${key} is not allowed; data is never parsed as HTML`);
    apply = next => { (element as any)[key] = next; };
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

// ---------- children ----------

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

/**
 * Insert a child before `before` (or at the end). A reactive child keeps its place: text
 * updates in place, other values replace the nodes it inserted last time.
 */
export function insert(parent: Node, value: Child, before: Node | null = null) {
  if (!dynamic(value)) {
    for (const node of nodesOf(value)) parent.insertBefore(node, before);
    return;
  }
  // The anchor marks the end of this child's nodes, so updates find their place.
  const anchor = before && before.nodeType === Node.COMMENT_NODE ? before : parent.insertBefore(document.createComment(''), before);
  let current: Node[] = [];
  effect(() => {
    const next = read(value as Value<Child>);
    const host = anchor.parentNode!;
    if ((typeof next === 'string' || typeof next === 'number' || typeof next === 'bigint') && current.length === 1 && current[0].nodeType === Node.TEXT_NODE) {
      const text = String(next);
      if ((current[0] as Text).data !== text) (current[0] as Text).data = text;
      return;
    }
    const nodes = nodesOf(next);
    for (const node of current) if (!nodes.includes(node) && node.parentNode === host) host.removeChild(node);
    for (const node of nodes) host.insertBefore(node, anchor);
    current = nodes;
  });
}

/** Render into a container. Returns dispose, which removes the nodes and stops their effects. */
export function mount(target: Element, render: () => Child): () => void {
  return scope(() => {
    const start = target.appendChild(document.createComment('')), end = target.appendChild(document.createComment(''));
    insert(target, untrack(render), end);
    onCleanup(() => removeRange(start, end));
  });
}

function removeRange(start: Node, end: Node) {
  const parent = start.parentNode;
  if (!parent) return;
  for (let node: Node | null = start; node; ) {
    const next: Node | null = node === end ? null : node.nextSibling;
    parent.removeChild(node);
    node = next;
  }
}

// A region between two markers whose content is rendered by `choose` and replaced as a whole.
function region(choose: () => (() => Child) | null | undefined): Node {
  const fragment = document.createDocumentFragment();
  const end = fragment.appendChild(document.createComment(''));
  let nodes: Node[] = [];
  effect(() => {
    const render = choose();
    const host = end.parentNode!;
    for (const node of nodes) if (node.parentNode === host) host.removeChild(node);
    // The effect owns this render, so its effects are disposed when the branch changes.
    nodes = render ? nodesOf(untrack(render)) : [];
    for (const node of nodes) host.insertBefore(node, end);
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

interface Row<T> { key: unknown; item: Cell<T>; index: Cell<number>; first: Node; last: Node; dispose: () => void }

export interface EachOptions { fallback?: () => Child }

/**
 * A keyed list. Rows are created once per key and reused when the list changes; reordering
 * moves only the rows outside the longest run that kept its order. render gets accessors,
 * so a row whose item changes updates in place.
 */
export function each<T>(list: Value<readonly T[]>, key: (item: T, index: number) => unknown,
  render: (item: () => T, index: () => number) => Child, options: EachOptions = {}): Node {
  const fragment = document.createDocumentFragment();
  const end = fragment.appendChild(document.createComment(''));
  // Rows belong to the scope that called each(), not to the effect, so a list change keeps them.
  const owner = getOwner();
  let rows = new Map<unknown, Row<T>>();
  let order: Row<T>[] = [];
  let fallback: { nodes: Node[]; dispose: () => void } | null = null;

  const create = (value: T, at: number, rowKey: unknown): Row<T> => {
    const item = cell(value), index = cell(at);
    let first!: Node, last!: Node;
    const dispose = runWithOwner(owner, () => scope(() => {
      const nodes = nodesOf(render(() => item.get(), () => index.get()));
      if (nodes.length === 1) first = last = nodes[0];
      else {
        // Several nodes, or nodes that may change: keep them between markers so moves take them all.
        const fragment = document.createDocumentFragment();
        first = fragment.appendChild(document.createComment(''));
        fragment.append(...nodes);
        last = fragment.appendChild(document.createComment(''));
      }
    }));
    return { key: rowKey, item, index, first, last, dispose };
  };
  const nodesOfRow = (row: Row<T>) => {
    const nodes: Node[] = [row.first];
    for (let node = row.first; node !== row.last; ) { node = node.nextSibling ?? row.last; nodes.push(node); }
    return nodes;
  };
  const place = (host: Node, row: Row<T>, before: Node) => {
    // A new multi-node row still sits in its own fragment.
    if (row.first === row.last || row.first.parentNode !== host) {
      if (row.first === row.last) host.insertBefore(row.first, before);
      else host.insertBefore(row.first.parentNode as DocumentFragment, before);
      return;
    }
    for (const node of nodesOfRow(row)) host.insertBefore(node, before);
  };
  const remove = (host: Node, row: Row<T>) => {
    row.dispose();
    if (row.first === row.last) { if (row.first.parentNode === host) host.removeChild(row.first); }
    else for (const node of nodesOfRow(row)) host.removeChild(node);
  };

  effect(() => {
    const items = read(list) ?? [];
    untrack(() => {
      const host = end.parentNode!;
      const nextRows = new Map<unknown, Row<T>>(), next: Row<T>[] = new Array(items.length);
      for (let at = 0; at < items.length; at++) {
        const rowKey = key(items[at], at);
        if (nextRows.has(rowKey)) throw new Error(`each(): the key ${String(rowKey)} appears twice`);
        let row = rows.get(rowKey);
        if (row) { row.item.set(items[at]); row.index.set(at); }
        else row = create(items[at], at, rowKey);
        nextRows.set(rowKey, row);
        next[at] = row;
      }
      for (const [rowKey, row] of rows) if (!nextRows.has(rowKey)) remove(host, row);
      // Rows on the longest increasing run of old positions stay; the others move or are new.
      const previous = new Map(order.map((row, at) => [row, at]));
      const stay = longestRun(next.map(row => previous.get(row) ?? -1));
      let before: Node = end;
      for (let at = next.length - 1; at >= 0; at--) {
        const row = next[at];
        if (!stay.has(at)) place(host, row, before);
        before = row.first;
      }
      rows = nextRows;
      order = next;
      if (options.fallback && !items.length && !fallback) {
        let nodes: Node[] = [];
        const dispose = runWithOwner(owner, () => scope(() => { nodes = nodesOf(options.fallback!()); }));
        for (const node of nodes) host.insertBefore(node, end);
        fallback = { nodes, dispose };
      } else if (items.length && fallback) {
        fallback.dispose();
        for (const node of fallback.nodes) node.parentNode?.removeChild(node);
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
    const start = target.appendChild(document.createComment('')), end = target.appendChild(document.createComment(''));
    insert(target, untrack(render), end);
    onCleanup(() => removeRange(start, end));
  });
  return document.createComment('portal');
}
