// Override drafts behind the change bar: what is saved, what changed since, and the save itself.
// Saving sends every change in one request; the server applies all of them or none.

import { ValidationError } from '../api';
import { cell, derive } from '../reactive';
import type { ControlsClient, FieldOverride, LayoutSection, ListValue, OverrideItem, PermValue, SeriesValue } from './client';

export const idOf = (kind: string, key: string) => `${kind}:${key}`;
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export interface Change { id: string; item: OverrideItem; from: unknown; to: unknown }

export class Drafts {
  readonly saved = cell(new Map<string, OverrideItem>(), { name: 'controls.saved' });
  readonly draft = cell(new Map<string, unknown>(), { name: 'controls.draft' });
  readonly errors = cell(new Map<string, string>(), { name: 'controls.errors' });
  readonly saving = cell(false, { name: 'controls.saving' });
  readonly changes = derive<Change[]>(() => {
    const saved = this.saved.get();
    return [...this.draft.get()].filter(([id]) => saved.has(id)).map(([id, to]) => ({ id, item: saved.get(id)!, from: saved.get(id)!.value, to }));
  });

  constructor(private readonly api: ControlsClient) {}

  /** Saved overrides from the server. Drafts that still differ are kept. */
  load(items: OverrideItem[]) {
    const saved = new Map(items.map(item => [idOf(item.kind, item.key), item]));
    this.saved.set(saved);
    const draft = new Map([...this.draft.peek()].filter(([id, value]) => saved.has(id) && !same(value, saved.get(id)!.value)));
    this.draft.set(draft);
  }

  item(id: string) { return this.saved.get().get(id); }
  /** The override the page shows (draft first), or null for the file value. */
  override(id: string): unknown {
    const draft = this.draft.get();
    return draft.has(id) ? draft.get(id) : this.saved.get().get(id)?.value ?? null;
  }
  value<T = unknown>(id: string): T { return (this.override(id) ?? this.item(id)?.file) as T; }
  dirty(id: string) { return this.draft.get().has(id); }
  overridden(id: string) { return this.override(id) !== null; }

  set(id: string, value: unknown) {
    const item = this.saved.peek().get(id);
    if (!item) return;
    const next = same(value, item.file) ? null : value;
    const draft = new Map(this.draft.peek());
    if (same(next, item.value)) draft.delete(id); else draft.set(id, next);
    this.draft.set(draft);
    if (this.errors.peek().has(id)) { const errors = new Map(this.errors.peek()); errors.delete(id); this.errors.set(errors); }
  }
  reset(id: string) { const item = this.saved.peek().get(id); if (item) this.set(id, item.file); }
  discard() { this.draft.set(new Map()); this.errors.set(new Map()); }

  /** Save every change at once. Per-item errors land in `errors`; nothing is saved then. */
  async save(): Promise<boolean> {
    const changes = this.changes.peek();
    if (!changes.length || this.saving.peek()) return false;
    this.saving.set(true);
    try {
      await this.api.saveOverrides(changes.map(change => ({ kind: change.item.kind, key: change.item.key, value: change.to })));
      this.draft.set(new Map());
      this.errors.set(new Map());
      this.load((await this.api.overrides()).items);
      return true;
    } catch (failure) {
      if (!(failure instanceof ValidationError)) throw failure;
      this.errors.set(new Map(Object.entries(failure.fields).flatMap(([field, message]) => {
        const index = /^changes\.(\d+)$/.exec(field);
        return index && changes[Number(index[1])] ? [[changes[Number(index[1])].id, message] as [string, string]] : [];
      })));
      return false;
    } finally {
      this.saving.set(false);
    }
  }
}

// ---------- describing values ----------

const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
export const SETTING_LABELS: Record<string, string> = {
  'security.password_min': 'Minimum password length', 'security.session_hours': 'Session length',
  'security.lockout': 'Failed sign-ins before lockout', 'security.two_step_roles': 'Two-step sign-in required for',
  'security.controls_ips': 'Controls allowed from',
  'mcp.enabled': 'MCP server', 'mcp.allow_writes': 'Allow writes', 'mcp.rate': 'Tool calls per minute',
  'mcp.disabled_tools': 'Disabled tools', 'mcp.hidden_models': 'Hidden models',
  'mail.personal': 'People connect their own mailbox',
  'unsaved.server_days': 'Unsaved changes kept on the server for', 'unsaved.server_entries': 'Unsaved changes per person',
  'unsaved.browser_days': 'Unsaved new records kept in the browser for', 'unsaved.browser_entries': 'Unsaved new records per person in one browser',
  'unsaved.entry_kb': 'Largest unsaved changes entry',
};

export function permSummary(value: PermValue | null | undefined, scopes: Record<string, string>) {
  if (!value || !value.ops.length) return 'No access';
  const parts = [value.ops.map(cap).join(', '), (scopes[value.scope ?? ''] ?? value.scope ?? '').toLowerCase()];
  if (value.read) parts.push(`${value.read.length} field${value.read.length === 1 ? '' : 's'}`);
  return parts.filter(Boolean).join(' · ');
}

