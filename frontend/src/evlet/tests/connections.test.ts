// Controls Connectors, Email and API keys, and the personal keys page, against a stub of their routes.
// Secrets are write only: a saved one shows as saved, and only a replaced one is sent.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { MyKeysClient, MyKeysPage } from '../settings/keys';
import { server, type Sent } from './server';
import { byText, click, lastToast, stopAll, text, typeInto, until } from './screens-helpers';
import { choiceValues, choose } from './ui-helpers';

const roles = ['admin', 'editor', 'viewer'].map(id => ({ id, kind: 'framework', base: null, realm: 'desk', opens_controls: id === 'admin', description: '', users: 1, app: null, declared: true }));
const state = () => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '', version: 1 }, me: { id: 1, username: 'owner', role: 'admin' }, apps: [], roles,
  models: [{ name: 'Customer', label: 'Customer', app: null, scopes: [null], fields: [] }, { name: 'Invoice', label: 'Invoice', app: null, scopes: [null], fields: [] }],
  scopes: { '': 'All records' }, settings: { 'security.two_step_roles': { file: ['admin'], value: ['admin'] }, 'security.password_min': { file: 15, value: 15 } },
  controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0, users: { active: 2, total: 2 }, audit: [], two_step: false, links: false, client: '127.0.0.1',
});
const mail = (name: string, port: number) => [
  { name: 'address', label: 'Email address', type: 'text', options: [], default: null, required: true, help: '' },
  { name: 'host', label: 'Server', type: 'text', options: [], default: null, required: true, help: '' },
  { name: 'port', label: 'Port', type: 'number', options: [], default: port, required: true, help: '' },
  { name: 'security', label: 'Security', type: 'select', options: [['starttls', 'STARTTLS'], ['ssl', 'SSL/TLS'], ['none', 'None (this server only)']], default: name === 'smtp' ? 'starttls' : 'ssl', required: true, help: '' },
  { name: 'username', label: 'Username', type: 'text', options: [], default: null, required: false, help: '' },
  { name: 'password', label: 'Password', type: 'secret', options: [], default: null, required: true, help: '' }];
const connectors = [
  { id: 'smtp', title: 'Email sending', app: 'evlet', category: 'Email', description: 'Sends framework and app emails over SMTP.', accounts: true, testable: true, fields: mail('smtp', 587) },
  { id: 'imap', title: 'Email receiving', app: 'evlet', category: 'Email', description: 'Reads mailboxes over IMAP.', accounts: true, testable: true, fields: mail('imap', 993) },
  { id: 'whatsapp', title: 'WhatsApp', app: 'crm', category: 'Messaging', description: 'Chats with customers.', accounts: false, testable: true, fields: [
    { name: 'gateway', label: 'Gateway URL', type: 'text', options: [], default: null, required: true, help: '' },
    { name: 'api_key', label: 'API key', type: 'secret', options: [], default: null, required: true, help: '' },
    { name: 'lead', label: 'Create a Lead for new numbers', type: 'toggle', options: [], default: true, required: false, help: '' }] },
];
const whatsapp = { id: 'whatsapp', connector: 'whatsapp', account: null, status: 'connected', default: false,
  settings: { gateway: 'https://wa.example.test', lead: true }, secrets: { api_key: { saved: true } }, changed_by: 'owner', changed_at: 1790000000 };
const notifications = { id: 'smtp:notifications', connector: 'smtp', account: 'notifications', status: 'connected', default: true,
  settings: { address: 'noreply@acme.test', host: 'smtp.acme.test', port: 587, security: 'starttls', username: null }, secrets: { password: { saved: true } }, changed_by: 'owner', changed_at: 1790000000 };
const reply = (body: unknown, status = 200) => ({ status, body, etag: null });

