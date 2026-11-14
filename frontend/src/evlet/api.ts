// Evlet API client: the browser side of the Python HTTP routes.
//
//   const api = createClient({ signal: () => router.signal });
//   await api.session();                 // learns the CSRF token
//   const { data, etag } = await api.get('invoice', '01hc1sd9vc1ye');
//   await api.update('invoice', '01hc1sd9vc1ye', { remarks: 'Paid by cheque' }, etag);
//
// Conventions follow evlet/auth.py and evlet/business.py: the session cookie is sent by the
// browser, every write carries X-Evlet-Request and X-CSRF-Token, and record writes send the
// ETag from the last read as If-Match. Errors come back as {error, fields?}. Record ids are
// 13-character text ids (ids.ts), never numbers.

import { isRecordId, recordId } from './ids';

export type FieldErrors = Record<string, string>;
export type ErrorKind = 'network' | 'aborted' | 'validation' | 'conflict' | 'auth' | 'forbidden' | 'not_found' | 'limited' | 'server' | 'response';

export class ApiError extends Error {
  /** A machine code when the server message starts with one, for example SCHEMA_VERSION_MISMATCH. */
  readonly code: string | null;
  constructor(message: string, readonly status: number, readonly kind: ErrorKind, readonly fields: FieldErrors = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = /^([A-Z][A-Z0-9_]+): /.exec(message)?.[1] ?? null;
  }
  /** Worth trying again later: the connection failed, the server was busy, or the response was cut short. */
  get retryable() { return this.kind === 'network' || this.status >= 500 || this.status === 408 || this.status === 429; }
}

/** 422: the server rejected values. `fields` maps field paths (lines.4.qty) to messages. */
export class ValidationError extends ApiError {}

/**
 * The record changed or the write had no current revision: 409 (changed since read, or a
 * newer calculation plan), 412 (precondition failed) or 428 (If-Match required). Reload first.
 * A move or action the record does not allow now is also a 409; its message says why.
 */
export class ConflictError extends ApiError {}

export interface ApiResponse<T> { data: T; etag: string | null; status: number }

export interface RequestOptions {
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  /** The ETag of the version being changed. */
  ifMatch?: string | null;
  /** Clear only this acknowledged kept draft after a record save; preserve leaves it untouched. */
  draftRevision?: string;
  signal?: AbortSignal;
  /** Read the body as a file (a Blob) instead of JSON, for downloads and PDFs. */
  file?: boolean;
}

export interface ClientOptions {
  /** Defaults to the global fetch. */
  fetch?: typeof fetch;
  /** Prefix for every path, for example a test server origin. */
  base?: string;
  /** Where the API is served (api in the page's config block); paths under /api are sent there. */
  api?: string;
  /** The current page's signal (router.signal): requests are aborted when the page changes. */
  signal?: () => AbortSignal | undefined;
  /** Called when a request is refused because the session ended (401). */
  onSessionEnded?: () => void;
}

// Shapes from the Python routes.
export interface User { id: number; username: string; role: string }
export interface Session { user: User | null; csrf?: string; setup?: boolean; tenant?: unknown; application?: { name?: string; playground?: boolean; docs?: string | null };
  /** Security(admin_sign_in=True) in config.py: the username the sign-in page starts with. */
  sign_in?: { username: string };
  /** Optional company identity provider on this origin; browser navigation starts the flow. */
  oidc?: { label: string; start: string };
  /** Signed in, but the account has no password yet (the Administrator after a development link). */
  password_needed?: boolean;
  /** First-run setup: the shortest password the new administrator may have. */
  password_min?: number }
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export type RecordData = Record<string, unknown> & {
  /** The 13-character text id (ids.ts). */
  id: string;
  children?: Record<string, RecordData[]>;
  children_pages?: Record<string, { total: number; limit: number; partial: boolean }>;
  /** After a write: the id given to each new row, by the _key it was sent with. */
  _row_ids?: Record<string, string>;
};
export interface DraftResult { values: Record<string, unknown>; errors: FieldErrors; states: Record<string, string>; revision: unknown }
/** filters: an object of "field__operator" keys, or a list of [field, operator, value] conditions and child groups (docs/ui.md, Filters). */
export interface ListQuery { limit?: number; offset?: number; filters?: Record<string, unknown> | unknown[]; orderBy?: string; q?: string }

const NAME = /^[A-Za-z][A-Za-z0-9_]*$/;
const SEGMENT = /^[A-Za-z0-9_-]{1,64}$/;

function modelPath(model: string) {
  if (!NAME.test(model)) throw new ApiError(`Unknown model ${model}`, 0, 'response');
  return `/api/${model}`;
}
function recordPath(model: string, id: string) {
  const text = recordId(id);
  if (!text) throw new ApiError('This is not a record id.', 0, 'response');
  return `${modelPath(model)}/${text}`;
}

