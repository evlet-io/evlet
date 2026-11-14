import { html } from '../dom';
import { RecordStore, type Row } from '../model/record';
import { buildChanges } from '../model/changes';
import { apply, capture, conflicts, type NodeChanges } from '../model/unsaved';
import { cell } from '../reactive';
import { Button, SegmentedControl } from '../ui/base';
import { displayValue, localField } from '../ui/fields';
import { copyText, DialogFooter, openModal, toast } from '../ui/layers';
import type { RecordData } from '../api';
import { unfinishedStructure } from './unsaved';

/** All saved rows, including section rows, after loadAll. This stays in memory for this review only. */
function savedData(row: Row): RecordData {
  const result: RecordData = { id: row.id!, ...Object.fromEntries([...row.fields].map(([name, field]) => [name, field.server.peek()])) };
  const children: Record<string, RecordData[]> = {};
  for (const [name, table] of row.tables) {
    const sections = table.savedSections;
    const rows = table.saved;
    const sectionData = (section: typeof sections[number]): RecordData => ({ id: section.id!, section: section.label });
    children[name] = rows.flatMap(child => [...sections.filter(section => section.before === child.key).map(sectionData), savedData(child)]);
    children[name].push(...sections.filter(section => section.before === null || !rows.some(child => child.key === section.before)).map(sectionData));
  }
  if (Object.keys(children).length) result.children = children;
  return result;
}

// A removal carries no copy of the deleted row's base values, and a newly added row may already
// exist after a lost save response. Do not resolve these structural decisions as ordinary fields.
function structuralChanges(changes: NodeChanges): boolean {
  return Object.values(changes.children ?? {}).some(table => Boolean(table.added?.length || table.removed?.length) ||
    Object.values(table.changed ?? {}).some(structuralChanges));
}

/** Compare before replacing anything. Cancel and failed reads leave the original draft intact. */
export function reviewConflict(store: RecordStore): () => void {
  const changes = capture(store.root), patch = JSON.stringify(buildChanges(store.root), null, 2);
  const version = store.editVersion.peek(), originalEtag = store.etag.peek(), originalConflict = store.conflict.peek();
  const reading = new AbortController();
  const temporary = new RecordStore({ api: store.api, schema: store.index, model: store.meta.name, user: store.user });
  const phase = cell<'loading' | 'ready' | 'failed'>('loading'), problem = cell(''), busy = cell(false);
  const choices = new Map<string, ReturnType<typeof cell<'mine' | 'saved'>>>();
  let latest: RecordData | null = null, etag: string | null = null, found: ReturnType<typeof conflicts> = [];
  let alive = true, blocked = unfinishedStructure(store.root) || structuralChanges(changes);
  let close = () => {};
  const show = (item: typeof found[number], value: unknown) => displayValue(localField({ ...item.field.meta, read_only: true }, value), { empty: 'Empty' });
  const restore = async () => {
    if (!latest || blocked || busy.peek()) return;
    if (store.editVersion.peek() !== version || store.etag.peek() !== originalEtag) { problem.set('Your draft changed while this comparison was open. Close it and compare again.'); return; }
    const skip = new Set(found.filter(item => choices.get(item.path)!.peek() === 'saved').map(item => item.path));
    // Verify that every selected edit still fits before replacing the original draft.
    const missed = apply(temporary.root, changes, skip);
    if (missed) { blocked = true; problem.set(`${missed} changes no longer fit the saved record. Your original draft is still open. Copy it before deciding what to discard.`); return; }
    busy.set(true);
    const original = savedData(store.root);
    try {
      store.applyServer(latest, etag);
      await store.loadAll();
      if (!alive) return;
      if (apply(store.root, changes, skip)) throw new Error('Changes could not be applied');
      close();
      toast('Changes compared and restored. Review them, then Save.');
    } catch {
      try { store.applyServer(original, originalEtag); await store.loadAll(); apply(store.root, changes); store.conflict.set(originalConflict); } catch { /* The copy remains available even if calculations cannot attach. */ }
      problem.set('The comparison could not be applied. Your original changes are available with Copy my changes.');
    }
    finally { busy.set(false); }
  };
  close = openModal({ title: 'Compare with the latest saved record', icon: 'history', tint: 'amber', className: 'ev-unsaved-conflicts',
    onClose: () => { alive = false; reading.abort(); temporary.dispose(); },
    body: () => html`<div class="ev-conflicts">
      <p class="muted">Your draft stays open until you apply this comparison. Applying keeps the selected edits unsaved; the final Save still checks the record version.</p>
      ${() => phase.get() === 'loading' ? html`<p>Reading the latest record…</p>` : phase.get() === 'ready' ? html`
        ${found.length ? found.map(item => html`<div class="ev-conflict" data-path=${item.path}><strong>${item.label}</strong>
          <div class="ev-conflict-values"><span><span class="muted">Yours</span> ${show(item, item.mine)}</span><span><span class="muted">Saved</span> ${show(item, item.saved)}</span></div>
          ${SegmentedControl({ label: `Keep for ${item.label}`, size: 'small', value: choices.get(item.path)!, options: [{ value: 'mine', label: 'Keep yours' }, { value: 'saved', label: 'Keep saved' }] })}</div>`)
          : html`<p>No overlapping field changes.</p>`}` : null}
      ${() => problem.get() ? html`<p role="alert">${problem}</p>` : null}
      ${Button({ label: 'Copy my changes', icon: 'copy', variant: 'ghost', onClick: () => void copyText(patch, 'your changes') })}
      ${Button({ label: 'Discard my changes and reload', variant: 'soft-danger', onClick: () => openModal({ title: 'Discard this draft?', icon: 'trash-2', tint: 'rose', narrow: true,
        body: () => html`<p>This discards your open edits and reads the saved record again. Copy your changes first if you need to keep them.</p>`,
        footer: done => DialogFooter({ cancel: done, submit: { label: 'Discard and reload', variant: 'danger', onClick: async () => {
          done(); try { await store.reload(); close(); } catch { problem.set('The latest record could not finish loading. Try again.'); }
        } } }) }) })}
    </div>`,
    footer: done => DialogFooter({ cancel: done, submit: { label: 'Apply comparison', disabled: () => phase.get() !== 'ready' || Boolean(problem.get()) || busy.get(), onClick: () => void restore() } }) });
  void (async () => {
    try {
      const response = await store.api.get(store.meta.name, store.id!, reading.signal);
      if (!alive) return;
      temporary.applyServer(response.data, response.etag); await temporary.loadAll();
      if (!alive) return;
      latest = savedData(temporary.root); etag = response.etag;
      found = conflicts(temporary.root, changes);
      found.forEach(item => choices.set(item.path, cell<'mine' | 'saved'>('mine')));
      if (response.data._locked) { blocked = true; problem.set('The saved record is now locked. Your draft is still open; copy your changes before deciding what to discard.'); }
      else if (blocked) problem.set('This draft includes row additions, deletions, ordering or section changes. Keep it open or copy the full changes; these cannot be merged automatically.');
      phase.set('ready');
    } catch { if (alive) { phase.set('failed'); problem.set('The latest record could not be read. Close this comparison and try again; your draft is unchanged.'); } }
  })();
  return close;
}
