// The API client the screens use: the framework client plus the routes the screens need and a
// cell per record holding its last detail, so the availability keys of a detail (_moves,
// _actions, _locked, _void, _revise, _unpost, _repost, _effects, revised_by) are at hand next to the record store.

import { ApiClient, type ApiResponse, type ClientOptions, type Page, type RecordData, type RequestOptions, type Session } from '../api';
import { cell, type Cell } from '../reactive';

const DETAIL = /^\/api\/([A-Za-z][A-Za-z0-9_]*)\/(\d+)(?:\/(?:moves\/[^/]+|actions\/[^/]+|void|revise|unpost|repost|confirm|cancel|reopen))?$/;
const NAME = /^[A-Za-z][A-Za-z0-9_.]*$/;

export interface Attachment { id: number; name: string; size: number }

export class AppClient extends ApiClient {
  private readonly details = new Map<string, Cell<RecordData | null>>();
  private readonly inflight = new Map<string, Promise<ApiResponse<RecordData>>>();

  constructor(options: ClientOptions = {}) { super(options); }

  /** The last detail the server sent for a record (from a read, save, move, action, void or revise). */
  detail(model: string, id: string): Cell<RecordData | null> {
    const key = `${model}:${id}`;
    let found = this.details.get(key);
    if (!found) this.details.set(key, found = cell<RecordData | null>(null, { name: `detail ${key}` }));
    return found;
  }

  /** A read of a record that is in flight now (GET /api/{model}/{id}), to share instead of asking again. */
  reading(model: string, id: string): Promise<ApiResponse<RecordData>> | undefined {
    return this.inflight.get(`/api/${model}/${id}`);
  }

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    if (method !== 'GET' || !/^\/api\/[A-Za-z][A-Za-z0-9_]*\/\d+$/.test(path) || options.query) return this.withDetail<T>(method, path, options);
    const pending = this.withDetail<T>(method, path, options);
    this.inflight.set(path, pending as Promise<ApiResponse<any>>);
    const done = () => { if (this.inflight.get(path) === pending) this.inflight.delete(path); };
    pending.then(done, done);
    return pending;
  }

  private async withDetail<T>(method: string, path: string, options: RequestOptions): Promise<ApiResponse<T>> {
    const response = await super.request<T>(method, path, options);
    const found = DETAIL.exec(path);
    const data = response.data as any;
    if (found && data && typeof data === 'object') {
      const record = 'job' in data && 'record' in data ? data.record : data;
      if (record && typeof record.id === 'string') this.detail(found[1], record.id).set(record);
    }
    return response;
  }

  forget() { this.details.clear(); }

  /** Finish a two-step sign-in with the ticket from login and a code. */
  async secondStep(ticket: string, code: string): Promise<Session & { backup_codes?: string[] }> {
    const { data } = await this.request<Session & { backup_codes?: string[] }>('POST', '/api/auth/login', { body: { ticket, code } });
    this.csrf = data.csrf ?? '';
    return data;
  }

  /** First-run setup: the first administrator account. */
  async setup(username: string, password: string) {
    await this.request('POST', '/api/auth/setup', { body: { username, password } });
  }

  /** Declared model-level measures (Phase 9), following the list's filters and search text. */
  async measures(model: string, filters?: Record<string, unknown> | unknown[], q?: string) {
    return (await this.request<{ measures: Record<string, unknown> }>('GET', `/api/${model}/measures`,
      { query: { filters: filters && Object.keys(filters).length ? JSON.stringify(filters) : undefined, q: q || undefined } })).data.measures;
  }

  /** Declared per-record measures (Phase 9). */
  async recordMeasures(model: string, id: string) {
    return (await this.request<{ measures: Record<string, unknown> }>('GET', `/api/${model}/${id}/measures`)).data.measures;
  }

  /** An ad hoc aggregate (Phase 9): measures as `name:function:field`, grouped by fields. */
  async aggregate(model: string, measures: string[], groupBy: string[] = [], filters?: Record<string, unknown> | unknown[], q?: string) {
    return (await this.request<{ group_by: string[]; measures: string[]; items: Array<Record<string, unknown>> }>('GET', `/api/${model}/aggregate`, { query: {
      measures: measures.join(','), group_by: groupBy.length ? groupBy.join(',') : undefined,
      filters: filters && Object.keys(filters).length ? JSON.stringify(filters) : undefined, q: q || undefined,
    } })).data;
  }

  /** One page of a relation (Phase 10): records of another model that link to this one. */
  async related(model: string, id: string, relation: string, page: { limit?: number; offset?: number } = {}) {
    if (!NAME.test(relation)) throw new Error(`Unknown relation ${relation}`);
    return (await this.request<Page<RecordData>>('GET', `/api/${model}/${id}/related/${relation}`, { query: page })).data;
  }

  /** One page of the record's journal, newest first (docs/journal.md), with the names of its actors and assignees. */
  async journal(model: string, id: string, page: { limit?: number; offset?: number } = {}) {
    return (await this.request<JournalPage>('GET', `/api/${model}/${id}/journal`, { query: page })).data;
  }

  /** Add a note to the record's journal; answers the entry as the caller sees it. */
  async addNote(model: string, id: string, text: string) {
    return (await this.request<JournalEntry>('POST', `/api/${model}/${id}/journal`, { body: { kind: 'note', text } })).data;
  }

  async lifecycle(model: string, id: string, operation: 'confirm' | 'cancel' | 'reopen', etag: string | null, reason?: string) {
    return this.request<RecordData>('POST', `/api/${model}/${id}/${operation}`, {
      body: operation === 'cancel' ? { reason } : {}, ifMatch: etag,
    });
  }

  async revisions(model: string, id: string, page: { limit?: number; offset?: number } = {}) {
    return (await this.request<{ items: Array<{ revision: number; execution_id: string; at: string; actor: string; hash: string }>; total: number }>(
      'GET', `/api/${model}/${id}/revisions`, { query: page })).data;
  }

  async snapshot(model: string, id: string, revision: number) {
    return (await this.request<{ revision: number; at: string; document: Record<string, unknown> }>(
      'GET', `/api/${model}/${id}/snapshot`, { query: { revision } })).data;
  }

  async revisionDiff(model: string, id: string, from: number, to: number) {
    return (await this.request<{ from: number; to: number; changes: Record<string, any> }>(
      'GET', `/api/${model}/${id}/diff`, { query: { from, to } })).data;
  }

  async attachments(model: string, id: string) {
    return (await this.request<{ items: Attachment[] }>('GET', `/api/${model}/${id}/attachments`)).data.items;
  }

  async upload(model: string, id: string, name: string, content: string) {
    return (await this.request<Attachment>('POST', `/api/${model}/${id}/attachments`, { body: { name, content } })).data;
  }

  /** Check a report's filters without running it (POST /api/_reports/{name}/evaluate). */
  async evaluateReport(name: string, filters: Record<string, unknown>, revision: number) {
    return (await this.request<ReportCheck>('POST', `/api/_reports/${reportName(name)}/evaluate`, { body: { filters, revision } })).data;
  }

  /** One page of a report, run with these filters and sort. */
  async runReport(name: string, body: { filters: Record<string, unknown>; limit?: number; offset?: number; sort?: string | null; revision?: number }, signal?: AbortSignal) {
    return (await this.request<ReportPage>('POST', `/api/_reports/${reportName(name)}`, { body, signal })).data;
  }

  /** A report as a file: its CSV export, or its PDF in a print design. */
  async reportFile(name: string, kind: 'export.csv' | { print: string }, body: { filters: Record<string, unknown>; sort?: string | null }) {
    const path = `/api/_reports/${reportName(name)}/${kind === 'export.csv' ? kind : `prints/${encodeURIComponent(kind.print)}`}`;
    // Print designs take the filters only; the export takes the sort too.
    return (await this.request<Blob>('POST', path, { body: kind === 'export.csv' ? body : { filters: body.filters }, file: true })).data;
  }

  async removeAttachment(model: string, id: string, file: number) {
    await this.request('DELETE', `/api/${model}/${id}/attachments/${file}`);
  }

  /** Void a record (Phase 5) with a reason. */
  async voidRecord(model: string, id: string, reason: string, etag: string | null) {
    return this.request<RecordData>('POST', `/api/${model}/${id}/void`, { body: { reason }, ifMatch: etag });
  }

  /** Void and copy a record into a new draft (Phase 5). Answers the copy, with _original. */
  async revise(model: string, id: string, reason: string, etag: string | null) {
    return this.request<RecordData>('POST', `/api/${model}/${id}/revise`, { body: { reason }, ifMatch: etag });
  }

  /** Take a posted record back to the state it posted from, cancelling what it posted. Admin by default. */
  async unpost(model: string, id: string, reason: string, etag: string | null) {
    return this.request<RecordData>('POST', `/api/${model}/${id}/unpost`, { body: { reason }, ifMatch: etag });
  }

  /** Post an unposted record again through its posting move; the reason is optional. */
  async repost(model: string, id: string, reason: string, etag: string | null) {
    return this.request<RecordData>('POST', `/api/${model}/${id}/repost`, { body: reason ? { reason } : {}, ifMatch: etag });
  }
}

