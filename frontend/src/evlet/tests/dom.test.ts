import { afterEach, describe, expect, it, vi } from 'vitest';
import { batch, cell, describe as inspect, onCleanup } from '../reactive';
import { each, html, match, mount, portal, template, when } from '../dom';

let host: HTMLElement;
const disposers: Array<() => void> = [];
function render(view: () => Node) {
  host = document.createElement('div');
  document.body.append(host);
  disposers.push(mount(host, view));
  return host;
}
afterEach(() => { disposers.splice(0).forEach(dispose => dispose()); document.body.innerHTML = ''; });

// Count DOM changes under a node, synchronously.
function watch(target: Node) {
  const observer = new MutationObserver(() => {});
  observer.observe(target, { childList: true, subtree: true, characterData: true, attributes: true });
  return () => {
    const records = observer.takeRecords();
    observer.disconnect();
    return {
      added: records.reduce((sum, record) => sum + record.addedNodes.length, 0),
      removed: records.reduce((sum, record) => sum + record.removedNodes.length, 0),
      text: records.filter(record => record.type === 'characterData').length,
      attributes: records.filter(record => record.type === 'attributes').length,
      targets: records.map(record => record.target),
    };
  };
}

describe('templates', () => {
  it('parses a call site once and clones it per use', () => {
    const spy = vi.spyOn(document, 'createElement');
    const make = (name: string) => html`<p class="greeting">Hello ${name}</p>`;
    const first = make('Ana') as HTMLElement, second = make('Omar') as HTMLElement;
    const templates = spy.mock.calls.filter(([tag]) => tag === 'template').length;
    spy.mockRestore();
    expect(templates).toBe(1);
    expect(first.outerHTML).toBe('<p class="greeting">Hello Ana</p>');
    expect(second.textContent).toBe('Hello Omar');
  });

  it('never parses data as HTML', () => {
    const payload = '<img src=x onerror="alert(1)">';
    const node = html`<div title=${payload} data-x="${payload} tail">${payload}</div>` as HTMLElement;
    expect(node.querySelector('img')).toBeNull();
    expect(node.textContent).toBe(payload);
    expect(node.getAttribute('title')).toBe(payload);
    expect(node.getAttribute('data-x')).toBe(`${payload} tail`);
  });

  it('refuses script URLs, inline handler attributes and HTML properties', () => {
    const link = html`<a href=${'javascript:alert(1)'}>x</a>` as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('about:blank');
    expect(() => html`<a onclick=${'alert(1)'}>x</a>`).toThrow('on:click');
    expect(() => html`<div prop:innerHTML=${'<b>x</b>'}></div>`).toThrow('not allowed');
  });

  it('binds one effect per dynamic attribute, property, class, style and text', () => {
    const name = cell('Ana'), active = cell(false), width = cell('10px'), value = cell('a');
    const view = render(() => html`
      <section>
        <input prop:value=${value} aria-label="Name">
        <span class="badge ${() => (active.get() ? 'on' : 'off')}" class:active=${active} style:width=${width}>${name}</span>
      </section>`);
    const span = view.querySelector('span')!, input = view.querySelector('input')!;
    expect(span.className).toBe('badge off');
    expect(input.value).toBe('a');
    const done = watch(view);
    name.set('Omar');
    const changes = done();
    expect(changes).toMatchObject({ added: 0, removed: 0, text: 1, attributes: 0 });
    active.set(true);
    expect(span.className).toBe('badge on active');
    width.set('20px');
    expect(span.style.width).toBe('20px');
    value.set('b');
    expect(input.value).toBe('b');
  });

  it('keeps the written case of property names', () => {
    const node = html`<div prop:textContent=${'plain'}></div>` as HTMLElement;
    expect(node.textContent).toBe('plain');
    expect(node.attributes.length).toBe(0);
  });

  it('attaches events and removes them when the scope ends', () => {
    const clicks = vi.fn();
    const view = render(() => html`<button on:click=${clicks}>Save</button>`);
    const button = view.querySelector('button')!;
    button.click();
    disposers.pop()!();
    button.click();
    expect(clicks).toHaveBeenCalledTimes(1);
  });

  it('supports several roots, static attributes, booleans and refs', () => {
    let seen: Element | null = null;
    const node = html`<label for="a">A</label><input id="a" disabled ref=${(el: Element) => { seen = el; }}>`;
    expect(node).toBeInstanceOf(DocumentFragment);
    const input = (node as DocumentFragment).querySelector('input')!;
    expect(input.disabled).toBe(true);
    expect(seen).toBe(input);
  });

  it('removes an attribute set to null or false', () => {
    const title = cell<string | null>('x');
    const view = render(() => html`<b title=${title}></b>`);
    title.set(null);
    expect(view.querySelector('b')!.hasAttribute('title')).toBe(false);
  });

  it('static template() clones a parsed fragment', () => {
    const row = template('<tr><td class="name"></td></tr>');
    const a = row() as HTMLElement, b = row() as HTMLElement;
    expect(a).not.toBe(b);
    expect(a.outerHTML).toBe('<tr><td class="name"></td></tr>');
  });

  it('a reactive child can switch between text and nodes', () => {
    const value = cell<unknown>('text');
    const view = render(() => html`<div>${() => value.get()}</div>`);
    const div = view.querySelector('div')!;
    expect(div.textContent).toBe('text');
    value.set(html`<em>node</em>`);
    expect(div.querySelector('em')!.textContent).toBe('node');
    value.set(['a', 'b']);
    expect(div.textContent).toBe('ab');
    value.set(null);
    expect(div.textContent).toBe('');
  });
});

