// Eva's screens against a stub of /api/_eva: hidden without Eva, a chat that streams activity rows and an
// answer, approvals, the allowance-used state, scheduled tasks and the Controls usage page.
import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, requests, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { Markdown } from '../eva/chat';

const reply = (body: unknown, status = 200) => ({ status, body, etag: null });
const status = (exhausted = false) => ({ models: ['Standard'], model: 'Standard', choose_model: false, tasks: true, pending_approvals: 0,
  allowance: { limit: '25.00', unit: 'aed', period: 'week', used: exhausted ? '25.00' : '6.80', resets: 1794268800, resets_text: '09-11-2026 00:00', exhausted },
  files: { max_size: 1048576, max_files: 5, kinds: ['.csv', '.pdf'] } });
const conversation = { id: 7, title: 'Overdue invoices', created_at: 1793600000, updated_at: 1793600000, archived: false, task_id: null, context: null };
const stored = (runs: unknown[], messages: unknown[]) => ({ conversation, messages, runs, files: [], usage: { tokens: 2770, cost: '0.06' } });
const done = { id: 3, status: 'completed', error: null, model: 'Standard', origin: 'eva', started_at: 1793600000, finished_at: 1793600010,
  activities: [{ call_id: 'c1', tool: 'list_records', label: 'Read invoice', status: 'done', summary: '23 records', duration_ms: 180,
    inputs: { model: 'invoice', filters: ['status'] }, records: [{ model: 'invoice', id: 118 }] }] };
const messages = [{ id: 1, run_id: 3, role: 'user', content: 'Which invoices are overdue?', created_at: 1793600000, files: [] },
  { id: 2, run_id: 3, role: 'assistant', content: 'There are **23 invoices** overdue.\n\n| Customer | Outstanding (AED) |\n|---|---|\n| Gulf Freight LLC | 148,200.00 |', created_at: 1793600010, files: [] }];

afterEach(() => stopAll());

const evaRoutes = (extra: Route[] = [], exhausted = false): Route[] => [
  ['GET', /\/api\/_eva$/, reply(status(exhausted))],
  ['GET', /\/api\/_eva\/conversations(\?.*)?$/, reply({ items: [conversation], archived: 2, next: null })],
  ...extra, ...adminRoutes(),
];

