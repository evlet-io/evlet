import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { Avatar, Button, Checkbox, Chip, EmptyState, Icon, IconButton, ProgressBar, SegmentedControl, Sparkline, StatCard, StateBanner, StatusPill, SummaryStrip, Switch, spokenText, trendSummary } from '../ui/base';
import { Menu, MenuButton, openDrawer, openModal, openPopover, toast } from '../ui/layers';
import { tipText, tooltip } from '../ui/tooltip';
import { cleanup, key, render, watch } from './ui-helpers';

afterEach(cleanup);
const XSS = '<img src=x onerror="window.pwned=1">';

describe('Icon', () => {
  it('builds SVG elements without parsing markup, decorative unless labelled', () => {
    const icon = Icon('lock', { size: 'xs', tint: 'rose' });
    expect(icon.namespaceURI).toBe('http://www.w3.org/2000/svg');
    expect(icon.getAttribute('class')).toBe('i xs c-rose');
    expect(icon.getAttribute('aria-hidden')).toBe('true');
    expect(icon.querySelector('rect,path')).not.toBeNull();
    const labelled = Icon('lock', { label: 'Locked' });
    expect(labelled.getAttribute('role')).toBe('img');
    expect(labelled.getAttribute('aria-label')).toBe('Locked');
    expect(Icon('no-such-icon').childNodes.length).toBeGreaterThan(0);
    expect(Icon('x', { tint: 'red<script>' as never }).getAttribute('class')).toBe('i c-slate');
  });
});

describe('Button and friends', () => {
  it('maps variants and sizes to the design classes', () => {
    expect(Button({ label: 'Issue', variant: 'primary' }).className).toBe('btn primary');
    expect(Button({ label: 'Delete', variant: 'soft-danger', size: 'small' }).className).toBe('btn danger-soft sm');
    const onClick = vi.fn();
    const button = Button({ label: 'Save', onClick });
    button.click();
    expect(onClick).toHaveBeenCalledOnce();
    expect(button.type).toBe('button');
  });

  it('gives icon-only buttons an accessible label and a tooltip', () => {
    const host = render(() => Button({ icon: 'ellipsis', tip: 'More actions' }));
    const button = host.querySelector('button')!;
    expect(button.className).toBe('btn icon');
    expect(button.getAttribute('aria-label')).toBe('More actions');
    button.dispatchEvent(new MouseEvent('mouseenter'));
    expect(tipText()).toBe('More actions');
    expect(button.getAttribute('aria-describedby')).toBe('evlet-tip');
    button.dispatchEvent(new MouseEvent('mouseleave'));
    expect(tipText()).toBeNull();
    expect(button.hasAttribute('aria-describedby')).toBe(false);
    const iconButton = render(() => IconButton({ icon: 'bell', tip: 'Notifications', active: cell(true) })).querySelector('button')!;
    expect(iconButton.getAttribute('aria-label')).toBe('Notifications');
    expect(iconButton.getAttribute('aria-pressed')).toBe('true');
  });

  it('updates only the bound nodes when a property cell changes', () => {
    const label = cell('Draft'), disabled = cell(false);
    const host = render(() => Button({ label, disabled }));
    const button = host.querySelector('button')!, text = [...button.querySelector('span')!.childNodes].find(node => node.nodeType === Node.TEXT_NODE)!;
    const done = watch(host);
    label.set('Issued');
    const records = done();
    expect(records).toHaveLength(1);
    expect(records[0].target).toBe(text);
    disabled.set(true);
    expect(button.disabled).toBe(true);
  });

  it('renders data as text, never markup', () => {
    const host = render(() => [Button({ label: XSS }), Chip({ label: XSS }), StatusPill({ label: XSS, tint: 'blue' }), Avatar({ name: XSS }),
      EmptyState({ icon: 'search', title: XSS, text: XSS }), StateBanner({ tone: 'info', title: XSS, text: XSS })]);
    expect(host.querySelector('img')).toBeNull();
    expect(host.textContent).toContain(XSS);
    expect((window as any).pwned).toBeUndefined();
  });
});

