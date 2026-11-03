// Pickers: the choice list of a Select, the calendar of dates and date-times, the time and
// duration steppers and the date range with presets. Field controls open them in a popover
// (fields.ts); the text input stays, so typing a value works as before. A picker only makes
// picking easier: values keep the wire format and the server checks them again.
//
// Dates are ISO days (YYYY-MM-DD) in the person's zone, shown in their date format
// (DD-MM-YYYY by default). Weeks start on Monday, as in the UAE.

import { html, when, type Child } from '../dom';
import { cell, derive, untrack, type Cell } from '../reactive';
import { formatDate, todayGst } from '../model/format';
import { parseDay } from './dateparse';
import { Icon } from './base';
import { read, uid } from './core';
import { openPopover } from './layers';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const pad = (number: number) => String(number).padStart(2, '0');

// ---------- day arithmetic (ISO days, no time zone) ----------

const partsOf = (day: string) => day.split('-').map(Number) as [number, number, number];
export const isoDay = (year: number, month: number, date: number) => {
  const at = new Date(Date.UTC(year, month - 1, date));
  return `${at.getUTCFullYear()}-${pad(at.getUTCMonth() + 1)}-${pad(at.getUTCDate())}`;
};
/** The day `count` days after `day`. */
export const addDays = (day: string, count: number) => { const [year, month, date] = partsOf(day); return isoDay(year, month, date + count); };
/** The same day `count` months later, kept inside the month (31 Jan + 1 month is 28 or 29 Feb). */
export function addMonths(day: string, count: number): string {
  const [year, month, date] = partsOf(day);
  const first = new Date(Date.UTC(year, month - 1 + count, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  return isoDay(first.getUTCFullYear(), first.getUTCMonth() + 1, Math.min(date, last));
}
/** 0 for Monday to 6 for Sunday. */
const weekday = (day: string) => { const [year, month, date] = partsOf(day); return (new Date(Date.UTC(year, month - 1, date)).getUTCDay() + 6) % 7; };
const startOfWeek = (day: string) => addDays(day, -weekday(day));
const startOfMonth = (day: string) => `${day.slice(0, 8)}01`;
const endOfMonth = (day: string) => addDays(addMonths(startOfMonth(day), 1), -1);
const longDay = (day: string) => { const [year, month, date] = partsOf(day); return `${WEEKDAYS[weekday(day)]}, ${date} ${MONTHS[month - 1]} ${year}`; };

// ---------- choice list ----------

export interface Choice { value: string; label: string; disabled?: boolean }

export interface ChoiceListProps {
  choices: Choice[];
  /** The chosen value, ticked in the list. */
  current: () => string | null;
  onPick: (value: string | null) => void;
  label: string;
  /** Offer Clear when a value is chosen. */
  clearable?: boolean;
  /** Show a search box from this many choices (default 8). */
  searchFrom?: number;
  /** Text typed on the closed control, to start with. */
  typed?: string;
}

/**
 * A list of choices with the current one ticked. Arrow keys, Home and End move, Enter picks,
 * letters jump to a choice (or search, when the list is long enough to have a search box).
 */
export function ChoiceList(props: ChoiceListProps): HTMLElement {
  const listId = uid('cl'), optionId = (index: number) => `${listId}-${index}`;
  const searchable = props.choices.length >= (props.searchFrom ?? 8);
  const query = cell(searchable ? props.typed ?? '' : '');
  const shown = derive(() => {
    const text = query.get().trim().toLowerCase();
    return props.choices.filter(choice => !text || choice.label.toLowerCase().includes(text) || choice.value.toLowerCase().includes(text));
  });
  const enabled = (index: number) => !shown.peek()[index]?.disabled;
  const startAt = () => {
    const list = shown.peek(), current = untrack(props.current);
    const at = list.findIndex(choice => choice.value === current);
    return at >= 0 ? at : Math.max(0, list.findIndex(choice => !choice.disabled));
  };
  const active = cell(startAt());
  const move = (to: number) => {
    const count = shown.peek().length;
    if (!count) return;
    const step = to < active.peek() ? -1 : 1;
    let next = Math.max(0, Math.min(count - 1, to));
    while (!enabled(next) && next + step >= 0 && next + step < count) next += step;
    if (!enabled(next)) return;
    active.set(next);
    document.getElementById(optionId(next))?.scrollIntoView?.({ block: 'nearest' });
  };
  const pick = (index: number) => { const choice = shown.peek()[index]; if (choice && !choice.disabled) props.onPick(choice.value); };
  let typeahead = '', typedAt = 0;
  const keys = (event: KeyboardEvent) => {
    const count = shown.peek().length;
    if (event.key === 'ArrowDown') { event.preventDefault(); move(active.peek() + 1 < count ? active.peek() + 1 : 0); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); move(active.peek() > 0 ? active.peek() - 1 : count - 1); }
    else if (event.key === 'Home' && !searchable) { event.preventDefault(); move(0); }
    else if (event.key === 'End' && !searchable) { event.preventDefault(); move(count - 1); }
    else if (event.key === 'Enter' || (event.key === ' ' && !searchable)) { event.preventDefault(); pick(active.peek()); }
    else if (!searchable && event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      // Letters jump to the next choice that starts with what was typed in the last half second.
      typeahead = event.timeStamp - typedAt > 500 ? event.key.toLowerCase() : typeahead + event.key.toLowerCase();
      typedAt = event.timeStamp;
      const list = shown.peek(), from = typeahead.length === 1 ? active.peek() + 1 : active.peek();
      for (let offset = 0; offset < list.length; offset++) {
        const index = (from + offset) % list.length;
        if (!list[index].disabled && list[index].label.toLowerCase().startsWith(typeahead)) { move(index); break; }
      }
    }
  };
  const list = html`<div class="choice-list" role="listbox" id=${listId} aria-label=${props.label} tabindex=${searchable ? null : '0'}
    aria-activedescendant=${() => shown.get().length ? optionId(active.get()) : null} on:keydown=${(event: KeyboardEvent) => { if (!searchable) keys(event); }}>${() => shown.get().map((choice, index) => html`<div
      class="mi" class:on=${() => active.get() === index} class:off=${Boolean(choice.disabled)} role="option" id=${optionId(index)} data-value=${choice.value}
      aria-selected=${() => String(props.current() === choice.value)} aria-disabled=${choice.disabled ? 'true' : null}
      on:pointerdown=${(event: Event) => event.preventDefault()} on:click=${() => pick(index)} on:mouseenter=${() => { if (!choice.disabled) active.set(index); }}>
      <span class="ck">${() => props.current() === choice.value ? Icon('check', { size: 'sm' }) : null}</span><span class="grow">${choice.label}</span></div>`)}</div>` as HTMLElement;
  const search = searchable ? html`<input class="input" role="combobox" aria-expanded="true" aria-controls=${listId} aria-autocomplete="list"
    aria-activedescendant=${() => shown.get().length ? optionId(active.get()) : null} placeholder="Search" aria-label=${`Search ${props.label}`} autocomplete="off"
    on:input=${(event: Event) => { query.set((event.target as HTMLInputElement).value); active.set(Math.max(0, shown.peek().findIndex(choice => !choice.disabled))); }}
    on:keydown=${keys}>` as HTMLInputElement : null;
  if (search) search.value = query.peek();
  queueMicrotask(() => {
    if (search) { search.focus(); search.setSelectionRange?.(search.value.length, search.value.length); } else list.focus();
    document.getElementById(optionId(active.peek()))?.scrollIntoView?.({ block: 'nearest' });
  });
  return html`<div class="choice-pick">${search}${list}${when(() => searchable && !shown.get().length, () => html`<div class="muted link-status" role="status">No matches</div>`)}${props.clearable
    ? when(() => props.current() !== null, () => html`<hr><button type="button" class="mi" on:click=${() => props.onPick(null)}>${Icon('x', { size: 'sm' })}<span>Clear</span></button>`) : null}</div>` as HTMLElement;
}

// ---------- dropdown ----------

export interface DropdownProps {
  /** The chosen value, or null for none. */
  value: () => string | null;
  /** The choices, read each time the list opens. */
  choices: () => Choice[];
  onChange: (value: string | null) => void;
  /** Names the list. Without it, the text of the <label for> the button's id. */
  label?: string;
  id?: string;
  /** Labels the button when no <label for> does. */
  ariaLabel?: string;
  disabled?: boolean | (() => boolean);
  /** Shown while nothing is chosen (default "Choose one"). */
  placeholder?: string;
  clearable?: boolean | (() => boolean);
  invalid?: () => boolean;
  required?: () => boolean;
  describedBy?: string;
  className?: string;
}

/**
 * Evlet's select: a button showing the chosen choice that opens a ChoiceList. Click, Down or Up
 * opens it; a letter opens it at that letter. Form fields, Settings and Controls all use it, so
 * no page shows the browser's own menu.
 */
export function Dropdown(props: DropdownProps): HTMLButtonElement & { open(typed?: string): void } {
  const labelOf = (value: string) => untrack(props.choices).find(choice => choice.value === value)?.label ?? value;
  const off = () => Boolean(read(props.disabled ?? false));
  const button = html`<button type="button" class=${`in-select input${props.className ? ` ${props.className}` : ''}`} id=${props.id ?? null}
    disabled=${off} aria-haspopup="listbox" aria-expanded="false" aria-label=${props.ariaLabel ?? null}
    aria-invalid=${() => props.invalid?.() ? 'true' : null} aria-required=${() => props.required?.() ? 'true' : null} aria-describedby=${props.describedBy ?? null}
    >${() => props.value() === null
      ? html`<span class="tx muted">${props.placeholder ?? 'Choose one'}</span>` : html`<span class="tx">${labelOf(props.value()!)}</span>`}${Icon('chevron-down', { size: 'sm' })}</button>` as HTMLButtonElement & { open(typed?: string): void };
  const name = () => props.label || button.labels?.[0]?.textContent?.trim() || props.ariaLabel || 'an option';
  button.open = (typed = '') => {
    if (off()) return;
    openPopover(button, close => ChoiceList({ choices: untrack(props.choices), current: props.value, label: name(), typed,
      clearable: Boolean(read(props.clearable ?? false)), onPick: value => { close(); if (value !== untrack(props.value)) props.onChange(value); } }),
    { role: 'dialog', label: `Choose ${name().toLowerCase()}`, className: 'select-pop', align: 'left' });
  };
  button.addEventListener('click', () => button.open());
  button.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); button.open(); }
    else if (event.key.length === 1 && event.key !== ' ' && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault(); event.stopPropagation(); button.open(event.key);
    }
  });
  return button;
}

