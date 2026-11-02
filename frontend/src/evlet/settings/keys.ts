// /settings/keys: a person's own API keys. The Settings shell shows it with its own client:
//
//   MyKeysPage({ api: ctx.api });

import { ApiClient, type ClientOptions } from '../api';
import { cell } from '../reactive';
import { mountToasts } from '../ui/layers';
import type { ApiKeyRow, CreatedKey, KeyScope, KeysApi } from '../controls/client';
import { KeysPage } from '../controls/keys';
import '../controls/controls.css';

type Listing = { items: ApiKeyRow[]; models: Array<{ name: string; label: string }> };

/** /api/_settings/me/keys: signed-in sessions only; a key can't manage keys. */
export class MyKeysClient extends ApiClient implements KeysApi {
  constructor(options: ClientOptions = {}) { super(options); }
  async keys() { return (await this.request<Listing>('GET', '/api/_settings/me/keys')).data; }
  async createKey(body: { name: string; scope: KeyScope; password?: string; code?: string }) { return (await this.request<CreatedKey>('POST', '/api/_settings/me/keys', { body })).data; }
  async revokeKey(id: number) { return (await this.request<{ items: ApiKeyRow[] }>('DELETE', `/api/_settings/me/keys/${id}`)).data; }
}

/** twoStep: the person has two-step sign-in on, so a new key asks for a code instead of the password. */
export function MyKeysPage(options: { api?: ApiClient; twoStep?: boolean }): HTMLElement {
  const api = options.api ?? new MyKeysClient();
  const models = cell<Listing['models']>([]);
  // The listing also names the models this person can read; keep them for the scope choice.
  const keys: KeysApi = {
    keys: async () => { const listing = (await api.request<Listing>('GET', '/api/_settings/me/keys')).data; models.set(listing.models); return listing; },
    createKey: async body => (await api.request<CreatedKey>('POST', '/api/_settings/me/keys', { body })).data,
    revokeKey: async id => (await api.request<{ items: ApiKeyRow[] }>('DELETE', `/api/_settings/me/keys/${id}`)).data,
  };
  mountToasts();
  return KeysPage({ api: keys, models: () => models.get(), title: 'API keys', confirm: options.twoStep ? 'code' : 'password',
    sub: 'For scripts and other systems that call Evlet as you. A key can never do more than you can.',
    empty: "No keys. Most people don't need one." });
}
