import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createClient } from '../api';
import { cell, scope } from '../reactive';
import { RecordStore } from '../model/record';
import { keepChanges, keepWhenHidden, KEEP_PAUSE_MS } from '../app/unsaved';
import { reviewConflict } from '../app/conflict-review';
import { byText, click, stopAll, text, until } from './screens-helpers';
import { id, responses, server } from './server';

const stops: Array<() => void> = [];
afterEach(() => { stops.splice(0).forEach(stop => stop()); stopAll(); vi.useRealTimers(); });
async function saved(api: any = {}) {
  const store = new RecordStore({ api, schema: responses.schema.body as any, model: 'customer' });
  store.applyServer({ id: id(1), title: 'Original', rate: '1.00', credit_days: 30 }, '"old"');
  await store.loadAll(); stops.push(() => store.dispose()); return store;
}

describe('visible bounded draft keeping', () => {
  it('deduplicates background and leave attempts and aborts a timed-out request', async () => {
    vi.useFakeTimers();
    const keepUnsaved = vi.fn(() => new Promise(() => {}));
    const store = await saved(); store.field('title').set('Waiting');
    const ctx: any = { api: { keepUnsaved }, me: cell(null) };
    const first = keepChanges(ctx, store.meta as any, store, false), second = keepChanges(ctx, store.meta as any, store, false);
    expect(keepUnsaved).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(5000);
    expect(await first).toContain('did not answer in time'); expect(await second).toBe(await first);
    expect((keepUnsaved.mock.calls[0] as any[])[4].aborted).toBe(true);
  });

  it('removes a late kept copy when a normal Save already completed', async () => {
    let finish!: (value: any) => void;
    const keepUnsaved = vi.fn(() => new Promise(resolve => { finish = resolve; })), discardUnsaved = vi.fn().mockResolvedValue(undefined);
    const store = await saved(); store.field('title').set('Saved now');
    const keeping = keepChanges({ api: { keepUnsaved, discardUnsaved }, me: cell(null) } as any, store.meta as any, store, false);
    store.applyServer({ id: id(1), title: 'Saved now', rate: '1.00', credit_days: 30 }, '"new"'); await store.loadAll();
    finish({ dropped: [], saved_at: 1, size: 1 }); await keeping;
    expect(discardUnsaved).toHaveBeenCalledExactlyOnceWith('customer', id(1));
    expect(store.dirty.peek()).toBe(false);
  });

  it('debounces edits, serializes attempts and never calls a record save', async () => {
    vi.useFakeTimers();
    const pending: Array<(value: any) => void> = [];
    const keepUnsaved = vi.fn(() => new Promise(resolve => pending.push(resolve)));
    const store = await saved();
    const ctx: any = { api: { keepUnsaved }, me: cell(null) };
    let keeper!: ReturnType<typeof keepWhenHidden>;
    stops.push(scope(() => { keeper = keepWhenHidden(ctx, store.meta as any, store, false, () => store.dirty.peek()); }));
    store.field('title').set('First');
    await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS - 1);
    expect(keepUnsaved).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(keeper.state.peek().kind).toBe('keeping');
    store.field('title').set('Second');
    await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS);
    expect(keepUnsaved).toHaveBeenCalledTimes(1);
    pending[0]({ dropped: [], saved_at: 1, size: 1 });
    await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS);
    expect(keepUnsaved).toHaveBeenCalledTimes(2);
    expect(keepUnsaved.mock.calls[1].slice(0, 4)).toMatchObject(['customer', id(1), { values: { title: 'Second' } }, '"old"']);
    pending[1]({ dropped: [], saved_at: 2, size: 1 }); await vi.advanceTimersByTimeAsync(0);
    expect(keeper.state.peek().kind).toBe('kept');
    expect(store.dirty.peek()).toBe(true); expect(store.etag.peek()).toBe('"old"');
  });

  it('shows a failed background attempt and lets the person retry while the record stays unsaved', async () => {
    vi.useFakeTimers();
    const keepUnsaved = vi.fn().mockRejectedValueOnce(new ApiError('Connection lost', 0, 'network')).mockResolvedValue({ dropped: [] });
    const store = await saved();
    let keeper!: ReturnType<typeof keepWhenHidden>;
    stops.push(scope(() => { keeper = keepWhenHidden({ api: { keepUnsaved }, me: cell(null) } as any, store.meta as any, store, false, () => store.dirty.peek()); }));
    store.field('title').set('Keep me'); await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS);
    expect(keeper.state.peek()).toMatchObject({ kind: 'failed' });
    expect(keeper.state.peek().text).toContain('Connection lost');
    await keeper.retry();
    expect(keeper.state.peek().kind).toBe('kept'); expect(store.field('title').value.peek()).toBe('Keep me');
  });

  it('does not claim the last copy includes text still being edited and respects disabled keeping', async () => {
    vi.useFakeTimers();
    const keepUnsaved = vi.fn().mockResolvedValue({ dropped: [] }), me = cell<any>(null);
    const store = await saved();
    const frame = document.createElement('div'); frame.innerHTML = '<div class="fld"><textarea></textarea></div>';
    let keeper!: ReturnType<typeof keepWhenHidden>;
    stops.push(scope(() => { keeper = keepWhenHidden({ api: { keepUnsaved }, me } as any, store.meta as any, store, false, () => store.dirty.peek()); keeper.watch(frame); }));
    store.field('title').set('Committed'); await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS);
    frame.querySelector('textarea')!.dispatchEvent(new Event('input', { bubbles: true }));
    await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS);
    expect(keeper.state.peek().text).toContain('Latest text is not kept');
    expect(keepUnsaved).toHaveBeenCalledTimes(1);
    me.set({ prefs: { keep_unsaved: false }, unsaved: { enabled: false } });
    store.field('title').set('Stay in this tab'); await vi.advanceTimersByTimeAsync(KEEP_PAUSE_MS);
    expect(keepUnsaved).toHaveBeenCalledTimes(1);
    expect(keeper.state.peek().kind).toBe('idle');
  });
});

