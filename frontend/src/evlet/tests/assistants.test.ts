// Controls MCP & AI, the personal AI assistants page and the consent page, against a stub of their routes.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { MyAIClient, MyAIPage } from '../settings/ai';
import { ConsentClient, ConsentPage } from '../settings/consent';
import { server, type Sent } from './server';
import { byText, click, lastToast, stopAll, text, until } from './screens-helpers';

const roles = ['admin', 'editor', 'viewer'].map(id => ({ id, kind: 'framework', base: null, realm: 'desk', opens_controls: id === 'admin', description: '', users: 1, app: null, declared: true }));
const state = () => ({
  instance: { name: 'Acme', host: 'erp.acme.com', evlet: '', version: 1 }, me: { id: 1, username: 'owner', role: 'admin' }, apps: [], roles,
  models: [{ name: 'Customer', label: 'Customer', app: 'crm', scopes: [null], fields: [] }, { name: 'Invoice', label: 'Invoice', app: 'billing', scopes: [null], fields: [] }],
  scopes: { '': 'All records' }, settings: {}, controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0,
  users: { active: 2, total: 2 }, audit: [], two_step: false, links: false, client: '127.0.0.1',
});
const setting = (key: string, file: unknown) => ({ kind: 'setting', key, file, value: null, changed_by: null, changed_at: null });
const overrides = { version: 1, problems: [], items: [setting('mcp.enabled', true), setting('mcp.allow_writes', false), setting('mcp.rate', 60),
  setting('mcp.disabled_tools', []), setting('mcp.hidden_models', [])] };
const mcp = (clients = [{ id: 7, client_name: 'Claude', username: 'rahul', role: 'editor', created_at: 1790000000, last_used: 1790000600, calls: 57 }]) =>
  ({ address: 'https://erp.acme.com/_/mcp', clients, totals: { reads: 241, writes: 0, denied: 7 } });
const reply = (body: unknown, status = 200) => ({ status, body, etag: null });
const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });

