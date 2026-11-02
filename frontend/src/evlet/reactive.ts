// Evlet's reactive core: cells hold values, derives compute from them, effects act on them.
//
// Push-pull: a write pushes a "stale" mark down the graph (DIRTY on direct readers, CHECK
// further down). Nothing is computed until something reads it. Reading a CHECK node first
// brings its sources up to date in dependency order, so a derive never sees a half-updated
// graph (no glitches) and runs at most once per change. Effects are queued by the push and
// run after the outermost batch or write, each pulling only what it reads.

const CLEAN = 0, CHECK = 1, DIRTY = 2;
type State = typeof CLEAN | typeof CHECK | typeof DIRTY;

export type Equals<T> = false | ((previous: T, next: T) => boolean);
export interface Options<T> {
  /** Shown in errors and by describe(), for example `record.lines[3].net`. */
  name?: string;
  /** Skip a write or recomputation that leaves the value unchanged. Defaults to Object.is; false always notifies. */
  equals?: Equals<T>;
}
export type ErrorHandler = (error: unknown, source: string) => void;
export interface Readable<T> { get(): T; peek(): T; readonly name: string }

// Turn on checks that cost a little at runtime (writes during a derive). Vite sets DEV.
let dev = (import.meta as any).env?.DEV ?? true;
export function setDev(value: boolean) { dev = value; }

// ---------- ownership ----------

interface Disposable { dispose(): void }

// An owner holds the effects, derives and scopes created while it runs, and cleanup callbacks.
class Owner implements Disposable {
  owned: Set<Disposable> | null = null;
  cleanups: Array<() => void> | null = null;
  disposed = false;
  constructor(public parent: Owner | null, public name: string, public onError: ErrorHandler | null = null, register = true) {
    if (register) parent?.adopt(this);
  }
  adopt(child: Disposable) { (this.owned ??= new Set()).add(child); }
  // Children are disposed newest first, then this owner's cleanups run newest first.
  reset() {
    if (this.owned) {
      const owned = [...this.owned].reverse();
      this.owned = null;
      for (const child of owned) child.dispose();
    }
    if (this.cleanups) {
      const cleanups = this.cleanups.reverse();
      this.cleanups = null;
      for (const cleanup of cleanups) {
        try { cleanup(); } catch (error) { report(error, this); }
      }
    }
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.reset();
    this.parent?.owned?.delete(this);
  }
}

class Scope extends Owner {}

// ---------- graph nodes ----------

abstract class Source<T> {
  observers: Computation<any>[] = [];
  /** Counts real changes, so a derive without subscriptions can tell whether it is still valid. */
  version = 0;
  constructor(public value: T, public name: string, public equals: Equals<T>) {}
}

export class Cell<T> extends Source<T> implements Readable<T> {
  get(): T {
    track(this);
    return this.value;
  }
  /** Read without subscribing the current derive or effect. */
  peek(): T { return this.value; }
  set(value: T): void {
    if (dev && running && !running.effect && running.computing)
      throw new Error(`Cannot write ${this.name} while computing ${running.name}. Derives must not change cells.`);
    if (this.equals && this.equals(this.value, value)) return;
    this.value = value;
    this.version++;
    // An effect that read this cell earlier in its current run must run again.
    if (running?.effect && running.computing && readThisRun(this)) running.rerun = true;
    for (const observer of this.observers) observer.stale(DIRTY);
    if (!batchDepth) flush();
  }
  update(change: (value: T) => T): void { this.set(change(this.value)); }
}

class Computation<T> extends Source<T> {
  state: State = DIRTY;
  sources: Source<any>[] | null = null;
  owner: Owner;
  error: unknown = undefined;
  failed = false;
  computing = false;
  disposed = false;
  rerun = false;
  ran = false;
  // A derive created outside any scope holds no subscriptions while nothing reads it: it keeps
  // its sources and their versions here and checks them when read.
  readonly unowned: boolean;
  cold: { sources: Source<any>[]; versions: number[] } | null = null;
  constructor(public fn: (previous: T) => T, value: T, name: string, equals: Equals<T>, public effect: boolean) {
    super(value, name, equals);
    // A computation owns what it creates while running; its owner is where it was created.
    this.owner = new Owner(owner, name, null, false);
    this.unowned = !effect && owner === null;
    // Created inside a scope that is already disposed: it never runs or subscribes.
    if (owner?.disposed) { this.disposed = true; this.owner.disposed = true; }
    else owner?.adopt(this);
  }

