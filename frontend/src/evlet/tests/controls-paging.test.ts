import { afterEach, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { server, type Sent } from './server';
import { byText, click, stopAll, text, typeInto, until } from './screens-helpers';
import { choiceValues, choose } from './ui-helpers';

const reply = (body: unknown, status = 200) => ({ status, body, etag: null });
const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });

function open(page: 'users' | 'keys', delaySearch = false) {
  const people = Array.from({ length: 151 }, (_, id) => ({ id: id + 1, username: `person${String(id).padStart(3, '0')}`,
    role: id % 2 ? 'editor' : 'viewer', active: id !== 149, sessions: 0, keys: 0 }));
  let keys = Array.from({ length: 205 }, (_, at) => ({ id: 205 - at, user_id: 1, username: 'person000', role: 'viewer',
    name: `Credential ${String(205 - at).padStart(3, '0')}`, prefix: `evk_${String(205 - at).padStart(8, '0')}`,
    scope: { access: 'read', models: null }, created_at: 1, last_used: null }));
  let finishOld: (() => void) | undefined;
  const state = { instance: { name: 'Acme', host: 'localhost', evlet: '', version: 1 }, me: { id: 1000, username: 'owner', role: 'admin' }, apps: [],
    roles: ['admin', 'editor', 'viewer'].map(id => ({ id, kind: 'framework', base: null, realm: 'desk', opens_controls: id === 'admin', users: 0, description: '', app: null })),
    models: [], scopes: {}, settings: { 'security.two_step_roles': { file: [], value: [] }, 'security.password_min': { file: 15, value: 15 } },
    controls: { allow_ips: [], require_two_step: false }, attention: [], overrides: 0, users: { active: 150, total: 151 }, audit: [], two_step: false, client: '127.0.0.1' };
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => reply({ user: state.me, csrf: 'token' })],
    ['GET', /\/api\/_controls\/state$/, () => reply(state)],
    ['GET', /\/api\/_controls\/overrides$/, () => reply({ version: 1, problems: [], items: [] })],
    ['GET', /\/api\/_controls\/users(?:\?|$)/, (sent: Sent) => {
      const params = new URL(sent.url, 'http://localhost').searchParams, q = params.get('q') ?? '', role = params.get('role');
      const offset = Number(params.get('offset') ?? 0), limit = 100;
      const matching = people.filter(user => user.username.includes(q) && (!role || user.role === role));
      const active = matching.filter(user => user.active).length;
      const rows = matching.filter(user => !params.has('active') || user.active === (params.get('active') === 'true'));
      const response = reply({ items: rows.slice(offset, offset + limit), total: rows.length, offset, limit,
        counts: { all: matching.length, active, inactive: matching.length - active } });
      if (delaySearch && q === 'person1') return new Promise(resolve => { finishOld = () => resolve(response); });
      return response;
    }],
    ['PATCH', /\/api\/_controls\/users\/\d+$/, (sent: Sent) => {
      Object.assign(people.find(user => user.id === Number(sent.url.split('/').pop()))!, sent.body);
      return reply(null, 204);
    }],
    ['GET', /\/api\/_controls\/keys(?:\?|$)/, (sent: Sent) => {
      const params = new URL(sent.url, 'http://localhost').searchParams, q = params.get('q') ?? '', before = Number(params.get('before') ?? Infinity);
      const matching = keys.filter(key => key.name.includes(q) && (!params.get('user') || key.username === params.get('user')));
      const rows = matching.filter(key => key.id < before), items = rows.slice(0, 100);
      return reply({ items, total: matching.length, next_before: rows.length > 100 ? items.at(-1)!.id : null });
    }],
    ['DELETE', /\/api\/_controls\/keys\/\d+$/, (sent: Sent) => {
      keys = keys.filter(key => key.id !== Number(sent.url.split('/').pop())); return reply({ items: keys.slice(0, 100) });
    }],
    ['POST', /\/api\/_controls\/keys$/, (sent: Sent) => reply({ key: { id: 206, username: sent.body.user }, token: 'evk_once_only' }, 201)],
  ]);
  window.history.replaceState(null, '', '/controls/' + page);
  const router = controlsRouter(), stop = router.start(), host = document.createElement('div');
  document.body.append(host);
  const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: stub.fetch }) }));
  running.push(() => { dispose(); stop(); });
  return { host, sent: stub.sent, people, finishOld: () => finishOld?.() };
}