describe('control flow', () => {
  it('when switches branches only when truthiness changes and disposes the old branch', () => {
    const count = cell(1);
    const created = vi.fn();
    const view = render(() => html`<div>${when(() => count.get() > 0, () => { created(); return html`<b>${count}</b>`; }, () => 'none')}</div>`);
    expect(view.textContent).toBe('1');
    count.set(2);
    expect(view.textContent).toBe('2');
    expect(created).toHaveBeenCalledTimes(1);
    count.set(0);
    expect(view.textContent).toBe('none');
    expect(inspect(count).observers.length).toBe(1); // the old <b> binding is gone; only the condition remains
  });

  it('match renders the case for a value and falls back', () => {
    const stage = cell<'draft' | 'issued' | 'void'>('draft');
    const view = render(() => html`<p>${match(stage, { draft: () => 'Draft', issued: () => html`<strong>Issued</strong>` }, () => 'Other')}</p>`);
    expect(view.textContent).toBe('Draft');
    stage.set('issued');
    expect(view.querySelector('strong')).not.toBeNull();
    stage.set('void');
    expect(view.textContent).toBe('Other');
  });

  it('portal renders elsewhere and removes its content with the scope', () => {
    const target = document.createElement('div');
    document.body.append(target);
    const open = cell(true);
    render(() => html`<main>${when(open, () => portal(target, () => html`<dialog open>Hi</dialog>`))}</main>`);
    expect(target.querySelector('dialog')).not.toBeNull();
    open.set(false);
    expect(target.querySelector('dialog')).toBeNull();
  });
});

