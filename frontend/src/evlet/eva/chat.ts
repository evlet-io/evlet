// Eva's chat (/eva and /eva/<conversation>): the conversation list beside the thread. A run streams
// into the thread; each tool call shows as a grey line while it runs and then folds into a row that
// opens for details. A change that needs a click shows an approval card and the run waits for it.
// Everything shown is rebuilt from what Evlet stored, so reopening a chat shows the same rows.
//
//   EvaPage(ctx, conversationId?)        the full page
//   EvaThread({ eva, ctx, id, compact })  one conversation, used by the page and the record panel

import { each, html, when, type Child } from '../dom';
import { ApiError } from '../api';
import { cell, derive, effect, onCleanup, type Cell } from '../reactive';
import { Button, EmptyState, Icon, IconButton, StatusPill } from '../ui/base';
import { DialogFooter, Menu, openDrawer, openModal, openPopover, toast } from '../ui/layers';
import { formatDateTime } from '../model/format';
import type { AppContext } from '../app/context';
import { Eva, follow, readFile, type Activity, type Approval, type Conversation, type EvaStatus, type FileRef, type Library, type Run, type RunEvent, type Stored } from './client';
import './eva.css';

/** "02-11-2026 14:05" in the person's region settings, from a Unix time. */
export const stamp = (seconds: number | null | undefined) => seconds ? formatDateTime(new Date(seconds * 1000).toISOString()) : '';
const size = (bytes: number) => bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
const errorText = (failure: unknown) => failure instanceof ApiError ? failure.message : 'Something went wrong. Try again.';

// ---------- the page ----------