it('pages users, searches beyond the first hundred, edits a late account and ignores stale search results', async () => {
  const { host, sent, finishOld } = open('users', true);
  await until(() => expect(host.querySelectorAll('tbody tr')).toHaveLength(100));
  click(byText(host, 'button', 'Next users'));
  await until(() => expect(byText(host, 'tr', /person150/)).toBeTruthy());
  expect(host.querySelectorAll('tbody tr')).toHaveLength(51);
  const input = host.querySelector<HTMLInputElement>('[aria-label="Search users"]')!;
  typeInto(input, 'person1');
  await until(() => expect(sent.some(request => new URL(request.url, 'http://localhost').searchParams.get('q') === 'person1')).toBe(true));
  typeInto(input, 'person150');
  await until(() => expect(text(host.querySelector('.ctl-tfoot'))).toContain('1–1 of 1'));
  finishOld();
  await Promise.resolve();
  expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
  click(byText(host, 'tr', /person150/));
  await until(() => expect(document.querySelector('#ctl-u-role')).toBeTruthy());
  choose(document.querySelector<HTMLElement>('#ctl-u-role')!, 'editor');
  click(byText(document.body, '.drawer button', 'Save user'));
  await until(() => expect(sent.some(request => request.method === 'PATCH' && request.url.endsWith('/users/151') && request.body.role === 'editor')).toBe(true));
  await until(() => expect(text(byText(host, 'tr', /person150/))).toContain('editor'));
  typeInto(input, 'person14');
  await until(() => expect(text(host.querySelector('.ctl-tfoot'))).toContain('1–10 of 10'));
  click(byText(host, '[role="tab"]', /^Inactive/));
  await until(() => expect(host.querySelectorAll('tbody tr')).toHaveLength(1));
  expect(text(host.querySelector('tbody'))).toContain('person149');
});

it('pages all keys in both directions and searches and revokes the oldest', async () => {
  const { host, sent } = open('keys');
  await until(() => expect(host.querySelectorAll('tbody tr')).toHaveLength(100));
  click(byText(host, 'button', 'Older keys'));
  await until(() => expect(byText(host, 'tr', /Credential 006/)).toBeTruthy());
  click(byText(host, 'button', 'Older keys'));
  await until(() => expect(byText(host, 'tr', /Credential 001/)).toBeTruthy());
  expect(host.querySelectorAll('tbody tr')).toHaveLength(5);
  click(byText(host, 'button', 'Newer keys'));
  await until(() => expect(byText(host, 'tr', /Credential 105/)).toBeTruthy());
  click(byText(host, 'button', 'Newer keys'));
  await until(() => expect(byText(host, 'tr', /Credential 205/)).toBeTruthy());
  typeInto(host.querySelector<HTMLInputElement>('[aria-label="Search API keys"]')!, 'Credential 001');
  await until(() => expect(text(host.querySelector('.ctl-tfoot'))).toContain('1 of 1 keys'));
  click(byText(byText(host, 'tr', /Credential 001/)!, 'button', 'Revoke'));
  await until(() => expect(document.querySelector('.modal')).toBeTruthy());
  click(byText(document.body, '.modal button', 'Revoke key'));
  await until(() => expect(text(host.querySelector('.ctl-tfoot'))).toContain('0 of 0 keys'));
  expect(sent.some(request => request.method === 'DELETE' && request.url.endsWith('/keys/1'))).toBe(true);
});

it('searches and pages key owners without loading all users into the picker', async () => {
  const { host, sent } = open('keys');
  await until(() => expect(byText(host, 'button', 'Create key')).toBeTruthy());
  click(byText(host, 'button', 'Create key'));
  const owner = () => document.querySelector<HTMLElement>('#ctl-k-user')!;
  await until(() => expect(choiceValues(owner())).toHaveLength(100));
  click(byText(document.body, '.modal button', 'Next users'));
  await until(() => expect(choiceValues(owner())).toContain('person150'));
  expect(choiceValues(owner())).toHaveLength(50);
  typeInto(document.querySelector<HTMLInputElement>('#ctl-k-user-search')!, 'person150');
  await until(() => expect(choiceValues(owner())).toEqual(['person150']));
  typeInto(document.querySelector<HTMLInputElement>('#ctl-k-name')!, 'Last account key');
  click(byText(document.body, '.modal button', 'Create key'));
  await until(() => expect(sent.some(request => request.method === 'POST' && request.url.endsWith('/keys') && request.body.user === 'person150')).toBe(true));
  await until(() => expect(text(document.querySelector('.modal'))).toContain('evk_once_only'));
});
