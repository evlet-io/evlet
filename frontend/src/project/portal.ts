// The project portal: the owner's page on the project endpoint (docs/project-endpoint.md#portal).
//
// One static page that calls the project services (/api/_services/<tool>) the way MCP clients call them as tools.
// It shows the project id and mode, the instances, the operation log with live output and the MCP address, and a
// button for each operate tool. Signed out it shows only the way to sign in: no id, no instance.

import { ApiError, createClient } from '../evlet/api';
import { html, mount } from '../evlet/dom';
import { cell } from '../evlet/reactive';
import { Button, EmptyState, StatusPill, openModal, toast, type Tint } from '../evlet/ui';

export interface Instance { name: string; id: string | null; host: string | null; version: string | null; apps: Record<string, string>; status: string; health: boolean; port: number | null; restarts: number; error: string | null }
export interface Status { id: string; created: string; mode: string; endpoint: string; mcp: string; two_step: boolean; instances: Instance[]; running: { operation: number; kind: string } | null; caddy?: Caddy | null; tools?: Record<string, 'operate' | 'develop'> }
/** The endpoint's push of the instances' hosts to Caddy's admin API; null without one. */
export interface Caddy { admin: string; state: string; error: string | null; pushed_at: number | null; hosts: number }
export interface Operation { id: number; kind: string; instance: string; origin: string; arguments: Record<string, unknown>; started: string; finished: string | null; result: string; error: string; lines?: number; output?: string[] }

type Client = ReturnType<typeof createClient>;

/** The project services, as the portal calls them. */
export function projectApi(client: Client) {
  const get = async <T>(tool: string, query: Record<string, string | number | undefined> = {}) =>
    (await client.request<T>('GET', `/api/_services/${tool}`, { query })).data;
  const post = <T>(tool: string, body: Record<string, unknown>) => client.service<T>(tool, body);
  return {
    session: () => client.session(),
    logout: () => client.logout(),
    status: () => get<Status>('project_status'),
    log: () => get<{ operations: Operation[] }>('operation_log', { limit: 30 }),
    operation: (id: number) => get<Operation>('operation_status', { operation: id }),
    logs: (instance: string) => get<{ lines: string[] }>('instance_logs', { instance, lines: 400 }),
    backups: (instance: string) => get<{ backups: { name: string; bytes: number }[] }>('backup_list', { instance }),
    start: (tool: string, body: Record<string, unknown>) => post<{ operation: number }>(tool, body),
    plan: (tool: string, query: Record<string, string>) => get<{ operation: number }>(tool, query),
  };
}
export type ProjectApi = ReturnType<typeof projectApi>;

const TINT: Record<string, Tint> = { running: 'green', ok: 'green', started: 'blue', starting: 'blue', restarting: 'amber', failed: 'rose', stopped: 'slate' };
export const tintOf = (state: string): Tint => TINT[state] ?? 'slate';
/** Whether the develop tools are available now: the project is in development (project_status lists them). */
export const develops = (status: Status) => Object.values(status.tools ?? {}).includes('develop');
const message = (error: unknown) => error instanceof ApiError ? (Object.values(error.fields)[0] ?? error.message) : String(error);