export function EvaPage(ctx: AppContext, status: Cell<EvaStatus | null>, conversationId?: number): HTMLElement {
  const eva = new Eva(ctx.api);
  const list = cell<Conversation[]>([]), archivedCount = cell(0), search = cell(''), archived = cell(false), loaded = cell(false);
  const current = cell<number | null>(conversationId ?? null);
  ctx.crumbs.set([{ label: 'Eva', href: ctx.router.href('eva', {}) }, ...(conversationId ? [{ label: 'Chat' }] : [])]);
  const load = async () => {
    try {
      const page = await eva.conversations({ q: search.peek(), archived: archived.peek() });
      list.set(page.items);
      archivedCount.set(page.archived);
    } catch (failure) { toast(errorText(failure), { icon: 'info' }); }
    loaded.set(true);
  };
  let typing: ReturnType<typeof setTimeout> | null = null;
  const searchFor = (value: string) => { search.set(value); if (typing) clearTimeout(typing); typing = setTimeout(() => void load(), 200); };
  void load();
  onCleanup(() => { if (typing) clearTimeout(typing); });

  const groups = derive(() => {
    const now = Date.now() / 1000, today = new Date(); today.setHours(0, 0, 0, 0);
    const start = today.getTime() / 1000;
    const buckets: Array<[string, Conversation[]]> = [['Today', []], ['Previous 7 days', []], ['Earlier', []]];
    for (const item of list.get()) buckets[item.updated_at >= start ? 0 : item.updated_at >= now - 7 * 86400 ? 1 : 2][1].push(item);
    return buckets.filter(([, items]) => items.length);
  });

  const rename = (item: Conversation) => {
    const title = cell(item.title);
    openModal({ title: 'Rename chat', narrow: true, icon: 'pencil',
      body: () => html`<div class="ctl-field"><label for="eva-rename">Name</label><input class="input" id="eva-rename" maxlength="120" prop:value=${title}
        on:input=${(event: Event) => title.set((event.target as HTMLInputElement).value)}></div>`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Rename', onClick: async () => {
        try { await eva.change(item.id, { title: title.peek() }); close(); toast('Renamed'); await load(); } catch (failure) { toast(errorText(failure), { icon: 'info' }); }
      } } }) });
  };
  const archive = async (item: Conversation) => {
    try {
      await eva.change(item.id, { archived: !item.archived });
      toast(item.archived ? 'Restored' : 'Archived', item.archived ? {} : { undo: () => void eva.change(item.id, { archived: false }).then(load) });
      if (current.peek() === item.id && !item.archived) void ctx.router.go('eva', {});
      await load();
    } catch (failure) { toast(errorText(failure), { icon: 'info' }); }
  };
  const remove = (item: Conversation) => openModal({ title: 'Delete this chat?', narrow: true, icon: 'trash-2', tint: 'rose',
    body: () => html`<p class="muted">The messages and files of "${item.title}" are deleted for good. Records Eva changed stay as they are.</p>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Delete chat', variant: 'danger', onClick: async () => {
      try { await eva.remove(item.id); close(); toast('Chat deleted'); if (current.peek() === item.id) void ctx.router.go('eva', {}); await load(); }
      catch (failure) { toast(errorText(failure), { icon: 'info' }); }
    } } }) });
  const menu = (item: Conversation, anchor: HTMLElement) => openPopover(anchor, close => Menu([
    { label: 'Rename', icon: 'pencil', onSelect: () => rename(item) },
    { label: item.archived ? 'Restore' : 'Archive', icon: 'inbox', onSelect: () => void archive(item) },
    'separator', { label: 'Delete', icon: 'trash-2', danger: true, onSelect: () => remove(item) },
  ], close), { label: 'Chat' });

  const row = (item: () => Conversation) => html`<div class="eva-conv" class:on=${() => current.get() === item().id}>
    <a href=${() => ctx.router.href('evaChat', { conversation: String(item().id) })} class="eva-conv-link">
      ${() => item().task_id ? Icon('clock', { size: 'xs', tint: 'violet' }) : item().context ? Icon('link', { size: 'xs', tint: 'slate' }) : null}
      <span class="t">${() => item().title}</span></a>
    ${IconButton({ icon: 'ellipsis', tip: 'Chat options', size: 'small', onClick: event => menu(item(), event.currentTarget as HTMLElement) })}</div>`;

  const side = html`<aside class="eva-side" aria-label="Chats">
    <div class="eva-side-top">
      <div class="eva-tabs" role="tablist"><a role="tab" class="on" aria-selected="true" href=${ctx.router.href('eva', {})}>${Icon('message-square', { size: 'sm' })}Chats</a>
        ${status.peek()?.tasks ? html`<a role="tab" aria-selected="false" href=${ctx.router.href('evaTasks', {})}>${Icon('clock', { size: 'sm' })}Scheduled</a>` : null}</div>
      ${Button({ label: 'New chat', icon: 'plus', size: 'small', onClick: () => void ctx.router.go('eva', {}) })}
      <label class="eva-search">${Icon('search', { size: 'sm' })}<input class="input" placeholder="Search chats" aria-label="Search chats" prop:value=${search}
        on:input=${(event: Event) => searchFor((event.target as HTMLInputElement).value)}></label>
    </div>
    <div class="eva-convs">
      ${() => archived.get() ? html`<div class="eva-group-h">Archived</div>` : null}
      ${() => groups.get().map(([label, items]) => html`<div class="eva-group-h">${archived.get() ? '' : label}</div>${each(() => items, item => item.id, row)}`)}
      ${when(() => loaded.get() && !list.get().length, () => html`<p class="eva-none">${() => search.get() ? 'No chats match.' : archived.get() ? 'Nothing archived.' : 'Your chats show up here.'}</p>`)}
    </div>
    <div class="eva-side-foot"><button type="button" class="nav-item" on:click=${() => { archived.set(!archived.peek()); void load(); }}>
      ${Icon(archived.get() ? 'arrow-left' : 'inbox', { size: 'sm' })}<span class="lbl">${() => archived.get() ? 'Back to chats' : 'Archived'}</span>
      ${() => archived.get() ? null : html`<span class="count">${archivedCount}</span>`}</button></div>
  </aside>`;

  const started = async (conversation: Conversation) => {
    current.set(conversation.id);
    await load();
    void ctx.router.go('evaChat', { conversation: String(conversation.id) }, {}, { replace: true });
  };
  return html`<div class="eva">${side}<section class="eva-main">${conversationId
    ? EvaThread({ ctx, eva, status, id: conversationId, onChanged: () => void load() })
    : EvaWelcome({ ctx, eva, status, onStarted: conversation => void started(conversation) })}</section></div>` as HTMLElement;
}

// ---------- a new chat ----------

const STARTERS = ['Which invoices are overdue by more than 30 days?', 'Summarise what changed in my projects this week', 'Make a spreadsheet of my open projects'];

function EvaWelcome(props: { ctx: AppContext; eva: Eva; status: Cell<EvaStatus | null>; onStarted: (conversation: Conversation) => void; context?: { model: string; id: string }; starters?: string[] }): HTMLElement {
  const { eva, status } = props;
  const composer = Composer({ eva, status, busy: cell(false), send: async (text, files) => {
    const conversation = await eva.create(props.context ? { context: props.context, title: 'New chat' } : {});
    await eva.send(conversation.id, text, files.map(file => file.id));
    props.onStarted(conversation);
  } });
  return html`<div class="eva-thread eva-welcome">
    <div class="eva-hello">${html`<span class="eva-mark lg">${Icon('sparkles')}</span>`}<h1>What can I help with?</h1>
      <p>Eva works with your records and your permissions. Changes that need approval always ask you first.</p>
      <div class="eva-starters">${(props.starters ?? STARTERS).map(text => html`<button type="button" on:click=${() => composer.fill(text)}>${text}</button>`)}</div></div>
    ${composer.node}</div>` as HTMLElement;
}

// ---------- one conversation ----------

interface Live { run: number; text: string; activities: Map<string, Activity>; status: Run['status'] | 'starting'; error: string | null }

export function EvaThread(props: { ctx: AppContext; eva: Eva; status: Cell<EvaStatus | null>; id: number; compact?: boolean; onChanged?: () => void }): HTMLElement {
  const { ctx, eva, status, id } = props;
  const stored = cell<Stored | null>(null), failure = cell(''), live = cell<Live | null>(null), busy = cell(false);
  let stop: (() => void) | null = null;
  onCleanup(() => stop?.());

  const reload = async () => {
    try {
      const data = await eva.open(id);
      stored.set(data);
      if (!props.compact) ctx.crumbs.set([{ label: 'Eva', href: ctx.router.href('eva', {}) }, { label: data.conversation.title }]);
      const running = data.runs.find(run => run.status === 'running');
      if (running && live.peek()?.run !== running.id) watch(running.id);
      if (!running) live.set(null);
    } catch (error) { failure.set(errorText(error)); }
  };
  const watch = (run: number) => {
    stop?.();
    live.set({ run, text: '', activities: new Map(), status: 'running', error: null });
    stop = follow(eva, run, event => apply(event), () => { stop = null; void reload().then(() => { props.onChanged?.(); void refreshStatus(); }); });
  };
  const apply = (event: RunEvent) => {
    const now = live.peek();
    if (!now) return;
    const next: Live = { ...now, activities: new Map(now.activities) };
    const key = String(event.call_id ?? '');
    if (event.type === 'message.delta') next.text += String(event.text ?? '');
    else if (event.type === 'tool.started') {
      // Text before a tool call belongs to that step; the answer starts after the last tool.
      next.text = '';
      next.activities.set(key, { call_id: key, tool: String(event.tool), label: String(event.label), status: 'running' });
    } else if (event.type === 'tool.completed') next.activities.set(key, { ...(next.activities.get(key) as Activity), status: 'done', label: String(event.label),
      summary: event.summary as string, duration_ms: event.duration_ms as number, inputs: event.inputs as any, records: event.records as any });
    else if (event.type === 'tool.failed') next.activities.set(key, { ...(next.activities.get(key) as Activity), status: event.denied ? 'refused' : 'failed', error: String(event.error) });
    else if (event.type === 'approval.requested') { next.activities.set(key, { call_id: key, tool: String(event.tool), label: String(event.label), status: 'approval', approval: event.approval as Approval }); next.status = 'awaiting_approval'; }
    else if (event.type === 'run.failed' || event.type === 'run.cancelled') { next.status = event.type === 'run.failed' ? 'failed' : 'cancelled'; next.error = String(event.error ?? ''); }
    else if (event.type === 'run.completed') next.status = 'completed';
    live.set(next);
  };
  const refreshStatus = async () => { try { status.set(await eva.status()); } catch { /* the next send will say */ } };
  void reload();

  const answer = async (approval: Approval, approve: boolean) => {
    try {
      await eva.answer(approval.id, approve);
      toast(approve ? 'Approved. Eva carries on.' : 'Declined. Nothing was changed.');
      const run = approval.run_id;
      await reload();
      if (run) watch(run);
    } catch (error) { toast(errorText(error), { icon: 'info' }); await reload(); }
  };
  const stopRun = async (run: number) => { try { await eva.cancel(run); toast('Stopped'); } catch (error) { toast(errorText(error), { icon: 'info' }); } };

  const composer = Composer({ eva, status, busy, running: () => { const now = live.get(); return now && now.status === 'running' ? now.run : null; },
    stop: run => void stopRun(run),
    waiting: () => Boolean(stored.get()?.runs.some(run => run.status === 'awaiting_approval')),
    send: async (text, files) => {
      const sent = await eva.send(id, text, files.map(file => file.id));
      const data = stored.peek();
      if (data) stored.set({ ...data, messages: [...data.messages, sent.message] });
      watch(sent.run.id);
    } });

  const turn = (run: Run) => {
    const data = stored.get()!;
    const asked = data.messages.filter(message => message.run_id === run.id && message.role === 'user');
    const answers = data.messages.filter(message => message.run_id === run.id && message.role === 'assistant');
    const now = live.get();
    const streaming = now && now.run === run.id ? now : null;
    const activities = streaming && streaming.activities.size ? [...streaming.activities.values()] : run.activities;
    const files = activities.flatMap(activity => (activity.records ?? []).filter(record => record.file)).map(record => ({ id: record.file!, name: record.name ?? 'File' }));
    return html`${asked.map(message => UserMessage(message, eva))}
      <div class="eva-turn">${html`<span class="eva-mark">${Icon('sparkles', { size: 'sm' })}</span>`}<div class="eva-body">
        ${activities.length ? html`<div class="eva-acts">${activities.map(activity => ActivityRow(ctx, activity, answer))}</div>` : null}
        ${answers.map(message => Markdown(message.content))}
        ${streaming && streaming.text ? html`<div class="eva-md">${Markdown(streaming.text)}<span class="eva-caret" aria-hidden="true"></span></div>` : null}
        ${streaming && streaming.status === 'running' ? html`<div class="eva-live" role="status"><span class="eva-spin"></span><span class="eva-shim">${liveLabel(streaming)}</span></div>` : null}
        ${files.length ? html`<div class="eva-files">${files.map(file => FileChip({ name: file.name, href: eva.fileUrl(file.id) }))}</div>` : null}
        ${run.status === 'failed' || streaming?.status === 'failed' ? RunProblem(run.error ?? streaming?.error ?? '') : null}
        ${run.status === 'cancelled' ? html`<p class="eva-note">Stopped. Anything Eva finished before that stays done.</p>` : null}
      </div></div>`;
  };

  return html`<div class="eva-thread" class:compact=${Boolean(props.compact)}>
    ${props.compact ? null : html`<div class="eva-thread-head"><h2>${() => stored.get()?.conversation.title ?? ''}</h2><span class="grow"></span>
      ${() => stored.get()?.conversation.context ? html`<a class="chip" href=${ctx.router.href('record', { model: stored.get()!.conversation.context!.model, id: String(stored.get()!.conversation.context!.id) })}>
        ${Icon('link', { size: 'xs' })}${stored.get()!.conversation.context!.model.replace(/_/g, ' ')} ${stored.get()!.conversation.context!.id}</a>` : null}
      ${() => stored.get() ? html`<span class="chip num" title="This chat so far">${stored.get()!.usage.tokens.toLocaleString('en')} tokens · AED ${stored.get()!.usage.cost}</span>` : null}</div>`}
    <div class="eva-scroll" ref=${(el: HTMLElement) => effect(() => { live.get(); stored.get(); queueMicrotask(() => { el.scrollTop = el.scrollHeight; }); })}>
      <div class="eva-msgs">${() => failure.get() ? EmptyState({ icon: 'info', tint: 'slate', title: 'This chat is not available', text: failure.get() })
        : stored.get() === null ? html`<p class="eva-none">Loading…</p>` : stored.get()!.runs.map(turn)}</div></div>
    ${composer.node}</div>` as HTMLElement;
}

const liveLabel = (live: Live) => {
  const running = [...live.activities.values()].reverse().find(activity => activity.status === 'running');
  return running ? running.label : live.text ? 'Writing…' : 'Thinking…';
};

function UserMessage(message: { content: string; files: FileRef[] }, eva: Eva): Node {
  return html`<div class="eva-user"><div class="eva-bubble">${message.content}
    ${message.files.length ? html`<div class="eva-files">${message.files.map(file => FileChip({ name: file.name, href: eva.fileUrl(file.id) }))}</div>` : null}</div></div>`;
}

function RunProblem(error: string): Node {
  if (error.startsWith('quota:')) {
    const [, scope, reset] = error.split(':');
    return html`<div class="eva-quota">${Icon('info', { tint: 'amber' })}<div><strong>${scope === 'person' ? 'You have used your Eva allowance' : "The workspace's Eva allowance is used up"}</strong>
      <p>Eva stopped before asking the AI provider again. It resets on ${stamp(Number(reset))}. Everything finished so far is kept.</p></div></div>`;
  }
  return html`<div class="eva-quota">${Icon('info', { tint: 'rose' })}<div><strong>Eva couldn't finish</strong><p>${error || 'Something went wrong.'}</p></div></div>`;
}

// ---------- activity rows and approvals ----------

const STATUS: Record<Activity['status'], [string, any]> = { running: ['Running', 'slate'], done: ['Done', 'green'], refused: ['Not allowed', 'rose'], failed: ['Failed', 'rose'], approval: ['Waiting for you', 'amber'] };

export function ActivityRow(ctx: AppContext, activity: Activity, answer: (approval: Approval, approve: boolean) => void): Node {
  const icon = activity.status === 'done' ? Icon('check', { size: 'sm', tint: 'green' }) : activity.status === 'approval' ? Icon('clock', { size: 'sm', tint: 'amber' })
    : activity.status === 'running' ? html`<span class="eva-spin sm"></span>` : Icon('x', { size: 'sm', tint: 'rose' });
  const records = (activity.records ?? []).filter(record => record.model && record.id);
  const row = html`<details class="eva-act" class:running=${activity.status === 'running'}>
    <summary>${icon}<b>${activity.status === 'refused' ? `Could not ${activity.label.charAt(0).toLowerCase()}${activity.label.slice(1).replace(/…$/, '')}` : activity.label}</b>
      ${activity.summary ? html`<span class="eva-sum">· ${activity.summary}</span>` : activity.status === 'refused' ? html`<span class="eva-sum">· not allowed for your role</span>` : null}
      ${Icon('chevron-right', { size: 'xs', className: 'chev' })}</summary>
    <dl class="eva-detail"><dt>Tool</dt><dd><code>${activity.tool}</code></dd>
      <dt>Status</dt><dd>${StatusPill({ label: STATUS[activity.status][0], tint: STATUS[activity.status][1] })}</dd>
      ${activity.duration_ms !== undefined ? html`<dt>Time</dt><dd class="num">${activity.duration_ms} ms</dd>` : null}
      ${activity.inputs ? html`<dt>Inputs</dt><dd><code class="eva-code">${JSON.stringify(activity.inputs)}</code></dd>` : null}
      ${records.length ? html`<dt>Records</dt><dd>${records.slice(0, 8).map((record, index) => html`${index ? ', ' : ''}<a href=${ctx.router.href('record', { model: record.model!, id: String(record.id) })}>${record.model!.replace(/_/g, ' ')} ${record.id}</a>`)}${records.length > 8 ? ` and ${records.length - 8} more` : ''}</dd>` : null}
      ${activity.error ? html`<dt>Reason</dt><dd>${activity.error}</dd>` : null}</dl></details>`;
  return activity.approval && activity.approval.status === 'pending' ? html`${row}${ApprovalCard(activity.approval, answer)}` : row;
}

const GROUP_TITLE: Record<string, string> = { create: 'Eva wants to create a record', update: 'Eva wants to change a record', confirm: 'Eva wants to confirm a document',
  action: 'Eva wants to run an action', destructive: 'Eva wants to make a change that can be hard to undo' };

export function ApprovalCard(approval: Approval, answer: (approval: Approval, approve: boolean) => void): Node {
  const summary = approval.summary;
  const facts: Array<[string, Child]> = [];
  if (summary.operation || summary.action || summary.service) facts.push(['Action', String(summary.operation ?? summary.action ?? summary.service).replace(/_/g, ' ')]);
  if (approval.tool === 'delete_record') facts.push(['Action', 'Delete']);
  if (summary.model) facts.push(['Record', `${summary.model.replace(/_/g, ' ')}${summary.id ? ` ${summary.id}` : ' (new)'}`]);
  for (const [key, value] of Object.entries(summary.record ?? {}).slice(0, 5)) facts.push([key.replace(/_/g, ' '), String(value ?? '')]);
  for (const [key, value] of Object.entries(summary.values ?? {}).slice(0, 8)) if (typeof value !== 'object') facts.push([`New ${key.replace(/_/g, ' ')}`, String(value ?? '')]);
  const busy = cell(false);
  const go = (approve: boolean) => { if (busy.peek()) return; busy.set(true); answer(approval, approve); };
  return html`<div class="eva-approve" role="group" aria-label="Approval needed">
    <div class="eva-approve-h">${Icon('lock', { size: 'sm' })}${GROUP_TITLE[summary.group ?? ''] ?? 'Eva wants to make a change'}</div>
    <div class="eva-approve-b"><dl class="eva-kv">${facts.map(([label, value]) => html`<dt>${label}</dt><dd>${value}</dd>`)}</dl>
      <p>Your approval covers this one change, exactly as shown, and nothing else. If the record changes first, Eva asks again.</p></div>
    <div class="eva-approve-f"><small>Waiting since ${stamp(approval.created_at)}</small>
      ${Button({ label: 'Decline', size: 'small', disabled: busy, onClick: () => go(false) })}
      ${Button({ label: 'Approve', icon: 'check', size: 'small', variant: 'primary', disabled: busy, onClick: () => go(true) })}</div></div>`;
}

export function FileChip(props: { name: string; href: string; detail?: string }): Node {
  const kind = props.name.split('.').pop()?.toLowerCase() ?? '';
  const tint = ['xlsx', 'csv'].includes(kind) ? 'green' : kind === 'pdf' ? 'rose' : ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(kind) ? 'violet' : 'blue';
  return html`<a class="eva-file" href=${props.href} download=${props.name}>${html`<span class=${`eva-file-tile b-${tint}`}>${Icon('file-text', { size: 'sm' })}</span>`}
    <span class="grow"><b>${props.name}</b>${props.detail ? html`<small>${props.detail}</small>` : null}</span>${Icon('download', { size: 'sm' })}</a>`;
}

// ---------- the message box ----------

interface ComposerOptions {
  eva: Eva; status: Cell<EvaStatus | null>; busy: Cell<boolean>;
  send(text: string, files: FileRef[]): Promise<void>;
  running?: () => number | null; stop?: (run: number) => void; waiting?: () => boolean;
}

function Composer(options: ComposerOptions): { node: HTMLElement; fill(text: string): void } {
  const { eva, status, busy } = options;
  const text = cell(''), files = cell<FileRef[]>([]);
  let area: HTMLTextAreaElement | null = null, picker: HTMLInputElement | null = null;
  const exhausted = () => Boolean(status.get()?.allowance.exhausted);
  const submit = async () => {
    const value = text.peek().trim();
    if (!value || busy.peek()) return;
    busy.set(true);
    try {
      await options.send(value, files.peek());
      text.set('');
      files.set([]);
    } catch (failure) {
      toast(errorText(failure), { icon: 'info' });
      if (failure instanceof ApiError && failure.kind === 'limited') { try { status.set(await eva.status()); } catch { /* shown already */ } }
    } finally { busy.set(false); }
  };
  const upload = async (list: FileList | null) => {
    for (const file of Array.from(list ?? [])) {
      if (files.peek().length >= (status.peek()?.files.max_files ?? 5)) { toast('Attach up to 5 files to one message', { icon: 'info' }); break; }
      if (file.size > (status.peek()?.files.max_size ?? 1048576)) { toast(`${file.name} is larger than 1 MB`, { icon: 'info' }); continue; }
      try { files.set([...files.peek(), await eva.upload(file.name, await readFile(file))]); }
      catch (failure) { toast(errorText(failure), { icon: 'info' }); }
    }
    if (picker) picker.value = '';
  };
  const attach = (anchor: HTMLElement) => openPopover(anchor, close => Menu([
    { label: 'Upload from this computer', icon: 'upload', onSelect: () => picker?.click() },
    { label: 'Choose a file in Evlet', icon: 'paperclip', onSelect: () => chooseFile(eva, file => files.set([...files.peek(), file])) },
  ], close), { label: 'Attach' });
  const keydown = (event: KeyboardEvent) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); } };

  const node = html`<div class="eva-compose-wrap">${() => exhausted() ? html`<div class="eva-quota">${Icon('info', { tint: 'amber' })}<div>
      <strong>You have used your Eva allowance for now</strong>
      <p>${() => { const allowance = status.get()!.allowance; return `${allowance.unit === 'aed' ? 'AED ' : ''}${allowance.used} of ${allowance.unit === 'aed' ? 'AED ' : ''}${allowance.limit ?? '—'}${allowance.unit === 'aed' ? '' : ` ${allowance.unit}`} used. It resets on ${allowance.resets_text ?? stamp(allowance.resets)} GST. Your chats and files stay here. Ask your administrator if you need more.`; }}</p></div></div>`
    : html`<div class="eva-compose">
      ${when(() => files.get().length > 0, () => html`<div class="eva-chips">${() => files.get().map(file => html`<span class="eva-chip">${Icon('paperclip', { size: 'xs' })}${file.name}
        <button type="button" aria-label=${`Remove ${file.name}`} on:click=${() => files.set(files.peek().filter(item => item.id !== file.id))}>${Icon('x', { size: 'xs' })}</button></span>`)}</div>`)}
      <textarea rows="2" placeholder=${() => options.waiting?.() ? 'Answer the approval above first' : 'Ask Eva about your records, or ask it to do something'} aria-label="Message Eva"
        prop:value=${text} disabled=${() => Boolean(options.waiting?.())} ref=${(el: HTMLTextAreaElement) => { area = el; }}
        on:input=${(event: Event) => text.set((event.target as HTMLTextAreaElement).value)} on:keydown=${keydown}></textarea>
      <div class="eva-compose-bar">
        ${IconButton({ icon: 'paperclip', tip: 'Attach a file', onClick: event => attach(event.currentTarget as HTMLElement) })}
        <input type="file" hidden multiple accept=${() => (status.get()?.files.kinds ?? []).join(',')} ref=${(el: HTMLInputElement) => { picker = el; }}
          on:change=${(event: Event) => void upload((event.target as HTMLInputElement).files)}>
        <span class="grow"></span><span class="muted eva-small">Uses your permissions</span>
        ${() => { const run = options.running?.(); return run
          ? html`<button type="button" class="eva-send stop" aria-label="Stop" on:click=${() => options.stop?.(run)}>${Icon('circle', { size: 'sm' })}</button>`
          : html`<button type="button" class="eva-send" aria-label="Send" disabled=${() => busy.get() || !text.get().trim() || Boolean(options.waiting?.())} on:click=${() => void submit()}>${Icon('arrow-up', { size: 'sm' })}</button>`; }}
      </div></div>`}
    <p class="eva-hint">Eva can make mistakes. Check important numbers before you rely on them.</p></div>` as HTMLElement;
  return { node, fill: value => { text.set(value); area?.focus(); } };
}

