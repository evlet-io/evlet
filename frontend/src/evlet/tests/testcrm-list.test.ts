// testCRM end to end in jsdom: the real screens over responses recorded from the engine
// (scripts/generate_testcrm_fixtures.py), as sara, a sales person who sees only her own deals.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, lastToast, press, stopAll, text, typeInto, until } from './screens-helpers';
import { scenario } from './testcrm-helpers';

afterEach(stopAll);

const submit = (host: HTMLElement) => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
// The number is the pinned first column (Meta.list_fields), the title the second.
const titles = (host: HTMLElement) => [...host.querySelectorAll('tbody tr[data-key] td:nth-child(3)')].map(text);
const listed = (run: ReturnType<typeof scenario>, sent: { method: string; url: string }[]) =>
  sent.filter(item => item.method === 'GET' && /\/api\/deal\?/.test(item.url)).map(item => Object.fromEntries(new URL(item.url, 'http://x').searchParams));

describe('testCRM: sign in and the deals list', () => {
  it('signs in, then the sidebar leads to Deals with only her deals and the pipeline measures', async () => {
    const run = scenario('sara_list', { signedIn: false });
    const { host } = run.start('/');
    await until(() => expect(host.querySelector('h1')?.textContent).toBe('Welcome back'));
    typeInto(host.querySelector<HTMLInputElement>('#ev-username')!, 'sara');
    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'not the password');
    submit(host);
    await until(() => expect(text(host.querySelector('[role="alert"]'))).toBe('Invalid username or password'));
    typeInto(host.querySelector<HTMLInputElement>('#ev-password')!, 'a long local test password');
    submit(host);
    await until(() => expect(location.pathname).toBe('/docs'));
    // Documents (with a State or lines) and records, from the schema alone.
    const nav = [...host.querySelectorAll('a.nav-item')].map(text);
    expect(nav).toEqual(expect.arrayContaining(['Deals', 'Leads', 'Organizations', 'Contacts', 'Products', 'Tasks', 'Bookings']));
    click(byText(host, 'a.nav-item', 'Deals'));
    await until(() => expect(location.pathname).toBe('/app/deal'));
    await until(() => expect(titles(host).length).toBe(8));
    expect(titles(host)).not.toContain('Fleet and warehouse ERP');
    expect([...host.querySelectorAll('thead th')].map(text).slice(1)).toEqual(['Number', 'Title', 'Organization', 'Owner', 'Stage', 'Total', 'Expected close']);
    await until(() => expect([...host.querySelectorAll('.sum-t')].map(item => text(item.querySelector('span')))).toEqual(['Pipeline', 'Won', 'Won this month', 'Deals']));
    await until(() => expect(text(byText(host, '.ev-list [role="tab"]', /^All deals/))).toBe('All deals8'));
    expect(run.misses).toEqual([]);
  });

  it('searches, filters by a saved view per stage and sorts on the server', async () => {
    const run = scenario('sara_list');
    const { host, sent } = run.start('/app/deal');
    await until(() => expect(titles(host).length).toBe(8));
    typeInto(host.querySelector<HTMLInputElement>('.toolbar input')!, 'planning');
    await until(() => expect(titles(host)).toEqual(['Production planning module']));
    expect(listed(run, sent).pop()).toMatchObject({ q: 'planning', offset: '0', limit: '50' });
    typeInto(host.querySelector<HTMLInputElement>('.toolbar input')!, '');
    await until(() => expect(titles(host).length).toBe(8));
    click(byText(host, '.ev-list [role="tab"]', /^Proposal/));
    await until(() => expect(titles(host)).toEqual(expect.arrayContaining(['Production planning module', 'Project costing and BOQ'])));
    expect(JSON.parse(listed(run, sent).pop()!.filters)).toEqual({ stage: 'proposal' });
    click(byText(host, '.ev-list [role="tab"]', /^All deals/));
    await until(() => expect(titles(host).length).toBe(8));
    const total = host.querySelector<HTMLElement>('thead th[data-key="total"]')!;
    click(total);
    click(total);
    await until(() => expect(titles(host)[0]).toBe('Group rollout, 150 sites'));
    expect(listed(run, sent).pop()).toMatchObject({ order_by: '-total' });
    expect(run.misses).toEqual([]);
  });

  it('moves a card on the board with the keyboard, and finds deals in the command palette', async () => {
    const run = scenario('sara_list');
    const { host, sent } = run.start('/app/deal?view=board');
    await until(() => expect(host.querySelectorAll('.board .card').length).toBe(8));
    const column = (label: string) => byText(host, '.col-k', new RegExp(`^${label}`))!;
    expect([...host.querySelectorAll('.col-h .pill')].map(text)).toEqual(['Lead', 'Qualified', 'Proposal', 'Negotiation', 'Won', 'Lost', 'Void']);
    const portal = byText(column('Qualified'), '.card', /Distributor portal/)!;
    press(portal, 'ArrowRight', { altKey: true });
    await until(() => expect(byText(column('Proposal'), '.card', /Distributor portal/)).not.toBeNull());
    await until(() => expect(sent.filter(item => item.method === 'POST' && /moves\/proposal$/.test(item.url))).toHaveLength(1));
    // Won deals cannot be moved by the board: the reason is shown and nothing is sent.
    press(byText(column('Won'), '.card', /AMC renewal/)!, 'ArrowRight', { altKey: true });
    expect(lastToast()).not.toBe('');
    expect(sent.filter(item => item.method === 'POST')).toHaveLength(1);

    press(document.body, 'k', { metaKey: true });
    const palette = () => document.querySelector<HTMLElement>('.modal.palette')!;
    await until(() => expect(palette()).not.toBeNull());
    typeInto(palette().querySelector<HTMLInputElement>('.pal-in input')!, 'rollout');
    await until(() => expect([...palette().querySelectorAll('.pal-sec[aria-label="Records"] .pal-it .lbl')].map(text))
      .toEqual(expect.arrayContaining(['Group rollout, 150 sites', 'ERP rollout, 3 entities'])));
    expect(run.misses.filter(miss => !/[?&]q=rollout/.test(miss))).toEqual([]);
  });
});
