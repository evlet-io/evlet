import { afterEach, describe, expect, it, vi } from 'vitest';
import { whenVisible } from '../app/related-data';
import { scope } from '../reactive';

afterEach(() => vi.unstubAllGlobals());

function observer() {
  let notify!: IntersectionObserverCallback;
  const disconnect = vi.fn();
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback) { notify = callback; }
    observe() {}
    disconnect = disconnect;
  });
  return { disconnect, show: () => notify([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver) };
}

describe('related content visibility lifetime', () => {
  it('loads once even if the browser delivers another queued visible notification', () => {
    const watching = observer(), load = vi.fn();
    const dispose = scope(() => whenVisible(document.createElement('section'), load));
    watching.show();
    watching.show();
    expect(load).toHaveBeenCalledTimes(1);
    expect(watching.disconnect).toHaveBeenCalled();
    dispose();
  });

  it('ignores an already queued notification after the record has closed', () => {
    const watching = observer(), load = vi.fn();
    const dispose = scope(() => whenVisible(document.createElement('section'), load));
    dispose();
    watching.show();
    expect(load).not.toHaveBeenCalled();
    expect(watching.disconnect).toHaveBeenCalled();
  });

  it('cancels the fallback microtask when its record closes before it runs', async () => {
    vi.stubGlobal('IntersectionObserver', undefined);
    const load = vi.fn();
    const dispose = scope(() => whenVisible(document.createElement('section'), load));
    dispose();
    await Promise.resolve();
    expect(load).not.toHaveBeenCalled();
    const stop = scope(() => whenVisible(document.createElement('section'), load));
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);
    stop();
  });
});
