// Controls Apps, Routing, Jobs, Overrides and Audit log in jsdom, against a stub of the /api/_controls routes.
import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '../dom';
import { Controls, controlsRouter } from '../controls/app';
import { ControlsClient } from '../controls/client';
import { server, type Sent } from './server';
import { byText, click, lastToast, requests, stopAll, text, typeInto, until } from './screens-helpers';

const settings = { 'security.password_min': { file: 15, value: 15 }, 'security.session_hours': { file: 8, value: 8 }, 'security.lockout': { file: 10, value: 5 },
  'security.two_step_roles': { file: ['admin'], value: ['admin'] }, 'security.controls_ips': { file: [], value: [] } };
const notes = { id: 'notes', title: 'Notes', version: '0.1.0', source: 'apps/notes', models: ['NotesItem'], connectors: [], permissions: 2, schedules: ['nightly'],
  frontends: [{ name: 'notes.desk', path: '/notes', host: 'localhost', realm: 'desk' }], update: { from: '0.1.0', to: '0.2.0', error: null }, restart: false };
const state = () => ({
  instance: { name: 'Acme', host: 'localhost', evlet: '0.1.3', version: 2, slug: 'acme', managed: true },
  me: { id: 1, username: 'owner', role: 'admin' }, apps: [notes], jobs: { failed: 1 },
  roles: [{ id: 'admin', kind: 'framework', base: null, realm: 'desk', opens_controls: true, description: '', users: 1, app: null, declared: true }],
  models: [], scopes: { '': 'All records' }, settings, controls: { allow_ips: [], require_two_step: false },
  attention: [{ tone: 'amber', icon: 'package', page: 'apps', title: 'Notes 0.2.0 is waiting to be migrated', text: 'Stop the server, then run evlet migrate acme on the server. It takes a backup first.' }],
  overrides: 2, users: { active: 1, total: 1 }, audit: [], two_step: false, links: false, client: '127.0.0.1', remove_fields: null, instance_fields: true,
});
const overrides = { version: 2, problems: [], items: [
  { kind: 'setting', key: 'security.lockout', file: 10, value: 5, changed_by: 'owner', changed_at: 1790000000 },
  { kind: 'setting', key: 'schedule.nightly.active', file: true, value: false, changed_by: 'owner', changed_at: 1790000000 },
  { kind: 'setting', key: 'security.password_min', file: 15, value: null, changed_by: null, changed_at: null },
] };
const jobs = (failed = true) => ({ available: true,
  schedules: [{ name: 'nightly', task: 'cleanup', every: 86400, expression: null, timezone: null, next_run: 1790000000, active: false, paused: true, app: 'notes' }],
  jobs: [{ id: 7, task: 'cleanup', actor: null, model: null, record_id: null, state: failed ? 'failed' : 'queued', attempts: failed ? 3 : 0, run_at: 1790000000, error: failed ? 'NotFoundError' : null },
    { id: 6, task: 'cleanup', actor: null, model: null, record_id: null, state: 'succeeded', attempts: 1, run_at: 1789990000, error: null }] });
const audit = (before?: string) => ({ areas: ['Jobs', 'Security'], next: before ? null : 9,
  items: before ? [{ id: 8, at: 1789000000, actor: 'owner', area: 'Security', target: 'security.lockout', detail: 'Changed' }]
    : [{ id: 10, at: 1790000000, actor: 'owner', area: 'Jobs', target: 'nightly', detail: 'Paused' }] });
const reply = (body: unknown, status = 200) => ({ status, body, etag: null });

const running: Array<() => void> = [];
afterEach(() => { running.splice(0).forEach(stop => stop()); stopAll(); });

