// Keyboard shortcuts on record forms, lists, side panels and dialogs (docs/ui.md#keyboard-shortcuts),
// on a Mac (⌘) and elsewhere (Ctrl), over the recorded responses.
import { afterEach, describe, expect, it } from 'vitest';
import { adminRoutes, byText, click, invoice, lastToast, press, recorded, requests, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, id, type Recorded } from './server';
import { formatKeys, keyMatches, withShortcut } from '../ui/shortcuts';
import { DialogFooter, openModal } from '../ui/layers';
import { html } from '../dom';

/** Pretend to be a Mac (⌘) or a Windows PC (Ctrl) for one test. */
function platform(name: 'MacIntel' | 'Win32') { Object.defineProperty(navigator, 'platform', { value: name, configurable: true }); }
afterEach(() => { stopAll(); delete (navigator as any).platform; });

const mod = (mac: boolean) => (mac ? { metaKey: true } : { ctrlKey: true });
const key = (target: Element | Document, letter: string, init: KeyboardEventInit = {}) =>
  press(target, letter, { code: /^[a-z]$/i.test(letter) ? `Key${letter.toUpperCase()}` : undefined, ...init });
const dialog = () => document.querySelector<HTMLElement>('.modal');
const dialogTitle = () => text(dialog()?.querySelector('h2'));
/** A help sheet group's rows as "keys what". */
const sheetRows = (group: Element | null | undefined) => [...(group?.querySelectorAll('dt') ?? [])].map(dt => `${text(dt)} ${text(dt.nextElementSibling)}`);

describe('shortcut keys and how they are written', () => {
  it('writes Mod as ⌘ on a Mac and Ctrl elsewhere, in tooltips too', () => {
    platform('MacIntel');
    expect(formatKeys('Mod+S')).toBe('⌘S');
    expect(formatKeys('Mod+Shift+D')).toBe('⇧⌘D');
    expect(formatKeys('Alt+N')).toBe('⌥N');
    expect(formatKeys('Alt+Shift+D')).toBe('⌥⇧D');
    expect(withShortcut('Save', 'Mod+S')).toBe('Save  ⌘S');
    platform('Win32');
    expect(formatKeys('Mod+S')).toBe('Ctrl+S');
    expect(formatKeys('Mod+Shift+D')).toBe('Ctrl+Shift+D');
    expect(formatKeys('Alt+N')).toBe('Alt+N');
    expect(withShortcut('Save', 'Mod+S')).toBe('Save  Ctrl+S');
  });

  it('matches ⌘ only on a Mac and Ctrl only elsewhere, and Option+N by its physical key', () => {
    const event = (init: KeyboardEventInit) => new KeyboardEvent('keydown', init);
    platform('MacIntel');
    expect(keyMatches(event({ key: 's', metaKey: true }), 'Mod+S')).toBe(true);
    expect(keyMatches(event({ key: 's', ctrlKey: true }), 'Mod+S')).toBe(false);
    // Option+N types a dead key on a Mac: the physical key still says N.
    expect(keyMatches(event({ key: 'Dead', code: 'KeyN', altKey: true }), 'Alt+N')).toBe(true);
    expect(keyMatches(event({ key: 'D', code: 'KeyD', metaKey: true, shiftKey: true }), 'Mod+Shift+D')).toBe(true);
    expect(keyMatches(event({ key: 'd', code: 'KeyD', metaKey: true }), 'Mod+Shift+D')).toBe(false);
    platform('Win32');
    expect(keyMatches(event({ key: 's', ctrlKey: true }), 'Mod+S')).toBe(true);
    expect(keyMatches(event({ key: 's', metaKey: true }), 'Mod+S')).toBe(false);
    expect(keyMatches(event({ key: '?', shiftKey: true }), '?')).toBe(true);
  });
});

const orderRoutes = (): Route[] => [
  ...adminRoutes(),
  ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
  ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
  ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
  ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }],
  ['PATCH', /\/api\/order\/0000000000001$/, recorded.update],
  ['DELETE', /\/api\/order\/0000000000001$/, { status: 204, etag: null, body: null }],
  ['GET', /\/api\/order\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }],
];
const rowsIn = (host: Element) => [...host.querySelectorAll<HTMLElement>('.grid tbody tr[data-key]')];
const cellOf = (row: Element, col: string) => [...row.querySelectorAll<HTMLElement>('td[data-col]')].find(td => td.dataset.col === col)!;

