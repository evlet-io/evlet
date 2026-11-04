import { afterEach, describe, expect, it } from 'vitest';
import type { FieldSchema } from '../model/schema';
import { formula, phrase } from '../ui/explain';
import { apiLinks, BaseField, ComputedField, CurrencyField, fieldFor, localField, setLinkSource, TextField } from '../ui/fields';
import { durationText, InputError, KINDS, parseDateTime, parseDuration, parseTime } from '../ui/kinds';
import { SchemaIndex } from '../model/schema';
import { tipText } from '../ui/tooltip';
import { backend, id, bigOrder, change, cleanup, key, pick, render, schema, watch } from './ui-helpers';

afterEach(() => { setLinkSource(null); cleanup(); });
const meta = (type: string, extra: Partial<FieldSchema> = {}): FieldSchema => ({ name: type, type, label: type, required: false, read_only: false, ...extra });
const parse = (type: string, text: string, extra: Partial<FieldSchema> = {}) => KINDS[type].parse!(text, meta(type, extra));
const fails = (type: string, text: string, extra: Partial<FieldSchema> = {}) => { try { parse(type, text, extra); return null; } catch (error) { expect(error).toBeInstanceOf(InputError); return (error as Error).message; } };

describe('field types: parsing and display', () => {
  it('keeps exact decimals as strings at the field scale', () => {
    expect(parse('currency', '42,000.5', { scale: 2 })).toBe('42000.50');
    expect(parse('decimal', '.5', { scale: 3 })).toBe('0.500');
    expect(fails('currency', '1.005', { scale: 2 })).toBe('Use at most 2 decimal places');
    expect(fails('decimal', 'ten', { scale: 2 })).toBe('Enter a number, for example 1250.50');
    expect(fails('percent', '120', { scale: 2, minimum: '0', maximum: '100' })).toBe('Use a number from 0 to 100');
    expect(KINDS.currency.format('25200', meta('currency', { scale: 2 }))).toBe('AED 25,200.00');
    expect(KINDS.percent.format('12.5', meta('percent', { scale: 2 }))).toBe('12.50%');
    expect(parse('integer', '1,200')).toBe(1200);
    expect(fails('integer', '2.5')).toBe('Enter a whole number');
    expect(fails('integer', '0', { minimum: 1 })).toBe('Use 1 or more');
  });

  it('reads dates as DD-MM-YYYY, date-times in GST and times without a zone', () => {
    expect(parse('date', '1-11-2026')).toBe('2026-11-01');
    expect(fails('date', '31-02-2026')).toBe('Enter a date as DD-MM-YYYY');
    expect(KINDS.date.format('2026-11-01', meta('date'))).toBe('01-11-2026');
    expect(parseDateTime('01-11-2026 13:20')).toBe('2026-11-01T09:20:00Z');
    expect(KINDS.datetime.format('2026-11-01T09:20:00Z', meta('datetime'))).toBe('01-11-2026 13:20');
    expect(parseTime('2:30 pm')).toBe('14:30:00');
    expect(KINDS.time.format('14:00:00', meta('time'))).toBe('14:00');
  });

  it('types durations as 1h 30m and stores ISO 8601', () => {
    expect(parseDuration('1h 30m')).toBe(5400);
    expect(parseDuration('1:30')).toBe(5400);
    expect(parseDuration('45')).toBe(2700);
    expect(parse('duration', '2d 4h')).toBe('P2DT4H');
    expect(KINDS.duration.format('PT1H30M', meta('duration'))).toBe('1h 30m');
    expect(durationText(0)).toBe('0m');
    expect(fails('duration', 'soon')).toBe('Type a length of time such as 1h 30m, 1:30, 1.30 or 90m');
  });

  it('checks email, URL and phone like the server', () => {
    expect(fails('email', 'khalid@')).toBe('Enter an email address like name@example.com');
    expect(parse('url', 'alsahra-trading.ae')).toBe('https://alsahra-trading.ae');
    expect(fails('url', 'javascript:alert(1)')).toBe('Enter a full web address, for example https://example.com');
    expect(parse('phone', '+971 50 418 2290')).toBe('+971504182290');
    expect(fails('phone', '12')).toBe('A phone number has 3 to 15 digits');
    expect(fails('text', 'abcdef', { max_length: 3 })).toBe('Use 3 characters or fewer');
  });

  it('explains rules in words and formulas', () => {
    const order = schema.models.find(model => model.name === 'order')!;
    const field = (name: string) => order.fields.find(item => item.name === name)!;
    expect(formula(schema.models.find(model => model.name === 'order_lines')!.fields.find(item => item.name === 'amount')!.compute)).toBe('round(qty * rate, 2)');
    expect(formula(field('due').compute)).toBe('issued + days(customer.credit_days)');
    expect(formula(field('total').compute)).toBe('lines.sum("amount")');
    expect(phrase(field('remark').locked_when, name => order.fields.find(item => item.name === name)!.label)).toBe('Stage is won');
  });
});