const reportName = (name: string) => { if (!NAME.test(name)) throw new Error(`Unknown report ${name}`); return name; };

/** What a report's filter check answers: the settled values, errors, field states and conditions. */
export interface ReportCheck { values: Record<string, unknown>; errors: Record<string, string>; states: Record<string, string>;
  rules: Record<string, { visible?: boolean; required?: boolean; read_only?: boolean }>; revision?: unknown }
/** One page of a report: cleaned rows, the row count, totals (all rows, or this page) and the filters it ran with. */
export interface ReportPage { rows: Array<Record<string, unknown>>; total: number; limit: number; offset: number; sort: string | null;
  totals?: { scope: 'all' | 'page'; values: Record<string, string | null> }; filters: Record<string, unknown>; revision?: unknown;
  /** With Report.group_by: the groups on this page with their row count and totals (of every row, or of the page). */
  groups?: { scope: 'all' | 'page'; items: Array<{ value: unknown; count: number; totals: Record<string, string | null> }> } }

/** A journal entry (docs/journal.md). `actor` is a user id, or null for the engine; `partial` marks an entry
 * the caller sees only in part; `done` is set on follow ups (the completing entry's id, or null). */
/** `via` names the AI assistant a person's write came through; `actor` stays the person. */
export interface JournalEntry { id: number; kind: string; actor: number | null; at: string; data: Record<string, any>; partial?: true; done?: number | null; via?: string }
/** A page of a journal: `users` names the actors and assignees on it by user id. */
export interface JournalPage extends Page<JournalEntry> { users: Record<string, string> }
