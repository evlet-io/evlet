// Regression tests for review findings on the DOM layer.
import { afterEach, describe, expect, it } from 'vitest';
import { cell, describe as info, scope } from '../reactive';
import { each, html, match, mount, portal, safeUrl, when } from '../dom';

const disposers: Array<() => void> = [];
afterEach(() => { disposers.splice(0).forEach(dispose => dispose()); });
function render(view: () => Node) {
  const root = document.createElement('div');
  disposers.push(mount(root, view));
  return root;
}

describe('URLs and HTML sinks', () => {
  it.each([
    'javascript:alert(1)', ' JaVaScRiPt:alert(1)', 'data:text/html,<script>alert(1)</script>', 'vbscript:x',
    'java\tscript:alert(1)', 'java\nscript:alert(1)', '\x01javascript:alert(1)', '\u0000javascript:alert(1)', 'javascript\r:alert(1)',
  ])('href %j is refused', url => {
    const link = render(() => html`<a href=${url}>x</a>`).querySelector('a')!;
    expect(link.getAttribute('href')).toBe('about:blank');
  });

  it.each(['https://evlet.io/x', 'http://a.b', 'mailto:a@b.ae', 'tel:+97141234567', '/m/invoice/1', 'record?id=2', '#lines', '//cdn.example/x.png'])
  ('href %j is allowed', url => {
    expect(safeUrl(url)).toBe(url);
  });

  it('checks prop: URL sinks too', () => {
    const root = render(() => html`<a prop:href=${'javascript:alert(1)'}>x</a><img prop:src=${'\x01javascript:1'}><form prop:action=${'javascript:1'}></form>`);
    expect(root.querySelector('a')!.getAttribute('href')).toBe('about:blank');
    expect(root.querySelector('img')!.getAttribute('src')).toBe('about:blank');
    expect(root.querySelector('form')!.getAttribute('action')).toBe('about:blank');
  });

  it('checks every URL-bearing attribute, whatever its case', () => {
    const root = render(() => html`
      <button FORMACTION=${'javascript:1'}>x</button>
      <svg><a xlink:href=${'javascript:alert(1)'}><text>x</text></a></svg>
      <video poster=${'javascript:1'}></video><object data=${'javascript:1'}></object>
      <a ping=${'https://ok.example javascript:1'}>p</a>`);
    expect(root.querySelector('button')!.getAttribute('formaction')).toBe('about:blank');
    expect(root.querySelector('svg a')!.getAttribute('xlink:href')).toBe('about:blank');
    expect(root.querySelector('video')!.getAttribute('poster')).toBe('about:blank');
    expect(root.querySelector('object')!.getAttribute('data')).toBe('about:blank');
    expect(root.querySelectorAll('a')[1].getAttribute('ping')).toBe('https://ok.example');
  });

  it('keeps only safe srcset candidates', () => {
    const img = render(() => html`<img srcset=${'/a.png 1x, javascript:alert(1) 2x, https://c.example/b.png 3x'}>`).querySelector('img')!;
    expect(img.getAttribute('srcset')).toBe('/a.png 1x, https://c.example/b.png 3x');
  });

  it('refuses srcdoc, meta content, SVG animation values and style text from values', () => {
    expect(() => html`<iframe srcdoc=${'<script>alert(1)</script>'}></iframe>`).toThrow('srcdoc');
    expect(() => html`<meta http-equiv="refresh" content=${'0;url=javascript:alert(1)'}>`).toThrow('meta');
    expect(() => html`<svg><animate attributeName="href" values=${'javascript:alert(1)'}/></svg>`).toThrow('animate');
    expect(() => html`<svg><set attributeName="href" to=${'javascript:1'}/></svg>`).toThrow('set');
    expect(() => html`<div style=${'position:fixed'}>x</div>`).toThrow('style:');
    expect(() => html`<div style="color: ${'red'}">x</div>`).toThrow('style:');
    expect(() => html`<a prop:innerhtml=${'<b>'}>x</a>`).toThrow('not allowed');
  });

  it('keeps tag and attribute names out of holes', () => {
    expect(() => html`<a ${'onclick'}="x">x</a>`).toThrow();
    expect(() => html`<${'script'}>x</a>`).toThrow();
  });
});

describe('template parsing', () => {
  it('refuses holes inside raw-text elements', () => {
    expect(() => html`<textarea>${'hello'}</textarea>`).toThrow('prop:value');
    expect(() => html`<title>${'t'}</title>`).toThrow('<title>');
    expect(() => html`<style>${'body{}'}</style>`).toThrow('<style>');
    expect(() => html`<script>${'1'}</script>`).toThrow('<script>');
    expect(() => html`<div><!-- ${'x'} --></div>`).toThrow();
  });

  it('allows a value binding on a textarea and holes after a raw-text element', () => {
    const root = render(() => html`<textarea prop:value=${'hello'}></textarea><p>${'after'}</p>`);
    expect((root.querySelector('textarea') as HTMLTextAreaElement).value).toBe('hello');
    expect(root.querySelector('p')!.textContent).toBe('after');
  });

  it('an unquoted value before /> does not take the slash', () => {
    const input = html`<input value=${'v'}/>` as HTMLInputElement;
    expect(input.getAttribute('value')).toBe('v');
  });
});

