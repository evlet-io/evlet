// Base and element components: Icon, IconTile, Button, IconButton, Checkbox, Switch,
// SegmentedControl, Key, Chip, AddChip, CountBadge, StatusPill, Avatar, RecordMark, Divider,
// ProgressBar, ScoreBars, EmptyState, InfoNote, StateBanner, Stat, SummaryStrip, LinkButton,
// FilterChip, SearchInput.
//
// Each is a plain function that returns DOM. Properties are plain values, accessors or cells;
// only the nodes bound to a reactive property change when it does.

import { html } from '../dom';
import { Cell } from '../reactive';
import { ICONS } from './icon-data';
import { initials, read, tint, tintFor, type Prop, type Tint } from './core';
import { tooltip, type Placement } from './tooltip';
import type { Child } from '../dom';

const SVG = 'http://www.w3.org/2000/svg';
export type IconSize = 'xs' | 'sm' | 'md' | 'lg';

// ---------- Icon ----------

/** A Lucide line icon at 12, 14, 16 or 18 px. Decorative unless given a label. */
export function Icon(name: string, options: { size?: IconSize; tint?: Tint; label?: string; className?: string } = {}): SVGSVGElement {
  const svg = document.createElementNS(SVG, 'svg');
  const size = options.size && options.size !== 'md' ? ` ${options.size}` : '';
  svg.setAttribute('class', `i${size}${options.tint ? ` c-${tint(options.tint)}` : ''}${options.className ? ` ${options.className}` : ''}`);
  for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }))
    svg.setAttribute(key, value);
  if (options.label) { svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', options.label); }
  else svg.setAttribute('aria-hidden', 'true');
  for (const [tag, attributes] of ICONS[name] ?? ICONS['circle-help']) {
    const shape = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, value);
    svg.append(shape);
  }
  return svg;
}

/** Icon on a soft tinted square, 22 to 44 px. */
export function IconTile(name: string, options: { tint?: Tint; size?: number } = {}): HTMLElement {
  const size = Math.max(22, Math.min(44, options.size ?? 32));
  const element = html`<span class="ph-icon b-${tint(options.tint)}" style:width=${`${size}px`} style:height=${`${size}px`} style:border-radius=${`${Math.round(size / 3.6)}px`}>${Icon(name, { size: size >= 32 ? 'lg' : size >= 26 ? 'md' : 'sm' })}</span>` as HTMLElement;
  return element;
}

// ---------- buttons ----------

export type ButtonVariant = 'primary' | 'default' | 'ghost' | 'success' | 'danger' | 'soft-danger';
export interface ButtonProps {
  label?: Prop<string>;
  variant?: ButtonVariant;
  size?: 'default' | 'small';
  icon?: string;
  /** Tint for the icon of a neutral button (the mockup tints icons, not backgrounds). */
  iconTint?: Tint;
  onClick?: (event: MouseEvent) => void;
  disabled?: Prop<boolean>;
  /** Tooltip; for an icon-only button it is also the accessible label. */
  tip?: Prop<string | null>;
  tipPlacement?: Placement;
  ariaLabel?: string;
  type?: 'button' | 'submit';
  /** Hide the label under 760 px (secondary buttons on phones). */
  hideLabelOnPhone?: boolean;
  pressed?: Prop<boolean>;
  className?: string;
}

const VARIANT: Record<ButtonVariant, string> = { primary: 'primary', default: '', ghost: 'ghost', success: 'success', danger: 'danger', 'soft-danger': 'danger-soft' };

/** The one button. Variants set meaning, sizes set density, an icon is optional. */
export function Button(props: ButtonProps): HTMLButtonElement {
  const iconOnly = props.label === undefined;
  const classes = ['btn', VARIANT[props.variant ?? 'default'], props.size === 'small' ? 'sm' : '', iconOnly ? 'icon' : '', props.className ?? ''].filter(Boolean).join(' ');
  const neutral = !['primary', 'success', 'danger'].includes(props.variant ?? 'default');
  const icon = props.icon ? Icon(props.icon, { size: 'sm', tint: props.iconTint, className: neutral && !props.iconTint && !iconOnly ? 'tint' : undefined }) : null;
  const label = iconOnly ? null : props.hideLabelOnPhone ? html`<span class="hide-sm">${props.label}</span>` : html`<span>${props.label}</span>`;
  const button = html`<button type=${props.type ?? 'button'} class=${classes}
    disabled=${props.disabled ?? false}
    aria-pressed=${props.pressed === undefined ? null : () => String(Boolean(read(props.pressed!)))}
    aria-label=${props.ariaLabel ?? (iconOnly ? () => read(props.tip ?? '') || null : null)}
    on:click=${(event: MouseEvent) => props.onClick?.(event)}>${icon}${label}</button>` as HTMLButtonElement;
  if (props.tip !== undefined) tooltip(button, props.tip, props.tipPlacement);
  return button;
}

