// Replays testCRM scenarios recorded from the real engine (scripts/generate_testcrm_fixtures.py).
// A scenario is a list of phases: the reads the screens make, one write, then the reads after it.
// A read is answered from the latest phase reached that recorded it; a write moves to the next phase.
// Requests the recording does not have are kept in `misses` and answered 404, so a test can show them.
import fixture from './testcrm-fixture.json';
import { start, type Route } from './screens-helpers';
import type { Recorded, Sent } from './server';

type Phase = Record<string, Recorded | { schema: string }>;
const data = fixture as unknown as Record<string, Phase[]> & { schemas: Record<string, Recorded>; ids: Record<string, number> };
export const ids = data.ids;

/** The id of a record a scenario read, by its title (or a lead's name). */
export function idOf(name: string, model: string, title: string): number {
  for (const phase of data[name]) for (const [key, value] of Object.entries(phase)) {
    const body = (value as Recorded).body;
    if (new RegExp(`^GET /api/${model}/\\d+$`).test(key) && (body?.title ?? body?.name) === title) return body.id;
  }
  throw new Error(`${name} did not read ${model} ${title}`);
}

/** The same key as the generator: method, path and the decoded query parameters in order. */
export function keyOf(method: string, url: string): string {
  const parsed = new URL(url, 'http://testcrm.local');
  const query = [...parsed.searchParams.entries()].sort(([a, x], [b, y]) => a < b ? -1 : a > b ? 1 : x < y ? -1 : x > y ? 1 : 0)
    .map(([name, value]) => `${name}=${value}`).join('&');
  return `${method} ${parsed.pathname}${query ? `?${query}` : ''}`;
}

const notRecorded = (key: string): Recorded => ({ status: 404, body: { error: `Not recorded: ${key}` }, etag: null });

/** A scenario; `signedIn` starts after its sign-in (the scenarios that record the login screen). */
export function scenario(name: string, { signedIn = true } = {}) {
  const phases = data[name];
  if (!phases) throw new Error(`No testCRM scenario ${name}`);
  const login = phases.findIndex(entry => (entry['POST /api/auth/login'] as Recorded | undefined)?.status === 200);
  let phase = signedIn && login >= 0 ? login + 1 : 0;
  const misses: string[] = [];
  const writes: Sent[] = [];
  const answer = (recorded: Recorded | { schema: string }): Recorded => structuredClone('schema' in recorded ? data.schemas[recorded.schema] : recorded);
  // Link titles are read in batches (?filters={"id__in":[...]}); a batch is answered from the records
  // the recording read one by one, as the server would list them.
  const batch = (url: string): Recorded | null => {
    const parsed = new URL(url, 'http://testcrm.local');
    const ids = (JSON.parse(parsed.searchParams.get('filters') ?? '{}') as { id__in?: number[] }).id__in;
    if (!ids || [...parsed.searchParams.keys()].some(name => !['filters', 'limit', 'offset'].includes(name))) return null;
    const items: unknown[] = [];
    for (const id of ids) {
      const key = `GET ${parsed.pathname}/${id}`;
      let found: Recorded | undefined;
      for (let at = phase; at >= 0 && !found; at--) found = phases[at][key] as Recorded | undefined;
      if (!found) return null;
      if (found.status === 200) items.push(structuredClone(found.body));
    }
    return { status: 200, body: { items, total: items.length, limit: ids.length, offset: 0 }, etag: null };
  };
  const reply = (sent: Sent): Recorded => {
    const key = keyOf(sent.method, sent.url);
    if (sent.method === 'GET') {
      for (let at = phase; at >= 0; at--) if (phases[at][key]) return answer(phases[at][key]);
      const listed = batch(sent.url);
      if (listed) return listed;
      misses.push(key);
      return notRecorded(key);
    }
    writes.push(sent);
    for (let at = phase; at < phases.length; at++) {
      if (phases[at][key]) { phase = at + 1; return answer(phases[at][key]); }
    }
    misses.push(key);
    return notRecorded(key);
  };
  const routes: Route[] = ['GET', 'POST', 'PATCH', 'DELETE'].map(method => [method, /./, reply]);
  return {
    misses, writes,
    /** The phase reached: 0 before the first write. */
    phase: () => phase,
    /** Moves past writes the screen will not send itself (made in another session or an earlier test). */
    skip: (count: number) => { phase = Math.min(phases.length - 1, phase + count); },
    start: (path: string) => start(routes, path),
  };
}
