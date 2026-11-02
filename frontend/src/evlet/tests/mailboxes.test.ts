// The Controls email switch and the personal email page, against a stub of their routes.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { MyEmailClient, MyEmailPage, server as parseServer } from '../settings/email';
import { server, type Sent } from './server';
import { byText, click, lastToast, stopAll, text, typeInto, until } from './screens-helpers';

const roles = ['admin', 'editor', 'viewer'].map(id => ({ id, kind: 'framework', base: null, realm: 'desk', opens_controls: id === 'admin', description: '', users: 1, app: null, declared: true }));
const state = () => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '', version: 1 }, me: { id: 1, username: 'owner', role: 'admin' }, apps: [], roles, models: [],
  scopes: { '': 'All records' }, settings: {}, controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0,
  users: { active: 2, total: 2 }, audit: [], two_step: false, links: false, client: '127.0.0.1',
});
const reply = (body: unknown, status = 200) => ({ status, body, etag: null });
const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); window.history.replaceState(null, '', '/'); });

const mailbox = { provider: 'imap', title: 'Other mailbox', address: 'daniel@acme.test', status: 'connected', error: null, connected_at: 1790000000,
  synced_at: 1790000600, send_from: 'mine', link_replies: true, only_crm: true };
const page = (overrides: Record<string, unknown> = {}) => ({ allowed: true, providers: { microsoft: true, google: false }, shared: 'noreply@acme.test', mailbox: null, ...overrides });

function personal(routes: any[], go?: (url: string) => void) {
  const stub = server(routes);
  const host = document.createElement('div');
  document.body.append(host);
  running.push(mount(host, () => MyEmailPage({ api: new MyEmailClient({ fetch: stub.fetch }), go })));
  return { host, sent: stub.sent };
}

