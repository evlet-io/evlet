// Users and Roles: instance data, so every change saves at once and writes an audit row.

import { html, when, type Child } from '../dom';
import { ValidationError } from '../api';
import { cell, derive } from '../reactive';
import { Avatar, Button, Checkbox, Icon, StatusPill, Switch } from '../ui/base';
import { DialogFooter, openDrawer, openModal, toast } from '../ui/layers';
import { Callout, Head, plural, stamp, type ControlsContext } from './app';
import type { RoleRow, UserRow } from './client';

const ROLE_ID = /^[a-z][a-z0-9_]{1,30}$/;
const TINTS: Record<string, string> = { admin: 'violet', editor: 'blue', viewer: 'slate', portal: 'amber' };
export const RoleChip = (role: string) => StatusPill({ label: role, tint: (TINTS[role] ?? 'teal') as any });
/** Only admins change admin accounts, give the admin role, manage roles and decide who opens Controls. */
export const isAdmin = (ctx: ControlsContext) => ctx.state.peek().me.role === 'admin';
const assignable = (ctx: ControlsContext) => ctx.state.peek().roles.filter(item => isAdmin(ctx) || item.id !== 'admin');
export { message, Select, field, ask } from '../ui/settings-helpers';
import { message, Select, field, ask } from '../ui/settings-helpers';

// ---------- users ----------

/** Whether a role must use two-step sign-in: config.py decides for admin, Controls for every other role. */
export function twoStepRequired(ctx: ControlsContext, role: string) {
  const state = ctx.state.get();
  if (role === 'admin') return state.admin_rules?.two_step ?? true;
  if (Boolean(state.settings['security.two_step']?.value) || (state.settings['security.two_step_roles'].value as string[]).includes(role)) return true;
  // Roles that open Controls need it too, unless an admin switched that off (older servers send no setting: on).
  const opens = state.roles.some(item => item.id === role && item.opens_controls);
  return opens && (state.settings['security.controls_two_step']?.value as boolean | undefined ?? true);
}

/** The shortest password for a role: config.py's for admin, the Controls setting for everyone else. */
export function passwordMinimum(ctx: ControlsContext, role: string) {
  const state = ctx.state.peek();
  return role === 'admin' && state.admin_rules ? state.admin_rules.password_min : state.settings['security.password_min'].value as number;
}

const lengthHint = (minimum: number) => minimum <= 1 ? 'Any password will do' : `At least ${minimum} characters`;

/** "2 sessions · 1 AI assistant": browser sessions and connected assistants both sign out with the user. */
const sessionText = (user: UserRow) => [user.sessions ? plural(user.sessions, 'session') : '', user.assistants ? plural(user.assistants, 'AI assistant') : ''].filter(Boolean).join(' · ');

