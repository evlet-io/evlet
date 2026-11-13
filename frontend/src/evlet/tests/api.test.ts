import { describe, expect, it, vi } from 'vitest';
import { ApiError, ConflictError, createClient, ValidationError } from '../api';
import { id, responses, server } from './server';

const order = responses.create.body;

describe('session', () => {
  it('reads an anonymous session and a signed-in one, keeping the CSRF token', async () => {
    let signedIn = false;
    const { fetch, sent } = server([['GET', /\/api\/auth\/session$/, () => responses[signedIn ? 'session' : 'session_anonymous']], ['POST', /\/api\/auth\/login$/, 'login']]);
    const api = createClient({ fetch });
    expect((await api.session()).user).toBeNull();
    expect(api.csrf).toBe('');
    const session = await api.login('admin', 'secret');
    signedIn = true;
    expect(session.user).toEqual({ id: 1, role: 'admin', username: 'admin' });
    expect(api.csrf).toBe('fixture-csrf-token');
    expect(sent[1]).toMatchObject({ method: 'POST', body: { username: 'admin', password: 'secret' } });
    expect(sent[1].headers['x-evlet-request']).toBe('1');
    expect((await api.session()).user?.username).toBe('admin');
  });

  it('reports a failed sign-in without ending the session', async () => {
    const ended = vi.fn();
    const { fetch } = server([['POST', /login$/, 'login_failed']]);
    const api = createClient({ fetch, onSessionEnded: ended });
    const error = await api.login('admin', 'wrong').catch(caught => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, kind: 'auth', message: 'Invalid username or password' });
    expect(ended).not.toHaveBeenCalled();
  });

  it('announces an ended session on 401', async () => {
    const ended = vi.fn(), event = vi.fn();
    window.addEventListener('evlet-session-ended', event);
    const { fetch } = server([['GET', /\/api\/schema$/, 'unauthenticated']]);
    const api = createClient({ fetch, onSessionEnded: ended });
    api.csrf = 'old';
    await expect(api.schema()).rejects.toMatchObject({ status: 401, message: 'Sign in to continue' });
    expect(ended).toHaveBeenCalledTimes(1);
    expect(event).toHaveBeenCalledTimes(1);
    expect(api.csrf).toBe('');
    window.removeEventListener('evlet-session-ended', event);
  });

  it('logs out with a 204 and forgets the token', async () => {
    const { fetch, sent } = server([['POST', /logout$/, 'logout']]);
    const api = createClient({ fetch });
    api.csrf = 'fixture-csrf-token';
    await api.logout();
    expect(sent[0].headers['x-csrf-token']).toBe('fixture-csrf-token');
    // The server refuses a write that is not JSON (415), so sign-out sends an empty object.
    expect(sent[0].headers['content-type']).toBe('application/json');
    expect(sent[0].body).toEqual({});
    expect(api.csrf).toBe('');
  });

  it('sends every write as JSON, with an empty object when it has no body', async () => {
    const { fetch, sent } = server([['POST', /sync$/, 'logout'], ['GET', /\/api\/order\?/, 'list']]);
    const api = createClient({ fetch });
    await api.request('POST', '/api/_settings/me/mailbox/sync');
    await api.request('GET', '/api/order', { query: { limit: 1 } });
    expect(sent[0].headers['content-type']).toBe('application/json');
    expect(sent[0].body).toEqual({});
    // Reads carry no body.
    expect(sent[1].headers['content-type']).toBeUndefined();
    expect(sent[1].body).toBeUndefined();
  });
});

