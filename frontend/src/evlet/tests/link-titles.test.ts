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
        return reply(ids.filter(item => item !== id(4)).map(item => ({ id: item, name: `Customer ${parseInt(item, 32)}` })));
      }],
      ['GET', /\/api\/product\?/, reply([{ id: id(1), name: 'Support hours' }])],
      ['GET', /\/api\/customer\/0000000000009$/, { status: 200, body: { id: id(9), name: 'Read already' }, etag: '"c9"' }],
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
});