export function UsersPage(ctx: ControlsContext): HTMLElement {
  const users = cell<UserRow[]>([]), total = cell(0), loaded = cell(false);
  const tab = cell<'all' | 'active' | 'inactive'>('all'), query = cell(''), role = cell('');
  const load = async () => {
    try {
      const page = await ctx.api.users();
      users.set(page.items);
      total.set(page.total);
    } catch (failure) { toast(message(failure), { icon: 'info' }); }
    loaded.set(true);
  };
  void load();
  const shown = derive(() => users.get()
    .filter(user => tab.get() === 'all' || (tab.get() === 'active') === user.active)
    .filter(user => !role.get() || user.role === role.get())
    .filter(user => !query.get() || user.username.includes(query.get().toLowerCase())));
  const refresh = async () => { await load(); ctx.state.set(await ctx.api.state()); };
  const tabs: Array<['all' | 'active' | 'inactive', string, () => number]> = [
    ['all', 'All users', () => users.get().length], ['active', 'Active', () => users.get().filter(user => user.active).length],
    ['inactive', 'Inactive', () => users.get().filter(user => !user.active).length]];
  return html`<div>
    ${Head('Users', 'People who sign in to the desk.', Button({ label: 'Add user', icon: 'user-plus', variant: 'primary', onClick: () => addUser(ctx, refresh) }),
      html` <span class="ctl-badge">${total}</span>`)}
    <div class="ctl-tabs" role="tablist">${tabs.map(([id, label, count]) => html`<button type="button" role="tab" class:on=${() => tab.get() === id}
      aria-selected=${() => String(tab.get() === id)} on:click=${() => tab.set(id)}>${label}<span class="tc">${count}</span></button>`)}</div>
    <div class="toolbar"><label class="ctl-search">${Icon('search', { size: 'sm' })}<input class="input" placeholder="Search users…" aria-label="Search users"
      prop:value=${query} on:input=${(event: Event) => query.set((event.target as HTMLInputElement).value)}></label>
      ${Select({ id: 'ctl-user-role', label: 'Filter by role', value: () => role.get(), options: () => [['', 'All roles'], ...ctx.state.get().roles.map(item => [item.id, item.id] as [string, string])], onChange: value => role.set(value) })}</div>
    <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>User</th><th>Role</th><th>Two-step sign-in</th><th>Sessions</th><th>AI</th><th>Status</th><th></th></tr></thead>
      <tbody>${() => shown.get().length ? shown.get().map(user => html`<tr class="ctl-click" tabindex="0" on:click=${() => openUser(ctx, user, refresh)}
        on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') openUser(ctx, user, refresh); }}>
        <td><span class="ctl-user">${Avatar({ name: user.username, size: 'sm' })}<strong>${user.username}</strong>${user.builtin
          ? [' ', StatusPill({ label: 'Built in', tint: 'slate' })] : null}</span></td>
        <td>${RoleChip(user.role)}</td>
        <td>${user.two_step ? StatusPill({ label: 'On', tint: 'green' }) : twoStepRequired(ctx, user.role) ? StatusPill({ label: 'Off · required', tint: 'rose' }) : html`<span class="muted">Off</span>`}</td>
        <td class="muted num">${sessionText(user) || 'None'}</td>
        <td>${user.allow_eva || user.allow_mcp ? [user.allow_eva ? StatusPill({ label: 'Eva', tint: 'violet' }) : null, ' ', user.allow_mcp ? StatusPill({ label: 'MCP', tint: 'blue' }) : null]
          : html`<span class="muted">No</span>`}</td>
        <td>${user.active ? StatusPill({ label: 'Active', tint: 'green' }) : StatusPill({ label: 'Inactive', tint: 'slate' })}</td>
        <td class="r">${Icon('chevron-right', { size: 'sm' })}</td></tr>`)
        : html`<tr><td colspan="7"><div class="ctl-empty">${() => loaded.get() ? 'No users match.' : 'Loading users…'}</div></td></tr>`}</tbody></table>
      <div class="ctl-tfoot"><span>${() => `${shown.get().length} of ${total.get()}`}</span><span>Accounts are stored in this instance</span></div></div>
  </div>` as HTMLElement;
}

