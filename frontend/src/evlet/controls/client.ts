// The Controls routes (/api/_controls/*) on top of the framework client. Every route checks access
// on the server; anyone without it gets 404.

import { ApiClient, type ClientOptions } from '../api';

export const OPS = ['read', 'create', 'update', 'delete'] as const;

export interface PermValue { ops: string[]; read: string[] | null; write: string[] | null; scope: string | null }
export type Kind = 'perm' | 'setting' | 'field' | 'custom' | 'layout' | 'list' | 'naming';
export interface OverrideItem {
  kind: Kind; key: string; file: any; value: any;
  changed_by: string | null; changed_at: number | null; source?: 'app' | 'framework';
}
export interface Problem { kind: string; key: string; error: string }
export interface Overrides { items: OverrideItem[]; problems: Problem[]; version: number }
export interface RoleRow {
  id: string; kind: 'framework' | 'custom'; base: string | null; realm: 'desk' | 'portal'; opens_controls: boolean;
  description: string; users: number; app: string | null; declared: boolean;
}
export interface UserRow { id: number; username: string; role: string; active: boolean; sessions: number; assistants?: number; two_step?: { since: number | null; agent: string } | null }
/** A field as models.py declares it: the props Controls can override. */
export interface FieldProps {
  label: string; help_text: string; placeholder: string; hidden: boolean; required: boolean; read_only: boolean;
  default: unknown; options: string[] | null;
}
export interface FieldOverride {
  label?: string; help_text?: string; placeholder?: string; hidden?: boolean; required?: boolean; read_only?: boolean;
  default?: unknown; options_add?: string[]; options_hidden?: string[];
}
export interface LayoutSection { title: string; fields: Array<{ name: string; width: 6 | 12 }> }
export interface ListValue { columns: string[]; page_size: number; sort: string }
export interface SeriesValue { id: string; label: string; format: string; when: string }
export interface FieldInfo {
  name: string; type: string; custom: boolean; editable: boolean; stored: boolean; unique: boolean; target: string | null; width: 6 | 12;
}
export interface ModelInfo {
  name: string; label: string; app: string | null; fields: Array<{ name: string; label: string; read_only: boolean }>; scopes: Array<string | null>;
  table: string; records: number; fields_detail: FieldInfo[]; naming: { field: string; date_field: string | null } | null;
}
export interface Counter {
  prefix: string; value: number; last_name: string | null; how: 'issued' | 'set' | null; note: string | null;
  changed_by: string | null; changed_at: number | null; series: string | null; next: string; issued: number | null;
}
export interface TryResult {
  checks: Array<{ id: string; format?: string; when?: string; next?: string }>;
  match: string | null; label?: string; name: string | null; errors?: Record<string, string>; error: string | null;
}
export interface AuditRow { id: number; at: number; actor: string; area: string; target: string; detail: string }
/** A Needs attention item. line is a config line to copy, such as trusted_proxies=("127.0.0.1",). */
export interface Attention { tone: string; icon: string; page: string; title: string; text: string; line?: string }
export interface AppInfo {
  id: string; title: string; version: string; source: string; models: string[]; connectors: string[]; permissions: number; schedules: string[];
  frontends: Array<{ name: string; path: string; host: string; realm: string }>;
  /** A newer source waiting to be migrated, or null. */
  update: { from: string; to: string | null; error: string | null } | null;
  /** The database is migrated but this server still runs the version it started with. */
  restart: boolean;
}
export interface RouteRow { host: string; path: string; serves: string; realm: string; source: string; locked: boolean }
export interface Routing {
  rows: RouteRow[]; planned: string[]; hosts: Array<{ host: string; primary: boolean; sub: boolean; realm: string }>; primary: string | null;
  proxy: { caddy: string; nginx: string } | null; proxy_error: string | null;
}
export interface HostCheck { host: string; records: string; dns: string | null; certificate: 'valid' | 'missing' | null; expires: number | null }
export interface Schedule {
  name: string; task: string; every: number | null; expression: string | null; timezone: string | null; next_run: number;
  active: boolean; paused: boolean; app: string | null;
}
export interface JobRow { id: number; task: string; actor: number | null; model: string | null; record_id: number | null; state: string; attempts: number; run_at: number; error: string | null }
export interface Jobs { available: boolean; schedules: Schedule[]; jobs: JobRow[] }
export interface CodeFile { file: string; code: string }
export interface ControlsState {
  instance: { name: string; host: string; evlet: string; version: number; slug: string | null; managed: boolean };
  me: { id: number; username: string; role: string };
  apps: AppInfo[];
  jobs: { failed: number };
  models: ModelInfo[];
  roles: RoleRow[];
  scopes: Record<string, string>;
  settings: Record<string, { file: any; value: any }>;
  controls: { allow_ips: string[]; require_two_step: boolean };
  attention: Attention[];
  overrides: number;
  users: { active: number; total: number };
  audit: AuditRow[];
  two_step: boolean;
  /** The address this request came from, as the allowlist sees it. */
  client: string;
  /** Why fields can't be removed on this server, or null. */
  remove_fields: string | null;
  instance_fields: boolean;
}