// ---------- grouped search (field picker) ----------

export interface GroupedChoice { value: string; label: string; group: string; hint?: string }

export interface GroupedChoiceListProps {
  choices: GroupedChoice[];
  current: () => string | null;
  onPick: (value: string) => void;
  label: string;
  /** At most this many matches are shown (default 100); typing narrows the rest. */
  limit?: number;
}

/**
 * A search box over a long list of choices in groups, such as a model's fields and then each
 * child table's. Every word typed must appear in the choice's group, label or value. Only the
 * first `limit` matches are drawn, so hundreds of choices stay quick. Arrows move, Enter picks.
 */
export function GroupedChoiceList(props: GroupedChoiceListProps): HTMLElement {
  const listId = uid('gl'), optionId = (index: number) => `${listId}-${index}`, limit = props.limit ?? 100;
  const haystacks = props.choices.map(choice => `${choice.group} ${choice.label} ${choice.value} ${choice.hint ?? ''}`.toLowerCase());
  const query = cell('');
  const found = derive(() => {
    const words = query.get().toLowerCase().split(/\s+/).filter(Boolean);
    return props.choices.filter((_, index) => words.every(word => haystacks[index].includes(word)));
  });
  const shown = derive(() => found.get().slice(0, limit));
  const start = Math.max(0, props.choices.findIndex(choice => choice.value === untrack(props.current)));
  const active = cell(start < limit ? start : 0);
  const move = (to: number) => {
    const count = shown.peek().length;
    if (!count) return;
    active.set((to + count) % count);
    document.getElementById(optionId(active.peek()))?.scrollIntoView?.({ block: 'nearest' });
  };
  const pick = (index: number) => { const choice = shown.peek()[index]; if (choice) props.onPick(choice.value); };
  const keys = (event: KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); move(active.peek() + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); move(active.peek() - 1); }
    else if (event.key === 'Enter') { event.preventDefault(); pick(active.peek()); }
  };
  const groups = () => {
    const result: Array<{ group: string; items: Array<{ choice: GroupedChoice; index: number }> }> = [];
    shown.get().forEach((choice, index) => {
      if (result[result.length - 1]?.group !== choice.group) result.push({ group: choice.group, items: [] });
      result[result.length - 1].items.push({ choice, index });
    });
    return result;
  };
  const input = html`<input class="input" role="combobox" aria-expanded="true" aria-controls=${listId} aria-autocomplete="list" autocomplete="off"
    placeholder="Search fields" aria-label=${`Search ${props.label}`} aria-activedescendant=${() => shown.get().length ? optionId(active.get()) : null}
    on:input=${(event: Event) => { query.set((event.target as HTMLInputElement).value); active.set(0); }} on:keydown=${keys}>` as HTMLInputElement;
  const list = html`<div class="choice-list grouped" role="listbox" id=${listId} aria-label=${props.label}>${() => groups().map(entry => html`<div role="group"
    aria-label=${entry.group}><div class="mh" aria-hidden="true">${entry.group}</div>${entry.items.map(({ choice, index }) => html`<div class="mi"
      class:on=${() => active.get() === index} role="option" id=${optionId(index)} data-value=${choice.value} aria-selected=${() => String(props.current() === choice.value)}
      on:pointerdown=${(event: Event) => event.preventDefault()} on:click=${() => pick(index)} on:mouseenter=${() => active.set(index)}>
      <span class="ck">${() => props.current() === choice.value ? Icon('check', { size: 'sm' }) : null}</span><span class="grow">${choice.label}</span>${choice.hint
        ? html`<span class="hint muted">${choice.hint}</span>` : null}</div>`)}</div>`)}</div>` as HTMLElement;
  queueMicrotask(() => { input.focus(); document.getElementById(optionId(active.peek()))?.scrollIntoView?.({ block: 'nearest' }); });
  return html`<div class="choice-pick">${input}${list}<div class="muted link-status" role="status">${() => !found.get().length ? 'No matches'
    : found.get().length > shown.get().length ? `${found.get().length - shown.get().length} more: type to narrow` : ''}</div></div>` as HTMLElement;
}