describe('reviewing a concurrent change', () => {
  const latest = { status: 200, etag: '"new"', body: { id: id(1), title: 'Saved elsewhere', rate: '2.00', credit_days: 45 } };
  async function edited(reply: any = latest) {
    const stub = server([['GET', /\/api\/customer\/0000000000001$/, reply]]);
    const store = await saved(createClient({ fetch: stub.fetch }));
    store.field('title').set('My draft'); store.conflict.set(new ApiError('Changed', 412, 'conflict'));
    return { store, sent: stub.sent };
  }

  it('shows both values and leaves the original draft and revision intact on Cancel', async () => {
    const { store, sent } = await edited(); reviewConflict(store);
    await until(() => expect(document.querySelector('.ev-conflict')).not.toBeNull());
    expect(text(document.querySelector('.ev-conflict'))).toContain('My draft');
    expect(text(document.querySelector('.ev-conflict'))).toContain('Saved elsewhere');
    click(byText(document, '.modal button', 'Cancel'));
    expect(store.field('title').value.peek()).toBe('My draft'); expect(store.etag.peek()).toBe('"old"');
    expect(sent.every(request => request.method === 'GET')).toBe(true);
  });

  it.each(['Keep yours', 'Keep saved'])('applies the chosen comparison as an unsaved draft (%s)', async choice => {
    const { store, sent } = await edited(); reviewConflict(store);
    await until(() => expect(document.querySelector('.ev-conflict')).not.toBeNull());
    click(byText(document, '.ev-conflict button', choice));
    click(byText(document, '.modal button', 'Apply comparison'));
    await until(() => expect(document.querySelector('.modal')).toBeNull());
    expect(store.field('title').value.peek()).toBe(choice === 'Keep yours' ? 'My draft' : 'Saved elsewhere');
    expect(store.field('rate').value.peek()).toBe('2.00'); expect(store.etag.peek()).toBe('"new"');
    expect(store.dirty.peek()).toBe(choice === 'Keep yours');
    expect(sent.every(request => request.method === 'GET')).toBe(true);
  });

  it('keeps the draft intact when the latest record cannot be read', async () => {
    const { store } = await edited({ status: 503, etag: null, body: { error: 'Unavailable' } }); reviewConflict(store);
    await until(() => expect(text(document.querySelector('[role="alert"]'))).toContain('could not be read'));
    expect(store.field('title').value.peek()).toBe('My draft'); expect(store.etag.peek()).toBe('"old"');
    expect((byText(document, '.modal button', 'Apply comparison') as HTMLButtonElement).disabled).toBe(true);
  });

  it('does not silently merge a row deletion over another person’s saved row', async () => {
    const stub = server([
      ['GET', /\/api\/order\/0000000000001$/, responses.detail],
      ['GET', /\/lines\/lines\?/, responses.lines_page],
      ['GET', /\/api\/customer\/0000000000001$/, { ...responses.customer, status: 200 }],
      ['GET', /\/api\/product\/0000000000001$/, { ...responses.product, status: 200 }],
    ]);
    const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema: responses.schema.body as any, model: 'order' });
    stops.push(() => store.dispose());
    await store.open(id(1));
    const table = store.table('lines'); table.remove(table.rows.peek()[0]);
    const revision = store.etag.peek();
    reviewConflict(store);
    await until(() => expect(text(document.querySelector('[role="alert"]'))).toContain('row additions, deletions'));
    expect((byText(document, '.modal button', 'Apply comparison') as HTMLButtonElement).disabled).toBe(true);
    expect(table.rows.peek()).toHaveLength(119); expect(store.etag.peek()).toBe(revision);
    click(byText(document, '.modal button', 'Discard my changes and reload'));
    const confirm = [...document.querySelectorAll('.modal')].at(-1)!;
    expect(text(confirm)).toContain('Discard this draft?');
    click(byText(confirm, 'button', 'Cancel'));
    expect(table.rows.peek()).toHaveLength(119); expect(store.dirty.peek()).toBe(true);
  });
});