function chooseFile(eva: Eva, picked: (file: FileRef) => void) {
  const query = cell(''), found = cell<Library | null>(null), busy = cell(false);
  const search = async () => { try { found.set(await eva.library(query.peek())); } catch (failure) { toast(errorText(failure), { icon: 'info' }); } };
  void search();
  let timer: ReturnType<typeof setTimeout> | null = null;
  openModal({ title: 'Choose a file in Evlet', icon: 'paperclip', tint: 'blue',
    body: close => html`<label class="eva-search">${Icon('search', { size: 'sm' })}<input class="input" placeholder="Search files you can open" aria-label="Search files" prop:value=${query}
        on:input=${(event: Event) => { query.set((event.target as HTMLInputElement).value); if (timer) clearTimeout(timer); timer = setTimeout(() => void search(), 200); }}></label>
      ${() => { const data = found.get(); if (!data) return html`<p class="eva-none">Loading…</p>`;
        const records = data.records.map(item => html`<button type="button" class="eva-pick" disabled=${busy} on:click=${async () => {
          busy.set(true);
          try { picked(await eva.importFile(item.model, item.record_id, item.file_id)); close(); } catch (failure) { toast(errorText(failure), { icon: 'info' }); } finally { busy.set(false); }
        }}>${Icon('file-text', { size: 'sm', tint: 'blue' })}<span class="grow"><b>${item.name}</b><small>On ${item.model.replace(/_/g, ' ')} ${item.record_id} · ${size(item.size)}</small></span></button>`);
        const chats = data.chats.map(item => html`<button type="button" class="eva-pick" on:click=${() => { picked({ id: item.id, name: item.name, size: item.size, kind: 'upload' }); close(); }}>
          ${Icon('message-square', { size: 'sm', tint: 'violet' })}<span class="grow"><b>${item.name}</b><small>From ${item.conversation ? `the chat "${item.conversation}"` : 'an earlier chat'} · ${size(item.size)}</small></span></button>`);
        return records.length + chats.length ? html`<div class="eva-picks">${records}${chats}</div>` : html`<p class="eva-none">No files match.</p>`; }}
      <p class="muted eva-small">Only files on records you can open are listed. Adding one to a chat doesn't share it with anyone.</p>`,
    footer: close => DialogFooter({ cancel: close, submit: { label: 'Done', onClick: close } }) });
}