/** Square button with only an icon. Always carries a tooltip and an accessible label. */
export function IconButton(props: { icon: string; tip: Prop<string>; onClick?: (event: MouseEvent) => void; active?: Prop<boolean>; disabled?: Prop<boolean>; size?: 'default' | 'small'; placement?: Placement; tint?: Tint }): HTMLButtonElement {
  const button = html`<button type="button" class=${`icon-btn${props.size === 'small' ? ' sm' : ''}`} class:on=${props.active ?? false}
    disabled=${props.disabled ?? false} aria-label=${props.tip}
    aria-pressed=${props.active === undefined ? null : () => String(Boolean(read(props.active!)))}
    on:click=${(event: MouseEvent) => props.onClick?.(event)}>${Icon(props.icon, { size: 'sm', tint: props.tint })}</button>` as HTMLButtonElement;
  tooltip(button, props.tip, props.placement);
  return button;
}

/** Text link for secondary navigation inside panels. */
export function LinkButton(props: { label: Prop<string>; href?: string; onClick?: (event: MouseEvent) => void; icon?: string }): HTMLElement {
  const content = [props.label, props.icon ? Icon(props.icon, { size: 'xs' }) : null];
  if (props.href) return html`<a class="link" href=${props.href}>${content}</a>` as HTMLElement;
  return html`<button type="button" class="link" on:click=${(event: MouseEvent) => props.onClick?.(event)}>${content}</button>` as HTMLElement;
}

/** Dashed chip that adds an item to a small set. */
export function AddChip(props: { label: string; onClick?: (event: MouseEvent) => void }): HTMLButtonElement {
  return html`<button type="button" class="chip add" on:click=${(event: MouseEvent) => props.onClick?.(event)}>${Icon('plus', { size: 'xs' })}${props.label}</button>` as HTMLButtonElement;
}

// ---------- toggles ----------

function toggler(value: Prop<boolean>, onChange?: (next: boolean) => void) {
  return () => {
    const next = !read(value);
    if (onChange) onChange(next);
    else if (value instanceof Cell) value.set(next);
  };
}

/** Square check for selection and task completion. Space toggles it. */
export function Checkbox(props: { checked: Prop<boolean>; label?: Prop<string>; onChange?: (next: boolean) => void; ariaLabel?: Prop<string>; disabled?: Prop<boolean>; mixed?: Prop<boolean> }): HTMLButtonElement {
  const box = html`<span class="check" class:on=${() => read(props.checked) || Boolean(props.mixed && read(props.mixed))}>${Icon('check')}</span>`;
  const state = () => props.mixed && read(props.mixed) ? 'mixed' : String(Boolean(read(props.checked)));
  if (props.label === undefined)
    return html`<button type="button" class="check" class:on=${() => read(props.checked) || Boolean(props.mixed && read(props.mixed))} role="checkbox"
      aria-checked=${state} aria-label=${props.ariaLabel ?? null} disabled=${props.disabled ?? false}
      on:click=${toggler(props.checked, props.onChange)}>${Icon('check')}</button>` as HTMLButtonElement;
  return html`<button type="button" class="ck-wrap" role="checkbox" aria-checked=${state} disabled=${props.disabled ?? false}
    on:click=${toggler(props.checked, props.onChange)}>${box}<span>${props.label}</span></button>` as HTMLButtonElement;
}

/** On and off toggle, role="switch" with aria-checked. */
export function Switch(props: { on: Prop<boolean>; label?: Prop<string>; onChange?: (next: boolean) => void; ariaLabel?: Prop<string>; disabled?: Prop<boolean>; id?: string; describedBy?: string }): HTMLButtonElement {
  return html`<button type="button" class="sw-wrap" role="switch" id=${props.id ?? null} aria-checked=${() => String(Boolean(read(props.on)))}
    aria-label=${props.ariaLabel ?? null} aria-describedby=${props.describedBy ?? null} disabled=${props.disabled ?? false}
    on:click=${toggler(props.on, props.onChange)}><span class="sw" class:on=${() => Boolean(read(props.on))}><i></i></span>${props.label === undefined ? null : html`<span>${props.label}</span>`}</button>` as HTMLButtonElement;
}

export interface Option<V extends string = string> { value: V; label: string; icon?: string }

