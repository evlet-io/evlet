// Eva's scheduled tasks (/eva/tasks): what a person asked Eva to do on a schedule. Each task is a
// schedule in Evlet's jobs system and runs as its owner with their permissions on the day; every
// run writes a chat the person reads later.

import { html, when } from '../dom';
import { cell, type Cell } from '../reactive';
import { Button, EmptyState, Icon, StatusPill, Switch } from '../ui/base';
import { DialogFooter, openDrawer, openModal, toast } from '../ui/layers';
import type { AppContext } from '../app/context';
import { Eva, type EvaStatus, type Parsed, type Task } from './client';
import { stamp } from './chat';
import './eva.css';

const RUN: Record<string, [string, any]> = { done: ['Done', 'green'], failed: ['Failed', 'rose'], skipped: ['Skipped', 'amber'], running: ['Running', 'blue'] };
const errorText = (failure: unknown) => (failure as { message?: string })?.message ?? 'Something went wrong. Try again.';

export function TasksPage(ctx: AppContext, status: Cell<EvaStatus | null>): HTMLElement {
  const eva = new Eva(ctx.api);
  const tasks = cell<Task[] | null>(null), limit = cell(10);
  ctx.crumbs.set([{ label: 'Eva', href: ctx.router.href('eva', {}) }, { label: 'Scheduled tasks' }]);
  const load = async () => {
    try { const page = await eva.tasks(); tasks.set(page.items); limit.set(page.limit); } catch (failure) { toast(errorText(failure), { icon: 'info' }); tasks.set([]); }
  };
  void load();
  const toggle = async (task: Task, active: boolean) => {
    try { await eva.changeTask(task.id, { active }); toast(active ? 'Resumed' : 'Paused'); await load(); } catch (failure) { toast(errorText(failure), { icon: 'info' }); }
  };

  return html`<div class="page eva-tasks">
    <div class="ph">${html`<span class="eva-mark lg">${Icon('clock')}</span>`}<h1>Scheduled tasks</h1>
      <span class="count">${() => `${tasks.get()?.length ?? 0} of ${limit.get()}`}</span><span class="grow"></span>
      <div class="ph-actions">${Button({ label: 'Chats', icon: 'message-square', onClick: () => void ctx.router.go('eva', {}) })}
        ${Button({ label: 'New task', icon: 'plus', variant: 'primary', onClick: () => newTask(eva, load) })}</div></div>
    <p class="muted eva-lead">Eva runs these on a schedule, as you, with your permissions on the day. Each run writes a chat you can open later.</p>
    ${() => tasks.get() === null ? html`<p class="eva-none">Loading…</p>` : !tasks.get()!.length
      ? EmptyState({ icon: 'clock', tint: 'violet', title: 'No scheduled tasks yet', text: 'For example: every Monday at 9 AM, review my overdue invoices and prepare a collection summary.',
        action: Button({ label: 'New task', icon: 'plus', onClick: () => newTask(eva, load) }) })
      : html`<div class="table-wrap"><table class="dt"><thead><tr><th>Task</th><th>When</th><th>Next run</th><th>Last run</th><th>On</th></tr></thead><tbody>
        ${tasks.get()!.map(task => html`<tr class="eva-click" tabindex="0" on:click=${(event: Event) => { if (!(event.target as HTMLElement).closest('.switch')) openTask(ctx, eva, task.id, load); }}
          on:keydown=${(event: KeyboardEvent) => { if (event.key === 'Enter') openTask(ctx, eva, task.id, load); }}>
          <td><b>${task.name}</b>${task.allow_changes ? html` <span class="chip">Can make changes</span>` : null}</td>
          <td><code class="eva-code">${task.cron}</code> <span class="muted">${task.timezone}</span></td>
          <td class="num">${task.active ? stamp(task.next_run_at) : html`<span class="muted">Paused</span>`}</td>
          <td>${task.last_status ? StatusPill({ label: `${RUN[task.last_status]?.[0] ?? task.last_status} ${stamp(task.last_run_at).slice(0, 5)}`, tint: RUN[task.last_status]?.[1] ?? 'slate' }) : html`<span class="muted">Not yet</span>`}</td>
          <td>${Switch({ on: task.active, ariaLabel: `${task.name} on`, onChange: next => void toggle(task, next) })}</td></tr>`)}</tbody></table></div>`}
    ${when(() => !status.get()?.tasks, () => html`<p class="muted eva-small">This workspace doesn't run background jobs, so tasks can't run yet.</p>`)}
  </div>` as HTMLElement;
}

