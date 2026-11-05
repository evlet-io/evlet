// RichTextEditor: Evlet's own rich text editor (docs/internal/rich-text.md).
//
// The surface is contenteditable, but the browser never edits it on its own: every beforeinput is
// cancelled and applied to the model (model.ts, ops.ts), which then renders. During IME composition,
// and for the input events phone keyboards do not let us cancel, the browser edits the text node; the
// editor then reads the changed block back, applies the difference to the model and renders again.

import './richtext.css';
import { html, listen } from '../dom';
import { cell, effect, untrack } from '../reactive';
import { Icon } from '../ui/base';
import { uid } from '../ui/core';
import { openPopover } from '../ui/layers';
import { canonical, linkFromInput, parseHTML, parsePastedHTML, parsePlainText, readSurface, toHTML } from './html';
import { History } from './history';
import {
  BREAK, blockLength, blockText, caret, clampSelection, collapsed, docEnd, emptyDoc, isEmpty, linkAt, marksAt, ordered, pos, samePos,
  sortMarks, text, textLength, type Block, type Doc, type Inline, type Mark, type Selection,
} from './model';
import {
  blockMatches, deleteBackward, deleteForward, deleteSelection, hasMark, indent, inputRule, insertBlocks, insertBreak, insertLink, insertRule,
  insertText, replaceRange, selectedBlocks, selectionBlocks, setLink, splitBlock, toggleBlock, toggleChecked, toggleMark,
  type BlockSpec, type State, type Unit,
} from './ops';
import { domText, pointToPos, posToPoint, renderSurface } from './render';

export interface RichTextProps {
  /** The stored HTML (reactive): a change from outside replaces the document. */
  value: () => string | null | undefined;
  /** Called with the canonical HTML after every change, or null when the document is empty. */
  onChange: (value: string | null) => void;
  id?: string;
  label?: string;
  labelledBy?: string;
  describedBy?: string;
  placeholder?: string;
  disabled?: () => boolean;
  invalid?: () => boolean;
  required?: () => boolean;
  /** Plain-text limit (the field's max_length): a counter shows near it. */
  maxLength?: number;
}

export interface RichTextEditor {
  element: HTMLElement;
  surface: HTMLElement;
  focus(): void;
  /** The current state, for tests and tools. */
  state(): State;
}

const isMac = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MARK_NAMES: Record<Mark, string> = { strong: 'Bold', em: 'Italic', u: 'Underline', s: 'Strikethrough', code: 'Inline code' };
const BLOCK_NAMES: Array<[BlockSpec, string, string]> = [
  [{ type: 'heading', level: 2 }, 'Heading', 'heading-2'], [{ type: 'heading', level: 3 }, 'Subheading', 'heading-3'],
  [{ type: 'item', list: 'bullet' }, 'Bulleted list', 'list'], [{ type: 'item', list: 'ordered' }, 'Numbered list', 'list-ordered'],
  [{ type: 'item', list: 'check' }, 'Checklist', 'list-checks'], [{ type: 'quote' }, 'Quote', 'text-quote'], [{ type: 'code' }, 'Code block', 'square-code'],
];

interface Status { marks: Mark[]; block: BlockSpec | null; link: boolean; undo: boolean; redo: boolean; length: number }

