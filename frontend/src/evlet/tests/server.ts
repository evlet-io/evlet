// A fetch stub that replays real backend responses recorded in api-fixture.json
// (scripts/generate_ui_framework_fixtures.py), and records what the client sent.
import fixture from './api-fixture.json';

export type Recorded = { status: number; body: any; etag: string | null };
export const responses = fixture as Record<string, Recorded>;
export interface Sent { method: string; url: string; headers: Record<string, string>; body: any }
type Reply = keyof typeof fixture | Recorded | ((sent: Sent) => Recorded | Promise<Recorded>);
type Route = [method: string, path: RegExp, reply: Reply];

/** Deep copy, so a test that changes a response does not change it for the next test. */
export const copy = <T>(value: T): T => structuredClone(value);

export function server(routes: Route[]) {
  const sent: Sent[] = [];
  const answer = async (reply: Reply, request: Sent) => typeof reply === 'function' ? await reply(request) : typeof reply === 'string' ? responses[reply] : reply;
  // Link titles are read in batches (?filters={"id__in":[...]}); without a route of its own, a batch is
  // answered from the routes of the single records, as the server would list them.
  const titles = async (url: string): Promise<Recorded | null> => {
    const parsed = new URL(url, 'http://127.0.0.1');
    const ids = (JSON.parse(parsed.searchParams.get('filters') ?? '{}') as { id__in?: number[] }).id__in;
    if (!ids) return null;
    const items: unknown[] = [];
    for (const id of ids) {
      const single = `${parsed.origin}${parsed.pathname}/${id}`;
      const route = routes.find(([verb, path]) => verb === 'GET' && path.test(single));
      const found = route ? await answer(route[2], { method: 'GET', url: single, headers: {}, body: undefined }) : null;
      if (found?.status === 200) items.push(copy(found.body));
    }
    return { status: 200, body: { items, total: items.length, limit: ids.length, offset: 0 }, etag: null };
  };
  const fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input), method = init.method ?? 'GET';
    const headers = Object.fromEntries(Object.entries(init.headers ?? {}).map(([key, value]) => [key.toLowerCase(), String(value)]));
    const request: Sent = { method, url, headers, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) };
    sent.push(request);
    if (init.signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
    const route = routes.find(([verb, path]) => verb === method && path.test(url));
    const recorded = route ? await answer(route[2], request) : method === 'GET' ? await titles(url) : null;
    if (!recorded) return new Response(JSON.stringify({ error: 'Route not found' }), { status: 404, headers: { 'content-type': 'application/json' } });
    const responseHeaders: Record<string, string> = { 'content-type': 'application/json' };
    if (recorded.etag) responseHeaders.etag = recorded.etag;
    return new Response(recorded.status === 204 ? null : JSON.stringify(recorded.body), { status: recorded.status, headers: responseHeaders });
  };
  return { fetch: fetch as typeof globalThis.fetch, sent };
}
