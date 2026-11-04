import { afterEach, describe, expect, it } from 'vitest';
import f from './lifecycle-fixture.json';
import type { Recorded } from './server';
import { revisionChanges } from '../app/revisions';
import { byText, click, requests, start, stopAll, text, until, type Route } from './screens-helpers';

afterEach(stopAll);
const routes = (record: Recorded = f.draft): Route[] => [
  ['GET', /\/api\/auth\/session$/, f.session], ['GET', /\/api\/schema$/, f.schema],
  ['GET', /\/api\/order\/0000000000001$/, record], ['GET', /\/api\/order\/0000000000001\/journal/, f.history],
  ['GET', /\/api\/order\/0000000000001\/revisions/, f.revisions], ['GET', /\/api\/order\/0000000000001\/snapshot/, f.snapshot2],
  ['GET', /\/api\/order\/0000000000001\/diff/, f.diff],
];

describe('document lifecycle contract', () => {
  it('confirms with the current ETag and updates state, revision and permitted actions', async () => {
    const { host, sent } = start([...routes(), ['POST', /\/api\/order\/0000000000001\/confirm$/, f.confirmed]], '/app/order/0000000000001?tab=details');
    await until(() => expect(byText(host, '.doc-head button', 'Confirm')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Confirm'));
    await until(() => expect(text(host)).toContain('Confirmed · Revision 1'));
    expect(requests(sent, 'POST', /\/confirm$/)[0].headers['if-match']).toBe(f.draft.etag);
    expect(byText(host, '.doc-head button', 'Cancel')).not.toBeNull();
    expect(byText(host, '.doc-head button', 'Confirm')).toBeNull();
    expect(byText(host, '[role="tab"]', 'Revisions')).not.toBeNull();
  });

  it('shows immutable snapshots and parent/child additions, removals and edits', async () => {
    const { host, sent } = start(routes(f.reconfirmed), '/app/order/0000000000001?tab=revisions');
    await until(() => expect(byText(host, 'button', 'Compare revisions')).not.toBeNull());
    await until(() => expect(byText(host, 'button', 'Compare revisions')?.hasAttribute('disabled')).toBe(false));
    click(byText(host, 'button', 'Compare revisions'));
    await until(() => expect(host.querySelector('[aria-label="Revision comparison"]')).not.toBeNull());
    const comparison = text(host.querySelector('[aria-label="Revision comparison"]'));
    expect(comparison).toContain('customer'); expect(comparison).toContain('added'); expect(comparison).toContain('removed');
    expect(requests(sent, 'GET', /\/diff/)[0].url).toContain('from=1&to=2');
    click(byText(host, 'button', 'View snapshot'));
    await until(() => expect(host.querySelector('[aria-label="Immutable snapshot"]')).not.toBeNull());
    expect(text(host.querySelector('[aria-label="Immutable snapshot"]'))).toContain('ORD-001');
    expect(host.querySelectorAll('[aria-label="Immutable snapshot"] input')).toHaveLength(0);
  });

  it('compares nested child edits and row positions using stable row identities', () => {
    const changes = revisionChanges({ rows: { lines: { updated: [{ id: 3, rows: { notes: { updated: [{ id: 5, fields: { text: { old: 'A', new: 'B' } } }] } } }], positions: [{ id: 3, old: 1, new: 0 }] } } });
    expect(changes).toEqual([
      { path: 'lines #3.notes #5.text', operation: 'modified', old: 'A', new: 'B' },
      { path: 'lines #3', operation: 'reordered', old: 2, new: 1 },
    ]);
  });

  it('shows nested child values when their containing row was added or removed', () => {
    const row = { id: 3, title: 'Line', children: { notes: [{ id: 5, text: 'Nested value' }] } };
    const added = revisionChanges({ rows: { lines: { added: [row] } } });
    const removed = revisionChanges({ rows: { lines: { removed: [row] } } });
    expect(added.find(item => item.path === 'lines #3.notes #5')?.new).toEqual({ id: 5, text: 'Nested value' });
    expect(removed.find(item => item.path === 'lines #3.notes #5')?.old).toEqual({ id: 5, text: 'Nested value' });
  });

});