/** Open the saved order on its Lines tab and change a quantity: an unsaved change. */
async function editedOrder() {
  const started = start(orderRoutes(), '/app/order/0000000000001?tab=t-lines');
  await until(() => expect(rowsIn(started.host).length).toBeGreaterThan(5));
  const qty = cellOf(rowsIn(started.host)[1], 'qty');
  click(qty);
  const input = qty.querySelector('input')!;
  input.value = '3';
  press(input, 'Enter');
  await until(() => expect(text(started.host.querySelector('.ev-savebar'))).toContain('Unsaved changes'));
  return started;
}

describe('record form shortcuts', () => {
  for (const mac of [true, false]) {
    it(`${mac ? '⌘S' : 'Ctrl+S'} saves a saved record like the Save button, and shows the key in its tooltip`, async () => {
      platform(mac ? 'MacIntel' : 'Win32');
      const { host, sent } = await editedOrder();
      const save = byText(host, '.ev-savebar button', 'Save')!;
      save.dispatchEvent(new Event('focus'));
      expect(text(document.querySelector('.tip'))).toBe(mac ? 'Save ⌘S' : 'Save Ctrl+S');
      expect(save.getAttribute('aria-keyshortcuts')).toBe(mac ? 'Meta+S' : 'Control+S');
      // The other platform's key does nothing; the browser's Save Page never opens.
      const wrong = key(host.querySelector('.doc')!, 's', mac ? { ctrlKey: true } : { metaKey: true });
      expect(wrong.defaultPrevented).toBe(false);
      const event = key(host.querySelector('.doc')!, 's', mod(mac));
      expect(event.defaultPrevented).toBe(true);
      await until(() => expect(requests(sent, 'PATCH', /order\/0000000000001$/)).toHaveLength(1));
      await until(() => expect(host.querySelector('.ev-savebar')).toBeNull());
    });
  }

  it('Mod+S with nothing changed does nothing, but still keeps the browser from saving the page', async () => {
    const { host, sent } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(host.querySelector('.doc-head h1')).not.toBeNull());
    const event = key(document.body, 's', mod(false));
    expect(event.defaultPrevented).toBe(true);
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(requests(sent, 'PATCH', /order/)).toHaveLength(0);
  });

  it('Mod+S creates a new record while typing in a field, with what was typed but not yet committed', async () => {
    const created = { status: 201, etag: '"new"', body: { id: id(56), title: 'Acme Trading', rate: '0.00', credit_days: 45 } };
    const { host, sent } = start([...adminRoutes(), ['POST', /\/api\/customer$/, created],
      ['GET', /\/api\/customer\/000000000001r$/, { ...created, status: 200 }]], '/app/customer/new?title=Acme%20Trading');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New customer'));
    const days = host.querySelector<HTMLInputElement>('.fld[data-field="credit_days"] input')!;
    days.focus();
    days.value = '45';
    days.dispatchEvent(new Event('input', { bubbles: true }));
    key(days, 's', mod(false));
    await until(() => expect(requests(sent, 'POST', /customer$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /customer$/)[0].body).toMatchObject({ title: 'Acme Trading', credit_days: 45 });
    await until(() => expect(location.pathname).toBe('/app/customer/000000000001r'));
  });

  it('Mod+Shift+D opens the delete confirmation with Delete focused; the arrows reach Cancel, which closes it', async () => {
    platform('MacIntel');
    const { host, sent } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(host.querySelector('.doc-head h1')).not.toBeNull());
    key(document.body, 'D', { metaKey: true, shiftKey: true });
    expect(dialogTitle()).toMatch(/^Delete /);
    expect(text(document.activeElement)).toBe('Delete');
    // Opening the confirmation deletes nothing, and a held key does not answer it.
    key(document.activeElement!, 'D', { metaKey: true, shiftKey: true, repeat: true });
    expect(dialog()).not.toBeNull();
    expect(requests(sent, 'DELETE', /order/)).toHaveLength(0);
    press(document.activeElement!, 'ArrowRight');
    expect(text(document.activeElement)).toBe('Cancel');
    press(document.activeElement!, 'ArrowRight');
    expect(text(document.activeElement)).toBe('Delete');
    press(document.activeElement!, 'ArrowLeft');
    expect(text(document.activeElement)).toBe('Cancel');
    // Enter presses the focused button (the browser turns it into a click).
    click(document.activeElement);
    expect(dialog()).toBeNull();
    expect(requests(sent, 'DELETE', /order/)).toHaveLength(0);
  });

  it('Mod+S in the delete confirmation deletes; Mod+Shift+D in it cancels', async () => {
    const { host, sent } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(host.querySelector('.doc-head h1')).not.toBeNull());
    key(document.body, 'D', { ctrlKey: true, shiftKey: true });
    key(document.activeElement!, 'D', { ctrlKey: true, shiftKey: true });
    expect(dialog()).toBeNull();
    expect(requests(sent, 'DELETE', /order/)).toHaveLength(0);
    key(document.body, 'D', { ctrlKey: true, shiftKey: true });
    key(document.activeElement!, 's', { ctrlKey: true });
    await until(() => expect(requests(sent, 'DELETE', /order\/0000000000001$/)).toHaveLength(1));
    await until(() => expect(location.pathname).toBe('/app/order'));
  });

  it('offers no delete shortcut where the role cannot delete', async () => {
    const schema = copy(screen.schema);
    schema.body.models.find((model: any) => model.name === 'order').permissions.delete = false;
    const { host } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema], ...orderRoutes().slice(2)], '/app/order/0000000000001');
    await until(() => expect(host.querySelector('.doc-head h1')).not.toBeNull());
    const event = key(document.body, 'D', { ctrlKey: true, shiftKey: true });
    expect(event.defaultPrevented).toBe(false);
    expect(dialog()).toBeNull();
  });

  it('Alt+N opens a new record of the model; Mod+Shift+D on it discards, asking first when something was typed', async () => {
    const { host } = start([...orderRoutes(), ['GET', /\/api\/customer\?/, { status: 200, etag: null, body: { items: [], total: 0, limit: 50, offset: 0 } }]], '/app/order/0000000000001');
    await until(() => expect(host.querySelector('.doc-head h1')).not.toBeNull());
    // A held key opens nothing.
    key(document.body, 'Dead', { code: 'KeyN', altKey: true, repeat: true });
    expect(location.pathname).toBe('/app/order/0000000000001');
    key(document.body, 'Dead', { code: 'KeyN', altKey: true });
    await until(() => expect(location.pathname).toBe('/app/order/new'));
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New order'));
    const title = host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!;
    title.value = 'Draft';
    title.dispatchEvent(new Event('change', { bubbles: true }));
    key(document.body, 'D', { ctrlKey: true, shiftKey: true });
    await until(() => expect(dialogTitle()).toBe('Discard unsaved changes?'));
    // The prompt focuses its main action.
    expect(text(document.activeElement)).toBe('Discard changes');
    key(document.activeElement!, 's', { ctrlKey: true });
    await until(() => expect(location.pathname).toBe('/app/order'));
  });
});

