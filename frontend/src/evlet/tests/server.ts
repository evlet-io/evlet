// A fetch stub that replays real backend responses recorded in api-fixture.json
// (scripts/generate_ui_framework_fixtures.py), and records what the client sent.
import fixture from './api-fixture.json';
import { legacyRecordId } from '../ids';

export type Recorded = { status: number; body: any; etag: string | null };
export const responses = fixture as Record<string, Recorded>;
export interface Sent { method: string; url: string; headers: Record<string, string>; body: any }
type Reply = keyof typeof fixture | Recorded | ((sent: Sent) => Recorded | Promise<Recorded>);
type Route = [method: string, path: RegExp, reply: Reply];

/** The text id of an old integer id, as the recordings number rows per table: id(1) is '0000000000001'. */
export const id = (n: number): string => legacyRecordId(String(n))!;

/** Deep copy, so a test that changes a response does not change it for the next test. */
export const copy = <T>(value: T): T => structuredClone(value);

/**
 * The search route (GET /api/<model>/search) as the list route the recordings have: the same text and limit.
 * Null for any other URL. optionsOf turns the list's answer into the search route's: each record's id and title.
 */
export function listUrlOf(url: string): string | null {
  const parsed = new URL(url, 'http://127.0.0.1');
  const match = /^\/api\/([A-Za-z0-9_]+)\/search$/.exec(parsed.pathname);
  if (!match) return null;
  const query = new URLSearchParams({ limit: parsed.searchParams.get('limit') ?? '8', offset: '0' });
  const q = parsed.searchParams.get('q');
  if (q) query.set('q', q);
  return `${/^https?:/.test(url) ? parsed.origin : ''}/api/${match[1]}?${query}`;
}
export function optionsOf(list: Recorded): Recorded {
  if (list.status !== 200) return list;
  const items = (list.body.items as Array<Record<string, unknown>>).map(item => ({ id: item.id, title: String(item.title ?? item.name ?? `#${item.id}`) }));
  return { status: 200, body: { items }, etag: null };
}

export function server(routes: Route[]) {
  const sent: Sent[] = [];
  const answer = async (reply: Reply, request: Sent) => typeof reply === 'function' ? await reply(request) : typeof reply === 'string' ? responses[reply] : reply;
  // Link titles are read in batches (?filters={"id__in":[...]}); without a route of its own, a batch is
  // answered from the routes of the single records, as the server would list them.
  const titles = async (url: string): Promise<Recorded | null> => {
    const parsed = new URL(url, 'http://127.0.0.1');
    const ids = (JSON.parse(parsed.searchParams.get('filters') ?? '{}') as { id__in?: string[] }).id__in;
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
    let route = routes.find(([verb, path]) => verb === method && path.test(url));
    const listed = !route && method === 'GET' ? listUrlOf(url) : null;
    if (listed) route = routes.find(([verb, path]) => verb === 'GET' && path.test(listed));
    const recorded = route ? (listed ? optionsOf(await answer(route[2], { ...request, url: listed })) : await answer(route[2], request))
      : method === 'GET' ? await titles(url) : null;
    if (!recorded) return new Response(JSON.stringify({ error: 'Route not found' }), { status: 404, headers: { 'content-type': 'application/json' } });
    const responseHeaders: Record<string, string> = { 'content-type': 'application/json' };
    if (recorded.etag) responseHeaders.etag = recorded.etag;
    return new Response(recorded.status === 204 ? null : JSON.stringify(recorded.body), { status: recorded.status, headers: responseHeaders });
  };
  return { fetch: fetch as typeof globalThis.fetch, sent };
}

/**
 * The recorded order schema with display rules added, as the server would compile them: remark shows only while the
 * stage is open (visible_when), and the lines Table applies only while it is not lost (applies_when), so total and
 * line_count read it as empty then. Plan entries are placed before what reads them, as compile_plan orders them.
 */
export function withTableRules(schema: any) {
  const changed = copy(schema), order = changed.models.find((model: any) => model.name === 'order');
  const stage = (op: string, value: string) => ({ $expr: op, args: [{ $expr: 'ref', field: 'stage', scope: 'self' }, { $expr: 'literal', type: 'text', value }] });
  const entry = (field: string, kind: string, expression: unknown, table = false) => ({ expression, field, kind, path: [], reads: [['self', null, 'stage']],
    result_type: 'boolean', rounding: null, scale: null, stored: true, target: `${field}:${kind}`, target_type: table ? 'table' : 'text', ...(table ? { table: true } : {}) });
  order.fields.find((field: any) => field.name === 'remark').visible_when = stage('eq', 'open');
  order.calculation.plan.fields.unshift(entry('lines', 'applies_when', stage('ne', 'lost'), true), entry('remark', 'visible_when', stage('eq', 'open')));
  return changed;
}
