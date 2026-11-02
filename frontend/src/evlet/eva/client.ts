// Eva's routes (/api/_eva) and approvals (/api/_tools/approvals) for the desk app. Every route answers
// 404 when Eva is off or not allowed for this person, so `status()` answering null hides Eva entirely.
//
//   const status = await evaStatus(ctx.api);   // null: no Eva for this person

import { ApiError, type ApiClient } from '../api';

export interface Allowance { limit: string | number | null; unit: 'aed' | 'tokens' | 'requests'; period: string; used: string | number; resets: number; resets_text?: string; exhausted: boolean }
export interface EvaStatus {
  models: string[]; model: string; choose_model: boolean; allowance: Allowance; tasks: boolean; pending_approvals: number;
  files: { max_size: number; max_files: number; kinds: string[] };
}
export interface Conversation { id: number; title: string; created_at: number; updated_at: number; archived: boolean; task_id: number | null; context: { model: string; id: number } | null }
export interface FileRef { id: number; name: string; size: number; kind: 'upload' | 'evlet' | 'generated'; created_at?: number }
export interface Message { id: number; run_id: number | null; role: 'user' | 'assistant'; content: string; created_at: number; files: FileRef[] }
export interface Approval {
  id: number; tool: string; origin: string; client: string | null; run_id: number | null; arguments: Record<string, unknown>;
  summary: { model?: string; id?: number; record?: Record<string, unknown>; values?: Record<string, unknown>; action?: string; operation?: string; service?: string; group?: string };
  status: 'pending' | 'approved' | 'declined' | 'used' | 'expired'; created_at: number; expires_at: number;
}
export interface Activity {
  call_id: string; tool: string; label: string; status: 'running' | 'done' | 'refused' | 'failed' | 'approval';
  summary?: string; duration_ms?: number; inputs?: Record<string, unknown> | null; records?: Array<{ model?: string; id?: number; file?: number; name?: string }>;
  error?: string; approval?: Approval;
}
export interface Run { id: number; status: 'running' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled'; error: string | null; model: string; origin: string;
  started_at: number; finished_at: number | null; activities: Activity[] }
export interface Stored { conversation: Conversation; messages: Message[]; runs: Run[]; files: FileRef[]; usage: { tokens: number; cost: string } }
export interface RunEvent { id: number; type: string; at: number; [key: string]: unknown }
export interface Library { records: Array<{ model: string; record_id: number; file_id: number; name: string; size: number }>; chats: Array<{ id: number; name: string; size: number; conversation: string | null }> }
export interface Task {
  id: number; name: string; instructions: string; cron: string; timezone: string; active: boolean; allow_changes: boolean; created_at: number;
  last_run_at: number | null; last_status: string | null; next_run_at: number | null;
  runs?: Array<{ id: number; at: number; status: 'running' | 'done' | 'failed' | 'skipped'; conversation_id: number | null; error: string | null; cost: string }>;
}
export interface Parsed { cron: string | null; summary: string; instructions: string; name: string; timezone: string; next: string[] }

/** Eva for this person, or null when it is off or not allowed (every route answers 404 then). */
export async function evaStatus(api: ApiClient): Promise<EvaStatus | null> {
  try { return (await api.request<EvaStatus>('GET', '/api/_eva')).data; }
  catch (failure) { if (failure instanceof ApiError && (failure.kind === 'not_found' || failure.kind === 'forbidden')) return null; throw failure; }
}

