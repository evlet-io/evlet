// The workspace editor (docs/ui.md, Customizing workspaces): reorder, hide and rename, add a shortcut, reset, and
// the server's 422 problems beside the rows they name. It talks to the Controls workspace routes.
import { afterEach, describe, expect, it } from 'vitest';
import { byText, click, invoice, press, requests, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy, type Sent } from './server';
import { editable, isDeclared, keyOf, overrideOf, problemsAt, type ListingItem } from '../app/workspace-editor';
import type { WorkspaceSchema } from '../model/schema';

afterEach(stopAll);

const billing: WorkspaceSchema = { key: 'billing', label: 'Billing', icon: 'receipt', app: 'app', source: 'declared', groups: [
  { key: 'invoices', label: 'Invoices', icon: null, items: [
    { key: 'model:invoice', kind: 'model', label: 'Invoices', model: 'invoice', view: 'list', filters: null },
    { key: 'model:invoice:board', kind: 'model', label: 'Board', model: 'invoice', view: 'board', filters: null },
  ] },
  { key: 'people', label: 'Customers', icon: null, items: [
    { key: 'model:customer', kind: 'model', label: 'Customers', model: 'customer', view: 'list', filters: null },
  ] },
] };
const entry = (override: ListingItem['override'] = null): ListingItem => ({ key: 'billing', label: 'Billing', source: 'declared', app: 'app', override,
  changed_by: override ? 'admin' : null, changed_at: override ? 1 : null, declared: billing, effective: billing });

describe('the editor model', () => {
  it('lays a saved override over the declared groups, keeping hidden rows and declared labels', () => {
    const view = editable(entry({ label: 'Money', groups: [{ key: 'people', items: [{ key: 'model:invoice:board', hidden: true }, { shortcut: { url: '/docs', label: 'Docs' } }] }] }));
    expect(view.label).toBe('Money');
    expect(view.groups.map(group => [group.key, group.items.map(item => [item.key ?? item.shortcut?.url, item.label, item.hidden])])).toEqual([
      ['people', [['model:invoice:board', 'Board', true], ['/docs', 'Docs', false], ['model:customer', 'Customers', false]]],
      ['invoices', [['model:invoice', 'Invoices', false]]],
    ]);
  });

  it('sends only what differs, and a view equal to the declared one is a reset', () => {
    const view = editable(entry());
    expect(isDeclared(view, entry())).toBe(true);
    view.groups.reverse();
    view.groups[1].items[0].label = 'All invoices';
    view.groups[1].items[1].hidden = true;
    expect(overrideOf(view)).toEqual({ groups: [
      { key: 'people', items: [{ key: 'model:customer' }] },
      { key: 'invoices', items: [{ key: 'model:invoice', label: 'All invoices' }, { key: 'model:invoice:board', hidden: true }] },
    ] });
    expect(isDeclared(view, entry())).toBe(false);
  });

  it('makes keys for new workspaces and places 422 problems on their rows', () => {
    expect(keyOf('Sales desk', new Set(['sales_desk']))).toBe('sales_desk_2');
    expect(keyOf('2026 plan', new Set())).toBe('w_2026_plan');
    const found = problemsAt({ 'groups.0.items.2.shortcut': 'Unknown model', 'groups.1.label': 'Too long', label: 'Empty', _form: 'Send an object' });
    expect(found.form).toEqual(['Name: Empty', 'Send an object']);
    expect([...found.at]).toEqual([['groups.0.items.2', ['Unknown model']], ['groups.1', ['Too long']]]);
  });
});

