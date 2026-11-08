// The bar a record shows when the person left unsaved changes on it (docs/ui.md, Unsaved changes):
// "You have unsaved changes from 5 minutes ago. Restore / Discard". A new record offers its
// unfinished draft from this browser with Restore / Start fresh. When someone saved the record in
// the meantime, Restore first lists the fields both changed, to keep theirs or the saved value.

import { html, when } from '../dom';
import { cell } from '../reactive';
import type { RecordStore } from '../model/record';
import { apply, conflicts, type Conflict, type NodeChanges } from '../model/unsaved';
import { Button, SegmentedControl, StateBanner } from '../ui/base';
import { displayValue, localField } from '../ui/fields';
import { DialogFooter, openModal, toast } from '../ui/layers';
import { relativeTime } from '../ui/time';
import type { UnsavedEntry } from './client';
import type { AppContext } from './context';
import type { ScreenModel } from './meta';
import { keepsUnsaved, unsavedInfo } from './unsaved';

type Bar =
  | { kind: 'server'; entry: UnsavedEntry; note: string }
  | { kind: 'local'; changes: NodeChanges; saved_at: number }
  | { kind: 'note'; text: string };

const missedNote = (missed: number) => missed ? ` ${missed === 1 ? 'One change no longer fits' : `${missed} changes no longer fit`} this record and ${missed === 1 ? 'was' : 'were'} left out.` : '';

export interface UnsavedBarOptions { isNew: boolean; /** A new record's draft was restored or set aside: the bar is gone. */ onSettled?: () => void }

/** The restore bar for a record that is ready to edit. Empty when nothing was kept. */
export function UnsavedBar(ctx: AppContext, model: ScreenModel, store: RecordStore, options: UnsavedBarOptions): Node {
  const bar = cell<Bar | null>(null, { name: `${model.name} unsaved` });
  const id = store.id;
  if (keepsUnsaved(ctx, model)) void (async () => {
    if (options.isNew) {
      const kept = ctx.unsaved.get(model.name, unsavedInfo(ctx).limits);
      if (kept) bar.set({ kind: 'local', changes: kept.changes, saved_at: kept.saved_at });
      return;
    }
    // Nobody asks the server about a record unless they have kept changes somewhere.
    if (!id || !unsavedInfo(ctx).count) return;
    try {
      const read = await ctx.api.unsaved(model.name, id);
      if (read.dropped === 'record') bar.set({ kind: 'note', text: 'Your unsaved changes were discarded because you can no longer change this record.' });
      else if (read.entry) bar.set({ kind: 'server', entry: read.entry,
        note: read.dropped.length ? ` Some fields you can no longer change were left out.` : '' });
    } catch { /* Nothing to offer: the record opens as saved. */ }
  })();

  const restoreServer = (entry: UnsavedEntry) => {
    const found = entry.stale ? conflicts(store.root, entry.changes) : [];
    const finish = (skip: Set<string>) => {
      const missed = apply(store.root, entry.changes, skip);
      bar.set(null);
      toast(`Changes restored. Save to keep them.${missedNote(missed)}`);
    };
    if (!found.length) { finish(new Set()); return; }
    openConflicts(found, finish, () => void discard());
  };

  const restoreLocal = (changes: NodeChanges) => {
    bar.set(null);
    // Set as the person's own edits, so leaving keeps them again.
    const missed = apply(store.root, changes);
    toast(`Draft restored. Nothing is saved until you create it.${missedNote(missed)}`);
    options.onSettled?.();
  };

  const discard = async () => {
    const current = bar.peek();
    bar.set(null);
    if (current?.kind === 'local') { ctx.unsaved.remove(model.name); options.onSettled?.(); return; }
    if (current?.kind === 'server' && id) {
      try { await ctx.api.discardUnsaved(model.name, id); toast('Unsaved changes discarded'); }
      catch { toast('Unsaved changes could not be discarded. Try again.', { icon: 'info' }); bar.set(current); }
    }
  };

  return html`<div class="ev-unsaved">${when(() => bar.get(), () => {
    const current = bar.peek()!;
    if (current.kind === 'note') return StateBanner({ tone: 'neutral', text: current.text, actions: [Button({ label: 'Dismiss', size: 'small', variant: 'ghost', onClick: () => bar.set(null) })] });
    if (current.kind === 'local') return StateBanner({ tone: 'info', icon: 'history',
      text: `Restore your unfinished new ${model.label.toLowerCase()} from ${relativeTime(new Date(current.saved_at * 1000))}?`,
      actions: [Button({ label: 'Start fresh', size: 'small', variant: 'ghost', onClick: () => void discard() }),
        Button({ label: 'Restore', size: 'small', variant: 'primary', onClick: () => restoreLocal(current.changes) })] });
    return StateBanner({ tone: 'info', icon: 'history',
      text: `You have unsaved changes from ${relativeTime(new Date(current.entry.saved_at * 1000))}.${current.entry.stale ? ' This record was saved since.' : ''}${current.note}`,
      actions: [Button({ label: 'Discard', size: 'small', variant: 'ghost', onClick: () => void discard() }),
        Button({ label: 'Restore', size: 'small', variant: 'primary', onClick: () => restoreServer(current.entry) })] });
  })}</div>`;
}

/** Fields both sides changed: keep yours or the saved value, per field, or discard all. */
export function openConflicts(found: Conflict[], restore: (skip: Set<string>) => void, discardAll: () => void) {
  const choices = new Map(found.map(item => [item.path, cell<'mine' | 'saved'>('mine', { name: `conflict ${item.path}` })]));
  const shown = (item: Conflict, value: unknown) => displayValue(localField({ ...item.field.meta, read_only: true }, value), { empty: 'Empty' });
  // The buttons run after openModal returns, so close is set by then.
  let close = () => {};
  close = openModal({ title: 'This record was saved since you left it', icon: 'history', tint: 'amber', className: 'ev-unsaved-conflicts',
    body: () => html`<div class="ev-conflicts">
      <p class="muted">You and the saved record both changed these fields. Choose which value to keep for each.</p>
      ${found.map(item => html`<div class="ev-conflict" data-path=${item.path}>
        <div class="ev-conflict-label"><strong>${item.label}</strong></div>
        <div class="ev-conflict-values"><span><span class="muted">Yours</span> ${shown(item, item.mine)}</span>
          <span><span class="muted">Saved</span> ${shown(item, item.saved)}</span></div>
        ${SegmentedControl({ label: `Keep for ${item.label}`, size: 'small', value: choices.get(item.path)!,
          options: [{ value: 'mine', label: 'Keep yours' }, { value: 'saved', label: 'Keep saved' }] })}
      </div>`)}
      <div>${Button({ label: 'Discard all my changes', variant: 'ghost', onClick: () => { close(); discardAll(); } })}</div>
    </div>`,
    footer: done => DialogFooter({ cancel: done, submit: { label: 'Restore', onClick: () => {
      done();
      restore(new Set(found.filter(item => choices.get(item.path)!.peek() === 'saved').map(item => item.path)));
    } } }) });
}
