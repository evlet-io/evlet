// /settings/email: a person's own mailbox. Admins can't read it; only emails linked to a record can
// be seen, in the record's Activity, by people who can see that record. The Settings shell passes its client:
//
//   MyEmailPage({ api: ctx.api });

import { html } from '../dom';
import { ApiClient, ValidationError, type ClientOptions } from '../api';
import { cell } from '../reactive';
import { Button, StatusPill, Switch } from '../ui/base';
import { mountToasts, toast } from '../ui/layers';
import { Callout, Head, stamp } from '../ui/settings-helpers';
import { ask, field, message, Select } from '../ui/settings-helpers';
import '../controls/controls.css';

export interface MyMailbox {
  provider: 'microsoft' | 'google' | 'imap'; title: string; address: string; status: 'connected' | 'error' | 'reconnect'; error: string | null;
  connected_at: number; synced_at: number | null; send_from: 'mine' | 'shared'; link_replies: boolean; only_crm: boolean;
}
export interface MyEmail { allowed: boolean; providers: { microsoft: boolean; google: boolean }; shared: string | null; mailbox: MyMailbox | null; queued?: boolean }
export interface MailboxDetails { address: string; imap_host: string; imap_port: number; smtp_host: string; smtp_port: number; password: string }

/** /api/_settings/me/mailbox: signed-in sessions only. */
export class MyEmailClient extends ApiClient {
  constructor(options: ClientOptions = {}) { super(options); }
  async load() { return (await this.request<MyEmail>('GET', '/api/_settings/me/mailbox')).data; }
  async connect(body: MailboxDetails) { return (await this.request<MyEmail>('POST', '/api/_settings/me/mailbox', { body })).data; }
  async signIn(provider: 'microsoft' | 'google') { return (await this.request<{ url: string }>('POST', '/api/_settings/me/mailbox/oauth', { body: { provider } })).data; }
  async change(body: Partial<Pick<MyMailbox, 'send_from' | 'link_replies' | 'only_crm'>>) { return (await this.request<MyEmail>('PATCH', '/api/_settings/me/mailbox', { body })).data; }
  async sync() { return (await this.request<MyEmail>('POST', '/api/_settings/me/mailbox/sync')).data; }
  async disconnect() { return (await this.request<MyEmail>('DELETE', '/api/_settings/me/mailbox')).data; }
}

// What /_/mail/callback reports back in ?mail=.
const RETURNED: Record<string, string> = {
  connected: 'Mailbox connected',
  denied: "You didn't approve access, so nothing was connected.",
  expired: 'That sign-in took too long or was already used. Try again.',
  failed: "Signing in didn't work. Try again, or ask an admin to check the sign-in settings.",
  mailbox: "Signed in, but your mailbox couldn't be opened. Check that IMAP is on for your account.",
  off: "Your workspace doesn't let people connect their own mailbox.",
  signin: 'Sign in to Evlet first, then connect again.',
};
const PROVIDERS: Array<['microsoft' | 'google' | 'imap', string, string, string]> = [
  ['microsoft', 'Microsoft 365', 'MS', 'Outlook and Exchange Online. Signs in with Microsoft.'],
  ['google', 'Google Workspace', 'G', 'Gmail for business. Signs in with Google.'],
  ['imap', 'Other mailbox', '@', 'Any provider, with IMAP and SMTP details.'],
];
const ADDRESS = /^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;".]{2,}$/;

/** "imap.example.com:993" as host and port, or null. */
export function server(value: string): [string, number] | null {
  const match = /^([A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\]):(\d{1,5})$/.exec(value.trim());
  return match && Number(match[2]) <= 65535 ? [match[1].replace(/^\[|\]$/g, ''), Number(match[2])] : null;
}

/** The mailbox routes over any client, such as the Settings shell's. */
const emailApi = (client: ApiClient) => client instanceof MyEmailClient ? client : {
  load: async () => (await client.request<MyEmail>('GET', '/api/_settings/me/mailbox')).data,
  connect: async (body: MailboxDetails) => (await client.request<MyEmail>('POST', '/api/_settings/me/mailbox', { body })).data,
  signIn: async (provider: 'microsoft' | 'google') => (await client.request<{ url: string }>('POST', '/api/_settings/me/mailbox/oauth', { body: { provider } })).data,
  change: async (body: Partial<Pick<MyMailbox, 'send_from' | 'link_replies' | 'only_crm'>>) => (await client.request<MyEmail>('PATCH', '/api/_settings/me/mailbox', { body })).data,
  sync: async () => (await client.request<MyEmail>('POST', '/api/_settings/me/mailbox/sync')).data,
  disconnect: async () => (await client.request<MyEmail>('DELETE', '/api/_settings/me/mailbox')).data,
};