export function settingText(key: string, value: unknown) {
  if (typeof value === 'boolean') return value ? 'On' : 'Off';
  if (key === 'mcp.rate') return `${value} a minute`;
  if (Array.isArray(value)) return value.length ? value.join(', ') : key === 'security.controls_ips' ? 'any address' : 'none';
  if (key === 'security.session_hours') return `${value} hours`;
  if (key === 'security.password_min') return `${value} characters`;
  if (key === 'security.lockout') return `${value} attempts`;
  if (key === 'unsaved.server_days' || key === 'unsaved.browser_days') return `${value} days`;
  if (key === 'unsaved.entry_kb') return `${value} KB`;
  return String(value);
}

const PROPS: Record<string, string> = { label: 'Label', help_text: 'Help', placeholder: 'Placeholder', hidden: 'Hidden', required: 'Required',
  read_only: 'Read only', default: 'Default', options_add: 'Adds', options_hidden: 'Hides' };

/** "Required · Label: Expected value" for a field override, or the file note. */
export function fieldSummary(value: FieldOverride | null) {
  if (!value) return 'As in models.py';
  return Object.entries(value).map(([prop, item]) => typeof item === 'boolean' ? (item ? PROPS[prop] : `Not ${PROPS[prop].toLowerCase()}`)
    : `${PROPS[prop]}: ${Array.isArray(item) ? item.join(', ') : String(item)}`).join(' · ');
}

function modelText(kind: string, value: unknown) {
  if (kind === 'field') return fieldSummary(value as FieldOverride | null);
  if (kind === 'layout') {
    const sections = value as LayoutSection[];
    return sections.map(section => `${section.title} (${section.fields.length})`).join(' · ');
  }
  if (kind === 'list') {
    const list = value as ListValue;
    return `${list.columns.join(', ')} · ${list.page_size} rows · sorted by ${list.sort}${list.open_in === 'panel' ? ' · opens in a side panel' : ''}`;
  }
  return (value as SeriesValue[]).map(series => series.when ? `${series.format} when ${series.when}` : series.format).join(' · ');
}

/** Area, target and the before and after text of one change, for Review. */
export function describe(change: Change, scopes: Record<string, string>) {
  const { item } = change;
  const shown = (value: unknown) => value === null || value === undefined ? null : value;
  if (item.kind !== 'perm' && item.kind !== 'setting') {
    const text = (value: unknown) => item.kind === 'field' ? fieldSummary(shown(value) as FieldOverride | null)
      : modelText(item.kind, shown(value) ?? item.file) + (shown(value) === null ? ' (file)' : '');
    return { area: item.kind === 'naming' ? 'Naming' : 'Models', target: item.kind === 'field' ? item.key : `${item.key} ${item.kind === 'naming' ? 'series' : item.kind}`,
      from: text(change.from), to: text(change.to) };
  }
  if (item.kind === 'perm') {
    const [role, model] = item.key.split('|');
    return { area: 'Permissions', target: `${role} · ${model}`,
      from: permSummary((shown(change.from) ?? item.file) as PermValue, scopes) + (shown(change.from) === null ? ' (file)' : ''),
      to: permSummary((shown(change.to) ?? item.file) as PermValue, scopes) + (shown(change.to) === null ? ' (file)' : '') };
  }
  return { area: item.key.startsWith('mcp.') ? 'MCP & AI' : item.key.startsWith('mail.') ? 'Email' : 'Security', target: SETTING_LABELS[item.key] ?? item.key,
    from: settingText(item.key, shown(change.from) ?? item.file) + (shown(change.from) === null ? ' (file)' : ''),
    to: settingText(item.key, shown(change.to) ?? item.file) + (shown(change.to) === null ? ' (file)' : '') };
}

// ---------- allowed networks ----------

function toBits(address: string): [bigint, number] | null {
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(address)) {
    const parts = address.split('.').map(Number);
    return parts.some(part => part > 255) ? null : [parts.reduce((total, part) => total * 256n + BigInt(part), 0n), 32];
  }
  if (!address.includes(':')) return null;
  const [head, tail = ''] = address.split('::');
  const left = head ? head.split(':') : [], right = tail ? tail.split(':') : [];
  const groups = address.includes('::') ? [...left, ...Array(8 - left.length - right.length).fill('0'), ...right] : left;
  if (groups.length !== 8 || groups.some(group => !/^[0-9a-f]{1,4}$/i.test(group))) return null;
  return [groups.reduce((total, group) => total * 65536n + BigInt(parseInt(group, 16)), 0n), 128];
}

/** Whether an address is inside any of the networks; an empty list allows every address. */
export function ipAllowed(address: string, networks: string[]): boolean {
  if (!networks.length) return true;
  const ip = toBits(address);
  if (!ip) return false;
  return networks.some(network => {
    const [base, size] = network.split('/');
    const net = toBits(base);
    if (!net || net[1] !== ip[1]) return false;
    const shift = BigInt(ip[1] - (size === undefined ? ip[1] : Number(size)));
    return shift >= 0n && (ip[0] >> shift) === (net[0] >> shift);
  });
}