/** Two to four mutually exclusive options. Arrow keys move the choice. */
export function SegmentedControl<V extends string>(props: { options: Option<V>[]; value: Cell<V>; size?: 'default' | 'small'; label?: string; onChange?: (value: V) => void }): HTMLElement {
  const choose = (value: V) => { props.value.set(value); props.onChange?.(value); };
  const group = html`<div class=${`seg${props.size === 'small' ? ' sm' : ''}`} role="radiogroup" aria-label=${props.label ?? null}></div>` as HTMLElement;
  const buttons = props.options.map(option => html`<button type="button" role="radio" class:on=${() => props.value.get() === option.value}
    aria-checked=${() => String(props.value.get() === option.value)} tabindex=${() => props.value.get() === option.value ? '0' : '-1'}
    on:click=${() => choose(option.value)}>${option.icon ? Icon(option.icon, { size: 'sm' }) : null}${option.label}</button>` as HTMLButtonElement);
  group.append(...buttons);
  group.addEventListener('keydown', event => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const at = props.options.findIndex(option => option.value === props.value.peek());
    const next = (at + step + props.options.length) % props.options.length;
    choose(props.options[next].value);
    buttons[next].focus();
  });
  return group;
}

// ---------- small elements ----------

/** Keyboard key hint. */
export function Key(keys: string): HTMLElement { return html`<kbd>${keys}</kbd>` as HTMLElement; }

/** Small neutral label for tags, counts and flags. */
export function Chip(props: { label: Prop<string>; icon?: string; tone?: Tint | 'neutral' }): HTMLElement {
  const tone = props.tone && props.tone !== 'neutral' ? ` b-${tint(props.tone)}` : '';
  return html`<span class=${`chip${tone}`}>${props.icon ? Icon(props.icon, { size: 'xs' }) : null}${props.label}</span>` as HTMLElement;
}

/** Count beside a label: filled for unread, plain for totals. */
export function CountBadge(props: { count: Prop<number>; tone?: 'filled' | 'plain'; label?: string }): HTMLElement {
  return html`<span class=${props.tone === 'filled' ? 'badge' : 'count num'} aria-label=${props.label ?? null}>${() => String(read(props.count))}</span>` as HTMLElement;
}

/** Dot and label on a tint; one colour per state. */
export function StatusPill(props: { label: Prop<string>; tint: Prop<Tint> }): HTMLElement {
  return html`<span class=${() => `pill b-${tint(read(props.tint))}`}><span class="d" aria-hidden="true"></span>${props.label}</span>` as HTMLElement;
}

export type AvatarSize = 'sm' | 'md' | 'lg' | 'xl';
/** Initials on a tinted circle for people. */
export function Avatar(props: { name: Prop<string>; size?: AvatarSize; tint?: Tint }): HTMLElement {
  const size = props.size && props.size !== 'md' ? ` ${props.size}` : '';
  return html`<span class=${() => `avatar${size} b-${props.tint ?? tintFor(read(props.name))}`} role="img" aria-label=${props.name}>${() => initials(read(props.name))}</span>` as HTMLElement;
}

/** First letter of a company on a rounded square. */
export function RecordMark(props: { name: Prop<string>; size?: 'sm' | 'lg'; tint?: Tint }): HTMLElement {
  return html`<span class=${() => `org-mark${props.size === 'lg' ? ' lg' : ''} b-${props.tint ?? tintFor(read(props.name))}`} aria-hidden="true">${() => (String(read(props.name) ?? '').trim()[0] ?? '?').toUpperCase()}</span>` as HTMLElement;
}

/** Thin rule between groups. */
export function Divider(direction: 'vertical' | 'horizontal' = 'vertical'): HTMLElement {
  return (direction === 'vertical' ? html`<span class="sep" role="separator" aria-orientation="vertical"></span>` : html`<hr class="divider">`) as HTMLElement;
}

/** Thin bar for a share of a whole, with an optional number. */
export function ProgressBar(props: { value: Prop<number>; tint?: Tint; label?: string; showValue?: boolean }): HTMLElement {
  const clamp = () => Math.max(0, Math.min(100, Number(read(props.value)) || 0));
  return html`<span class="prob" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${() => String(clamp())} aria-label=${props.label ?? null}>
    <span class="bar"><span style:width=${() => `${clamp()}%`} style:background=${props.tint ? `var(--t-${tint(props.tint)})` : null}></span></span>${props.showValue === false ? null : html`<span class="num">${() => `${clamp()}%`}</span>`}</span>` as HTMLElement;
}