// ---------- Markdown, built as DOM nodes (never parsed as HTML) ----------

export function Markdown(source: string): Node {
  const root = document.createElement('div');
  root.className = 'eva-md';
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  let index = 0;
  const inline = (text: string, into: HTMLElement) => {
    const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      into.append(text.slice(last, match.index));
      const piece = match[0];
      const element = document.createElement(piece.startsWith('**') ? 'strong' : 'code');
      element.textContent = piece.startsWith('**') ? piece.slice(2, -2) : piece.slice(1, -1);
      into.append(element);
      last = match.index! + piece.length;
    }
    into.append(text.slice(last));
    return into;
  };
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    if (line.trim().startsWith('```')) {
      const pre = document.createElement('pre'), code = document.createElement('code');
      const body: string[] = [];
      index++;
      while (index < lines.length && !lines[index].trim().startsWith('```')) body.push(lines[index++]);
      index++;
      code.textContent = body.join('\n');
      pre.append(code);
      root.append(pre);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && index + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1])) {
      const cells = (text: string) => text.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
      const wrap = document.createElement('div'), table = document.createElement('table'), head = document.createElement('tr');
      wrap.className = 'eva-table';
      for (const cell of cells(line)) head.append(inline(cell, document.createElement('th')));
      const thead = document.createElement('thead'); thead.append(head); table.append(thead);
      const tbody = document.createElement('tbody');
      index += 2;
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
        const row = document.createElement('tr');
        for (const cell of cells(lines[index++])) {
          const td = inline(cell, document.createElement('td'));
          if (/^-?[\d,]+(\.\d+)?%?$/.test(cell) || /^(AED|USD)\s?-?[\d,]+(\.\d+)?$/.test(cell)) td.className = 'r num';
          row.append(td);
        }
        tbody.append(row);
      }
      table.append(tbody);
      wrap.append(table);
      root.append(wrap);
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) { root.append(inline(heading[2], document.createElement(heading[1].length <= 2 ? 'h3' : 'h4'))); index++; continue; }
    if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const list = document.createElement(ordered ? 'ol' : 'ul');
      while (index < lines.length && /^\s*([-*]|\d+[.)])\s+/.test(lines[index])) list.append(inline(lines[index++].replace(/^\s*([-*]|\d+[.)])\s+/, ''), document.createElement('li')));
      root.append(list);
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|\s*([-*]|\d+[.)])\s+|```|\s*\|)/.test(lines[index])) paragraph.push(lines[index++]);
    if (!paragraph.length) paragraph.push(lines[index++]);
    root.append(inline(paragraph.join(' '), document.createElement('p')));
  }
  return root;
}

// ---------- Ask Eva from a record ----------

/** The side panel on a record: a new chat with the record as its context, kept like any other chat. */
export function openEvaPanel(ctx: AppContext, status: Cell<EvaStatus | null>, model: { name: string; label: string }, id: string, title: string) {
  {
    const eva = new Eva(ctx.api);
    const conversation = cell<number | null>(null);
    openDrawer({ title: 'Eva', icon: 'sparkles', tint: 'violet', className: 'eva-drawer',
      body: close => html`<div class="eva-panel">
        <div class="eva-context">${Icon('link', { size: 'sm', tint: 'blue' })}<span class="grow">About <b>${model.label} ${title}</b><br><small class="muted">Eva reads it with your permissions</small></span>
          ${() => conversation.get() ? Button({ label: 'Open in Eva', icon: 'maximize-2', size: 'small', variant: 'ghost', onClick: () => { close(); void ctx.router.go('evaChat', { conversation: String(conversation.get()) }); } }) : null}</div>
        ${() => conversation.get()
          ? EvaThread({ ctx, eva, status, id: conversation.get()!, compact: true })
          : EvaWelcome({ ctx, eva, status, context: { model: model.name, id }, onStarted: started => conversation.set(started.id),
            starters: [`Summarise this ${model.label.toLowerCase()}`, `What changed on this ${model.label.toLowerCase()} recently?`, `Draft a short update about this ${model.label.toLowerCase()}`] })}</div>` });
  }
}