describe('keyed each', () => {
  type Item = { id: string; label: string };
  const items = (...ids: string[]) => ids.map(id => ({ id, label: id.toUpperCase() }));
  function list(source: ReturnType<typeof cell<Item[]>>, made = vi.fn()) {
    const view = render(() => html`<ul>${each(source, item => item.id, item => { made(); return html`<li>${() => item().label}</li>`; })}</ul>`);
    return { ul: view.querySelector('ul')!, made };
  }
  const labels = (ul: Element) => [...ul.querySelectorAll('li')].map(li => li.textContent);

  it('creates each row once and reuses it by key', () => {
    const source = cell(items('a', 'b', 'c'));
    const { ul, made } = list(source);
    const before = [...ul.children];
    source.set(items('a', 'b', 'c'));
    expect([...ul.children]).toEqual(before);
    expect(made).toHaveBeenCalledTimes(3);
  });

  it('updates a row in place when its item changes', () => {
    const source = cell(items('a', 'b'));
    const { ul } = list(source);
    const done = watch(ul);
    source.set([{ id: 'a', label: 'Changed' }, { id: 'b', label: 'B' }]);
    expect(done()).toMatchObject({ added: 0, removed: 0, text: 1 });
    expect(labels(ul)).toEqual(['Changed', 'B']);
  });

  it('swapping two neighbours moves one row', () => {
    const source = cell(items('a', 'b', 'c', 'd', 'e'));
    const { ul } = list(source);
    const done = watch(ul);
    source.set(items('a', 'c', 'b', 'd', 'e'));
    const changes = done();
    expect(labels(ul)).toEqual(['A', 'C', 'B', 'D', 'E']);
    expect(changes.added).toBe(1);
  });

  it('moving the last row to the front is one move, and reversing five rows is four', () => {
    const source = cell(items('a', 'b', 'c', 'd', 'e'));
    const { ul } = list(source);
    let done = watch(ul);
    source.set(items('e', 'a', 'b', 'c', 'd'));
    expect(done().added).toBe(1);
    done = watch(ul);
    source.set(items('d', 'c', 'b', 'a', 'e'));
    expect(labels(ul)).toEqual(['D', 'C', 'B', 'A', 'E']);
    expect(done().added).toBe(4); // only one row can keep its place
  });

  it('inserts and removes only the changed rows', () => {
    const source = cell(items('a', 'b', 'c'));
    const { ul, made } = list(source);
    const done = watch(ul);
    source.set(items('a', 'x', 'c', 'y'));
    const changes = done();
    expect(labels(ul)).toEqual(['A', 'X', 'C', 'Y']);
    expect(changes).toMatchObject({ added: 2, removed: 1 });
    expect(made).toHaveBeenCalledTimes(5);
  });

  it('runs a removed row cleanups and stops its bindings', () => {
    const label = cell('shared');
    const source = cell(['a', 'b']);
    const cleaned: string[] = [];
    render(() => html`<ul>${each(source, id => id, item => {
      const id = item();
      onCleanup(() => cleaned.push(id));
      return html`<li>${label}</li>`;
    })}</ul>`);
    expect(inspect(label).observers.length).toBe(2);
    source.set(['a']);
    expect(inspect(label).observers.length).toBe(1);
    expect(cleaned).toEqual(['b']);
  });

  it('shows a fallback while empty', () => {
    const source = cell<Item[]>([]);
    const view = render(() => html`<ul>${each(source, item => item.id, item => html`<li>${() => item().label}</li>`, { fallback: () => html`<li class="empty">No lines yet</li>` })}</ul>`);
    expect(view.querySelector('.empty')).not.toBeNull();
    source.set(items('a'));
    expect(view.querySelector('.empty')).toBeNull();
    source.set([]);
    expect(view.querySelector('.empty')).not.toBeNull();
  });

  it('moves rows that render several nodes as a unit', () => {
    const source = cell(['a', 'b']);
    const view = render(() => html`<div>${each(source, id => id, item => html`<dt>${item}</dt><dd>${() => item() + '!'}</dd>`)}</div>`);
    source.set(['b', 'a']);
    expect(view.querySelector('div')!.textContent).toBe('bb!aa!');
  });

  it('gives rows a reactive index', () => {
    const source = cell(['a', 'b', 'c']);
    const view = render(() => html`<ol>${each(source, id => id, (item, index) => html`<li>${() => index() + 1}. ${item}</li>`)}</ol>`);
    source.set(['c', 'a', 'b']);
    expect([...view.querySelectorAll('li')].map(li => li.textContent)).toEqual(['1. c', '2. a', '3. b']);
  });

  it('rejects duplicate keys', () => {
    const source = cell(['a']);
    render(() => html`<ul>${each(source, id => id, item => html`<li>${item}</li>`)}</ul>`);
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    source.set(['a', 'a']);
    expect(errors.mock.calls[0][1]).toBeInstanceOf(Error);
    errors.mockRestore();
  });

  it('a 1,000-row list edit touches only the bound text node', () => {
    const rows = Array.from({ length: 1000 }, (_, index) => ({ id: index, qty: cell(String(index)) }));
    const total = cell(0);
    const view = render(() => html`<table><tbody>${each(rows, row => row.id, row => html`<tr><td>${row().qty}</td></tr>`)}</tbody><tfoot><tr><td>${total}</td></tr></tfoot></table>`);
    expect(view.querySelectorAll('tbody tr').length).toBe(1000);
    const done = watch(view);
    batch(() => { rows[500].qty.set('x'); total.set(1); });
    const changes = done();
    expect(changes).toMatchObject({ added: 0, removed: 0, text: 2, attributes: 0 });
  });
});
