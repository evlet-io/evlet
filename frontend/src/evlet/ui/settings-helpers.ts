// Presentation helpers shared by Controls and personal Settings. Keep this module independent of
// either route shell so opening personal settings does not load the administrative screens.
import { html, type Child } from '../dom';
import { ApiError } from '../api';
import { Icon } from './base';
import { DialogFooter, openModal } from './layers';
import { Dropdown } from './pickers';
import { formatDateTime } from '../model/format';

export const stamp = (seconds: number | null | undefined) => seconds ? formatDateTime(new Date(seconds * 1000).toISOString()) : '';
export const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

export function Head(title: string, sub: string, actions: Child = null, extra: Child = null): HTMLElement {
  return html`<div class="ctl-head"><div class="grow"><h1>${title}${extra}</h1>${sub ? html`<p>${sub}</p>` : null}</div>
    <div class="ctl-head-actions">${actions}</div></div>` as HTMLElement;
}

export function Callout(tone: 'info' | 'warn' | 'err', icon: string, body: Child): HTMLElement {
  return html`<div class=${`ctl-callout ${tone}`}>${Icon(icon, { size: 'sm' })}<span>${body}</span></div>` as HTMLElement;
}

export const message = (failure: unknown) => failure instanceof ApiError ? failure.message : 'Something went wrong. Try again.';

/** A Controls select: Evlet's Dropdown over [value, label] pairs. */
export function Select(props: { id: string; value: () => string; options: () => Array<[string, string]>; onChange: (value: string) => void; label?: string; disabled?: boolean }) {
  return Dropdown({ id: props.id, label: props.label, ariaLabel: props.label, value: props.value, disabled: props.disabled, className: 'ctl-select',
    choices: () => props.options().map(([value, label]) => ({ value, label })), onChange: value => { if (value !== null) props.onChange(value); } });
}

export const field = (label: string, id: string, control: Node, hint: () => string | null = () => null, error: () => string | null = () => null) =>
  html`<div class="ctl-field"><label for=${id}>${label}</label>${control}
    ${() => error() ? html`<span class="ctl-err">${error()}</span>` : hint() ? html`<span class="ctl-hint">${hint()}</span>` : null}</div>`;

export function ask(title: string, body: Child, yes: string, run: (() => unknown) | null, danger = false) {
  openModal({ title, narrow: true, icon: danger ? 'trash-2' : 'info', tint: danger ? 'rose' : 'blue',
    body: () => html`<p class="ctl-confirm">${body}</p>`,
    footer: close => run ? DialogFooter({ cancel: close, submit: { label: yes, variant: danger ? 'danger' : 'primary', onClick: () => { close(); void run(); } } })
      : DialogFooter({ submit: { label: yes, onClick: close } }) });
}
