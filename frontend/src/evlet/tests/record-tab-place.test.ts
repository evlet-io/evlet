// The record page's tab bar sticks to the top of the scrolling view, and switching tabs keeps the page still
// (docs/ui.md, Record pages): jsdom has no layout, so the stuck case stubs the positions it reads.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, byText, click, recorded, start, stopAll, until, type Route } from './screens-helpers';

afterEach(() => { vi.restoreAllMocks(); stopAll(); });

const routes: Route[] = [...adminRoutes(),
  ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
  ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
  ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }]];

async function open() {
  const started = start(routes, '/app/order/0000000000001');
  await until(() => expect(started.host.querySelector('.doc-tabs [role="tab"]')).not.toBeNull());
  const view = started.host.querySelector<HTMLElement>('.view')!;
  view.style.overflowY = 'auto';
  const bar = started.host.querySelector<HTMLElement>('.doc-tabs')!;
  const other = [...bar.querySelectorAll<HTMLElement>('[role="tab"]')].find(tab => tab.getAttribute('aria-selected') !== 'true')!;
  return { ...started, view, bar, other };
}

describe('record tabs', () => {
  it('stick to the top of the scrolling area', async () => {
    const { bar } = await open();
    expect(bar.classList.contains('tabs-sticky')).toBe(true);
    expect(bar.getAttribute('role')).toBe('tablist');
  });

  it('keep the scroll position when the tab changes', async () => {
    const { view, other } = await open();
    view.scrollTop = 500;
    click(other);
    expect(other.getAttribute('aria-selected')).toBe('true');
    expect(view.scrollTop).toBe(500);
  });

  it('start the new tab right under the stuck tab bar, filling the view below it', async () => {
    const { host, view, bar, other } = await open();
    // The bar sticks at the top with a 14 px gap; the panels start 300 px down the content.
    bar.style.position = 'sticky'; bar.style.top = '0px'; bar.style.marginBottom = '14px';
    Object.defineProperty(view, 'clientHeight', { configurable: true, get: () => 600 });
    const real = Element.prototype.getBoundingClientRect;
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      return this.getAttribute('role') === 'tabpanel' ? new DOMRect(0, 300 - view.scrollTop, 100, 40) : real.call(this);
    });
    view.scrollTop = 500;
    click(other);
    expect(view.scrollTop).toBe(286);
    const panel = host.querySelector<HTMLElement>('[role="tabpanel"]')!;
    expect(panel.style.getPropertyValue('--tab-fill')).toBe('586px');
    // Above the stuck point the position stays.
    view.scrollTop = 120;
    click(byText(bar, '[role="tab"]', bar.querySelector('[aria-selected="false"]')!.textContent!)!);
    expect(view.scrollTop).toBe(120);
  });
});
