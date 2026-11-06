// Apps, Routing, Jobs, Overrides and the Audit log. Apps and Routing are read only: migrations run
// from the server shell. Schedule pauses, Run now and Retry save at once; Reset goes through the change bar.

import { html, when, type Child } from '../dom';
import { cell, derive } from '../reactive';
import { Button, Icon, StatusPill, Switch } from '../ui/base';
import { exactTime, RelativeTime, relativeTime } from '../ui/time';
import { DialogFooter, openDrawer, openModal, toast } from '../ui/layers';
import { Callout, Head, plural, stamp, type ControlsContext } from './app';
import type { AppInfo, AuditRow, CodeFile, HostCheck, Jobs, OverrideItem, Routing } from './client';
import { describe, idOf } from './drafts';
import { message, Select } from './people';
import { UnsavedSection } from './unsaved';

/** How many recent runs GET /api/_controls/jobs sends (PAGE in evlet/controls_system.py). */
const RECENT = 50;

const copy = (text: string) => void navigator.clipboard?.writeText(text).then(() => toast('Copied'), () => toast('Copy it by hand', { icon: 'info' }));
const CodeBlock = (file: string, code: string) => html`<div class="ctl-codefile"><div class="ctl-line"><span class="mono muted ctl-small grow">${file}</span>
  ${Button({ label: 'Copy', icon: 'copy', size: 'small', onClick: () => copy(code) })}</div><pre class="ctl-code">${code}</pre></div>`;
const Command = (text: string) => CodeBlock('On the server', text);

export function openCode(title: string, sub: string, files: CodeFile[]) {
  openModal({ title, icon: 'file-text', tint: 'blue', className: 'ctl-review',
    body: () => html`<div class="ctl-form"><p class="muted ctl-small">${sub}</p>
      ${files.length ? files.map(item => CodeBlock(item.file, item.code)) : html`<div class="ctl-empty">Nothing to export.</div>`}</div>`,
    footer: close => DialogFooter({ submit: { label: 'Done', onClick: close } }) });
}

// ---------- apps ----------

const appStatus = (app: AppInfo) => app.update ? StatusPill({ label: app.update.error ? "Can't read source" : 'Migration waiting', tint: 'amber' })
  : app.restart ? StatusPill({ label: 'Restart to load', tint: 'blue' }) : StatusPill({ label: 'Up to date', tint: 'green' });

export function AppsPage(ctx: ControlsContext): HTMLElement {
  const s = () => ctx.state.get();
  const slug = () => s().instance.slug ?? '<instance>';
  const install = () => openModal({ title: 'Install an app', icon: 'package', tint: 'blue', narrow: true,
    body: () => html`<div class="ctl-form"><p class="ctl-confirm">Apps run code on the server, so they are installed from the server shell, not from the browser. Installing checks the app, backs up the database and runs its migrations.</p>
      ${Command(`evlet install <app> --instance ${slug()} --trust`)}
      <p class="muted ctl-small">The app shows up here, with its models, routes and connectors, after the instance restarts.</p></div>`,
    footer: close => DialogFooter({ submit: { label: 'Got it', onClick: close } }) });
  return html`<div>
    ${Head('Apps', 'Apps installed on this instance. Models, frontends, permissions and connectors are loaded from them on every start.',
      Button({ label: 'Install app', icon: 'plus', variant: 'primary', onClick: install }))}
    ${() => s().apps.length ? html`<div class="ctl-grid g2">${s().apps.map(app => html`<button type="button" class="ctl-card ctl-app" on:click=${() => openApp(ctx, app)}>
      <span class="ctl-line"><span class="ph-icon b-blue ctl-ic">${Icon('package', { size: 'sm' })}</span>
        <span class="grow"><strong>${app.title}</strong> <span class="ctl-badge">${app.version}</span><span class="ctl-sub mono">${app.source}</span></span>${appStatus(app)}</span>
      <span class="ctl-line muted ctl-small"><span><strong>${app.models.length}</strong> ${app.models.length === 1 ? 'model' : 'models'}</span>
        <span><strong>${app.frontends.length}</strong> frontends</span><span><strong>${app.connectors.length}</strong> connectors</span>
        <span><strong>${app.permissions}</strong> permission rules</span></span>
      ${app.frontends.length ? html`<span class="ctl-chips">${app.frontends.map(item => html`<span class="ctl-chip">${item.path}</span>`)}</span>` : null}</button>`)}</div>`
      : html`<section class="ctl-card"><div class="ctl-empty">${s().instance.managed ? 'No apps are installed yet.' : 'This instance is built in Python, not installed with evlet install, so it has no apps to list.'}</div></section>`}
  </div>` as HTMLElement;
}

