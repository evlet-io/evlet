import { cell, type Cell } from './reactive';

/** A bounded cache of cells. A caller holding a cell keeps its identity across eviction; the cache
 * keeps only weak references to evicted cells, so an open view still receives subsequent updates. */
export class CellCache<T> {
  private readonly retained = new Map<string, { cell: Cell<T>; bytes: number }>();
  private readonly live = new Map<string, WeakRef<Cell<T>>>();
  private bytes = 0;
  private readonly collected = new FinalizationRegistry<{ key: string; ref: WeakRef<Cell<T>> }>(({ key, ref }) => {
    if (this.live.get(key) === ref) this.live.delete(key);
  });

  constructor(private readonly empty: T, private readonly limit = 128, private readonly budget = 4 * 1024 * 1024) {}

  peek(key: string): Cell<T> | undefined { return this.live.get(key)?.deref(); }

  get(key: string, initial: T = this.empty): Cell<T> {
    let found = this.peek(key);
    if (!found) {
      found = cell(initial, { name: `cached ${key}` });
      const ref = new WeakRef(found);
      this.live.set(key, ref);
      this.collected.register(found, { key, ref });
    }
    this.keep(key, found);
    return found;
  }

  set(key: string, value: T): void {
    const found = this.get(key);
    found.set(value);
    this.keep(key, found, weight(value, this.budget));
  }

  clear(): void {
    for (const ref of this.live.values()) ref.deref()?.set(this.empty);
    this.live.clear(); this.retained.clear(); this.bytes = 0;
  }

  private keep(key: string, found: Cell<T>, bytes?: number): void {
    const previous = this.retained.get(key);
    this.retained.delete(key);
    this.bytes -= previous?.bytes ?? 0;
    const size = bytes ?? previous?.bytes ?? weight(found.peek(), this.budget);
    // One huge record remains available to its open views without occupying the shared cache.
    if (size <= this.budget) { this.retained.set(key, { cell: found, bytes: size }); this.bytes += size; }
    while (this.retained.size > this.limit || this.bytes > this.budget) {
      const oldest = this.retained.keys().next().value!;
      this.bytes -= this.retained.get(oldest)!.bytes;
      this.retained.delete(oldest);
    }
  }
}

// Approximate retained JSON memory without allocating a second serialized copy. Stop at the cap;
// this is a cache budget, not a claim about a browser's exact object/heap representation.
function weight(value: unknown, cap: number): number {
  let bytes = 0;
  const pending = [value], seen = new Set<object>();
  while (pending.length && bytes <= cap) {
    const next = pending.pop();
    if (typeof next === 'string') bytes += next.length * 2 + 16;
    else if (next && typeof next === 'object') {
      if (seen.has(next)) continue;
      seen.add(next); bytes += 32;
      for (const [key, item] of Object.entries(next)) { bytes += key.length * 2 + 16; pending.push(item); }
    } else bytes += 8;
  }
  return bytes;
}