export interface ConnectorField {
  name: string; label: string; type: 'text' | 'number' | 'select' | 'toggle' | 'role' | 'secret';
  options: Array<[string, string]>; default: any; required: boolean; help: string;
}
export interface ConnectorInfo {
  id: string; title: string; app: string; category: string; description: string; accounts: boolean; testable: boolean; fields: ConnectorField[];
}
/** A saved connector or email account. Secrets only ever say whether they are saved. */
export interface ConnectorRow {
  id: string; connector: string; account: string | null; status: 'connected' | 'error'; default: boolean;
  settings: Record<string, any>; secrets: Record<string, { saved: boolean }>; changed_by: string | null; changed_at: number | null;
}
export interface ConnectorBody { settings: Record<string, unknown>; secrets: Record<string, string> }
export interface TestResult { ok: boolean; message?: string; error?: string }
export interface KeyScope { access: 'read' | 'write'; models: string[] | null }
export interface ApiKeyRow {
  id: number; user_id: number; username: string; role: string; name: string; prefix: string;
  scope: KeyScope; created_at: number; last_used: number | null;
}
export interface CreatedKey { key: ApiKeyRow; token: string }
/** The key routes a keys page needs: Controls (every user) or a person's own. */
export interface KeysApi {
  keys(): Promise<{ items: ApiKeyRow[]; models?: Array<{ name: string; label: string }> }>;
  createKey(body: { name: string; scope: KeyScope; user?: string }): Promise<CreatedKey>;
  revokeKey(id: number): Promise<unknown>;
}

/** A connected AI assistant, as Controls lists it. Per-request detail stays on each person's own page. */
export interface AssistantRow { id: number; client_name: string; username: string; role: string; created_at: number; last_used: number | null; calls: number }
export interface McpOverview { address: string; clients: AssistantRow[]; totals: { reads: number; writes: number; denied: number } }

export class ControlsClient extends ApiClient implements KeysApi {
  constructor(options: ClientOptions = {}) { super(options); }

