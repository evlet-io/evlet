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
export interface UserRow { id: number; username: string; role: string; active: boolean; sessions: number }
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

export class ControlsClient extends ApiClient {
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
  async deleteRole(id: string) {
    return (await this.request<{ items: RoleRow[] }>('DELETE', `/api/_controls/roles/${encodeURIComponent(id)}`)).data.items;
  }
}