function openApp(ctx: ControlsContext, app: AppInfo) {
  const slug = ctx.state.peek().instance.slug ?? '<instance>';
  openDrawer({ title: `${app.title} ${app.version}`, icon: 'package', tint: 'blue', className: 'ctl-drawer',
    body: () => html`<div class="ctl-form">
      <p class="mono muted ctl-small">${app.source}</p>
      ${app.update?.error ? Callout('err', 'info', app.update.error) : app.update ? html`<div class="ctl-form">
        ${Callout('warn', 'info', `Version ${app.update.to} is waiting to be migrated. Migrations run from the server shell, never from the browser. Stop the server first: Evlet never changes the database under a running server.`)}
        ${Command(`evlet migrate ${slug}`)}
        <p class="muted ctl-small">It takes a backup into data/backups first, then runs the migrations. Start the server again afterwards.</p></div>`
        : app.restart ? Callout('info', 'info', 'The database is migrated. Restart the server to load the new version.') : null}
      <section class="ctl-section"><h3>Models</h3>${app.models.length ? html`<div class="ctl-chips">${app.models.map(name =>
        html`<a class="ctl-chip" href=${ctx.router.href('models', {}, { model: name })}>${name}</a>`)}</div>` : html`<p class="muted ctl-small">None.</p>`}</section>
      <section class="ctl-section"><h3>Frontends</h3>${app.frontends.length ? html`<div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Name</th><th>Served at</th><th>Sign-in</th></tr></thead>
        <tbody>${app.frontends.map(item => html`<tr><td class="mono">${item.name}</td><td class="mono">${item.host}${item.path}</td><td>${item.realm}</td></tr>`)}</tbody></table></div>`
        : html`<p class="muted ctl-small">None.</p>`}</section>
      <section class="ctl-section"><h3>Connectors</h3>${app.connectors.length ? html`<div class="ctl-chips">${app.connectors.map(name => html`<span class="ctl-chip">${name}</span>`)}</div>`
        : html`<p class="muted ctl-small">None.</p>`}</section>
      <section class="ctl-section"><h3>Schedules</h3>${app.schedules.length ? html`<div class="ctl-chips">${app.schedules.map(name => html`<span class="ctl-chip mono">${name}</span>`)}</div>`
        : html`<p class="muted ctl-small">None.</p>`}</section></div>`,
    footer: close => DialogFooter({ submit: { label: 'Close', onClick: close } }) });
}

// ---------- routing ----------

const SOURCES: Record<string, () => Node> = {
  locked: () => html`<span class="ctl-line muted">${Icon('lock', { size: 'xs' })}Locked</span>`,
  framework: () => html`<span class="ctl-src s-file"><i></i>Framework</span>`,
  'routes.py': () => html`<span class="ctl-src s-ov"><i></i>routes.py</span>`,
};
const source = (value: string) => (SOURCES[value] ?? (() => html`<span class="ctl-src s-app"><i></i>${value.replace(/^app /, 'App ')}</span>`))();

