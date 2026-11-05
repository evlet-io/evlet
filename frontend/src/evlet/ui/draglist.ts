// dragList: reorder items in one or more lists by mouse, touch or keyboard. Items carry
// data-drag-item="<kind>" and data-drag-id; the lists they can sit in carry data-drop-zone="<kind>"
// and data-zone; each item has a handle, data-drag-handle="<kind>", that is a real button.
//
// Pointer: press on a handle and move (mouse, pen or touch); the item under the pointer shows
// where it will land, and letting go moves it. Keyboard: Space or Enter on a handle lifts the
// item, arrow keys move it (across list edges), Space or Enter drops it and Esc puts it back.
// Each step is announced in a polite live region. Moves go through `move`, and the caller
// re-renders; listeners sit on `root`, so the lists inside it may be redrawn freely.

import { onCleanup } from '../reactive';

export interface DragListOptions {
  /** Which items, zones and handles this list moves: data-drag-item, data-drop-zone and data-drag-handle values. */
  kind: string;
  /** Put item `id` in zone `zone`, before item `before` (null: at the end). */
  move: (id: string, zone: string, before: string | null) => void;
  /** How an item is named in announcements. */
  label?: (id: string) => string;
  /** How a zone is named in announcements, when there is more than one. */
  zoneLabel?: (zone: string) => string;
}

/** Pixels the pointer moves before a press becomes a drag (a plain click stays a click). */
const THRESHOLD = 4;

