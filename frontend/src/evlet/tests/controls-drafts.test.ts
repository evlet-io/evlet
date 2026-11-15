import { describe, expect, it } from 'vitest';
import { Drafts, describe as describeChange } from '../controls/drafts';
import { overrideText } from '../controls/system';
import type { ControlsClient, OverrideItem } from '../controls/client';

const item = (key: string, value: unknown = null): OverrideItem =>
  ({ kind: 'setting', key, file: 12, value, changed_by: null, changed_at: null });
const first = 'setting:security.session_hours', second = 'setting:security.lockout';
const deferred = <T>() => {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
function pendingSave() {
  const sent: unknown[] = [], write = deferred<unknown>(), refresh = deferred<any>();
  const api = { saveOverrides: (changes: unknown) => { sent.push(changes); return write.promise; }, overrides: () => refresh.promise };
  const drafts = new Drafts(api as unknown as ControlsClient);
  drafts.load([item('security.session_hours'), item('security.lockout')], 1);
  drafts.set(first, 8);
  const saved = drafts.save();
  return { drafts, saved, write, refresh, sent, api };
}

describe('Controls drafts during a save', () => {
  it('keeps later edits to the same and another item, then saves them', async () => {
    const { drafts, saved, write, refresh, sent, api } = pendingSave();
    drafts.set(first, 4);
    drafts.set(second, 5);
    write.resolve({ version: 2 });
    refresh.resolve({ items: [item('security.session_hours', 8), item('security.lockout')], version: 2 });
    expect(await saved).toBe(true);
    expect(sent).toEqual([[{ kind: 'setting', key: 'security.session_hours', value: 8 }]]);
    expect(drafts.value(first)).toBe(4);
    expect(drafts.value(second)).toBe(5);
    expect(drafts.changes.peek()).toHaveLength(2);
    // A new successful response acknowledges precisely the remaining edits.
    api.saveOverrides = async (changes: unknown) => { sent.push(changes); return { version: 3 }; };
    api.overrides = async () => ({ items: [item('security.session_hours', 4), item('security.lockout', 5)], version: 3 });
    expect(await drafts.save()).toBe(true);
    expect(drafts.changes.peek()).toHaveLength(0);
  });

  it('keeps an edit back to the old saved value across config refreshes and rejects stale responses', async () => {
    const { drafts, saved, write, refresh } = pendingSave();
    drafts.set(first, 12);
    drafts.load([item('security.session_hours'), item('security.lockout')], 1);
    expect(drafts.dirty(first)).toBe(true);
    write.resolve({ version: 2 });
    refresh.resolve({ items: [item('security.session_hours', 8), item('security.lockout')], version: 2 });
    await saved;
    drafts.load([item('security.session_hours'), item('security.lockout')], 1);
    expect(drafts.value(first)).toBe(12);
    expect(drafts.dirty(first)).toBe(true);
    expect(drafts.item(first)?.value).toBe(8);
  });

  it('keeps local edits when the write succeeds but its refresh fails', async () => {
    const { drafts, saved, write, refresh } = pendingSave();
    drafts.set(first, 4);
    write.resolve({ version: 2 });
    refresh.reject(new Error('Offline'));
    await expect(saved).rejects.toThrow('Offline');
    expect(drafts.value(first)).toBe(4);
    expect(drafts.dirty(first)).toBe(true);
    expect(drafts.saving.peek()).toBe(false);
  });
});

describe('layout change summaries', () => {
  it('describes legacy sections and every hidden side-panel item in review and saved overrides', () => {
    const sections = [{ title: 'Details', fields: [{ name: 'notes', width: 12 as const }] }];
    const layout: OverrideItem = { kind: 'layout', key: 'Customer', file: sections,
      value: { sections, panel: { fields: ['notes'], sections: ['Private'], tabs: ['activity'], blocks: ['summary'] } },
      changed_by: 'admin', changed_at: 1 };
    const review = describeChange({ id: 'layout:Customer', item: layout, from: null, to: layout.value }, {});
    expect(review.from).toBe('Details (1) (file)');
    for (const part of ['fields: notes', 'sections: Private', 'tabs: activity', 'blocks: summary']) expect(review.to).toContain(part);
    expect(overrideText(layout, {}).value).toBe(review.to);
    expect(overrideText({ ...layout, file: null }, {}).file).toBe('No longer defined in the app');
  });
});
