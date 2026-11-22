// The record's name or number shows once (docs/ui.md, Each fact once on the record page): while this person can
// edit it, the form keeps the field and the header does not repeat it in a side panel's subtitle; while nothing
// lets them edit it (read only, or locked by the record's state), the header shows it and the form leaves it out.
// Over the recorded quote (naming fills its number), with its title moved to its own title field.
import { afterEach, describe, expect, it } from 'vitest';
import { recorded, screen, start, stopAll, text, until, type Route } from './screens-helpers';
import { copy, type Recorded } from './server';

afterEach(stopAll);

const id = screen.quote_detail.body.id as string;

function routes(options: { numberWritable: boolean; locked?: boolean }): Route[] {
  const schema = copy(screen.schema);
  const quote = schema.body.models.find((model: { name: string }) => model.name === 'quote');
  Object.assign(quote, { title_field: 'title', open_in: 'panel' });
  quote.fields.find((meta: { name: string }) => meta.name === 'number').read_only = !options.numberWritable;
  const detail: Recorded = copy(screen.quote_detail);
  Object.assign(detail.body, { title: 'Spring quote', ...(options.locked ? { _locked: true } : {}) });
  return [
    ['GET', /\/api\/auth\/session$/, recorded.session],
    ['GET', /\/api\/schema$/, schema],
    ['GET', /\/api\/quote\?/, { status: 200, etag: null, body: { items: [detail.body], total: 1, limit: 50, offset: 0 } }],
    ['GET', new RegExp(`/api/quote/${id}$`), detail],
    ['GET', /\/api\/customer\/\w+$/, { ...recorded.customer, status: 200 }],
  ];
}
const numberField = (host: Element) => host.querySelector<HTMLElement>('.ev-cmp-card [data-field="number"]');
const subtitle = (host: Element) => text(host.querySelector('.doc-head .doc-sub'));

describe('the record number, once', () => {
  it('stays in the form while it can be edited, and the side panel subtitle does not repeat it', async () => {
    const { host } = start(routes({ numberWritable: true }), `/app/quote?open=${id}`);
    await until(() => expect(text(host.querySelector('.ev-panel .doc-head h1'))).toBe('Spring quote'));
    await until(() => expect(numberField(host)).not.toBeNull());
    expect(numberField(host)!.hidden).toBe(false);
    expect(subtitle(host)).not.toContain('QT-2026-00001');
  });

  it('shows a number nothing lets this person edit in the subtitle only, not again in the form', async () => {
    const { host } = start(routes({ numberWritable: false }), `/app/quote?open=${id}`);
    await until(() => expect(subtitle(host)).toContain('QT-2026-00001'));
    expect(numberField(host)).toBeNull();
  });

  it('leaves a writable number out of the form while the record is locked, and the subtitle shows it', async () => {
    const { host } = start(routes({ numberWritable: true, locked: true }), `/app/quote?open=${id}`);
    await until(() => expect(subtitle(host)).toContain('QT-2026-00001'));
    expect(numberField(host)).toBeNull();
  });

  it('on the full page keeps an editable number in the form only, and the breadcrumb ends with it', async () => {
    const { host } = start(routes({ numberWritable: true }), `/app/quote/${id}`);
    await until(() => expect(text(host.querySelector('.doc-head h1'))).toBe('Spring quote'));
    await until(() => expect(numberField(host)).not.toBeNull());
    expect(text(host.querySelector('.doc-head'))).not.toContain('QT-2026-00001');
    stopAll();
    const locked = start(routes({ numberWritable: true, locked: true }), `/app/quote/${id}`);
    await until(() => expect(text(locked.host.querySelector('.doc-head h1'))).toBe('Spring quote'));
    expect(numberField(locked.host)).toBeNull();
  });
});