export function dragList(root: HTMLElement, options: DragListOptions): () => void {
  const { kind } = options;
  const live = document.createElement('div');
  live.className = 'sr-only';
  live.setAttribute('aria-live', 'polite');
  live.setAttribute('data-drag-live', kind);
  root.append(live);
  const say = (text: string) => { live.textContent = text; };
  const name = (id: string) => options.label?.(id) ?? id;

  const items = () => [...root.querySelectorAll<HTMLElement>(`[data-drag-item="${kind}"]`)];
  const zones = () => [...root.querySelectorAll<HTMLElement>(`[data-drop-zone="${kind}"]`)];
  const itemOf = (id: string) => items().find(item => item.dataset.dragId === id) ?? null;
  const zoneOf = (item: HTMLElement) => item.closest<HTMLElement>(`[data-drop-zone="${kind}"]`)!;
  const inZone = (zone: HTMLElement) => items().filter(item => zoneOf(item) === zone);
  /** Where an item is now: its zone and the item after it. */
  const place = (id: string) => {
    const item = itemOf(id)!, zone = zoneOf(item), list = inZone(zone), index = list.indexOf(item);
    return { zone: zone.dataset.zone ?? '', before: list[index + 1]?.dataset.dragId ?? null, index, count: list.length, zoneElement: zone };
  };
  const where = (id: string) => {
    const now = place(id), many = zones().length > 1;
    return `${name(id)}, position ${now.index + 1} of ${now.count}${many ? ` in ${options.zoneLabel?.(now.zone) ?? now.zone}` : ''}`;
  };
  const focusHandle = (id: string) => itemOf(id)?.querySelector<HTMLElement>(`[data-drag-handle="${kind}"]`)?.focus();

  // ---------- keyboard ----------

  let lifted: { id: string; zone: string; before: string | null } | null = null;
  const mark = (on: boolean) => {
    for (const item of items()) item.classList.toggle('drag-lifted', on && item.dataset.dragId === lifted?.id);
    if (lifted) itemOf(lifted.id)?.querySelector(`[data-drag-handle="${kind}"]`)?.setAttribute('aria-pressed', String(on));
  };
  const step = (id: string, delta: -1 | 1) => {
    const now = place(id), all = zones(), zoneIndex = all.indexOf(now.zoneElement), list = inZone(now.zoneElement).filter(item => item.dataset.dragId !== id);
    const target = now.index + delta;
    if (target >= 0 && target <= list.length) {
      if (target === now.index) return false;
      options.move(id, now.zone, list[target]?.dataset.dragId ?? null);
    } else {
      // Past a list's edge: the end of the list before, or the start of the list after.
      const next = all[zoneIndex + delta];
      if (!next) return false;
      const others = inZone(next);
      options.move(id, next.dataset.zone ?? '', delta < 0 ? null : others[0]?.dataset.dragId ?? null);
    }
    return true;
  };
  const keydown = (event: KeyboardEvent) => {
    const handle = (event.target as Element).closest?.(`[data-drag-handle="${kind}"]`);
    const item = handle?.closest<HTMLElement>(`[data-drag-item="${kind}"]`);
    if (!handle || !item) return;
    const id = item.dataset.dragId!;
    if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      if (lifted?.id === id) { mark(false); lifted = null; say(`${name(id)} dropped. ${where(id)}.`); }
      else { lifted = { id, ...place(id) }; mark(true); say(`${name(id)} lifted. ${where(id)}. Use the arrow keys to move, Space to drop, Escape to cancel.`); }
    } else if (lifted?.id === id && ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
      event.preventDefault();
      if (step(id, event.key === 'ArrowUp' || event.key === 'ArrowLeft' ? -1 : 1)) { focusHandle(id); mark(true); say(where(id)); }
    } else if (lifted?.id === id && event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      const back = lifted;
      lifted = null;
      options.move(id, back.zone, back.before);
      focusHandle(id);
      mark(false);
      say(`Move cancelled. ${where(id)}.`);
    }
  };
  const focusout = (event: FocusEvent) => {
    // Leaving a lifted item drops it where it is.
    if (!lifted || (event.relatedTarget as Element | null)?.closest?.(`[data-drag-item="${kind}"]`)?.getAttribute('data-drag-id') === lifted.id) return;
    const id = lifted.id;
    queueMicrotask(() => { if (lifted?.id === id && !itemOf(id)?.contains(document.activeElement)) { mark(false); lifted = null; } });
  };

  // ---------- pointer (mouse, pen, touch) ----------

  let press: { id: string; x: number; y: number; pointer: number; dragging: boolean } | null = null;
  let target: { zone: string; before: string | null } | null = null;
  const clearMarks = () => root.querySelectorAll('.drag-over-before, .drag-over-after, .drag-over-zone, .drag-source')
    .forEach(element => element.classList.remove('drag-over-before', 'drag-over-after', 'drag-over-zone', 'drag-source'));
  const aim = (x: number, y: number) => {
    const hit = document.elementFromPoint?.(x, y) as Element | null;
    clearMarks();
    itemOf(press!.id)?.classList.add('drag-source');
    const over = hit?.closest<HTMLElement>(`[data-drag-item="${kind}"]`);
    if (over && over.dataset.dragId !== press!.id && root.contains(over)) {
      const box = over.getBoundingClientRect(), zone = zoneOf(over);
      const others = inZone(zone).filter(item => item.dataset.dragId !== press!.id);
      // Two columns of items: left or right half decides; one column: top or bottom half.
      const after = box.width && box.width < zone.getBoundingClientRect().width * 0.75 ? x > box.left + box.width / 2 : y > box.top + box.height / 2;
      over.classList.add(after ? 'drag-over-after' : 'drag-over-before');
      target = { zone: zone.dataset.zone ?? '', before: after ? others[others.indexOf(over) + 1]?.dataset.dragId ?? null : over.dataset.dragId ?? null };
      return;
    }
    const zone = hit?.closest<HTMLElement>(`[data-drop-zone="${kind}"]`);
    if (zone && root.contains(zone)) { zone.classList.add('drag-over-zone'); target = { zone: zone.dataset.zone ?? '', before: null }; return; }
    target = null;
  };
  const pointerdown = (event: PointerEvent) => {
    const handle = (event.target as Element).closest?.(`[data-drag-handle="${kind}"]`) as HTMLElement | null;
    const item = handle?.closest<HTMLElement>(`[data-drag-item="${kind}"]`);
    if (!handle || !item || (event.button !== undefined && event.button > 0)) return;
    press = { id: item.dataset.dragId!, x: event.clientX, y: event.clientY, pointer: event.pointerId, dragging: false };
    try { handle.setPointerCapture?.(event.pointerId); } catch { /* not every pointer can be captured */ }
  };
  const pointermove = (event: PointerEvent) => {
    if (!press || event.pointerId !== press.pointer) return;
    if (!press.dragging && Math.hypot(event.clientX - press.x, event.clientY - press.y) < THRESHOLD) return;
    if (!press.dragging) { press.dragging = true; root.classList.add('drag-active'); say(`${name(press.id)} picked up.`); }
    event.preventDefault();
    aim(event.clientX, event.clientY);
  };
  const pointerup = (event: PointerEvent) => {
    if (!press || event.pointerId !== press.pointer) return;
    const done = press;
    press = null;
    root.classList.remove('drag-active');
    clearMarks();
    if (!done.dragging) return;
    // The click that ends a drag is not a click on the handle.
    root.addEventListener('click', swallow, { capture: true, once: true });
    setTimeout(() => root.removeEventListener('click', swallow, { capture: true }), 0);
    if (event.type === 'pointercancel' || !target) { say('Move cancelled.'); target = null; return; }
    const to = target;
    target = null;
    options.move(done.id, to.zone, to.before);
    say(`${name(done.id)} dropped. ${where(done.id)}.`);
  };
  const swallow = (event: Event) => { event.stopPropagation(); event.preventDefault(); };

  root.addEventListener('keydown', keydown);
  root.addEventListener('focusout', focusout);
  root.addEventListener('pointerdown', pointerdown);
  root.addEventListener('pointermove', pointermove);
  root.addEventListener('pointerup', pointerup);
  root.addEventListener('pointercancel', pointerup);
  const stop = () => {
    root.removeEventListener('keydown', keydown);
    root.removeEventListener('focusout', focusout);
    root.removeEventListener('pointerdown', pointerdown);
    root.removeEventListener('pointermove', pointermove);
    root.removeEventListener('pointerup', pointerup);
    root.removeEventListener('pointercancel', pointerup);
    live.remove();
  };
  onCleanup(stop);
  return stop;
}
