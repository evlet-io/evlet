// The RichTextEditor's controls around its surface (richtext/editor.ts): the formatting toolbar, the floating
// selection toolbar, the @mention list and its warnings, the image's alt text and caption popover, and the
// keyboard shortcuts. They call the editor through its commands and read its status; the document state stays in
// the editor.

import { html, type Child } from '../dom';
import { cell, type Cell, type Readable } from '../reactive';
import { Icon } from '../ui/base';
import { openPopover } from '../ui/layers';
import { blockText, caret, collapsed, linkAt, marksAt, pos, text, textLength, type Mark } from './model';
import { blockMatches, hasMark, replaceRange, selectedBlocks, type BlockSpec, type State } from './ops';
import type { Person, RichTextProps } from './editor';

export const isMac = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const MARK_NAMES: Record<Mark, string> = { strong: 'Bold', em: 'Italic', u: 'Underline', s: 'Strikethrough', code: 'Inline code' };
export const BLOCK_NAMES: Array<[BlockSpec, string, string]> = [
  [{ type: 'heading', level: 2 }, 'Heading', 'heading-2'], [{ type: 'heading', level: 3 }, 'Subheading', 'heading-3'],
  [{ type: 'item', list: 'bullet' }, 'Bulleted list', 'list'], [{ type: 'item', list: 'ordered' }, 'Numbered list', 'list-ordered'],
  [{ type: 'item', list: 'check' }, 'Checklist', 'list-checks'], [{ type: 'quote' }, 'Quote', 'text-quote'], [{ type: 'code' }, 'Code block', 'square-code'],
];

/** What the toolbar buttons show: the marks and block at the selection, a link there, Undo and Redo, the length. */
export interface Status { marks: Mark[]; block: BlockSpec | null; link: boolean; undo: boolean; redo: boolean; length: number }

/** The status at a state: `stored` are the marks toggled at a caret, if any. */
export function statusOf(state: State, stored: Mark[] | null, history: { canUndo: boolean; canRedo: boolean }): Status {
  const head = state.doc.blocks[state.selection.head.block];
  const marks = collapsed(state.selection)
    ? (stored ?? marksAt(head.content, state.selection.head.offset).marks)
    : (['strong', 'em', 'u', 's', 'code'] as Mark[]).filter(mark => hasMark(state, mark));
  const blocks = selectedBlocks(state).map(index => state.doc.blocks[index]);
  const block = BLOCK_NAMES.map(([spec]) => spec).find(spec => blocks.length > 0 && blocks.every(item => blockMatches(item, spec))) ?? null;
  const link = Boolean(linkAt(head.content, state.selection.head.offset)) && head.type !== 'code';
  return { marks, block, link, undo: history.canUndo, redo: history.canRedo, length: textLength(state.doc) };
}

/** What the controls ask of the editor. */
export interface EditorCommands {
  status: Readable<Status>;
  disabled(): boolean;
  mark(name: Mark): void;
  block(spec: BlockSpec | 'rule'): void;
  undo(): void;
  redo(): void;
  linkDialog(anchor: HTMLElement): void;
  /** Type @ at the caret, which opens the people picker. */
  mention(): void;
  /** Open the file picker for an image. */
  pickImage(): void;
  /** Put the focus back in the surface with the model's selection, before a toolbar button runs. */
  syncSelection(): void;
}

/** The formatting toolbar, one Tab stop whose buttons the arrow keys move between, and its Link button (Mod+K opens
 * the link popover from it). */