  stale(state: State) {
    if (this.disposed) return;
    if (this.computing) { if (this.effect) this.rerun = true; return; }
    if (this.state >= state) return;
    if (this.state === CLEAN && this.effect) queue.push(this);
    this.state = state;
    for (const observer of this.observers) observer.stale(CHECK);
  }

  // Bring this node up to date: confirm CHECK sources first, recompute only if one really changed.
  refresh() {
    if (this.disposed) return;
    if (this.state === CHECK) {
      for (const source of this.sources ?? []) {
        if (source instanceof Computation) {
          source.refresh();
          if ((this.state as State) === DIRTY) break;
        }
      }
      if (this.state === CHECK) this.state = CLEAN;
    }
    if (this.state === DIRTY) this.update();
  }

  update() {
    if (this.disposed) return;
    const previous = this.value, failedBefore = this.failed;
    this.owner.reset();
    const saved = [listener, gathered, gatheredFrom, owner, running] as const;
    listener = this; gathered = null; gatheredFrom = 0; owner = this.owner; running = this;
    this.computing = true;
    this.rerun = false;
    try {
      const value = this.fn(previous);
      this.failed = false; this.error = undefined;
      this.value = value;
    } catch (error) {
      if (this.effect) report(error, this.owner, this.name);
      else { this.failed = true; this.error = error; }
    } finally {
      this.computing = false;
      this.ran = true;
      // A node disposed while it ran (its scope ended) keeps no subscriptions.
      if (this.disposed) { if (this.sources) unlink(this, 0); this.sources = null; }
      else this.relink();
      [listener, gathered, gatheredFrom, owner, running] = saved;
    }
    this.state = CLEAN;
    if (this.effect) {
      if (this.rerun && !this.disposed) {
        this.rerun = false;
        this.state = DIRTY;
        queue.push(this);
        if (!batchDepth) flush();
      }
      return;
    }
    const changed = this.failed || failedBefore || !this.equals || !this.equals(previous, this.value);
    if (!changed) return;
    this.version++;
    // Readers are already marked CHECK; tell them this source really changed.
    for (const observer of this.observers) observer.state = DIRTY;
  }

  // Replace the subscriptions from the previous run with the sources read in this one.
  relink() {
    const sources = this.sources;
    if (gathered) {
      if (sources) unlink(this, gatheredFrom);
      if (sources && gatheredFrom > 0) {
        sources.length = gatheredFrom + gathered.length;
        for (let index = 0; index < gathered.length; index++) sources[gatheredFrom + index] = gathered[index];
      } else this.sources = gathered;
      const list = this.sources!;
      for (let index = gatheredFrom; index < list.length; index++) list[index].observers.push(this);
    } else if (sources && gatheredFrom < sources.length) {
      unlink(this, gatheredFrom);
      sources.length = gatheredFrom;
    }
  }

  // Drop subscriptions but remember what was read, at which versions.
  cool() {
    if (this.cold || this.computing || this.disposed) return;
    const sources = this.sources ?? [];
    // A node already marked stale recomputes on its next read.
    this.cold = { sources, versions: sources.map(source => this.state === CLEAN ? source.version : -1) };
    this.sources = null;
    unlinkAll(this, sources);
  }

  // Subscribe again to the sources a cold node remembers (they are known to be current).
  warm() {
    const cold = this.cold;
    if (!cold) return;
    this.cold = null;
    this.sources = cold.sources;
    this.state = CLEAN;
    for (const source of cold.sources) {
      if (source instanceof Computation) source.warm();
      source.observers.push(this);
    }
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.owner.dispose();
    if (this.sources) { unlink(this, 0); this.sources = null; }
    this.cold = null;
    this.owner.parent?.owned?.delete(this);
    // Readers of a disposed derive run again, so they can read whatever replaced it.
    if (!this.effect && this.observers.length) {
      for (const observer of this.observers) observer.stale(DIRTY);
      if (!batchDepth) flush();
    }
  }
}