describe('ranges of dynamic content', () => {
  it('a row that renders a when() keeps its later content on move and removal', () => {
    const list = cell(['a', 'b']), open = cell(false);
    const div = render(() => html`<div>${each(list, x => x, x => when(open, () => html`<span>${x()}</span>`))}</div>`).querySelector('div')!;
    open.set(true);
    expect(div.textContent).toBe('ab');
    list.set(['b', 'a']);
    expect(div.textContent).toBe('ba');
    list.set(['a']);
    expect(div.textContent).toBe('a');
    list.set([]);
    expect(div.querySelectorAll('span').length).toBe(0);
    list.set(['b', 'a']);
    expect(div.textContent).toBe('ba');
  });

  it('a row that renders an accessor updates', () => {
    const list = cell([{ id: 1, name: 'x' }]);
    const root = render(() => html`<div>${each(list, x => x.id, x => () => x().name)}</div>`);
    list.set([{ id: 1, name: 'y' }]);
    expect(root.textContent).toBe('y');
  });

  it('switching an outer when removes content an inner when added later', () => {
    const a = cell(true), b = cell(false);
    const root = render(() => html`<div>${when(a, () => when(b, () => html`<p>inner</p>`), () => 'off')}</div>`);
    b.set(true);
    expect(root.textContent).toBe('inner');
    a.set(false);
    expect(root.textContent).toBe('off');
    expect(root.querySelector('p')).toBeNull();
  });

  it('a branch whose child list grew is removed entirely', () => {
    const a = cell(true), items = cell(['x']);
    const root = render(() => html`<div>${when(a, () => html`<b>${() => items.get().map(s => html`<i>${s}</i>`)}</b>${() => items.get().map(s => html`<i>${s}</i>`)}`)}</div>`);
    items.set(['x', 'y', 'z']);
    expect(root.querySelectorAll('i').length).toBe(6);
    a.set(false);
    expect(root.querySelectorAll('i').length).toBe(0);
  });

  it('a dynamic child that switches from a region keeps no orphans', () => {
    const show = cell(true), mode = cell<'region' | 'text'>('region');
    const root = render(() => html`<div>${() => mode.get() === 'region' ? when(show, () => html`<em>yes</em>`) : 'plain'}</div>`);
    show.set(false); show.set(true);
    mode.set('text');
    expect(root.querySelector('div')!.textContent).toBe('plain');
    expect(root.querySelector('em')).toBeNull();
  });

  it('match switching many times leaves only its markers and content', () => {
    const value = cell<'a' | 'b' | 'c'>('a');
    const root = render(() => html`<div>${match(value, { a: () => 'A', b: () => html`<em>B</em>` }, () => 'F')}</div>`);
    for (let index = 0; index < 1000; index++) value.set((['a', 'b', 'c'] as const)[index % 3]);
    expect(root.textContent).toBe('A');
    expect([...root.querySelector('div')!.childNodes].filter(node => node.nodeType !== Node.COMMENT_NODE).length).toBe(1);
  });

  it('portal content is removed with its scope, also when it grew', () => {
    const target = document.createElement('div');
    const show = cell(true), lines = cell(['a']);
    render(() => html`<div>${when(show, () => portal(target, () => html`<dialog>${() => lines.get().map(line => html`<p>${line}</p>`)}</dialog>`))}</div>`);
    lines.set(['a', 'b']);
    show.set(false);
    expect(target.childNodes.length).toBe(0);
  });
});

describe('each', () => {
  it('a duplicate key disposes the rows it created in that pass and keeps the DOM as it was', () => {
    const list = cell([1, 2]);
    const label = cell('L');
    const errors: unknown[] = [];
    const root = document.createElement('div');
    disposers.push(scope(() => { mount(root, () => html`<ul>${each(list, x => x, x => html`<li>${label}${x()}</li>`)}</ul>`); }, { onError: error => errors.push(error) }));
    list.set([3, 4, 3]);
    expect(errors).toHaveLength(1);
    expect([...root.querySelectorAll('li')].map(li => li.textContent)).toEqual(['L1', 'L2']);
    expect(info(label).observers).toHaveLength(2);
    list.set([3, 1]);
    expect([...root.querySelectorAll('li')].map(li => li.textContent)).toEqual(['L3', 'L1']);
  });

  it('matches a reference over random edits and reuses nodes', () => {
    let seed = 7;
    const random = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    const list = cell<number[]>([]);
    const ul = render(() => html`<ul>${each(list, x => x, x => html`<li>${() => x()}</li>`)}</ul>`).querySelector('ul')!;
    const nodes = new Map<number, Node>();
    let next = 1;
    for (let step = 0; step < 400; step++) {
      let items = list.peek().slice();
      const op = random();
      if (op < 0.3) items.splice(Math.floor(random() * (items.length + 1)), 0, next++);
      else if (op < 0.5 && items.length) items.splice(Math.floor(random() * items.length), 1);
      else if (op < 0.7) for (let i = items.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [items[i], items[j]] = [items[j], items[i]]; }
      else if (op < 0.8) items = [];
      else for (let k = Math.floor(random() * 5); k > 0; k--) items.push(next++);
      list.set(items);
      expect([...ul.querySelectorAll('li')].map(li => Number(li.textContent))).toEqual(items);
      for (const li of ul.querySelectorAll('li')) {
        const key = Number(li.textContent);
        if (nodes.has(key)) expect(nodes.get(key)).toBe(li);
        nodes.set(key, li);
      }
    }
  });
});
