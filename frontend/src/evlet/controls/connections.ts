// Connectors and Email: instance data, so every change saves at once and writes an audit row.
// Secrets are write only: the server says whether one is saved, never what it is.

import { html, when } from '../dom';
import { ValidationError } from '../api';
import { cell, type Cell } from '../reactive';
import { Button, Icon, StatusPill, Switch } from '../ui/base';
import { DialogFooter, openDrawer, toast } from '../ui/layers';
import { Callout, Head, plural, stamp, type ControlsContext } from './app';
import type { ConnectorBody, ConnectorField, ConnectorInfo, ConnectorRow, TestResult } from './client';
import { ask, field, message, Select } from './people';

type Listing = { connectors: ConnectorInfo[]; items: ConnectorRow[] };
const ADDRESS = /^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;".]{2,}$/;
const ACCOUNT = /^[a-z0-9][a-z0-9_-]{0,39}$/;
const SECURITY: Record<string, string> = { starttls: 'STARTTLS', ssl: 'SSL/TLS', none: 'None' };

function useListing(ctx: ControlsContext) {
  const listing = cell<Listing | null>(null);
  const load = async () => {
    try { listing.set(await ctx.api.connectors()); } catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  void load();
  return { listing, load };
}

const statusPill = (row: ConnectorRow | undefined) => !row ? StatusPill({ label: 'Not set up', tint: 'slate' })
  : row.status === 'error' ? StatusPill({ label: 'Not connected', tint: 'rose' }) : StatusPill({ label: 'Connected', tint: 'green' });

/** One field of a connector form. A saved secret shows as saved until Replace; a new value is never shown back. */
function FieldControl(ctx: ControlsContext, item: ConnectorField, values: Cell<Record<string, any>>, secrets: Cell<Record<string, string>>,
  saved: boolean, errors: Cell<Record<string, string>>): Node {
  const id = `ctl-cf-${item.name}`;
  const set = (value: unknown) => { values.set({ ...values.peek(), [item.name]: value }); errors.set({}); };
  const error = () => errors.get()[item.name] ?? null;
  const hint = () => item.help || null;
  if (item.type === 'secret') {
    const replacing = cell(!saved);
    const input = () => html`<input class="input mono" id=${id} type="password" autocomplete="new-password" placeholder="Paste a new value"
      on:input=${(event: Event) => { secrets.set({ ...secrets.peek(), [item.name]: (event.target as HTMLInputElement).value }); errors.set({}); }}>`;
    return field(item.label, id, html`<div>${() => replacing.get() ? input()
      : html`<div class="ctl-line"><span class="ctl-secret grow">•••••••••••• saved</span>${Button({ label: 'Replace', size: 'small', onClick: () => replacing.set(true) })}</div>`}</div>` as HTMLElement,
    () => "Stored encrypted. It can't be shown again.", error);
  }
  if (item.type === 'toggle') {
    return html`<div class="ctl-field"><span class="lbl">${item.label}</span>${Switch({ on: () => Boolean(values.get()[item.name]), label: item.label, onChange: next => set(next) })}</div>`;
  }
  if (item.type === 'select' || item.type === 'role') {
    const options = () => item.type === 'role'
      ? ctx.state.get().roles.filter(role => role.realm === 'desk' && role.id !== 'admin').map(role => [role.id, role.id] as [string, string])
      : item.options;
    return field(item.label, id, Select({ id, value: () => String(values.get()[item.name] ?? ''), options, onChange: value => set(value) }), hint, error);
  }
  const number = item.type === 'number';
  return field(item.label, id, html`<input class=${number ? 'input num' : 'input mono'} id=${id} type=${number ? 'number' : 'text'} autocomplete="off"
    prop:value=${() => values.get()[item.name] ?? ''}
    on:input=${(event: Event) => { const raw = (event.target as HTMLInputElement).value; set(number ? (raw === '' ? null : Number(raw)) : raw); }}>`, hint, error);
}

const TestNote = (result: Cell<TestResult | 'running' | null>) => () => {
  const value = result.get();
  if (!value) return null;
  if (value === 'running') return Callout('info', 'clock', 'Testing the connection…');
  return value.ok ? Callout('info', 'check', value.message ?? 'Connected.') : Callout('err', 'info', value.error ?? 'The test failed.');
};

/** A form over a connector's fields, shared by the connector drawer and the email account drawer. */
function form(ctx: ControlsContext, info: ConnectorInfo, row: ConnectorRow | undefined, skip: string[] = []) {
  const values = cell<Record<string, any>>(Object.fromEntries(info.fields.filter(item => item.type !== 'secret')
    .map(item => [item.name, row ? row.settings[item.name] ?? null : item.default])));
  const secrets = cell<Record<string, string>>({}), errors = cell<Record<string, string>>({});
  const result = cell<TestResult | 'running' | null>(null), busy = cell(false);
  const body = (): ConnectorBody => ({ settings: values.peek(),
    secrets: Object.fromEntries(Object.entries(secrets.peek()).filter(([, value]) => value.trim())) });
  const fields = info.fields.filter(item => !skip.includes(item.name))
    .map(item => FieldControl(ctx, item, values, secrets, Boolean(row?.secrets[item.name]?.saved), errors));
  const failed = (failure: unknown) => {
    if (failure instanceof ValidationError) errors.set(failure.fields);
    else errors.set({ _form: message(failure) });
  };
  const test = async (id: string, extra: Record<string, unknown> = {}) => {
    result.set('running');
    try { result.set(await ctx.api.testConnector(id, { ...body(), ...extra })); }
    catch (failure) { result.set(null); failed(failure); }
  };
  return { values, secrets, errors, result, busy, body, fields, failed, test };
}

// ---------- connectors ----------

export function ConnectorsPage(ctx: ControlsContext): HTMLElement {
  const { listing, load } = useListing(ctx);
  const tab = cell('all');
  const declared = () => listing.get()?.connectors ?? [];
  const categories = () => ['all', ...new Set(declared().map(item => item.category))];
  const card = (info: ConnectorInfo) => {
    const rows = () => listing.get()!.items.filter(row => row.connector === info.id);
    const row = () => info.accounts ? undefined : rows()[0];
    const set = () => info.accounts ? rows().length > 0 : Boolean(row());
    return html`<div class="ctl-card ctl-conn"><div class="ctl-conn-top"><span class="ctl-conn-logo">${info.title.slice(0, 2).toUpperCase()}</span>
      <span class="grow"><h3>${info.title}</h3><span class="ctl-sub">${info.category}</span></span>
      ${() => info.accounts ? StatusPill({ label: plural(rows().length, 'account'), tint: rows().length ? 'green' : 'slate' }) : statusPill(row())}</div>
      <p>${info.description}</p>
      ${() => row()?.status === 'error' ? Callout('err', 'info', 'The last test failed. Check the details and test again.') : null}
      <div class="ctl-conn-foot"><span class="ctl-badge">${info.app === 'evlet' ? 'Evlet' : info.app}</span><span class="grow"></span>
        ${() => info.accounts ? Button({ label: 'Open Email', size: 'small', onClick: () => void ctx.router.go('email', {}) })
          : Button({ label: set() ? 'Configure' : 'Set up', size: 'small', variant: set() ? 'default' : 'primary', onClick: () => openConnector(ctx, info, row(), load) })}</div></div>`;
  };
  return html`<div>
    ${Head('Connectors', 'Connections to other services. Each installed app can add its own; they appear here automatically.')}
    ${() => listing.get() === null ? html`<div class="ctl-empty">Loading connectors…</div>` : [
      html`<div class="ctl-tabs" role="tablist">${categories().map(id => html`<button type="button" role="tab" class:on=${() => tab.get() === id}
        aria-selected=${() => String(tab.get() === id)} on:click=${() => tab.set(id)}>${id === 'all' ? 'All' : id}
        <span class="tc">${id === 'all' ? declared().length : declared().filter(item => item.category === id).length}</span></button>`)}</div>`,
      html`<div class="ctl-grid g3">${() => declared().filter(item => tab.get() === 'all' || item.category === tab.get()).map(card)}</div>`]}
  </div>` as HTMLElement;
}

function openConnector(ctx: ControlsContext, info: ConnectorInfo, row: ConnectorRow | undefined, reload: () => Promise<void>) {
  const state = form(ctx, info, row);
  const save = async (close: () => void) => {
    state.busy.set(true);
    try {
      await ctx.api.saveConnector(info.id, state.body());
      close();
      toast(`${info.title} saved`);
      await reload();
    } catch (failure) { state.failed(failure); } finally { state.busy.set(false); }
  };
  const disconnect = (close: () => void) => ask(`Disconnect ${info.title}?`,
    'Saved credentials are deleted. Anything that uses this connection stops working until it is set up again.', 'Disconnect', async () => {
      try { await ctx.api.removeConnector(info.id); close(); toast('Disconnected'); await reload(); }
      catch (failure) { toast(message(failure), { icon: 'info' }); }
    }, true);
  openDrawer({ title: info.title, icon: 'link', tint: 'blue', className: 'ctl-drawer',
    body: () => html`<div class="ctl-form">
      <p class="muted ctl-small">${info.category} · added by ${info.app === 'evlet' ? 'Evlet' : info.app}</p>
      ${statusPill(row)}
      ${state.fields}
      ${when(() => state.errors.get()._form, () => Callout('err', 'info', () => state.errors.get()._form))}
      ${TestNote(state.result)}</div>`,
    footer: close => html`${row ? Button({ label: 'Disconnect', variant: 'soft-danger', onClick: () => disconnect(close) }) : null}<span class="grow"></span>
      ${info.testable ? Button({ label: 'Test', icon: 'zap', onClick: () => void state.test(info.id) }) : null}
      ${DialogFooter({ cancel: close, submit: { label: () => state.busy.get() ? 'Saving…' : 'Save', disabled: state.busy, onClick: () => void save(close) } })}`,
  });
}

// ---------- email ----------

export function EmailPage(ctx: ControlsContext): HTMLElement {
  const { listing, load } = useListing(ctx);
  const accounts = () => (listing.get()?.items ?? []).filter(row => row.account && (row.connector === 'smtp' || row.connector === 'imap'));
  const info = (id: string) => listing.peek()!.connectors.find(item => item.id === id)!;
  const makeDefault = async (row: ConnectorRow) => {
    try { await ctx.api.makeDefault(row.id); toast(`${row.settings.address} is now the default`); await load(); }
    catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  const failing = () => accounts().filter(row => row.status === 'error');
  return html`<div>
    ${Head('Email', 'Mailboxes this instance sends from and reads. Passwords are stored encrypted and never shown again.',
      Button({ label: 'Add account', icon: 'plus', variant: 'primary', disabled: () => listing.get() === null, onClick: () => openAccount(ctx, info, undefined, load) }))}
    ${() => failing().length ? Callout('err', 'info', failing().map(row => `${row.settings.address}: the last test failed.`).join(' ')) : null}
    <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Account</th><th>Direction</th><th>Server</th><th>Status</th><th class="c">Default</th></tr></thead>
      <tbody>${() => accounts().length ? accounts().map(row => html`<tr class="ctl-click" tabindex="0" on:click=${() => openAccount(ctx, info, row, load)}
        on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') openAccount(ctx, info, row, load); }}>
        <td><strong>${row.account}</strong><span class="ctl-sub">${row.settings.address}</span></td>
        <td>${row.connector === 'smtp' ? StatusPill({ label: 'Sends', tint: 'blue' }) : StatusPill({ label: 'Receives', tint: 'teal' })}</td>
        <td class="mono muted">${row.settings.host}:${row.settings.port}<span class="ctl-sub">${SECURITY[row.settings.security] ?? ''}</span></td>
        <td>${statusPill(row)}<span class="ctl-sub">${row.changed_at ? `Changed ${stamp(row.changed_at)}` : ''}</span></td>
        <td class="c">${row.connector !== 'smtp' ? null : row.default ? html`<span class="ctl-default" aria-label="Default">${Icon('check', { size: 'sm' })}</span>`
          : html`<button type="button" class="ctl-link" on:click=${(event: MouseEvent) => { event.stopPropagation(); void makeDefault(row); }}>Make default</button>`}</td></tr>`)
        : html`<tr><td colspan="5"><div class="ctl-empty">${() => listing.get() === null ? 'Loading accounts…' : 'No email accounts yet.'}</div></td></tr>`}</tbody></table></div>
    <p class="muted ctl-small ctl-after">Apps pick an account by name. If an app doesn't name one, the default outgoing account is used.</p>
  </div>` as HTMLElement;
}

function openAccount(ctx: ControlsContext, info: (id: string) => ConnectorInfo, row: ConnectorRow | undefined, reload: () => Promise<void>) {
  const direction = cell(row?.connector ?? 'smtp'), name = cell(row?.account ?? ''), to = cell(''), sent = cell<TestResult | 'running' | null>(null);
  const forms: Record<string, ReturnType<typeof form>> = {
    smtp: form(ctx, info('smtp'), row?.connector === 'smtp' ? row : undefined), imap: form(ctx, info('imap'), row?.connector === 'imap' ? row : undefined) };
  const current = () => forms[direction.get()];
  const id = () => `${direction.peek()}:${name.peek().trim()}`;
  const check = () => {
    const state = current();
    if (!row && !ACCOUNT.test(name.peek().trim())) { state.errors.set({ name: 'Use lowercase letters, digits, - or _.' }); return false; }
    if (!ADDRESS.test(String(state.values.peek().address ?? ''))) { state.errors.set({ address: 'Enter a valid email address.' }); return false; }
    if (!row && !state.secrets.peek().password?.trim()) { state.errors.set({ password: 'Enter the mailbox password.' }); return false; }
    return true;
  };
  const save = async (close: () => void) => {
    const state = current();
    if (!check()) return;
    state.busy.set(true);
    try {
      await ctx.api.saveConnector(id(), state.body());
      close();
      toast('Account saved');
      await reload();
    } catch (failure) { state.failed(failure); } finally { state.busy.set(false); }
  };
  const sendTest = async () => {
    if (!ADDRESS.test(to.peek().trim())) { sent.set({ ok: false, error: 'Enter a valid email address.' }); return; }
    sent.set('running');
    try { sent.set(await ctx.api.testConnector(row!.id, { to: to.peek().trim() })); }
    catch (failure) { sent.set({ ok: false, error: message(failure) }); }
  };
  const remove = (close: () => void) => {
    ask(`Remove ${row!.settings.address}?`, 'Apps that use it will stop sending or reading through it.', 'Remove', async () => {
      try { await ctx.api.removeConnector(row!.id); close(); toast('Account removed'); await reload(); }
      catch (failure) { toast(message(failure), { icon: 'info' }); }
    }, true);
  };
  openDrawer({ title: row ? row.account! : 'New account', icon: 'mail', tint: 'blue', className: 'ctl-drawer',
    body: () => html`<div class="ctl-form">
      ${field('Direction', 'ctl-em-dir', Select({ id: 'ctl-em-dir', value: () => direction.get(), options: () => [['smtp', 'Outgoing'], ['imap', 'Incoming']],
        disabled: Boolean(row), onChange: value => direction.set(value) }))}
      ${field('Name', 'ctl-em-name', html`<input class="input mono" id="ctl-em-name" autocomplete="off" placeholder="notifications" disabled=${Boolean(row)}
        prop:value=${name} on:input=${(event: Event) => { name.set((event.target as HTMLInputElement).value); current().errors.set({}); }}>`,
        () => row ? 'Apps use this name to pick the account.' : 'Lowercase letters, digits, - or _. Apps use it to pick this account.',
        () => current().errors.get().name ?? null)}
      ${() => current().fields}
      ${() => when(() => current().errors.get()._form, () => Callout('err', 'info', () => current().errors.get()._form))}
      ${() => TestNote(current().result)}
      ${row?.connector === 'smtp' ? html`<section class="ctl-section"><h3>Send a test</h3><div class="ctl-line">
        <input class="input grow" type="email" aria-label="Send test to" placeholder="you@example.com" prop:value=${to}
          on:input=${(event: Event) => to.set((event.target as HTMLInputElement).value)}>
        ${Button({ label: 'Send', icon: 'send', onClick: () => void sendTest() })}</div>${TestNote(sent)}</section>` : null}
    </div>`,
    footer: close => html`${row ? Button({ label: 'Remove', variant: 'soft-danger', onClick: () => remove(close) }) : null}<span class="grow"></span>
      ${Button({ label: 'Test', icon: 'zap', onClick: () => { if (check()) void current().test(id()); } })}
      ${DialogFooter({ cancel: close, submit: { label: () => current().busy.get() ? 'Saving…' : row ? 'Save' : 'Add account', disabled: () => current().busy.get(), onClick: () => void save(close) } })}`,
  });
}
