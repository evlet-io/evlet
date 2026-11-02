// The API client the screens use: the framework client plus the routes the screens need and a
// cell per record holding its last detail, so the availability keys of a detail (_moves,
// _actions, _locked, _void, _revise, _effects, revised_by) are at hand next to the record store.

import { ApiClient, type ApiResponse, type ClientOptions, type Page, type RecordData, type RequestOptions, type Session } from '../api';
import { cell, type Cell } from '../reactive';

const DETAIL = /^\/api\/([A-Za-z][A-Za-z0-9_]*)\/(\d+)(?:\/(?:moves\/[^/]+|actions\/[^/]+|void|revise))?$/;
const NAME = /^[A-Za-z][A-Za-z0-9_.]*$/;

export interface Attachment { id: number; name: string; size: number }

export class AppClient extends ApiClient {
  private readonly details = new Map<string, Cell<RecordData | null>>();

  constructor(options: ClientOptions = {}) { super(options); }

  /** The last detail the server sent for a record (from a read, save, move, action, void or revise). */
  detail(model: string, id: number): Cell<RecordData | null> {
    const key = `${model}:${id}`;
    let found = this.details.get(key);
    if (!found) this.details.set(key, found = cell<RecordData | null>(null, { name: `detail ${key}` }));
    return found;
  }

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    const response = await super.request<T>(method, path, options);
    const found = DETAIL.exec(path);
    const data = response.data as any;
    if (found && data && typeof data === 'object') {
      const record = 'job' in data && 'record' in data ? data.record : data;
      if (record && typeof record.id === 'number') this.detail(found[1], record.id).set(record);
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

  /** Declared model-level measures (Phase 9), following the list's filters. */
  async measures(model: string, filters?: Record<string, unknown>) {
    return (await this.request<{ measures: Record<string, unknown> }>('GET', `/api/${model}/measures`,
      { query: { filters: filters && Object.keys(filters).length ? JSON.stringify(filters) : undefined } })).data.measures;
  }

  /** Declared per-record measures (Phase 9). */
  async recordMeasures(model: string, id: number) {
    return (await this.request<{ measures: Record<string, unknown> }>('GET', `/api/${model}/${id}/measures`)).data.measures;
  }

  /** An ad hoc aggregate (Phase 9): measures as `name:function:field`, grouped by fields. */
  async aggregate(model: string, measures: string[], groupBy: string[] = [], filters?: Record<string, unknown>) {
    return (await this.request<{ group_by: string[]; measures: string[]; items: Array<Record<string, unknown>> }>('GET', `/api/${model}/aggregate`, { query: {
      measures: measures.join(','), group_by: groupBy.length ? groupBy.join(',') : undefined,
      filters: filters && Object.keys(filters).length ? JSON.stringify(filters) : undefined,
    } })).data;
  }

  /** One page of a relation (Phase 10): records of another model that link to this one. */
  async related(model: string, id: number, relation: string, page: { limit?: number; offset?: number } = {}) {
    if (!NAME.test(relation)) throw new Error(`Unknown relation ${relation}`);
    return (await this.request<Page<RecordData>>('GET', `/api/${model}/${id}/related/${relation}`, { query: page })).data;
  }

  /** One page of the record's journal, newest first (Phase 8, provisional shape). */
  async journal(model: string, id: number, page: { limit?: number; offset?: number } = {}) {
    return (await this.request<Page<JournalEntry>>('GET', `/api/${model}/${id}/journal`, { query: page })).data;
  }

  async addNote(model: string, id: number, text: string) {
    return (await this.request<JournalEntry>('POST', `/api/${model}/${id}/journal`, { body: { kind: 'note', text } })).data;
  }

  async attachments(model: string, id: number) {
    return (await this.request<{ items: Attachment[] }>('GET', `/api/${model}/${id}/attachments`)).data.items;
  }

  async upload(model: string, id: number, name: string, content: string) {
    return (await this.request<Attachment>('POST', `/api/${model}/${id}/attachments`, { body: { name, content } })).data;
  }

  async removeAttachment(model: string, id: number, file: number) {
    await this.request('DELETE', `/api/${model}/${id}/attachments/${file}`);
  }

  /** Void a record (Phase 5) with a reason. */
  async voidRecord(model: string, id: number, reason: string, etag: string | null) {
    return this.request<RecordData>('POST', `/api/${model}/${id}/void`, { body: { reason }, ifMatch: etag });
  }

  /** Void and copy a record into a new draft (Phase 5). Answers the copy, with _original. */
  async revise(model: string, id: number, reason: string, etag: string | null) {
    return this.request<RecordData>('POST', `/api/${model}/${id}/revise`, { body: { reason }, ifMatch: etag });
  }
}

/** A journal entry (Phase 8, provisional: from docs/handoff-model.md). */
export interface JournalEntry { id: number; kind: string; actor: string | null; at: string; data: Record<string, any> }
