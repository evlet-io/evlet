import { afterEach, describe, expect, it } from 'vitest';
import { createClient } from '../api';
import { readConfig } from '../app/config';
import { appRouter } from '../app/context';
import { server } from './server';

function block(value: unknown) {
  const node = document.createElement('script');
  node.id = 'evlet-config';
  node.type = 'application/json';
  node.textContent = typeof value === 'string' ? value : JSON.stringify(value);
  document.head.append(node);
}
afterEach(() => { document.getElementById('evlet-config')?.remove(); });

describe('runtime config', () => {
  it('reads base, api and docs from the config block', () => {
    block({ frontend: 'crm.crm', base: '/crm', api: '/api', realm: 'desk', host: 'erp.acme.com', version: '0.2.0', docs: '/help' });
    expect(readConfig()).toMatchObject({ frontend: 'crm.crm', base: '/crm', docs: '/help', host: 'erp.acme.com' });
    const router = appRouter();
    expect(router.href('record', { model: 'deal', id: '7' })).toBe('/crm/deal/7');
    expect(router.match('/crm/deal/new')?.name).toBe('create');
    expect(router.match('/app/deal')).toBeNull();
    expect(router.href('topic', { topic: 'routes' })).toBe('/help/routes');
    expect(router.match('/login')?.name).toBe('login');
  });

  it('keeps the desk defaults without a block and refuses other origins', () => {
    expect(readConfig()).toMatchObject({ base: '/app', api: '/api', docs: '/docs' });
    expect(appRouter().href('list', { model: 'invoice' })).toBe('/app/invoice');
    block('{not json');
    expect(readConfig().base).toBe('/app');
    document.getElementById('evlet-config')!.textContent = JSON.stringify({ base: '//evil.example', api: 'https://evil.example/api', docs: null });
    expect(readConfig()).toMatchObject({ base: '/app', api: '/api', docs: null });
  });

  it('sends API calls under the configured api path', async () => {
    const { fetch, sent } = server([['GET', /\/v2\/auth\/session$/, 'session_anonymous']]);
    const api = createClient({ fetch, api: '/v2' });
    await api.session();
    expect(sent[0].url).toBe('/v2/auth/session');
    expect(api.url('/api/invoice/1/attachments/2')).toBe('/v2/invoice/1/attachments/2');
  });
});