describe('Eva', () => {
  it('is not in the sidebar when the server has no Eva for this person', async () => {
    const { host } = start(adminRoutes(), '/docs');
    await until(() => expect(host.querySelector('.sidebar')).not.toBeNull());
    expect(byText(host, '.nav-item', 'Eva')).toBeNull();
  });

  it('rebuilds a stored chat: activity rows open for details, the answer renders as a table', async () => {
    const { host } = start(evaRoutes([['GET', /\/api\/_eva\/conversations\/7$/, reply(stored([done], messages))]]), '/eva/7');
    await until(() => expect(text(host)).toContain('There are 23 invoices overdue.'));
    expect(byText(host, '.nav-item', 'Eva')).not.toBeNull();
    const row = host.querySelector('.eva-act') as HTMLDetailsElement;
    expect(text(row.querySelector('summary'))).toBe('Read invoice · 23 records');
    expect(text(row.querySelector('.eva-detail'))).toContain('list_records');
    expect(row.querySelector('.eva-detail a')!.getAttribute('href')).toBe('/app/invoice/118');
    expect(text(host.querySelector('.eva-table'))).toContain('Gulf Freight LLC148,200.00');
    expect(host.querySelector('.eva-table td.r')).not.toBeNull();
    expect(text(host.querySelector('.eva-thread-head'))).toContain('2,770 tokens · AED 0.06');
    expect(text(host.querySelector('.eva-side'))).toContain('Overdue invoices');
  });

  it('sends a message, follows the run and shows it finished', async () => {
    let polls = 0;
    const events = [
      { id: 1, type: 'run.started', at: 1, run_id: 4 }, { id: 2, type: 'tool.started', at: 1, call_id: 'c9', tool: 'list_records', label: 'Reading project…' },
      { id: 3, type: 'tool.completed', at: 1, call_id: 'c9', tool: 'list_records', label: 'Read project', summary: '1 record', duration_ms: 9, inputs: { model: 'project' }, records: [] },
      { id: 4, type: 'message.delta', at: 1, text: 'One project.' }, { id: 5, type: 'run.completed', at: 1, run_id: 4, status: 'completed' }];
    const finished = { ...done, id: 4, activities: [{ call_id: 'c9', tool: 'list_records', label: 'Read project', status: 'done', summary: '1 record', duration_ms: 9, inputs: { model: 'project' }, records: [] }] };
    const { host, sent } = start(evaRoutes([
      ['POST', /\/api\/_eva\/conversations$/, reply(conversation, 201)],
      ['POST', /\/api\/_eva\/conversations\/7\/messages$/, reply({ run: { id: 4, status: 'running' }, message: { id: 9, run_id: 4, role: 'user', content: 'How many projects?', created_at: 1, files: [] } }, 202)],
      ['GET', /\/api\/_eva\/runs\/4\/events/, () => reply({ items: polls++ ? [] : events, status: 'completed', done: polls > 1 })],
      ['GET', /\/api\/_eva\/conversations\/7$/, () => reply(stored(polls > 1 ? [finished] : [{ ...finished, status: 'running', activities: [] }],
        [{ id: 9, run_id: 4, role: 'user', content: 'How many projects?', created_at: 1, files: [] }, ...(polls > 1 ? [{ id: 10, run_id: 4, role: 'assistant', content: 'One project.', created_at: 2, files: [] }] : [])]))],
    ]), '/eva');
    await until(() => expect(text(host)).toContain('What can I help with?'));
    typeInto(host.querySelector('.eva-compose textarea')!, 'How many projects?');
    click(host.querySelector('.eva-send'));
    await until(() => expect(requests(sent, 'POST', /\/messages$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /\/messages$/)[0].body).toEqual({ text: 'How many projects?', files: [] });
    await until(() => expect(text(host.querySelector('.eva-msgs'))).toContain('One project.'), 4000);
    await until(() => expect(text(host.querySelector('.eva-act summary'))).toBe('Read project · 1 record'), 4000);
  });

  it('shows an approval card and answers it as the person', async () => {
    const approval = { id: 12, tool: 'run_action', origin: 'eva', client: 'Eva', run_id: 5, arguments: {}, status: 'pending', created_at: 1793600000, expires_at: 1793686400,
      summary: { model: 'invoice', id: 142, action: 'confirm', group: 'confirm', record: { name: 'INV-2026-0142', total: '18375.00' } } };
    const waiting = { ...done, id: 5, status: 'awaiting_approval', activities: [{ call_id: 'a1', tool: 'run_action', label: 'Running confirm on invoice 142', status: 'approval', approval }] };
    const { host, sent } = start(evaRoutes([
      ['GET', /\/api\/_eva\/conversations\/7$/, reply(stored([waiting], [{ id: 1, run_id: 5, role: 'user', content: 'Confirm it', created_at: 1, files: [] }]))],
      ['POST', /\/api\/_tools\/approvals\/12$/, reply({ ...approval, status: 'approved' })],
      ['GET', /\/api\/_eva\/runs\/5\/events/, reply({ items: [], status: 'completed', done: true })],
    ]), '/eva/7');
    await until(() => expect(host.querySelector('.eva-approve')).not.toBeNull());
    const card = host.querySelector('.eva-approve')!;
    expect(text(card)).toContain('Eva wants to confirm a document');
    expect(text(card)).toContain('INV-2026-0142');
    expect((host.querySelector('.eva-compose textarea') as HTMLTextAreaElement).disabled).toBe(true);
    click(byText(card, 'button', 'Approve'));
    await until(() => expect(requests(sent, 'POST', /approvals\/12$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /approvals\/12$/)[0].body).toEqual({ approve: true });
  });

  it('replaces the message box with the allowance notice when it is used up', async () => {
    const { host } = start(evaRoutes([], true), '/eva');
    await until(() => expect(text(host)).toContain('You have used your Eva allowance for now'));
    expect(text(host.querySelector('.eva-quota'))).toContain('AED 25.00 of AED 25.00 used. It resets on 09-11-2026 00:00 GST.');
    expect(host.querySelector('.eva-compose textarea')).toBeNull();
  });

  it('reads a schedule from plain words and creates the task', async () => {
    const task = { id: 1, name: 'Review my overdue invoices', instructions: 'Review my overdue invoices.', cron: '0 9 * * 1', timezone: 'Asia/Dubai', active: true,
      allow_changes: false, created_at: 1, last_run_at: null, last_status: null, next_run_at: 1794200400 };
    let created = false;
    const { host, sent } = start(evaRoutes([
      ['GET', /\/api\/_eva\/tasks$/, () => reply({ items: created ? [task] : [], limit: 10, jobs: true })],
      ['POST', /\/api\/_eva\/tasks\/parse$/, reply({ cron: '0 9 * * 1', summary: 'Mondays at 09:00', instructions: 'Review my overdue invoices.', name: 'Review my overdue invoices',
        timezone: 'Asia/Dubai', next: ['09-11-2026 09:00', '16-11-2026 09:00', '23-11-2026 09:00'] })],
      ['POST', /\/api\/_eva\/tasks$/, () => { created = true; return reply(task, 201); }],
    ]), '/eva/tasks');
    await until(() => expect(text(host)).toContain('No scheduled tasks yet'));
    click(byText(host, '.ph-actions button', 'New task'));
    await until(() => expect(document.querySelector('#eva-task-text')).not.toBeNull());
    typeInto(document.querySelector('#eva-task-text')!, 'Every Monday at 9 AM, review my overdue invoices.');
    await until(() => expect(text(document.querySelector('.eva-parsed'))).toContain('Mondays at 09:00'), 3000);
    expect((document.querySelector('#eva-task-cron') as HTMLInputElement).value).toBe('0 9 * * 1');
    click(byText(document.body, '.modal button', 'Create task'));
    await until(() => expect(requests(sent, 'POST', /\/api\/_eva\/tasks$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /\/api\/_eva\/tasks$/)[0].body).toMatchObject({ cron: '0 9 * * 1', allow_changes: false, name: 'Review my overdue invoices' });
    await until(() => expect(text(host)).toContain('Review my overdue invoices'));
  });

  it("shows an administrator's pause and offers no way around it", async () => {
    const task = { id: 2, name: 'Weekly', instructions: 'Summarise.', cron: '0 9 * * 1', timezone: 'Asia/Dubai', active: false, paused_by_admin: true,
      allow_changes: false, created_at: 1, last_run_at: null, last_status: null, next_run_at: null };
    const { host } = start(evaRoutes([['GET', /\/api\/_eva\/tasks$/, reply({ items: [task], limit: 10, jobs: true })]]), '/eva/tasks');
    await until(() => expect(text(host)).toContain('Paused by an administrator'));
    expect((host.querySelector('[aria-label="Weekly on"]') as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('Eva Markdown', () => {
  it('builds elements and never parses HTML', () => {
    const node = Markdown('**Hi** <img src=x onerror=alert(1)>\n\n- one\n- `two`\n\n```\n<b>code</b>\n```') as HTMLElement;
    expect(node.querySelector('img')).toBeNull();
    expect(node.querySelector('strong')!.textContent).toBe('Hi');
    expect(node.querySelectorAll('li')).toHaveLength(2);
    expect(node.querySelector('pre code')!.textContent).toBe('<b>code</b>');
  });
});