/** A button showing the chosen choice that opens a GroupedChoiceList: the field of a filter row. */
export function GroupedPicker(props: { value: () => string | null; choices: GroupedChoice[]; onChange: (value: string) => void; label: string;
  placeholder?: string; className?: string }): HTMLButtonElement & { open(): void } {
  const labelOf = (value: string) => props.choices.find(choice => choice.value === value)?.label ?? value;
  const button = html`<button type="button" class=${`in-select input${props.className ? ` ${props.className}` : ''}`} aria-haspopup="listbox" aria-expanded="false"
    aria-label=${() => props.value() === null ? props.label : `${props.label}: ${labelOf(props.value()!)}`}>${() => props.value() === null
      ? html`<span class="tx muted">${props.placeholder ?? 'Choose one'}</span>` : html`<span class="tx">${labelOf(props.value()!)}</span>`}${Icon('chevron-down', { size: 'sm' })}</button>` as HTMLButtonElement & { open(): void };
  button.open = () => openPopover(button, close => GroupedChoiceList({ choices: props.choices, current: props.value, label: props.label,
    onPick: value => { close(); if (value !== untrack(props.value)) props.onChange(value); } }),
  { role: 'dialog', label: props.label, className: 'select-pop field-pop', align: 'left' });
  button.addEventListener('click', () => button.open());
  button.addEventListener('keydown', event => { if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); button.open(); } });
  return button;
}