export function editorToolbar(props: RichTextProps, id: string, ui: EditorCommands) {
  const { status } = ui;
  const pressedMark = (name: Mark) => () => String(status.get().marks.includes(name));
  const tool = (icon: string, label: string, shortcut: string, run: (button: HTMLElement) => void, pressed?: () => string | null, enabled: () => boolean = () => true) => {
    const button = html`<button type="button" class="rt-tool" tabindex="-1" aria-label=${shortcut ? `${label} (${shortcut})` : label} title=${shortcut ? `${label} (${shortcut})` : label}
      aria-pressed=${pressed ?? null} disabled=${() => ui.disabled() || !enabled()}
      on:mousedown=${(event: MouseEvent) => event.preventDefault()}
      on:click=${(event: MouseEvent) => { ui.syncSelection(); run(event.currentTarget as HTMLElement); }}>${Icon(icon, { size: 'sm' })}</button>` as HTMLButtonElement;
    return button;
  };
  const mod = isMac() ? '⌘' : 'Ctrl+';
  const linkButton = tool('link', 'Link', `${mod}K`, button => ui.linkDialog(button), () => String(status.get().link));
  const bulletSpec = BLOCK_NAMES.find(([spec]) => spec.type === 'item' && spec.list === 'bullet')!;
  const blockTool = ([spec, label, icon]: [BlockSpec, string, string]) => tool(icon, label, spec.type === 'item' && spec.list === 'ordered' ? `${mod}Shift+7` : spec.type === 'item' && spec.list === 'bullet' ? `${mod}Shift+8` : '',
    () => ui.block(spec), () => String(JSON.stringify(status.get().block) === JSON.stringify(spec)));
  // Mention types @ at the caret (a space first after a word), which opens the people picker.
  const mentionTool = () => tool('at-sign', 'Mention', '', () => ui.mention());
  const groups: HTMLElement[][] = props.toolbar === 'compact' ? [
    [tool('bold', 'Bold', `${mod}B`, () => ui.mark('strong'), pressedMark('strong')), tool('italic', 'Italic', `${mod}I`, () => ui.mark('em'), pressedMark('em')),
      blockTool(bulletSpec), linkButton, ...(props.mentions ? [mentionTool()] : [])],
  ] : [
    [tool('bold', 'Bold', `${mod}B`, () => ui.mark('strong'), pressedMark('strong')), tool('italic', 'Italic', `${mod}I`, () => ui.mark('em'), pressedMark('em')),
      tool('underline', 'Underline', `${mod}U`, () => ui.mark('u'), pressedMark('u')), tool('strikethrough', 'Strikethrough', '', () => ui.mark('s'), pressedMark('s')),
      tool('code', 'Inline code', '', () => ui.mark('code'), pressedMark('code')), linkButton],
    BLOCK_NAMES.map(blockTool),
    [...(props.images ? [tool('image', 'Image', '', () => ui.pickImage())] : []), tool('minus', 'Divider', '', () => ui.block('rule')),
      tool('undo-2', 'Undo', `${mod}Z`, () => ui.undo(), undefined, () => status.get().undo), tool('redo-2', 'Redo', isMac() ? '⌘Shift+Z' : 'Ctrl+Y', () => ui.redo(), undefined, () => status.get().redo)],
  ];
  const tools = groups.flat();
  tools[0].tabIndex = 0;
  // A toolbar is one Tab stop; the arrow keys move between its buttons (roving tabindex).
  const toolbar = html`<div class=${props.toolbar === 'compact' ? 'rt-toolbar rt-toolbar-compact' : 'rt-toolbar'} role="toolbar" aria-label="Formatting" aria-controls=${id}
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
  return { toolbar, linkButton, pressedMark };
}

/** The floating selection toolbar (desktop): a few marks right above selected text. Mouse only; the main toolbar has
 * the same buttons for the keyboard. */
export function floatingBar(floating: Readable<DOMRect | null>, ui: EditorCommands, linkButton: HTMLElement, pressedMark: (name: Mark) => () => string) {
  const quick = (icon: string, label: string, run: (button: HTMLElement) => void, pressed?: () => string | null) => html`<button type="button" class="rt-tool" tabindex="-1"
    aria-label=${label} aria-pressed=${pressed ?? null} on:mousedown=${(event: MouseEvent) => event.preventDefault()}
    on:click=${(event: MouseEvent) => run(event.currentTarget as HTMLElement)}>${Icon(icon, { size: 'sm' })}</button>` as HTMLElement;
  return html`<div class="rt-float" aria-hidden="true" hidden=${() => !floating.get()}
    style:top=${() => floating.get() ? `${Math.max(8, floating.get()!.top - 42)}px` : null}
    style:left=${() => floating.get() ? `${Math.max(8, Math.min(window.innerWidth - 168, floating.get()!.left + floating.get()!.width / 2 - 80))}px` : null}>${[
      quick('bold', 'Bold', () => ui.mark('strong'), pressedMark('strong')), quick('italic', 'Italic', () => ui.mark('em'), pressedMark('em')),
      quick('underline', 'Underline', () => ui.mark('u'), pressedMark('u')), quick('code', 'Inline code', () => ui.mark('code'), pressedMark('code')),
      quick('link', 'Link', () => ui.linkDialog(linkButton), () => String(ui.status.get().link))]}</div>` as HTMLElement;
}

/** The people picker open after @: where it started, what was typed, the people found and the one chosen. */
export interface Picking { block: number; start: number; query: string; items: Person[]; active: number }

/** What the people picker needs of the editor. */
export interface MentionHost {
  state(): State;
  composing(): boolean;
  apply(next: State, options?: { message?: string }): void;
  announce(message: string): void;
  surface: HTMLElement;
}

/** @mentions: the picker that opens after @ at a word start, the search as the query grows, the chosen person put in
 * place of @query, and the people mentioned who can't open the record. */
export function mentionPicker(props: RichTextProps, id: string, ed: MentionHost) {
  const picking = cell<Picking | null>(null);
  const known = new Map<string, Person>();
  const mentioned = cell<Person[]>([]);
  const listId = `${id}-people`;
  let searchTimer = 0, searchToken = 0;
  const closePicker = () => { if (picking.peek()) { picking.set(null); searchToken++; } };
  /** Open, update or close the picker from the text before the caret: @ at a word start, then the query. */
  const check = () => {
    const state = ed.state();
    if (!props.mentions || ed.composing() || !collapsed(state.selection)) { closePicker(); return; }
    const at = state.selection.head, block = state.doc.blocks[at.block];
    if (!block || block.type === 'code' || block.type === 'rule' || block.type === 'image') { closePicker(); return; }
    const before = blockText(block).slice(0, at.offset);
    const match = /(?:^|[\s(\uE000\uE001])@([^\s@\uE000\uE001]{0,30})$/.exec(before);
    if (!match) { closePicker(); return; }
    const start = at.offset - match[1].length - 1, query = match[1];
    const current = picking.peek();
    if (current && current.block === at.block && current.start === start && current.query === query) return;
    picking.set({ block: at.block, start, query, items: current?.items ?? [], active: 0 });
    window.clearTimeout(searchTimer);
    const token = ++searchToken;
    searchTimer = window.setTimeout(() => {
      void props.mentions!.search(query).then(items => {
        if (token !== searchToken || !picking.peek()) return;
        picking.set({ ...picking.peek()!, items: items.slice(0, 8), active: 0 });
        ed.announce(items.length ? `${Math.min(items.length, 8)} ${items.length === 1 ? 'person' : 'people'}. Up and down to choose, Enter to mention.` : 'No one matches');
      }, () => { if (token === searchToken && picking.peek()) picking.set({ ...picking.peek()!, items: [] }); });
    }, 120);
  };
  /** Put the chosen person in place of @query, then a space. */
  const choose = (person: Person) => {
    const current = picking.peek();
    if (!current) return;
    known.set(person.id, person);
    const state = ed.state();
    const range = { anchor: pos(current.block, current.start), head: state.selection.head };
    const { doc, end } = replaceRange(state.doc, range.anchor, range.head, [{ kind: 'mention', id: person.id, name: person.name }, text(' ')]);
    closePicker();
    ed.apply({ doc, selection: caret(end) }, { message: person.can_read ? `Mentioned ${person.name}` : `Mentioned ${person.name}, who can't open this record` });
  };
  /** People mentioned in the text that the picker said cannot open the record. */
  const refresh = () => {
    const ids = new Set<string>();
    for (const block of ed.state().doc.blocks) for (const piece of block.content) if (piece.kind === 'mention') ids.add(piece.id);
    mentioned.set([...ids].map(item => known.get(item)).filter((person): person is Person => Boolean(person) && !person!.can_read));
  };
  const caretRect = (): DOMRect | null => {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount) return null;
    const rect = selection.getRangeAt(0).getBoundingClientRect?.();
    return rect && (rect.width || rect.height || rect.top) ? rect : ed.surface.getBoundingClientRect();
  };
  return { picking, check, choose, closePicker, refresh,
    list: props.mentions ? mentionList(listId, picking, caretRect, choose) : null, warnings: props.mentions ? mentionWarnings(mentioned) : null };
}

