// Permissions and Security: overrides. Each change is a draft until Save; an override can only make
// a rule stricter than the app files, and the server checks every rule again.

import { html, when, type Child } from '../dom';
import { cell, derive } from '../reactive';
import { Button, Checkbox, Icon, IconButton } from '../ui/base';
import { openDrawer, toast } from '../ui/layers';
import { Callout, Head, type ControlsContext } from './app';
import { OPS, type ModelInfo, type PermValue } from './client';
import { idOf, SETTING_LABELS } from './drafts';
import { RoleChip } from './people';

const ORDER = ['read', 'create', 'update', 'delete', 'void', 'revise'];
const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** Keep a rule consistent the way the server expects it: write needs read, no access clears fields and rows. */
export function normalize(value: PermValue, file: PermValue): PermValue {
  let ops = ORDER.filter(op => value.ops.includes(op));
  if (ops.length && !ops.includes('read')) ops = ['read', ...ops];
  if (!ops.includes('create')) ops = ops.filter(op => op !== 'revise');
  if (!ops.length) return { ops: [], read: null, write: null, scope: null };
  return { ops, read: value.read, write: ops.some(op => op !== 'read') ? value.write : null, scope: file.scope ?? value.scope };
}

export function toggleOp(value: PermValue, file: PermValue, op: string): PermValue {
  let ops = new Set(value.ops);
  if (ops.has(op)) { ops.delete(op); if (op === 'read') ops = new Set(); }
  else { ops.add(op); ops.add('read'); }
  return normalize({ ...value, ops: [...ops], read: ops.size ? value.read : null }, file);
}

const SOURCE: Record<string, [string, string]> = { ov: ['s-ov', 'Override'], app: ['s-app', 'App rule'], framework: ['s-file', 'Framework default'] };
const Source = (kind: string) => html`<span class=${`ctl-src ${SOURCE[kind][0]}`}><i></i>${SOURCE[kind][1]}</span>`;

function fieldSummary(model: ModelInfo, value: PermValue) {
  if (!value.ops.length) return '—';
  const all = model.fields.length, read = value.read ? value.read.length : all, write = value.write ? value.write.length : all;
  const writes = value.ops.some(op => op !== 'read');
  if (read === all && (!writes || write === all)) return 'All fields';
  return writes ? `${read} read · ${write} write` : `${read} of ${all}`;
}

// ---------- permissions ----------