function openUser(ctx: ControlsContext, user: UserRow, refresh: () => Promise<void>) {
  const role = cell(user.role), active = cell(user.active), password = cell(''), error = cell(''), fields = cell<Record<string, string>>({}), busy = cell(false);
  const locked = !isAdmin(ctx) && user.role === 'admin';
  // The built-in Administrator keeps its role and stays active; its password, sessions and two-step are managed as anyone's.
  const builtin = Boolean(user.builtin);
  const roles = locked || builtin ? ctx.state.peek().roles.filter(item => item.id === 'admin') : assignable(ctx);
  const required = twoStepRequired(ctx, user.role);
  const save = async (close: () => void) => {
    const body: Record<string, unknown> = {};
    if (role.peek() !== user.role) body.role = role.peek();
    if (active.peek() !== user.active) body.active = active.peek();
    if (password.peek()) body.password = password.peek();
    if (!Object.keys(body).length) { close(); toast('No changes'); return; }
    busy.set(true);
    try {
      await ctx.api.changeUser(user.id, body);
      close();
      toast(`${user.username} saved`);
      await refresh();
    } catch (failure) {
      if (failure instanceof ValidationError) fields.set(failure.fields);
      else error.set(message(failure));
    } finally { busy.set(false); }
  };
  const resetTwoStep = async () => {
    try {
      await ctx.api.changeUser(user.id, { two_step: 'reset' });
      toast(`Two-step sign-in is reset for ${user.username}`);
      await refresh();
    } catch (failure) { error.set(message(failure)); }
  };
  const signOut = () => {
    const keys = user.keys ?? 0, revoke = cell(false);
    openModal({ title: `Sign ${user.username} out everywhere?`, narrow: true, icon: 'log-out', tint: 'rose',
      body: () => html`<p class="muted ctl-small">Their browser and command line sessions and their AI assistants end. They can sign in again.</p>
        ${keys ? Checkbox({ checked: revoke, label: `Also revoke API keys (${keys} active)` }) : null}`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Sign out everywhere', variant: 'danger', onClick: async () => {
        close();
        try {
          await ctx.api.changeUser(user.id, { sessions: 'end', revoke_keys: revoke.peek() });
          toast(revoke.peek() ? `${user.username} is signed out everywhere and their API keys are revoked` : `${user.username} is signed out everywhere`);
          await refresh();
        } catch (failure) { error.set(message(failure)); }
      } } }) });
  };
  // Allow Eva and Allow MCP save at once, apart from the form: they open Eva or MCP and grant no record access.
  const eva = cell(Boolean(user.allow_eva)), mcp = cell(Boolean(user.allow_mcp));
  const allow = async (key: 'allow_eva' | 'allow_mcp', next: boolean, target: typeof eva) => {
    target.set(next);
    try {
      await ctx.api.changeUser(user.id, { [key]: next });
      toast(`${key === 'allow_eva' ? 'Eva' : 'MCP'} ${next ? 'allowed' : 'turned off'} for ${user.username}`);
      await refresh();
    } catch (failure) { target.set(!next); error.set(message(failure)); }
  };
  openDrawer({ title: user.username, icon: 'user', tint: 'blue', className: 'ctl-drawer',
    body: () => html`<div class="ctl-form">
      ${locked ? Callout('info', 'lock', 'Only an admin can change an admin account.') : null}
      ${builtin ? Callout('info', 'lock', `The System Administrator is built in. Its username, name and role never change, and it can't be deactivated.${
        user.password_set === false ? ' It has no password yet.' : ''}`) : null}
      ${field('Role', 'ctl-u-role', Select({ id: 'ctl-u-role', value: () => role.get(), options: () => roles.map(item => [item.id, item.id]), disabled: locked || builtin,
        onChange: value => { role.set(value); error.set(''); } }), () => roles.find(item => item.id === role.get())?.description ?? null, () => fields.get().role ?? null)}
      <div class="ctl-field"><span class="lbl">Status</span>${Switch({ on: active, disabled: locked || builtin, onChange: next => { active.set(next); error.set(''); },
        label: () => active.get() ? 'Active, can sign in' : "Inactive, can't sign in" })}</div>
      ${when(error, () => Callout('err', 'info', error))}
      <section class="ctl-section"><h3>Two-step sign-in</h3><div class="ctl-line">${user.two_step
        ? [StatusPill({ label: 'On', tint: 'green' }), html`<span class="muted ctl-small grow">Set up ${stamp(user.two_step.since)} from ${user.two_step.agent || 'a browser'}.</span>`,
          ctx.state.peek().me.role === 'admin' ? Button({ label: 'Reset two-step', size: 'small', onClick: () => void resetTwoStep() }) : null]
        : [required ? StatusPill({ label: 'Required, not set up', tint: 'rose' }) : StatusPill({ label: 'Not set up', tint: 'slate' }),
          html`<span class="muted ctl-small">${required ? 'They set it up at their next sign-in.' : 'People set it up in Settings, under Sign-in & security.'}</span>`]}</div></section>
      <section class="ctl-section"><h3>AI access</h3>
        <div class="ctl-field">${Switch({ on: eva, disabled: locked, label: 'Allow Eva', onChange: next => void allow('allow_eva', next, eva) })}
          <span class="muted ctl-small">Chat with Eva and create scheduled tasks. Eva uses their own permissions, never more.</span></div>
        <div class="ctl-field">${Switch({ on: mcp, disabled: locked, label: 'Allow MCP', onChange: next => void allow('allow_mcp', next, mcp) })}
          <span class="muted ctl-small">${user.assistants ? `Connect outside assistants. Turning it off signs out their ${user.assistants} connected assistant${user.assistants === 1 ? '' : 's'}.` : 'Connect outside assistants such as Claude.'}</span></div></section>
      <section class="ctl-section"><h3>Sessions</h3><div class="ctl-line"><span class="grow">${sessionText(user) || 'No active sessions.'}</span>
        ${(user.sessions || user.assistants || user.keys) && !locked ? Button({ label: 'Sign out everywhere', size: 'small', icon: 'log-out', onClick: signOut }) : null}</div></section>
      ${locked ? null : html`<section class="ctl-section"><h3>Password</h3>
        ${field('New password', 'ctl-u-pass', html`<input class="input" id="ctl-u-pass" type="password" autocomplete="new-password"
          placeholder=${() => lengthHint(passwordMinimum(ctx, role.get()))} prop:value=${password}
          on:input=${(event: Event) => password.set((event.target as HTMLInputElement).value)}>`,
          () => 'Leave empty to keep the current one. Their sessions end when it changes.', () => fields.get().password ?? null)}</section>`}
    </div>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: () => busy.get() ? 'Saving…' : 'Save user', disabled: busy, onClick: () => void save(close) } }),
  });
}

function addUser(ctx: ControlsContext, refresh: () => Promise<void>) {
  const username = cell(''), role = cell('viewer'), password = cell(''), fields = cell<Record<string, string>>({}), busy = cell(false);
  const roles = assignable(ctx);
  const input = (id: string, value: typeof username, type = 'text') => html`<input class="input" id=${id} type=${type} autocomplete="off"
    prop:value=${value} on:input=${(event: Event) => { value.set((event.target as HTMLInputElement).value); fields.set({}); }}>`;
  const submit = async (close: () => void) => {
    busy.set(true);
    try {
      await ctx.api.addUser({ username: username.peek().trim(), password: password.peek(), role: role.peek() });
      close();
      toast(`${username.peek().trim().toLowerCase()} added`);
      await refresh();
    } catch (failure) {
      if (failure instanceof ValidationError) fields.set(failure.fields);
      else fields.set({ _form: message(failure) });
    } finally { busy.set(false); }
  };
  openModal({ title: 'Add user', icon: 'user-plus', tint: 'blue', narrow: true, onSubmit: () => void submit(() => {}),
    body: () => html`<div class="ctl-form">
      ${field('Username', 'ctl-n-user', input('ctl-n-user', username), () => '3 to 64 letters, numbers, dots, hyphens or underscores.', () => fields.get().username ?? null)}
      ${field('Role', 'ctl-n-role', Select({ id: 'ctl-n-role', value: () => role.get(), options: () => roles.map(item => [item.id, item.id]), onChange: value => role.set(value) }),
        () => roles.find(item => item.id === role.get())?.description ?? null, () => fields.get().role ?? null)}
      ${field('Password', 'ctl-n-pass', input('ctl-n-pass', password, 'password'), () => `${lengthHint(passwordMinimum(ctx, role.get()))}. Give it to them in person.`, () => fields.get().password ?? null)}
      ${when(() => fields.get()._form, () => Callout('err', 'info', () => fields.get()._form))}
      </div>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: () => busy.get() ? 'Adding…' : 'Add user', icon: 'user-plus', disabled: busy, onClick: () => void submit(close) } }),
  });
}

