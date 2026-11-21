// Presentation helpers shared by Controls and personal Settings. Keep this module independent of
// either route shell so opening personal settings does not load the administrative screens.
import { html, when, type Child } from '../dom';
import { ApiError } from '../api';
import { Icon, IconButton } from './base';
import { DialogFooter, openModal } from './layers';
import { Dropdown } from './pickers';
import { formatDateTime } from '../model/format';
import type { Readable } from '../reactive';

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

/** What a setting row needs from Controls drafts: whether a draft differs from the file, how to reset it, and the
 * server's errors by draft id. */
export interface SettingDrafts { overridden(id: string): boolean; reset(id: string): void; readonly errors: Readable<Map<string, string>> }

/** The Changed tag and reset button a setting's title carries while its draft differs from the file. */
export const SettingMark = (drafts: SettingDrafts, id: string) => () => drafts.overridden(id)
  ? html`<span class="ctl-tag">Changed</span>${IconButton({ icon: 'rotate-ccw', size: 'small', tip: 'Reset to the file value', onClick: () => drafts.reset(id) })}`
  : null;

/** The server's error for a draft id, while there is one. */
export const SettingError = (drafts: SettingDrafts, id: string) =>
  when(() => drafts.errors.get().has(id), () => html`<span class="ctl-err">${() => drafts.errors.get().get(id)}</span>`);

/** One Controls or Settings row: a title and text on the left, the control on the right. With drafts and an id,
 * the title carries SettingMark and SettingError shows under the text. */
export function SettingRow(options: { drafts?: SettingDrafts; id?: string | null; title: Child; text?: Child; control: Child }): HTMLElement {
  const { drafts, id } = options;
  const text = options.text === undefined || options.text === null || options.text === '' ? null : html`<p>${options.text}</p>`;
  return html`<div class="ctl-setrow"><div class="grow"><strong>${options.title} ${drafts && id ? SettingMark(drafts, id) : null}</strong>${text}${drafts && id ? SettingError(drafts, id) : null}</div>
    <div class="ctl-setctl">${options.control}</div></div>` as HTMLElement;
}

/** row, mark and error for a panel whose draft ids come from id(key); a null key is a row with no draft. */
export const settingKit = (drafts: SettingDrafts, id: (key: string) => string) => ({
  row: (key: string | null, title: string, text: Child, control: Child) => SettingRow({ drafts, id: key ? id(key) : null, title, text, control }),
  mark: (key: string) => SettingMark(drafts, id(key)),
  error: (key: string) => SettingError(drafts, id(key)),
});
