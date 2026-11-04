// SplitView: a main area and a side panel beside it, with a divider to resize the panel. Lists use
// it to open a record beside the narrowed list. The width is a cell (the caller keeps it, for
// example in the person's layout preferences). When the container is narrower than `breakpoint`
// the panel covers the main area instead, and `overlay` says so.

import { html, when, type Child } from '../dom';
import { cell, onCleanup, untrack, type Cell, type Readable } from '../reactive';

export interface SplitViewProps {
  main: Child;
  /** The panel, rendered while `open` is true. */
  side: () => Child;
  open: Readable<boolean>;
  /** Panel width in pixels. Dragging and the arrow keys write it. */
  width: Cell<number>;
  /** Accessible name of the divider: "Resize the invoice panel". */
  label: string;
  /** Width the divider resets to on double click (default 480). */
  initial?: number;
  minSide?: number;
  minMain?: number;
  /** Largest share of the container the panel may take (default 0.7). */
  maxShare?: number;
  /** Below this container width the panel covers the main area (default 900). */
  breakpoint?: number;
  /** Set to true while the panel covers the main area. */
  overlay?: Cell<boolean>;
}

const STEP = 24;

/** Main area and resizable side panel; the panel covers the main area on narrow screens. */
export function SplitView(props: SplitViewProps): HTMLElement {
  const minSide = props.minSide ?? 380, minMain = props.minMain ?? 320, share = props.maxShare ?? 0.7, initial = props.initial ?? 480;
  const breakpoint = props.breakpoint ?? 900;
  const overlay = props.overlay ?? cell(false, { name: 'split (overlay)' });
  const room = cell(0, { name: 'split (container width)' });
  /** The width the panel can take now: at least minSide, leaving minMain to the main area. */
  const fit = (value: number) => {
    const total = room.peek();
    if (!total) return Math.max(minSide, Math.round(value));
    const most = Math.max(minSide, Math.min(total - minMain, Math.floor(total * share)));
    return Math.round(Math.min(most, Math.max(minSide, value)));
  };
  const most = () => { const total = room.get(); return total ? Math.max(minSide, Math.min(total - minMain, Math.floor(total * share))) : null; };
  const shown = () => { room.get(); return fit(props.width.get()); };

  const divider = html`<div class="split-div" role="separator" tabindex="0" aria-orientation="vertical" aria-label=${props.label}
    aria-valuemin=${String(minSide)} aria-valuemax=${() => most() === null ? null : String(most())} aria-valuenow=${() => String(shown())}
    title="Drag to resize. Double click to reset."></div>` as HTMLElement;
  const set = (value: number) => props.width.set(fit(value));
  divider.addEventListener('keydown', event => {
    const moves: Record<string, number> = { ArrowLeft: STEP, ArrowRight: -STEP };
    if (event.key in moves) { event.preventDefault(); set(untrack(shown) + moves[event.key]); }
    else if (event.key === 'Home') { event.preventDefault(); set(Number.MAX_SAFE_INTEGER); }
    else if (event.key === 'End') { event.preventDefault(); set(minSide); }
  });
  divider.addEventListener('dblclick', () => set(initial));
  divider.addEventListener('pointerdown', event => {
    event.preventDefault();
    const right = root.getBoundingClientRect().right;
    divider.setPointerCapture?.(event.pointerId);
    divider.classList.add('drag');
    const move = (next: PointerEvent) => set(right - next.clientX);
    const up = () => { divider.classList.remove('drag'); divider.removeEventListener('pointermove', move); divider.removeEventListener('pointerup', up); divider.removeEventListener('pointercancel', up); };
    divider.addEventListener('pointermove', move);
    divider.addEventListener('pointerup', up);
    divider.addEventListener('pointercancel', up);
  });

  const root = html`<div class="split" class:split-open=${props.open} class:split-over=${overlay}>
    <div class="split-main">${props.main}</div>
    ${when(props.open, () => [divider, html`<div class="split-side" style:width=${() => overlay.get() ? null : `${shown()}px`}>${props.side()}</div>`])}</div>` as HTMLElement;

  // Measure the container; without ResizeObserver (tests, old browsers) the panel keeps its width beside the main area.
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(entries => {
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      room.set(width);
      overlay.set(width > 0 && width < breakpoint);
    });
    observer.observe(root);
    onCleanup(() => observer.disconnect());
  }
  return root;
}