/** The list of people to mention, under the caret. */
export function mentionList(listId: string, picking: Readable<Picking | null>, caretRect: () => DOMRect | null, choose: (person: Person) => void) {
  return html`<div class="rt-mentions pop show" role="listbox" id=${listId} aria-label="People to mention"
    hidden=${() => !picking.get() || !picking.get()!.items.length}
    style:top=${() => { void picking.get(); const rect = caretRect(); return rect ? `${rect.bottom + 6}px` : null; }}
    style:left=${() => { void picking.get(); const rect = caretRect(); return rect ? `${Math.max(8, Math.min(window.innerWidth - 280, rect.left))}px` : null; }}>${() =>
      (picking.get()?.items ?? []).map((person, index) => html`<button type="button" class="mi rt-person" role="option" id=${`${listId}-${index}`} tabindex="-1"
        aria-selected=${() => String(picking.get()?.active === index)} class:on=${() => picking.get()?.active === index}
        on:mousedown=${(event: MouseEvent) => event.preventDefault()} on:click=${() => choose(person)}>
        <span class="rt-person-name">${person.name}</span><span class="muted">@${person.username}</span>${person.can_read ? null
          : html`<span class="rt-person-warn">Can't open this record</span>`}</button>`)}</div>` as HTMLElement;
}