  async state() { return (await this.request<ControlsState>('GET', '/api/_controls/state')).data; }
  async overrides() { return (await this.request<Overrides>('GET', '/api/_controls/overrides')).data; }
  async saveOverrides(changes: Array<{ kind: string; key: string; value: unknown }>) {
    return (await this.request<{ version: number; saved: number }>('POST', '/api/_controls/overrides', { body: { changes } })).data;
  }
  async users(offset = 0) {
    return (await this.request<{ items: UserRow[]; total: number; offset: number; limit: number }>('GET', '/api/_controls/users', { query: { offset } })).data;
  }
  async addUser(body: { username: string; password: string; role: string }) {
    return (await this.request<{ id: number; username: string; role: string }>('POST', '/api/_controls/users', { body })).data;
  }
  async changeUser(id: number, body: Record<string, unknown>) { await this.request('PATCH', `/api/_controls/users/${id}`, { body }); }
  async roles() { return (await this.request<{ items: RoleRow[] }>('GET', '/api/_controls/roles')).data.items; }
  async createRole(body: { id: string; base: string; realm: string; description: string }) {
    return (await this.request<{ items: RoleRow[] }>('POST', '/api/_controls/roles', { body })).data.items;
  }
  async changeRole(id: string, body: Record<string, unknown>) {
    return (await this.request<{ items: RoleRow[] }>('PATCH', `/api/_controls/roles/${encodeURIComponent(id)}`, { body })).data.items;
  }
  async addField(model: string, body: Record<string, unknown>) {
    return (await this.request<{ name: string; backup: string | null; version: number }>('POST', `/api/_controls/fields/${model}`, { body })).data;
  }
  async removeField(model: string, name: string) {
    return (await this.request<{ name: string; backup: string | null; version: number }>('DELETE', `/api/_controls/fields/${model}/${name}`)).data;
  }
  async counters(model: string) {
    return (await this.request<{ field: string; items: Counter[] }>('GET', `/api/_controls/naming/${model}/counters`)).data;
  }
  async setCounter(model: string, body: { prefix: string; value: number; note: string }) {
    return (await this.request<{ field: string; items: Counter[] }>('POST', `/api/_controls/naming/${model}/counters`, { body })).data;
  }
  async tryName(model: string, body: { values: Record<string, unknown>; series?: SeriesValue[] }) {
    return (await this.request<TryResult>('POST', `/api/_controls/naming/${model}/try`, { body })).data;
  }
  async exportCode(only?: { kind: string; key: string }) {
    return (await this.request<CodeFile[]>('GET', '/api/_controls/overrides/export', { query: only ?? {} })).data;
  }
  async audit(query: { area?: string; q?: string; before?: number }) {
    return (await this.request<{ items: AuditRow[]; next: number | null; areas: string[] }>('GET', '/api/_controls/audit', { query })).data;
  }
  async routing() { return (await this.request<Routing>('GET', '/api/_controls/routing')).data; }
  // The server takes JSON bodies only: actions without data send {}.
  async checkHosts() { return (await this.request<{ hosts: HostCheck[]; notes: string[]; ok: boolean }>('POST', '/api/_controls/routing/check', { body: {} })).data; }
  async jobs() { return (await this.request<Jobs>('GET', '/api/_controls/jobs')).data; }
  async setSchedule(name: string, active: boolean) { return (await this.request<Jobs>('POST', `/api/_controls/jobs/schedules/${name}`, { body: { active } })).data; }
  async runSchedule(name: string) { return (await this.request<Jobs & { job: number }>('POST', `/api/_controls/jobs/schedules/${name}/run`, { body: {} })).data; }
  async retryJob(id: number) { return (await this.request<Jobs>('POST', `/api/_controls/jobs/${id}/retry`, { body: {} })).data; }
  async connectors() { return (await this.request<{ connectors: ConnectorInfo[]; items: ConnectorRow[] }>('GET', '/api/_controls/connectors')).data; }
  async saveConnector(id: string, body: ConnectorBody) {
    return (await this.request<ConnectorRow>('PUT', `/api/_controls/connectors/${encodeURIComponent(id)}`, { body })).data;
  }
  async removeConnector(id: string) { await this.request('DELETE', `/api/_controls/connectors/${encodeURIComponent(id)}`); }
  async testConnector(id: string, body: Partial<ConnectorBody> & { to?: string }) {
    return (await this.request<TestResult>('POST', `/api/_controls/connectors/${encodeURIComponent(id)}/test`, { body })).data;
  }
  async makeDefault(id: string) { return (await this.request<ConnectorRow>('POST', `/api/_controls/connectors/${encodeURIComponent(id)}/default`, { body: {} })).data; }
  async keys() { return (await this.request<{ items: ApiKeyRow[] }>('GET', '/api/_controls/keys')).data; }
  async createKey(body: { name: string; scope: KeyScope; user?: string }) { return (await this.request<CreatedKey>('POST', '/api/_controls/keys', { body })).data; }
  async revokeKey(id: number) { return (await this.request<{ items: ApiKeyRow[] }>('DELETE', `/api/_controls/keys/${id}`)).data; }
  async mcp() { return (await this.request<McpOverview>('GET', '/api/_controls/mcp')).data; }
  async signOutAssistant(id: number) { return (await this.request<McpOverview>('DELETE', `/api/_controls/mcp/${id}`)).data; }
  async deleteRole(id: string) {
    return (await this.request<{ items: RoleRow[] }>('DELETE', `/api/_controls/roles/${encodeURIComponent(id)}`)).data.items;
  }
}
