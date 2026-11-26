import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../evlet/api';
import { mount } from '../evlet/dom';
import { Portal, type Operation, type ProjectApi, type Status } from './portal';

const STATUS: Status = {
  id: '01hc1sd9vc1ye', created: '2026-11-26T10:00:00Z', mode: 'development', endpoint: 'http://127.0.0.1:7999',
  mcp: 'http://127.0.0.1:7999/_/mcp', two_step: false, running: null, tools: { project_status: 'operate', migrate: 'develop', build: 'develop' },
  instances: [{ name: 'acme', id: '01hc1sdbbbbbb', host: 'http://127.0.0.1:8000', version: '0.4.21', apps: { notes: '0.1.0' },
    status: 'running', health: true, port: 8000, restarts: 0, error: null }],
};
const LOG: Operation[] = [{ id: 2, kind: 'backup_create', instance: 'acme', origin: 'cli', arguments: {}, started: '2026-11-26T10:01:00Z',
  finished: '2026-11-26T10:01:01Z', result: 'ok', error: '', lines: 3 }];

function stub(signedIn = true, status: Status = STATUS): ProjectApi & { started: Array<[string, Record<string, unknown>]> } {
  const refused = () => Promise.reject(new ApiError('Sign in', 401, 'auth'));
  const started: Array<[string, Record<string, unknown>]> = [];
  return {
    started,
    session: () => Promise.resolve({ user: signedIn ? { id: 1, username: 'admin', role: 'admin' } : null }),
    logout: () => Promise.resolve(),
    status: () => signedIn ? Promise.resolve(status) : refused(),
    log: () => signedIn ? Promise.resolve({ operations: LOG }) : refused(),
    operation: id => Promise.resolve({ ...LOG[0], id, result: 'ok', output: ['Backup: /x.sqlite3', 'With: two files'] }),
    logs: () => Promise.resolve({ lines: ['INFO started'] }),
    backups: () => Promise.resolve({ backups: [{ name: '20261126T100101Z.sqlite3', bytes: 4096 }] }),
    start: (tool, body) => { started.push([tool, body]); return Promise.resolve({ operation: 3 }); },
    plan: (tool, body) => { started.push([tool, body]); return Promise.resolve({ operation: 4 }); },
  } as ProjectApi & { started: Array<[string, Record<string, unknown>]> };
}

async function render(api: ProjectApi) {
  const root = document.createElement('div');
  document.body.append(root);
  const portal = Portal(api, { refresh: 60_000 });
  const dispose = mount(root, () => portal.view);
  await vi.waitFor(() => expect(root.querySelector('[data-signed-out], [data-project-id]')).not.toBeNull());
  return { root, dispose: () => { portal.stop(); dispose(); root.remove(); } };
}

const button = (root: ParentNode, label: string) =>
  [...root.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === label)!;

afterEach(() => { document.body.innerHTML = ''; });

describe('project portal', () => {
  it('shows no ids or instances signed out', async () => {
    const { root, dispose } = await render(stub(false));
    expect(root.textContent).toContain('Sign in');
    expect(root.textContent).not.toContain(STATUS.id);
    expect(root.textContent).not.toContain('acme');
    dispose();
  });

  it('shows the project, its instances, the log and the MCP address', async () => {
    const { root, dispose } = await render(stub());
    expect(root.querySelector('[data-project-id]')!.textContent).toBe(STATUS.id);
    expect(root.querySelector('[data-mcp]')!.textContent).toBe(STATUS.mcp);
    expect(root.querySelector('[data-caddy]')).toBeNull();
    const row = root.querySelector('[data-instance="acme"]')!;
    expect(row.textContent).toContain('01hc1sdbbbbbb');
    expect(row.textContent).toContain('notes 0.1.0');
    expect(row.textContent).toContain('healthy');
    expect(root.querySelector('[data-log]')!.textContent).toContain('backup_create');
    dispose();
  });

  it('shows the Caddy push and its error', async () => {
    const api = stub();
    const caddy = { admin: 'http://localhost:2019', state: 'failed', error: 'Caddy refused PATCH /id/evlet-x (400): bad host; trying again in 5 s', pushed_at: null, hosts: 0 };
    api.status = () => Promise.resolve({ ...STATUS, caddy });
    const { root, dispose } = await render(api);
    expect(root.querySelector('[data-caddy]')!.textContent).toContain('http://localhost:2019');
    expect(root.querySelector('[data-caddy-error]')!.textContent).toContain('trying again in 5 s');
    dispose();
  });

  it('runs a tool and follows its output', async () => {
    const api = stub();
    const { root, dispose } = await render(api);
    button(root.querySelector('[data-instance="acme"]')!, 'Back up').click();
    await vi.waitFor(() => expect(document.querySelector('[data-output]')?.textContent).toContain('Backup: /x.sqlite3'));
    expect(api.started).toEqual([['backup_create', { instance: 'acme' }]]);
    dispose();
  });

  it('asks before a restore and says what is lost', async () => {
    const api = stub();
    const { root, dispose } = await render(api);
    button(root.querySelector('[data-instance="acme"]')!, 'Backups').click();
    await vi.waitFor(() => expect(button(document, 'Restore…')).toBeTruthy());
    button(document, 'Restore…').click();
    await vi.waitFor(() => expect(document.querySelector('[data-warning]')?.textContent).toContain('is lost'));
    expect(api.started).toEqual([]);
    button(document, 'Restore and lose later changes').click();
    await vi.waitFor(() => expect(api.started).toEqual([['backup_restore', { instance: 'acme', backup: '20261126T100101Z.sqlite3', confirm: true }]]));
    dispose();
  });

  it('shows the develop tools in development and hides them in production', async () => {
    const api = stub();
    const { root, dispose } = await render(api);
    expect(root.querySelector('[data-develop]')).not.toBeNull();
    button(root.querySelector('[data-develop]')!, 'Build').click();
    await vi.waitFor(() => expect(api.started).toEqual([['build', {}]]));
    dispose();
    const production = await render(stub(true, { ...STATUS, mode: 'production', tools: { project_status: 'operate' } }));
    expect(production.root.querySelector('[data-project-id]')).not.toBeNull();
    expect(production.root.querySelector('[data-develop]')).toBeNull();
    expect(production.root.textContent).not.toContain('Run tests');
    production.dispose();
  });
});