describe('toggles', () => {
  it('Checkbox has role, state and toggles its cell', () => {
    const checked = cell(false);
    const box = render(() => Checkbox({ checked, label: 'Done' })).querySelector('[role="checkbox"]') as HTMLButtonElement;
    expect(box.getAttribute('aria-checked')).toBe('false');
    box.click();
    expect(checked.peek()).toBe(true);
    expect(box.getAttribute('aria-checked')).toBe('true');
    expect(box.querySelector('.check')!.classList.contains('on')).toBe(true);
    const mixed = render(() => Checkbox({ checked: false, mixed: true, ariaLabel: 'Select all' })).querySelector('[role="checkbox"]')!;
    expect(mixed.getAttribute('aria-checked')).toBe('mixed');
  });

  it('Switch uses role="switch" with aria-checked', () => {
    const on = cell(true);
    const toggle = render(() => Switch({ on, label: 'Active' })).querySelector('[role="switch"]') as HTMLButtonElement;
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    toggle.click();
    expect(on.peek()).toBe(false);
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('SegmentedControl is a radio group that arrow keys move through', () => {
    const value = cell<'a' | 'b' | 'c'>('a');
    const host = render(() => SegmentedControl({ options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }, { value: 'c', label: 'C' }], value, label: 'View' }));
    const group = host.querySelector('[role="radiogroup"]')!;
    const radios = [...group.querySelectorAll('[role="radio"]')];
    expect(radios.map(radio => radio.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
    key(radios[0], 'ArrowLeft');
    expect(value.peek()).toBe('c');
    expect(document.activeElement).toBe(radios[2]);
    key(radios[2], 'ArrowRight');
    expect(value.peek()).toBe('a');
  });

  it('ProgressBar clamps and reports its value', () => {
    const value = cell(140);
    const bar = render(() => ProgressBar({ value, label: 'Share' })).querySelector('[role="progressbar"]')!;
    expect(bar.getAttribute('aria-valuenow')).toBe('100');
    value.set(35);
    expect(bar.getAttribute('aria-valuenow')).toBe('35');
  });
});

describe('Tooltip', () => {
  it('shows on keyboard focus, follows its text and hides on scroll', () => {
    const text = cell('Locked when Stage is won');
    const host = render(() => { const span = document.createElement('span'); span.tabIndex = 0; tooltip(span, text); return span; });
    const target = host.querySelector('span')!;
    target.dispatchEvent(new FocusEvent('focus'));
    expect(tipText()).toBe('Locked when Stage is won');
    text.set('Calculated');
    expect(tipText()).toBe('Calculated');
    document.dispatchEvent(new Event('scroll'));
    expect(tipText()).toBeNull();
  });

  it('is never shown on touch-only devices', () => {
    const media = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query === '(hover: none)' })) as never;
    try {
      const host = render(() => Button({ icon: 'bell', tip: 'Notifications' }));
      host.querySelector('button')!.dispatchEvent(new MouseEvent('mouseenter'));
      expect(tipText()).toBeNull();
    } finally { window.matchMedia = media; }
  });
});

describe('Popover and Menu', () => {
  it('opens one at a time, moves with arrow keys, and closes on Esc back to its button', async () => {
    const chosen = vi.fn();
    const host = render(() => MenuButton({ label: 'New', items: [{ label: 'Deal', onSelect: chosen }, 'separator', { label: 'Lead', onSelect: () => {} }] }));
    const button = host.querySelector('button')!;
    expect(button.getAttribute('aria-haspopup')).toBe('menu');
    button.click();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    await Promise.resolve();
    const items = [...document.querySelectorAll<HTMLElement>('.pop [role="menuitem"]')];
    expect(document.activeElement).toBe(items[0]);
    key(items[0], 'ArrowDown');
    expect(document.activeElement).toBe(items[1]);
    key(items[1], 'ArrowDown');
    expect(document.activeElement).toBe(items[0]);
    key(document.activeElement!, 'Escape');
    expect(document.querySelector('.pop')).toBeNull();
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);
    button.click();
    await Promise.resolve();
    document.querySelector<HTMLElement>('.pop [role="menuitem"]')!.click();
    expect(chosen).toHaveBeenCalledOnce();
    expect(document.querySelector('.pop')).toBeNull();
  });

  it('closes on an outside click and when another opens', () => {
    const host = render(() => [Button({ label: 'One' }), Button({ label: 'Two' })]);
    const [one, two] = [...host.querySelectorAll('button')];
    openPopover(one, close => Menu([{ label: 'A', onSelect: () => {} }], close));
    openPopover(two, close => Menu([{ label: 'B', onSelect: () => {} }], close));
    expect(document.querySelectorAll('.pop')).toHaveLength(1);
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(document.querySelectorAll('.pop')).toHaveLength(0);
  });
});