describe('BaseField', () => {
  it('labels its input, marks required, shows errors and binds aria', () => {
    const field = localField({ name: 'deal', type: 'text', label: 'Deal name', required: true });
    const host = render(() => TextField({ field }));
    const input = host.querySelector('input')!, label = host.querySelector('label')!;
    expect(label.getAttribute('for')).toBe(input.id);
    expect(label.querySelector('.req')).not.toBeNull();
    expect(input.getAttribute('aria-required')).toBe('true');
    change(input, '   ');
    expect(field.error.peek()).toBe('Deal name is required');
    expect(host.querySelector('.fld')!.classList.contains('invalid')).toBe(true);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    const error = host.querySelector('.err')!;
    expect(input.getAttribute('aria-describedby')).toBe(error.id);
    expect(error.textContent).toBe('Deal name is required');
    change(input, 'ERP rollout');
    expect(field.value.peek()).toBe('ERP rollout');
    expect(field.error.peek()).toBeNull();
  });

  it('commits on Enter, formats the value, and keeps bad text with a message', () => {
    const field = localField({ name: 'rate', type: 'currency', label: 'Rate', scale: 2 }, '10.00');
    const host = render(() => CurrencyField({ field }));
    const input = host.querySelector('input')!;
    expect(input.value).toBe('10.00');
    expect(host.querySelector('.in-affix span')!.textContent).toBe('AED');
    input.value = '1250.5';
    key(input, 'Enter');
    expect(field.value.peek()).toBe('1250.50');
    expect(input.value).toBe('1,250.50');
    input.value = 'abc';
    key(input, 'Enter');
    expect(field.value.peek()).toBe('1250.50');
    expect(input.value).toBe('abc');
    expect(host.querySelector('.err')!.textContent).toBe('Enter a number, for example 1250.50');
  });

  it('hides while applies_when is false, locks with a reason, and refuses the wrong type', () => {
    const field = localField({ name: 'reason', type: 'text', label: 'Reason' }, null);
    const host = render(() => BaseField({ field }));
    const root = host.querySelector<HTMLElement>('.fld')!;
    field.rules.set({ applies: false, required: false, locked: false });
    expect(root.hidden).toBe(true);
    field.rules.set({ applies: true, required: false, locked: true });
    expect(root.hidden).toBe(false);
    expect(host.querySelector('input')!.disabled).toBe(true);
    expect(host.querySelector('.ff-hint')!.textContent).toBe('locked');
    expect(() => CurrencyField({ field })).toThrow('Reason is a text field, not currency');
  });

  it('renders every field type with a control, and data as text', () => {
    const XSS = '<img src=x onerror="window.pwned=1">';
    const types = ['text', 'longtext', 'integer', 'decimal', 'currency', 'percent', 'date', 'datetime', 'time', 'duration', 'boolean', 'select', 'link', 'email', 'url', 'phone', 'tags', 'state', 'sequence', 'reference'];
    const host = render(() => types.map(type => fieldFor({ field: localField({ name: type, type, label: XSS, choices: [XSS] }, type === 'tags' ? [XSS] : type === 'state' || type === 'sequence' || type === 'text' ? XSS : null) })));
    expect(host.querySelectorAll('.fld')).toHaveLength(types.length);
    expect(host.querySelector('img')).toBeNull();
    expect(host.textContent).toContain(XSS);
    expect(host.querySelector('[data-field="boolean"] [role="switch"]')).not.toBeNull();
    expect(host.querySelector('[data-field="select"] button.in-select[aria-haspopup="listbox"]')).not.toBeNull();
    expect(host.querySelector('select')).toBeNull();
    expect(host.querySelector('[data-field="state"]')!.textContent).toContain('changes through moves');
  });

  it('adds and removes tags with the keyboard', () => {
    const field = localField({ name: 'tags', type: 'tags', label: 'Tags' }, ['Hot']);
    const host = render(() => BaseField({ field }));
    const input = host.querySelector<HTMLInputElement>('.in-tags input')!;
    input.value = 'Renewal';
    key(input, 'Enter');
    expect(field.value.peek()).toEqual(['Hot', 'Renewal']);
    input.value = 'hot';
    key(input, 'Enter');
    expect(field.error.peek()).toBe('hot is already there');
    input.value = '';
    key(input, 'Backspace');
    expect(field.value.peek()).toEqual(['Hot']);
    host.querySelector<HTMLButtonElement>('[aria-label="Remove Hot"]')!.click();
    expect(field.value.peek()).toBeNull();
  });
});

