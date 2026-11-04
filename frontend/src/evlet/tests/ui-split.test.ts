import { afterEach, describe, expect, it } from 'vitest';
import { html } from '../dom';
import { cell } from '../reactive';
import { SplitView } from '../ui/split';
import { cleanup, key, render } from './ui-helpers';

afterEach(() => { cleanup(); delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver; });

/** A ResizeObserver whose callback a test calls with the container width. */
function observe() {
  const callbacks: Array<(entries: Array<{ contentRect: { width: number } }>) => void> = [];
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class { constructor(callback: never) { callbacks.push(callback); } observe() {} disconnect() {} };
  return (width: number) => callbacks.forEach(callback => callback([{ contentRect: { width } }]));
}

describe('SplitView', () => {
  it('shows the panel while open, beside a separator the arrow keys resize, and double click resets', () => {
    const open = cell(false), width = cell(500);
    const host = render(() => SplitView({ main: html`<p>List</p>`, side: () => html`<p>Panel</p>`, open, width, label: 'Resize the panel', initial: 480 }));
    expect(host.querySelector('.split-main')!.textContent).toBe('List');
    expect(host.querySelector('.split-side')).toBeNull();
    open.set(true);
    const divider = host.querySelector<HTMLElement>('.split-div')!, side = host.querySelector<HTMLElement>('.split-side')!;
    expect(side.textContent).toBe('Panel');
    expect(divider.getAttribute('role')).toBe('separator');
    expect(divider.getAttribute('aria-orientation')).toBe('vertical');
    expect(divider.getAttribute('aria-label')).toBe('Resize the panel');
    expect(divider.getAttribute('aria-valuenow')).toBe('500');
    expect(side.style.width).toBe('500px');
    key(divider, 'ArrowLeft');
    expect(width.peek()).toBe(524);
    key(divider, 'ArrowRight');
    key(divider, 'ArrowRight');
    expect(width.peek()).toBe(476);
    key(divider, 'End');
    expect(width.peek()).toBe(380);
    divider.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(width.peek()).toBe(480);
    expect(divider.getAttribute('aria-valuenow')).toBe('480');
    open.set(false);
    expect(host.querySelector('.split-div')).toBeNull();
  });

  it('keeps room for the main area, and covers it below the breakpoint', () => {
    const resize = observe();
    const open = cell(true), width = cell(900), overlay = cell(false);
    const host = render(() => SplitView({ main: html`<p>List</p>`, side: () => html`<p>Panel</p>`, open, width, overlay, label: 'Resize the panel' }));
    resize(1000);
    const divider = host.querySelector<HTMLElement>('.split-div')!, side = host.querySelector<HTMLElement>('.split-side')!;
    // At most 70 percent, leaving 320 pixels to the list; the saved width is kept for a wider screen.
    expect(divider.getAttribute('aria-valuemax')).toBe('680');
    expect(side.style.width).toBe('680px');
    expect(width.peek()).toBe(900);
    key(divider, 'Home');
    expect(width.peek()).toBe(680);
    resize(800);
    expect(overlay.peek()).toBe(true);
    expect(host.querySelector('.split')!.classList.contains('split-over')).toBe(true);
    expect(side.style.width).toBe('');
  });
});