describe('Modal, Drawer and Toast', () => {
  it('focuses the first field, keeps Tab inside, saves on Cmd Enter, closes on Esc and returns focus', () => {
    const opener = render(() => Button({ label: 'New' })).querySelector('button')!;
    opener.focus();
    const submit = vi.fn(), closed = vi.fn();
    openModal({ title: 'New deal', onSubmit: submit, onClose: closed,
      body: () => { const input = document.createElement('input'); input.className = 'input'; return input; },
      footer: () => Button({ label: 'Create deal', variant: 'primary' }) });
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(document.getElementById(dialog.getAttribute('aria-labelledby')!)!.textContent).toBe('New deal');
    expect(document.activeElement).toBe(dialog.querySelector('input'));
    const buttons = [...dialog.querySelectorAll<HTMLElement>('button')];
    buttons[buttons.length - 1].focus();
    key(buttons[buttons.length - 1], 'Tab');
    expect(document.activeElement).toBe(dialog.querySelector('button'));
    key(dialog, 'Enter', { metaKey: true });
    expect(submit).toHaveBeenCalledOnce();
    key(dialog, 'Escape');
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(closed).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(opener);
  });

  it('closes a drawer from the scrim', () => {
    openDrawer({ title: 'Contact', body: () => 'Details' });
    expect(document.querySelector('.drawer.show')).not.toBeNull();
    document.querySelector<HTMLElement>('.scrim')!.click();
    expect(document.querySelector('.drawer')).toBeNull();
  });

  it('shows a toast for 2.8 s, 5 s with Undo, in a polite live region', () => {
    vi.useFakeTimers();
    try {
      const undo = vi.fn();
      toast('Moved to Discovery');
      toast(XSS, { undo });
      expect(document.querySelector('.toasts')!.getAttribute('aria-live')).toBe('polite');
      expect(document.querySelector('.toasts img')).toBeNull();
      vi.advanceTimersByTime(3000);
      expect(document.querySelectorAll('.toast')).toHaveLength(1);
      document.querySelector<HTMLButtonElement>('.toast button')!.click();
      expect(undo).toHaveBeenCalledOnce();
      vi.advanceTimersByTime(200);
      expect(document.querySelectorAll('.toast')).toHaveLength(0);
    } finally { vi.useRealTimers(); }
  });
});

