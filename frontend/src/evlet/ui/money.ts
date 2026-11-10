// Money on screen: the amount with the site's currency symbol (model/format.ts, currencyDisplay). A text
// symbol is plain text; an image symbol is an <img> at text height on the number's baseline, with its ISO
// code as alt text for screen readers and copying. "Match the text colour" paints the image's shape in the
// current text colour, so a one-colour symbol reads in light and dark themes alike.

import { html, type Child } from '../dom';
import { currencyDecimals, currencyDisplay, formatMoney, formatNumber, INVALID_AMOUNT, symbolText } from '../model/format';

/**
 * The symbol of a code as a node: an image, or a text span. The image's data: URI was checked when the
 * schema was read (PNG or WebP only), so it is set as a property here rather than through a template.
 */
export function CurrencySymbol(code: string, display = currencyDisplay(code)): HTMLElement {
  if (!display.image) return html`<span class="money-sym">${symbolText(code, display)}</span>` as HTMLElement;
  const image = document.createElement('img');
  image.className = display.tint ? 'money-img tint' : 'money-img';
  image.alt = code;
  image.src = display.image;
  image.draggable = false;
  if (display.tint) image.style.setProperty('--money-mask', `url("${display.image}")`);
  return image;
}

/**
 * An amount with its symbol, as the site shows the code (or as `display` says, for a preview), at the code's
 * decimal places (currencyDecimals) unless `places` says otherwise. Text symbols give the same text as
 * formatMoney; an image symbol gives a span with the image before or after the number. Empty values give ''.
 */
export function moneyChild(value: unknown, currency: string, places = currencyDecimals(currency), display = currencyDisplay(currency)): Child {
  if (value === null || value === undefined || value === '') return '';
  if (!display.image) return formatMoney(value as string, { currency, scale: places, display });
  const number = formatNumber(value as string | number, places);
  if (number === INVALID_AMOUNT) return number;
  const gap = display.space ? ' ' : '';
  return display.position === 'prefix'
    ? html`<span class="money">${CurrencySymbol(currency, display)}${gap}${number}</span>`
    : html`<span class="money">${number}${gap}${CurrencySymbol(currency, display)}</span>`;
}

/** An amount that follows its value and currency as they change, at the currency's decimals (or `places`). */
export const Money = (value: () => unknown, currency: () => string, places?: () => number): Child =>
  () => moneyChild(value(), currency(), places ? places() : currencyDecimals(currency()));