export function RoutingPage(ctx: ControlsContext): HTMLElement {
  const routing = cell<Routing | null>(null), checks = cell<Map<string, HostCheck>>(new Map()), notes = cell<string[]>([]), checking = cell(false);
  void ctx.api.routing().then(value => routing.set(value), failure => toast(message(failure), { icon: 'info' }));
  const check = async () => {
    checking.set(true);
    try {
      const result = await ctx.api.checkHosts();
      checks.set(new Map(result.hosts.map(item => [item.host, item])));
      notes.set(result.notes);
      toast(result.ok ? 'Every host points here' : 'Some hosts need attention', { icon: result.ok ? 'check' : 'info' });
    } catch (failure) { toast(message(failure), { icon: 'info' }); } finally { checking.set(false); }
  };
  const proxy = () => {
    const value = routing.peek()!.proxy!;
    const kind = cell<'caddy' | 'nginx'>('caddy');
    openModal({ title: 'Proxy config', icon: 'file-text', tint: 'blue', className: 'ctl-review',
      body: () => html`<div class="ctl-form"><p class="muted ctl-small">Generated from the resolved hosts, the same as <code>evlet proxy</code>. Write it on the server and reload the proxy yourself.</p>
        <div class="seg" role="tablist"><button type="button" class:on=${() => kind.get() === 'caddy'} on:click=${() => kind.set('caddy')}>Caddy</button>
          <button type="button" class:on=${() => kind.get() === 'nginx'} on:click=${() => kind.set('nginx')}>nginx</button></div>
        ${() => CodeBlock(`evlet proxy ${ctx.state.peek().instance.slug ?? '<instance>'} --${kind.get()}`, value[kind.get()])}</div>`,
      footer: close => DialogFooter({ submit: { label: 'Done', onClick: close } }) });
  };
  const dns = (item?: HostCheck) => !item ? html`<span class="muted">Not checked</span>` : item.dns === 'points here' ? StatusPill({ label: 'Points here', tint: 'green' })
    : StatusPill({ label: item.dns === 'points elsewhere' ? 'Points elsewhere' : 'No record', tint: 'rose' });
  const cert = (item?: HostCheck) => !item ? null : item.certificate === 'valid' ? StatusPill({ label: 'Valid', tint: 'green' })
    : item.certificate === 'missing' ? StatusPill({ label: 'No valid certificate', tint: 'rose' }) : StatusPill({ label: 'Waiting for DNS', tint: 'amber' });
  return html`<div>
    ${() => Head('Routing', 'Hosts and paths resolved at start-up from the installed apps and routes.py.', routing.get() ? [
      Button({ label: 'Proxy config', icon: 'file-text', disabled: !routing.get()!.proxy, tip: routing.get()!.proxy_error, onClick: proxy }),
      Button({ label: () => checking.get() ? 'Checking…' : 'Check hosts', icon: 'rotate-ccw', variant: 'primary', disabled: () => checking.get() || !routing.get()!.proxy,
        onClick: () => void check() })] : null)}
    ${Callout('info', 'info', html`To move a frontend or add a host, edit <code>routes.py</code> in the instance folder and restart. Locked paths belong to the framework, and no app can take them.`)}
    ${() => { const value = routing.get(); if (!value) return html`<div class="ctl-empty">Loading routes…</div>`; return [
      value.proxy_error ? Callout('warn', 'info', value.proxy_error) : null,
      html`<section class="ctl-card ctl-gap"><div class="ctl-card-head"><div><h2>Hosts</h2><p>Each host must point at this server before a certificate is issued</p></div></div>
        <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Host</th><th>Kind</th><th>Sign-in</th><th>DNS</th><th>Certificate</th><th>Points to</th><th>Renews</th></tr></thead><tbody>
        ${value.hosts.map(host => html`<tr><td class="strong">${host.host}</td><td>${host.primary ? StatusPill({ label: 'Primary', tint: 'violet' }) : host.sub ? StatusPill({ label: 'Sub-host', tint: 'teal' }) : html`<span class="muted">Local</span>`}</td><td>${host.realm}</td>
          <td>${() => dns(checks.get().get(host.host))}</td><td>${() => cert(checks.get().get(host.host))}</td>
          <td class="mono">${() => checks.get().get(host.host)?.records || ''}</td><td class="muted num">${() => stamp(checks.get().get(host.host)?.expires)}</td></tr>`)}
        </tbody></table></div>${() => notes.get().map(note => html`<p class="muted ctl-small ctl-pad">${note}</p>`)}</section>`,
      html`<section class="ctl-card"><div class="ctl-card-head"><div><h2>Routes</h2><p>${plural(value.rows.length, 'route')} resolved, no clashes</p></div></div>
        <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Host</th><th>Path</th><th>Serves</th><th>Sign-in</th><th>Source</th></tr></thead><tbody>
        ${value.rows.map(row => html`<tr><td>${row.host}</td><td class="mono strong">${row.path}</td><td class="mono muted">${row.serves}</td><td>${row.realm}</td><td>${source(row.source)}</td></tr>`)}
        <tr><td class="muted">—</td><td class="mono">${value.planned.join(', ')}</td><td class="muted">Reserved for Evlet</td><td class="muted">404 for now</td><td>${StatusPill({ label: 'Reserved', tint: 'slate' })}</td></tr>
        </tbody></table></div></section>`]; }}
  </div>` as HTMLElement;
}