describe('StatCard, Sparkline and SummaryStrip', () => {
  it('shows a muted label, the value, a caption beside the label, and an icon chip in its tone', () => {
    const value = cell('AED 508,000.00');
    const card = render(() => StatCard({ label: 'Budget', value, note: 'Filtered by a rule', icon: 'wallet', tone: 'teal' })).firstElementChild!;
    expect(card.className).toBe('sum-t stat-card tone-teal has-ic');
    expect(card.querySelector(':scope > span')!.textContent).toBe('Budget');
    expect(card.querySelector('b')!.textContent).toBe('AED 508,000.00');
    expect(card.querySelector('em')!.textContent).toBe('Filtered by a rule');
    expect(card.querySelector('.stat-ic svg')!.getAttribute('aria-hidden')).toBe('true');
    // The dot stands in for the chip on a phone, where the chip hides; the label text stays the label.
    expect(card.querySelector('span > .stat-dot')).not.toBeNull();
    value.set('AED 1.00');
    expect(card.querySelector('b')!.textContent).toBe('AED 1.00');
    expect(card.querySelector('b')!.getAttribute('title')).toBe('AED 1.00');
  });

  it('is neutral by default, keeps class names safe, and marks a tone with a dot', () => {
    const plain = StatCard({ label: 'Projects', value: '7' });
    expect(plain.className).toBe('sum-t stat-card');
    expect(plain.querySelector('.stat-ic, .stat-dot, em, .stat-tr')).toBeNull();
    expect(StatCard({ label: 'X', value: '1', tone: 'red<b>' as never }).className).toBe('sum-t stat-card tone-slate');
    const dotted = StatCard({ label: 'Won', value: '3', tone: 'green' });
    expect(dotted.querySelector('span > .stat-dot')).not.toBeNull();
    expect(dotted.querySelector('span')!.textContent).toBe('Won');
  });

  it('draws a trend line from numbers, labelled with a summary, and nothing without data', () => {
    const trend = cell<number[] | null>([3, 5, 4, 9]);
    const card = render(() => StatCard({ label: 'Budget', value: '9', tone: 'blue', trend, trendLabel: 'last 4 months' })).firstElementChild!;
    const svg = card.querySelector('.stat-tr svg.ev-spark')!;
    // Small and inline at the right of the thin card.
    expect([svg.getAttribute('width'), svg.getAttribute('height')]).toEqual(['64', '24']);
    expect(svg.namespaceURI).toBe('http://www.w3.org/2000/svg');
    expect(svg.getAttribute('role')).toBe('img');
    expect(svg.getAttribute('aria-label')).toBe('Budget trend, last 4 months: 4 points, from 3 to 9, low 3, high 9');
    expect(svg.querySelectorAll('path')).toHaveLength(2);
    expect(svg.querySelector('path.l')!.getAttribute('d')).toBe('M2 22 L22 15.33 L42 18.67 L62 2');
    expect(svg.querySelector('path.a')!.getAttribute('d')).toMatch(/ L62 24 L2 24 Z$/);
    expect(svg.querySelector('circle.p')!.getAttribute('cx')).toBe('62');
    trend.set([7]);
    expect(card.querySelector('svg.ev-spark')).toBeNull();
    trend.set(null);
    expect(card.querySelector('svg.ev-spark')).toBeNull();
    trend.set([1, 2]);
    expect(card.querySelector('svg.ev-spark')).not.toBeNull();
  });

  it('skips values that are not numbers, centres a flat line, and tints a standalone line', () => {
    expect(Sparkline({ values: [] })).toBeNull();
    expect(Sparkline({ values: [Number.NaN, 4] })).toBeNull();
    const flat = Sparkline({ values: [4, Number.POSITIVE_INFINITY, 4, 4], tone: 'rose', width: 50, height: 20 })!;
    expect(flat.getAttribute('class')).toBe('ev-spark c-rose');
    expect(flat.getAttribute('viewBox')).toBe('0 0 50 20');
    expect(flat.querySelector('path.l')!.getAttribute('d')).toBe('M2 10 L25 10 L48 10');
    expect(flat.getAttribute('aria-label')).toBe('Trend: 3 points, from 4 to 4, low 4, high 4');
    expect(trendSummary([1250.5, -3, 10])).toBe('3 points, from 1,250.5 to 10, low -3, high 1,250.5');
  });

  it('takes a string or a node as the value, and names the card from its text or image alt', () => {
    const text = StatCard({ label: 'Budget', value: 'AED 508,000.00', note: 'Filtered by a rule' });
    expect(text.querySelector('b')!.textContent).toBe('AED 508,000.00');
    expect(text.getAttribute('role')).toBe('group');
    expect(text.getAttribute('aria-label')).toBe('Budget, AED 508,000.00, Filtered by a rule');

    const amount = document.createElement('span');
    const symbol = document.createElement('img');
    symbol.src = 'data:image/png;base64,'; symbol.alt = 'AED';
    amount.append(symbol, '508,000.00');
    const card = StatCard({ label: 'Budget', value: () => amount });
    const value = card.querySelector('b')!;
    expect(value.firstElementChild).toBe(amount);
    expect(value.querySelector('img[alt="AED"]')).not.toBeNull();
    expect(card.getAttribute('aria-label')).toContain('AED');
    expect(card.getAttribute('aria-label')).toBe('Budget, AED 508,000.00');
    expect(value.getAttribute('title')).toBe('AED 508,000.00');
    expect(spokenText(amount)).toBe('AED 508,000.00');
  });

  it('puts up to four cards in the summary strip', () => {
    const strip = SummaryStrip([1, 2, 3, 4, 5].map(n => ({ label: `L${n}`, value: String(n) })));
    expect(strip.className).toBe('sum');
    expect(strip.querySelectorAll(':scope > .stat-card')).toHaveLength(4);
  });
});