// ---------- calendar ----------

export interface CalendarProps {
  /** The chosen day, ticked. */
  value: () => string | null;
  onPick: (day: string) => void;
  /** Days of a range being chosen: both ends are marked and the days between shaded. */
  range?: () => [string | null, string | null];
  /** Called with the day under the pointer while a range is picked. */
  onHover?: (day: string | null) => void;
  label?: string;
  /** The day shown first when nothing is chosen (today by default). */
  start?: string;
}

/**
 * A month of days with month and year navigation. Arrow keys move by day and week, Page Up and
 * Page Down by month (with Shift by year), Home and End to the week's ends; Enter picks.
 * The title opens a month grid, and the month grid's title a grid of years.
 */
export function Calendar(props: CalendarProps): HTMLElement {
  const today = todayGst();
  const focus = cell(untrack(props.value) ?? props.start ?? today, { name: 'calendar.focus' });
  const view = cell<'days' | 'months' | 'years'>('days');
  const month = derive(() => focus.get().slice(0, 7));
  let keyboard = false;
  const gridId = uid('cal');

  const goTo = (day: string, fromKeys = false) => {
    keyboard = fromKeys;
    focus.set(day);
    if (fromKeys) queueMicrotask(() => root.querySelector<HTMLElement>(`[data-day="${day}"]`)?.focus());
  };
  const step = (count: number) => {
    if (view.peek() === 'days') goTo(addMonths(focus.peek(), count));
    else if (view.peek() === 'months') goTo(addMonths(focus.peek(), count * 12));
    else goTo(addMonths(focus.peek(), count * 144));
  };
  const title = () => {
    const [year, number] = partsOf(focus.get());
    if (view.get() === 'days') return `${MONTHS[number - 1]} ${year}`;
    if (view.get() === 'months') return String(year);
    const first = year - (year % 12);
    return `${first} to ${first + 11}`;
  };

  const days = () => {
    const first = startOfWeek(`${month.get()}-01`);
    const weeks = Array.from({ length: 6 }, (_, row) => Array.from({ length: 7 }, (_, column) => addDays(first, row * 7 + column)));
    const inRange = (day: string) => {
      const [from, to] = props.range?.() ?? [null, null];
      return Boolean(from && to && day > from && day < to);
    };
    const isEnd = (day: string) => { const [from, to] = props.range?.() ?? [null, null]; return day === from || day === to; };
    return html`<div class="cal-grid" role="grid" id=${gridId} aria-label=${props.label ?? 'Choose a day'} on:keydown=${dayKeys}>
      <div class="cal-row cal-head" role="row">${WEEKDAYS.map(name => html`<span role="columnheader" aria-label=${name}>${name.slice(0, 2)}</span>`)}</div>
      ${weeks.map(week => html`<div class="cal-row" role="row">${week.map(day => html`<button type="button" role="gridcell" class="cal-day" data-day=${day}
        class:out=${!day.startsWith(month.peek())} class:today=${day === today} class:on=${() => props.value() === day || isEnd(day)} class:between=${() => inRange(day)}
        aria-selected=${() => String(props.value() === day || isEnd(day))} aria-current=${day === today ? 'date' : null} aria-label=${longDay(day)}
        tabindex=${() => focus.get() === day ? '0' : '-1'} on:click=${() => { focus.set(day); props.onPick(day); }}
        on:mouseenter=${() => props.onHover?.(day)}>${Number(day.slice(8))}</button>`)}</div>`)}</div>`;
  };
  const dayKeys = (event: KeyboardEvent) => {
    const day = focus.peek();
    const moves: Record<string, () => string> = {
      ArrowLeft: () => addDays(day, -1), ArrowRight: () => addDays(day, 1), ArrowUp: () => addDays(day, -7), ArrowDown: () => addDays(day, 7),
      Home: () => startOfWeek(day), End: () => addDays(startOfWeek(day), 6),
      PageUp: () => addMonths(day, event.shiftKey ? -12 : -1), PageDown: () => addMonths(day, event.shiftKey ? 12 : 1),
    };
    if (!moves[event.key]) return;
    event.preventDefault();
    goTo(moves[event.key](), true);
  };
  const months = () => {
    const [year] = partsOf(focus.get());
    return html`<div class="cal-pick" role="group" aria-label=${`Months of ${year}`}>${MONTHS.map((name, index) => {
      const day = isoDay(year, index + 1, 1);
      return html`<button type="button" class:on=${() => props.value()?.slice(0, 7) === day.slice(0, 7)} class:today=${today.slice(0, 7) === day.slice(0, 7)}
        on:click=${() => { focus.set(addMonths(focus.peek(), (year - partsOf(focus.peek())[0]) * 12 + index + 1 - partsOf(focus.peek())[1])); view.set('days'); }}>${name.slice(0, 3)}</button>`;
    })}</div>`;
  };
  const years = () => {
    const [year] = partsOf(focus.get()), first = year - (year % 12);
    return html`<div class="cal-pick" role="group" aria-label="Years">${Array.from({ length: 12 }, (_, index) => first + index).map(each => html`<button type="button"
      class:on=${() => props.value()?.slice(0, 4) === String(each)} class:today=${today.startsWith(String(each))}
      on:click=${() => { focus.set(addMonths(focus.peek(), (each - partsOf(focus.peek())[0]) * 12)); view.set('months'); }}>${each}</button>`)}</div>`;
  };

  const root = html`<div class="cal" on:mouseleave=${() => props.onHover?.(null)}>
    <div class="cal-h">
      <button type="button" class="icon-btn" aria-label=${() => view.get() === 'days' ? 'Previous month' : view.get() === 'months' ? 'Previous year' : 'Earlier years'}
        on:click=${() => step(-1)}>${Icon('chevron-left', { size: 'sm' })}</button>
      <button type="button" class="cal-title" aria-live="polite" disabled=${() => view.get() === 'years'}
        aria-label=${() => view.get() === 'days' ? `${title()}, choose a month` : view.get() === 'months' ? `${title()}, choose a year` : title()}
        on:click=${() => view.set(view.peek() === 'days' ? 'months' : 'years')}>${title}</button>
      <button type="button" class="icon-btn" aria-label=${() => view.get() === 'days' ? 'Next month' : view.get() === 'months' ? 'Next year' : 'Later years'}
        on:click=${() => step(1)}>${Icon('chevron-right', { size: 'sm' })}</button>
    </div>
    ${() => view.get() === 'days' ? (month.get(), days()) : view.get() === 'months' ? months() : years()}</div>` as HTMLElement;
  // Keyboard focus follows the focused day after the month re-renders.
  queueMicrotask(() => { if (keyboard) root.querySelector<HTMLElement>(`[data-day="${focus.peek()}"]`)?.focus(); });
  return root;
}