function open(path: string) {
  window.history.replaceState(null, '', path);
  const stub = server([
    ['GET', /\/api\/auth\/session$/, () => reply({ user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'token' })],
    ['GET', /\/api\/_controls\/state$/, () => reply(state())],
    ['GET', /\/api\/_controls\/overrides$/, () => reply(overrides)],
    ['GET', /\/api\/_controls\/overrides\/export/, (sent: Sent) => reply(sent.url.includes('kind=') ? [{ file: 'apps/notes/notes/hooks.py', code: '# nightly is paused\n' }]
      : [{ file: 'instances/acme/config.py', code: 'security = Security(lockout=5)\n' }, { file: 'apps/notes/notes/hooks.py', code: '# nightly is paused\n' }])],
    ['GET', /\/api\/_controls\/routing$/, () => reply({ rows: [{ host: 'localhost', path: '/controls', serves: 'evlet.controls', realm: 'control', source: 'locked', locked: true },
      { host: 'localhost', path: '/notes', serves: 'notes.desk', realm: 'desk', source: 'app notes', locked: false }],
      planned: ['/report', '/print'], hosts: [{ host: 'erp.acme.com', primary: true, sub: false, realm: 'desk' }, { host: 'portal.acme.com', primary: false, sub: true, realm: 'portal' }],
      primary: 'erp.acme.com', proxy: { caddy: 'portal.acme.com {\n\trespond @primary_only 404\n}', nginx: 'server_name erp.acme.com portal.acme.com;' }, proxy_error: null })],
    ['GET', /\/api\/_controls\/jobs$/, () => reply(jobs())],
    ['POST', /\/api\/_controls\/jobs\/schedules\/nightly$/, () => reply({ ...jobs(), schedules: [{ ...jobs().schedules[0], active: true, paused: false }] })],
    ['POST', /\/api\/_controls\/jobs\/schedules\/nightly\/run$/, () => reply({ ...jobs(), job: 8 })],
    ['POST', /\/api\/_controls\/jobs\/7\/retry$/, () => reply(jobs(false))],
    ['GET', /\/api\/_controls\/audit/, (sent: Sent) => reply(audit(new URL(sent.url, 'http://x').searchParams.get('before') ?? undefined))],
  ] as any);
  const router = controlsRouter();
  const stop = router.start();
  const host = document.createElement('div');
  document.body.append(host);
  // The server refuses a POST or PATCH that is not JSON (evlet/app.py), so the stub does too.
  const strict = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const type = Object.entries(init.headers ?? {}).find(([key]) => key.toLowerCase() === 'content-type')?.[1];
    if (['POST', 'PATCH'].includes(init.method ?? 'GET') && type !== 'application/json') {
      return new Response(JSON.stringify({ error: 'Send application/json' }), { status: 415, headers: { 'content-type': 'application/json' } });
    }
    return stub.fetch(input, init);
  }) as typeof fetch;
  const dispose = mount(host, () => Controls({ router, api: new ControlsClient({ fetch: strict }) }));
  running.push(() => { dispose(); stop(); });
  return { host, sent: stub.sent };
}