describe('fields bound to the record store', () => {
  it('follows applies_when, required_when and locked_when, and says why', async () => {
    const { store } = backend();
    await store.open(id(1));
    const host = render(() => ['stage', 'reason', 'remark'].map(name => BaseField({ field: store.field(name) })));
    const reason = host.querySelector<HTMLElement>('[data-field="reason"]')!, remark = host.querySelector<HTMLElement>('[data-field="remark"]')!;
    expect(reason.hidden).toBe(true);
    pick(host.querySelector<HTMLElement>('[data-field="stage"] .in-select')!, 'Lost');
    expect(store.field('stage').value.peek()).toBe('lost');
    expect(reason.hidden).toBe(false);
    expect(reason.querySelector('.req')).not.toBeNull();
    reason.querySelector<HTMLElement>('.req')!.dispatchEvent(new MouseEvent('mouseenter'));
    expect(tipText()).toBe('Required when Stage is lost');
    // Remark locks when the saved stage is won; this order was saved as open.
    expect(remark.querySelector('input')!.disabled).toBe(false);
  });

  it('marks calculated values with their rule and shows the result read only', async () => {
    const { store } = backend();
    await store.open(id(1));
    await store.idle();
    const host = render(() => [ComputedField({ field: store.field('total') }), BaseField({ field: store.field('due') })]);
    const hint = host.querySelector<HTMLElement>('[data-field="total"] .ff-hint.calc')!;
    expect(hint.textContent).toBe('ƒ calculated');
    hint.dispatchEvent(new FocusEvent('focus'));
    expect(tipText()).toBe('= lines.sum("amount")');
    expect(host.querySelector('[data-field="total"] .fval.ro')!.textContent).toBe('AED 300.00');
    expect(host.querySelector('[data-field="due"] .fval.ro')!.textContent).toBe('16-12-2026');
    expect(host.querySelector('[data-field="total"] input')).toBeNull();
  });

  it('shows where an initial value comes from until the user edits it', async () => {
    const { store } = await bigOrder(1);
    const line = store.table('lines').add({ product: 1 });
    await store.idle();
    const host = render(() => BaseField({ field: line.field('rate') }));
    expect(line.field('rate').value.peek()).toBe('250.00');
    expect(host.querySelector('.ff-hint.init')!.textContent).toBe('from product');
    change(host.querySelector('input')!, '200');
    expect(line.field('rate').value.peek()).toBe('200.00');
    expect(host.querySelector('.ff-hint.init')).toBeNull();
  });

  it('updates only the nodes bound to the changed cells', async () => {
    const { store } = await bigOrder(2);
    const lines = store.table('lines').rows.peek();
    const host = render(() => [BaseField({ field: store.field('title') }), ComputedField({ field: store.field('total') }), BaseField({ field: lines[0].field('qty') })]);
    const done = watch(host);
    lines[1].field('qty').set('5');
    const records = done();
    // Only the total's text changed (qty of the second line is not shown).
    expect(records.length).toBe(1);
    expect(records[0].target.parentElement!.closest('[data-field]')!.getAttribute('data-field')).toBe('total');
  });

  it('chooses a Link with keyboard search and shows the record title', async () => {
    const { store, api } = backend();
    await store.open(id(1));
    setLinkSource(apiLinks(api, new SchemaIndex(schema)));
    const line = store.table('lines').rows.peek()[0];
    const host = render(() => BaseField({ field: line.field('product') }));
    await new Promise(resolve => setTimeout(resolve, 0));
    const button = host.querySelector<HTMLButtonElement>('.in-link')!;
    expect(button.textContent).toContain('Support hours');
    button.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    const input = document.querySelector<HTMLInputElement>('.link-search input')!;
    expect(input.getAttribute('role')).toBe('combobox');
    expect(document.activeElement).toBe(input);
    const options = [...document.querySelectorAll('[role="option"]')];
    expect(options.map(option => option.textContent)).toEqual(['SSupport hours', 'LLicence <b>bold</b>']);
    expect(document.querySelector('.link-list b')).toBeNull();
    key(input, 'ArrowDown');
    expect(input.getAttribute('aria-activedescendant')).toBe(options[1].id);
    key(input, 'Enter');
    expect(line.field('product').value.peek()).toBe(id(2));
    expect(document.querySelector('.pop')).toBeNull();
    expect(button.textContent).toContain('Licence <b>bold</b>');
  });

  it('shows a local field value cell change in its input', () => {
    const value = localField({ name: 'n', type: 'integer', label: 'Count' }, 1);
    const host = render(() => BaseField({ field: value }));
    value.value.set(42);
    expect(host.querySelector('input')!.value).toBe('42');
  });
});

