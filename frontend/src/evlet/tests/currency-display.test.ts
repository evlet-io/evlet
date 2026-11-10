// Currency display per site: a text prefix, a text suffix, an image symbol and right-to-left text, in the
// formatter, the field at rest and in its input, and the Controls page that edits them.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { formatMoney, setCurrencyDisplays, setRegion } from '../model/format';
import { displayValue, FieldControl, localField } from '../ui/fields';
import { moneyChild } from '../ui/money';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { readSymbol, sniffImage } from '../controls/currencies';
import { server, type Sent } from './server';
import { byText, click, stopAll, text, typeInto, until } from './screens-helpers';

// A 1 x 1 PNG.
const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); setCurrencyDisplays({}); setRegion({}); });

describe('formatting money with the site display', () => {
  it('keeps today’s output until a code is changed', () => {
    setCurrencyDisplays({});
    expect(formatMoney('25200')).toBe('AED 25,200.00');
    expect(formatMoney('99', { currency: 'USD', scale: 0 })).toBe('USD 99');
  });

  it('puts a text symbol before or after the amount, with or without a space', () => {
    setCurrencyDisplays({ USD: { symbol: '$', image: null, position: 'prefix', space: false }, EUR: { symbol: '€', image: null, position: 'suffix', space: true } });
    expect(formatMoney('1234.5', { currency: 'USD' })).toBe('$1,234.50');
    expect(formatMoney('-75', { currency: 'USD' })).toBe('$-75.00');
    expect(formatMoney('1234.5', { currency: 'EUR' })).toBe('1,234.50 €');
    // Separators and decimals still follow the person's region.
    setRegion({ number: '1.234,56' });
    expect(formatMoney('1234.5', { currency: 'EUR' })).toBe('1.234,50 €');
    expect(formatMoney('1234.5')).toBe('AED 1.234,50');
  });

  it('isolates a right-to-left symbol so it stays on its side of the digits', () => {
    setCurrencyDisplays({ AED: { symbol: 'د.إ', image: null, position: 'suffix', space: true } });
    expect(formatMoney('1234.5')).toBe('1,234.50 ⁨د.إ⁩');
    setCurrencyDisplays({ AED: { symbol: 'د.إ', image: null, position: 'prefix', space: true } });
    expect(formatMoney('1234.5')).toBe('⁨د.إ⁩ 1,234.50');
    const node = moneyChild('1234.5', 'AED');
    expect(node).toBe('⁨د.إ⁩ 1,234.50');
  });

  it('shows an image symbol as an image with its code as alt text, and as the code where only text fits', () => {
    setCurrencyDisplays({ AED: { symbol: 'AED', image: PIXEL, position: 'suffix', space: true, tint: true } });
    expect(formatMoney('1234.5')).toBe('1,234.50 AED');
    const span = moneyChild('1234.5', 'AED') as HTMLElement;
    expect(span.className).toBe('money');
    const image = span.querySelector('img')!;
    expect(image.alt).toBe('AED');
    expect(image.getAttribute('src')).toBe(PIXEL);
    expect(image.classList.contains('tint')).toBe(true);
    expect(image.style.getPropertyValue('--money-mask')).toContain(PIXEL);
    // The image follows the number, after a no-break space; the number is unchanged.
    expect(span.firstChild!.textContent).toBe('1,234.50');
    expect(span.lastChild).toBe(image);
    expect(moneyChild('', 'AED')).toBe('');
  });

  it('ignores entries that are not well formed, SVG included', () => {
    setCurrencyDisplays({ aed: { symbol: 'x' }, USD: { symbol: '$', image: 'data:image/svg+xml;base64,PHN2Zz4=', position: 'prefix', space: false } } as any);
    expect(formatMoney('1', { currency: 'USD' })).toBe('$1.00');
    expect(moneyChild('1', 'USD')).toBe('$1.00');
    expect(formatMoney('1')).toBe('AED 1.00');
  });
});

describe('Currency fields', () => {
  const meta = { name: 'total', type: 'currency', label: 'Total', scale: 2, currency: 'AED' };

  it('show the image at rest and in the input adornment on its side', () => {
    setCurrencyDisplays({ AED: { symbol: 'AED', image: PIXEL, position: 'suffix', space: true } });
    const field = localField(meta, '5616.35');
    const host = document.createElement('div');
    running.push(mount(host, () => [displayValue(field), FieldControl(field).element]));
    const rest = host.querySelector('.tx.num')!;
    expect(text(rest)).toBe('5,616.35');
    expect(rest.querySelector('img')?.alt).toBe('AED');
    const boxes = [...host.querySelectorAll('.in-affix > .in-box')];
    expect(boxes).toHaveLength(1);
    expect(host.querySelector('.in-affix')!.lastElementChild).toBe(boxes[0]);
    expect(boxes[0].querySelector('img')?.alt).toBe('AED');
    expect((host.querySelector('.in-affix input') as HTMLInputElement).value).toBe('5,616.35');
  });

  it('show a text prefix in the input as before', () => {
    setCurrencyDisplays({ AED: { symbol: 'د.إ', image: null, position: 'prefix', space: true } });
    const field = localField(meta, '10');
    const host = document.createElement('div');
    running.push(mount(host, () => [displayValue(field), FieldControl(field).element]));
    expect(text(host.querySelector('.tx.num'))).toBe('⁨د.إ⁩ 10.00');
    expect(host.querySelector('.in-affix')!.firstElementChild!.className).toBe('in-box');
    expect(text(host.querySelector('.in-box'))).toBe('⁨د.إ⁩');
  });
});

