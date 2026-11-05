// Field, section and tab order: column flow in the DOM, the shared drag list (mouse, touch and
// keyboard), Enter moving to the next field, and read-only values out of the Tab order.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { html } from '../dom';
import { cell } from '../reactive';
import { dragList } from '../ui/draglist';
import { enterMovesToNextField, fieldFor, localField } from '../ui/fields';
import { flowGrid } from '../ui/fold';
import { FormSection } from '../ui/inline';
import { cleanup, render } from './ui-helpers';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const order = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('[data-field]')].map(item => item.dataset.field);
const node = (name: string) => html`<div class="ff" data-field=${name}><input aria-label=${name}></div>` as HTMLElement;

describe('section flow', () => {
  it('rows keeps the declared order in one grid: across, then down', () => {
    const host = render(() => flowGrid(['a', 'b', 'c', 'd'].map(name => ({ node: node(name), wide: false })), 'rows'));
    expect(host.querySelectorAll('.fs-colrun')).toHaveLength(0);
    expect(order(host)).toEqual(['a', 'b', 'c', 'd']);
  });

  it('columns goes down the left column, then the right, and a full-width field breaks the run', () => {
    const cells = [['a', false], ['b', false], ['c', false], ['notes', true], ['d', false], ['e', false]] as const;
    const host = render(() => flowGrid(cells.map(([name, wide]) => ({ node: node(name), wide })), 'columns'));
    const runs = [...host.querySelectorAll('.fs-colrun')];
    expect(runs.map(run => [...run.querySelectorAll('.fs-col')].map(column => order(column)))).toEqual([[['a', 'b'], ['c']], [['d'], ['e']]]);
    // DOM order, which Tab follows, is the visual order: a, b down the left, c on the right, the break, then d, e.
    expect(order(host)).toEqual(['a', 'b', 'c', 'notes', 'd', 'e']);
    expect(host.querySelector('.fs-cols > [data-field="notes"]')).not.toBeNull();
  });

  it('FormSection follows the flow it is given', () => {
    const fields = ['one', 'two', 'three', 'four'].map(name => localField({ name, type: 'text', label: name }, 'x'));
    const host = render(() => FormSection({ title: 'Place', fields, flow: 'columns' }));
    expect([...host.querySelectorAll('.fs-col')].map(column => [...column.querySelectorAll<HTMLElement>('.ff')].map(item => item.dataset.field))).toEqual([['one', 'two'], ['three', 'four']]);
  });
});

describe('read-only values', () => {
  it('leave the Tab order but still take focus from a click', () => {
    const field = localField({ name: 'code', type: 'text', label: 'Code', read_only: true }, 'A-1');
    const calculated = localField({ name: 'total', type: 'decimal', label: 'Total', compute: { $expr: 'x' } as any }, '5');
    const host = render(() => FormSection({ title: 'Place', fields: [field, calculated] }));
    const values = [...host.querySelectorAll<HTMLElement>('.fval.ro')];
    expect(values).toHaveLength(2);
    expect(values.map(value => value.getAttribute('tabindex'))).toEqual(['-1', '-1']);
    expect([...host.querySelectorAll('.ff-hint')].every(hint => hint.getAttribute('tabindex') === '-1')).toBe(true);
    values[0].focus();
    expect(document.activeElement).toBe(values[0]);
  });
});