/** Four rising bars and a number; grey to amber to green with the score. */
export function ScoreBars(props: { score: Prop<number> }): HTMLElement {
  const score = () => Math.max(0, Math.min(100, Number(read(props.score)) || 0));
  const bars = [0, 1, 2, 3].map(index => html`<i class:on=${() => score() >= 25 * index + 1} style:height=${`${6 + index * 3}px`}></i>`);
  return html`<span class=${() => `score ${score() >= 70 ? 'c-green' : score() >= 45 ? 'c-amber' : 'c-slate'}`} aria-label=${() => `Score ${score()}`}><span class="bars" aria-hidden="true">${bars}</span><span class="num">${() => String(score())}</span></span>` as HTMLElement;
}

/** Tinted icon, a heading, what to do next and an optional button. */
export function EmptyState(props: { icon: string; tint?: Tint; title: Prop<string>; text?: Prop<string>; action?: Node }): HTMLElement {
  return html`<div class="empty"><div class=${`ei b-${tint(props.tint)}`}>${Icon(props.icon, { size: 'lg' })}</div><h3>${props.title}</h3>${props.text === undefined ? null : html`<p>${props.text}</p>`}${props.action ?? null}</div>` as HTMLElement;
}

/** Quiet explanation above a block that behaves differently from the rest. */
export function InfoNote(text: Prop<string>, icon = 'info'): HTMLElement {
  return html`<p class="eff-note">${Icon(icon, { size: 'sm' })}<span>${text}</span></p>` as HTMLElement;
}

export type BannerTone = 'neutral' | 'info' | 'danger';
const BANNER: Record<BannerTone, [Tint, string]> = { neutral: ['slate', 'pencil'], info: ['blue', 'lock'], danger: ['rose', 'circle-x'] };
/** Full-width note under a header about the record's state, with optional actions. */
export function StateBanner(props: { tone: Prop<BannerTone>; title?: Prop<string>; text: Prop<Child>; actions?: Node[]; icon?: string }): HTMLElement {
  const tone = () => BANNER[read(props.tone)] ?? BANNER.neutral;
  const icon = () => Icon(props.icon ?? tone()[1], { size: 'sm' });
  return html`<div class=${() => `inv-banner b-${tone()[0]}`} role="status">${icon}<div>${props.title === undefined ? null : html`<b>${props.title}</b>`} ${props.text}</div>${props.actions?.length ? [html`<div class="grow"></div>`, ...props.actions] : null}</div>` as HTMLElement;
}

// ---------- stats ----------

export interface StatProps { label: Prop<string>; value: Prop<string>; note?: Prop<string> }
/** Label, value and a short note. The unit of the summary strip. */
export function Stat(props: StatProps): HTMLElement {
  return html`<div class="sum-t"><span>${props.label}</span><b class="num">${props.value}</b>${props.note === undefined ? null : html`<em>${props.note}</em>`}</div>` as HTMLElement;
}

/** Up to four stats in one bordered row. */
export function SummaryStrip(stats: StatProps[]): HTMLElement {
  return html`<div class="sum">${stats.slice(0, 4).map(Stat)}</div>` as HTMLElement;
}

// ---------- list helpers ----------

/** An active filter with a remove button. */
export function FilterChip(props: { label: Prop<string>; onRemove: () => void }): HTMLElement {
  return html`<span class="filter-chip">${props.label}<button type="button" aria-label=${() => `Remove filter ${read(props.label)}`} on:click=${props.onRemove}>${Icon('x', { size: 'xs' })}</button></span>` as HTMLElement;
}

/** Text input with a search icon. Filters as you type and keeps the cursor in place. */
export function SearchInput(props: { value: Cell<string>; placeholder?: string; label?: string; onEnter?: (value: string) => void }): HTMLElement {
  const input = html`<input class="input" type="search" placeholder=${props.placeholder ?? 'Search'} aria-label=${props.label ?? props.placeholder ?? 'Search'}
    prop:value=${props.value} on:input=${(event: Event) => props.value.set((event.target as HTMLInputElement).value)}
    on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') props.onEnter?.(props.value.peek()); if (event.key === 'Escape' && props.value.peek()) { event.stopPropagation(); props.value.set(''); } }}>`;
  return html`<div class="search-field">${Icon('search', { size: 'sm' })}${input}</div>` as HTMLElement;
}

/** A borderless input that creates an item on Enter. */
export function QuickAddInput(props: { placeholder: string; onAdd: (text: string) => void }): HTMLElement {
  return html`<div class="quick-add">${Icon('plus', { size: 'sm' })}<input class="input" placeholder=${props.placeholder} aria-label=${props.placeholder}
    on:keydown=${(event: KeyboardEvent) => {
      const input = event.target as HTMLInputElement;
      if (event.key === 'Enter' && input.value.trim()) { props.onAdd(input.value.trim()); input.value = ''; }
    }}></div>` as HTMLElement;
}