/** Move keyboard focus into a calendar (to its focused day). */
export function focusCalendar(root: ParentNode) {
  root.querySelector<HTMLElement>('.cal-day[tabindex="0"]')?.focus();
}

// ---------- steppers ----------

export interface StepperProps {
  label: string;
  value: Cell<number>;
  min: number;
  max: number;
  /** What the buttons add or take away (the arrow keys always move by 1). */
  step?: number;
  /** Wrap around at the ends, as a clock does. */
  wrap?: boolean;
  onChange?: () => void;
  width?: number;
}

/** A number with up and down buttons, a spin button for the keyboard (arrows, Page Up/Down, Home, End). */
export function Stepper(props: StepperProps): HTMLElement {
  const by = props.step ?? 1;
  const set = (next: number) => {
    const span = props.max - props.min + 1;
    const value = props.wrap ? ((next - props.min) % span + span) % span + props.min : Math.max(props.min, Math.min(props.max, next));
    if (value !== props.value.peek()) { props.value.set(value); props.onChange?.(); }
  };
  const nudge = (direction: 1 | -1) => {
    const now = props.value.peek();
    // Buttons snap to the step: 07 + 5 is 10, not 12.
    set(by > 1 ? (direction > 0 ? Math.floor(now / by) * by + by : Math.ceil(now / by) * by - by) : now + direction);
  };
  const keys = (event: KeyboardEvent) => {
    const now = props.value.peek();
    const next = { ArrowUp: now + 1, ArrowDown: now - 1, PageUp: now + 10, PageDown: now - 10, Home: props.min, End: props.max }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    set(next);
  };
  return html`<div class="stepper">
    <button type="button" class="icon-btn" tabindex="-1" aria-label=${`More ${props.label.toLowerCase()}`} on:click=${() => nudge(1)}>${Icon('chevron-down', { size: 'sm', className: 'flip' })}</button>
    <span class="stepper-v num" role="spinbutton" tabindex="0" aria-label=${props.label} aria-valuemin=${props.min} aria-valuemax=${props.max}
      aria-valuenow=${() => props.value.get()} style:min-width=${props.width ? `${props.width}ch` : null} on:keydown=${keys}>${() => pad(props.value.get())}</span>
    <button type="button" class="icon-btn" tabindex="-1" aria-label=${`Fewer ${props.label.toLowerCase()}`} on:click=${() => nudge(-1)}>${Icon('chevron-down', { size: 'sm' })}</button>
    <span class="stepper-l" aria-hidden="true">${props.label}</span></div>` as HTMLElement;
}