// ---------- jobs ----------

const every = (seconds: number) => seconds % 86400 === 0 ? plural(seconds / 86400, 'day') : seconds % 3600 === 0 ? plural(seconds / 3600, 'hour') : plural(Math.round(seconds / 60), 'minute');
const STATES: Record<string, [string, 'green' | 'rose' | 'blue' | 'slate']> = {
  succeeded: ['Done', 'green'], failed: ['Failed', 'rose'], running: ['Running', 'blue'], queued: ['Queued', 'slate'] };
const stateOf = (state: string): [string, 'green' | 'rose' | 'blue' | 'slate'] => STATES[state] ?? [state, 'slate'];
const ago = (seconds: number) => RelativeTime({ at: new Date(seconds * 1000) });
const ALL = 'all';
// Inside a tab, so not focusable on its own: the exact time is the title.
const hint = (seconds: number) => { const at = new Date(seconds * 1000); return html`<time datetime=${at.toISOString()} title=${exactTime(at)}>${relativeTime(at)}</time>`; };

/** The jobs the left list shows: every task a schedule runs or a recent run used, by name. */
export function jobNames(value: Jobs): string[] {
  return [...new Set([...value.schedules.map(item => item.task), ...value.jobs.map(job => job.task)])].sort();
}

/**
 * Jobs: a list of jobs (tasks) with All on top, and the selected job's schedules and recent runs.
 * The selection is ?job=<task> in the URL. Runs come from the one recent list the server sends
 * (the newest PAGE runs across every job), so a job's runs are filtered from those.
 */
