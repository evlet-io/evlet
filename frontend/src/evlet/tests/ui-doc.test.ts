import { afterEach, describe, expect, it, vi } from 'vitest';
import { html } from '../dom';
import { cell } from '../reactive';
import { Button, RecordMark } from '../ui/base';
import { SplitButton } from '../ui/layers';
import { DOC_NARROW, DocFrame, DocHeader, docWidthClasses, flowFromState, LockBanner, MoveButton, SideBlock, StatusBadge, StatusFlow, TabPanel, Tabs, type Lifecycle } from '../ui/doc';
import { ENTRIES, LibraryPage, builtIds } from '../ui/library';
import { Board, BoardCard, BulkBar, Filters, PageHeader, type ActiveFilter } from '../ui/list';
import { tipText } from '../ui/tooltip';
import { backend, id, cleanup, key, render } from './ui-helpers';

afterEach(() => { vi.useRealTimers(); cleanup(); });
const lastToast = () => [...document.querySelectorAll('.toast')].pop()?.textContent ?? null;

describe('doc view blocks', () => {
  it('Tabs are a tablist with arrow keys, counts and panels', () => {
    const active = cell('activity');
    const host = render(() => {
      const tabs = Tabs({ tabs: [{ id: 'activity', label: 'Activity' }, { id: 'details', label: 'Details' }, { id: 'lines', label: 'Lines', count: 120 }], active, label: 'Record', doc: true });
      return [tabs, TabPanel(tabs, 'activity', active, () => 'Journal'), TabPanel(tabs, 'lines', active, () => 'Grid')];
    });
    const tabs = [...host.querySelectorAll<HTMLElement>('[role="tab"]')];
    expect(host.querySelector('[role="tablist"]')!.classList.contains('doc-tabs')).toBe(true);
    expect(tabs[2].textContent).toBe('Lines120');
    expect(tabs.map(tab => tab.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    expect(host.querySelector('[role="tabpanel"]')!.textContent).toBe('Journal');
    key(tabs[0], 'ArrowLeft');
    expect(active.peek()).toBe('lines');
    expect(document.activeElement).toBe(tabs[2]);
    const panel = host.querySelector('[role="tabpanel"]')!;
    expect(panel.textContent).toBe('Grid');
    expect(panel.getAttribute('aria-labelledby')).toBe(tabs[2].id);
    expect(tabs[2].getAttribute('aria-controls')).toBe(panel.id);
  });

  it('StatusFlow marks done and current steps, runs moves this person may take and explains a passing block', () => {
    vi.useFakeTimers();
    const current = cell('draft');
    const moved = vi.fn((to: string) => current.set(to));
    const { steps, good, bad } = flowFromState({ field: 'status', states: ['draft', 'issued', 'paid', 'void'], moves: [], final: ['paid', 'void'] });
    expect(good).toEqual(['paid']);
    expect(bad).toEqual(['void']);
    const dirty = cell(true);
    const host = render(() => StatusFlow({ steps, good, bad, current, onMove: moved, canMove: to => to === 'issued',
      blocked: () => dirty.get() ? 'Save or discard your changes first' : null }));
    const items = [...host.querySelectorAll<HTMLElement>('.step')];
    expect(items.map(item => item.getAttribute('role'))).toEqual(['listitem', 'listitem', 'listitem', 'listitem']);
    expect(items[0].getAttribute('aria-current')).toBe('step');
    // Only Issued can be chosen: the current step and states this person cannot move to are plain.
    expect(items.map(item => item.querySelector('button') !== null)).toEqual([false, true, false, false]);
    expect(items.map(item => item.classList.contains('plain'))).toEqual([true, false, true, true]);
    expect(items[2].querySelector('.step-tx')!.getAttribute('aria-label')).toBe('Paid');
    expect(items[1].querySelector('button')!.getAttribute('aria-label')).toBe('Issued. Move to Issued');
    items[1].querySelector('button')!.click();
    expect(moved).not.toHaveBeenCalled();
    expect(lastToast()).toContain('Save or discard your changes first');
    dirty.set(false);
    items[1].querySelector('button')!.click();
    expect(moved).toHaveBeenCalledWith('issued');
    expect(items[0].classList.contains('done')).toBe(true);
    expect(items[0].querySelector('svg')).not.toBeNull();
    expect(items[0].querySelector('.step-tx')!.getAttribute('aria-label')).toBe('Draft, done');
    expect(items[1].classList.contains('cur')).toBe(true);
    expect(items[1].querySelector('button')).toBeNull();
    expect(items[3].classList.contains('lost')).toBe(true);
  });

  it('StatusBadge is plain text without moves and a menu of the moves this person may take with them', () => {
    vi.useFakeTimers();
    const value = cell<string | null>('active');
    const moves = cell([{ to: 'left', label: 'Mark as left' }]);
    const moved = vi.fn((to: string) => value.set(to));
    const host = render(() => html`<div>${StatusBadge({ value, tint: v => v === 'active' ? 'green' : 'slate', moves: () => moves.get(), onMove: moved })}</div>`);
    const badge = () => host.querySelector<HTMLElement>('.ev-status')!;
    expect(badge().tagName).toBe('BUTTON');
    expect(badge().className).toContain('b-green');
    expect(badge().getAttribute('aria-label')).toBe('Status: Active. Change status');
    expect(badge().getAttribute('aria-haspopup')).toBe('menu');
    badge().click();
    const item = document.querySelector<HTMLElement>('[role="menuitem"]')!;
    expect(item.textContent).toBe('Mark as left');
    item.click();
    expect(moved).toHaveBeenCalledWith('left');
    // No move into or out of this state for this person: plain text, named, with no button.
    moves.set([]);
    expect(badge().tagName).toBe('SPAN');
    expect(badge().getAttribute('role')).toBe('img');
    expect(badge().getAttribute('aria-label')).toBe('Status: Left');
    expect(badge().className).toContain('b-slate');
    expect(badge().querySelector('button')).toBeNull();
    value.set(null);
    expect(host.querySelector('.ev-status')).toBeNull();
  });

  it('StatusBadge names other statuses by their label and never offers moves without onMove', () => {
    const host = render(() => html`<div>${StatusBadge({ value: 'partly_paid', tint: () => 'amber', name: 'Payment status', moves: () => [{ to: 'paid', label: 'Paid' }] })}</div>`);
    const badge = host.querySelector<HTMLElement>('.ev-status')!;
    expect(badge.tagName).toBe('SPAN');
    expect(badge.getAttribute('aria-label')).toBe('Payment status: Partly paid');
    expect(badge.className).toContain('b-amber');
  });

  it('a blocked MoveButton stays visible, dims, and says why instead of running', () => {
    vi.useFakeTimers();
    const blocked = cell<string | null>('Choose a customer first');
    const move = vi.fn();
    const host = render(() => MoveButton({ label: 'Issue', variant: 'primary', blocked, onMove: move }));
    const button = host.querySelector('button')!;
    expect(button.hasAttribute('data-blocked')).toBe(true);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    button.dispatchEvent(new FocusEvent('focus'));
    expect(tipText()).toBe('Choose a customer first');
    button.click();
    expect(move).not.toHaveBeenCalled();
    expect(lastToast()).toContain('Choose a customer first');
    blocked.set(null);
    expect(button.hasAttribute('data-blocked')).toBe(false);
    button.click();
    expect(move).toHaveBeenCalledOnce();
  });

  it('LockBanner follows the lifecycle with Revise and Void only while locked', () => {
    const lifecycle = cell<Lifecycle>('draft');
    const host = render(() => LockBanner({ lifecycle, state: 'Issued', reason: 'Customer cancelled the order.', onRevise: () => {}, onVoid: () => {} }));
    const banner = host.querySelector('.inv-banner')!;
    expect(banner.className).toContain('b-slate');
    expect(banner.textContent).toContain('Draft.');
    expect(banner.querySelectorAll('button')).toHaveLength(0);
    lifecycle.set('locked');
    expect(banner.className).toContain('b-blue');
    expect(banner.textContent).toContain('Issued and locked.');
    expect([...banner.querySelectorAll('button')].map(button => button.textContent)).toEqual(['Revise', 'Void']);
    lifecycle.set('void');
    expect(banner.className).toContain('b-rose');
    expect(banner.textContent).toContain('Customer cancelled the order.');
    expect(banner.querySelectorAll('button')).toHaveLength(0);
  });

  it('DocHeader and DocFrame lay out a record with an editable title and the type accent', async () => {
    const { store } = backend();
    await store.open(id(1));
    const side = cell<'left' | 'right' | 'off'>('right');
    const host = render(() => DocFrame({ tint: 'teal', sidePosition: side, main: DocHeader({ type: 'Order', icon: 'receipt', mark: RecordMark({ name: 'Big order', size: 'lg' }), title: store.field('title'), meta: ['AED 300.00'] }),
      side: SideBlock({ title: 'Tags', body: 'None yet' }) }));
    const frame = host.querySelector<HTMLElement>('.doc')!;
    expect(frame.style.getPropertyValue('--acc')).toBe('var(--t-teal)');
    // No type eyebrow above the title (the breadcrumb names the type); the given mark starts the row.
    expect(host.querySelector('.doc-head .eyebrow')).toBeNull();
    expect(host.querySelector('.doc-row > .doc-mark .org-mark')).not.toBeNull();
    expect(host.querySelector('.doc-title-line h1 button.fval')!.textContent).toContain('Big order');
    expect(host.querySelector('.doc-row > .rec-meta')!.textContent).toBe('AED 300.00');
    expect(host.querySelector('.side-sec h4')!.textContent).toBe('Tags');
    side.set('left');
    expect(frame.classList.contains('side-left')).toBe(true);
    side.set('off');
    expect(host.querySelector('.doc-side')).toBeNull();
    expect(frame.classList.contains('no-side')).toBe(true);
  });

  it('DocFrame is responsive to its own width: the side column leaves a narrow frame, the main column steps down', () => {
    // Not the window's width: a record page and a list's side panel use the same frame and the same rules.
    expect(docWidthClasses(0, 0)).toEqual([]);
    expect(docWidthClasses(1200, 780)).toEqual([]);
    expect(docWidthClasses(DOC_NARROW - 1, DOC_NARROW - 37)).toEqual(['doc-narrow']);
    expect(docWidthClasses(1000, 590)).toEqual(['doc-m']);
    expect(docWidthClasses(480, 444)).toEqual(['doc-narrow', 'doc-m', 'doc-s']);
    expect(docWidthClasses(375, 343)).toEqual(['doc-narrow', 'doc-m', 'doc-s', 'doc-xs']);
  });

  it('DocHeader puts the status on the title line, the actions on its row and, without a mark, the type icon tile', () => {
    const host = render(() => DocHeader({ type: 'Project', icon: 'kanban', tint: 'blue', title: 'Website rebuild', status: [html`<span class="pill ev-status">On hold</span>`],
      meta: ['Acme'], actions: [Button({ label: 'Log hours', icon: 'clock' }),
        SplitButton({ main: Button({ label: 'Resume', variant: 'primary' }), items: [{ label: 'Complete', onSelect: () => {} }, { label: 'Cancel', onSelect: () => {} }], menuLabel: 'Other moves' })] }));
    const tile = host.querySelector<HTMLElement>('.doc-mark .ph-icon')!;
    expect(tile.classList.contains('b-blue')).toBe(true);
    expect(tile.getAttribute('aria-label')).toBe('Project');
    expect(tile.querySelector('svg')).not.toBeNull();
    expect(tile.style.borderRadius).toBe('');
    const line = host.querySelector('.doc-title-line')!;
    expect(line.querySelector('h1')!.textContent).toBe('Website rebuild');
    expect(line.querySelector('.ev-status')!.textContent).toBe('On hold');
    expect(host.querySelector('.rec-meta')!.textContent).toBe('Acme');
    const row = host.querySelector('.doc-row')!;
    expect([...row.children].map(child => child.className)).toEqual(['doc-mark', 'ttl', 'ph-actions', 'rec-meta']);
    const split = row.querySelector('.ph-actions .btn-split')!;
    expect(split.querySelector('.split-main')!.textContent).toBe('Resume');
    split.querySelector<HTMLButtonElement>('.split-more')!.click();
    expect([...document.querySelectorAll('[role="menuitem"]')].map(item => item.textContent)).toEqual(['Complete', 'Cancel']);
  });

  it('SplitButton has no chevron without alternatives', () => {
    const host = render(() => SplitButton({ main: Button({ label: 'Issue', variant: 'primary' }), items: [], menuLabel: 'Other moves' }));
    expect(host.querySelector('.btn-split.solo .split-main')!.textContent).toBe('Issue');
    expect(host.querySelector('.split-more')).toBeNull();
  });
});

describe('list blocks', () => {
  it('Board groups cards with counts and totals, moves by keyboard with Undo, and refuses with a reason', () => {
    vi.useFakeTimers();
    const cards = cell([{ id: 1, stage: 'new', value: 100 }, { id: 2, stage: 'new', value: 300 }, { id: 3, stage: 'won', value: 600 }]);
    const onMove = (card: { id: number }, to: string) => {
      if (to === 'lost') return 'Add a reason before marking it lost';
      cards.set(cards.peek().map(item => item.id === card.id ? { ...item, stage: to } : item));
      return null;
    };
    const host = render(() => Board({ label: 'Deals', columns: [{ value: 'new', label: 'New', tint: 'blue' }, { value: 'won', label: 'Won', tint: 'green' }, { value: 'lost', label: 'Lost', tint: 'rose' }],
      cards, key: card => card.id, group: card => card.stage, measure: card => card.value, onMove, card: card => BoardCard({ title: () => `Deal ${card().id}` }) }));
    const columns = [...host.querySelectorAll('.col-k')];
    expect(columns.map(column => column.querySelector('.ct')!.textContent)).toEqual(['2', '1', '0']);
    expect(columns[0].querySelector('.amt')!.textContent).toBe('400');
    expect(columns[1].querySelector('[role="progressbar"]')!.getAttribute('aria-valuenow')).toBe('60');
    expect(columns[2].querySelector('.col-empty')).not.toBeNull();
    const card = columns[0].querySelector<HTMLElement>('.card')!;
    key(card, 'ArrowRight', { altKey: true });
    expect(cards.peek()[0].stage).toBe('won');
    expect(lastToast()).toContain('Moved to Won');
    [...document.querySelectorAll<HTMLButtonElement>('.toast button')].pop()!.click();
    expect(cards.peek()[0].stage).toBe('new');
    key(columns[1].querySelector<HTMLElement>('.card')!, 'ArrowRight', { altKey: true });
    expect(lastToast()).toContain('Add a reason before marking it lost');
    expect(cards.peek()[2].stage).toBe('won');
  });

  it('Board offers Undo only when the card may move back, and undoes from where the card is now', () => {
    vi.useFakeTimers();
    const cards = cell([{ id: 1, stage: 'new' }]);
    const moved: string[] = [];
    const onMove = (card: { id: number; stage: string }, to: string) => {
      moved.push(`${card.stage}>${to}`);
      cards.set(cards.peek().map(item => item.id === card.id ? { ...item, stage: to } : item));
      return null;
    };
    // Nothing moves out of won.
    const canMove = (card: { stage: string }) => card.stage === 'won' ? 'Won deals stay won' : null;
    const host = render(() => Board({ label: 'Deals', columns: ['new', 'lost', 'won'].map(value => ({ value, label: value[0].toUpperCase() + value.slice(1), tint: 'blue' as const })),
      cards, key: card => card.id, group: card => card.stage, onMove, canMove, card: card => BoardCard({ title: () => `Deal ${card().id}` }) }));
    const cardIn = (index: number) => [...host.querySelectorAll<HTMLElement>('.col-k')][index].querySelector<HTMLElement>('.card')!;
    const toastButtons = () => [...document.querySelectorAll('.toast')].pop()!.querySelectorAll('button');
    key(cardIn(0), 'ArrowRight', { altKey: true });
    expect(lastToast()).toContain('Moved to Lost');
    toastButtons()[0].click();
    expect(cards.peek()[0].stage).toBe('new');
    expect(moved).toEqual(['new>lost', 'lost>new']);
    // Into won there is no way back, so no Undo; out of won is refused before onMove runs.
    cards.set([{ id: 2, stage: 'lost' }]);
    key(cardIn(1), 'ArrowRight', { altKey: true });
    expect(lastToast()).toContain('Moved to Won');
    expect(toastButtons()).toHaveLength(0);
    key(cardIn(2), 'ArrowLeft', { altKey: true });
    expect(lastToast()).toContain('Won deals stay won');
    expect(moved).toEqual(['new>lost', 'lost>new', 'lost>won']);
  });

  it('BulkBar rises with a selection and Filters remove and clear chips', () => {
    const selected = cell(0);
    const assign = vi.fn();
    const host = render(() => [BulkBar({ count: selected, actions: [{ label: 'Assign', icon: 'user', onClick: assign }], onClear: () => selected.set(0) }),
      Filters({ filters: cell<ActiveFilter[]>([{ key: 'owner', label: 'Owner: Aslam K' }, { key: 'stage', label: 'Stage: Won' }]) }), PageHeader({ icon: 'handshake', title: 'Deals', count: 42 })]);
    const bar = host.querySelector<HTMLElement>('.bulk')!;
    expect(bar.classList.contains('show')).toBe(false);
    expect(bar.getAttribute('aria-hidden')).toBe('true');
    selected.set(3);
    expect(bar.classList.contains('show')).toBe(true);
    expect(bar.querySelector('.n')!.textContent).toBe('3 selected');
    bar.querySelector<HTMLButtonElement>('[aria-label="Clear selection"]')!.click();
    expect(selected.peek()).toBe(0);
    host.querySelector<HTMLButtonElement>('[aria-label="Remove filter Owner: Aslam K"]')!.click();
    expect([...host.querySelectorAll('.filter-chip')].map(chip => chip.textContent)).toEqual(['Stage: Won']);
    [...host.querySelectorAll<HTMLButtonElement>('.filters button')].find(button => button.textContent === 'Clear')!.click();
    expect(host.querySelectorAll('.filter-chip')).toHaveLength(0);
    expect(host.querySelector('.ph h1')!.textContent).toBe('Deals');
  });
});

describe('Library page', () => {
  it('lists every catalog component with details, and demos the ones built here', async () => {
    const { store } = backend();
    await store.open(id(1));
    const host = render(() => LibraryPage({ record: store }));
    expect(ENTRIES).toHaveLength(118);
    expect(host.querySelectorAll('[data-libcard]')).toHaveLength(118);
    const live = builtIds(store);
    expect(live.length).toBeGreaterThan(70);
    for (const id of ['icon', 'button', 'tooltip', 'base-field', 'reference-field', 'date-range', 'inline-value', 'popover', 'modal', 'drawer', 'toast', 'data-table', 'child-table', 'doc-header', 'status-flow', 'board'])
      expect(live).toContain(id);
    expect(host.querySelector('#libc-child-table .grid')).not.toBeNull();
    host.querySelector<HTMLButtonElement>('#libc-currency-field .lib-foot')!.click();
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain('Exact money amount');
    expect(dialog.textContent).toContain('Currency()');
    [...dialog.querySelectorAll<HTMLButtonElement>('.lib-link')].find(button => button.textContent === 'BaseField')!.click();
    expect(document.querySelector('[role="dialog"] h2')!.textContent).toBe('BaseField');
    // The filter narrows both the index and the cards.
    const filter = host.querySelector<HTMLInputElement>('.lib-filter input')!;
    filter.value = 'childtable';
    filter.dispatchEvent(new Event('input'));
    expect(host.querySelectorAll('[data-libcard]:not([hidden])')).toHaveLength(1);
    expect(host.querySelectorAll('.lib-idx:not([hidden])')).toHaveLength(1);
  });
});