export function MyEmailPage(options: { api?: ApiClient; go?: (url: string) => void }): HTMLElement {
  const api = emailApi(options.api ?? new MyEmailClient());
  const go = options.go ?? ((url: string) => window.location.assign(url));
  const data = cell<MyEmail | null>(null), form = cell(false), busy = cell(false), error = cell('');
  const draft = { address: cell(''), imap: cell(''), smtp: cell(''), password: cell('') };
  const run = async (step: () => Promise<MyEmail>, done?: string) => {
    try { data.set(await step()); if (done) toast(done); } catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  const returned = new URLSearchParams(window.location.search).get('mail');
  if (returned && RETURNED[returned]) {
    toast(RETURNED[returned], returned === 'connected' ? {} : { icon: 'info' });
    window.history.replaceState(null, '', window.location.pathname);
  }
  void run(() => api.load());
  mountToasts();
  const d = () => data.get()!;
  const mailbox = () => d().mailbox!;

  const choose = async (provider: 'microsoft' | 'google' | 'imap') => {
    if (provider === 'imap') { form.set(true); error.set(''); return; }
    try { go((await api.signIn(provider)).url); } catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  const connect = async () => {
    const address = draft.address.peek().trim(), imap = server(draft.imap.peek()), smtp = server(draft.smtp.peek());
    if (!ADDRESS.test(address)) return error.set('Enter a valid email address.');
    if (!imap || !smtp) return error.set('Enter both servers, with the port, like imap.example.com:993.');
    if (!draft.password.peek()) return error.set('Enter the password.');
    busy.set(true);
    try {
      data.set(await api.connect({ address, imap_host: imap[0], imap_port: imap[1], smtp_host: smtp[0], smtp_port: smtp[1], password: draft.password.peek() }));
      form.set(false); draft.password.set('');
      toast(`${address} connected`);
    } catch (failure) {
      error.set(failure instanceof ValidationError ? Object.values(failure.fields).join(' ') : message(failure));
      // The workspace may have turned personal mailboxes off meanwhile: reload, so the banner and buttons agree.
      if (!(failure instanceof ValidationError)) await api.load().then(next => data.set(next), () => undefined);
    } finally { busy.set(false); }
  };
  const disconnect = () => ask('Disconnect your mailbox?',
    `Emails you send from records go from ${d().shared ?? 'the shared account'} again. Emails already linked to records stay there.`, 'Disconnect',
    () => run(() => api.disconnect(), 'Mailbox disconnected'), true);
  const input = (id: string, value: typeof draft.address, type = 'text', placeholder = '') => html`<input class="input" id=${id} type=${type}
    autocomplete="off" placeholder=${placeholder} prop:value=${value} on:input=${(event: Event) => { value.set((event.target as HTMLInputElement).value); error.set(''); }}>` as HTMLElement;

  const policy = () => d().allowed ? Callout('info', 'lock', "Your workspace lets people connect their own mailbox. Admins can't read it. Only emails that are linked to a record can be seen, and only by people who can see that record.")
    : Callout('warn', 'lock', d().mailbox ? "Your admin has turned off personal mailboxes, so yours is paused. Nothing is read and emails go from the shared account."
      : "Your workspace doesn't let people connect their own mailbox. Ask an admin to turn it on in Controls, under Email.");
  const pill = () => mailbox().status === 'connected' ? StatusPill({ label: 'Working', tint: 'green' })
    : mailbox().status === 'error' ? StatusPill({ label: 'Not syncing', tint: 'rose' }) : StatusPill({ label: 'Connect again', tint: 'amber' });
  const row = (title: string, text: string | (() => string), control: unknown) => html`<div class="ctl-setrow"><div class="grow"><strong>${title}</strong>
    ${text ? html`<p>${text}</p>` : null}</div><div class="ctl-setctl">${control}</div></div>`;

  const connected = () => html`<section class="ctl-card"><div class="ctl-card-head"><div><h2>Connected mailbox</h2></div></div>
      ${row(mailbox().address, () => `${mailbox().title} · connected ${stamp(mailbox().connected_at)} · ${mailbox().synced_at ? `last synced ${stamp(mailbox().synced_at)}` : 'not synced yet'}`,
        html`${pill()}${mailbox().status !== 'reconnect' ? Button({ label: 'Sync now', icon: 'repeat', size: 'small', disabled: !d().allowed,
          onClick: () => void run(() => api.sync(), 'Syncing. New emails show on records in a minute.') }) : null}`)}
      ${mailbox().error ? html`<div class="ctl-pad">${Callout('err', 'info', mailbox().error)}</div>` : null}</section>
    <section class="ctl-card"><div class="ctl-card-head"><div><h2>How it's used</h2></div></div>
      ${row('Send emails from records as', '', Select({ id: 'my-mail-as', label: 'Send emails from records as', value: () => mailbox().send_from,
        options: () => [['mine', mailbox().address], ['shared', d().shared ? `${d().shared} (shared)` : 'The shared account']],
        onChange: value => void run(() => api.change({ send_from: value as 'mine' | 'shared' }), 'Saved') }))}
      ${row('Link replies to records', 'Replies and other emails with people on your records show on those records. When off, nothing is read from your mailbox.',
        Switch({ on: mailbox().link_replies, ariaLabel: 'Link replies to records', onChange: next => void run(() => api.change({ link_replies: next }), 'Saved') }))}
      ${row('Only read emails with customers and leads', mailbox().only_crm ? 'Other emails stay in your mailbox and are never copied here.'
        : 'Emails with anyone on a record you can see are copied to that record.',
        Switch({ on: mailbox().only_crm, ariaLabel: 'Only read emails with customers and leads', onChange: next => void run(() => api.change({ only_crm: next }), 'Saved') }))}</section>
    <div class="ctl-line"><span class="grow"></span>${Button({ label: 'Disconnect mailbox', variant: 'soft-danger', onClick: disconnect })}</div>`;

  const details = () => html`<section class="ctl-card pad"><h2>Other mailbox</h2>
    <p class="muted ctl-small">Ask your email provider for these details. Use an app password if your account has two-step sign-in.</p>
    <div class="ctl-form">
      ${field('Email address', 'my-mail-address', input('my-mail-address', draft.address, 'email'))}
      ${field('Incoming server (IMAP)', 'my-mail-imap', input('my-mail-imap', draft.imap, 'text', 'imap.example.com:993'))}
      ${field('Outgoing server (SMTP)', 'my-mail-smtp', input('my-mail-smtp', draft.smtp, 'text', 'smtp.example.com:587'))}
      ${field('Password or app password', 'my-mail-password', input('my-mail-password', draft.password, 'password'), () => 'Stored encrypted. Nobody can see it after you save.')}
      ${() => error.get() ? Callout('err', 'info', error.get()) : null}
      <div class="ctl-line"><span class="grow"></span>${Button({ label: 'Back', onClick: () => form.set(false) })}
        ${Button({ label: () => busy.get() ? 'Connecting…' : 'Connect', variant: 'primary', disabled: busy, onClick: () => void connect() })}</div></div></section>`;

  const choices = () => html`<section class="ctl-card"><div class="ctl-card-head"><div><h2>Connect a mailbox</h2></div></div>
    <div class="ctl-grid g3 ctl-pad">${PROVIDERS.map(([id, title, logo, text]) => {
      const ready = id === 'imap' || d().providers[id];
      return html`<div class="ctl-card ctl-conn"><div class="ctl-conn-top"><span class="ctl-conn-logo">${logo}</span><span class="grow"><h3>${title}</h3></span></div>
        <p>${ready || !d().allowed ? text : `Ask an admin to set up ${title} sign-in in Controls first.`}</p>
        <div class="ctl-conn-foot"><span class="grow"></span>${Button({ label: 'Connect', size: 'small', variant: 'primary', disabled: !ready || !d().allowed,
          onClick: () => void choose(id) })}</div></div>`;
    })}</div></section>`;

  return html`<div class="ctl-page">
    ${() => Head('Your email', data.get()?.mailbox ? 'Send emails from records as yourself, and see replies on the record.'
      : `Connect your mailbox to send emails from records as yourself.${data.get()?.shared ? ` Without it, emails go from ${data.get()!.shared}.` : ''}`)}
    ${() => data.get() === null ? html`<div class="ctl-empty">Loading…</div>`
      : html`<div class="ctl-stack">${policy()}${d().mailbox ? connected() : () => form.get() && d().allowed ? details() : choices()}</div>`}
  </div>` as HTMLElement;
}