// One signal that aborts when any of the given signals aborts, and a release that drops
// the listeners it added (the page signal lives longer than any request).
function anySignal(signals: Array<AbortSignal | undefined>): { signal: AbortSignal | undefined; release: () => void } {
  const list = signals.filter((signal): signal is AbortSignal => Boolean(signal));
  if (list.length < 2) return { signal: list[0], release: () => {} };
  if (typeof (AbortSignal as any).any === 'function') return { signal: (AbortSignal as any).any(list), release: () => {} };
  const controller = new AbortController();
  const listeners = list.map(signal => {
    const abort = () => controller.abort(signal.reason);
    if (signal.aborted) controller.abort(signal.reason); else signal.addEventListener('abort', abort, { once: true });
    return [signal, abort] as const;
  });
  return { signal: controller.signal, release: () => { for (const [signal, abort] of listeners) signal.removeEventListener('abort', abort); } };
}

// The error for a failed response. The class depends only on the status, so a 409 from a
// proxy with an HTML body is still a ConflictError.
function failure(status: number, data: any): ApiError {
  const message = typeof data?.error === 'string' ? data.error : status === 422 ? 'Some values are not valid.'
    : status === 409 || status === 412 || status === 428 ? 'This record changed since you opened it. Reload it before saving.' : 'The request failed.';
  const fields: FieldErrors = data?.fields && typeof data.fields === 'object' && !Array.isArray(data.fields) ? data.fields : {};
  const kind = kindOf(status);
  if (kind === 'validation') return new ValidationError(message, status, kind, fields);
  if (kind === 'conflict') return new ConflictError(message, status, kind, fields);
  return new ApiError(message, status, kind, fields);
}

function kindOf(status: number): ErrorKind {
  if (status === 401) return 'auth';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409 || status === 412 || status === 428) return 'conflict';
  if (status === 422) return 'validation';
  if (status === 429) return 'limited';
  return status >= 500 ? 'server' : 'response';
}

export class ApiClient {
  /** The CSRF token from the last session or login response. */
  csrf = '';
  private readonly fetcher: typeof fetch;

  constructor(private readonly options: ClientOptions = {}) {
    this.fetcher = options.fetch ?? ((...args) => fetch(...args));
  }

