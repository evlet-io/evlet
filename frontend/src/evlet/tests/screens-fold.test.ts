// Collapsible sections on the generated record page: the first section is the primary area, which never folds; the
// others are "more information" rows that open and close. The app's starting state, the person's saved choice and
// Reset layout, a #field link, and a refused create that opens the section with the error.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, recorded, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy } from './server';

afterEach(stopAll);

function schema() {
  const value = copy(screen.schema);
  value.body.models.find((model: any) => model.name === 'order').layout = [
    { label: 'Main', fields: ['title', 'customer', 'stage'], collapsible: false, collapsed: false },
    { label: 'Dates', fields: ['issued', 'due'], collapsible: true, collapsed: true },
    { label: 'Notes', fields: ['reason', 'remark'], collapsible: true, collapsed: 'empty' },
    { label: 'Totals', fields: ['total', 'line_count'], collapsible: true, collapsed: false },
  ];
  return value;
}
const routes = (extra: Route[] = []): Route[] => [
  ['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema()],
  ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
  ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
  ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
  ...extra,
];
const memory = () => {
  const storage = new Map<string, string>();
  return { storage, store: { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => void storage.set(key, value),
    removeItem: (key: string) => void storage.delete(key) } as unknown as Storage };
};
const section = (host: Element, title: string) => host.querySelector<HTMLElement>(title === 'Main' ? '.ev-cmp-main' : `.ev-more-row[data-section="${title}"]`)!;
const expanded = (host: Element, title: string) => section(host, title).querySelector('.ev-more-toggle')?.getAttribute('aria-expanded') ?? 'plain';

describe('collapsible record sections', () => {
  it('starts each section as the app says, remembers the person’s choice and resets it from the More menu', async () => {
    const { storage, store } = memory();
    const { host } = start(routes(), '/app/order/0000000000001', { storage: store });
    await until(() => expect(section(host, 'Dates')).not.toBeNull());
    // Main can't collapse; Dates starts closed; Notes is empty on this record, so it starts closed; Totals starts open.
    expect(['Main', 'Dates', 'Notes', 'Totals'].map(title => expanded(host, title))).toEqual(['plain', 'false', 'false', 'true']);
    // Closed, a row sums its values up on one line.
    expect(text(section(host, 'Dates').querySelector('.ev-more-sum'))).toMatch(/^Issued: .+ · Due: .+$/);
    // Reason applies only to some stages, so the line leaves it out here.
    expect(text(section(host, 'Notes').querySelector('.ev-more-sum'))).toBe('Remark: Not set');
    // Read-only and locked fields fold the same way: the section's contents are hidden.
    expect(section(host, 'Dates').querySelector<HTMLElement>('.ev-more-body')!.hidden).toBe(true);
    click(section(host, 'Dates').querySelector('.ev-more-toggle'));
    expect(expanded(host, 'Dates')).toBe('true');
    const key = [...storage.keys()].find(name => name.endsWith('.form.order.open'))!;
    expect(JSON.parse(storage.get(key)!)).toEqual({ Dates: true });
    stopAll();

    const again = start(routes(), '/app/order/0000000000001', { storage: store }).host;
    await until(() => expect(section(again, 'Dates')).not.toBeNull());
    expect(expanded(again, 'Dates')).toBe('true');
    click(again.querySelector('[aria-label="More actions"]'));
    click(byText(document.body, '[role="menuitem"]', 'Reset layout'));
    expect(expanded(again, 'Dates')).toBe('false');
    expect(JSON.parse(storage.get(key)!)).toEqual({});
  });

  it('Reset layout puts every section back to its starting state at once, on the same page', async () => {
    // A time-ordered text id (with letters), as every new record has.
    const textId = '01hff1w8vgqwf';
    const retext = (source: any) => JSON.parse(JSON.stringify(source).split('0000000000001').join(textId));
    const { store } = memory();
    const { host } = start(retext(routes()).map((route: any, index: number) => [route[0], new RegExp(routes()[index][1].source.split('0000000000001').join(textId)), route[2]]),
      `/app/order/${textId}`, { storage: store });
    await until(() => expect(section(host, 'Dates')).not.toBeNull());
    const starting = ['plain', 'false', 'false', 'true'];
    expect(['Main', 'Dates', 'Notes', 'Totals'].map(title => expanded(host, title))).toEqual(starting);
    for (const title of ['Dates', 'Notes', 'Totals']) click(section(host, title).querySelector('.ev-more-toggle'));
    expect(['Main', 'Dates', 'Notes', 'Totals'].map(title => expanded(host, title))).toEqual(['plain', 'true', 'true', 'false']);
    click(host.querySelector('[aria-label="More actions"]'));
    const reset = byText<HTMLButtonElement>(document.body, '[role="menuitem"]', 'Reset layout')!;
    expect(reset.getAttribute('aria-disabled') === 'true' || reset.disabled).toBe(false);
    click(reset);
    await until(() => expect(['Main', 'Dates', 'Notes', 'Totals'].map(title => expanded(host, title))).toEqual(starting));
    expect(['Dates', 'Notes', 'Totals'].map(title => section(host, title).querySelector<HTMLElement>('.ev-more-body')!.hidden)).toEqual([true, true, false]);
  });

  it('opens the section of a #field link and focuses the field', async () => {
    const { host } = start(routes(), '/app/order/0000000000001#due');
    await until(() => expect(section(host, 'Dates')).not.toBeNull());
    await until(() => expect(expanded(host, 'Dates')).toBe('true'));
    expect(section(host, 'Dates').contains(document.activeElement)).toBe(true);
    expect(document.activeElement!.closest('[data-field]')!.getAttribute('data-field')).toBe('due');
  });

  it('a new record opens sections that start closed when empty; a refused create opens the section with the error', async () => {
    const { host, sent } = start(routes([['POST', /\/api\/order$/, { status: 422, etag: null, body: { error: 'Validation failed', fields: { issued: 'Use a date this year' } } }]]),
      '/app/order/new?title=T');
    await until(() => expect(section(host, 'Dates')).not.toBeNull());
    expect(expanded(host, 'Notes')).toBe('true');
    expect(expanded(host, 'Dates')).toBe('false');
    click(byText(host, '.ev-savebar button', /^Create/));
    await until(() => expect(requests(sent, 'POST', /\/api\/order$/)).toHaveLength(1));
    await until(() => expect(expanded(host, 'Dates')).toBe('true'));
    expect(text(section(host, 'Dates').querySelector('.fs-err'))).toBe('1 error');
    await until(() => expect(document.activeElement!.closest('[data-field]')?.getAttribute('data-field')).toBe('issued'));
  });
});