describe('Controls email switch', () => {
  it('turns personal mailboxes on behind the change bar', async () => {
    window.history.replaceState(null, '', '/controls/email');
    const stub = server([
      ['GET', /\/api\/auth\/session$/, () => reply({ user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' })],
      ['GET', /\/api\/_controls\/state$/, () => reply(state())],
      ['GET', /\/api\/_controls\/overrides$/, () => reply({ version: 1, problems: [], items: [
        { kind: 'setting', key: 'mail.personal', file: false, value: null, changed_by: null, changed_at: null }] })],
      ['GET', /\/api\/_controls\/connectors$/, () => reply({ connectors: [], items: [] })],
      ['POST', /\/api\/_controls\/overrides$/, () => reply({ version: 2, saved: 1 })],
    ] as any);
    const router = controlsRouter();
    const stop = router.start();
    const host = document.createElement('div');
    document.body.append(host);
    const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
    running.push(() => { dispose(); stop(); });
    await until(() => expect(text(host)).toContain("Admins can't read their mailboxes."));
    click(host.querySelector('[aria-label="Let people connect their own mailbox"]'));
    await until(() => expect(text(host)).toContain('1 unsaved change'));
    click(byText(host, '.ctl-changebar button', 'Save changes'));
    await until(() => expect(stub.sent.some(item => item.method === 'POST')).toBe(true));
    expect(stub.sent.find(item => item.method === 'POST')!.body).toEqual({ changes: [{ kind: 'setting', key: 'mail.personal', value: true }] });
  });
});

describe('Your email', () => {
  it('reads host and port', () => {
    expect(parseServer('imap.example.com:993')).toEqual(['imap.example.com', 993]);
    expect(parseServer('[::1]:1143')).toEqual(['::1', 1143]);
    expect(parseServer('imap.example.com')).toBeNull();
  });

  it('says so when the workspace has it off', async () => {
    const { host } = personal([['GET', /\/api\/_settings\/me\/email$/, () => reply(page({ allowed: false }))]] as any);
    await until(() => expect(text(host)).toContain("Your workspace doesn't let people connect their own mailbox."));
    expect(byText(host, 'button', 'Connect')!.hasAttribute('disabled')).toBe(true);
  });

  it('connects another mailbox with an app password and checks the form first', async () => {
    const { host, sent } = personal([
      ['GET', /\/api\/_settings\/me\/email$/, () => reply(page())],
      ['POST', /\/api\/_settings\/me\/email$/, () => reply(page({ mailbox }))],
    ] as any);
    await until(() => expect(text(host)).toContain('Other mailbox'));
    expect(text(host)).toContain('Ask an admin to set up Google Workspace sign-in in Controls first.');
    const cards = host.querySelectorAll('.ctl-conn');
    click(byText(cards[2], 'button', 'Connect'));
    await until(() => expect(host.querySelector('#my-mail-address')).toBeTruthy());
    typeInto(host.querySelector<HTMLInputElement>('#my-mail-address')!, 'daniel@acme.test');
    typeInto(host.querySelector<HTMLInputElement>('#my-mail-imap')!, 'imap.acme.test');
    click(byText(host, '.ctl-form button', 'Connect'));
    await until(() => expect(text(host)).toContain('Enter both servers, with the port'));
    typeInto(host.querySelector<HTMLInputElement>('#my-mail-imap')!, 'imap.acme.test:993');
    typeInto(host.querySelector<HTMLInputElement>('#my-mail-smtp')!, 'smtp.acme.test:587');
    typeInto(host.querySelector<HTMLInputElement>('#my-mail-password')!, 'app-password');
    click(byText(host, '.ctl-form button', 'Connect'));
    await until(() => expect(text(host)).toContain('Connected mailbox'));
    expect(sent.find((item: Sent) => item.method === 'POST')!.body).toEqual({ address: 'daniel@acme.test', imap_host: 'imap.acme.test', imap_port: 993,
      smtp_host: 'smtp.acme.test', smtp_port: 587, password: 'app-password' });
    expect(lastToast()).toBe('daniel@acme.test connected');
  });

  it('signs in with Microsoft 365 by going to the address the server gives', async () => {
    const went: string[] = [];
    const { host, sent } = personal([
      ['GET', /\/api\/_settings\/me\/email$/, () => reply(page())],
      ['POST', /\/api\/_settings\/me\/email\/oauth$/, () => reply({ url: 'https://login.example.test/authorize?state=abc' })],
    ] as any, url => went.push(url));
    await until(() => expect(text(host)).toContain('Microsoft 365'));
    click(byText(host.querySelectorAll('.ctl-conn')[0], 'button', 'Connect'));
    await until(() => expect(went).toEqual(['https://login.example.test/authorize?state=abc']));
    expect(sent.find((item: Sent) => item.method === 'POST')!.body).toEqual({ provider: 'microsoft' });
  });

  it('shows the result of a sign-in, changes options and disconnects', async () => {
    window.history.replaceState(null, '', '/settings/email?mail=denied');
    let current: any = page({ mailbox });
    const { host, sent } = personal([
      ['GET', /\/api\/_settings\/me\/email$/, () => reply(current)],
      ['PATCH', /\/api\/_settings\/me\/email$/, (request: Sent) => { current = page({ mailbox: { ...mailbox, ...request.body } }); return reply(current); }],
      ['POST', /\/api\/_settings\/me\/email\/sync$/, () => reply({ ...current, queued: true })],
      ['DELETE', /\/api\/_settings\/me\/email$/, () => { current = page(); return reply(current); }],
    ] as any);
    expect(lastToast()).toBe("You didn't approve access, so nothing was connected.");
    expect(window.location.search).toBe('');
    await until(() => expect(text(host)).toContain('Connected mailbox'));
    expect(text(host)).toContain('Working');
    click(host.querySelector('[aria-label="Only read emails with customers and leads"]'));
    await until(() => expect(text(host)).toContain('Emails with anyone on a record you can see are copied to that record.'));
    expect(sent.find((item: Sent) => item.method === 'PATCH')!.body).toEqual({ only_crm: false });
    click(byText(host, 'button', 'Sync now'));
    await until(() => expect(lastToast()).toBe('Syncing. New emails show on records in a minute.'));
    click(byText(host, 'button', 'Disconnect mailbox'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Emails you send from records go from noreply@acme.test again.'));
    click(byText(document.body, '.modal button', 'Disconnect'));
    await until(() => expect(text(host)).toContain('Connect a mailbox'));
  });
});