function openTask(ctx: AppContext, eva: Eva, id: number, changed: () => Promise<void>) {
  const task = cell<Task | null>(null);
  const load = async () => { try { task.set(await eva.task(id)); } catch (failure) { toast(errorText(failure), { icon: 'info' }); } };
  void load();
  const act = async (step: () => Promise<unknown>, done: string) => {
    try { await step(); toast(done); await load(); await changed(); } catch (failure) { toast(errorText(failure), { icon: 'info' }); }
  };
  openDrawer({ title: () => task.get()?.name ?? 'Task', icon: 'clock', tint: 'violet', className: 'eva-task-drawer',
    body: close => html`${() => { const t = task.get(); if (!t) return html`<p class="eva-none">Loading…</p>`;
      return html`<div class="eva-task">
        <div class="eva-task-actions">${Button({ label: t.active ? 'Pause' : 'Resume', icon: t.active ? 'circle' : 'arrow-right', size: 'small',
          onClick: () => void act(() => eva.changeTask(t.id, { active: !t.active }), t.active ? 'Paused' : 'Resumed') })}
          ${Button({ label: 'Run now', icon: 'zap', size: 'small', onClick: () => void act(() => eva.runTask(t.id), 'Queued. The result shows up below when it has run.') })}
          ${Button({ label: 'Delete', icon: 'trash-2', size: 'small', variant: 'soft-danger', onClick: () => openModal({ title: `Delete ${t.name}?`, narrow: true, icon: 'trash-2', tint: 'rose',
            body: () => html`<p class="muted">It stops running. Chats from earlier runs stay.</p>`,
            footer: done => DialogFooter({ cancel: done, submit: { label: 'Delete task', variant: 'danger', onClick: async () => { done(); await act(() => eva.deleteTask(t.id), 'Task deleted'); close(); } } }) }) })}</div>
        <section><h3>Instructions</h3><p class="eva-instructions">${t.instructions}</p></section>
        <dl class="eva-kv"><dt>Schedule</dt><dd><code class="eva-code">${t.cron}</code></dd><dt>Time zone</dt><dd>${t.timezone}</dd>
          <dt>Runs as</dt><dd>${ctx.user.username}, with your permissions on the day</dd>
          <dt>Changes</dt><dd>${t.allow_changes ? 'Can make changes that need no approval. Anything that needs approval is skipped.' : 'Read only'}</dd>
          <dt>Next run</dt><dd>${t.active ? stamp(t.next_run_at) : 'Paused'}</dd></dl>
        <section><h3>Runs</h3>${t.runs?.length ? html`<div class="table-wrap"><table class="dt"><tbody>${t.runs.map(run => html`<tr>
          <td class="num">${stamp(run.at)}</td><td>${StatusPill({ label: RUN[run.status]?.[0] ?? run.status, tint: RUN[run.status]?.[1] ?? 'slate' })}</td>
          <td class="r num">AED ${run.cost}</td><td>${run.conversation_id ? html`<a href=${ctx.router.href('evaChat', { conversation: String(run.conversation_id) })} on:click=${() => close()}>Open</a>` : null}</td></tr>
          ${run.error ? html`<tr><td colspan="4" class="muted eva-small">${run.error}</td></tr>` : null}`)}</tbody></table></div>` : html`<p class="muted">No runs yet.</p>`}</section>
        <p class="muted eva-small">If you lose Eva access, this task stops and says why. It never runs with more access than you have that day.</p></div>`; }}`,
  });
}