/** The portal. refresh is how often the status and the log are read again, in milliseconds. */
export function Portal(api: ProjectApi, options: { refresh?: number } = {}) {
  const signedIn = cell<boolean | null>(null);
  const status = cell<Status | null>(null);
  const log = cell<Operation[]>([]);
  const problem = cell('');

  async function load() {
    try {
      const [next, entries] = await Promise.all([api.status(), api.log()]);
      status.set(next); log.set(entries.operations); problem.set(''); signedIn.set(true);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.kind === 'auth')) { signedIn.set(false); status.set(null); log.set([]); return; }
      problem.set(message(error));
    }
  }

  async function begin(tool: string, body: Record<string, unknown>, plan = false) {
    try {
      const started = plan ? await api.plan(tool, body as Record<string, string>) : await api.start(tool, body);
      await load();
      follow(started.operation);
    } catch (error) { toast(message(error), { tone: 'error' }); }
  }

  /** The output of one operation, read again every second while it runs. */
  function follow(id: number) {
    const current = cell<Operation | null>(null);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      try {
        const found = await api.operation(id);
        current.set(found);
        if (found.result === 'running') timer = setTimeout(read, 1000); else void load();
      } catch (error) { problem.set(message(error)); }
    };
    void read();
    openModal({ className: 'pp-wide',
      title: () => current.get() ? `${current.get()!.kind} ${current.get()!.instance}`.trim() : `Operation ${id}`, icon: 'list',
      body: () => html`<div class="pp-op">
        <p>${() => current.get() ? StatusPill({ label: current.get()!.result, tint: tintOf(current.get()!.result) }) : 'Reading…'}
          <span class="pp-muted">${() => current.get() ? `${current.get()!.origin} · ${current.get()!.started}` : ''}</span></p>
        <pre class="pp-output" data-output>${() => (current.get()?.output ?? []).join('\n')}</pre>
        ${() => current.get()?.error ? html`<p class="pp-error">${current.get()!.error}</p>` : null}</div>`,
      onClose: () => clearTimeout(timer),
    });
  }

  function lines(instance: string) {
    const shown = cell<string[]>([]);
    void api.logs(instance).then(found => shown.set(found.lines), error => shown.set([message(error)]));
    openModal({ className: 'pp-wide', title: `${instance}: process output`, icon: 'list', body: () => html`<div class="pp-op">
      <p class="pp-muted">The last lines the instance printed. They can hold values from tracebacks.</p>
      <pre class="pp-output">${() => shown.get().join('\n')}</pre></div>` });
  }

  function restore(instance: string) {
    const backups = cell<{ name: string; bytes: number }[] | null>(null);
    void api.backups(instance).then(found => backups.set(found.backups), error => { backups.set([]); problem.set(message(error)); });
    const close = openModal({ title: `${instance}: backups`, icon: 'history', body: () => html`<div class="pp-op">
      ${() => backups.get() === null ? 'Reading…' : backups.get()!.length === 0 ? 'No backups yet.' : html`<ul class="pp-list">${backups.get()!.map(item => html`<li>
        <span>${item.name}</span><span class="pp-muted">${Math.round(item.bytes / 1024)} KB</span>
        ${Button({ label: 'Restore…', size: 'small', variant: 'soft-danger', onClick: () => { close(); confirmRestore(instance, item.name); } })}</li>`)}</ul>`}</div>` });
  }

  function confirmRestore(instance: string, backup: string) {
    openModal({ title: `Restore ${instance}?`, icon: 'circle-alert', tint: 'rose', narrow: true,
      body: () => html`<p data-warning>Restoring ${backup} replaces every database file of ${instance}. Every change made in ${instance}
        since that backup was written is lost. ${instance} stops for the restore and starts again.</p>`,
      footer: close => [Button({ label: 'Cancel', onClick: close }),
        Button({ label: 'Restore and lose later changes', variant: 'danger', onClick: () => { close(); void begin('backup_restore', { instance, backup, confirm: true }); } })] });
  }

  function form(title: string, fields: { name: string; label: string; placeholder: string; value?: string }[], actions: { label: string; run: (values: Record<string, string>) => void; danger?: boolean }[]) {
    const values: Record<string, string> = Object.fromEntries(fields.map(field => [field.name, field.value ?? '']));
    openModal({ title, narrow: true, body: () => html`<div class="pp-form">${fields.map(field => html`<label>${field.label}
        <input class="input" name=${field.name} placeholder=${field.placeholder} value=${values[field.name]} on:input=${(event: Event) => { values[field.name] = (event.target as HTMLInputElement).value; }}></label>`)}</div>`,
      footer: close => actions.map(action => Button({ label: action.label, variant: action.danger ? 'danger' : 'default', onClick: () => { close(); action.run(values); } })) });
  }

  const instanceRow = (item: Instance) => html`<tr data-instance=${item.name}>
    <td><strong>${item.name}</strong><div class="pp-id">${item.id ?? 'no id yet'}</div></td>
    <td>${item.host ? html`<a href=${item.host} target="_blank" rel="noopener">${item.host}</a>` : '—'}</td>
    <td>${item.version ?? '—'}</td>
    <td>${Object.entries(item.apps).map(([name, version]) => `${name} ${version}`).join(', ') || 'none'}</td>
    <td>${StatusPill({ label: item.health ? 'healthy' : item.status, tint: item.health ? 'green' : tintOf(item.status) })}${item.restarts ? html`<span class="pp-muted"> ${item.restarts} restarts</span>` : null}</td>
    <td class="pp-actions">
      ${item.status === 'stopped' ? Button({ label: 'Start', size: 'small', onClick: () => void begin('instance_start', { instance: item.name }) })
        : Button({ label: 'Stop', size: 'small', onClick: () => void begin('instance_stop', { instance: item.name }) })}
      ${Button({ label: 'Restart', size: 'small', onClick: () => void begin('instance_restart', { instance: item.name }) })}
      ${Button({ label: 'Output', size: 'small', onClick: () => lines(item.name) })}
      ${Button({ label: 'Back up', size: 'small', onClick: () => void begin('backup_create', { instance: item.name }) })}
      ${Button({ label: 'Backups', size: 'small', onClick: () => restore(item.name) })}
      ${Button({ label: 'Update app', size: 'small', onClick: () => form(`Update an app in ${item.name}`, [
        { name: 'app', label: 'App', placeholder: 'notes', value: Object.keys(item.apps)[0] }, { name: 'ref', label: 'Tag, branch or commit', placeholder: 'v1.2.0' }], [
        { label: 'Plan', run: values => void begin('app_update_plan', { instance: item.name, ...values }, true) },
        { label: 'Update', run: values => void begin('app_update', { instance: item.name, ...values }) }]) })}
      ${Button({ label: 'Install app', size: 'small', onClick: () => form(`Install an app in ${item.name}`, [
        { name: 'source', label: 'Git URL, or an app in apps/ (development)', placeholder: 'https://github.com/you/notes.git' }], [
        { label: 'Install', run: values => void begin('app_install', { instance: item.name, ...values }) }]) })}
    </td></tr>`;

  const logRow = (item: Operation) => html`<tr class="pp-row" tabindex="0" on:click=${() => follow(item.id)} on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') follow(item.id); }}>
    <td>${item.id}</td><td>${item.kind}</td><td>${item.instance || 'project'}</td><td>${item.origin}</td>
    <td>${StatusPill({ label: item.result, tint: tintOf(item.result) })}</td><td>${item.started}</td><td>${item.finished ?? ''}</td></tr>`;

  const caddyCard = (caddy: Caddy) => html`<section class="pp-card" data-caddy><h2>Caddy</h2>
    <p>${StatusPill({ label: caddy.state, tint: tintOf(caddy.state) })}
      <span class="pp-muted">Admin API <code>${caddy.admin}</code> · ${caddy.hosts} host${caddy.hosts === 1 ? '' : 's'} routed</span></p>
    ${caddy.error ? html`<p class="pp-error" data-caddy-error>${caddy.error}</p>` : ''}</section>`;

  const signedOut = () => html`<main class="pp" data-signed-out>
    ${EmptyState({ icon: 'lock', title: 'Project endpoint', text: 'Sign in as the owner to see and run the project. In development, evlet endpoint link prints a one-time link.',
      action: html`<a class="btn primary" href="/login">Sign in</a>` as HTMLElement })}</main>`;

  const page = (current: Status) => html`<main class="pp">
    <header class="pp-head"><div><h1>Project <span class="pp-id" data-project-id>${current.id}</span></h1>
      <p>${StatusPill({ label: current.mode, tint: current.mode === 'production' ? 'violet' : 'blue' })}
        <span class="pp-muted">Two-step sign-in ${current.two_step ? 'on' : 'off'} · created ${current.created}</span></p></div>
      <div class="pp-actions">${Button({ label: 'Refresh', onClick: () => void load() })}
        ${Button({ label: 'Sign out', variant: 'ghost', onClick: () => void api.logout().then(() => signedIn.set(false)) })}</div></header>
    <section class="pp-card"><h2>MCP</h2><p>Connect an MCP client to <code data-mcp>${current.mcp}</code>; it signs in as you.</p></section>
    ${current.caddy ? caddyCard(current.caddy) : ''}
    <section class="pp-card"><h2>Instances</h2>
      ${current.instances.length ? html`<table class="pp-table"><thead><tr><th>Instance</th><th>Host</th><th>Evlet</th><th>Apps</th><th>Status</th><th></th></tr></thead>
        <tbody>${current.instances.map(instanceRow)}</tbody></table>` : html`<p>No instances yet: evlet instance NAME.</p>`}
      <div class="pp-actions">${Button({ label: 'Plan Evlet upgrade', onClick: () => void begin('evlet_upgrade_plan', {}, true) })}
        ${Button({ label: 'Upgrade Evlet', onClick: () => form('Upgrade Evlet', [{ name: 'to', label: 'Version (empty: the newest of this minor)', placeholder: '0.4.22' }], [
          { label: 'Upgrade', run: values => void begin('evlet_upgrade', values), danger: true }]) })}</div></section>
    ${develops(current) ? html`<section class="pp-card" data-develop><h2>Develop</h2>
      <p class="pp-muted">Development mode: change the project's apps. In production these tools are gone.</p>
      <div class="pp-actions">${Button({ label: 'Build', onClick: () => void begin('build', {}) })}
        ${Button({ label: 'Run tests', onClick: () => form('Run tests', [{ name: 'target', label: 'App or test path in apps/ (empty: all)', placeholder: 'notes' }], [
          { label: 'Run', run: values => void begin('tests_run', values) }]) })}
        ${Button({ label: 'Make migration', onClick: () => form('Make a migration', [
          { name: 'app', label: 'App', placeholder: 'notes' }, { name: 'instance', label: 'Instance', placeholder: 'acme', value: current.instances[0]?.name }], [
          { label: 'Make', run: values => void begin('makemigration', values) }]) })}
        ${Button({ label: 'Migrate', onClick: () => form('Migrate an instance', [{ name: 'instance', label: 'Instance', placeholder: 'acme', value: current.instances[0]?.name }], [
          { label: 'Migrate', run: values => void begin('migrate', values) }]) })}</div></section>` : null}
    <section class="pp-card"><h2>Operation log</h2>
      ${() => log.get().length ? html`<table class="pp-table" data-log><thead><tr><th>#</th><th>Operation</th><th>Instance</th><th>From</th><th>Result</th><th>Started</th><th>Ended</th></tr></thead>
        <tbody>${log.get().map(logRow)}</tbody></table>` : html`<p>No operations yet.</p>`}</section>
  </main>`;

  void api.session().then(() => load(), () => signedIn.set(false));
  const timer = setInterval(() => { if (signedIn.get()) void load(); }, options.refresh ?? 5000);
  const view = html`<div class="pp-root">${() => problem.get() ? html`<p class="pp-error" role="alert">${problem.get()}</p>` : null}
    ${() => signedIn.get() === false ? signedOut() : status.get() ? page(status.get()!) : html`<main class="pp"><p>Loading…</p></main>`}</div>`;
  return { view, load, stop: () => clearInterval(timer) };
}

export function start(root: Element) {
  const portal = Portal(projectApi(createClient()));
  return mount(root, () => portal.view);
}