export function PermissionsPage(ctx: ControlsContext): HTMLElement {
  const { drafts, state } = ctx;
  const roles = () => state.get().roles;
  const asked = ctx.router.route.peek()?.query.role;
  const role = cell(roles().some(item => item.id === asked) ? asked! : roles().find(item => item.id !== 'admin')?.id ?? 'admin', { name: 'permissions.role' });
  const query = cell('');
  const pick = (id: string) => { role.set(id); void ctx.router.go('permissions', {}, { role: id }, { replace: true }); };
  const groups = derive(() => {
    const models = state.get().models.filter(model => !query.get() || model.name.toLowerCase().includes(query.get().toLowerCase()));
    const apps = [...new Set(models.map(model => model.app ?? ''))];
    return apps.map(app => [app, models.filter(model => (model.app ?? '') === app)] as const);
  });
  const current = () => roles().find(item => item.id === role.get());
  const locked = () => role.get() === 'admin';

  const row = (model: ModelInfo) => {
    const id = idOf('perm', `${role.peek()}|${model.name}`);
    const item = () => drafts.saved.get().get(id);
    const value = () => drafts.value<PermValue>(id);
    const file = () => item()!.file as PermValue;
    const source = () => drafts.overridden(id) ? 'ov' : item()?.source ?? 'framework';
    if (!drafts.saved.peek().has(id)) return null;
    return html`<tr class:ctl-ovrow=${() => drafts.overridden(id)}>
      <td><strong>${model.name}</strong>${when(() => drafts.dirty(id), () => html` <span class="ctl-tag">Unsaved</span>`)}
        ${when(() => drafts.errors.get().has(id), () => html`<span class="ctl-err">${() => drafts.errors.get().get(id)}</span>`)}</td>
      ${OPS.map(op => html`<td class="c">${Checkbox({ checked: () => value().ops.includes(op), ariaLabel: `${cap(op)} ${model.name}`,
        disabled: () => locked() || !file().ops.includes(op), onChange: () => drafts.set(id, toggleOp(value(), file(), op)) })}</td>`)}
      <td><button type="button" class="ctl-cell" disabled=${() => locked() || !value().ops.length} on:click=${() => openRule(ctx, role.peek(), model)}>
        ${() => fieldSummary(model, value())}${() => !locked() && value().ops.length ? Icon('chevron-right', { size: 'xs' }) : null}</button></td>
      <td><button type="button" class="ctl-cell" disabled=${() => locked() || !value().ops.length} on:click=${() => openRule(ctx, role.peek(), model)}>
        ${() => value().ops.length ? state.get().scopes[value().scope ?? ''] ?? value().scope : '—'}${() => !locked() && value().ops.length ? Icon('chevron-right', { size: 'xs' }) : null}</button></td>
      <td>${() => Source(source())}</td>
      <td class="r">${() => drafts.overridden(id) && !locked() ? IconButton({ icon: 'rotate-ccw', tip: item()?.source === 'app' ? 'Reset to app rule' : 'Reset to framework default', size: 'small', onClick: () => drafts.reset(id) }) : null}</td></tr>`;
  };

  return html`<div>
    ${Head('Permissions', "Defaults come from each app's permissions.py. Changes you make are saved to this instance as overrides and checked on the server for every request.")}
    <div class="toolbar"><div class="seg ctl-roles" role="tablist" aria-label="Role">${() => roles().map(item => html`<button type="button" role="tab"
      class:on=${() => role.get() === item.id} aria-selected=${() => String(role.get() === item.id)} on:click=${() => pick(item.id)}>${item.id}</button>`)}</div></div>
    <div class="toolbar"><label class="ctl-search">${Icon('search', { size: 'sm' })}<input class="input" placeholder="Filter models…" aria-label="Filter models"
      prop:value=${query} on:input=${(event: Event) => query.set((event.target as HTMLInputElement).value)}></label><span class="grow"></span>
      <div class="ctl-legend">${() => current()?.base ? html`<span class="ctl-line">Inherits from ${RoleChip(current()!.base!)}</span>` : null}
        ${Source('framework')}${Source('app')}${Source('ov')}</div></div>
    ${when(locked, () => Callout('info', 'lock', 'Admin always has full access. This keeps at least one role able to fix the instance.'))}
    <div class="table-wrap"><table class="dt ctl-table ctl-perms"><thead><tr><th>Model</th>${OPS.map(op => html`<th class="c">${cap(op)}</th>`)}<th>Fields</th><th>Rows</th><th>Source</th><th></th></tr></thead>
      <tbody>${() => { role.get(); return groups.get().length ? groups.get().map(([app, models]) => [
        html`<tr class="ctl-group"><td colspan="9">${app ? `${app} · permissions.py` : 'Models'}</td></tr>`, models.map(row)])
        : html`<tr><td colspan="9"><div class="ctl-empty">No models match.</div></td></tr>`; }}</tbody></table></div>
    <p class="muted ctl-small">Create, update and delete need read access. Turning off read turns them off too.</p>
  </div>` as HTMLElement;
}