describe('the editor dialog', () => {
  function routes(answer: (sent: Sent) => any) {
    const schema = copy(invoice.schema);
    schema.body.workspaces = [billing];
    return [
      ['GET', /\/api\/auth\/session$/, invoice.session],
      ['GET', /\/api\/schema$/, schema],
      ['GET', /\/api\/invoice\?/, invoice.invoices],
      ['GET', /\/api\/invoice\/(measures|aggregate|list_tabs)/, { status: 200, etag: null, body: { measures: {}, items: [], group_by: [] } }],
      ['GET', /\/api\/customer\?/, screen.customers_page1],
      ['GET', /\/api\/_settings\/me$/, screen.settings_me],
      ['GET', /\/api\/_controls\/workspaces$/, { status: 200, etag: null, body: { items: [entry()], problems: [] } }],
      ['PUT', /\/api\/_controls\/workspaces\/[a-z_0-9]+$/, answer],
      ['DELETE', /\/api\/_controls\/workspaces\/billing$/, { status: 200, etag: null, body: { items: [entry()], problems: [] } }],
    ] as Route[];
  }
  const ok = { status: 200, etag: null, body: { items: [entry({ groups: [] })], problems: [] } };
  async function open(answer: (sent: Sent) => any = () => ok) {
    const started = start(routes(answer), '/app/invoice');
    await until(() => expect(started.host.querySelector('.app-switch')).not.toBeNull());
    click(started.host.querySelector('.app-switch'));
    await until(() => expect(document.querySelector('.ws-pop .ws-edit')).not.toBeNull());
    click(document.querySelector('.ws-pop .ws-edit'));
    await until(() => expect(document.querySelector('.wse')).not.toBeNull());
    return started;
  }
  const groups = () => [...document.querySelectorAll('.wse-group')].map(group => [
    (group.querySelector('.wse-gh .wse-name') as HTMLInputElement).value,
    [...group.querySelectorAll('.wse-item .wse-name')].map(input => (input as HTMLInputElement).value)]);
  const save = () => click(byText(document.body, '.modal-f button', 'Save'));
  const body = (sent: Sent[], method: string) => { const sentBody = requests(sent, method, /_controls\/workspaces\//).pop()!.body; return typeof sentBody === 'string' ? JSON.parse(sentBody) : sentBody; };

  it('reorders, hides and renames, then saves the override and reloads the sidebar', async () => {
    const { sent } = await open();
    expect(groups()).toEqual([['Invoices', ['Invoices', 'Board']], ['Customers', ['Customers']]]);
    // Move the Customers group up with the keyboard.
    const grip = document.querySelectorAll<HTMLElement>('[data-drag-handle="ws-group"]')[1];
    press(grip, ' ');
    press(grip, 'ArrowUp');
    press(grip, ' ');
    expect(groups().map(group => group[0])).toEqual(['Customers', 'Invoices']);
    click(document.querySelector('[aria-label="Hide Board"]'));
    typeInto(document.querySelectorAll<HTMLInputElement>('.wse-item .wse-name')[1], 'All invoices');
    save();
    await until(() => expect(requests(sent, 'PUT', /_controls\/workspaces\/billing$/)).toHaveLength(1));
    expect(body(sent, 'PUT')).toEqual({ groups: [
      { key: 'people', items: [{ key: 'model:customer' }] },
      { key: 'invoices', items: [{ key: 'model:invoice', label: 'All invoices' }, { key: 'model:invoice:board', hidden: true }] },
    ] });
    // The sidebar follows the schema read again after the save.
    await until(() => expect(requests(sent, 'GET', /\/api\/schema$/).length).toBe(2));
    await until(() => expect(document.querySelector('.toast')?.textContent).toContain('Saved Billing'));
  });

  it('adds a shortcut and shows the server\'s 422 problem beside it', async () => {
    const { sent } = await open(() => ({ status: 422, etag: null, body: { error: 'Check the workspace', fields: { 'groups.1.items.1.shortcut': 'Unknown model nope' } } }));
    click(byText(document.querySelectorAll('.wse-group')[1] as HTMLElement, '.wse-add', 'Add shortcut'));
    const form = document.querySelector('.wse-new')!;
    const opens = form.querySelector<HTMLSelectElement>('select[aria-label="Opens"]')!;
    opens.value = 'url';
    opens.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => expect(form.querySelector('input[aria-label="Address"]')).not.toBeNull());
    typeInto(form.querySelector<HTMLInputElement>('input[aria-label="Address"]')!, '/docs');
    typeInto(form.querySelector<HTMLInputElement>('input[aria-label="Label"]')!, 'Docs');
    click(byText(form as HTMLElement, 'button', 'Add'));
    expect(groups()[1]).toEqual(['Customers', ['Customers', 'Docs']]);
    save();
    await until(() => expect(text(document.querySelectorAll('.wse-group')[1].querySelectorAll('.wse-item')[1].querySelector('.wse-err'))).toBe('Unknown model nope'));
    expect(body(sent, 'PUT').groups[1].items[1]).toEqual({ shortcut: { url: '/docs', label: 'Docs' } });
  });

  it('resets to the declared workspace after asking', async () => {
    const { sent } = await open();
    click(byText(document.body, '.modal-f button', 'Reset to declared'));
    await until(() => expect(byText(document.body, '.modal-f button', /^Reset$/)).toBeTruthy());
    click(byText(document.body, '.modal-f button', /^Reset$/));
    await until(() => expect(requests(sent, 'DELETE', /_controls\/workspaces\/billing$/)).toHaveLength(1));
  });
});