/** Hours and minutes steppers. Minute buttons move by 5. */
export function TimeSteppers(props: { hours: Cell<number>; minutes: Cell<number>; onChange: () => void }): HTMLElement {
  return html`<div class="time-steppers" role="group" aria-label="Time">${Stepper({ label: 'Hours', value: props.hours, min: 0, max: 23, wrap: true, onChange: props.onChange })}
    <span class="colon" aria-hidden="true">:</span>${Stepper({ label: 'Minutes', value: props.minutes, min: 0, max: 59, step: 5, wrap: true, onChange: props.onChange })}</div>` as HTMLElement;
}

// ---------- picker panels for the field controls ----------

/** The footer of a picker panel: actions on the left, the main one on the right. */
const footer = (...items: Child[]) => html`<div class="pick-f">${items}</div>`;
const textButton = (label: string, onClick: () => void, primary = false) =>
  html`<button type="button" class=${primary ? 'btn primary sm' : 'btn ghost sm'} on:click=${onClick}>${label}</button>`;

export interface DatePanelProps {
  value: () => string | null;
  onPick: (day: string | null) => void;
  clearable: boolean;
  label: string;
}

/** A calendar with Today and Clear. Picking a day closes it (the control decides). */
export function DatePanel(props: DatePanelProps): HTMLElement {
  return html`<div class="pick">${Calendar({ value: props.value, onPick: props.onPick, label: props.label })}
    ${footer(textButton('Today', () => props.onPick(todayGst())), html`<span class="grow"></span>`,
      props.clearable ? when(() => props.value() !== null, () => textButton('Clear', () => props.onPick(null))) : null)}</div>` as HTMLElement;
}

export interface DateTimeValue { day: string; hours: number; minutes: number }