/** People mentioned who can't open the record: they are only told they were mentioned. */
export function mentionWarnings(mentioned: Readable<Person[]>) {
  return html`<div class="rt-warn" role="status">${() => mentioned.get().map(person =>
    html`<p>${Icon('info', { size: 'xs' })} ${person.name} can't open this record. They'll only be told they were mentioned.</p>`)}</div>` as HTMLElement;
}

/** The picker's keys while it lists people: the arrows choose, Enter or Tab mentions, Esc closes. True when handled. */
export function pickerKey(event: KeyboardEvent, picking: Cell<Picking | null>, announce: (message: string) => void, choose: (person: Person) => void, close: () => void): boolean {
  const open = picking.peek();
  if (!open || !open.items.length) return false;
  const count = open.items.length;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const active = (open.active + (event.key === 'ArrowDown' ? 1 : count - 1)) % count;
    picking.set({ ...open, active });
    const person = open.items[active];
    announce(`${person.name}${person.can_read ? '' : ", can't open this record"}`);
    return true;
  }
  if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); event.stopPropagation(); choose(open.items[open.active]); return true; }
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return true; }
  return false;
}

/** Mod+B, I, U, K, Z, Shift+Z or Y, Shift+7, 8 or 9 and Mod+Enter, and Tab in a list (indent): the editor's own keys. */
export function shortcutKey(event: KeyboardEvent, ui: EditorCommands, linkButton: HTMLElement, list: { inList(): boolean; indent(back: boolean): void }) {
  const mod = isMac() ? event.metaKey : event.ctrlKey;
  const key = event.key.toLowerCase();
  if (mod && !event.altKey) {
    if (key === 'b' || key === 'i' || key === 'u') { event.preventDefault(); if (!ui.disabled()) ui.mark(key === 'b' ? 'strong' : key === 'i' ? 'em' : 'u'); return; }
    // Ctrl or Cmd K is the link inside the editor, not the app's command palette.
    if (key === 'k') { event.preventDefault(); event.stopPropagation(); if (!ui.disabled()) ui.linkDialog(linkButton); return; }
    if (key === 'z' && !event.shiftKey) { event.preventDefault(); ui.undo(); return; }
    if ((key === 'z' && event.shiftKey) || key === 'y') { event.preventDefault(); ui.redo(); return; }
    if (event.shiftKey && (event.code === 'Digit7' || event.code === 'Digit8' || event.code === 'Digit9')) {
      event.preventDefault();
      if (!ui.disabled()) ui.block({ type: 'item', list: event.code === 'Digit7' ? 'ordered' : event.code === 'Digit8' ? 'bullet' : 'check' });
      return;
    }
    // Ctrl or Cmd Enter is for the form around the editor (save), not a new line.
    if (event.key === 'Enter') { event.preventDefault(); return; }
  }
  if (event.key === 'Tab' && !mod && !event.altKey) {
    // Tab indents only inside lists; anywhere else it leaves the editor, as Tab should.
    if (list.inList()) {
      event.preventDefault();
      if (!ui.disabled()) list.indent(event.shiftKey);
    }
  }
}

/** The image's alt text and caption, in a popover by its tool. */
export function imagePopover(anchor: HTMLElement, current: { alt: string; caption: string }, save: (values: { alt: string; caption: string }) => void, surface: HTMLElement) {
  openPopover(anchor, close => {
    const alt = html`<input class="rt-link-in" maxlength="300" autocomplete="off" aria-label="Alt text">` as HTMLInputElement;
    const caption = html`<input class="rt-link-in" maxlength="300" autocomplete="off" aria-label="Caption (optional)">` as HTMLInputElement;
    alt.value = current.alt; caption.value = current.caption;
    queueMicrotask(() => alt.focus());
    return html`<form class="rt-link" novalidate on:submit=${(event: Event) => {
      event.preventDefault();
      close();
      save({ alt: alt.value.replace(/\s+/g, ' ').trim(), caption: caption.value.replace(/\s+/g, ' ').trim() });
      surface.focus();
    }}><label class="rt-link-l">Alt text</label>${alt}<span class="muted rt-hint">Says what the image shows, for people who can't see it.</span>
      <label class="rt-link-l">Caption (optional)</label>${caption}<div class="rt-link-b"><button type="submit" class="btn primary small">Save</button></div></form>` as Child;
  }, { role: 'dialog', label: 'Image', onClose: () => { if (!surface.contains(document.activeElement)) surface.focus(); } });
}