function open(path: string, routes: any[] = []) {
  window.history.replaceState(null, '', path);
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => reply({ user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' })],
    ['GET', /\/api\/_controls\/state$/, () => reply(state())],
    ['GET', /\/api\/_controls\/overrides$/, () => reply({ version: 1, problems: [], items: [] })],
    ['GET', /\/api\/_controls\/users/, () => reply({ offset: 0, limit: 100, total: 2, items: [
      { id: 1, username: 'owner', role: 'admin', active: true, sessions: 1 }, { id: 2, username: 'rahul', role: 'editor', active: true, sessions: 0 }] })],
    ...routes,
  ] as any);
  const router = controlsRouter();
  const stop = router.start();
  const host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
  running.push(() => { dispose(); stop(); });
  return { host, sent: stub.sent };
}
const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });
const drawer = () => document.querySelector('.drawer')!;
const posted = (sent: Sent[], method: string, pattern: RegExp) => sent.filter(item => item.method === method && pattern.test(item.url));

describe('Connectors', () => {
  it('shows a saved secret as saved and sends only a replaced one', async () => {
    const { host, sent } = open('/controls/connectors', [
      ['GET', /\/api\/_controls\/connectors$/, () => reply({ connectors, items: [whatsapp] })],
      ['POST', /\/connectors\/whatsapp\/test$/, () => reply({ ok: false, error: 'The gateway rejected the API key.' })],
      ['PUT', /\/connectors\/whatsapp$/, (request: Sent) => reply({ ...whatsapp, settings: request.body.settings })],
    ]);
    await until(() => expect(byText(host, '.ctl-conn h3', 'WhatsApp')).toBeTruthy());
    expect(text(byText(host, '.ctl-conn', /WhatsApp/))).toContain('Connected');
    expect(text(byText(host, '.ctl-conn', /Email sending/))).toContain('0 accounts');
    click(byText(byText(host, '.ctl-conn', /WhatsApp/)!, 'button', 'Configure'));
    await until(() => expect(text(drawer())).toContain('•••••••••••• saved'));
    expect(drawer().querySelector('input[type="password"]')).toBeNull();
    click(byText(drawer(), 'button', 'Test'));
    await until(() => expect(text(drawer())).toContain('The gateway rejected the API key.'));
    click(byText(drawer(), 'button', 'Save'));
    await until(() => expect(lastToast()).toBe('WhatsApp saved'));
    expect(posted(sent, 'PUT', /whatsapp$/)[0].body).toEqual({ settings: { gateway: 'https://wa.example.test', lead: true }, secrets: {} });

    click(byText(byText(host, '.ctl-conn', /WhatsApp/)!, 'button', 'Configure'));
    await until(() => expect(byText(drawer(), 'button', 'Replace')).toBeTruthy());
    click(byText(drawer(), 'button', 'Replace'));
    typeInto(drawer().querySelector<HTMLInputElement>('input[type="password"]')!, 'new-key-value');
    click(byText(drawer(), 'button', 'Save'));
    await until(() => expect(posted(sent, 'PUT', /whatsapp$/)).toHaveLength(2));
    expect(posted(sent, 'PUT', /whatsapp$/)[1].body.secrets).toEqual({ api_key: 'new-key-value' });
  });
});