describe('Enter moves to the next field', () => {
  const press = (target: Element, init: KeyboardEventInit = {}) => {
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    return event;
  };
  const form = (on: () => boolean) => {
    const fields = [localField({ name: 'first', type: 'text', label: 'First' }), localField({ name: 'note', type: 'longtext', label: 'Note' }),
      localField({ name: 'locked', type: 'text', label: 'Locked', read_only: true }), localField({ name: 'last', type: 'text', label: 'Last' })];
    const host = render(() => html`<div>${fields.map(field => fieldFor({ field }))}</div>`);
    enterMovesToNextField(host, on);
    const control = (name: string) => host.querySelector<HTMLElement>(`[data-field="${name}"] input, [data-field="${name}"] textarea`)!;
    return { host, control };
  };

  it('moves forward with Enter and back with Shift+Enter, skipping fields that cannot change', () => {
    const { control } = form(() => true);
    control('first').focus();
    expect(press(control('first')).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(control('note'));
    // Enter in long text is a new line.
    expect(press(control('note')).defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(control('note'));
    control('last').focus();
    press(control('last'), { shiftKey: true });
    expect(document.activeElement).toBe(control('note'));
  });

  it('does nothing when the setting is off, or with Ctrl or Cmd (save)', () => {
    const on = cell(false);
    const { control } = form(() => on.get());
    control('first').focus();
    press(control('first'));
    expect(document.activeElement).toBe(control('first'));
    on.set(true);
    press(control('first'), { ctrlKey: true });
    expect(document.activeElement).toBe(control('first'));
    press(control('first'));
    expect(document.activeElement).toBe(control('note'));
  });
});

describe('dragList', () => {
  // Two lists of items, each with a grip button; moves are applied to the data and redrawn.
  function lists() {
    const data = cell<Record<string, string[]>>({ one: ['a', 'b', 'c'], two: ['d'] });
    const moves: Array<[string, string, string | null]> = [];
    const host = render(() => () => html`<div>${Object.entries(data.get()).map(([zone, items]) => html`<ul data-drop-zone="row" data-zone=${zone}>${items.map(id =>
      html`<li data-drag-item="row" data-drag-id=${id}><button type="button" data-drag-handle="row" aria-label=${`Move ${id}`}>⋮</button>${id}</li>`)}</ul>`)}</div>`);
    dragList(host, { kind: 'row', label: id => id.toUpperCase(), zoneLabel: zone => `list ${zone}`,
      move: (id, zone, before) => {
        moves.push([id, zone, before]);
        const next = Object.fromEntries(Object.entries(data.peek()).map(([key, items]) => [key, items.filter(item => item !== id)]));
        const target = next[zone], at = before ? target.indexOf(before) : -1;
        target.splice(at < 0 ? target.length : at, 0, id);
        data.set(next);
      } });
    const handle = (id: string) => host.querySelector<HTMLElement>(`[data-drag-id="${id}"] [data-drag-handle]`)!;
    const live = () => host.querySelector('[data-drag-live]')!.textContent;
    return { host, data, moves, handle, live };
  }
  const key = (target: Element, name: string) => target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));

  it('keyboard: Space lifts, arrows move across lists, Space drops, with announcements', () => {
    const { data, handle, live, host } = lists();
    handle('b').focus();
    key(handle('b'), ' ');
    expect(live()).toContain('B lifted. B, position 2 of 3 in list one');
    expect(host.querySelector('[data-drag-id="b"]')!.classList.contains('drag-lifted')).toBe(true);
    key(handle('b'), 'ArrowDown');
    expect(data.peek().one).toEqual(['a', 'c', 'b']);
    expect(document.activeElement).toBe(handle('b'));
    key(handle('b'), 'ArrowDown');
    expect(data.peek()).toEqual({ one: ['a', 'c'], two: ['b', 'd'] });
    expect(live()).toBe('B, position 1 of 2 in list two');
    key(handle('b'), ' ');
    expect(live()).toContain('B dropped');
    expect(host.querySelector('.drag-lifted')).toBeNull();
  });

  it('keyboard: Escape puts the item back where it was lifted', () => {
    const { data, handle, live } = lists();
    handle('a').focus();
    key(handle('a'), 'Enter');
    key(handle('a'), 'ArrowDown');
    key(handle('a'), 'ArrowDown');
    expect(data.peek().one).toEqual(['b', 'c', 'a']);
    key(handle('a'), 'Escape');
    expect(data.peek().one).toEqual(['a', 'b', 'c']);
    expect(live()).toContain('Move cancelled');
    expect(document.activeElement).toBe(handle('a'));
  });

  const pointer = (type: string, target: Element, x: number, y: number, pointerType = 'mouse') =>
    target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerId: 1, pointerType, button: 0 }));
  /** Lay the items out in one column, 20px apart, so the pointer can aim at them. */
  const layout = (host: HTMLElement) => {
    const rows = [...host.querySelectorAll<HTMLElement>('[data-drag-item]')];
    rows.forEach((row, index) => { row.getBoundingClientRect = () => ({ top: index * 20, bottom: index * 20 + 20, left: 0, right: 200, width: 200, height: 20, x: 0, y: index * 20, toJSON() {} }) as DOMRect; });
    for (const zone of host.querySelectorAll<HTMLElement>('[data-drop-zone]')) zone.getBoundingClientRect = () => ({ top: 0, bottom: 100, left: 0, right: 200, width: 200, height: 100, x: 0, y: 0, toJSON() {} }) as DOMRect;
    (document as any).elementFromPoint = (_x: number, y: number) => rows[Math.floor(y / 20)] ?? null;
  };

  for (const pointerType of ['mouse', 'touch']) {
    it(`${pointerType}: press a grip, move and let go to drop before or after the item under the pointer`, () => {
      const { data, handle, host, moves } = lists();
      layout(host);
      pointer('pointerdown', handle('a'), 5, 5, pointerType);
      // A move under the threshold is still a click.
      pointer('pointermove', handle('a'), 6, 6, pointerType);
      expect(host.classList.contains('drag-active')).toBe(false);
      // Over the lower half of c (third row): after c.
      pointer('pointermove', handle('a'), 5, 55, pointerType);
      expect(host.querySelector('[data-drag-id="c"]')!.classList.contains('drag-over-after')).toBe(true);
      pointer('pointerup', handle('a'), 5, 55, pointerType);
      expect(moves.at(-1)).toEqual(['a', 'one', null]);
      expect(data.peek().one).toEqual(['b', 'c', 'a']);
      expect(host.querySelector('.drag-over-after, .drag-source')).toBeNull();
    });
  }

  it('a press without a move is a click, and a cancelled pointer moves nothing', () => {
    const { handle, host, moves } = lists();
    layout(host);
    pointer('pointerdown', handle('b'), 5, 25);
    pointer('pointerup', handle('b'), 5, 25);
    pointer('pointerdown', handle('b'), 5, 25);
    pointer('pointermove', handle('b'), 5, 65);
    pointer('pointercancel', handle('b'), 5, 65);
    expect(moves).toEqual([]);
  });
});
