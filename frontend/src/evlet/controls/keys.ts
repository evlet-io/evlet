// API keys: Controls lists every key and creates keys that act as any user; Settings shows a
// person's own (../settings/keys.ts). A key is shown once, then only its prefix.

import { html } from '../dom';
import { ValidationError } from '../api';
import { cell, onCleanup } from '../reactive';
import { Button, IconButton, StatusPill } from '../ui/base';
import { copyText, DialogFooter, openModal, toast } from '../ui/layers';
import type { ControlsContext } from './app';
import type { ApiKeyRow, KeyScope, KeysApi, UserPage } from './client';
import { Callout, Head, stamp, ask, field, message, Select } from '../ui/settings-helpers';

type Choice = { name: string; label: string };
export interface KeysOptions {
  api: KeysApi;
  /** Models a key can be narrowed to. */
  models: () => Choice[];
  /** Controls only: the users a key can act as. Without it the key acts as the person signed in. */
  users?: (query: string, offset: number) => Promise<UserPage>;
  /** Settings only: a new key asks who you are, a two-step code with two-step on, else the password. */
  confirm?: 'code' | 'password';
  title: string;
  sub: string;
  empty: string;
}

const STALE = 30 * 24 * 3600;
export const scopeText = (scope: KeyScope, models: Choice[] = []) =>
  `${scope.models ? scope.models.map(name => models.find(item => item.name === name)?.label ?? name).join(', ') : 'All permitted'} · ${scope.access === 'write' ? 'read and write' : 'read'}`;

/** The keys table with Create key and Revoke; Controls adds the Acts as column. */
export function KeysPage(options: KeysOptions): HTMLElement {
  const rows = cell<ApiKeyRow[] | null>(null);
  const total = cell(0), before = cell<number | undefined>(undefined), next = cell<number | null>(null);
  const history = cell<Array<number | undefined>>([]), loading = cell(false), query = cell(''), owner = cell('');
  let request = 0, alive = true, timer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => { alive = false; request++; clearTimeout(timer); });
  const controls = Boolean(options.users);
  const load = async (cursor?: number) => {
    const mine = ++request;
    loading.set(true);
    try {
      const q = query.peek().trim(), user = owner.peek().trim();
      const page = await options.api.keys(controls ? { ...(cursor === undefined ? {} : { before: cursor }), ...(q ? { q } : {}), ...(user ? { user } : {}) } : undefined);
      if (!alive || mine !== request) return false;
      rows.set(page.items); total.set(page.total ?? page.items.length); next.set(page.next_before ?? null); before.set(cursor);
      return true;
    } catch (failure) { if (alive && mine === request) { toast(message(failure), { icon: 'info' }); rows.set([]); } return false; }
    finally { if (alive && mine === request) loading.set(false); }
  };
  const reload = async () => { clearTimeout(timer); history.set([]); before.set(undefined); await load(); };
  const search = () => {
    clearTimeout(timer); request++; loading.set(true);
    timer = setTimeout(() => void reload(), 200);
  };
  void load();
  const now = () => Math.floor(Date.now() / 1000);
  const revoke = (row: ApiKeyRow) => ask(`Revoke ${row.name}?`, 'Anything using this key gets a 401 from now on.', 'Revoke key', async () => {
    try { await options.api.revokeKey(row.id); toast('Key revoked'); await reload(); }
    catch (failure) { toast(message(failure), { icon: 'info' }); }
  }, true);
  const columns = controls ? 7 : 5;
  return html`<div>
    ${Head(options.title, options.sub, Button({ label: 'Create key', icon: 'plus', variant: 'primary', onClick: () => createKey(options, reload) }))}
    ${controls ? html`<div class="toolbar"><input class="input" aria-label="Search API keys" placeholder="Search name, prefix or user…" prop:value=${query}
      on:input=${(event: Event) => { query.set((event.target as HTMLInputElement).value); search(); }}>
      <input class="input" aria-label="Filter keys by username" placeholder="Exact username" prop:value=${owner}
      on:input=${(event: Event) => { owner.set((event.target as HTMLInputElement).value); search(); }}></div>` : null}
    <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Name</th><th>Key</th>${controls ? html`<th>Acts as</th>` : null}
      <th>${controls ? 'Scope' : 'Can'}</th><th>Last used</th>${controls ? html`<th>Created</th>` : null}<th></th></tr></thead>
      <tbody>${() => rows.get() === null ? html`<tr><td colspan=${columns}><div class="ctl-empty">Loading keys…</div></td></tr>`
        : rows.get()!.length ? rows.get()!.map(row => html`<tr>
          <td><strong>${row.name}</strong>${now() - (row.last_used ?? row.created_at) > STALE ? html` ${StatusPill({ label: 'Unused 30+ days', tint: 'amber' })}` : null}
            ${controls ? null : html`<span class="ctl-sub">Created ${stamp(row.created_at)}</span>`}</td>
          <td><code class="ctl-icode">${row.prefix}…</code></td>${controls ? html`<td>${row.username}</td>` : null}
          <td class="ctl-small-cell">${scopeText(row.scope, options.models())}</td>
          <td class="muted num">${row.last_used ? stamp(row.last_used) : 'Never'}</td>${controls ? html`<td class="muted num">${stamp(row.created_at)}</td>` : null}
          <td class="r">${Button({ label: 'Revoke', size: 'small', variant: 'soft-danger', onClick: () => revoke(row) })}</td></tr>`)
        : html`<tr><td colspan=${columns}><div class="ctl-empty">${controls && (query.get() || owner.get()) ? 'No matching keys.' : options.empty}</div></td></tr>`}</tbody></table></div>
    ${controls ? html`<div class="ctl-tfoot"><span>${() => `${rows.get()?.length ?? 0} of ${total.get()} keys`}</span><span class="ctl-line">
      ${Button({ label: 'Newer keys', size: 'small', disabled: () => loading.get() || !history.get().length, onClick: async () => {
        const previous = history.peek(); if (await load(previous[previous.length - 1])) history.set(previous.slice(0, -1));
      } })}
      ${Button({ label: 'Older keys', size: 'small', disabled: () => loading.get() || next.get() === null, onClick: async () => {
        const cursor = next.peek(); if (cursor === null) return;
        const previous = before.peek(); if (await load(cursor)) history.set([...history.peek(), previous]);
      } })}</span></div>` : null}
  </div>` as HTMLElement;
}