describe('the image a site uploads', () => {
  const bytes = (data: string) => Uint8Array.from(atob(data.split(',')[1]), char => char.charCodeAt(0));

  it('is read by its bytes, not its name or type', async () => {
    expect(sniffImage(bytes(PIXEL))).toBe('image/png');
    expect(sniffImage(new TextEncoder().encode('RIFF\x00\x00\x00\x00WEBPVP8L'))).toBe('image/webp');
    expect(sniffImage(new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"></svg>'))).toBeNull();
    expect(await readSymbol(new Blob([bytes(PIXEL)], { type: 'application/octet-stream' }))).toEqual({ uri: PIXEL });
    expect(await readSymbol(new Blob(['<svg></svg>'], { type: 'image/png' }))).toEqual({ error: 'Use a PNG or WebP image. SVG and other formats are not accepted.' });
    expect(await readSymbol(new Blob([new Uint8Array(33 * 1024)]))).toEqual({ error: 'The image is 33.0 KB. Use one of at most 32 KB.' });
  });
});

describe('Controls, Currencies', () => {
  const state = (role: string) => ({
    instance: { name: 'Acme', host: 'localhost', evlet: '', version: 1 }, me: { id: 1, username: 'owner', role }, apps: [],
    roles: [], models: [], scopes: { '': 'All records' }, settings: {}, controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0,
    users: { active: 1, total: 1 }, jobs: { failed: 0 }, audit: [], two_step: false, links: false, client: '127.0.0.1', remove_fields: null, instance_fields: true,
  });
  const item = (value: unknown) => ({ kind: 'setting', key: 'display.currencies', file: {}, value, changed_by: null, changed_at: null });

  function open(role = 'admin', value: unknown = null) {
    window.history.replaceState(null, '', '/controls/currencies');
    let saved = value;
    const stub = server([
      ['GET', /\/api\/auth\/session$/, () => ({ status: 200, body: { user: { id: 1, username: 'owner', role }, csrf: 'token' }, etag: null })],
      ['GET', /\/api\/_controls\/state$/, () => ({ status: 200, body: state(role), etag: null })],
      ['GET', /\/api\/_controls\/overrides$/, () => ({ status: 200, body: { version: 1, problems: [], items: [item(saved)] }, etag: null })],
      ['POST', /\/api\/_controls\/overrides$/, (sent: Sent) => { saved = sent.body.changes[0].value; return { status: 200, body: { version: 2, saved: 1 }, etag: null }; }],
    ] as any);
    const router = controlsRouter();
    const stop = router.start();
    const host = document.createElement('div');
    document.body.append(host);
    const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
    running.push(() => { dispose(); stop(); host.remove(); });
    return { host, sent: stub.sent };
  }

  it('adds a code, previews the draft and saves it through the change bar', { timeout: 10000 }, async () => {
    const { host, sent } = open();
    await until(() => expect(host.querySelector('.ctl-cur-code')).not.toBeNull());
    typeInto(host.querySelector('.ctl-cur-code') as HTMLInputElement, 'usd');
    click(byText(host, 'button', 'Add currency'));
    await until(() => expect(host.querySelector('[data-code="USD"]')).not.toBeNull());
    const symbol = host.querySelector('#ctl-cur-sym-USD') as HTMLInputElement;
    symbol.value = '$';
    symbol.dispatchEvent(new Event('change', { bubbles: true }));
    click(byText(host, '[data-code="USD"] button', 'After the amount'));
    await until(() => expect(text(host.querySelector('[data-code="USD"] .ctl-cur-preview'))).toBe('1,234.50 $·-75.00 $'));
    click(byText(host, '.ctl-changebar button', 'Save changes'));
    await until(() => expect(sent.some(item => item.method === 'POST')).toBe(true));
    expect(sent.find(item => item.method === 'POST')!.body).toEqual({ changes: [{ kind: 'setting', key: 'display.currencies',
      value: { USD: { symbol: '$', image: null, position: 'suffix', space: true, tint: false, decimals: 2 } } }] });
  });

  it('shows the image in the preview and resets a code to the default', async () => {
    const { host } = open('admin', { AED: { symbol: 'AED', image: PIXEL, position: 'prefix', space: true, tint: true } });
    await until(() => expect(host.querySelector('[data-code="AED"] .ctl-cur-preview img')).not.toBeNull());
    expect((host.querySelector('[data-code="AED"] .ctl-cur-preview img') as HTMLImageElement).alt).toBe('AED');
    click(byText(host, '[data-code="AED"] button', 'Reset to default'));
    await until(() => expect(host.querySelector('[data-code="AED"]')).toBeNull());
    expect(text(host.querySelector('.ctl-changebar'))).toContain('1 unsaved change');
  });

  it('is read only for roles that open Controls but are not admin', async () => {
    const { host } = open('ops', { USD: { symbol: '$', image: null, position: 'prefix', space: false, tint: false } });
    await until(() => expect(host.querySelector('[data-code="USD"]')).not.toBeNull());
    expect(text(host)).toContain('Only an admin can change how currencies are shown.');
    expect((host.querySelector('#ctl-cur-sym-USD') as HTMLInputElement).disabled).toBe(true);
    expect(host.querySelector('.ctl-cur-code')).toBeNull();
  });
});