export function JobsPage(ctx: ControlsContext): HTMLElement {
  const jobs = cell<Jobs | null>(null);
  const act = async (run: () => Promise<Jobs>, done: string) => {
    try { jobs.set(await run()); toast(done); ctx.state.set(await ctx.api.state()); } catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  void ctx.api.jobs().then(value => jobs.set(value), failure => toast(message(failure), { icon: 'info' }));
  const names = derive(() => { const value = jobs.get(); return value ? jobNames(value) : []; });
  // An unknown ?job= falls back to All once the jobs are loaded.
  const selected = derive(() => { const wanted = ctx.router.route.get()?.query.job; return wanted && names.get().includes(wanted) ? wanted : ALL; });
  const select = (name: string) => void ctx.router.go('jobs', {}, name === ALL ? {} : { job: name }, { replace: true });
  const runsOf = (value: Jobs, name: string) => name === ALL ? value.jobs : value.jobs.filter(job => job.task === name);
  let list: HTMLElement;
  const keys = (event: KeyboardEvent) => {
    const order = [ALL, ...names.peek()], at = order.indexOf(selected.peek());
    const to = { ArrowDown: at + 1, ArrowRight: at + 1, ArrowUp: at - 1, ArrowLeft: at - 1, Home: 0, End: order.length - 1 }[event.key];
    if (to === undefined) return;
    event.preventDefault();
    const next = order[Math.max(0, Math.min(order.length - 1, to))];
    select(next);
    queueMicrotask(() => list.querySelector<HTMLElement>(`[data-job="${CSS.escape(next)}"]`)?.focus());
  };
  const entry = (value: Jobs, name: string) => {
    const runs = runsOf(value, name), latest = runs[0];
    const on = () => selected.get() === name;
    return html`<button type="button" role="tab" class="ctl-jitem" class:on=${on} id=${`ctl-job-${name}`} data-job=${name}
      aria-selected=${() => String(on())} aria-controls="ctl-job-runs" tabindex=${() => on() ? '0' : '-1'} on:click=${() => select(name)}>
      <span class="ctl-jname"><span class="grow">${name === ALL ? 'All' : html`<span class="mono">${name}</span>`}</span> <span class="ctl-n" title=${plural(runs.length, 'recent run')}>${runs.length}</span></span>
      ${latest ? html`<span class="ctl-jhint"><span class=${`ctl-jdot b-${stateOf(latest.state)[1]}`} aria-hidden="true"></span> <span>${stateOf(latest.state)[0]}</span> ${hint(latest.run_at)}</span>`
        : html`<span class="ctl-jhint muted">No runs yet</span>`}</button>`;
  };
  const schedules = (value: Jobs, name: string) => {
    const shown = name === ALL ? value.schedules : value.schedules.filter(item => item.task === name);
    if (name !== ALL && !shown.length) return null;
    return html`<section class="ctl-card ctl-gap"><div class="ctl-card-head"><div><h2>Schedules</h2><p>Defined by apps. Pausing one is saved to this instance.</p></div></div>
      <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Schedule</th><th>Task</th><th>Every</th><th>Next run</th><th>App</th><th class="c">Active</th><th></th></tr></thead><tbody>
      ${shown.length ? shown.map(item => html`<tr><td class="mono strong">${item.name}</td><td class="mono muted">${item.task}</td>
        <td>${item.expression ? html`<code>${item.expression}</code>` : every(item.every ?? 0)}</td><td class="num" class:muted=${!item.active}>${item.active ? stamp(item.next_run) : 'Paused'}</td>
        <td>${item.app ? html`<span class="ctl-chip">${item.app}</span>` : html`<span class="muted">—</span>`}</td>
        <td class="c">${Switch({ on: item.active, ariaLabel: `Run ${item.name}`, onChange: next => void act(() => ctx.api.setSchedule(item.name, next), `${item.name} ${next ? 'resumed' : 'paused'}`) })}</td>
        <td class="r">${Button({ label: 'Run now', icon: 'zap', size: 'small', onClick: () => void act(() => ctx.api.runSchedule(item.name), `${item.name} queued`) })}</td></tr>`)
        : html`<tr><td colspan="7"><div class="ctl-empty">No schedules.</div></td></tr>`}</tbody></table></div></section>`;
  };
  const runs = (value: Jobs, name: string) => {
    const shown = runsOf(value, name), all = name === ALL, columns = all ? 7 : 6;
    const limit = value.jobs.length >= RECENT ? `Showing the last ${RECENT} runs across all jobs, so older runs of this job may not be listed. ` : '';
    return html`<section class="ctl-card"><div class="ctl-card-head"><div><h2>${all ? 'Recent runs' : html`Recent runs of <span class="mono">${name}</span>`}</h2>
      <p>${limit}Newest first. A retried job keeps its idempotency key.</p></div></div>
      <div class="table-wrap"><table class="dt ctl-table ctl-runs"><thead><tr><th>Run</th>${all ? html`<th>Job</th>` : null}<th>Record</th><th>Status</th><th>Due</th><th class="r">Attempts</th><th></th></tr></thead><tbody>
      ${shown.length ? shown.map(job => html`<tr data-run=${String(job.id)}><td class="mono muted">#${job.id}</td>${all ? html`<td class="mono">${job.task}</td>` : null}
        <td>${job.model ? `${job.model} #${job.record_id}` : html`<span class="muted">—</span>`}</td>
        <td>${StatusPill({ label: stateOf(job.state)[0], tint: stateOf(job.state)[1] })}${job.error ? html`<span class="ctl-sub ctl-err">${job.error}</span>` : null}</td>
        <td class="muted num">${ago(job.run_at)}</td><td class="r num">${job.attempts}</td>
        <td class="r">${job.state === 'failed' ? Button({ label: 'Retry', icon: 'rotate-ccw', size: 'small', onClick: () => void act(() => ctx.api.retryJob(job.id), `Job #${job.id} queued again`) }) : null}</td></tr>`)
        : html`<tr><td colspan=${String(columns)}><div class="ctl-empty">No runs yet.</div></td></tr>`}</tbody></table></div></section>`;
  };
  return html`<div>
    ${Head('Jobs', 'Background work and the schedules from installed apps.')}
    ${() => { const value = jobs.get(); if (!value) return html`<div class="ctl-empty">Loading jobs…</div>`;
      if (!value.available) return html`<section class="ctl-card"><div class="ctl-empty">This instance has no background jobs.</div></section>`;
      list = html`<div class="ctl-card ctl-jlist" role="tablist" aria-label="Jobs" aria-orientation="vertical" on:keydown=${keys}>
        ${[ALL, ...names.get()].map(name => entry(value, name))}</div>` as HTMLElement;
      return html`<div class="ctl-split ctl-jobs">${list}
        <div class="ctl-min" role="tabpanel" id="ctl-job-runs" aria-labelledby=${() => `ctl-job-${selected.get()}`}>
          ${() => [schedules(value, selected.get()), runs(value, selected.get())]}</div></div>`; }}
    ${UnsavedSection(ctx)}
  </div>` as HTMLElement;
}

// ---------- overrides ----------

const KINDS: Array<[string, string]> = [['perm', 'Permissions'], ['field', 'Fields'], ['custom', 'Instance fields'], ['layout', 'Form layouts'],
  ['list', 'List views'], ['naming', 'Naming'], ['setting', 'Settings']];
const SCHEDULE = /^schedule\.([a-z][a-z0-9_]*)\.active$/;

/** What the Overrides page shows for one saved override: the target, the app file value and this instance's value. */
export function overrideText(item: OverrideItem, scopes: Record<string, string>) {
  const schedule = SCHEDULE.exec(item.key);
  if (schedule) return { target: `${schedule[1]} schedule`, file: 'Running', value: item.value === false ? 'Paused' : 'Running' };
  if (item.kind === 'custom') return { target: `${item.key} instance fields`, file: 'None', value: (item.value as Array<{ name: string }>).map(field => field.name).join(', ') };
  const text = describe({ id: idOf(item.kind, item.key), item, from: null, to: item.value }, scopes);
  return { target: text.target, file: text.from.replace(/ \(file\)$/, ''), value: text.to };
}

export function OverridesPage(ctx: ControlsContext): HTMLElement {
  const tab = cell('all');
  const rows = derive(() => [...ctx.drafts.saved.get().values()].filter(item => item.value !== null && item.value !== undefined));
  const shown = derive(() => rows.get().filter(item => tab.get() === 'all' || item.kind === tab.get()));
  const exportAll = async () => {
    try { openCode('Export overrides as code', 'Every saved override, grouped by the file it belongs in. Paste each block into the app, deploy it, then reset the overrides here.', await ctx.api.exportCode()); }
    catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  const showCode = async (item: OverrideItem) => {
    try { openCode('Move into code', 'Paste this into the app, deploy it, then reset the override here.', await ctx.api.exportCode({ kind: item.kind, key: item.key })); }
    catch (failure) { toast(message(failure), { icon: 'info' }); }
  };
  const tabs: Array<[string, string]> = [['all', 'All'], ...KINDS];
  return html`<div>
    ${Head('Overrides', 'Every value on this instance that differs from the app files. Reset one to go back to the file, or export them as code to move them into the app.',
      Button({ label: 'Export as code', icon: 'file-text', onClick: () => void exportAll() }))}
    <div class="ctl-tabs" role="tablist">${tabs.map(([id, label]) => html`<button type="button" role="tab" class:on=${() => tab.get() === id}
      aria-selected=${() => String(tab.get() === id)} on:click=${() => tab.set(id)}>${label}<span class="tc">${() => rows.get().filter(item => id === 'all' || item.kind === id).length}</span></button>`)}</div>
    <div class="table-wrap"><table class="dt ctl-table"><thead><tr><th>Area</th><th>What</th><th>In the app files</th><th>On this instance</th><th>Changed</th><th></th></tr></thead>
      <tbody>${() => shown.get().length ? shown.get().map(item => {
        const text = overrideText(item, ctx.state.peek().scopes);
        const id = idOf(item.kind, item.key);
        return html`<tr class="ctl-ovrow"><td>${StatusPill({ label: KINDS.find(([kind]) => kind === item.kind)?.[1] ?? item.kind, tint: 'slate' })}</td>
          <td class="mono strong">${text.target}</td><td class="muted ctl-small">${text.file}</td><td class="ctl-small"><strong>${text.value}</strong></td>
          <td class="muted ctl-small">${item.changed_by ?? '—'}<span class="ctl-sub">${stamp(item.changed_at)}</span></td>
          <td class="r ctl-nowrap">${() => ctx.drafts.dirty(id) ? html`<span class="ctl-tag">Reset pending</span>` : [
            Button({ icon: 'file-text', tip: 'Show code', size: 'small', variant: 'ghost', onClick: () => void showCode(item) }),
            item.kind === 'custom' ? html`<a class="ctl-link" href=${ctx.router.href('models', {}, { model: item.key })}>Models ${Icon('arrow-right', { size: 'xs' })}</a>`
              : Button({ label: 'Reset', icon: 'rotate-ccw', size: 'small', onClick: () => ctx.drafts.reset(id) })]}</td></tr>`;
      }) : html`<tr><td colspan="6"><div class="ctl-empty">No overrides. This instance matches the app files.</div></td></tr>`}</tbody></table></div>
  </div>` as HTMLElement;
}

// ---------- audit ----------

export function AuditRows(rows: AuditRow[]): Child {
  return rows.map(row => html`<div class="ctl-audit"><span class="when">${stamp(row.at)}</span>
    <span><strong>${row.target}</strong> <span class="ctl-badge">${row.area}</span><span class="detail">${row.detail} · by ${row.actor}</span></span></div>`);
}

export function AuditPage(ctx: ControlsContext): HTMLElement {
  const rows = cell<AuditRow[]>([]), next = cell<number | null>(null), areas = cell<string[]>([]), loaded = cell(false);
  const query = cell(''), area = cell('');
  let token = 0;
  const load = async (more = false) => {
    const mine = ++token;
    try {
      const page = await ctx.api.audit({ q: query.peek().trim() || undefined, area: area.peek() || undefined, before: more ? next.peek() ?? undefined : undefined });
      if (mine !== token) return;
      rows.set(more ? [...rows.peek(), ...page.items] : page.items);
      next.set(page.next);
      areas.set(page.areas);
    } catch (failure) { toast(message(failure), { icon: 'info' }); }
    loaded.set(true);
  };
  void load();
  return html`<div>
    ${Head('Audit log', "Every change made in Controls, newest first. Entries can't be edited or deleted.")}
    <div class="toolbar"><label class="ctl-search">${Icon('search', { size: 'sm' })}<input class="input" placeholder="Search changes…" aria-label="Search changes"
      prop:value=${query} on:input=${(event: Event) => { query.set((event.target as HTMLInputElement).value); void load(); }}></label>
      ${Select({ id: 'ctl-audit-area', label: 'Area', value: () => area.get(), options: () => [['', 'All areas'], ...areas.get().map(item => [item, item] as [string, string])],
        onChange: value => { area.set(value); void load(); } })}</div>
    <section class="ctl-card">${() => rows.get().length ? AuditRows(rows.get()) : html`<div class="ctl-empty">${() => loaded.get() ? 'No changes match.' : 'Loading…'}</div>`}
      ${() => next.get() === null ? null : html`<div class="ctl-pad">${Button({ label: 'Show older', onClick: () => void load(true) })}</div>`}</section>
  </div>` as HTMLElement;
}
