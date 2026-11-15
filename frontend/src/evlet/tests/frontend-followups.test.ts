import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClient } from '../api';
import { RecordStore } from '../model/record';
import { openReportPrint } from '../app/report-page';
import { closeDialogs } from '../ui/layers';
import { adminRoutes, invoice, byText, click, press, requests, start, stopAll, until, text, typeInto } from './screens-helpers';
import { copy, id, responses, server } from './server';

const tick = () => new Promise(resolve => setTimeout(resolve, 10));
const deferred = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; };
afterEach(() => { stopAll(); vi.restoreAllMocks(); });

describe('record and request lifetimes', () => {
  it('aborts reads and never installs a detail received after disposal', async () => {
    const answer = deferred<any>();
    let signal: AbortSignal | undefined;
    const api: any = { get: (_model: string, _id: string, passed: AbortSignal) => { signal = passed; return answer.promise; } };
    const store = new RecordStore({ api, schema: responses.schema.body as any, model: 'customer' });
    const opening = store.open(id(1));
    store.dispose();
    expect(signal?.aborted).toBe(true);
    answer.resolve({ data: responses.customer.body, etag: null });
    await opening;
    expect(store.ready.peek()).toBe(false);
    expect(() => store.root).toThrow('Open or create');
  });

  it('does not append late child pages or restart calculations after disposal', async () => {
    const answer = deferred<any>();
    const stub = server([
      ['GET', /\/api\/order\/0000000000001$/, 'detail'],
      ['GET', /\/lines\/lines\?/, () => answer.promise],
    ]);
    const store = new RecordStore({ api: createClient({ fetch: stub.fetch }), schema: responses.schema.body as any, model: 'order' });
    await store.open(id(1), { loadAll: false });
    const rows = store.table('lines').rows.peek();
    const loading = store.loadAll();
    store.dispose();
    answer.resolve(responses.lines_page);
    await loading;
    expect(store.table('lines').rows.peek()).toBe(rows);
    expect(rows).toHaveLength(100);
    expect(store.ready.peek()).toBe(false);
    expect(store.engine).toBeNull();
    expect(stub.sent).toHaveLength(2);
  });

  it('keeps the latest tab totals when an older measure request finishes last', async () => {
    const original = deferred<any>();
    const { host, sent } = start([
      ['GET', /\/api\/auth\/session$/, invoice.session], ['GET', /\/api\/schema$/, invoice.schema],
      ['GET', /\/api\/invoice\?/, invoice.invoices],
      ['GET', /\/api\/invoice\/measures/, request => new URL(request.url, 'http://x').searchParams.has('filters') ? copy(invoice.invoice_measures_issued) : original.promise],
      ['GET', /\/api\/invoice\/list_tabs/, invoice.invoice_list_tabs],
      ['GET', new RegExp(`/api/customer/${invoice.invoice_customer.body.id}$`), invoice.invoice_customer],
    ], '/app/invoice');
    await until(() => expect(requests(sent, 'GET', /measures/)).toHaveLength(1));
    click(byText(host, '.ev-list [role="tab"]', /^Issued/));
    await until(() => expect(text(host.querySelector('.sum'))).toContain('Count1'));
    original.resolve(copy(invoice.invoice_measures));
    await original.promise; await tick();
    expect(text(host.querySelector('.sum'))).toContain('Count1');
    expect(location.search).toContain('tab=issued');
  });
});

describe('keyboard save', () => {
  it.each([{ ctrlKey: true }, { metaKey: true }])('commits active textarea text before keyboard create (%j)', async modifiers => {
    const { host, sent } = start([...adminRoutes(), ['POST', /\/api\/contact$/, () => new Promise(() => {})]], '/app/contact/new');
    await until(() => expect(host.querySelector('textarea')).not.toBeNull());
    typeInto(host.querySelector<HTMLInputElement>('input[aria-required="true"]')!, 'Keyboard contact');
    const notes = host.querySelector('textarea')!;
    notes.focus(); notes.value = 'Keep these notes without requiring a blur.';
    notes.dispatchEvent(new Event('input', { bubbles: true }));
    press(notes, 'Enter', modifiers);
    await until(() => expect(requests(sent, 'POST', /\/contact$/)).toHaveLength(1));
    expect(requests(sent, 'POST', /\/contact$/)[0].body.notes).toBe(notes.value);
    press(notes, 'Enter', { ...modifiers, repeat: true });
    expect(requests(sent, 'POST', /\/contact$/)).toHaveLength(1);
  });
});

describe('report PDF previews', () => {
  it('does not create an object URL for a response arriving after the drawer closes', async () => {
    const answer = deferred<Blob>();
    const create = vi.fn(() => 'blob:late');
    (URL as any).createObjectURL = create;
    (URL as any).revokeObjectURL = vi.fn();
    let signal!: AbortSignal;
    openReportPrint({ api: { reportFile: (_name: string, _print: unknown, _body: unknown, passed: AbortSignal) => { signal = passed; return answer.promise; } } } as any,
      { name: 'Rates', label: 'Rates' } as any, [{ name: 'A', title: 'A', default: true }], {});
    closeDialogs();
    expect(signal.aborted).toBe(true);
    answer.resolve(new Blob(['old PDF']));
    await answer.promise; await tick();
    expect(create).not.toHaveBeenCalled();
  });

  it('accepts only the latest request even when a design is selected again', async () => {
    const answers = [deferred<Blob>(), deferred<Blob>(), deferred<Blob>()];
    const signals: AbortSignal[] = [];
    const create = vi.fn(() => 'blob:current'), revoke = vi.fn();
    (URL as any).createObjectURL = create; (URL as any).revokeObjectURL = revoke;
    openReportPrint({ api: { reportFile: (_name: string, _print: unknown, _body: unknown, signal: AbortSignal) => { signals.push(signal); return answers[signals.length - 1].promise; } } } as any,
      { name: 'Rates', label: 'Rates' } as any, [{ name: 'A', title: 'A', default: true }, { name: 'B', title: 'B', default: false }], {});
    const select = document.querySelector<HTMLSelectElement>('.ev-print-design select')!;
    for (const name of ['B', 'A']) { select.value = name; select.dispatchEvent(new Event('change', { bubbles: true })); }
    const latest = new Blob(['current PDF']);
    answers[2].resolve(latest); await tick();
    answers[0].resolve(new Blob(['stale A'])); answers[1].resolve(new Blob(['stale B'])); await tick();
    expect(create).toHaveBeenCalledExactlyOnceWith(latest);
    expect(signals.slice(0, 2).every(signal => signal.aborted)).toBe(true);
    closeDialogs();
    expect(revoke).toHaveBeenCalledExactlyOnceWith('blob:current');
  });
});
