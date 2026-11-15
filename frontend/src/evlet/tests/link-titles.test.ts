// Link titles are read in batches: one list request per target model, then kept for the session.
import { describe, expect, it } from 'vitest';
import { AppClient } from '../app/client';
import { SchemaIndex } from '../model/schema';
import { apiLinks } from '../ui/fields';
import { id, responses, server } from './server';

const schema = new SchemaIndex(responses.schema.body);
const wait = () => new Promise(resolve => setTimeout(resolve, 5));
const reply = (items: Array<Record<string, unknown>>) => ({ status: 200, body: { items, total: items.length, limit: 100, offset: 0 }, etag: null });

describe('link titles', () => {
  it('reads every title a page asks for with one request per model and keeps them', async () => {
    const stub = server([
      ['GET', /\/api\/customer\?/, sent => {
        const ids: string[] = JSON.parse(new URL(sent.url, 'http://x').searchParams.get('filters')!).id__in;
        return reply(ids.filter(item => item !== id(4)).map(item => ({ id: item, title: `Customer ${parseInt(item, 32)}` })));
      }],
      ['GET', /\/api\/product\?/, reply([{ id: id(1), title: 'Support hours' }])],
      ['GET', /\/api\/customer\/0000000000009$/, { status: 200, body: { id: id(9), title: 'Read already' }, etag: '"c9"' }],
    ]);
    const api = new AppClient({ fetch: stub.fetch });
    const links = apiLinks(api, schema);
    // A record being read (a calculation lookup, say) gives its title without another request.
    const reading = api.get('customer', id(9));
    const titles = [3, 1, 4, 3, 2, 9].map(n => links.title('customer', id(n)));
    const product = links.title('product', id(1));
    await reading;
    await wait();
    expect(titles.map(title => title.peek())).toEqual(['Customer 3', 'Customer 1', `#${id(4)}`, 'Customer 3', 'Customer 2', 'Read already']);
    expect(product.peek()).toBe('Support hours');
    const lists = stub.sent.filter(item => !/\/[0-9a-z]{13}$/.test(item.url)).map(item => decodeURIComponent(item.url.replace(/^.*\/api\//, '')));
    expect(lists).toEqual([`customer?limit=4&offset=0&filters={"id__in":${JSON.stringify([1, 2, 3, 4].map(id))}}`,
      `product?limit=1&offset=0&filters={"id__in":["${id(1)}"]}`]);
    // Kept for the session: asking again sends nothing.
    links.title('customer', id(2));
    await wait();
    expect(stub.sent).toHaveLength(3);
  });

  it('updates held title and record cells after a rename without another list request', async () => {
    const stub = server([
      ['GET', /\/api\/customer\/0000000000001$/, { status: 200, body: { id: id(1), title: 'Before' }, etag: '"1"' }],
      ['PATCH', /\/api\/customer\/0000000000001$/, { status: 200, body: { id: id(1), title: 'After' }, etag: '"2"' }],
    ]);
    const api = new AppClient({ fetch: stub.fetch }), links = apiLinks(api, schema);
    await api.get('customer', id(1));
    const title = links.title('customer', id(1)), record = links.record!('customer', id(1));
    expect(title.peek()).toBe('Before');
    await api.update('customer', id(1), { title: 'After' }, '"1"');
    expect(title.peek()).toBe('After');
    expect(record.peek()?.title).toBe('After');
    expect(stub.sent).toHaveLength(2);
    window.dispatchEvent(new Event('evlet-session-ended'));
    expect(title.peek()).toBeNull(); expect(record.peek()).toBeNull();
    expect((api as any).listeners.size).toBe(0);
  });

  it('does not repopulate a cleared link source with a late list response', async () => {
    let finish!: (reply: any) => void;
    const stub = server([['GET', /\/api\/customer\?/, () => new Promise(resolve => { finish = resolve; })]]);
    const api = new AppClient({ fetch: stub.fetch }), links = apiLinks(api, schema);
    const title = links.title('customer', id(1));
    await wait(); links.clear!();
    finish(reply([{ id: id(1), title: 'Previous session' }]));
    await wait();
    expect(title.peek()).toBeNull();
  });
});