export class Derive<T> extends Computation<T> implements Readable<T> {
  get(): T {
    if (this.computing) throw new Error(`${this.name} reads itself: a derive cannot depend on its own value`);
    if (this.disposed) {
      // A derive created in a disposed scope still gives its value once, without subscribing.
      if (!this.ran) {
        const saved = [owner, listener] as const;
        owner = null; listener = null;
        try { this.value = this.fn(this.value); } finally { [owner, listener] = saved; this.ran = true; }
      }
      return this.value;
    }
    track(this);
    if (this.cold) {
      // Recompute only if a source changed; read by a derive or effect, subscribe again.
      if (!stillValid(this.cold)) {
        this.cold = null;
        this.state = DIRTY;
        this.update();
      } else if (listener) this.warm();
    } else this.refresh();
    if (this.unowned && !listener && !this.observers.length) this.cool();
    if (this.failed) throw this.error;
    return this.value;
  }
  peek(): T { return untrack(() => this.get()); }
}

// Whether the sources a cold derive read still have the versions it computed from.
function stillValid(cold: { sources: Source<any>[]; versions: number[] }) {
  for (let index = 0; index < cold.sources.length; index++) {
    const source = cold.sources[index];
    if (source instanceof Derive) {
      if (source.disposed) return false;
      try { untrack(() => source.get()); } catch { /* a failure changes its version */ }
    }
    if (source.version !== cold.versions[index]) return false;
  }
  return true;
}

function unlink(computation: Computation<any>, from: number) {
  unlinkAll(computation, computation.sources!.slice(from));
}

function unlinkAll(computation: Computation<any>, sources: Source<any>[]) {
  for (const source of sources) {
    const observers = source.observers, at = observers.lastIndexOf(computation);
    if (at >= 0) { observers[at] = observers[observers.length - 1]; observers.pop(); }
    // An unowned derive nobody reads any more lets go of its own sources too.
    if (source instanceof Computation && source.unowned && !source.observers.length) source.cool();
  }
}

// Whether the running computation read this cell earlier in the current run.
function readThisRun(source: Source<any>) {
  if (!running || listener !== running) return false;
  if (gathered?.includes(source)) return true;
  const sources = running.sources;
  if (!sources) return false;
  for (let index = 0; index < gatheredFrom; index++) if (sources[index] === source) return true;
  return false;
}

// ---------- tracking and scheduling ----------

let listener: Computation<any> | null = null;   // who records reads
let running: Computation<any> | null = null;    // who is computing, even inside untrack()
let owner: Owner | null = null;                 // who owns new nodes
// Reads that match the previous run's sources in order are only counted; the rest are gathered.
let gathered: Source<any>[] | null = null;
let gatheredFrom = 0;
let batchDepth = 0;
let queue: Computation<any>[] = [];
let flushing = false;
const MAX_ROUNDS = 1000;

function track(source: Source<any>) {
  if (!listener) return;
  if (!gathered && listener.sources?.[gatheredFrom] === source) gatheredFrom++;
  else if (!gathered) gathered = [source];
  else if (gathered[gathered.length - 1] !== source) gathered.push(source);
}

function flush() {
  if (flushing) return;
  flushing = true;
  try {
    for (let round = 0; queue.length; round++) {
      if (round === MAX_ROUNDS) {
        const names = [...new Set(queue.map(item => item.name))].slice(0, 5).join(', ');
        const first = queue[0];
        for (const item of queue) item.state = CLEAN;
        queue = [];
        report(new Error(`Effects did not settle after ${MAX_ROUNDS} rounds (${names}). An effect keeps changing a cell that effects read.`), first.owner, first.name);
        break;
      }
      const current = queue;
      queue = [];
      for (const effect of current) runEffect(effect);
    }
  } finally {
    flushing = false;
  }
}

// An effect inside another effect runs after it, so a parent that re-runs and disposes it first wins.
function runEffect(effect: Computation<any>) {
  const chain: Computation<any>[] = [];
  for (let item: Owner | null = effect.owner.parent; item; item = item.parent) {
    const computation = owners.get(item);
    if (computation?.effect && computation.state !== CLEAN && !computation.computing) chain.push(computation);
  }
  for (let index = chain.length - 1; index >= 0; index--) chain[index].refresh();
  effect.refresh();
}

function report(error: unknown, from: Owner, name = from.name) {
  for (let item: Owner | null = from; item; item = item.parent) {
    if (item.onError) {
      try { item.onError(error, name); return; } catch (next) { error = next; }
    }
  }
  errorHandler(error, name);
}

