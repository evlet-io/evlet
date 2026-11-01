import { describe as group, expect, it, vi } from 'vitest';
import { batch, cell, derive, describe, effect, getOwner, onCleanup, runWithOwner, scope, setErrorHandler, untrack } from '../reactive';

group('cells and derives', () => {
  it('derives are lazy and cached', () => {
    const a = cell(1);
    const run = vi.fn(() => a.get() * 2);
    const doubled = derive(run);
    expect(run).not.toHaveBeenCalled();
    expect(doubled.get()).toBe(2);
    expect(doubled.get()).toBe(2);
    expect(run).toHaveBeenCalledTimes(1);
    a.set(5);
    expect(run).toHaveBeenCalledTimes(1);
    expect(doubled.get()).toBe(10);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('a diamond recomputes each node once and effects never see a mixed state', () => {
    const a = cell(1, { name: 'a' });
    const b = derive(() => a.get() * 2, { name: 'b' });
    const c = derive(() => a.get() + 10, { name: 'c' });
    const dRuns = vi.fn(() => b.get() + c.get());
    const d = derive(dRuns, { name: 'd' });
    const seen: Array<[number, number, number]> = [];
    effect(() => { seen.push([b.get(), c.get(), d.get()]); });
    a.set(2);
    a.set(3);
    expect(seen).toEqual([[2, 11, 13], [4, 12, 16], [6, 13, 19]]);
    expect(dRuns).toHaveBeenCalledTimes(3);
  });

  it('a change that does not reach a derive value stops propagation (equality cut-off)', () => {
    const a = cell(3);
    const parity = derive(() => a.get() % 2);
    const downstream = vi.fn(() => parity.get() === 1 ? 'odd' : 'even');
    const label = derive(downstream);
    const log = vi.fn();
    effect(() => log(label.get()));
    a.set(5);
    a.set(7);
    expect(downstream).toHaveBeenCalledTimes(1);
    expect(log).toHaveBeenCalledTimes(1);
    a.set(8);
    expect(log).toHaveBeenLastCalledWith('even');
  });

  it('writing an equal value is a no-op, and equals:false always notifies', () => {
    const plain = cell('x');
    const always = cell({ n: 1 }, { equals: false });
    const runs = vi.fn();
    effect(() => { plain.get(); always.get(); runs(); });
    plain.set('x');
    expect(runs).toHaveBeenCalledTimes(1);
    always.set(always.peek());
    expect(runs).toHaveBeenCalledTimes(2);
  });

  it('custom equality skips writes the comparer treats as the same', () => {
    const money = cell('1.50', { equals: (a, b) => Number(a) === Number(b) });
    const runs = vi.fn();
    effect(() => { money.get(); runs(); });
    money.set('1.5');
    expect(runs).toHaveBeenCalledTimes(1);
    expect(money.peek()).toBe('1.50');
  });

  it('dynamic dependencies: a branch not taken is not tracked', () => {
    const useA = cell(true), a = cell('a'), b = cell('b');
    const runs = vi.fn(() => useA.get() ? a.get() : b.get());
    const pick = derive(runs);
    effect(() => { pick.get(); });
    b.set('B');
    expect(runs).toHaveBeenCalledTimes(1);
    useA.set(false);
    expect(pick.get()).toBe('B');
    a.set('A');
    expect(runs).toHaveBeenCalledTimes(2);
    expect(describe(pick).sources).toEqual([useA.name, b.name]);
  });

  it('untrack and peek read without subscribing', () => {
    const a = cell(1), b = cell(1);
    const runs = vi.fn();
    effect(() => { a.get(); untrack(() => b.get()); b.peek(); runs(); });
    b.set(2);
    expect(runs).toHaveBeenCalledTimes(1);
    a.set(2);
    expect(runs).toHaveBeenCalledTimes(2);
  });

  it('a derive that throws rethrows to its readers and recovers', () => {
    const a = cell(0);
    const checked = derive(() => { if (a.get() < 0) throw new Error('negative'); return a.get(); });
    expect(checked.get()).toBe(0);
    a.set(-1);
    expect(() => checked.get()).toThrow('negative');
    a.set(4);
    expect(checked.get()).toBe(4);
  });

  it('a derive reading itself reports a cycle', () => {
    let self: any;
    self = derive(() => self.get() + 1, { name: 'loop' });
    expect(() => self.get()).toThrow('loop reads itself');
  });

  it('names nodes for debugging', () => {
    const net = cell('10.00', { name: 'record.lines[3].net' });
    const total = derive(() => net.get(), { name: 'record.total' });
    effect(() => { total.get(); }, { name: 'render total' });
    expect(describe(total)).toEqual({ name: 'record.total', kind: 'derive', value: '10.00', sources: ['record.lines[3].net'], observers: ['render total'] });
    expect(describe(net).observers).toEqual(['record.total']);
  });

  it('refuses writes during a derive in development', () => {
    const a = cell(1, { name: 'a' });
    const bad = derive(() => { a.set(2); return 1; }, { name: 'bad' });
    expect(() => bad.get()).toThrow('Cannot write a while computing bad');
  });
});

group('effects and batching', () => {
  it('batch runs each effect once with the final values', () => {
    const first = cell('Ana'), last = cell('Diaz');
    const seen: string[] = [];
    effect(() => { seen.push(`${first.get()} ${last.get()}`); });
    batch(() => { first.set('Omar'); last.set('Saleh'); });
    expect(seen).toEqual(['Ana Diaz', 'Omar Saleh']);
  });

  it('nested batches flush at the outermost end and return values', () => {
    const a = cell(0);
    const seen: number[] = [];
    effect(() => { seen.push(a.get()); });
    const result = batch(() => { a.set(1); batch(() => a.set(2)); expect(seen).toEqual([0]); return 'done'; });
    expect(result).toBe('done');
    expect(seen).toEqual([0, 2]);
  });

  it('an effect may write cells; dependants run in the same flush', () => {
    const source = cell(1), mirror = cell(0);
    const seen: number[] = [];
    effect(() => mirror.set(source.get() * 10));
    effect(() => { seen.push(mirror.get()); });
    source.set(2);
    expect(seen).toEqual([10, 20]);
  });

  it('effects that keep re-triggering each other are stopped with a clear error', () => {
    const ping = cell(0), pong = cell(0);
    effect(() => pong.set(ping.get() + 1), { name: 'ping' });
    expect(() => effect(() => ping.set(pong.get() + 1), { name: 'pong' })).toThrow('did not settle');
  });

  it('an effect writing a cell it reads does not re-trigger itself', () => {
    const counter = cell(0);
    const runs = vi.fn();
    effect(() => { runs(); counter.set(counter.get() + 1); });
    expect(counter.peek()).toBe(1);
    counter.set(10);
    expect(counter.peek()).toBe(11);
    expect(runs).toHaveBeenCalledTimes(2);
  });

  it('one throwing effect does not stop the others', () => {
    const a = cell(1);
    const errors: string[] = [];
    const restore = setErrorHandler((error, source) => errors.push(`${source}: ${(error as Error).message}`));
    const seen: number[] = [];
    effect(() => { if (a.get() === 2) throw new Error('boom'); }, { name: 'fragile' });
    effect(() => { seen.push(a.get()); });
    a.set(2);
    a.set(3);
    setErrorHandler(restore);
    expect(seen).toEqual([1, 2, 3]);
    expect(errors).toEqual(['fragile: boom']);
  });

  it('a scope onError handler catches its effects errors before the global handler', () => {
    const a = cell(0);
    const caught: string[] = [];
    const global = vi.fn();
    const restore = setErrorHandler(global);
    scope(() => { effect(() => { if (a.get()) throw new Error('inside'); }); }, { onError: error => caught.push((error as Error).message) });
    a.set(1);
    setErrorHandler(restore);
    expect(caught).toEqual(['inside']);
    expect(global).not.toHaveBeenCalled();
  });

  it('an effect returning a function uses it as cleanup', () => {
    const a = cell(1);
    const log: string[] = [];
    effect(() => { const value = a.get(); log.push(`run ${value}`); return () => log.push(`clean ${value}`); });
    a.set(2);
    expect(log).toEqual(['run 1', 'clean 1', 'run 2']);
  });

  it('effects created inside an effect are disposed when the parent re-runs', () => {
    const outer = cell(1), inner = cell(1);
    const innerRuns = vi.fn();
    effect(() => {
      outer.get();
      effect(() => { inner.get(); innerRuns(); });
    });
    expect(innerRuns).toHaveBeenCalledTimes(1);
    outer.set(2);
    expect(innerRuns).toHaveBeenCalledTimes(2);
    inner.set(2);
    // Only the inner effect of the second parent run remains.
    expect(innerRuns).toHaveBeenCalledTimes(3);
  });

  it('a parent effect runs before its child when both are stale, so the old child does not run', () => {
    const shared = cell(1);
    const log: string[] = [];
    effect(() => {
      const value = shared.get();
      log.push(`parent ${value}`);
      effect(() => { log.push(`child of ${value} sees ${shared.get()}`); });
    });
    log.length = 0;
    shared.set(2);
    expect(log).toEqual(['parent 2', 'child of 2 sees 2']);
  });
});

group('scopes and cleanup', () => {
  it('disposing a scope stops its effects and derives', () => {
    const a = cell(1);
    const runs = vi.fn();
    let total: any;
    const dispose = scope(() => {
      total = derive(() => a.get() + 1);
      effect(() => { total.get(); runs(); });
    });
    a.set(2);
    expect(runs).toHaveBeenCalledTimes(2);
    dispose();
    a.set(3);
    expect(runs).toHaveBeenCalledTimes(2);
    expect(describe(a).observers).toEqual([]);
  });

  it('runs cleanups newest first, children before the owner, once', () => {
    const log: string[] = [];
    const dispose = scope(() => {
      onCleanup(() => log.push('outer first'));
      scope(() => { onCleanup(() => log.push('child')); });
      effect(() => { onCleanup(() => log.push('effect')); });
      onCleanup(() => log.push('outer last'));
    });
    dispose();
    dispose();
    expect(log).toEqual(['effect', 'child', 'outer last', 'outer first']);
  });

  it('effect cleanups run before each re-run and on disposal', () => {
    const a = cell(1);
    const log: string[] = [];
    const dispose = scope(() => {
      effect(() => { const v = a.get(); onCleanup(() => log.push(`cleanup ${v}`)); });
    });
    a.set(2);
    dispose();
    expect(log).toEqual(['cleanup 1', 'cleanup 2']);
  });

  it('a throwing cleanup does not stop the remaining cleanups', () => {
    const log: string[] = [];
    const restore = setErrorHandler(() => log.push('reported'));
    scope(() => {
      onCleanup(() => log.push('second'));
      onCleanup(() => { throw new Error('bad cleanup'); });
    })();
    setErrorHandler(restore);
    expect(log).toEqual(['reported', 'second']);
  });

  it('a scope that throws while starting is disposed', () => {
    const log: string[] = [];
    expect(() => scope(() => { onCleanup(() => log.push('cleaned')); throw new Error('start failed'); })).toThrow('start failed');
    expect(log).toEqual(['cleaned']);
  });

  it('runWithOwner attaches work done after an await to the original scope', async () => {
    const a = cell(1);
    const runs = vi.fn();
    let owner: unknown;
    const dispose = scope(() => { owner = getOwner(); });
    await Promise.resolve();
    runWithOwner(owner, () => effect(() => { a.get(); runs(); }));
    dispose();
    a.set(2);
    expect(runs).toHaveBeenCalledTimes(1);
  });

  it('disposing a child scope releases it from its parent', () => {
    let child!: () => void;
    const parentLog: string[] = [];
    const dispose = scope(() => { child = scope(() => onCleanup(() => parentLog.push('child'))); });
    child();
    dispose();
    expect(parentLog).toEqual(['child']);
  });
});

group('scale', () => {
  it('updates 10,000 cells in one batch with one effect run per reader', () => {
    const cells = Array.from({ length: 10000 }, (_, index) => cell(index));
    const total = derive(() => cells.reduce((sum, item) => sum + item.get(), 0));
    const runs = vi.fn();
    effect(() => { total.get(); runs(); });
    batch(() => cells.forEach(item => item.set(item.peek() + 1)));
    expect(total.get()).toBe(10000 * 9999 / 2 + 10000);
    expect(runs).toHaveBeenCalledTimes(2);
  });

  it('a long chain does not recompute untouched branches', () => {
    const root = cell(0);
    let previous: { get(): number } = root;
    const counts: number[] = [];
    for (let index = 0; index < 200; index++) {
      const source = previous, at = index;
      counts.push(0);
      previous = derive(() => { counts[at]++; return source.get() + 1; });
    }
    expect(previous.get()).toBe(200);
    root.set(1);
    expect(previous.get()).toBe(201);
    expect(counts.every(count => count === 2)).toBe(true);
  });
});