  /** The address of an API path, such as a file to link to. */
  url(path: string): string {
    const api = this.options.api;
    const routed = api && (path === '/api' || path.startsWith('/api/')) ? api + path.slice(4) : path;
    return (this.options.base ?? '') + routed;
  }

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (method !== 'GET') {
      headers['X-Evlet-Request'] = '1';
      if (this.csrf) headers['X-CSRF-Token'] = this.csrf;
    }
    // The server takes writes only as JSON (evlet/app.py answers 415 otherwise), so a write
    // without a body (sign-out, mailbox sync) sends an empty object.
    const body = options.body === undefined && ['POST', 'PATCH', 'PUT'].includes(method) ? {} : options.body;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.ifMatch) headers['If-Match'] = options.ifMatch;
    if (options.draftRevision) headers['X-Evlet-Draft-Revision'] = options.draftRevision;
    const query = Object.entries(options.query ?? {}).filter(([, value]) => value !== undefined && value !== '');
    const url = this.url(path) + (query.length ? '?' + new URLSearchParams(query.map(([key, value]) => [key, String(value)])) : '');
    const { signal, release } = anySignal([options.signal, this.options.signal?.()]);
    try {
      return await this.send<T>(method, path, url, headers, { ...options, body }, signal);
    } finally {
      release();
    }
  }

  private async send<T>(method: string, path: string, url: string, headers: Record<string, string>, options: RequestOptions, signal: AbortSignal | undefined): Promise<ApiResponse<T>> {
    let response: Response;
    try {
      response = await this.fetcher(url, { method, headers, credentials: 'same-origin', body: options.body === undefined ? undefined : JSON.stringify(options.body), signal });
    } catch (cause) {
      if (signal?.aborted || (cause as Error)?.name === 'AbortError') throw new ApiError('The request was cancelled.', 0, 'aborted');
      throw new ApiError('Unable to reach the server. Check the connection and try again.', 0, 'network');
    }
    if (response.status === 401 && !path.startsWith('/api/auth/login') && path !== '/_/oidc/finish') {
      this.csrf = '';
      this.options.onSessionEnded?.();
      if (typeof window !== 'undefined') window.dispatchEvent(new Event('evlet-session-ended'));
    }
    const etag = response.headers.get('etag');
    if (response.status === 204) return { data: null as T, etag, status: 204 };
    if (options.file && response.ok) return { data: await response.blob() as T, etag, status: response.status };
    let data: any;
    try {
      data = await response.json();
    } catch {
      if (!response.ok) throw failure(response.status, null);
      // A successful status line does not guarantee that the body arrived.
      throw new ApiError('The server returned an unreadable response.', response.status, 'network');
    }
    if (!response.ok) throw failure(response.status, data);
    return { data: data as T, etag, status: response.status };
  }

  // ---------- session ----------

  async session(): Promise<Session> {
    const { data } = await this.request<Session>('GET', '/api/auth/session');
    this.csrf = data.csrf ?? '';
    return data;
  }
  async login(username: string, password: string): Promise<Session> {
    const { data } = await this.request<Session>('POST', '/api/auth/login', { body: { username, password } });
    this.csrf = data.csrf ?? '';
    return data;
  }
  /** Consume the one-use, HttpOnly provider-return cookie. No token or ticket is read from the URL. */
  async finishOidc(): Promise<Session> {
    const { data } = await this.request<Session>('POST', '/_/oidc/finish', { body: {} });
    this.csrf = data.csrf ?? '';
    return data;
  }
  async logout(): Promise<void> {
    await this.request('POST', '/api/auth/logout');
    this.csrf = '';
  }

  // ---------- models ----------

  async schema<T = unknown>(): Promise<T> { return (await this.request<T>('GET', '/api/schema')).data; }

  async list<T = RecordData>(model: string, query: ListQuery = {}): Promise<Page<T>> {
    const { data } = await this.request<Page<T>>('GET', modelPath(model), { query: {
      limit: query.limit ?? 20, offset: query.offset ?? 0,
      filters: query.filters && Object.keys(query.filters).length ? JSON.stringify(query.filters) : undefined,
      order_by: query.orderBy && query.orderBy !== '-id' ? query.orderBy : undefined, q: query.q,
    } });
    return data;
  }

  async get(model: string, id: string, signal?: AbortSignal) { return this.request<RecordData>('GET', recordPath(model, id), { signal }); }

  /** Create a record. Child rows go in `children: {table: [rows]}`, each new row with a `_key`. */
  async create(model: string, values: Record<string, unknown>) { return this.request<RecordData>('POST', modelPath(model), { body: values }); }

  /**
   * Change a record. A Table may be sent as a change set
   * `children: {lines: {create: [...], update: [{id, ...}], delete: [ids], order: [ids and _keys]}}`.
   */
  async update(model: string, id: string, changes: Record<string, unknown>, etag: string | null, draftRevision?: string) {
    return this.request<RecordData>('PATCH', recordPath(model, id), { body: changes, ifMatch: etag, draftRevision });
  }

  async remove(model: string, id: string, etag: string | null) { return this.request<null>('DELETE', recordPath(model, id), { ifMatch: etag }); }

  /**
   * One page of an owned table: path is the table name, or a route to a nested table
   * through row ids, for example ['lines', '01hc1sd9vc1ye', 'parts'].
   */
  async lines<T = RecordData>(model: string, id: string, path: string[], page: { limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<Page<T>> {
    if (path.length % 2 !== 1 || path.some((part, index) => index % 2 ? !isRecordId(part) : !NAME.test(part)))
      throw new ApiError('Use a table name, or table, row id, table... for nested rows.', 0, 'response');
    const { data } = await this.request<Page<T>>('GET', `${recordPath(model, id)}/lines/${path.join('/')}`, { query: { limit: page.limit, offset: page.offset }, signal });
    return data;
  }

  /** Evaluate unsaved values on the server (calculations the browser cannot run locally). */
  async draft(model: string, body: { id?: string | null; values: Record<string, unknown>; revision?: unknown; calculation?: unknown }): Promise<DraftResult> {
    modelPath(model);
    return (await this.request<DraftResult>('POST', `/api/_draft/${model}`, { body })).data;
  }

  /**
   * A step of a slip (POST /api/_slips/{name}/{step}): present and check answer its surface, hand-in its
   * outcome. A slip presented from a record hands in with that record's revision as If-Match.
   */
  async slip<T>(name: string, step: 'present' | 'check' | 'hand-in', body: Record<string, unknown>, ifMatch: string | null = null): Promise<T> {
    if (!NAME.test(name)) throw new ApiError(`Unknown slip ${name}`, 0, 'response');
    return (await this.request<T>('POST', `/api/_slips/${name}/${step}`, { body, ifMatch })).data;
  }

  /** Run a model action. A long action answers 202 with {job, record}. */
  async action(model: string, id: string, name: string, args: Record<string, unknown> = {}, etag: string | null = null, draftRevision?: string) {
    if (!NAME.test(name)) throw new ApiError(`Unknown action ${name}`, 0, 'response');
    return this.request<RecordData | { job: unknown; record: RecordData }>('POST', `${recordPath(model, id)}/actions/${name}`, { body: args, ifMatch: etag, draftRevision });
  }

  /**
   * Move a record by a State rule (POST /api/{model}/{id}/moves/{key}). `values` hold the remark and the fields
   * the move asks for (`remark` and `asks` in `_moves`); the server answers with the record and its new `_moves`,
   * 422 with field errors, or 409 when the move is not available.
   */
  async move(model: string, id: string, key: string, etag: string | null, values: Record<string, unknown> = {}, draftRevision?: string) {
    if (!SEGMENT.test(key)) throw new ApiError(`Unknown move ${key}`, 0, 'response');
    return this.request<RecordData>('POST', `${recordPath(model, id)}/moves/${key}`, { body: values, ifMatch: etag, draftRevision });
  }
}

export function createClient(options: ClientOptions = {}) { return new ApiClient(options); }