/** A calendar and time steppers, with Now, Clear and Done. Every change is reported at once. */
export function DateTimePanel(props: { value: () => DateTimeValue | null; now: () => DateTimeValue; onChange: (value: DateTimeValue | null) => void;
  onDone: () => void; clearable: boolean; label: string }): HTMLElement {
  const start = untrack(props.value);
  const day = cell<string | null>(start?.day ?? null), hours = cell(start?.hours ?? 9), minutes = cell(start?.minutes ?? 0);
  const report = () => props.onChange({ day: day.peek() ?? todayGst(), hours: hours.peek(), minutes: minutes.peek() });
  return html`<div class="pick">${Calendar({ value: () => day.get(), onPick: picked => { day.set(picked); report(); }, label: props.label })}
    <div class="pick-time">${Icon('clock', { size: 'sm' })}${TimeSteppers({ hours, minutes, onChange: report })}</div>
    ${footer(textButton('Now', () => { const now = props.now(); day.set(now.day); hours.set(now.hours); minutes.set(now.minutes); report(); }),
      props.clearable ? textButton('Clear', () => { day.set(null); props.onChange(null); }) : null, html`<span class="grow"></span>`, textButton('Done', props.onDone, true))}</div>` as HTMLElement;
}

/** Hours and minutes steppers with Now, Clear and Done. */
export function TimePanel(props: { value: () => { hours: number; minutes: number } | null; now: () => { hours: number; minutes: number };
  onChange: (value: { hours: number; minutes: number } | null) => void; onDone: () => void; clearable: boolean }): HTMLElement {
  const start = untrack(props.value);
  const hours = cell(start?.hours ?? 9), minutes = cell(start?.minutes ?? 0);
  const report = () => props.onChange({ hours: hours.peek(), minutes: minutes.peek() });
  const presets = ['09:00', '12:00', '13:00', '17:00'];
  return html`<div class="pick pick-narrow">${TimeSteppers({ hours, minutes, onChange: report })}
    <div class="pick-chips" role="group" aria-label="Common times">${presets.map(text => html`<button type="button" class="chip"
      on:click=${() => { hours.set(Number(text.slice(0, 2))); minutes.set(Number(text.slice(3))); report(); }}>${text}</button>`)}</div>
    ${footer(textButton('Now', () => { const now = props.now(); hours.set(now.hours); minutes.set(now.minutes); report(); }),
      props.clearable ? textButton('Clear', () => props.onChange(null)) : null, html`<span class="grow"></span>`, textButton('Done', props.onDone, true))}</div>` as HTMLElement;
}

const DURATION_PRESETS: Array<[string, number]> = [['15m', 900], ['30m', 1800], ['45m', 2700], ['1h', 3600], ['1h 30m', 5400], ['2h', 7200], ['4h', 14400], ['8h', 28800]];

/** Hours and minutes of a length of time, with common lengths. Reports seconds. */
export function DurationPanel(props: { value: () => number | null; onChange: (seconds: number | null) => void; onDone: () => void; clearable: boolean }): HTMLElement {
  const start = Math.max(0, untrack(props.value) ?? 0);
  const hours = cell(Math.floor(start / 3600)), minutes = cell(Math.floor(start % 3600 / 60));
  const report = () => props.onChange(hours.peek() * 3600 + minutes.peek() * 60);
  return html`<div class="pick pick-narrow"><div class="time-steppers" role="group" aria-label="Length of time">
      ${Stepper({ label: 'Hours', value: hours, min: 0, max: 999, onChange: report, width: 3 })}${Stepper({ label: 'Minutes', value: minutes, min: 0, max: 59, step: 5, onChange: report })}</div>
    <div class="pick-chips" role="group" aria-label="Common lengths">${DURATION_PRESETS.map(([label, seconds]) => html`<button type="button" class="chip"
      class:on=${() => props.value() === seconds} on:click=${() => { hours.set(Math.floor(seconds / 3600)); minutes.set(seconds % 3600 / 60); report(); }}>${label}</button>`)}</div>
    ${footer(props.clearable ? textButton('Clear', () => props.onChange(null)) : null, html`<span class="grow"></span>`, textButton('Done', props.onDone, true))}</div>` as HTMLElement;
}

// ---------- date range ----------

export type DateRange = [string, string];

/** The named ranges a range picker offers, for a given today. */
export function rangePresets(today = todayGst()): Array<{ label: string; range: DateRange }> {
  const [year, month] = partsOf(today);
  const quarter = isoDay(year, Math.floor((month - 1) / 3) * 3 + 1, 1);
  const lastMonth = addMonths(startOfMonth(today), -1);
  return [
    { label: 'Today', range: [today, today] },
    { label: 'Yesterday', range: [addDays(today, -1), addDays(today, -1)] },
    { label: 'This week', range: [startOfWeek(today), addDays(startOfWeek(today), 6)] },
    { label: 'Last 7 days', range: [addDays(today, -6), today] },
    { label: 'This month', range: [startOfMonth(today), endOfMonth(today)] },
    { label: 'Last month', range: [lastMonth, endOfMonth(lastMonth)] },
    { label: 'Last 30 days', range: [addDays(today, -29), today] },
    { label: 'This quarter', range: [quarter, addDays(addMonths(quarter, 3), -1)] },
    { label: 'This year', range: [`${year}-01-01`, `${year}-12-31`] },
    { label: 'Last year', range: [`${year - 1}-01-01`, `${year - 1}-12-31`] },
  ];
}