describe('Email', () => {
  it('adds an account, checks the address first and makes another one the default', async () => {
    let items: any[] = [notifications];
    const { host, sent } = open('/controls/email', [
      ['GET', /\/api\/_controls\/connectors$/, () => reply({ connectors, items })],
      ['PUT', /\/connectors\/smtp%3Aaccounts$/, (request: Sent) => {
        items = [...items, { ...notifications, id: 'smtp:accounts', account: 'accounts', default: false, settings: request.body.settings }];
        return reply(items[1]);
      }],
      ['POST', /\/connectors\/smtp%3Aaccounts\/default$/, () => reply({ ...items[1], default: true })],
    ]);
    await until(() => expect(byText(host, 'tr', /notifications/)).toBeTruthy());
    expect(text(byText(host, 'tr', /notifications/))).toContain('smtp.acme.test:587');
    click(byText(host, 'button', 'Add account'));
    await until(() => expect(drawer()).toBeTruthy());
    typeInto(drawer().querySelector<HTMLInputElement>('#ctl-em-name')!, 'accounts');
    typeInto(drawer().querySelector<HTMLInputElement>('#ctl-cf-address')!, 'not an address');
    click(byText(drawer(), 'button', 'Add account'));
    await until(() => expect(text(drawer())).toContain('Enter a valid email address.'));
    typeInto(drawer().querySelector<HTMLInputElement>('#ctl-cf-address')!, 'accounts@acme.test');
    typeInto(drawer().querySelector<HTMLInputElement>('#ctl-cf-host')!, 'smtp.acme.test');
    click(byText(drawer(), 'button', 'Add account'));
    await until(() => expect(text(drawer())).toContain('Enter the mailbox password.'));
    typeInto(drawer().querySelector<HTMLInputElement>('input[type="password"]')!, 'app-password');
    click(byText(drawer(), 'button', 'Add account'));
    await until(() => expect(lastToast()).toBe('Account saved'));
    expect(posted(sent, 'PUT', /accounts$/)[0].body).toEqual({
      settings: { address: 'accounts@acme.test', host: 'smtp.acme.test', port: 587, security: 'starttls', username: null }, secrets: { password: 'app-password' } });
    await until(() => expect(byText(host, 'tr', /^accounts/)).toBeTruthy());
    click(byText(byText(host, 'tr', /^accounts/)!, 'button', 'Make default'));
    await until(() => expect(posted(sent, 'POST', /default$/)).toHaveLength(1));
  });
});

