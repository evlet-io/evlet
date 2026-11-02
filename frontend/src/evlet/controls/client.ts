// The Controls routes (/api/_controls/*) on top of the framework client. Every route checks access
// on the server; anyone without it gets 404.

import { ApiClient, type ClientOptions } from '../api';

export const OPS = ['read', 'create', 'update', 'delete'] as const;

export interface PermValue { ops: string[]; read: string[] | null; write: string[] | null; scope: string | null }
export type Kind = 'perm' | 'setting' | 'field' | 'layout' | 'list' | 'naming';
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
export interface UserRow { id: number; username: string; role: string; active: boolean; sessions: number }
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
  /** The address this request came from, as the allowlist sees it. */
  client: string;
  /** Why fields can't be removed on this server, or null. */
  remove_fields: string | null;
  instance_fields: boolean;
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
  async deleteRole(id: string) {
    return (await this.request<{ items: RoleRow[] }>('DELETE', `/api/_controls/roles/${encodeURIComponent(id)}`)).data.items;
  }
}