describe('Controls system pages', () => {
  it('shows a waiting migration with the shell command and no browser migrate', async () => {
    const { host } = open('/controls/apps');
    await until(() => expect(byText(host, '.ctl-app', /Notes/)).toBeTruthy());
    expect(text(byText(host, '.ctl-nav-item', /^Apps/))).toBe('Apps 1');
    expect(text(host.querySelector('.ctl-app'))).toContain('Migration waiting');
    click(byText(host, 'button', 'Install app'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('evlet install <app> --instance acme --trust'));
    click(byText(document.body, '.modal button', 'Got it'));
    click(host.querySelector('.ctl-app'));
    await until(() => expect(text(document.querySelector('.drawer'))).toContain('Version 0.2.0 is waiting to be migrated'));
    expect(text(document.querySelector('.drawer pre'))).toBe('evlet migrate acme');
    expect(text(document.querySelector('.drawer'))).toContain('Stop the server first');
    expect(byText(document.body, '.drawer button', /migrate/i)).toBeNull();
  });

  it('lists routes read only, the sub-hosts with their sign-in and the proxy config', async () => {
    const { host } = open('/controls/routing');
    await until(() => expect(byText(host, 'tr', /\/notes/)).toBeTruthy());
    expect(text(byText(host, 'tr', /\/controls/))).toContain('Locked');
    expect(text(byText(host, 'tr', /\/notes/))).toContain('App notes');
    expect(text(byText(host, 'tr', /erp\.acme\.com/))).toContain('Primary');
    expect(text(byText(host, 'tr', /portal\.acme\.com/))).toMatch(/Sub-host\s*portal/);
    expect(byText<HTMLButtonElement>(host, 'button', 'Check hosts')!.disabled).toBe(false);
    expect(text(byText(host, 'tr', /Reserved/))).toContain('/report, /print');
    click(byText(host, 'button', 'Proxy config'));
    await until(() => expect(text(document.querySelector('.modal pre'))).toContain('respond @primary_only 404'));
  });

  it('pauses and resumes schedules, runs one now and retries failed jobs only', async () => {
    const { host, sent } = open('/controls/jobs');
    await until(() => expect(host.querySelector('[role="switch"][aria-label="Run nightly"]')).toBeTruthy());
    expect(text(byText(host, 'tr', /nightly/))).toContain('Paused');
    click(host.querySelector('[role="switch"][aria-label="Run nightly"]'));
    await until(() => expect(lastToast()).toBe('nightly resumed'));
    expect(requests(sent, 'POST', /schedules\/nightly$/)[0].body).toEqual({ active: true });
    click(byText(host, 'button', 'Run now'));
    await until(() => expect(lastToast()).toBe('nightly queued'));
    expect(byText(host, 'tr', /#6/)!.querySelector('button')).toBeNull();
    click(byText(byText(host, 'tr', /#7/)!, 'button', 'Retry'));
    await until(() => expect(lastToast()).toBe('Job #7 queued again'));
    expect(requests(sent, 'POST', /jobs\/7\/retry$/)).toHaveLength(1);
    await until(() => expect(byText(host, 'tr', /#7/)!.querySelector('button')).toBeNull());
  });

  it('lists overrides with the file value, resets through the change bar and exports code', async () => {
    const { host, sent } = open('/controls/overrides');
    await until(() => expect(byText(host, 'tr', /Failed sign-ins/)).toBeTruthy());
    expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(text(byText(host, 'tr', /Failed sign-ins/))).toContain('10 attempts');
    expect(text(byText(host, 'tr', /nightly schedule/))).toMatch(/Running ?Paused/);
    click(byText(host, 'tr', /nightly schedule/)!.querySelector('button[aria-label="Show code"]'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('apps/notes/notes/hooks.py'));
    expect(requests(sent, 'GET', /export\?kind=setting&key=schedule\.nightly\.active$/)).toHaveLength(1);
    click(byText(document.body, '.modal button', 'Done'));
    click(byText(host, 'button', 'Export as code'));
    await until(() => expect(text(document.querySelector('.modal'))).toContain('security = Security(lockout=5)'));
    click(byText(document.body, '.modal button', 'Done'));
    click(byText(byText(host, 'tr', /Failed sign-ins/)!, 'button', 'Reset'));
    await until(() => expect(text(document.querySelector('.ctl-changebar'))).toContain('1 unsaved change'));
    expect(text(byText(host, 'tr', /Failed sign-ins/))).toContain('Reset pending');
  });

  it('searches and filters the audit log and pages to older entries', async () => {
    const { host, sent } = open('/controls/audit');
    await until(() => expect(text(host.querySelector('.ctl-audit'))).toContain('nightly'));
    click(byText(host, 'button', 'Show older'));
    await until(() => expect(host.querySelectorAll('.ctl-audit')).toHaveLength(2));
    expect(requests(sent, 'GET', /audit\?before=9$/)).toHaveLength(1);
    typeInto(host.querySelector<HTMLInputElement>('input[aria-label="Search changes"]')!, 'lockout');
    await until(() => expect(requests(sent, 'GET', /audit\?q=lockout$/)).toHaveLength(1));
    const area = host.querySelector<HTMLSelectElement>('select[aria-label="Area"]')!;
    area.value = 'Security';
    area.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(requests(sent, 'GET', /audit\?q=lockout&area=Security$/)).toHaveLength(1));
  });

  it('links the overview tiles and attention items to the new pages', async () => {
    const { host } = open('/controls');
    await until(() => expect(text(host)).toContain('Notes 0.2.0 is waiting to be migrated'));
    expect(text(byText(host, '.ctl-nav-item', /^Jobs/))).toBe('Jobs 1');
    expect(text(byText(host, '.ctl-nav-item', /^Overrides/))).toBe('Overrides 2');
    click(byText(host, '.ctl-tile', /Overrides on this instance/));
    await until(() => expect(text(host.querySelector('h1'))).toBe('Overrides'));
  });
});
