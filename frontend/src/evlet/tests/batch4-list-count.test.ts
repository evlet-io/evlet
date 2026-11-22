import { afterEach, describe, expect, it, vi } from 'vitest';
import { adminRoutes, click, requests, screen, start, stopAll, text, until } from './screens-helpers';
import { copy, type Sent } from './server';

afterEach(() => { vi.useRealTimers(); stopAll(); });

const query = (sent: Sent) => Object.fromEntries(new URL(sent.url, 'http://x').searchParams);

// The server counts a list up to a cap (docs/performance.md#list-counts) and says so with total_capped.
function capped(sent: Sent) {
  const page = copy(screen.customers_page1);
  const offset = Number(query(sent).offset ?? 0);
  page.body.offset = offset;
  page.body.total = Math.max(10000, offset + page.body.items.length);
  page.body.total_capped = true;
  return page;
}

describe('capped list counts', () => {
  it('reads "10,000+" and keeps a next page past the cap', async () => {
    const { host, sent } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, capped]], '/app/customer');
    await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('10,000+ customers'));
    expect(text(host.querySelector('.ph .count'))).toBe('10,000+');
    expect(text(host.querySelector('.ev-pager span'))).toBe('1–50 of 10,000+');
    for (let page = 1; page < 3; page++) click(host.querySelector('.ev-pager [aria-label="Next page"]'));
    await until(() => expect(requests(sent, 'GET', /\/api\/customer\?/).map(query).pop()).toMatchObject({ offset: '100' }));
    expect(host.querySelector<HTMLButtonElement>('.ev-pager [aria-label="Next page"]')!.disabled).toBe(false);
  });

  it('shows the exact total when the server did not cap it', async () => {
    const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, screen.customers_page1]], '/app/customer');
    await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('55 customers'));
    expect(text(host.querySelector('.ev-pager span'))).toBe('1–50 of 55');
  });
});
