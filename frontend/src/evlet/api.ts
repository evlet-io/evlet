// Evlet API client: the browser side of the Python HTTP routes.
//
//   const api = createClient({ signal: () => router.signal });
//   await api.session();                 // learns the CSRF token
//   const { data, etag } = await api.get('invoice', 42);
//   await api.update('invoice', 42, { remarks: 'Paid by cheque' }, etag);
//
// Conventions follow evlet/auth.py and evlet/business.py: the session cookie is sent by the
// browser, every write carries X-Evlet-Request and X-CSRF-Token, and record writes send the
// ETag from the last read as If-Match. Errors come back as {error, fields?}.

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
 */
export class ConflictError extends ApiError {}

export interface ApiResponse<T> { data: T; etag: string | null; status: number }

export interface RequestOptions {
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  /** The ETag of the version being changed. */
  ifMatch?: string | null;
  signal?: AbortSignal;
}

export interface ClientOptions {
  /** Defaults to the global fetch. */
  fetch?: typeof fetch;
  /** Prefix for every path, for example a test server origin. */
  base?: string;
  /** The current page's signal (router.signal): requests are aborted when the page changes. */
  signal?: () => AbortSignal | undefined;
  /** Called when a request is refused because the session ended (401). */
  onSessionEnded?: () => void;
}

// Shapes from the Python routes.
export interface User { id: number; username: string; role: string }
export interface Session { user: User | null; csrf?: string; setup?: boolean; tenant?: unknown; application?: { name?: string; playground?: boolean } }
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export type RecordData = Record<string, unknown> & {
  id: number;
  children?: Record<string, RecordData[]>;
  children_pages?: Record<string, { total: number; limit: number; partial: boolean }>;
  /** After a write: the id given to each new row, by the _key it was sent with. */
  _row_ids?: Record<string, number>;
};
export interface DraftResult { values: Record<string, unknown>; errors: FieldErrors; states: Record<string, string>; revision: unknown }
export interface ListQuery { limit?: number; offset?: number; filters?: Record<string, unknown>; orderBy?: string; q?: string }

const NAME = /^[A-Za-z][A-Za-z0-9_]*$/;
const SEGMENT = /^[A-Za-z0-9_-]{1,64}$/;

function modelPath(model: string) {
  if (!NAME.test(model)) throw new ApiError(`Unknown model ${model}`, 0, 'response');
  return `/api/${model}`;
}
function recordPath(model: string, id: number) {
  if (!Number.isSafeInteger(id) || id < 1) throw new ApiError('This record ID is outside the supported browser range.', 0, 'response');
  return `${modelPath(model)}/${id}`;
}