function createKey(options: KeysOptions, reload: () => Promise<void>) {
  const name = cell(''), user = cell(''), access = cell<'read' | 'write'>('read'), model = cell('');
  const errors = cell<Record<string, string>>({}), busy = cell(false), created = cell(''), proof = cell('');
  const people = cell<UserPage['items']>([]), userQuery = cell(''), userOffset = cell(0), userLimit = cell(100), userTotal = cell(0), userLoading = cell(false);
  let ownerRequest = 0, alive = true, timer: ReturnType<typeof setTimeout> | undefined;
  const loadUsers = async (offset = 0) => {
    if (!options.users) return;
    clearTimeout(timer);
    const mine = ++ownerRequest;
    userLoading.set(true);
    try {
      const page = await options.users(userQuery.peek().trim(), offset);
      if (!alive || mine !== ownerRequest) return;
      people.set(page.items); userOffset.set(page.offset); userLimit.set(page.limit); userTotal.set(page.total);
      user.set(page.items[0]?.username ?? '');
    } catch (failure) { if (alive && mine === ownerRequest) errors.set({ user: message(failure) }); }
    finally { if (alive && mine === ownerRequest) userLoading.set(false); }
  };
  void loadUsers();
  const submit = async () => {
    if (busy.peek() || userLoading.peek()) return;
    if (!name.peek().trim()) return errors.set({ name: 'Give the key a name so you can recognise it later.' });
    if (options.users && !user.peek()) return errors.set({ user: 'Choose an active user.' });
    if (options.confirm && !proof.peek().trim())
      return errors.set(options.confirm === 'code' ? { code: 'Enter a code from your app, or a backup code.' } : { password: 'Enter your password.' });
    busy.set(true);
    try {
      const scope: KeyScope = { access: access.peek(), models: model.peek() ? [model.peek()] : null };
      const result = await options.api.createKey({ name: name.peek().trim(), scope, ...(options.users ? { user: user.peek() } : {}),
        ...(options.confirm === 'code' ? { code: proof.peek().trim() } : options.confirm === 'password' ? { password: proof.peek() } : {}) });
      created.set(result.token);
      await reload();
    } catch (failure) {
      errors.set(failure instanceof ValidationError ? failure.fields : { _form: message(failure) });
    } finally { busy.set(false); }
  };
  openModal({ title: () => created.get() ? 'Key created' : 'Create API key', icon: 'hash', tint: 'blue', narrow: true, onSubmit: () => { if (!created.peek()) void submit(); },
    body: () => html`${() => created.get() ? html`<div class="ctl-form">
        ${Callout('warn', 'info', "Copy it now. For security it won't be shown again.")}
        <div class="ctl-line ctl-token"><code class="grow">${created}</code>${IconButton({ icon: 'copy', tip: 'Copy', onClick: () => void copyText(created.peek(), 'the key') })}</div></div>`
      : html`<div class="ctl-form">
        ${field('Name', 'ctl-k-name', html`<input class="input" id="ctl-k-name" autocomplete="off" placeholder="Website lead form" prop:value=${name}
          on:input=${(event: Event) => { name.set((event.target as HTMLInputElement).value); errors.set({}); }}>`, () => null, () => errors.get().name ?? null)}
        ${options.users ? html`${field('Find user', 'ctl-k-user-search', html`<input class="input" id="ctl-k-user-search" placeholder="Search users…" prop:value=${userQuery}
          on:input=${(event: Event) => { userQuery.set((event.target as HTMLInputElement).value); clearTimeout(timer); ownerRequest++; userLoading.set(true); user.set('');
            timer = setTimeout(() => void loadUsers(), 200); }}>`)}
          ${field('Acts as', 'ctl-k-user', Select({ id: 'ctl-k-user', value: () => user.get(),
            options: () => people.get().map(item => [item.username, `${item.username} (${item.role})`]), onChange: value => { user.set(value); errors.set({}); } }),
            () => userLoading.get() ? 'Loading users…' : people.get().length ? null : 'No eligible users on this page.', () => errors.get().user ?? null)}
          <div class="ctl-line"><span class="muted ctl-small">${() => `${userOffset.get() + (people.get().length ? 1 : 0)}–${Math.min(userOffset.get() + userLimit.get(), userTotal.get())} of ${userTotal.get()} active users`}</span>
            ${Button({ label: 'Previous users', size: 'small', disabled: () => userLoading.get() || userOffset.get() === 0, onClick: () => void loadUsers(Math.max(0, userOffset.peek() - userLimit.peek())) })}
            ${Button({ label: 'Next users', size: 'small', disabled: () => userLoading.get() || userOffset.get() + userLimit.get() >= userTotal.get(), onClick: () => void loadUsers(userOffset.peek() + userLimit.peek()) })}</div>` : null}
        ${field('Can', 'ctl-k-access', Select({ id: 'ctl-k-access', value: () => access.get(), options: () => [['read', 'Read'], ['write', 'Read and write']],
          onChange: value => { access.set(value as 'read' | 'write'); errors.set({}); } }))}
        ${field('Models', 'ctl-k-models', Select({ id: 'ctl-k-models', value: () => model.get(),
          options: () => [['', options.users ? 'All permitted' : 'Everything you can see'], ...options.models().map(item => [item.name, item.label] as [string, string])],
          onChange: value => { model.set(value); errors.set({}); } }), () => null, () => errors.get().scope ?? null)}
        ${options.confirm === 'code' ? field('Code from your app, or a backup code', 'ctl-k-code', html`<input class="input" id="ctl-k-code" autocomplete="one-time-code" maxlength="9"
          prop:value=${proof} on:input=${(event: Event) => { proof.set((event.target as HTMLInputElement).value); errors.set({}); }}>`, () => null, () => errors.get().code ?? null)
          : options.confirm === 'password' ? field('Your password', 'ctl-k-password', html`<input class="input" id="ctl-k-password" type="password" autocomplete="current-password"
          prop:value=${proof} on:input=${(event: Event) => { proof.set((event.target as HTMLInputElement).value); errors.set({}); }}>`, () => null, () => errors.get().password ?? null) : null}
        ${() => errors.get()._form ? Callout('err', 'info', errors.get()._form) : null}
        <p class="muted ctl-small">${options.users ? 'The key can never do more than the user it acts as.' : "A key can never do more than you can."}</p></div>`}`,
    footer: close => html`${() => created.get() ? DialogFooter({ submit: { label: 'Done', onClick: close } })
      : DialogFooter({ cancel: close, submit: { label: () => busy.get() ? 'Creating…' : 'Create key', disabled: () => busy.get() || userLoading.get(), onClick: () => void submit() } })}`,
    onClose: () => { alive = false; ownerRequest++; clearTimeout(timer); created.set(''); },
  });
}

export function ApiKeysPage(ctx: ControlsContext): HTMLElement {
  const users = async (query: string, offset: number) => {
    const page = await ctx.api.users(offset, { q: query, active: true });
    return { ...page, items: page.items.filter(item => item.active && (ctx.state.peek().me.role === 'admin' || item.role !== 'admin')) };
  };
  return KeysPage({ api: ctx.api, users, models: () => ctx.state.get().models.map(item => ({ name: item.name, label: item.label })),
    title: 'API keys', sub: "Keys let scripts and other systems call the API as a user. Each key has that user's permissions, narrowed to its scope.",
    empty: 'No keys yet.' });
}
