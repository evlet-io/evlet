// The Controls routes (/api/_controls/*) on top of the framework client. Every route checks access
// on the server; anyone without it gets 404.

import { ApiClient, type ClientOptions } from '../api';

export const OPS = ['read', 'create', 'update', 'delete'] as const;

export interface PermValue { ops: string[]; read: string[] | null; write: string[] | null; scope: string | null }
export interface OverrideItem {
  kind: 'perm' | 'setting'; key: string; file: any; value: any;
  changed_by: string | null; changed_at: number | null; source?: 'app' | 'framework';
}
export interface Problem { kind: string; key: string; error: string }
export interface Overrides { items: OverrideItem[]; problems: Problem[]; version: number }
export interface RoleRow {
  id: string; kind: 'framework' | 'custom'; base: string | null; realm: 'desk' | 'portal'; opens_controls: boolean;
  description: string; users: number; app: string | null; declared: boolean;
}
export interface UserRow { id: number; username: string; role: string; active: boolean; sessions: number; assistants?: number }
export interface ModelInfo { name: string; label: string; app: string | null; fields: Array<{ name: string; label: string; read_only: boolean }>; scopes: Array<string | null> }
export interface AuditRow { id: number; at: number; actor: string; area: string; target: string; detail: string }
export interface Attention { tone: string; icon: string; page: string; title: string; text: string }
export interface ControlsState {
  instance: { name: string; host: string; evlet: string; version: number };
  me: { id: number; username: string; role: string };
  apps: Array<{ id: string; version: string; models: string[] }>;
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
  links: boolean;
  /** The address this request came from, as the allowlist sees it. */
  client: string;
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