// One signal that aborts when any of the given signals aborts.
function anySignal(signals: Array<AbortSignal | undefined>): AbortSignal | undefined {
  const list = signals.filter((signal): signal is AbortSignal => Boolean(signal));
  if (list.length < 2) return list[0];
  const controller = new AbortController();
  for (const signal of list) {
    if (signal.aborted) { controller.abort(signal.reason); break; }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true });
  }
  return controller.signal;
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

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (method !== 'GET') {
      headers['X-Evlet-Request'] = '1';
      if (this.csrf) headers['X-CSRF-Token'] = this.csrf;
    }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.ifMatch) headers['If-Match'] = options.ifMatch;
    const query = Object.entries(options.query ?? {}).filter(([, value]) => value !== undefined && value !== '');
    const url = (this.options.base ?? '') + path + (query.length ? '?' + new URLSearchParams(query.map(([key, value]) => [key, String(value)])) : '');
    const signal = anySignal([options.signal, this.options.signal?.()]);
    let response: Response;
    try {
      response = await this.fetcher(url, { method, headers, credentials: 'same-origin', body: options.body === undefined ? undefined : JSON.stringify(options.body), signal });
    } catch (cause) {
      if (signal?.aborted || (cause as Error)?.name === 'AbortError') throw new ApiError('The request was cancelled.', 0, 'aborted');
      throw new ApiError('Unable to reach the server. Check the connection and try again.', 0, 'network');
    }
    if (response.status === 401 && !path.startsWith('/api/auth/login')) {
      this.csrf = '';
      this.options.onSessionEnded?.();
      if (typeof window !== 'undefined') window.dispatchEvent(new Event('evlet-session-ended'));
    }
    const etag = response.headers.get('etag');
    if (response.status === 204) return { data: null as T, etag, status: 204 };
    let data: any;
    try {
      data = await response.json();
    } catch {
      // A successful status line does not guarantee that the body arrived.
      throw new ApiError('The server returned an unreadable response.', response.status, response.ok ? 'network' : kindOf(response.status));
    }
    if (!response.ok) {
      const message = typeof data?.error === 'string' ? data.error : 'The request failed.';
      const fields: FieldErrors = data?.fields && typeof data.fields === 'object' ? data.fields : {};
      const kind = kindOf(response.status);
      if (kind === 'validation') throw new ValidationError(message, response.status, kind, fields);
      if (kind === 'conflict') throw new ConflictError(message, response.status, kind, fields);
      throw new ApiError(message, response.status, kind, fields);
    }
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

  async get(model: string, id: number, signal?: AbortSignal) { return this.request<RecordData>('GET', recordPath(model, id), { signal }); }

  /** Create a record. Child rows go in `children: {table: [rows]}`, each new row with a `_key`. */
  async create(model: string, values: Record<string, unknown>) { return this.request<RecordData>('POST', modelPath(model), { body: values }); }

  /**
   * Change a record. A Table may be sent as a change set
   * `children: {lines: {create: [...], update: [{id, ...}], delete: [ids], order: [ids and _keys]}}`.
   */
  async update(model: string, id: number, changes: Record<string, unknown>, etag: string | null) {
    return this.request<RecordData>('PATCH', recordPath(model, id), { body: changes, ifMatch: etag });
  }

  async remove(model: string, id: number, etag: string | null) { return this.request<null>('DELETE', recordPath(model, id), { ifMatch: etag }); }

  /**
   * One page of an owned table: path is the table name, or a route to a nested table
   * through row ids, for example ['lines', 17, 'parts'].
   */
  async lines<T = RecordData>(model: string, id: number, path: Array<string | number>, page: { limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<Page<T>> {
    if (path.length % 2 !== 1 || path.some((part, index) => index % 2 ? !Number.isSafeInteger(part) || (part as number) < 1 : !NAME.test(String(part))))
      throw new ApiError('Use a table name, or table, row id, table... for nested rows.', 0, 'response');
    const { data } = await this.request<Page<T>>('GET', `${recordPath(model, id)}/lines/${path.join('/')}`, { query: { limit: page.limit, offset: page.offset }, signal });
    return data;
  }

  /** Evaluate unsaved values on the server (calculations the browser cannot run locally). */
  async draft(model: string, body: { id?: number | null; values: Record<string, unknown>; revision?: unknown; calculation?: unknown }): Promise<DraftResult> {
    modelPath(model);
    return (await this.request<DraftResult>('POST', `/api/_draft/${model}`, { body })).data;
  }

  /** Run a model action. A long action answers 202 with {job, record}. */
  async action(model: string, id: number, name: string, args: Record<string, unknown> = {}, etag: string | null = null) {
    if (!NAME.test(name)) throw new ApiError(`Unknown action ${name}`, 0, 'response');
    return this.request<RecordData | { job: unknown; record: RecordData }>('POST', `${recordPath(model, id)}/actions/${name}`, { body: args, ifMatch: etag });
  }

  /** Move a record to another state (POST /api/{model}/{id}/moves/{to}). */
  async move(model: string, id: number, to: string, etag: string | null, body: Record<string, unknown> = {}) {
    if (!SEGMENT.test(to)) throw new ApiError(`Unknown state ${to}`, 0, 'response');
    return this.request<RecordData>('POST', `${recordPath(model, id)}/moves/${to}`, { body, ifMatch: etag });
  }
}

export function createClient(options: ClientOptions = {}) { return new ApiClient(options); }
