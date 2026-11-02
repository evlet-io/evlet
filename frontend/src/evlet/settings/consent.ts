// /_/oauth/authorize: the consent page an AI assistant sends a person to. Signed-in desk sessions only;
// the approval is a CSRF-protected POST, and the server checks the request again before it returns.
//
//   mount(element, () => ConsentPage({}));

import { html, when } from '../dom';
import { ApiClient, ApiError, type ClientOptions } from '../api';
import { cell } from '../reactive';
import { Button, Switch } from '../ui/base';

export interface ConsentInfo { client: string; returns_to: string; user: string; writes: boolean }

export class ConsentClient extends ApiClient {
  constructor(options: ClientOptions = {}) { super(options); }
  async info(query: string) { return (await this.request<ConsentInfo>('GET', '/api/_oauth/authorize?' + query)).data; }
  async answer(query: string, approve: boolean, allowChanges: boolean) {
    return (await this.request<{ redirect: string }>('POST', '/api/_oauth/authorize', { body: { query, approve, allow_changes: allowChanges } })).data;
  }
}

export function ConsentPage(options: { api?: ConsentClient; location?: Pick<Location, 'pathname' | 'search' | 'assign'> }): HTMLElement {
  const api = options.api ?? new ConsentClient();
  const place = options.location ?? window.location;
  const query = place.search.replace(/^\?/, '');
  const info = cell<ConsentInfo | null>(null), error = cell(''), busy = cell(false), changes = cell(false);
  void (async () => {
    try {
      const session = await api.session();
      if (!session.user) { place.assign('/login?next=' + encodeURIComponent(place.pathname + place.search)); return; }
      info.set(await api.info(query));
    } catch (failure) { error.set(failure instanceof ApiError ? failure.message : 'This page could not load. Try again.'); }
  })();
  const answer = async (approve: boolean) => {
    busy.set(true);
    try { place.assign((await api.answer(query, approve, changes.peek())).redirect); }
    catch (failure) { error.set(failure instanceof ApiError ? failure.message : 'Something went wrong. Try again.'); busy.set(false); }
  };
  const i = () => info.get()!;
  return html`<div class="ev-login">
    <div class="ev-login-brand"><strong>evlet</strong><span>AI assistants</span></div>
    <main class="ev-login-card ev-consent" aria-labelledby="ev-consent-title">
      ${() => error.get() ? html`<h1 id="ev-consent-title">Can't connect</h1><p class="ev-error" role="alert">${error}</p>
          ${Button({ label: 'Back to the workspace', onClick: () => place.assign('/') })}`
        : info.get() === null ? html`<p>Checking the request…</p>`
          : html`<span class="pill b-violet">AI assistant</span>
            <h1 id="ev-consent-title">Connect ${i().client}?</h1>
            <p>${i().client} will work as you, <strong>${i().user}</strong>. It sees only what you can see, and you can disconnect it any time in Settings.</p>
            <ul class="ev-consent-list"><li>Read and search your records</li>
              ${when(() => i().writes, () => html`<li>${Switch({ on: changes, label: 'Let it make changes too', onChange: next => changes.set(next) })}</li>`)}</ul>
            ${i().writes ? null : html`<p class="muted">Your admin has turned off changes, so it can only read and search.</p>`}
            <p class="muted">After you answer, you go back to <strong>${i().returns_to}</strong>.</p>
            <div class="ev-consent-actions">${Button({ label: 'Cancel', disabled: busy, onClick: () => void answer(false) })}
              ${Button({ label: 'Allow', variant: 'primary', disabled: busy, onClick: () => void answer(true) })}</div>`}
    </main>
  </div>` as HTMLElement;
}