describe('hint and tooltip', () => {
  it('shows help text under the input, linked to it, and the error in its place', () => {
    const field = localField({ name: 'reference', type: 'text', label: 'Reference', help_text: 'The number on the customer’s order', required: true });
    const host = render(() => BaseField({ field }));
    const input = host.querySelector('input')!, help = host.querySelector<HTMLElement>('.fld-help')!;
    expect(help.textContent).toBe('The number on the customer’s order');
    expect(input.getAttribute('aria-describedby')!.split(' ')).toContain(help.id);
    expect(help.hidden).toBe(false);
    change(input, '');
    expect(field.error.peek()).toBe('Reference is required');
    expect(help.hidden).toBe(true);
    expect(host.querySelector('.err')!.textContent).toBe('Reference is required');
    change(input, 'PO-7');
    expect(help.hidden).toBe(false);
    expect(render(() => BaseField({ field: localField({ name: 'x', type: 'text', label: 'X' }) })).querySelector('.fld-help')).toBeNull();
  });

  it('puts a ? icon with the tooltip inside the input, and only when one is set', () => {
    const tip = 'Leave empty to use the customer’s rate';
    const host = render(() => [
      BaseField({ field: localField({ name: 'note', type: 'text', label: 'Note', tooltip: tip }) }),
      BaseField({ field: localField({ name: 'rate', type: 'percent', label: 'Rate', scale: 2, tooltip: tip }) }),
      BaseField({ field: localField({ name: 'kind', type: 'select', label: 'Kind', choices: ['a', 'b'], tooltip: tip }) }),
      BaseField({ field: localField({ name: 'plain', type: 'text', label: 'Plain' }) }),
    ]);
    const icon = (name: string) => host.querySelector<HTMLElement>(`[data-field="${name}"] .fld-tip`);
    expect(icon('note')!.closest('.fld-ctl')!.classList.contains('tip-in')).toBe(true);
    expect(icon('note')!.getAttribute('aria-label')).toBe('About note');
    // Inside a unit box it sits between the input and the % box.
    expect(icon('rate')!.previousElementSibling!.tagName).toBe('INPUT');
    expect(icon('rate')!.nextElementSibling!.textContent).toBe('%');
    expect(icon('kind')!.closest('.fld-ctl')!.classList.contains('tip-chev')).toBe(true);
    expect(icon('plain')).toBeNull();
    icon('note')!.dispatchEvent(new FocusEvent('focus'));
    expect(tipText()).toBe(tip);
  });
});

