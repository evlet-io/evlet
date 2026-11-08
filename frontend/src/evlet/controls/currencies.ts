// Controls, Build: Currencies (docs/controls.md#currencies). How each currency code is shown on this site:
// a text symbol or a small image, before or after the amount, with or without a space. Codes left out show
// as they always have ("AED 25,200.00"). The whole set is one setting, display.currencies, saved through the
// change bar and audited; only admins change it. The preview uses the draft, so it shows what Save would give.

import { html, when } from '../dom';
import { cell, derive } from '../reactive';
import { Button, Switch } from '../ui/base';
import { toast } from '../ui/layers';
import { moneyChild } from '../ui/money';
import type { CurrencyDisplay } from '../model/format';
import { Callout, Head, type ControlsContext } from './app';
import { idOf } from './drafts';

const ID = idOf('setting', 'display.currencies');
export const MAX_IMAGE = 32 * 1024;
export const MAX_PIXELS = 128;
type Displays = Record<string, CurrencyDisplay>;
const fresh = (code: string): CurrencyDisplay => ({ symbol: code, image: null, position: 'prefix', space: true, tint: false });

/**
 * The type of image bytes, read from the bytes and never from the name: PNG, WebP or null. The server checks
 * again, more strictly, and keeps the final word.
 */
export function sniffImage(bytes: Uint8Array): 'image/png' | 'image/webp' | null {
  const png = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (png.every((byte, index) => bytes[index] === byte)) return 'image/png';
  const text = (from: number, to: number) => String.fromCharCode(...bytes.slice(from, to));
  if (bytes.length >= 12 && text(0, 4) === 'RIFF' && text(8, 12) === 'WEBP') return 'image/webp';
  return null;
}

/** A chosen file as a data: URI, or the reason it can't be a currency symbol. */
export async function readSymbol(file: Blob): Promise<{ uri: string } | { error: string }> {
  if (file.size > MAX_IMAGE) return { error: `The image is ${(file.size / 1024).toFixed(1)} KB. Use one of at most 32 KB.` };
  const bytes = new Uint8Array(await file.arrayBuffer());
  const kind = sniffImage(bytes);
  if (!kind) return { error: 'Use a PNG or WebP image. SVG and other formats are not accepted.' };
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return { uri: `data:${kind};base64,${btoa(binary)}` };
}

function imageSize(uri: string): Promise<[number, number] | null> {
  return new Promise(resolve => {
    const image = new Image();
    image.onload = () => resolve([image.naturalWidth, image.naturalHeight]);
    image.onerror = () => resolve(null);
    image.src = uri;
  });
}

