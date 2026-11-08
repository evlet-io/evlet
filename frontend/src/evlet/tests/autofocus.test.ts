// Autofocus on forms that start empty (docs/ui.md, New records): a new record and a slip focus their first
// editable field; an existing record, a touch device and a focus moved elsewhere are left alone.
import { afterEach, describe, expect, it } from 'vitest';
import { autofocusForm, initialFocusField } from '../ui/autofocus';
import { localField } from '../ui/fields';
import { DEFAULT_LIMITS } from '../app/unsaved';
import { adminRoutes, byText, click, recorded, screen, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';
import { copy } from './server';

const realMatchMedia = window.matchMedia;
afterEach(() => { window.matchMedia = realMatchMedia; stopAll(); localStorage.clear(); });

const touch = () => {
  window.matchMedia = ((query: string) => ({ matches: query.includes('hover: none') || query.includes('pointer: coarse'), media: query,
    addEventListener() {}, removeEventListener() {} })) as any;
};
const text_ = (name: string, meta: Record<string, unknown> = {}, rules = {}) => localField({ name, type: 'text', label: name, ...meta }, null, rules);
const model = { name: 'thing' };
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const focusedField = () => document.activeElement?.closest('[data-field]')?.getAttribute('data-field') ?? null;

describe('initialFocusField', () => {
  it('picks the first field that shows and can be edited', () => {
    expect(initialFocusField(model, [{ fields: [text_('a'), text_('b')] }])).toBe('a');
  });

  it('skips read-only, calculated, hidden, locked and not applicable fields', () => {
    const fields = [text_('ro', { read_only: true }), text_('calc', { compute: { $expr: 'field', name: 'x' } }), text_('hid', { hidden: true }),
      text_('locked', {}, { locked: true }), text_('off', {}, { applies: false }), text_('ok')];
    expect(initialFocusField(model, [{ fields }])).toBe('ok');
  });

  it('skips the fields of a closed section', () => {
    expect(initialFocusField(model, [{ fields: [text_('a')], open: false }, { fields: [text_('b')] }])).toBe('b');
  });

  it('returns null when nothing qualifies', () => {
    expect(initialFocusField(model, [{ fields: [text_('a', { read_only: true })] }, { fields: [text_('b')], open: false }])).toBeNull();
  });
});

describe('autofocusForm', () => {
  const form = () => {
    const root = document.createElement('div');
    root.innerHTML = '<div data-field="a"><label>A <button class="fld-tip">?</button></label><input id="a"></div>';
    document.body.append(root);
    return root;
  };

  it('focuses the chosen field\'s control, not the tooltip button', async () => {
    form();
    autofocusForm(document.body.lastElementChild as HTMLElement, () => 'a');
    await tick();
    expect(document.activeElement?.id).toBe('a');
  });

  it('does not steal a focus moved elsewhere while the form loaded', async () => {
    const other = document.createElement('input');
    document.body.append(other);
    const root = form();
    autofocusForm(root, () => 'a');
    other.focus();
    await tick();
    expect(document.activeElement).toBe(other);
  });

  it('does nothing on a touch device', async () => {
    touch();
    const root = form();
    autofocusForm(root, () => 'a');
    await tick();
    expect(document.activeElement).toBe(document.body);
  });
});

describe('new record forms', () => {
  it('focuses the first field of a new record', async () => {
    const { host } = start(adminRoutes(), '/app/order/new');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New order'));
    await until(() => expect(focusedField()).toBe('title'));
    expect(document.activeElement).toBe(host.querySelector('.fld[data-field="title"] input'));
  });

  it('skips a read-only first field', async () => {
    const { host } = start(adminRoutes(), '/app/quote/new');
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('New quote'));
    await until(() => expect(focusedField()).toBe('customer'));
    expect(document.activeElement?.matches('button.in-link')).toBe(true);
  });

  it('skips the fields of a collapsed section', async () => {
    const schema = copy(screen.schema);
    const order = schema.body.models.find((item: { name: string }) => item.name === 'order');
    order.layout = [{ label: 'Basics', collapsible: true, collapsed: true, flow: 'rows', fields: ['title', 'customer'] },
      { label: 'More', collapsible: false, collapsed: false, flow: 'rows', fields: ['stage', 'issued', 'due', 'reason', 'remark', 'total', 'line_count'] }];
    const { host } = start([['GET', /\/api\/auth\/session$/, recorded.session], ['GET', /\/api\/schema$/, schema]], '/app/order/new');
    await until(() => expect(host.querySelector('.fs-fold.closed')).not.toBeNull());
    await until(() => expect(focusedField()).toBe('stage'));
  });

  it('focuses the first field again after a draft is restored, keeping its values', async () => {
    const me = copy(screen.settings_me);
    me.body.unsaved = { limits: { ...DEFAULT_LIMITS }, count: 0, enabled: true };
    const { host, router } = start([...adminRoutes(), ['GET', /\/api\/_settings\/me$/, me]], '/app/customer/new', { storage: localStorage });
    const title = () => host.querySelector<HTMLInputElement>('.fld[data-field="title"] input')!;
    await until(() => expect(focusedField()).toBe('title'));
    typeInto(title(), 'Acme Trading');
    await router.go('docs', {});
    await until(() => expect(location.pathname).toBe('/docs'));
    await router.go('create', { model: 'customer' });
    const bar = () => host.querySelector('.ev-unsaved .inv-banner');
    await until(() => expect(bar()).not.toBeNull());
    // The click focuses the Restore button, which goes with the bar.
    byText(bar()!, 'button', 'Restore')!.focus();
    expect(focusedField()).toBeNull();
    click(byText(bar()!, 'button', 'Restore'));
    await until(() => expect(title().value).toBe('Acme Trading'));
    await until(() => expect(focusedField()).toBe('title'));
    expect(title().value).toBe('Acme Trading');
  });

  it('does not autofocus on a touch device', async () => {
    touch();
    const { host } = start(adminRoutes(), '/app/order/new');
    await until(() => expect(host.querySelector('.fld[data-field="title"] input')).not.toBeNull());
    await tick();
    await new Promise(resolve => requestAnimationFrame(resolve));
    expect(focusedField()).toBeNull();
  });

  it('does not autofocus an existing record', async () => {
    const routes: Route[] = [...adminRoutes(),
      ['GET', /\/api\/order\/0000000000001$/, recorded.detail],
      ['GET', /\/api\/order\/0000000000001\/lines\/lines\?/, recorded.lines_page],
      ['GET', /\/api\/customer\/0000000000001$/, { ...recorded.customer, status: 200 }],
      ['GET', /\/api\/product\/0000000000001$/, { ...recorded.product, status: 200 }]];
    const { host } = start(routes, '/app/order/0000000000001');
    await until(() => expect(host.querySelector('[data-field="customer"]')).not.toBeNull());
    await tick();
    await new Promise(resolve => requestAnimationFrame(resolve));
    expect(focusedField()).toBeNull();
  });
});

