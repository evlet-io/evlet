// Sign in and first-run setup. Setup creates the first administrator, then signs in with it.

import { html, when } from '../dom';
import { cell, type Cell } from '../reactive';
import type { Session } from '../api';
import { Button } from '../ui/base';
import { TwoStepSetup } from './settings';

export interface LoginProps {
  session: Session | null;
  /** The message to show (a refused sign-in, an ended session, a connection problem). */
  error: Cell<string>;
  busy: Cell<boolean>;
  onSubmit: (username: string, password: string, setup: boolean) => void;
  onRetry: () => void;
}

/** The Evlet mark: three bars on the accent square. Shared by sign-in and the sidebar. */
export const Logo = () => html`<span class="ev-logo" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M6 6h12M6 12h9M6 18h12" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg></span>`;

export function LoginScreen(props: LoginProps): HTMLElement {
  const application = props.session?.application ?? {};
  const setup = Boolean(props.session?.setup);
  const place = application.playground ? 'your Playground' : application.name || 'your workspace';
  const tenant = (props.session?.tenant as { label?: string } | undefined)?.label;
  const username = cell(''), password = cell('');
  const submit = (event: Event) => {
    event.preventDefault();
    if (props.busy.peek()) return;
    props.onSubmit(username.peek().trim(), password.peek(), setup);
  };
  const label = () => props.busy.get() ? (setup ? 'Creating…' : 'Signing in…') : setup ? 'Create administrator' : 'Sign in';
  return html`<div class="ev-login">
    <div class="ev-login-brand">${Logo()}<strong>evlet</strong>
      <span>${application.playground ? 'Playground' : application.name || 'Your workspace'}</span></div>
    <main class="ev-login-card" aria-labelledby="ev-login-title">
      <span class="pill b-blue">${tenant || 'Your workspace'}</span>
      <h1 id="ev-login-title">${setup ? `Set up ${place}` : 'Welcome back'}</h1>
      <p>${setup ? 'Create the first administrator account.' : 'Sign in to your Evlet workspace.'}</p>
      <form on:submit=${submit}>
        <label for="ev-username">Username</label>
        <input id="ev-username" class="input" autocomplete="username" required minlength="3" maxlength="64"
          prop:value=${username} disabled=${props.busy} on:input=${(event: Event) => username.set((event.target as HTMLInputElement).value)}>
        <label for="ev-password">Password</label>
        <input id="ev-password" class="input" type="password" autocomplete=${setup ? 'new-password' : 'current-password'} required
          minlength=${setup ? '15' : null} maxlength="128" prop:value=${password} disabled=${props.busy}
          aria-describedby=${setup ? 'ev-password-hint' : null}
          on:input=${(event: Event) => password.set((event.target as HTMLInputElement).value)}>
        ${setup ? html`<small id="ev-password-hint">Use at least 15 characters.</small>` : null}
        ${when(() => Boolean(props.error.get()), () => html`<p class="ev-error" role="alert">${() => props.error.get()}</p>`)}
        ${Button({ label, variant: 'primary', type: 'submit', icon: 'arrow-right', disabled: props.busy, className: 'ev-login-submit' })}
      </form>
      ${when(() => Boolean(props.error.get()), () => html`<button type="button" class="link" on:click=${props.onRetry}>Check connection</button>`)}
      <footer>Simple tools. Your application.</footer>
    </main>
    <p class="ev-login-foot">Evlet · Python applications, thoughtfully put together.</p>
  </div>` as HTMLElement;
}

/** What login answers when the account needs a second step. */
export interface Challenge { two_step: 'code' | 'setup'; ticket: string; secret?: string; uri?: string }

/** The second step of signing in: a code from the authenticator app (or a backup code), or setting the app up. */
export function TwoStepScreen(props: { session: Session | null; challenge: Challenge; error: Cell<string>; busy: Cell<boolean>; onSubmit: (code: string) => void; onCancel: () => void }): HTMLElement {
  const code = cell(''), local = cell('');
  const setup = props.challenge.two_step === 'setup';
  const submit = (event: Event) => {
    event.preventDefault();
    if (props.busy.peek()) return;
    const value = code.peek().trim();
    if (setup && !/^\d{6}$/.test(value)) { local.set('Enter the 6 digits from the app.'); return; }
    if (!value) { local.set('Enter the code from your authenticator app.'); return; }
    props.onSubmit(value);
  };
  const message = () => local.get() || props.error.get();
  return html`<div class="ev-login">
    <div class="ev-login-brand">${Logo()}<strong>evlet</strong><span>${props.session?.application?.name || 'Your workspace'}</span></div>
    <main class="ev-login-card" aria-labelledby="ev-login-title">
      <h1 id="ev-login-title">${setup ? 'Set up two-step sign-in' : 'Two-step sign-in'}</h1>
      <p>${setup ? 'Your role needs a code from an authenticator app, on top of your password.' : 'Enter the 6-digit code from your authenticator app, or one of your backup codes.'}</p>
      <form on:submit=${submit}>
        ${setup ? TwoStepSetup(props.challenge.secret ?? '', code, local) : html`<label for="tfa-code">Code</label><input id="tfa-code" class="input" autocomplete="one-time-code" maxlength="9"
          prop:value=${code} disabled=${props.busy} on:input=${(event: Event) => { code.set((event.target as HTMLInputElement).value); local.set(''); }}>`}
        ${when(() => Boolean(message()) && !(setup && local.get()), () => html`<p class="ev-error" role="alert">${message}</p>`)}
        ${Button({ label: () => props.busy.get() ? 'Checking…' : setup ? 'Turn on and sign in' : 'Sign in', variant: 'primary', type: 'submit', icon: 'arrow-right', disabled: props.busy, className: 'ev-login-submit' })}
      </form>
      <button type="button" class="link" on:click=${props.onCancel}>Use another account</button>
    </main></div>` as HTMLElement;
}

/** Shown while the session is read. */
export function Connecting(): HTMLElement {
  return html`<div class="ev-connecting" role="status">${Logo()}<p>Connecting to Evlet…</p></div>` as HTMLElement;
}