describe('list shortcuts', () => {
  const customers = (): Recorded => screen.customers_page1;
  for (const mac of [true, false]) {
    it(`Alt+N opens a new record from the list (${mac ? 'Mac' : 'Windows'}); the New tooltip says so`, async () => {
      platform(mac ? 'MacIntel' : 'Win32');
      const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, customers()]], '/app/customer');
      await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('55 customers'));
      const button = byText(host, 'button', 'New customer')!;
      button.dispatchEvent(new Event('focus'));
      expect(text(document.querySelector('.tip'))).toBe(mac ? 'New customer ⌥N' : 'New customer Alt+N');
      key(document.body, mac ? 'Dead' : 'n', { code: 'KeyN', altKey: true });
      await until(() => expect(location.pathname).toBe('/app/customer/new'));
    });
  }

  it('keeps the plain keys out of the way while typing: C and / do nothing in the search box', async () => {
    const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, customers()]], '/app/customer');
    await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('55 customers'));
    const search = host.querySelector<HTMLInputElement>('.ev-list .search input, .ev-list input[type="search"]')!;
    search.focus();
    expect(press(search, 'c').defaultPrevented).toBe(false);
    expect(press(search, '/').defaultPrevented).toBe(false);
    expect(press(search, '?', { shiftKey: true }).defaultPrevented).toBe(false);
    expect(dialog()).toBeNull();
    expect(location.pathname).toBe('/app/customer');
    // Out of the field, / focuses the search.
    search.blur();
    press(document.body, '/');
    expect(document.activeElement).toBe(search);
  });

  it('? opens the sheet with this screen\'s shortcuts first, in the platform\'s keys', async () => {
    platform('MacIntel');
    const { host } = start([...adminRoutes(), ['GET', /\/api\/customer\?/, customers()]], '/app/customer');
    await until(() => expect(text(host.querySelector('.ev-list .summary'))).toBe('55 customers'));
    press(document.body, '?', { shiftKey: true });
    expect(dialogTitle()).toBe('Keyboard shortcuts');
    const groups = [...dialog()!.querySelectorAll('.ev-keys-g')].map(group => text(group.querySelector('h3')));
    expect(groups).toEqual(['This screen', 'Everywhere', 'In a dialog']);
    expect(sheetRows(dialog()!.querySelector('.ev-keys-g'))).toEqual(['⌥N New customer', 'C New customer', '/ Search the list', '↩ Open the focused customer']);
    expect(sheetRows(dialog())).toContain('⌘K Search and commands');
    expect(sheetRows(dialog())).toContain('⌘S Confirm, save or submit');
    expect(text(dialog())).not.toContain('Ctrl');
  });

  it('the sheet on a record lists Save and Delete in Ctrl keys off a Mac', async () => {
    platform('Win32');
    const { host } = start(orderRoutes(), '/app/order/0000000000001');
    await until(() => expect(host.querySelector('.doc-head h1')).not.toBeNull());
    press(document.body, '?', { shiftKey: true });
    expect(sheetRows(dialog()!.querySelector('.ev-keys-g'))).toEqual(['Ctrl+S Save changes', 'Ctrl+Shift+D Delete this order', 'Alt+N New order', 'Alt+Shift+D Duplicate this order']);
    expect(sheetRows(dialog())).toContain('Ctrl K Search and commands');
    expect(text(dialog())).not.toContain('⌘');
  });
});