function openRule(ctx: ControlsContext, role: string, model: ModelInfo) {
  const { drafts, state } = ctx;
  const id = idOf('perm', `${role}|${model.name}`);
  const value = () => drafts.value<PermValue>(id);
  const file = drafts.saved.peek().get(id)!.file as PermValue;
  const names = model.fields.map(item => item.name);
  const fileRead = new Set(file.read ?? names);
  const fileWrite = new Set((file.write ?? names).filter(name => fileRead.has(name)));
  const writes = () => value().ops.some(op => op !== 'read');
  const reads = (name: string) => !value().read || value().read!.includes(name);
  const writesField = (name: string) => writes() && reads(name) && (!value().write || value().write!.includes(name));
  const ordered = (list: string[]) => list.length === names.length ? null : names.filter(name => list.includes(name));
  const toggleField = (name: string, kind: 'read' | 'write') => {
    let read = value().read ?? [...names], write = value().write ?? [...names];
    if (kind === 'read') {
      if (read.includes(name)) { read = read.filter(item => item !== name); write = write.filter(item => item !== name); }
      else read = [...read, name];
    } else write = write.includes(name) ? write.filter(item => item !== name) : [...write, name];
    if (!read.length) { toast('Keep at least one readable field, or turn off read instead', { icon: 'info' }); return; }
    drafts.set(id, normalize({ ...value(), read: ordered(read), write: ordered(write.filter(item => read.includes(item))) }, file));
  };
  const scopes = file.scope ? [file.scope] : model.scopes;
  const scopeOption = (scope: string | null) => {
    const unavailable = scope === 'own_customer' && !state.peek().links;
    return html`<label class="ctl-radio" class:off=${unavailable}><input type="radio" name="ctl-scope" checked=${() => value().scope === scope}
      disabled=${unavailable || Boolean(file.scope)} on:change=${() => drafts.set(id, normalize({ ...value(), scope }, file))}>
      <span><strong>${state.peek().scopes[scope ?? ''] ?? scope}</strong>${scope ? html`<small class="mono">${scope}</small>` : null}
      ${unavailable ? html`<small>Needs portal customer links, which arrive with portal hosts.</small>` : null}</span></label>`;
  };
  openDrawer({ title: `${model.name} for ${role}`, icon: 'list-checks', tint: 'blue', className: 'ctl-drawer',
    body: () => html`<div class="ctl-form">
      <section class="ctl-section"><h3>Can</h3><div class="ctl-chips">${OPS.map(op => html`<button type="button" class="ctl-chip" class:on=${() => value().ops.includes(op)}
        aria-pressed=${() => String(value().ops.includes(op))} disabled=${!file.ops.includes(op)}
        on:click=${() => drafts.set(id, toggleOp(value(), file, op))}>${() => value().ops.includes(op) ? Icon('check', { size: 'xs' }) : null}${cap(op)}</button>`)}</div></section>
      ${when(() => value().ops.length, () => html`<section class="ctl-section"><h3>Which records</h3>${scopes.map(scopeOption)}
        ${file.scope ? html`<p class="muted ctl-small">This filter comes from the app and can't be changed here.</p>` : null}</section>
      <section class="ctl-section"><div class="ctl-line"><h3 class="grow">Fields</h3>${Button({ label: 'All fields', size: 'small', variant: 'ghost',
        onClick: () => drafts.set(id, normalize({ ...value(), read: file.read, write: file.write }, file)) })}</div>
        <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Field</th><th class="c">Read</th><th class="c">Write</th></tr></thead><tbody>
        ${model.fields.map(item => html`<tr><td><span class="mono">${item.name}</span><span class="ctl-sub">${item.label}</span></td>
          <td class="c">${Checkbox({ checked: () => reads(item.name), ariaLabel: `Read ${item.name}`, disabled: !fileRead.has(item.name), onChange: () => toggleField(item.name, 'read') })}</td>
          <td class="c">${item.read_only ? html`<span class="muted" title="Read only field">${Icon('lock', { size: 'xs' })}</span>`
            : Checkbox({ checked: () => writesField(item.name), ariaLabel: `Write ${item.name}`, disabled: () => !writes() || !reads(item.name) || !fileWrite.has(item.name), onChange: () => toggleField(item.name, 'write') })}</td></tr>`)}
        </tbody></table></div></section>`)}
      <section class="ctl-section"><h3>${drafts.saved.peek().get(id)!.source === 'app' ? 'App rule' : 'Framework default'}</h3>
        <p class="muted ctl-small">${describeFile(file, state.peek().scopes, model)}</p></section></div>`,
    footer: close => html`${() => drafts.overridden(id) ? Button({ label: 'Reset', icon: 'rotate-ccw', onClick: () => drafts.reset(id) }) : null}<span class="grow"></span>
      ${Button({ label: 'Done', variant: 'primary', onClick: close })}`,
  });
}