export function RichTextEditor(props: RichTextProps): RichTextEditor {
  const id = props.id ?? uid('rt');
  const history = new History();
  let state: State = { doc: emptyDoc(), selection: caret(pos(0, 0)) };
  let elements: HTMLElement[] = [];
  let cache = new Map<Block, HTMLElement>();
  let stored: { marks: Mark[]; href: string | null } | null = null; // marks toggled at a caret
  let composing = false, rendering = false, pendingReconcile = false, focused = false;
  let lastEmitted: string | null | undefined;
  let afterRule: State | null = null; // undo point when Backspace follows an input rule
  let breakAfterSpace = false;
  const status = cell<Status>({ marks: [], block: null, link: false, undo: false, redo: false, length: 0 });
  const floating = cell<DOMRect | null>(null);
  const disabled = () => Boolean(props.disabled?.());

  // ---------- the DOM ----------

  const live = html`<span class="rt-sr" aria-live="polite"></span>` as HTMLElement;
  const announce = (message: string) => { live.textContent = ''; window.setTimeout(() => { live.textContent = message; }, 20); };

  const surface = html`<div class="rt-surface" id=${id} role="textbox" aria-multiline="true" contenteditable=${() => disabled() ? 'false' : 'true'}
    tabindex="0" spellcheck="true" aria-label=${props.labelledBy ? null : props.label ?? null} aria-labelledby=${props.labelledBy ?? null}
    aria-describedby=${props.describedBy ?? null} aria-readonly=${() => disabled() ? 'true' : null}
    aria-invalid=${() => props.invalid?.() ? 'true' : null} aria-required=${() => props.required?.() ? 'true' : null}
    data-placeholder=${props.placeholder || null} translate="no"></div>` as HTMLElement;

  // ---------- state changes ----------

  const emit = () => {
    const value = isEmpty(state.doc) ? null : toHTML(state.doc);
    lastEmitted = value;
    props.onChange(value);
  };

  function render() {
    rendering = true;
    try {
      elements = renderSurface(surface, state.doc, cache);
      cache = new Map(state.doc.blocks.map((block, index) => [block, elements[index]]));
      surface.classList.toggle('rt-empty', state.doc.blocks.length === 1 && blockLength(state.doc.blocks[0]) === 0 && state.doc.blocks[0].type === 'paragraph');
      if (focused || document.activeElement === surface) writeSelection();
    } finally { rendering = false; }
    refreshStatus();
  }

  function refreshStatus() {
    const head = state.doc.blocks[state.selection.head.block];
    const marks = collapsed(state.selection)
      ? (stored?.marks ?? marksAt(head.content, state.selection.head.offset).marks)
      : (['strong', 'em', 'u', 's', 'code'] as Mark[]).filter(mark => hasMark(state, mark));
    const blocks = selectedBlocks(state).map(index => state.doc.blocks[index]);
    const block = BLOCK_NAMES.map(([spec]) => spec).find(spec => blocks.length > 0 && blocks.every(item => blockMatches(item, spec))) ?? null;
    const link = Boolean(linkAt(head.content, state.selection.head.offset)) && head.type !== 'code';
    status.set({ marks, block, link, undo: history.canUndo, redo: history.canRedo, length: textLength(state.doc) });
    updateFloating();
  }

  /** Apply a change: record it for undo (group joins typing bursts), render and report the value. */
  function apply(next: State, options: { group?: string | null; message?: string; keepStored?: boolean } = {}) {
    if (next === state) return;
    history.record(state, options.group ?? null);
    if (options.group === undefined || options.group === null) history.close();
    state = { doc: next.doc, selection: clampSelection(next.doc, next.selection) };
    if (!options.keepStored) stored = null;
    afterRule = null;
    render();
    emit();
    if (options.message) announce(options.message);
  }

  function load(value: string | null | undefined) {
    const doc = parseHTML(value ?? '');
    // A document opened for editing puts the caret at its end, ready to add to it.
    state = { doc, selection: caret(docEnd(doc)) };
    history.clear();
    stored = null;
    lastEmitted = value ?? null;
    render();
  }

  // ---------- selection ----------

  function readSelection(): Selection | null {
    const selection = window.getSelection();
    if (!selection || !selection.anchorNode) return null;
    const anchor = pointToPos(surface, selection.anchorNode, selection.anchorOffset);
    const head = pointToPos(surface, selection.focusNode, selection.focusOffset);
    return anchor && head ? clampSelection(state.doc, { anchor, head }) : null;
  }

  function writeSelection() {
    const selection = window.getSelection();
    if (!selection || !elements.length) return;
    const point = (at: { block: number; offset: number }) => posToPoint(elements[at.block], at.offset);
    const [anchorNode, anchorOffset] = point(state.selection.anchor);
    const [focusNode, focusOffset] = point(state.selection.head);
    if (selection.anchorNode === anchorNode && selection.anchorOffset === anchorOffset && selection.focusNode === focusNode && selection.focusOffset === focusOffset) return;
    try { selection.setBaseAndExtent(anchorNode, anchorOffset, focusNode, focusOffset); } catch { /* a detached node: the next render fixes it */ }
  }

  function syncSelection() {
    const next = readSelection();
    if (!next) return;
    if (!samePos(next.anchor, state.selection.anchor) || !samePos(next.head, state.selection.head)) {
      state = { ...state, selection: next };
      stored = null;
      history.close();
      refreshStatus();
    }
  }

  /** The range an input event acts on: its target range when the browser gives one, else the selection. */
  function targetOf(event: InputEvent): Selection {
    const ranges = typeof event.getTargetRanges === 'function' ? event.getTargetRanges() : [];
    if (ranges.length) {
      const start = pointToPos(surface, ranges[0].startContainer, ranges[0].startOffset);
      const end = pointToPos(surface, ranges[0].endContainer, ranges[0].endOffset);
      if (start && end) return clampSelection(state.doc, { anchor: start, head: end });
    }
    return readSelection() ?? state.selection;
  }

  // ---------- editing ----------

  function typed(value: string, range: Selection) {
    const [from] = ordered(range);
    const block = state.doc.blocks[from.block];
    const style = stored ?? marksAt(block.content, from.offset);
    const after = insertText({ doc: state.doc, selection: range }, value, style);
    // A word ends at a space: the next letter starts a new undo step.
    if (breakAfterSpace && !/^\s/.test(value)) history.close();
    breakAfterSpace = /\s$/.test(value);
    apply(after, { group: 'type', keepStored: true });
    if (stored) stored = { ...stored };
    const ruled = inputRule(state);
    if (ruled) {
      apply(ruled, { message: 'Formatted' });
      afterRule = after;
    }
  }

  function remove(range: Selection, direction: 'backward' | 'forward', unit: Unit) {
    if (afterRule && direction === 'backward' && collapsed(range)) {
      // Backspace right after an input rule gives back the typed characters.
      const restore = afterRule;
      apply(restore, { message: 'Formatting undone' });
      return;
    }
    const current = { doc: state.doc, selection: range };
    apply(direction === 'backward' ? deleteBackward(current, unit) : deleteForward(current, unit), { group: 'delete' });
  }

  function onBeforeInput(event: InputEvent) {
    if (disabled()) { event.preventDefault(); return; }
    const type = event.inputType;
    if (composing || type === 'insertCompositionText' || type === 'deleteCompositionText' || type === 'insertFromComposition') return;
    if (!event.cancelable) { pendingReconcile = true; return; }
    event.preventDefault();
    const range = targetOf(event);
    switch (type) {
      case 'insertText': case 'insertReplacementText': {
        const value = event.data ?? event.dataTransfer?.getData('text/plain') ?? '';
        if (value) typed(value, range);
        return;
      }
      case 'insertParagraph': apply(splitBlock({ doc: state.doc, selection: range })); return;
      case 'insertLineBreak': {
        const block = state.doc.blocks[ordered(range)[0].block];
        apply(block.type === 'code' ? insertText({ doc: state.doc, selection: range }, '\n', { marks: [], href: null }) : insertBreak({ doc: state.doc, selection: range }));
        return;
      }
      case 'insertFromPaste': case 'insertFromDrop': case 'insertFromYank': case 'insertFromPasteAsQuotation':
        if (event.dataTransfer) paste(event.dataTransfer, range);
        return;
      case 'insertLink': { const href = linkFromInput(event.data ?? ''); if (href) applyLink(href, range); return; }
      case 'deleteContentBackward': case 'deleteContentForward': case 'deleteWordBackward': case 'deleteWordForward':
      case 'deleteSoftLineBackward': case 'deleteSoftLineForward': case 'deleteHardLineBackward': case 'deleteHardLineForward': {
        const backward = type.endsWith('Backward');
        const unit: Unit = type.includes('Word') ? 'word' : type.includes('Line') ? 'line' : 'char';
        // A range the browser worked out (a grapheme, a word) is used as it is.
        if (!collapsed(range)) apply(deleteSelection({ doc: state.doc, selection: range }), { group: 'delete' });
        else remove(range, backward ? 'backward' : 'forward', unit);
        return;
      }
      case 'deleteByCut': case 'deleteContent': case 'deleteByDrag': case 'deleteEntireSoftLine':
        if (!collapsed(range)) apply(deleteSelection({ doc: state.doc, selection: range }));
        return;
      case 'formatBold': mark('strong'); return;
      case 'formatItalic': mark('em'); return;
      case 'formatUnderline': mark('u'); return;
      case 'formatStrikeThrough': mark('s'); return;
      case 'historyUndo': undo(); return;
      case 'historyRedo': redo(); return;
      default: return; // Anything else would change the DOM behind the model's back.
    }
  }

  /** Read the text the browser changed (composition, uncancellable phone input) back into the model. */
  function reconcile() {
    pendingReconcile = false;
    const children = Array.from(surface.children) as HTMLElement[];
    const same = children.length === elements.length && children.every((child, index) => child === elements[index]);
    const selection = window.getSelection();
    if (same) {
      let doc = state.doc, changed = false, single = false;
      let caretAt: { block: number; offset: number } | null = null;
      children.forEach((element, index) => {
        const block = doc.blocks[index];
        if (block.type === 'rule') return;
        const code = block.type === 'code';
        const now = domText(element, code), before = code ? blockText(block) : blockText(block);
        if (now === before) return;
        let start = 0;
        while (start < now.length && start < before.length && now[start] === before[start]) start++;
        let end = 0;
        while (end < now.length - start && end < before.length - start && now[now.length - 1 - end] === before[before.length - 1 - end]) end++;
        const inserted = now.slice(start, now.length - end);
        const style = marksAt(block.content, start);
        const pieces: Inline[] = [];
        inserted.split(BREAK).forEach((part, at) => { if (at) pieces.push({ kind: 'break' }); if (part) pieces.push(text(part, style.marks, style.href)); });
        doc = replaceRange(doc, pos(index, start), pos(index, before.length - end), pieces).doc;
        caretAt = pos(index, start + inserted.length);
        // One short insertion and nothing removed: typing, as far as the shortcuts are concerned.
        single = !changed && inserted.length > 0 && inserted.length <= 2 && start === before.length - end;
        changed = true;
      });
      if (!changed) return;
      const read = selection?.anchorNode ? pointToPos(surface, selection.focusNode, selection.focusOffset) : null;
      const at = read ?? caretAt!;
      apply({ doc, selection: caret(at) }, { group: 'type' });
      // Phone keyboards commit a space or a backtick through this path: the Markdown shortcuts still apply.
      if (single && caretAt && samePos(at, caretAt)) {
        const typedState = state;
        const ruled = inputRule(state);
        if (ruled) { apply(ruled, { message: 'Formatted' }); afterRule = typedState; }
      }
      return;
    }
    // The browser changed the block structure: read the whole surface back.
    const read = selection?.anchorNode ? pointToPos(surface, selection.focusNode, selection.focusOffset) : null;
    const doc = readSurface(surface);
    cache = new Map();
    for (const child of Array.from(surface.childNodes)) child.remove();
    apply({ doc, selection: caret(read ?? pos(0, 0)) }, { group: 'type' });
  }

  // ---------- commands ----------

  function mark(name: Mark) {
    if (collapsed(state.selection)) {
      const head = state.doc.blocks[state.selection.head.block];
      const current = stored ?? marksAt(head.content, state.selection.head.offset);
      const on = !current.marks.includes(name);
      stored = { marks: sortMarks(on ? [...current.marks, name] : current.marks.filter(item => item !== name)), href: current.href };
      refreshStatus();
      announce(`${MARK_NAMES[name]} ${on ? 'on' : 'off'}`);
      return;
    }
    const on = !hasMark(state, name);
    apply(toggleMark(state, name), { message: `${MARK_NAMES[name]} ${on ? 'on' : 'off'}` });
  }

  function block(spec: BlockSpec | 'rule') {
    if (spec === 'rule') { apply(insertRule(state), { message: 'Divider added' }); return; }
    const all = selectedBlocks(state).every(index => blockMatches(state.doc.blocks[index], spec));
    const name = BLOCK_NAMES.find(([item]) => JSON.stringify(item) === JSON.stringify(spec))?.[1] ?? 'Paragraph';
    apply(toggleBlock(state, spec), { message: all ? 'Paragraph' : name });
  }

  function undo() {
    const previous = history.undo(state);
    if (!previous) { announce('Nothing to undo'); return; }
    state = previous; stored = null; afterRule = null; render(); emit(); announce('Undone');
  }

  function redo() {
    const next = history.redo(state);
    if (!next) { announce('Nothing to redo'); return; }
    state = next; stored = null; render(); emit(); announce('Redone');
  }

  function applyLink(href: string | null, range: Selection = state.selection) {
    const current = { doc: state.doc, selection: range };
    if (href && collapsed(range) && !linkAt(state.doc.blocks[range.head.block].content, range.head.offset)) {
      const label = href.replace(/^mailto:/, '');
      apply(insertLink(current, label, href), { message: 'Link added' });
      return;
    }
    // The caret goes to the end of the link, so typing carries on after it rather than over it.
    const linked = setLink(current, href);
    apply({ doc: linked.doc, selection: caret(ordered(range)[1]) }, { message: href ? 'Link added' : 'Link removed' });
  }

  function linkDialog(anchor: HTMLElement) {
    const head = state.doc.blocks[state.selection.head.block];
    if (head.type === 'code') return;
    const range = state.selection;
    const existing = linkAt(head.content, range.head.offset)?.href ?? '';
    const error = cell('');
    openPopover(anchor, close => {
      const input = html`<input class="rt-link-in" type="url" inputmode="url" autocomplete="off" placeholder="https://example.com" aria-label="Link address"
        aria-describedby=${`${id}-link-err`}>` as HTMLInputElement;
      input.value = existing;
      const save = () => {
        const href = linkFromInput(input.value);
        if (!href) { error.set('Use a web address (http or https) or an email address'); input.focus(); return; }
        close();
        state = { ...state, selection: range };
        applyLink(href, range);
        surface.focus();
      };
      queueMicrotask(() => input.focus());
      // novalidate: the browser's own URL check would refuse evlet.io/docs; linkFromInput decides.
      return html`<form class="rt-link" novalidate on:submit=${(event: Event) => { event.preventDefault(); save(); }}>
        <label class="rt-link-l">Link</label>${input}<span class="err" id=${`${id}-link-err`} role="alert">${error}</span>
        <div class="rt-link-b"><button type="submit" class="btn primary small">Apply</button>${existing
          ? html`<button type="button" class="btn small" on:click=${() => { close(); state = { ...state, selection: range }; applyLink(null, range); surface.focus(); }}>${Icon('unlink', { size: 'sm' })}Remove link</button>` : null}</div></form>`;
    }, { role: 'dialog', label: 'Link', onClose: () => { if (!surface.contains(document.activeElement)) surface.focus(); } });
  }

  // ---------- paste, copy, drop ----------

  function paste(data: DataTransfer, range: Selection, plainOnly = false) {
    const markup = plainOnly ? '' : data.getData('text/html');
    const plain = data.getData('text/plain');
    const target = state.doc.blocks[ordered(range)[0].block];
    const current = { doc: state.doc, selection: range };
    if (target.type === 'code') { apply(insertText(current, plain || '', { marks: [], href: null }), { message: 'Pasted' }); return; }
    // A web address pasted over selected text links it.
    const single = plain.trim();
    if (!collapsed(range) && single && !/\s/.test(single) && /^(https?:\/\/|mailto:)/i.test(single)) {
      const href = linkFromInput(single);
      if (href) { apply(setLink(current, href), { message: 'Link added' }); return; }
    }
    let blocks = markup ? parsePastedHTML(markup) : [];
    if (!blocks.length && plain) blocks = parsePlainText(plain);
    if (!blocks.length) return;
    apply(insertBlocks(current, blocks), { message: 'Pasted' });
  }

  function copy(event: ClipboardEvent, cut: boolean) {
    syncSelection();
    if (collapsed(state.selection) || !event.clipboardData) return;
    event.preventDefault();
    const blocks = selectionBlocks(state);
    const doc: Doc = { blocks };
    event.clipboardData.setData('text/html', toHTML(doc));
    event.clipboardData.setData('text/plain', blocks.map(item => item.type === 'code' ? blockText(item) : blockText(item).split(BREAK).join('\n')).join('\n'));
    if (cut && !disabled()) apply(deleteSelection(state));
  }

  // ---------- keys ----------

  function onKeyDown(event: KeyboardEvent) {
    if (composing || event.isComposing) return;
    const mod = isMac() ? event.metaKey : event.ctrlKey;
    const key = event.key.toLowerCase();
    if (mod && !event.altKey) {
      if (key === 'b' || key === 'i' || key === 'u') { event.preventDefault(); if (!disabled()) mark(key === 'b' ? 'strong' : key === 'i' ? 'em' : 'u'); return; }
      // Ctrl or Cmd K is the link inside the editor, not the app's command palette.
      if (key === 'k') { event.preventDefault(); event.stopPropagation(); if (!disabled()) linkDialog(linkButton); return; }
      if (key === 'z' && !event.shiftKey) { event.preventDefault(); undo(); return; }
      if ((key === 'z' && event.shiftKey) || key === 'y') { event.preventDefault(); redo(); return; }
      if (event.shiftKey && (event.code === 'Digit7' || event.code === 'Digit8' || event.code === 'Digit9')) {
        event.preventDefault();
        if (!disabled()) block({ type: 'item', list: event.code === 'Digit7' ? 'ordered' : event.code === 'Digit8' ? 'bullet' : 'check' });
        return;
      }
      // Ctrl or Cmd Enter is for the form around the editor (save), not a new line.
      if (event.key === 'Enter') { event.preventDefault(); return; }
    }
    if (event.key === 'Tab' && !mod && !event.altKey) {
      // Tab indents only inside lists; anywhere else it leaves the editor, as Tab should.
      const items = selectedBlocks(state);
      if (items.length && items.every(index => state.doc.blocks[index].type === 'item')) {
        event.preventDefault();
        if (!disabled()) apply(indent(state, event.shiftKey ? -1 : 1), { message: event.shiftKey ? 'Outdented' : 'Indented' });
      }
    }
  }

  // ---------- toolbar ----------

  const pressedMark = (name: Mark) => () => String(status.get().marks.includes(name));
  const tool = (icon: string, label: string, shortcut: string, run: (button: HTMLElement) => void, pressed?: () => string | null, enabled: () => boolean = () => true) => {
    const button = html`<button type="button" class="rt-tool" tabindex="-1" aria-label=${shortcut ? `${label} (${shortcut})` : label} title=${shortcut ? `${label} (${shortcut})` : label}
      aria-pressed=${pressed ?? null} disabled=${() => disabled() || !enabled()}
      on:mousedown=${(event: MouseEvent) => event.preventDefault()}
      on:click=${(event: MouseEvent) => { syncSelectionFromState(); run(event.currentTarget as HTMLElement); }}>${Icon(icon, { size: 'sm' })}</button>` as HTMLButtonElement;
    return button;
  };
  const mod = isMac() ? '⌘' : 'Ctrl+';
  const linkButton = tool('link', 'Link', `${mod}K`, button => linkDialog(button), () => String(status.get().link));
  const groups: HTMLElement[][] = [
    [tool('bold', 'Bold', `${mod}B`, () => mark('strong'), pressedMark('strong')), tool('italic', 'Italic', `${mod}I`, () => mark('em'), pressedMark('em')),
      tool('underline', 'Underline', `${mod}U`, () => mark('u'), pressedMark('u')), tool('strikethrough', 'Strikethrough', '', () => mark('s'), pressedMark('s')),
      tool('code', 'Inline code', '', () => mark('code'), pressedMark('code')), linkButton],
    BLOCK_NAMES.map(([spec, label, icon]) => tool(icon, label, spec.type === 'item' && spec.list === 'ordered' ? `${mod}Shift+7` : spec.type === 'item' && spec.list === 'bullet' ? `${mod}Shift+8` : '',
      () => block(spec), () => String(JSON.stringify(status.get().block) === JSON.stringify(spec)))),
    [tool('minus', 'Divider', '', () => block('rule')),
      tool('undo-2', 'Undo', `${mod}Z`, () => undo(), undefined, () => status.get().undo), tool('redo-2', 'Redo', isMac() ? '⌘Shift+Z' : 'Ctrl+Y', () => redo(), undefined, () => status.get().redo)],
  ];
  const tools = groups.flat();
  tools[0].tabIndex = 0;
  // A toolbar is one Tab stop; the arrow keys move between its buttons (roving tabindex).
  const toolbar = html`<div class="rt-toolbar" role="toolbar" aria-label="Formatting" aria-controls=${id}
    on:keydown=${(event: KeyboardEvent) => {
      // Disabled buttons (Undo with nothing to undo) are skipped.
      const usable = tools.filter(item => !(item as HTMLButtonElement).disabled);
      const index = usable.indexOf(document.activeElement as HTMLElement);
      if (index < 0) return;
      const next = event.key === 'ArrowRight' ? (index + 1) % usable.length : event.key === 'ArrowLeft' ? (index - 1 + usable.length) % usable.length
        : event.key === 'Home' ? 0 : event.key === 'End' ? usable.length - 1 : -1;
      if (next < 0) return;
      event.preventDefault();
      tools.forEach(item => { item.tabIndex = item === usable[next] ? 0 : -1; });
      usable[next].focus();
    }}
    on:focusin=${(event: FocusEvent) => { const index = tools.indexOf(event.target as HTMLElement); if (index >= 0) tools.forEach((item, at) => { item.tabIndex = at === index ? 0 : -1; }); }}>${groups.map((group, index) =>
      html`${index ? html`<span class="rt-sep" aria-hidden="true"></span>` : null}${group}`)}</div>` as HTMLElement;

  function syncSelectionFromState() { if (document.activeElement !== surface) surface.focus({ preventScroll: true }); writeSelection(); }

  // The floating selection toolbar (desktop): a few marks right above selected text. Mouse only; the
  // main toolbar has the same buttons for the keyboard.
  const quick = (icon: string, label: string, run: (button: HTMLElement) => void, pressed?: () => string | null) => html`<button type="button" class="rt-tool" tabindex="-1"
    aria-label=${label} aria-pressed=${pressed ?? null} on:mousedown=${(event: MouseEvent) => event.preventDefault()}
    on:click=${(event: MouseEvent) => run(event.currentTarget as HTMLElement)}>${Icon(icon, { size: 'sm' })}</button>` as HTMLElement;
  const float = html`<div class="rt-float" aria-hidden="true" hidden=${() => !floating.get()}
    style:top=${() => floating.get() ? `${Math.max(8, floating.get()!.top - 42)}px` : null}
    style:left=${() => floating.get() ? `${Math.max(8, Math.min(window.innerWidth - 168, floating.get()!.left + floating.get()!.width / 2 - 80))}px` : null}>${[
      quick('bold', 'Bold', () => mark('strong'), pressedMark('strong')), quick('italic', 'Italic', () => mark('em'), pressedMark('em')),
      quick('underline', 'Underline', () => mark('u'), pressedMark('u')), quick('code', 'Inline code', () => mark('code'), pressedMark('code')),
      quick('link', 'Link', () => linkDialog(linkButton), () => String(status.get().link))]}</div>` as HTMLElement;

  function updateFloating() {
    const fine = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: fine)').matches;
    if (!fine || !focused || collapsed(state.selection) || disabled() || composing) { if (floating.peek()) floating.set(null); return; }
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount) return;
    const rect = selection.getRangeAt(0).getBoundingClientRect?.();
    floating.set(rect && (rect.width || rect.height) ? rect : null);
  }

  // On a phone the toolbar rides above the on-screen keyboard while the editor has focus.
  const phone = cell(false);
  const keyboardBottom = cell(0);
  const placePhoneBar = () => {
    const viewport = window.visualViewport;
    keyboardBottom.set(viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0);
  };
  const isPhone = () => typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches && window.innerWidth < 640;

  const counter = props.maxLength ? html`<span class="rt-count" aria-live="polite" hidden=${() => status.get().length < props.maxLength! * 0.8}
    class:over=${() => status.get().length > props.maxLength!}>${() => `${status.get().length.toLocaleString('en')} / ${props.maxLength!.toLocaleString('en')}`}</span>` : null;

  const element = html`<div class="rt" class:rt-disabled=${disabled} class:rt-phone=${phone} class:rt-invalid=${() => Boolean(props.invalid?.())}
    style:--rt-keyboard=${() => `${keyboardBottom.get()}px`}>${toolbar}${surface}${float}${counter}${live}</div>` as HTMLElement;

  // ---------- events ----------

  listen(surface, 'beforeinput', onBeforeInput as EventListener);
  // A cancelled beforeinput changes nothing, so an input event means the browser edited the DOM itself
  // (an uncancellable phone keyboard event, or a composition the browser finished on its own).
  listen(surface, 'input', () => { if (!composing) reconcile(); });
  listen(surface, 'compositionstart', () => {
    composing = true;
    history.close();
    // Typing over a selection: take the selected text out first, so the composition replaces it in the model too.
    syncSelection();
    floating.set(null);
  });
  listen(surface, 'compositionend', () => {
    composing = false;
    reconcile();
  });
  listen(surface, 'keydown', onKeyDown as EventListener);
  listen(surface, 'paste', ((event: ClipboardEvent) => {
    event.preventDefault();
    if (disabled() || !event.clipboardData) return;
    syncSelection();
    paste(event.clipboardData, state.selection);
  }) as EventListener);
  listen(surface, 'copy', (event => copy(event as ClipboardEvent, false)) as EventListener);
  listen(surface, 'cut', (event => copy(event as ClipboardEvent, true)) as EventListener);
  // Dragging text inside the editor would move DOM the model doesn't know about; drops from outside insert at the drop point.
  listen(surface, 'dragstart', event => event.preventDefault());
  listen(surface, 'drop', ((event: DragEvent) => {
    event.preventDefault();
    if (disabled() || !event.dataTransfer) return;
    const point = dropPoint(event);
    const at = point ? pointToPos(surface, point[0], point[1]) : null;
    paste(event.dataTransfer, at ? caret(clampSelection(state.doc, caret(at)).head) : state.selection);
  }) as EventListener);
  listen(surface, 'mousedown', ((event: MouseEvent) => {
    const box = (event.target as Element).closest?.('.rt-check');
    if (!box || disabled()) return;
    event.preventDefault();
    const index = elements.indexOf(box.parentElement as HTMLElement);
    if (index >= 0) { const checked = !(state.doc.blocks[index] as { checked?: boolean }).checked; apply(toggleChecked(state, index), { message: checked ? 'Done' : 'Not done' }); }
  }) as EventListener);
  listen(surface, 'click', ((event: MouseEvent) => {
    // Links open with Ctrl or Cmd held; a plain click places the caret.
    const link = (event.target as Element).closest?.('a[href]') as HTMLAnchorElement | null;
    if (!link) return;
    event.preventDefault();
    if (isMac() ? event.metaKey : event.ctrlKey) window.open(link.href, '_blank', 'noopener,noreferrer');
  }) as EventListener);
  listen(surface, 'focus', () => {
    focused = true;
    writeSelection();
    if (isPhone()) { phone.set(true); placePhoneBar(); }
    refreshStatus();
  });
  listen(surface, 'blur', () => { focused = false; floating.set(null); phone.set(false); history.close(); });
  listen(document, 'selectionchange', () => {
    if (rendering || composing || document.activeElement !== surface) return;
    syncSelection();
    updateFloating();
  });
  if (window.visualViewport) {
    listen(window.visualViewport, 'resize', () => { if (phone.peek()) placePhoneBar(); });
    listen(window.visualViewport, 'scroll', () => { if (phone.peek()) placePhoneBar(); });
  }

  // A value from outside (the record was reloaded, Undo in a toast) replaces the document.
  effect(() => {
    const value = props.value() ?? null;
    untrack(() => {
      if (value === lastEmitted) return;
      if (lastEmitted !== undefined && value !== null && lastEmitted !== null && canonical(value) === lastEmitted) { lastEmitted = value; return; }
      if (composing) return;
      load(value);
    });
  });

  return {
    element, surface,
    focus: () => { surface.focus(); writeSelection(); },
    state: () => state,
  };
}

function dropPoint(event: DragEvent): [Node, number] | null {
  const doc = document as Document & { caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null; caretRangeFromPoint?: (x: number, y: number) => Range | null };
  const position = doc.caretPositionFromPoint?.(event.clientX, event.clientY);
  if (position) return [position.offsetNode, position.offset];
  const range = doc.caretRangeFromPoint?.(event.clientX, event.clientY);
  return range ? [range.startContainer, range.startOffset] : null;
}