describe('side panel shortcuts', () => {
  const [three] = [String(invoice.invoice_draft.body.id)];
  function routes(): Route[] {
    const body = copy(invoice.schema).body;
    const model = body.models.find((item: { name: string }) => item.name === 'invoice');
    Object.assign(model, { open_in: 'panel', permissions: { ...model.permissions, delete: true } });
    return [
      ['GET', /\/api\/auth\/session$/, invoice.session],
      ['GET', /\/api\/schema$/, { ...invoice.schema, body }],
      ['GET', /\/api\/invoice\?/, invoice.invoices],
      ['GET', /\/api\/invoice\/measures/, invoice.invoice_measures],
      ['GET', /\/api\/invoice\/aggregate/, invoice.invoice_aggregate_by_status],
      ['GET', /\/api\/invoice\/\w+\/measures$/, invoice.invoice_record_measures],
      ['GET', /\/api\/invoice\/\w+\/related\/payment\.invoice/, invoice.invoice_related_payments],
      ['GET', /\/api\/invoice\/\w+\/journal/, invoice.invoice_journal],
      ['GET', /\/api\/invoice\/\w+\/attachments$/, invoice.invoice_attachments],
      ['GET', /\/api\/invoice\/\w{13}$/, invoice.invoice_draft],
      ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
    ];
  }
  const panel = (host: Element) => host.querySelector<HTMLElement>('.ev-panel');

  it('acts on the panel record while the focus is inside the panel, otherwise on the list', async () => {
    const { host } = start(routes(), '/app/invoice');
    await until(() => expect(host.querySelector(`.ev-list tbody tr[data-key="${three}"]`)).not.toBeNull());
    click(host.querySelector(`.ev-list tbody tr[data-key="${three}"]`));
    await until(() => expect(text(panel(host)?.querySelector('.doc-head h1'))).toBe('Draft invoice'));
    // Focus in the list: the list has no delete, so nothing opens.
    host.querySelector<HTMLElement>(`.ev-list tbody tr[data-key="${three}"]`)!.focus();
    expect(key(document.activeElement!, 'D', { ctrlKey: true, shiftKey: true }).defaultPrevented).toBe(false);
    expect(dialog()).toBeNull();
    // Focus in the panel: Mod+Shift+D asks to delete the panel's record.
    panel(host)!.focus();
    key(document.activeElement!, 'D', { ctrlKey: true, shiftKey: true });
    expect(dialogTitle()).toBe('Delete Draft invoice?');
    expect(text(document.activeElement)).toBe('Delete');
    key(document.activeElement!, 'Escape');
    expect(dialog()).toBeNull();
    // Alt+N from the panel opens a new record in the panel, as the list's New does.
    panel(host)!.focus();
    key(document.activeElement!, 'n', { code: 'KeyN', altKey: true });
    await until(() => expect(location.search).toBe('?open=new'));
  });
});