function newTask(eva: Eva, created: () => Promise<void>) {
  const text = cell(''), parsed = cell<Parsed | null>(null), name = cell(''), instructions = cell(''), cron = cell(''), zone = cell('Asia/Dubai');
  const changes = cell(false), fields = cell<Record<string, string>>({}), busy = cell(false);
  try { zone.set(Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Dubai'); } catch { /* keep the default */ }
  let timer: ReturnType<typeof setTimeout> | null = null;
  const read = async () => {
    if (!text.peek().trim()) return;
    try {
      const result = await eva.parse(text.peek(), zone.peek());
      parsed.set(result);
      if (result.cron) { cron.set(result.cron); instructions.set(result.instructions); if (!name.peek()) name.set(result.name); }
    } catch (failure) { toast(errorText(failure), { icon: 'info' }); }
  };
  const input = (id: string, value: Cell<string>, extra = '') => html`<input class=${`input ${extra}`} id=${id} prop:value=${value}
    on:input=${(event: Event) => value.set((event.target as HTMLInputElement).value)}>`;
  const save = async (close: () => void) => {
    busy.set(true);
    try {
      await eva.createTask({ name: name.peek(), instructions: instructions.peek() || text.peek(), cron: cron.peek(), timezone: zone.peek(), allow_changes: changes.peek() });
      close();
      toast('Task created');
      await created();
    } catch (failure) { fields.set((failure as { fields?: Record<string, string> }).fields ?? {}); toast(errorText(failure), { icon: 'info' }); }
    finally { busy.set(false); }
  };
  const error = (key: string) => () => fields.get()[key] ? html`<span class="ctl-err">${fields.get()[key]}</span>` : null;
  openModal({ title: 'New scheduled task', icon: 'clock', tint: 'violet',
    body: () => html`<div class="eva-form">
      <div class="ctl-field"><label for="eva-task-text">What should Eva do, and when?</label>
        <textarea class="input" id="eva-task-text" rows="3" placeholder="Every Monday at 9 AM, review my overdue invoices and prepare a collection summary." prop:value=${text}
          on:input=${(event: Event) => { text.set((event.target as HTMLTextAreaElement).value); if (timer) clearTimeout(timer); timer = setTimeout(() => void read(), 400); }}></textarea></div>
      ${() => { const result = parsed.get(); if (!result) return null;
        return result.cron ? html`<dl class="eva-kv eva-parsed"><dt>Eva understood</dt><dd>${result.summary}</dd><dt>Next runs</dt><dd class="num">${result.next.join(', ')}</dd></dl>`
          : html`<p class="muted eva-small">Eva couldn't read a schedule from that. Fill in the cron expression below.</p>`; }}
      <div class="ctl-field"><label for="eva-task-name">Name</label>${input('eva-task-name', name)}${error('name')}</div>
      <div class="ctl-field"><label for="eva-task-instructions">Instructions</label>
        <textarea class="input" id="eva-task-instructions" rows="2" prop:value=${instructions} on:input=${(event: Event) => instructions.set((event.target as HTMLTextAreaElement).value)}></textarea>${error('instructions')}</div>
      <div class="eva-row"><div class="ctl-field"><label for="eva-task-cron">Cron expression</label>${input('eva-task-cron', cron, 'mono')}
        <span class="muted eva-small">Minute, hour, day, month, weekday. At most once an hour.</span>${error('cron')}</div>
        <div class="ctl-field"><label for="eva-task-zone">Time zone</label>${input('eva-task-zone', zone)}${error('timezone')}</div></div>
      <div class="ctl-field">${Switch({ on: changes, onChange: next => changes.set(next), label: 'Allow changes that need no approval' })}
        <span class="muted eva-small">Off: the task only reads. Changes that need approval are always skipped, because nobody is there to approve them.</span></div>
    </div>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: () => busy.get() ? 'Creating…' : 'Create task', disabled: busy, onClick: () => void save(close) } }) });
}