let errorHandler: ErrorHandler = (error, source) => console.error(`Evlet: ${source} failed`, error);
/** Replace the handler for effect errors that no scope handles. Returns the previous handler. */
export function setErrorHandler(handler: ErrorHandler): ErrorHandler {
  const previous = errorHandler;
  errorHandler = handler;
  return previous;
}

// Maps a computation's own Owner back to it, so runEffect can find parent effects.
const owners = new WeakMap<Owner, Computation<any>>();

// ---------- public API ----------

let counter = 0;
const label = (kind: string, name?: string) => name ?? `${kind}${++counter}`;

/** A value that can change. Readers created with derive() or effect() update when it does. */
export function cell<T>(value: T, options: Options<T> = {}): Cell<T> {
  return new Cell(value, label('cell', options.name), options.equals === undefined ? Object.is : options.equals);
}

/**
 * A cached value computed from cells and other derives. Lazy: it computes when read.
 * Inside a scope it is disposed with the scope. Outside any scope it subscribes to its
 * sources only while something reads it, and otherwise checks them when read.
 */
export function derive<T>(fn: (previous: T | undefined) => T, options: Options<T> = {}): Derive<T> {
  const node = new Derive<T>(fn as (previous: T) => T, undefined as T, label('derive', options.name),
    options.equals === undefined ? Object.is : options.equals, false);
  owners.set(node.owner, node);
  return node;
}

/** Run fn now and again whenever what it read changes. Returns a function that stops it. */
export function effect(fn: () => void | (() => void), options: { name?: string } = {}): () => void {
  const node = new Computation<void>(() => {
    const cleanup = fn();
    if (typeof cleanup === 'function') onCleanup(cleanup);
  }, undefined, label('effect', options.name), false, true);
  owners.set(node.owner, node);
  if (node.disposed) return () => {};
  // The first run happens now; writes it makes are batched until it finishes.
  batch(() => runEffect(node));
  return () => node.dispose();
}

/** Group writes: effects run once, after the outermost batch. */
export function batch<T>(fn: () => T): T {
  batchDepth++;
  try {
    return fn();
  } finally {
    if (--batchDepth === 0) flush();
  }
}

/** Read without subscribing. */
export function untrack<T>(fn: () => T): T {
  const saved = listener;
  listener = null;
  try {
    return fn();
  } finally {
    listener = saved;
  }
}

export interface ScopeOptions { name?: string; onError?: ErrorHandler }

/**
 * Run fn in a new ownership scope. Effects, derives and scopes created inside, and onCleanup
 * callbacks, belong to it. Returns dispose, which stops them all (newest first).
 */
export function scope(fn: (dispose: () => void) => void, options: ScopeOptions = {}): () => void {
  const item = new Scope(owner, label('scope', options.name), options.onError ?? null);
  const dispose = () => item.dispose();
  const saved = [owner, listener] as const;
  owner = item; listener = null;
  try {
    fn(dispose);
  } catch (error) {
    dispose();
    throw error;
  } finally {
    [owner, listener] = saved;
  }
  return dispose;
}

/**
 * Run when the current scope is disposed, or before the current effect or derive runs again.
 * Registered on a scope that is already disposed, it runs at once. Outside any scope it is ignored.
 */
export function onCleanup(fn: () => void): void {
  if (!owner) return;
  if (owner.disposed) {
    try { fn(); } catch (error) { report(error, owner); }
    return;
  }
  (owner.cleanups ??= []).push(fn);
}

/** The current owner, to restore after an await with runWithOwner(). */
export function getOwner(): unknown { return owner; }

/** Run fn as if inside the given owner (for work that continues after an await). */
export function runWithOwner<T>(target: unknown, fn: () => T): T {
  const saved = [owner, listener] as const;
  owner = target as Owner | null; listener = null;
  try {
    return fn();
  } finally {
    [owner, listener] = saved;
  }
}

/** Whether the value is a cell or derive. */
export function isReadable(value: unknown): value is Readable<unknown> {
  return value instanceof Cell || value instanceof Derive;
}

/** Debugging: a node's name, current value and the names of its sources and readers. */
export function describe(node: Readable<unknown>) {
  const item = node as unknown as Source<unknown>;
  return {
    name: item.name,
    kind: item instanceof Cell ? 'cell' : 'derive',
    value: item.value,
    sources: item instanceof Computation ? (item.sources ?? item.cold?.sources ?? []).map(source => source.name) : [],
    observers: item.observers.map(observer => observer.name),
  };
}