function open(routes: any[] = []) {
  window.history.replaceState(null, '', '/controls/mcp');
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => reply({ user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' })],
    ['GET', /\/api\/_controls\/state$/, () => reply(state())],
    ['GET', /\/api\/_controls\/overrides$/, () => reply(overrides)],
    ['GET', /\/api\/_controls\/mcp$/, () => reply(mcp())],
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

describe('Controls MCP & AI', () => {
  it('shows totals and clients, confirms before allowing writes, and signs a client out', async () => {
    const { host, sent } = open([['DELETE', /\/api\/_controls\/mcp\/7$/, () => reply(mcp([]))]]);
    await until(() => expect(text(host)).toContain('57 calls'));
    expect(text(host)).toContain('248tool calls');
    expect(text(host)).toContain('Denied by permissions7');
    expect(text(host)).toContain('https://erp.acme.com/_/mcp');
    expect(text(byText(host, 'tr', /create_record/))).toContain('Writes off');
    click(host.querySelector('[aria-label="Allow writes"]'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('Claude asks the user before each change, but other clients may not.'));
    click(byText(document.body, '.modal button', 'Allow writes'));
    await until(() => expect(text(host)).toContain('1 unsaved change'));
    expect(text(byText(host, 'tr', /create_record/))).not.toContain('Writes off');
    click(host.querySelector('[aria-label="Show Customer"]'));
    await until(() => expect(text(host)).toContain('2 unsaved changes'));
    click(byText(host, '.ctl-row-btn button', 'Sign out'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain("rahul's Claude loses access straight away."));
    click(byText(document.body, '.modal button', 'Sign out'));
    await until(() => expect(text(host)).toContain('No clients connected.'));
    expect(lastToast()).toBe('Client signed out');
    expect(sent.some(item => item.method === 'DELETE' && /mcp\/7$/.test(item.url))).toBe(true);
  });
});

const mine = (writes: boolean, clients = [{ id: 3, client_name: 'Claude', created_at: 1790000000, last_used: null, calls: 0, allow_changes: false, apps: null }]) => ({
  enabled: true, writes, address: 'https://erp.acme.com/_/mcp', clients, apps: ['crm', 'billing'], chosen: null,
  requests: [{ at: 1790000600, client_name: 'Claude', tool: 'list_records', model: 'customer', kind: 'read', outcome: 'denied' }],
});

describe('Personal AI assistants', () => {
  it('shows the connect steps, the writes-off state, my requests, and saves apps and disconnects', async () => {
    const stub = server([
      ['GET', /\/api\/_settings\/me\/ai$/, () => reply(mine(false))],
      ['PATCH', /\/api\/_settings\/me\/ai$/, (request: Sent) => reply({ ...mine(false), chosen: request.body.apps })],
      ['DELETE', /\/api\/_settings\/me\/ai\/3$/, () => reply(mine(false, []))],
    ] as any);
    const host = document.createElement('div');
    document.body.append(host);
    running.push(mount(host, () => MyAIPage({ api: new MyAIClient({ fetch: stub.fetch }) })));
    await until(() => expect(text(host)).toContain('Your admin has turned off changes'));
    expect(text(host)).toContain('Add custom connector');
    expect(text(host)).toContain('https://erp.acme.com/_/mcp');
    expect(host.querySelector<HTMLButtonElement>('[aria-label="Can make changes"]')!.disabled).toBe(true);
    expect(text(byText(host, 'tr', /list_records/))).toContain('Not allowed');
    click(host.querySelector('[aria-label="billing"]'));
    await until(() => expect(stub.sent.find(item => item.method === 'PATCH')?.body).toEqual({ apps: ['crm'] }));
    click(byText(host, 'button', 'Disconnect'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('It loses access straight away.'));
    click(byText(document.body, '.modal button', 'Disconnect'));
    await until(() => expect(text(host)).toContain('Follow the steps above to connect one.'));
  });

  it('lets a person allow changes when the workspace does', async () => {
    const stub = server([
      ['GET', /\/api\/_settings\/me\/ai$/, () => reply(mine(true))],
      ['PATCH', /\/api\/_settings\/me\/ai\/3$/, (request: Sent) => reply(mine(true, [{ ...mine(true).clients[0], allow_changes: request.body.allow_changes }]))],
    ] as any);
    const host = document.createElement('div');
    document.body.append(host);
    running.push(mount(host, () => MyAIPage({ api: new MyAIClient({ fetch: stub.fetch }) })));
    await until(() => expect(text(host)).toContain('Assistants can make changes if you allow it below.'));
    click(host.querySelector('[aria-label="Can make changes"]'));
    await until(() => expect(lastToast()).toBe('Changes allowed'));
    expect(stub.sent.find(item => item.method === 'PATCH')!.body).toEqual({ allow_changes: true });
  });
});

describe('Consent page', () => {
  const place = (search: string) => {
    const visited: string[] = [];
    return { visited, location: { pathname: '/_/oauth/authorize', search, assign: (url: string) => { visited.push(url); } } };
  };

  it('sends a signed-out person to sign in and back', async () => {
    const stub = server([['GET', /\/api\/auth\/session$/, () => reply({ user: null })]] as any);
    const { visited, location } = place('?client_id=mcp_1&state=x');
    running.push(mount(document.body.appendChild(document.createElement('div')), () => ConsentPage({ api: new ConsentClient({ fetch: stub.fetch }), location })));
    await until(() => expect(visited).toEqual(['/login?next=' + encodeURIComponent('/_/oauth/authorize?client_id=mcp_1&state=x')]));
  });

  it('shows the client, posts the answer with the choice and follows the redirect', async () => {
    const stub = server([
      ['GET', /\/api\/auth\/session$/, () => reply({ user: { id: 2, username: 'rahul', role: 'editor' }, csrf: 'token' })],
      ['GET', /\/api\/_oauth\/authorize\?client_id=mcp_1/, () => reply({ client: 'Claude', returns_to: 'claude.ai', user: 'rahul', writes: true })],
      ['POST', /\/api\/_oauth\/authorize$/, () => reply({ redirect: 'https://claude.ai/api/mcp/auth_callback?code=abc&state=x' })],
    ] as any);
    const { visited, location } = place('?client_id=mcp_1&state=x');
    const host = document.body.appendChild(document.createElement('div'));
    running.push(mount(host, () => ConsentPage({ api: new ConsentClient({ fetch: stub.fetch }), location })));
    await until(() => expect(text(host)).toContain('Connect Claude?'));
    expect(text(host)).toContain('claude.ai');
    click(byText(host, '.sw-wrap', 'Let it make changes too'));
    click(byText(host, 'button', 'Allow'));
    await until(() => expect(visited).toEqual(['https://claude.ai/api/mcp/auth_callback?code=abc&state=x']));
    const posted = stub.sent.find(item => item.method === 'POST')!;
    expect(posted.body).toEqual({ query: 'client_id=mcp_1&state=x', approve: true, allow_changes: true });
    expect(posted.headers['x-csrf-token']).toBe('token');
  });

  it('shows a request problem instead of redirecting', async () => {
    const stub = server([
      ['GET', /\/api\/auth\/session$/, () => reply({ user: { id: 2, username: 'rahul', role: 'editor' }, csrf: 'token' })],
      ['GET', /\/api\/_oauth\/authorize/, () => reply({ error: "The assistant asked to return to an address it didn't register." }, 400)],
    ] as any);
    const { visited, location } = place('?client_id=mcp_1&redirect_uri=https://evil.test');
    const host = document.body.appendChild(document.createElement('div'));
    running.push(mount(host, () => ConsentPage({ api: new ConsentClient({ fetch: stub.fetch }), location })));
    await until(() => expect(text(host)).toContain("Can't connect"));
    expect(text(host)).toContain("address it didn't register");
    expect(visited).toEqual([]);
  });
});
