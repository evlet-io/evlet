// The record page's decisions without the DOM (app/record-actions.ts): why a move or an action cannot run now,
// which actions the role has, whether the record can be deleted, and what More lists.
import { describe, expect, it } from 'vitest';
import { actionRefused, deletable, editBlocked, moveBlocked, moveTo, recordActions, recordMenuItems } from '../app/record-actions';
import type { ScreenModel } from '../app/meta';

const model = {
  name: 'invoice', label: 'Invoice', plural: 'Invoices', title_field: 'number', fields: [], list_fields: [],
  permissions: { create: true, update: true, delete: true },
  state: { field: 'status', show: 'badge', steps: [{ value: 'draft', label: 'Draft' }, { value: 'issued', label: 'Issued', lock: true }], rules: [] },
  actions: [{ name: 'send', label: 'Send', arguments: [] }, { name: 'remind', label: 'Remind', arguments: [] }],
} as unknown as ScreenModel;

describe('record decisions', () => {
  it('blocks every run on a new record, then on unsaved changes', () => {
    expect(editBlocked(true, () => false)).toBe('Save the record first');
    expect(editBlocked(false, () => true)).toBe('Save or discard your changes first');
    expect(editBlocked(false, () => false)).toBeNull();
  });

  it('picks the allowed move to a state and says why one cannot run', () => {
    const extras = { _moves: [
      { key: 'a', to: 'issued', label: 'Issue', allowed: false, reason: 'Add a line first' },
      { key: 'b', to: 'issued', label: 'Issue now', allowed: true },
      { key: 'c', to: 'void', label: 'Void', allowed: false },
    ] } as any;
    expect(moveTo(extras, 'issued')!.key).toBe('b');
    expect(moveBlocked(model, extras, 'issued', () => 'draft')).toBeNull();
    expect(moveBlocked(model, extras, 'void', () => 'draft')).toBe('Void is not available now');
    expect(moveBlocked(model, { _moves: [extras._moves[0]] } as any, 'issued', () => 'draft')).toBe('Add a line first');
    expect(moveBlocked(model, {}, 'issued', () => 'draft')).toBe('This record cannot move from Draft to Issued');
  });

  it('lists the actions the detail allows for the role, else the schema’s, and their refusals', () => {
    expect(recordActions(model, {}).map(action => action.name)).toEqual(['send', 'remind']);
    const extras = { _actions: [{ name: 'remind', label: 'Remind now', allowed: false, reason: 'Nothing is due' }] };
    expect(recordActions(model, extras)).toEqual([{ name: 'remind', label: 'Remind now', arguments: [] }]);
    expect(actionRefused(extras, { name: 'remind', label: 'Remind now' } as any)).toBe('Nothing is due');
    expect(actionRefused(extras, { name: 'send', label: 'Send' } as any)).toBeNull();
  });

  it('deletes only a saved, unlocked record the role may delete that never posted', () => {
    expect(deletable(model, false, false, {})).toBe(true);
    expect(deletable(model, true, false, {})).toBe(false);
    expect(deletable(model, false, true, {})).toBe(false);
    expect(deletable(model, false, false, { _deletable: false })).toBe(false);
    expect(deletable({ ...model, permissions: { create: true, update: true, delete: false } } as ScreenModel, false, false, {})).toBe(false);
  });

  it('builds More: Copy link always, the saved record’s items, Reset layout and Delete after a separator', () => {
    const none = () => {};
    const handlers = { copyLink: none, copyId: none, reload: none, onDuplicate: none, resetLayout: none, onRemove: none };
    const labels = (items: ReturnType<typeof recordMenuItems>) => items.map(item => item === 'separator' ? '—' : 'label' in item ? item.label : item.heading);
    expect(labels(recordMenuItems({ ...handlers, isNew: true, duplicate: false, layout: null, remove: false }))).toEqual(['Copy link']);
    const saved = recordMenuItems({ ...handlers, isNew: false, duplicate: true, layout: { changed: false }, remove: true });
    expect(labels(saved)).toEqual(['Copy link', 'Copy record ID', 'Reload', 'Duplicate', 'Reset layout', '—', 'Delete']);
    expect((saved[4] as { disabled?: boolean }).disabled).toBe(true);
  });
});
