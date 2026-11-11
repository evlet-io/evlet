// Sign in and first-run setup. Setup creates the first administrator, then signs in with it. An account
// without a password yet (the built-in Administrator after a development link) chooses one first.

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

/** The Evlet mark, the folded leaf from evlet.io, served from /assets (frontend/public/assets). Shared by sign-in, the sidebar and Controls. */
export const Logo = (label = '') => html`<img class="ev-logo" src="/assets/evlet-icon-blue.svg" alt=${label} width="351" height="351">`;
/** The evlet.io horizontal logo, and the same artwork with a white wordmark that dark themes show instead. */
export const Lockup = () => html`<span class="ev-lockup"><img class="ev-lockup-light" src="/assets/evlet-logo-primary.svg" alt="Evlet" width="1126" height="351"><img
  class="ev-lockup-dark" src="/assets/evlet-logo-white.svg" alt="Evlet" width="1126" height="351"></span>`;

export function LoginScreen(props: LoginProps): HTMLElement {
  const application = props.session?.application ?? {};
  const setup = Boolean(props.session?.setup);
  const place = application.playground ? 'your Playground' : application.name || 'your workspace';
  const tenant = (props.session?.tenant as { label?: string } | undefined)?.label;
  const minimum = props.session?.password_min ?? 15;
  // Security(admin_sign_in=True) fills in the administrator; /login?user=NAME fills in that name.
  const asked = new URLSearchParams(window.location.search).get('user') ?? '';
  const start = setup ? '' : props.session?.sign_in?.username || (/^[A-Za-z0-9_.-]{3,64}$/.test(asked) ? asked : '');
  const username = cell(start), password = cell('');
  const submit = (event: Event) => {
    event.preventDefault();
    if (props.busy.peek()) return;
    props.onSubmit(username.peek().trim(), password.peek(), setup);
  };
  const label = () => props.busy.get() ? (setup ? 'Creating…' : 'Signing in…') : setup ? 'Create administrator' : 'Sign in';
  return html`<div class="ev-login">
    <div class="ev-login-brand">${Lockup()}
      <span>${application.playground ? 'Playground' : application.name || 'Your workspace'}</span></div>
    <main class="ev-login-card" aria-labelledby="ev-login-title">
      <span class="pill b-blue">${tenant || 'Your workspace'}</span>
      <h1 id="ev-login-title">${setup ? `Set up ${place}` : 'Welcome back'}</h1>
      <p>${setup ? 'Create the first administrator account.' : 'Sign in to your Evlet workspace.'}</p>
      <form on:submit=${submit}>
        <label for="ev-username">Username</label>
        <input id="ev-username" class="input" autocomplete="username" required minlength="3" maxlength="64" autofocus=${start ? null : ''}
          prop:value=${username} disabled=${props.busy} on:input=${(event: Event) => username.set((event.target as HTMLInputElement).value)}>
        <label for="ev-password">Password</label>
        <input id="ev-password" class="input" type="password" autocomplete=${setup ? 'new-password' : 'current-password'} required
          minlength=${setup ? String(minimum) : null} maxlength="128" prop:value=${password} disabled=${props.busy} autofocus=${start ? '' : null}
          aria-describedby=${setup ? 'ev-password-hint' : null}
          on:input=${(event: Event) => password.set((event.target as HTMLInputElement).value)}>
        ${setup ? html`<small id="ev-password-hint">${minimum <= 1 ? 'Any password will do.' : `Use at least ${minimum} characters.`}</small>` : null}
        ${when(() => Boolean(props.error.get()), () => html`<p class="ev-error" role="alert">${() => props.error.get()}</p>`)}
        ${Button({ label, variant: 'primary', type: 'submit', icon: 'arrow-right', disabled: props.busy, className: 'ev-login-submit' })}
      </form>
      ${when(() => Boolean(props.error.get()), () => html`<button type="button" class="link" on:click=${props.onRetry}>Check connection</button>`)}
      <footer>Simple tools. Your application.</footer>
    </main>
    <p class="ev-login-foot">Evlet · Python applications, thoughtfully put together.</p>
  </div>` as HTMLElement;
}

/** The first password of an account that has none, such as the built-in Administrator after a development sign-in link. */
export function SetPasswordScreen(props: { session: Session | null; error: Cell<string>; busy: Cell<boolean>; onSubmit: (password: string) => void; onCancel: () => void }): HTMLElement {
  const minimum = props.session?.password_min ?? 1;
  const password = cell(''), again = cell(''), local = cell('');
  const submit = (event: Event) => {
    event.preventDefault();
    if (props.busy.peek()) return;
    if (password.peek().length < Math.max(minimum, 1)) { local.set(minimum <= 1 ? 'Enter a password.' : `Use at least ${minimum} characters.`); return; }
    if (password.peek() !== again.peek()) { local.set("The two passwords don't match."); return; }
    props.onSubmit(password.peek());
  };
  const message = () => local.get() || props.error.get();
  const name = props.session?.user?.username ?? '';
  return html`<div class="ev-login">
    <div class="ev-login-brand">${Lockup()}<span>${props.session?.application?.name || 'Your workspace'}</span></div>
    <main class="ev-login-card" aria-labelledby="ev-login-title">
      <h1 id="ev-login-title">Set your password</h1>
      <p>${`You are signed in as ${name}. Choose a password to use from now on.`}</p>
      <form on:submit=${submit}>
        <label for="ev-new-password">New password</label>
        <input id="ev-new-password" class="input" type="password" autocomplete="new-password" required maxlength="128" autofocus
          minlength=${String(Math.max(minimum, 1))} aria-describedby="ev-new-password-hint" prop:value=${password} disabled=${props.busy}
          on:input=${(event: Event) => { password.set((event.target as HTMLInputElement).value); local.set(''); }}>
        <small id="ev-new-password-hint">${minimum <= 1 ? 'Any password will do.' : `Use at least ${minimum} characters.`}</small>
        <label for="ev-again-password">Repeat it</label>
        <input id="ev-again-password" class="input" type="password" autocomplete="new-password" required maxlength="128" prop:value=${again} disabled=${props.busy}
          on:input=${(event: Event) => { again.set((event.target as HTMLInputElement).value); local.set(''); }}>
        ${when(() => Boolean(message()), () => html`<p class="ev-error" role="alert">${message}</p>`)}
        ${Button({ label: () => props.busy.get() ? 'Saving…' : 'Save and continue', variant: 'primary', type: 'submit', icon: 'arrow-right', disabled: props.busy, className: 'ev-login-submit' })}
      </form>
      <button type="button" class="link" on:click=${props.onCancel}>Sign out</button>
    </main></div>` as HTMLElement;
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
    <div class="ev-login-brand">${Lockup()}<span>${props.session?.application?.name || 'Your workspace'}</span></div>
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