export function CurrenciesPage(ctx: ControlsContext): HTMLElement {
  const { drafts } = ctx;
  const admin = () => ctx.state.get().me.role === 'admin';
  const displays = (): Displays => drafts.value<Displays>(ID) ?? {};
  const codes = derive(() => Object.keys(displays()).sort().join(' '));
  const adding = cell(''), addError = cell<string | null>(null);
  const imageErrors = cell<Record<string, string>>({});
  const put = (code: string, change: Partial<CurrencyDisplay> | null) => {
    const next = { ...displays() };
    if (change === null) delete next[code]; else next[code] = { ...fresh(code), ...next[code], ...change };
    drafts.set(ID, next);
  };
  const add = () => {
    const code = adding.peek().trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(code)) { addError.set('Enter a three-letter ISO code, such as AED.'); return; }
    if (displays()[code]) { addError.set(`${code} is already listed.`); return; }
    put(code, {});
    adding.set('');
    addError.set(null);
  };
  const choose = async (code: string, input: HTMLInputElement) => {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    const fail = (text: string) => imageErrors.set({ ...imageErrors.peek(), [code]: text });
    const read = await readSymbol(file);
    if ('error' in read) { fail(read.error); return; }
    const size = await imageSize(read.uri);
    if (!size) { fail('This image could not be read. It may be damaged.'); return; }
    if (size[0] > MAX_PIXELS || size[1] > MAX_PIXELS) { fail(`The image is ${size[0]} x ${size[1]} pixels. Use one of at most 128 x 128.`); return; }
    const { [code]: _, ...rest } = imageErrors.peek();
    imageErrors.set(rest);
    put(code, { image: read.uri, tint: true });
  };

  const row = (code: string) => {
    const value = () => displays()[code] ?? fresh(code);
    const disabled = () => !admin();
    const file = html`<input type="file" accept="image/png,image/webp" hidden aria-label=${`Image symbol for ${code}`}
      on:change=${(event: Event) => void choose(code, event.target as HTMLInputElement)}>` as HTMLInputElement;
    return html`<div class="ctl-cur" data-code=${code}>
      <div class="ctl-cur-head"><strong class="mono">${code}</strong>
        <span class="ctl-cur-preview" aria-label=${() => `Preview of ${code}`}>${() => moneyChild('1234.5', code, 2, value())}<span class="muted">·</span>${() => moneyChild('-75', code, 2, value())}</span>
        <span class="grow"></span>
        ${Button({ label: 'Reset to default', size: 'small', disabled, onClick: () => put(code, null) })}</div>
      <div class="ctl-cur-grid">
        <div class="ctl-field"><label for=${`ctl-cur-sym-${code}`}>Symbol</label>
          <input class="input" id=${`ctl-cur-sym-${code}`} maxlength="8" disabled=${() => disabled() || Boolean(value().image)} prop:value=${() => value().symbol}
            on:change=${(event: Event) => { const text = (event.target as HTMLInputElement).value.trim(); put(code, { symbol: text || code }); }}>
          <span class="ctl-hint">${() => value().image ? 'The image is shown instead. Text-only places, such as filters and CSV, show the code.' : html`Text such as $, €, <bdi>د.إ</bdi> or the code itself.`}</span></div>
        <div class="ctl-field"><span class="lbl">Image symbol</span>
          <div class="ctl-line">${file}
            ${Button({ label: () => value().image ? 'Replace image' : 'Upload image', icon: 'upload', size: 'small', disabled, onClick: () => file.click() })}
            ${when(() => Boolean(value().image), () => Button({ label: 'Remove image', size: 'small', variant: 'soft-danger', disabled, onClick: () => put(code, { image: null, tint: false }) }))}</div>
          ${() => imageErrors.get()[code] ? html`<span class="ctl-err" role="alert">${imageErrors.get()[code]}</span>`
            : html`<span class="ctl-hint">PNG or WebP, at most 32 KB and 128 x 128 pixels. SVG is not accepted.</span>`}</div>
        <div class="ctl-field"><span class="lbl">Position</span>
          <div class="seg sm" role="radiogroup" aria-label=${`Position of the ${code} symbol`}>
            ${(['prefix', 'suffix'] as const).map(side => html`<button type="button" role="radio" disabled=${disabled} class:on=${() => value().position === side}
              aria-checked=${() => String(value().position === side)} on:click=${() => put(code, { position: side })}>${side === 'prefix' ? 'Before the amount' : 'After the amount'}</button>`)}</div></div>
        <div class="ctl-field"><span class="lbl">Spacing</span>
          ${Switch({ on: () => value().space, label: 'Space between symbol and amount', disabled, onChange: next => put(code, { space: next }) })}
          ${when(() => Boolean(value().image), () => Switch({ on: () => Boolean(value().tint), label: 'Match the text colour', disabled,
            onChange: next => put(code, { tint: next }) }))}</div>
      </div></div>`;
  };

  return html`<div>
    ${Head('Currencies', 'How amounts show their currency on this site: in forms, lists, boards, totals, reports and print designs. Separators and decimals follow each person’s region and do not change here.')}
    ${() => admin() ? null : Callout('info', 'lock', 'Only an admin can change how currencies are shown.')}
    ${() => drafts.errors.get().has(ID) ? Callout('err', 'info', drafts.errors.get().get(ID)!) : null}
    <section class="ctl-card"><div class="ctl-card-head"><div><h2>Currency display</h2>
        <p>A code that is not listed shows as it always has: the code, a space, then the amount, such as AED 1,234.50.</p></div></div>
      ${() => codes.get() ? codes.get().split(' ').map(row)
        : html`<div class="ctl-empty">Every currency shows its code before the amount.</div>`}
      ${() => admin() ? html`<div class="ctl-pad ctl-line">
        <input class="input ctl-cur-code" maxlength="3" placeholder="AED" aria-label="Currency code to add" prop:value=${adding}
          on:input=${(event: Event) => { adding.set((event.target as HTMLInputElement).value.toUpperCase()); addError.set(null); }}
          on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') { event.preventDefault(); add(); } }}>
        ${Button({ label: 'Add currency', icon: 'plus', size: 'small', onClick: add })}
        ${() => addError.get() ? html`<span class="ctl-err" role="alert">${addError.get()}</span>` : null}
        ${() => drafts.overridden(ID) ? Button({ label: 'Reset all to default', size: 'small', onClick: () => { drafts.reset(ID); toast('Every currency goes back to its code when you save'); } }) : null}</div>` : null}
    </section>
    <p class="muted ctl-small ctl-cur-note">Changes are saved with the change bar and recorded in the audit log. CSV exports keep plain numbers, with the code in the column name. People see a change on their next page load.</p>
  </div>` as HTMLElement;
}