describe('API keys', () => {
  it('shows a new key once and lists only its prefix', async () => {
    let items: any[] = [];
    const { host, sent } = open('/controls/keys', [
      ['GET', /\/api\/_controls\/keys$/, () => reply({ items })],
      ['POST', /\/api\/_controls\/keys$/, (request: Sent) => {
        const key = { id: 7, user_id: 2, username: request.body.user, role: 'editor', name: request.body.name, prefix: 'evk_ab12cd34', scope: request.body.scope, created_at: Math.floor(Date.now() / 1000), last_used: null };
        items = [key];
        return reply({ key, token: 'evk_ab12cd34_' + 'x'.repeat(43) }, 201);
      }],
      ['DELETE', /\/api\/_controls\/keys\/7$/, () => { items = []; return reply({ items }); }],
    ]);
    await until(() => expect(text(host)).toContain('No keys yet.'));
    click(byText(host, 'button', 'Create key'));
    await until(() => expect(document.querySelector('.modal #ctl-k-name')).toBeTruthy());
    await until(() => expect((byText(document.body, '.modal button', 'Create key') as HTMLButtonElement).disabled).toBe(false));
    click(byText(document.body, '.modal button', 'Create key'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Give the key a name so you can recognise it later.'));
    typeInto(document.querySelector<HTMLInputElement>('#ctl-k-name')!, 'Website lead form');
    const user = document.querySelector<HTMLElement>('#ctl-k-user')!;
    await until(() => expect(choiceValues(user)).toHaveLength(2));
    choose(user, 'rahul');
    choose(document.querySelector<HTMLElement>('#ctl-k-models')!, 'Customer');
    click(byText(document.body, '.modal button', 'Create key'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain("Copy it now. For security it won't be shown again."));
    expect(text(document.querySelector('.modal'))).toContain('evk_ab12cd34_xxx');
    expect(posted(sent, 'POST', /keys$/)[0].body).toEqual({ name: 'Website lead form', scope: { access: 'read', models: ['Customer'] }, user: 'rahul' });
    click(byText(document.body, '.modal button', 'Done'));
    await until(() => expect(byText(host, 'tr', /Website lead form/)).toBeTruthy());
    expect(text(byText(host, 'tr', /Website lead form/))).toContain('evk_ab12cd34…');
    expect(text(host)).not.toContain('xxxx');
    expect(text(byText(host, 'tr', /Website lead form/))).toContain('Customer · read');
    click(byText(byText(host, 'tr', /Website lead form/)!, 'button', 'Revoke'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Anything using this key gets a 401 from now on.'));
    click(byText(document.body, '.modal button', 'Revoke key'));
    await until(() => expect(text(host)).toContain('No keys yet.'));
  });

  it('mounts the personal keys page on its own routes', async () => {
    const stub = server([
      ['GET', /\/api\/_settings\/me\/keys$/, () => reply({ items: [], models: [{ name: 'Customer', label: 'Customer' }] })],
      ['POST', /\/api\/_settings\/me\/keys$/, (request: Sent) => reply({ key: { id: 1, user_id: 1, username: 'owner', role: 'admin', name: request.body.name,
        prefix: 'evk_aaaa1111', scope: request.body.scope, created_at: 1790000000, last_used: null }, token: 'evk_aaaa1111_' + 'y'.repeat(43) }, 201)],
    ] as any);
    const host = document.createElement('div');
    document.body.append(host);
    running.push(mount(host, () => MyKeysPage({ api: new MyKeysClient({ fetch: stub.fetch }) })));
    await until(() => expect(text(host)).toContain("No keys. Most people don't need one."));
    expect(text(host)).toContain('A key can never do more than you can.');
    click(byText(host, 'button', 'Create key'));
    await until(() => expect(document.querySelector('#ctl-k-name')).toBeTruthy());
    expect(document.querySelector('#ctl-k-user')).toBeNull();
    typeInto(document.querySelector<HTMLInputElement>('#ctl-k-name')!, 'Power BI export');
    // Without two-step sign-in a new key asks for the password.
    expect(document.querySelector('#ctl-k-code')).toBeNull();
    click(byText(document.body, '.modal button', 'Create key'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Enter your password.'));
    typeInto(document.querySelector<HTMLInputElement>('#ctl-k-password')!, 'my password');
    click(byText(document.body, '.modal button', 'Create key'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('evk_aaaa1111_yyy'));
    expect(stub.sent.find(item => item.method === 'POST')!.body).toEqual({ name: 'Power BI export', scope: { access: 'read', models: null }, password: 'my password' });
  });

  it('asks only for a code before a new personal key when two-step sign-in is on', async () => {
    const stub = server([
      ['GET', /\/api\/_settings\/me\/keys$/, () => reply({ items: [], models: [] })],
      ['POST', /\/api\/_settings\/me\/keys$/, (request: Sent) => reply({ key: { id: 1, user_id: 1, username: 'owner', role: 'admin', name: request.body.name,
        prefix: 'evk_aaaa1111', scope: request.body.scope, created_at: 1790000000, last_used: null }, token: 'evk_aaaa1111_' + 'y'.repeat(43) }, 201)],
    ] as any);
    const host = document.createElement('div');
    document.body.append(host);
    running.push(mount(host, () => MyKeysPage({ api: new MyKeysClient({ fetch: stub.fetch }), twoStep: true })));
    await until(() => expect(byText(host, 'button', 'Create key')).toBeTruthy());
    click(byText(host, 'button', 'Create key'));
    await until(() => expect(document.querySelector('#ctl-k-name')).toBeTruthy());
    expect(document.querySelector('#ctl-k-password')).toBeNull();
    typeInto(document.querySelector<HTMLInputElement>('#ctl-k-name')!, 'Script');
    typeInto(document.querySelector<HTMLInputElement>('#ctl-k-code')!, '123456');
    click(byText(document.body, '.modal button', 'Create key'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('evk_aaaa1111_yyy'));
    expect(stub.sent.find(item => item.method === 'POST')!.body).toEqual({ name: 'Script', scope: { access: 'read', models: null }, code: '123456' });
  });
});
