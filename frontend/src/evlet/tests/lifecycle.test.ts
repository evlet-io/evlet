// The Lifecycle Playground's Order (scripts/generate_lifecycle_fixtures.py): confirmed as a release, reopened with a
// remark, cancelled as a final step, all through the state manager's move route.
import { afterEach, describe, expect, it } from 'vitest';
import f from './lifecycle-fixture.json';
import type { Recorded } from './server';
import { byText, click, requests, start, stopAll, text, typeInto, until, type Route } from './screens-helpers';

afterEach(stopAll);
const routes = (record: Recorded = f.draft): Route[] => [
  ['GET', /\/api\/auth\/session$/, f.session], ['GET', /\/api\/schema$/, f.schema],
  ['GET', /\/api\/order\/0000000000001$/, record], ['GET', /\/api\/order\/0000000000001\/journal/, f.history],
];
const banner = (host: Element) => text(host.querySelector('.inv-banner > div'));

describe('document lifecycle contract', () => {
  it('confirms by the primary rule with the current ETag, then offers the rules out of Confirmed', async () => {
    const { host, sent } = start([...routes(), ['POST', /\/api\/order\/0000000000001\/moves\/draft_to_confirmed$/, f.confirmed]], '/app/order/0000000000001');
    await until(() => expect(byText(host, '.doc-head button', 'Confirm')).not.toBeNull());
    expect(byText(host, '.doc-head button', 'Confirm')!.classList.contains('primary')).toBe(true);
    click(byText(host, '.doc-head button', 'Confirm'));
    await until(() => expect(banner(host)).toBe('Confirmed and locked. Only remarks can change.'));
    expect(requests(sent, 'POST', /\/moves\//)[0].headers['if-match']).toBe(f.draft.etag);
    expect(byText(host, '.doc-head button', 'Reopen')).not.toBeNull();
    expect(byText(host, '.doc-head button', 'Confirm')).toBeNull();
    expect(host.querySelector('.ev-more-row[data-section="Revisions"]')).toBeNull();
  });

  it('reopens with the remark the rule requires', async () => {
    const { host, sent } = start([...routes(f.confirmed), ['POST', /\/api\/order\/0000000000001\/moves\/confirmed_to_draft$/, f.reopened]], '/app/order/0000000000001');
    await until(() => expect(byText(host, '.doc-head button', 'Reopen')).not.toBeNull());
    click(byText(host, '.doc-head button', 'Reopen'));
    const dialog = document.querySelector('.modal')!;
    expect(text(dialog.querySelector('.fld label')).replace('*', '').trim()).toBe('Remark');
    click(byText(dialog, '.modal-f button', 'Reopen'));
    expect(text(dialog.querySelector('.err'))).toBe('Remark is required');
    expect(requests(sent, 'POST', /\/moves\//)).toHaveLength(0);
    typeInto(dialog.querySelector('textarea')!, 'Customer and lines changed');
    click(byText(dialog, '.modal-f button', 'Reopen'));
    await until(() => expect(banner(host)).toBe('Draft. Everything can change until it is confirmed.'));
    expect(requests(sent, 'POST', /\/moves\//)[0].body).toEqual({ remark: 'Customer and lines changed' });
  });
});