describe('records', () => {
  it('reads the schema, a list page and a detail with its ETag', async () => {
    const { fetch, sent } = server([['GET', /\/api\/schema$/, 'schema'], ['GET', /\/api\/order\?/, 'list'], ['GET', /\/api\/order\/0000000000001$/, 'detail']]);
    const api = createClient({ fetch });
    const schema = await api.schema<{ models: Array<{ name: string }> }>();
    expect(schema.models.map(model => model.name)).toContain('order');
    const page = await api.list('order', { limit: 20, filters: { stage: 'open' }, q: 'Big', orderBy: 'title' });
    expect(page).toMatchObject({ total: 1, limit: 20, offset: 0 });
    const query = new URL(sent[1].url, 'http://x').searchParams;
    expect(Object.fromEntries(query)).toEqual({ limit: '20', offset: '0', filters: '{"stage":"open"}', q: 'Big', order_by: 'title' });
    const { data, etag } = await api.get('order', id(1));
    expect(etag).toBe(responses.detail.etag);
    expect(data.children_pages).toEqual({ lines: { limit: 100, partial: true, total: 120 } });
    expect(sent[2].headers['x-evlet-request']).toBeUndefined();
  });

  it('creates with child rows and receives the ids of new rows', async () => {
    const { fetch, sent } = server([['POST', /\/api\/order$/, 'create']]);
    const api = createClient({ fetch });
    api.csrf = 'token';
    const { data, etag, status } = await api.create('order', { title: 'Big order', children: { lines: [{ _key: 'l0', qty: '1' }] } });
    expect(status).toBe(201);
    expect(etag).toBe(responses.create.etag);
    expect(data._row_ids?.l0).toBe(id(1));
    expect(sent[0].headers).toMatchObject({ 'content-type': 'application/json', 'x-csrf-token': 'token', 'x-evlet-request': '1' });
  });

  it('updates with If-Match and a change set', async () => {
    const { fetch, sent } = server([['PATCH', /\/api\/order\/0000000000001$/, 'update']]);
    const api = createClient({ fetch });
    const changes = { title: 'Renamed', children: { lines: { create: [{ _key: 'n1', qty: '2', rate: '1' }], update: [{ id: id(2), qty: '3' }], delete: [id(3)] } } };
    const { data, etag } = await api.update('order', id(1), changes, responses.detail.etag);
    expect(sent[0].headers['if-match']).toBe(responses.detail.etag);
    expect(sent[0].body).toEqual(changes);
    expect(data._row_ids).toEqual({ n1: id(121) });
    expect(etag).toBe(responses.update.etag);
  });

  it('turns 428 and 409 into conflicts and 422 into field errors', async () => {
    const { fetch } = server([
      ['PATCH', /\/api\/order\/0000000000001$/, sent => responses[!sent.headers['if-match'] ? 'missing_if_match' : sent.headers['if-match'] === '"stale"' ? 'stale_if_match' : 'validation']],
    ]);
    const api = createClient({ fetch });
    const missing = await api.update('order', id(1), { title: 'x' }, null).catch(error => error);
    expect(missing).toBeInstanceOf(ConflictError);
    expect(missing).toMatchObject({ status: 428, kind: 'conflict', message: 'A current If-Match revision is required' });
    const stale = await api.update('order', id(1), { title: 'x' }, '"stale"').catch(error => error);
    expect(stale).toMatchObject({ status: 409, message: 'This record changed since you opened it. Reload it before saving.' });
    const invalid = await api.update('order', id(1), {}, responses.detail.etag).catch(error => error);
    expect(invalid).toBeInstanceOf(ValidationError);
    expect(invalid.fields).toEqual({ [`lines.${id(4)}.qty`]: 'Thirteen is not allowed' });
  });

  it('treats 412 as a conflict and reads an error code prefix', async () => {
    // The 409 message the server sends for a newer calculation plan (evlet/business.py check_calculation).
    const { fetch } = server([
      ['PATCH', /\/api\/order\/0000000000001$/, { status: 409, body: { error: 'SCHEMA_VERSION_MISMATCH: this document uses a newer calculation plan; reload it' }, etag: null }],
      ['PATCH', /\/api\/order\/0000000000002$/, { status: 412, body: { error: 'Precondition failed' }, etag: null }],
    ]);
    const api = createClient({ fetch });
    await expect(api.update('order', id(1), {}, 'x')).rejects.toMatchObject({ code: 'SCHEMA_VERSION_MISMATCH', kind: 'conflict' });
    await expect(api.update('order', id(2), {}, 'x')).rejects.toBeInstanceOf(ConflictError);
  });

  it('pages lines, including nested tables', async () => {
    const { fetch, sent } = server([['GET', /\/lines\/lines\?/, 'lines_page'], ['GET', /\/lines\/lines\/0000000000001\/parts/, 'nested_lines_page']]);
    const api = createClient({ fetch });
    const page = await api.lines('order', id(1), ['lines'], { limit: 100, offset: 100 });
    expect(page).toMatchObject({ total: 120, limit: 100, offset: 100 });
    expect(page.items[0].id).toBe(id(101));
    expect(sent[0].url).toBe(`/api/order/${id(1)}/lines/lines?limit=100&offset=100`);
    const parts = await api.lines('order', id(1), ['lines', id(1), 'parts'], { limit: 5 });
    expect(parts.items.map(item => item.id)).toEqual([id(1), id(2)]);
    expect(sent[1].url).toBe(`/api/order/${id(1)}/lines/lines/${id(1)}/parts?limit=5`);
    await expect(api.lines('order', id(1), ['lines', id(1)])).rejects.toThrow('nested rows');
  });

  it('posts drafts and actions with the right paths', async () => {
    const { fetch, sent } = server([['POST', /_draft\/order$/, 'draft'], ['POST', /actions\/close$/, 'action']]);
    const api = createClient({ fetch });
    const draft = await api.draft('order', { id: id(1), values: { stage: 'lost' }, revision: 3 });
    expect(draft.states).toEqual({ reason: 'required-input' });
    expect(draft.revision).toBe(3);
    const done = await api.action('order', id(1), 'close', { note: 'done' }, responses.update.etag);
    expect((done.data as any).stage).toBe('won');
    expect(sent[1].headers['if-match']).toBe(responses.update.etag);
  });

  it('moves a record through the move route and reads what is available next', async () => {
    const draft = responses.invoice_draft, issued = responses.invoice_issued;
    const id = draft.body.id;
    const { fetch, sent } = server([['GET', new RegExp(`/api/invoice/${id}$`), 'invoice_draft'], ['POST', /moves\/issue$/, 'invoice_issued']]);
    const api = createClient({ fetch });
    const before = await api.get('invoice', id);
    expect(before.etag).toBe(draft.etag);
    // Availability travels beside the values, outside the ETag.
    expect(before.data._moves).toEqual([{ key: 'issue', to: 'issued', label: 'Issue', allowed: true, primary: true }]);
    const moved = await api.move('invoice', id, 'issue', before.etag);
    expect(sent[1]).toMatchObject({ method: 'POST', url: `/api/invoice/${id}/moves/issue`, body: {} });
    expect(sent[1].headers['if-match']).toBe(draft.etag);
    expect(moved.etag).toBe(issued.etag);
    expect(moved.data).toMatchObject({ status: 'issued', number: issued.body.number, _locked: true });
    expect(moved.data._moves).toEqual([{ key: 'mark_paid', to: 'paid', label: 'Mark paid', allowed: false, reason: 'Your role cannot use this move' },
      { key: 'reopen', to: 'draft', label: 'Reopen', allowed: false, reason: 'Your role cannot use this move', remark: { mode: 'required', label: null, required: true } },
      { key: 'void', to: 'void', label: 'Void', allowed: true, remark: { mode: 'required', label: 'Reason', required: true } }]);
    expect(moved.data._effects).toEqual([{ target: 'ledgerentry', rows: 3, reversals: 0, balanced: true }]);
    await expect(api.move('invoice', id, '../x', null)).rejects.toThrow('Unknown move');
  });

  it('reports a move the record does not allow as a conflict with the reason', async () => {
    const { fetch } = server([['POST', /moves\/issue$/, 'invoice_move_refused']]);
    await expect(createClient({ fetch }).move('invoice', id(1), 'issue', '"x"')).rejects.toMatchObject({
      kind: 'conflict', status: 409, message: 'Issue is not available: the condition for this move is not met' });
  });

  it('pages owned rows of a document and keeps the detail extras of later phases', async () => {
    const { fetch, sent } = server([['GET', /\/lines\/lines\?/, 'invoice_lines_page'], ['GET', /\/api\/invoice\/[0-9a-z]{13}$/, 'invoice_void']]);
    const api = createClient({ fetch });
    const page = await api.lines('invoice', id(1), ['lines'], { limit: 1, offset: 1 });
    expect(sent[0].url).toBe(`/api/invoice/${id(1)}/lines/lines?limit=1&offset=1`);
    expect(page).toMatchObject({ total: 2, limit: 1, offset: 1 });
    expect(page.items.map(item => item.item)).toEqual(['Licence']);
    const { data } = await api.get('invoice', id(1));
    expect(data).toMatchObject({ status: 'void', _moves: [], _locked: true });
    expect(data._effects).toEqual([{ target: 'ledgerentry', rows: 3, reversals: 3, balanced: true }]);
  });

  it('reads the document schema: state, effects, measures, key fields and relations', () => {
    const invoice = responses.invoice_schema.body.models.find((model: any) => model.name === 'invoice');
    expect(invoice.state).toMatchObject({ field: 'status', default: 'draft', show: 'bar' });
    expect(invoice.state.steps.map((step: any) => [step.key, step.label, step.color, step.lock, step.final])).toEqual([
      ['draft', 'Draft', null, false, false], ['issued', 'Issued', 'green', true, false], ['paid', 'Paid', 'teal', true, true], ['void', 'Void', 'rose', true, true]]);
    expect(invoice.state.rules.map((rule: any) => [rule.key, rule.from, rule.to, rule.guarded, rule.remark?.mode ?? null])).toEqual([
      ['issue', ['draft'], 'issued', true, null], ['mark_paid', ['issued'], 'paid', false, null], ['reopen', ['issued'], 'draft', false, 'required'], ['void', ['issued'], 'void', false, 'required']]);
    expect(invoice).toMatchObject({ key_fields: ['customer', 'total', 'due_on'], permissions: { create: true, update: true, delete: false } });
    expect(invoice.relations.map((relation: any) => relation.name)).toEqual(['payment.invoice']);
    expect(invoice.measures.map((measure: any) => [measure.name, measure.per_record])).toEqual([['outstanding', false], ['count', false], ['payments', true], ['paid', true]]);
    expect(invoice.effects).toEqual([{ target: 'ledgerentry', on: 'issued', reverse: { swap: ['debit', 'credit'] }, balanced: ['debit', 'credit'] }]);
    expect(invoice.fields.find((field: any) => field.name === 'number').sequence).toMatchObject({ assign_on: 'issued', format: 'INV-{year}-{n:05}' });
  });

  it('checks ids and names before sending', async () => {
    const { fetch, sent } = server([]);
    const api = createClient({ fetch });
    // Ids are 13-character text; a number, a decimal string or a path segment is refused.
    await expect(api.get('order', 1 as unknown as string)).rejects.toThrow('This is not a record id.');
    await expect(api.get('order', '42')).rejects.toThrow('This is not a record id.');
    await expect(api.get('order', '../auth/0000')).rejects.toThrow('This is not a record id.');
    await expect(api.lines('order', id(1), ['lines', '1', 'parts'])).rejects.toThrow('nested rows');
    await expect(api.create('../auth', {})).rejects.toThrow('Unknown model');
    expect(sent).toEqual([]);
  });

  it('maps a missing record to not_found', async () => {
    const { fetch } = server([['GET', new RegExp(`${id(999999)}$`), 'not_found']]);
    await expect(createClient({ fetch }).get('order', id(999999))).rejects.toMatchObject({ kind: 'not_found', message: 'Record not found' });
  });
});

