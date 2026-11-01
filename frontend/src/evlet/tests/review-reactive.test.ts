// Regression tests for review findings on the reactive core.
import { describe, expect, it, vi } from 'vitest';
import { cell, derive, describe as info, effect, onCleanup, scope, setErrorHandler } from '../reactive';

describe('disposed nodes', () => {
  it('a derive disposed by its owner is not recomputed or re-subscribed by another reader', () => {
    const a = cell(1, { name: 'a' });
    let inner: any = null, runs = 0, created = 0;
    const stop = scope(() => {
      effect(() => { a.get(); const n = ++created; inner = derive(() => { runs++; return a.get(); }, { name: 'inner' + n }); }, { name: 'parent' });
      effect(() => { inner.get(); }, { name: 'outer' });
    });
    for (let value = 2; value < 6; value++) a.set(value);
    // Only the live inner derive and the parent read a; no dead derive stays subscribed.
    expect(info(a).observers.sort()).toEqual(['inner5', 'parent']);
    expect(runs).toBeLessThanOrEqual(8);
    stop();
    expect(info(a).observers).toEqual([]);
  });

  it('an effect that disposes its own scope and then creates an effect leaves nothing running', () => {
    const a = cell(0);
    let inner = 0;
    const dispose = scope(() => {
      effect(() => {
        if (a.get() === 1) {
          dispose();
          effect(() => { a.get(); inner++; });
        }
      });
    });
    a.set(1);
    a.set(2);
    a.set(3);
    expect(inner).toBe(0);
    expect(info(a).observers).toEqual([]);
  });

  it('onCleanup registered after its scope was disposed runs at once', () => {
    const a = cell(0);
    let cleaned = false;
    const dispose = scope(() => {
      effect(() => { if (a.get() === 1) { dispose(); onCleanup(() => { cleaned = true; }); } });
    });
    a.set(1);
    expect(cleaned).toBe(true);
  });
});

describe('writes inside effects', () => {
  it('an effect that writes a cell it read in the same run runs again until it settles', () => {
    const counter = cell(0);
    const runs = vi.fn();
    effect(() => { runs(); const value = counter.get(); if (value < 3) counter.set(value + 1); });
    expect(counter.peek()).toBe(3);
    expect(runs).toHaveBeenCalledTimes(4);
  });

  it('an effect that always writes what it reads hits the loop guard, reported to onError', () => {
    const counter = cell(0);
    const caught: string[] = [];
    scope(() => { effect(() => counter.set(counter.get() + 1), { name: 'runaway' }); }, { onError: error => caught.push((error as Error).message) });
    expect(caught).toHaveLength(1);
    expect(caught[0]).toContain('did not settle');
    expect(caught[0]).toContain('runaway');
  });

  it('the loop guard reports to the global handler when no scope handles it, and the graph recovers', () => {
    const errors: unknown[] = [];
    const restore = setErrorHandler(error => errors.push(error));
    const ping = cell(0), pong = cell(0);
    const stopPing = effect(() => pong.set(ping.get() + 1));
    const stopPong = effect(() => ping.set(pong.get() + 1));
    setErrorHandler(restore);
    expect(String(errors[0])).toContain('did not settle');
    stopPing(); stopPong();
    const later = cell(0), seen: number[] = [];
    effect(() => { seen.push(later.get()); });
    later.set(1);
    expect(seen).toEqual([0, 1]);
  });
});

describe('ownership of derives', () => {
  it('derives created outside any scope do not stay subscribed once nothing reads them', () => {
    const a = cell(0);
    for (let index = 0; index < 1000; index++) derive(() => a.get()).get();
    expect(info(a).observers).toHaveLength(0);
  });

  it('an unowned derive read without a reader still caches until its inputs change', () => {
    const a = cell(1);
    const run = vi.fn(() => a.get() * 2);
    const doubled = derive(run);
    expect([doubled.get(), doubled.get()]).toEqual([2, 2]);
    expect(run).toHaveBeenCalledTimes(1);
    a.set(2);
    expect(doubled.get()).toBe(4);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('an unowned derive used by an effect stops reading its inputs when the effect is disposed', () => {
    const a = cell(0);
    const shared = derive(() => a.get() + 1);
    const seen: number[] = [];
    const stop = effect(() => { seen.push(shared.get()); });
    a.set(1);
    expect(seen).toEqual([1, 2]);
    stop();
    expect(info(a).observers).toEqual([]);
    a.set(5);
    expect(shared.get()).toBe(6);
  });

  it('100,000 scopes created and disposed leave no observers', () => {
    const a = cell(0);
    const shared = derive(() => a.get() + 1);
    for (let index = 0; index < 100_000; index++) scope(() => { effect(() => { shared.get(); a.get(); }); derive(() => a.get()).get(); })();
    expect(info(a).observers).toEqual([]);
    expect(info(shared).observers).toEqual([]);
  });
});