/** '01-11-2026 to 30-11-2026', or one day when both ends are the same. */
export const rangeText = (range: DateRange) => range[0] === range[1] ? formatDate(range[0]) : `${formatDate(range[0])} to ${formatDate(range[1])}`;

export interface DateRangePanelProps {
  value: () => DateRange | null;
  onApply: (range: DateRange | null) => void;
  label?: string;
}

/**
 * Pick a range: a preset, or a first and a last day on the calendar (the order does not
 * matter). Apply reports it; Clear removes it.
 */
export function DateRangePanel(props: DateRangePanelProps): HTMLElement {
  const start = untrack(props.value);
  const from = cell<string | null>(start?.[0] ?? null), to = cell<string | null>(start?.[1] ?? null), hover = cell<string | null>(null);
  const picking = derive(() => from.get() !== null && to.get() === null);
  const pick = (day: string) => {
    if (!from.peek() || to.peek()) { from.set(day); to.set(null); return; }
    const first = from.peek()!;
    if (day < first) { from.set(day); to.set(first); } else to.set(day);
  };
  const shown = (): [string | null, string | null] => {
    const first = from.get(), last = to.get(), over = hover.get();
    if (first && !last && over) return over < first ? [over, first] : [first, over];
    return [first, last];
  };
  const chosen = (): DateRange | null => from.get() && to.get() ? [from.get()!, to.get()!] : null;
  const presets = rangePresets();
  const same = (range: DateRange) => from.get() === range[0] && to.get() === range[1];
  // The ends can be typed too, the same forgiving way as a date field (120627, 1/6/27, today).
  const typedError = cell<string | null>(null);
  const typed = (end: 'from' | 'to') => html`<input class="input" placeholder=${end === 'from' ? 'From' : 'To'} aria-label=${end === 'from' ? 'First day' : 'Last day'} autocomplete="off"
    prop:value=${() => { const day = (end === 'from' ? from : to).get(); return day ? formatDate(day) : ''; }}
    on:change=${(event: Event) => {
      const input = event.target as HTMLInputElement, text = input.value.trim();
      const day = text ? parseDay(text) : null;
      // Text that can't be read keeps that end as it was and says what was typed.
      typedError.set(text && !day ? `Couldn't read ${text} as a date` : null);
      if (text && !day) { const kept = (end === 'from' ? from : to).peek(); input.value = kept ? formatDate(kept) : ''; return; }
      (end === 'from' ? from : to).set(day);
      const first = from.peek(), last = to.peek();
      if (first && last && last < first) { from.set(last); to.set(first); }
      input.value = day ? formatDate((end === 'from' ? from : to).peek() ?? day) : '';
    }}
    on:input=${() => typedError.set(null)}
    on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') { event.preventDefault(); (event.target as HTMLInputElement).dispatchEvent(new Event('change')); } }}>`;
  return html`<div class="pick pick-range">
    <div class="range-presets" role="group" aria-label="Ranges">${presets.map(preset => html`<button type="button" class="mi" class:on=${() => same(preset.range)}
      aria-pressed=${() => String(same(preset.range))} on:click=${() => { from.set(preset.range[0]); to.set(preset.range[1]); }}>${preset.label}</button>`)}</div>
    <div class="range-main">${Calendar({ value: () => null, range: shown, onPick: pick, onHover: day => { if (picking.peek()) hover.set(day); },
      label: props.label ?? 'Choose the first and last day', start: start?.[0] })}
      <div class="range-typed">${typed('from')}<span aria-hidden="true">to</span>${typed('to')}</div>
      <div class="range-note" class:muted=${() => !typedError.get()} class:err-inline=${() => Boolean(typedError.get())} role="status">${() => typedError.get()
        ?? (chosen() ? rangeText(chosen()!) : from.get() ? `From ${formatDate(from.get()!)}: choose the last day` : 'Choose the first day')}</div>
      ${footer(textButton('Clear', () => props.onApply(null)), html`<span class="grow"></span>`,
        html`<button type="button" class="btn primary sm" disabled=${() => !chosen()} on:click=${() => props.onApply(chosen())}>Apply</button>`)}</div></div>` as HTMLElement;
}