describe('failures and cancellation', () => {
  it('reports a network failure as retryable', async () => {
    const api = createClient({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
    const error = await api.schema().catch(caught => caught);
    expect(error).toMatchObject({ kind: 'network', retryable: true, message: 'Unable to reach the server. Check the connection and try again.' });
  });

  it('reports an unreadable body', async () => {
    const api = createClient({ fetch: async () => new Response('<html>', { status: 502 }) });
    await expect(api.schema()).rejects.toMatchObject({ status: 502, kind: 'server', retryable: true });
  });

  it('aborts requests of a page when the navigation signal aborts', async () => {
    let page = new AbortController();
    let release!: () => void;
    const fetch = vi.fn((_: RequestInfo | URL, init: RequestInit = {}) => new Promise<Response>((resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      release = () => resolve(new Response(JSON.stringify(responses.detail.body), { status: 200 }));
    }));
    const api = createClient({ fetch: fetch as any, signal: () => page.signal });
    const pending = api.get('order', id(1));
    page.abort();
    await expect(pending).rejects.toMatchObject({ kind: 'aborted', retryable: false });
    page = new AbortController();
    const next = api.get('order', id(1));
    release();
    expect((await next).data.id).toBe(order.id);
  });

  it('combines a request signal with the page signal', async () => {
    const own = new AbortController();
    const { fetch } = server([['GET', /order\/0000000000001$/, 'detail']]);
    const api = createClient({ fetch, signal: () => new AbortController().signal });
    own.abort();
    await expect(api.get('order', id(1), own.signal)).rejects.toMatchObject({ kind: 'aborted' });
  });
});