describe('slips', () => {
  it('focus the first field that can be filled in, past the read-only source field', async () => {
    const routes: Route[] = [
      ['GET', /\/api\/auth\/session$/, screen.slip_session],
      ['GET', /\/api\/schema$/, screen.slip_schema],
      ['GET', /\/api\/customer\/0000000000001$/, screen.slip_customer],
      ['POST', /\/api\/_slips\/plan_visit\/present$/, screen.slip_present],
      ['POST', /\/api\/_slips\/plan_visit\/check$/, screen.slip_check],
    ];
    const { host } = start(routes, '/app/customer/0000000000001');
    await until(() => expect(byText(host, '.doc-head button', 'Plan a visit')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Plan a visit'));
    await until(() => expect(document.querySelector('.modal.slip')).not.toBeNull());
    await until(() => expect(focusedField()).not.toBeNull());
    expect(focusedField()).not.toBe('customer');
    const sheet = document.querySelector('.modal.slip')!;
    const first = [...sheet.querySelectorAll<HTMLElement>('[data-field]')].find(block => !block.hidden
      && block.querySelector('input:not(:disabled), button.in-select:not(:disabled), button.in-link:not(:disabled), textarea:not(:disabled)'));
    expect(focusedField()).toBe(first?.getAttribute('data-field'));
  });
});