export class Eva {
  constructor(readonly api: ApiClient) {}
  private async call<T>(method: string, path: string, body?: unknown, query?: Record<string, string | number | undefined>) {
    return (await this.api.request<T>(method, path, { body, query })).data;
  }
  status() { return this.call<EvaStatus>('GET', '/api/_eva'); }
  conversations(query: { q?: string; archived?: boolean } = {}) {
    return this.call<{ items: Conversation[]; archived: number; next: number | null }>('GET', '/api/_eva/conversations', undefined, { q: query.q || undefined, archived: query.archived ? '1' : undefined });
  }
  create(body: { title?: string; context?: { model: string; id: number } } = {}) { return this.call<Conversation>('POST', '/api/_eva/conversations', body); }
  open(id: number) { return this.call<Stored>('GET', `/api/_eva/conversations/${id}`); }
  change(id: number, body: { title?: string; archived?: boolean }) { return this.call<Conversation>('PATCH', `/api/_eva/conversations/${id}`, body); }
  remove(id: number) { return this.call<null>('DELETE', `/api/_eva/conversations/${id}`); }
  send(id: number, text: string, files: number[], model?: string) {
    return this.call<{ run: { id: number; status: string }; message: Message }>('POST', `/api/_eva/conversations/${id}/messages`, { text, files, ...(model ? { model } : {}) });
  }
  events(run: number, after: number) { return this.call<{ items: RunEvent[]; status: Run['status']; done: boolean }>('GET', `/api/_eva/runs/${run}/events`, undefined, { after }); }
  cancel(run: number) { return this.call<{ id: number; status: string }>('POST', `/api/_eva/runs/${run}/cancel`, {}); }
  answer(approval: number, approve: boolean) { return this.call<Approval>('POST', `/api/_tools/approvals/${approval}`, { approve }); }
  approvals() { return this.call<{ items: Approval[] }>('GET', '/api/_tools/approvals'); }
  upload(name: string, content: string) { return this.call<FileRef>('POST', '/api/_eva/files', { name, content }); }
  library(q: string) { return this.call<Library>('GET', '/api/_eva/library', undefined, { q }); }
  importFile(model: string, id: number, fileId: number) { return this.call<FileRef>('POST', '/api/_eva/files/import', { model, id, file_id: fileId }); }
  fileUrl(id: number) { return this.api.url(`/api/_eva/files/${id}`); }
  tasks() { return this.call<{ items: Task[]; limit: number; jobs: boolean }>('GET', '/api/_eva/tasks'); }
  task(id: number) { return this.call<Task>('GET', `/api/_eva/tasks/${id}`); }
  parse(text: string, timezone?: string) { return this.call<Parsed>('POST', '/api/_eva/tasks/parse', { text, ...(timezone ? { timezone } : {}) }); }
  createTask(body: Partial<Task>) { return this.call<Task>('POST', '/api/_eva/tasks', body); }
  changeTask(id: number, body: Partial<Task>) { return this.call<Task>('PATCH', `/api/_eva/tasks/${id}`, body); }
  deleteTask(id: number) { return this.call<null>('DELETE', `/api/_eva/tasks/${id}`); }
  runTask(id: number) { return this.call<{ job: { id: number } }>('POST', `/api/_eva/tasks/${id}/run`, {}); }
}

/** Follow one run's events: the SSE stream where the browser has it, else polling. Stops on its own when the run ends. */
export function follow(eva: Eva, run: number, onEvent: (event: RunEvent) => void, onEnd: () => void): () => void {
  let stopped = false, last = 0, source: EventSource | null = null, timer: ReturnType<typeof setTimeout> | null = null;
  const stop = () => { stopped = true; source?.close(); if (timer) clearTimeout(timer); };
  const seen = (event: RunEvent) => { if (event.id <= last) return; last = event.id; onEvent(event); };
  const poll = async () => {
    if (stopped) return;
    try {
      const page = await eva.events(run, last);
      page.items.forEach(seen);
      if (page.done) { stop(); onEnd(); return; }
    } catch (failure) {
      if (failure instanceof ApiError && failure.kind === 'not_found') { stop(); onEnd(); return; }
    }
    if (!stopped) timer = setTimeout(() => void poll(), 700);
  };
  if (typeof EventSource !== 'undefined') {
    source = new EventSource(eva.api.url(`/api/_eva/runs/${run}/stream`), { withCredentials: true });
    const types = ['run.started', 'message.delta', 'tool.started', 'tool.completed', 'tool.failed', 'approval.requested', 'approval.answered',
      'usage.updated', 'run.completed', 'run.failed', 'run.cancelled'];
    for (const type of types) source.addEventListener(type, message => seen(JSON.parse((message as MessageEvent).data)));
    source.addEventListener('end', () => { stop(); onEnd(); });
    // The stream reconnects on its own with the last id; after repeated failures, poll instead.
    let failures = 0;
    source.onerror = () => { if (++failures > 3 && !stopped) { source?.close(); source = null; void poll(); } };
  } else void poll();
  return stop;
}

/** A file from the person's computer as base64, for upload(). */
export function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