// ---------- roles ----------

export function RolesPage(ctx: ControlsContext): HTMLElement {
  const roles = () => ctx.state.get().roles;
  const refresh = async () => ctx.reload();
  const setControls = async (row: RoleRow, next: boolean) => {
    const apply = async () => {
      try { await ctx.api.changeRole(row.id, { opens_controls: next }); await refresh(); toast(next ? `${row.id} can open Controls` : `${row.id} can no longer open Controls`); }
      catch (failure) { toast(message(failure), { icon: 'info' }); }
    };
    if (!next) return void apply();
    ask('Let this role open Controls?', html`People with <strong>${row.id}</strong> will be able to change users, roles, permissions and security on this instance.`, 'Allow', apply);
  };
  const setWorkspaces = async (row: RoleRow, next: boolean) => {
    try { await ctx.api.changeRole(row.id, { customize_workspaces: next }); await refresh(); toast(next ? `${row.id} can customize workspaces` : `${row.id} can no longer customize workspaces`); }
    catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  const remove = (row: RoleRow) => {
    if (row.users) return ask('Role still in use', html`${row.users === 1 ? '1 user has' : `${row.users} users have`} the <strong>${row.id}</strong> role. Move them to another role first.`, 'OK', null);
    ask(`Delete ${row.id}?`, "Its permission overrides are removed too. This can't be undone.", 'Delete role', async () => {
      try { await ctx.api.deleteRole(row.id); await refresh(); toast('Role deleted'); }
      catch (failure) { toast(message(failure), { icon: 'info' }); }
    }, true);
  };
  return html`<div>
    ${Head('Roles', 'Roles decide what people can do. Framework roles come from Evlet. Custom roles start from a framework role and inherit its defaults.',
      isAdmin(ctx) ? Button({ label: 'New role', icon: 'plus', variant: 'primary', onClick: () => newRole(ctx) }) : null)}
    <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Role</th><th>Type</th><th>Based on</th><th>Signs in on</th><th class="r">Users</th><th>Opens Controls</th><th>Customize workspaces</th><th></th></tr></thead>
      <tbody>${() => roles().map(row => html`<tr><td><div>${RoleChip(row.id)}</div><span class="ctl-sub">${row.description}</span></td>
        <td>${row.kind === 'framework' ? html`<span class="ctl-line muted">${Icon('lock', { size: 'xs' })}Framework</span>` : row.app ? html`<span>From ${row.app}</span>` : html`<span>Custom</span>`}</td>
        <td>${row.base ? RoleChip(row.base) : html`<span class="muted">—</span>`}</td><td>${row.realm === 'portal' ? 'Portal' : 'Desk'}</td><td class="r num">${row.users}</td>
        <td><span class="ctl-line">${Switch({ on: row.opens_controls, ariaLabel: `Opens Controls for ${row.id}`, disabled: row.id === 'admin' || row.realm !== 'desk' || !isAdmin(ctx),
          onChange: next => void setControls(row, next) })}${row.id === 'admin' ? html`<span class="muted ctl-small">Always</span>` : null}</span></td>
        <td><span class="ctl-line">${Switch({ on: Boolean(row.customize_workspaces), ariaLabel: `Customize workspaces for ${row.id}`, disabled: row.id === 'admin' || row.realm !== 'desk' || !isAdmin(ctx),
          onChange: next => void setWorkspaces(row, next) })}${row.id === 'admin' ? html`<span class="muted ctl-small">Always</span>` : null}</span></td>
        <td class="r ctl-nowrap"><a class="ctl-link" href=${ctx.router.href('permissions', {}, { role: row.id })}>Permissions ${Icon('arrow-right', { size: 'xs' })}</a>
          ${row.kind === 'custom' && !row.declared && isAdmin(ctx) ? Button({ icon: 'trash-2', tip: `Delete ${row.id}`, size: 'small', variant: 'ghost', onClick: () => remove(row) }) : null}</td></tr>`)}</tbody></table></div>
    ${Callout('info', 'info', html`Opening Controls is a separate right from any model permission. It never applies to portal roles, and admin always has it so the instance can't be locked out. Only admins change who opens Controls, manage roles and change admin accounts. From the server shell, <code>evlet controls grant &lt;instance&gt; &lt;user&gt;</code> restores access.`)}
  </div>` as HTMLElement;
}

function newRole(ctx: ControlsContext) {
  const id = cell(''), base = cell('editor'), realm = cell<'desk' | 'portal'>('desk'), description = cell(''), error = cell(''), busy = cell(false);
  const taken = () => ctx.state.peek().roles.some(role => role.id === id.peek().trim()) || ['controls', 'public'].includes(id.peek().trim());
  const create = async (close: () => void) => {
    const value = id.peek().trim();
    if (!ROLE_ID.test(value)) return error.set('Use 2 to 31 lowercase letters, digits or underscores, starting with a letter.');
    if (taken()) return error.set('That name is taken.');
    busy.set(true);
    try {
      await ctx.api.createRole({ id: value, base: base.peek(), realm: realm.peek(), description: description.peek() });
      close();
      await ctx.reload();
      toast(`Role ${value} created`);
    } catch (failure) {
      error.set(failure instanceof ValidationError ? Object.values(failure.fields).join(' ') : message(failure));
    } finally { busy.set(false); }
  };
  openModal({ title: 'New role', icon: 'user-cog', tint: 'blue', narrow: true,
    body: () => html`<div class="ctl-form">
      ${field('Name', 'ctl-r-id', html`<input class="input mono" id="ctl-r-id" placeholder="warehouse" autocomplete="off" prop:value=${id}
        on:input=${(event: Event) => { id.set((event.target as HTMLInputElement).value); error.set(''); }}>`, () => 'Lowercase letters, digits and underscores.', () => error.get() || null)}
      ${field('Signs in on', 'ctl-r-realm', Select({ id: 'ctl-r-realm', value: () => realm.get(), options: () => [['desk', 'Desk'], ['portal', 'Portal']],
        onChange: value => { realm.set(value as 'desk' | 'portal'); base.set(value === 'portal' ? 'portal' : 'editor'); } }),
        () => realm.get() === 'portal' ? 'Portal roles sign in on portal hosts and never open Controls.' : null)}
      ${() => field('Start from', 'ctl-r-base', Select({ id: 'ctl-r-base', value: () => base.get(), disabled: realm.get() === 'portal',
        options: () => realm.get() === 'portal' ? [['portal', 'portal']] : [['editor', 'editor'], ['viewer', 'viewer']], onChange: value => base.set(value) }))}
      ${field("What it's for", 'ctl-r-desc', html`<input class="input" id="ctl-r-desc" placeholder="Warehouse team. Stock moves only." prop:value=${description}
        on:input=${(event: Event) => description.set((event.target as HTMLInputElement).value)}>`)}
      <p class="muted ctl-small">${() => `The role gets ${base.get()}'s defaults on every model. Narrow it in Permissions.`}</p></div>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: () => busy.get() ? 'Creating…' : 'Create role', disabled: busy, onClick: () => void create(close) } }),
  });
}