describe('dialog keys', () => {
  it('focuses the main action of a confirm, moves between actions with the arrows (wrapping) and answers Mod+S and Mod+Shift+D', () => {
    platform('MacIntel');
    const answers: string[] = [];
    const open = () => openModal({ title: 'Archive it?', narrow: true, body: () => html`<p>Sure?</p>`,
      footer: close => [html`<button type="button" class="btn">Later</button>`, DialogFooter({ cancel: () => { answers.push('cancel'); close(); }, submit: { label: 'Archive', onClick: () => { answers.push('archive'); close(); } } })] });
    open();
    expect(text(document.activeElement)).toBe('Archive');
    press(document.activeElement!, 'ArrowRight');
    expect(text(document.activeElement)).toBe('Later');
    press(document.activeElement!, 'ArrowUp');
    expect(text(document.activeElement)).toBe('Archive');
    press(document.activeElement!, 'ArrowLeft');
    expect(text(document.activeElement)).toBe('Cancel');
    // Mod+S is the main action wherever the focus is.
    expect(key(document.activeElement!, 's', { metaKey: true }).defaultPrevented).toBe(true);
    expect(answers).toEqual(['archive']);
    // Mod+Shift+D closes it as Esc does: nothing is archived.
    open();
    key(document.activeElement!, 'D', { metaKey: true, shiftKey: true });
    expect(answers).toEqual(['archive']);
    expect(dialog()).toBeNull();
  });

  it('focuses the first field of a dialog that asks for input, and Mod+S submits it from the field', () => {
    platform('Win32');
    let submitted = 0;
    openModal({ title: 'Rename', narrow: true, onSubmit: () => { submitted += 1; }, body: () => html`<input class="input" aria-label="Name">`,
      footer: close => DialogFooter({ cancel: close, submit: { label: 'Rename', onClick: () => {} } }) });
    expect(document.activeElement?.tagName).toBe('INPUT');
    // The arrows move the caret in a field, not between the actions.
    expect(press(document.activeElement!, 'ArrowRight').defaultPrevented).toBe(false);
    key(document.activeElement!, 's', { ctrlKey: true });
    // A held key submits once.
    key(document.activeElement!, 's', { ctrlKey: true, repeat: true });
    expect(submitted).toBe(1);
  });

  it('keeps the screen\'s shortcuts quiet while a dialog is open', async () => {
    const { host, sent } = await editedOrder();
    openModal({ title: 'Something else', narrow: true, body: () => html`<p>Over the record</p>` });
    // Focus left the dialog somehow: Mod+S still saves nothing under it, and the browser does not save the page.
    (host.querySelector('.doc') as HTMLElement).focus();
    expect(key(document.body, 's', { ctrlKey: true }).defaultPrevented).toBe(true);
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(requests(sent, 'PATCH', /order/)).toHaveLength(0);
  });
});
