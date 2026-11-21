// A model script on the record page (Meta.script): loaded only for a model that declares one, then its custom
// block, field look and on_open run over the recorded Site responses (layout_* in screens-fixture.json).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { recorded, screen, start, stopAll, until, type Route } from './screens-helpers';
import { copy, type Recorded, type Sent } from './server';
import { evlet, setScriptImporter } from '../model/script';

afterEach(() => { vi.restoreAllMocks(); stopAll(); });

const site = screen.layout_site_detail.body.id as string;
const URL_ = '/assets/scripts/0123456789abcdef/site.js';
const opened: unknown[] = [];

class Site extends evlet.Model {
  opened() { opened.push([this.model, this.is_new]); }
  region_look() { return { tone: 'danger' }; }
  map(el: HTMLElement) { el.textContent = `map of ${this.field('region').value}`; }
}

function schema(scripted: boolean): Recorded {
  const body = copy(screen.layout_schema.body);
  const model = body.models.find((item: { name: string }) => item.name === 'site');
  if (scripted) {
    model.script = { url: URL_, on_open: 'opened' };
    model.fields.find((item: { name: string }) => item.name === 'region').script = { look: 'region_look' };
    model.record_layout.blocks.unshift({ kind: 'custom', name: 'Map', label: 'Map', custom: 'map', main: true });
  }
  return { ...screen.layout_schema, body };
}
const routes = (scripted: boolean): Route[] => [
  ['GET', /\/api\/auth\/session$/, recorded.session],
  ['GET', /\/api\/schema$/, schema(scripted)],
  ['GET', /\/api\/job\/aggregate\?/, (sent: Sent) => new URL(sent.url, 'http://127.0.0.1').searchParams.get('group_by') ? screen.layout_by_status : screen.layout_totals],
  ['GET', /\/api\/job\?/, screen.layout_open_jobs],
  ['GET', new RegExp(`/api/site/${site}$`), screen.layout_site_detail],
];

describe('a model script on the record page', () => {
  it('is never requested for a model without one', async () => {
    const load = vi.fn(async () => ({ default: Site }));
    setScriptImporter(load);
    const { host } = start(routes(false), `/app/site/${site}`);
    await until(() => expect(host.querySelector('.ev-cmp-main [data-field="region"]')).not.toBeNull());
    expect(load).not.toHaveBeenCalled();
    expect(host.querySelector('.ev-script')).toBeNull();
  });

  it('loads once, runs on_open, draws its custom block in a shadow root and sets the field look', async () => {
    opened.length = 0;
    const load = vi.fn(async () => ({ default: Site }));
    setScriptImporter(load);
    const { host } = start(routes(true), `/app/site/${site}`);
    await until(() => expect(host.querySelector('[data-block="Map"] .ev-script')?.shadowRoot?.textContent).toBe(`map of ${screen.layout_site_detail.body.region}`));
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith(URL_);
    expect(opened).toEqual([['site', false]]);
    await until(() => expect(host.querySelector('[data-field="region"]')?.getAttribute('data-look')).toBe('danger'));
  });
});