function describeFile(file: PermValue, scopes: Record<string, string>, model: ModelInfo) {
  if (!file.ops.length) return 'No access.';
  const fields = file.read ? `${file.read.length} of ${model.fields.length} fields` : 'all fields';
  return `${file.ops.map(cap).join(', ')}, ${fields}, ${(scopes[file.scope ?? ''] ?? file.scope ?? '').toLowerCase()}.`;
}

// ---------- security ----------

export function SecurityPage(ctx: ControlsContext): HTMLElement {
  const { drafts, state } = ctx;
  const id = (key: string) => idOf('setting', key);
  const get = <T>(key: string) => drafts.value<T>(id(key));
  const file = <T>(key: string) => drafts.saved.peek().get(id(key))!.file as T;
  const mark = (key: string) => () => drafts.overridden(id(key)) ? html`<span class="ctl-tag">Changed</span>${IconButton({ icon: 'rotate-ccw', size: 'small',
    tip: 'Reset to the file value', onClick: () => drafts.reset(id(key)) })}` : null;
  const desk = () => state.get().roles.filter(role => role.realm === 'desk');
  const row = (key: string, title: string, text: Child, control: Child) => html`<div class="ctl-setrow"><div class="grow"><strong>${title} ${mark(key)}</strong><p>${text}</p>
    ${when(() => drafts.errors.get().has(id(key)), () => html`<span class="ctl-err">${() => drafts.errors.get().get(id(key))}</span>`)}</div><div class="ctl-setctl">${control}</div></div>`;
  const choose = (key: string, unit: string, options: number[]) => html`<select class="input ctl-select" aria-label=${SETTING_LABELS[key]}
    on:change=${(event: Event) => drafts.set(id(key), Number((event.target as HTMLSelectElement).value))}>
    ${options.filter(option => option <= file<number>(key)).map(option => html`<option value=${String(option)} selected=${() => get<number>(key) === option}>${option} ${unit}</option>`)}</select>`;
  const minimum = (event: Event) => {
    const input = event.target as HTMLInputElement, low = file<number>('security.password_min');
    let value = Number.parseInt(input.value, 10);
    if (Number.isNaN(value)) value = low;
    if (value < low) { value = low; toast(`Minimum password length can't go below ${low}`, { icon: 'info' }); }
    if (value > 64) { value = 64; toast("Minimum password length can't go above 64", { icon: 'info' }); }
    input.value = String(value);
    drafts.set(id('security.password_min'), value);
  };
  const ipError = cell('');
  const networks = (event: Event) => {
    const lines = (event.target as HTMLTextAreaElement).value.split('\n').map(line => line.trim()).filter(Boolean);
    const bad = lines.find(line => !/^[0-9A-Fa-f:.]+(\/\d{1,3})?$/.test(line) || !/[.:]/.test(line));
    ipError.set(bad ? `${bad} isn't an IP address or range.` : '');
    if (!bad) drafts.set(id('security.controls_ips'), lines);
  };
  const toggleTwoStep = (role: string) => {
    const now = get<string[]>('security.two_step_roles');
    drafts.set(id('security.two_step_roles'), desk().map(item => item.id).filter(item => item === role ? !now.includes(role) : now.includes(item)));
  };
  return html`<div>
    ${Head('Security', 'Sign-in rules for this instance. You can make a rule stricter than the framework default, never looser.')}
    <div class="ctl-stack">
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Sign-in</h2></div></div>
        ${row('security.password_min', 'Minimum password length', () => `Evlet requires at least ${file<number>('security.password_min')} characters. You can raise it.`,
          html`<span class="ctl-addon"><input class="input num" type="number" min=${String(file<number>('security.password_min'))} max="64" aria-label="Minimum password length"
            prop:value=${() => String(get<number>('security.password_min'))} on:change=${minimum}><span>chars</span></span>`)}
        ${row('security.session_hours', 'Session length', () => `People sign in again after this long. Default is ${file<number>('security.session_hours')} hours.`, choose('security.session_hours', 'hours', [1, 4, 8, 12]))}
        ${row('security.lockout', 'Failed sign-ins before lockout', 'Sign-in is blocked for 15 minutes after this many wrong passwords from one network.', choose('security.lockout', 'attempts', [3, 5, 10]))}
      </section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Two-step sign-in ${mark('security.two_step_roles')}</h2>
        <p>People with these roles must set up an authenticator app before they can sign in.</p></div></div>
        <div class="ctl-pad"><div class="ctl-chips">${() => desk().map(role => html`<button type="button" class="ctl-chip" class:on=${() => get<string[]>('security.two_step_roles').includes(role.id)}
          aria-pressed=${() => String(get<string[]>('security.two_step_roles').includes(role.id))} disabled=${role.id === 'admin'} title=${role.id === 'admin' ? 'Always required for admin' : null}
          on:click=${() => toggleTwoStep(role.id)}>${() => get<string[]>('security.two_step_roles').includes(role.id) ? Icon('check', { size: 'xs' }) : null}${role.id}${role.id === 'admin' ? Icon('lock', { size: 'xs' }) : null}</button>`)}</div>
          ${when(() => !state.get().two_step, () => Callout('warn', 'info', 'Two-step sign-in arrives with personal settings. Until then this list is saved, but nobody is asked for a code yet.'))}
          ${when(() => drafts.errors.get().has(id('security.two_step_roles')), () => html`<span class="ctl-err">${() => drafts.errors.get().get(id('security.two_step_roles'))}</span>`)}</div>
      </section>
      <section class="ctl-card"><div class="ctl-card-head"><div><h2>Controls access</h2><p>Where this page can be opened from</p></div></div>
        <div class="ctl-setrow"><div class="grow"><strong>Path</strong><p>Fixed by the framework. Apps and routes.py can't take it.</p></div><div class="ctl-setctl"><code>/controls</code>${Icon('lock', { size: 'sm' })}</div></div>
        <div class="ctl-setrow"><div class="grow"><strong>Host</strong><p>Only the primary host serves Controls. Any other host gets a 404.</p></div><div class="ctl-setctl"><span>${() => state.get().instance.host}</span>${Icon('lock', { size: 'sm' })}</div></div>
        <div class="ctl-setrow"><div class="grow"><strong>Who can open it</strong><p>Roles with "Opens Controls" turned on. Anyone else gets a 404.</p></div>
          <div class="ctl-setctl"><span class="ctl-chips">${() => state.get().roles.filter(role => role.opens_controls).map(role => RoleChip(role.id))}</span>
          <a class="ctl-link" href=${ctx.router.href('roles', {})}>Roles ${Icon('arrow-right', { size: 'xs' })}</a></div></div>
        ${row('security.controls_ips', 'Allowed networks', () => state.get().controls.allow_ips.length
          ? `routes.py already limits Controls to ${state.get().controls.allow_ips.join(', ')}. This list narrows it further. One IP or range per line.`
          : 'Leave empty to allow any address. One IP or range per line, for example 10.0.0.0/8.',
          html`<div class="ctl-ips"><textarea class="input mono" rows="3" placeholder="Any address" aria-label="Allowed networks"
            prop:value=${() => get<string[]>('security.controls_ips').join('\n')} on:change=${networks}></textarea>
            ${when(ipError, () => html`<span class="ctl-err">${ipError}</span>`)}</div>`)}
      </section>
    </div></div>` as HTMLElement;
}
