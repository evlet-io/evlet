// Per-user preferences (docs/ui-design/README.md section 10): sidebar, density, list or
// board, column widths and quick access. Recent records and recent searches belong to the
// signed-in session: they stay in memory and go when the user signs out.
//
// The theme, region and other Settings live on the server (settings.ts, ui/theme.ts). These layout
// preferences live in browser storage under `evlet.<username>.`. Preferences never hold record data
// (titles, values or searches). Every read and write is guarded: private windows and blocked
// storage fall back to the defaults.

import { cell, effect, getOwner, runWithOwner, scope, untrack, type Cell } from '../reactive';

export type Density = 'comfortable' | 'compact';

export class Prefs {
  private readonly cells = new Map<string, Cell<any>>();
  private readonly stop: () => void;
  private root: unknown = null;

  constructor(readonly user: string, private readonly storage: Storage | null = safeStorage()) {
    // Saving effects belong to the preferences, not to the page that first asked for a cell.
    this.stop = runWithOwner(null, () => scope(() => { this.root = getOwner(); }, { name: 'prefs' }));
    // Earlier builds stored recent record titles, searches and the theme here; they are not kept any more.
    for (const name of ['recents', 'recentQ', 'theme']) try { this.storage?.removeItem(this.key(name)); } catch { /* blocked */ }
  }

  private key(name: string) { return `evlet.${this.user}.${name}`; }

  private read<T>(name: string, fallback: T): T {
    try {
      const raw = this.storage?.getItem(this.key(name));
      return raw === null || raw === undefined ? fallback : JSON.parse(raw) as T;
    } catch { return fallback; }
  }

  /** A cell saved under this name. The same name gives the same cell. */
  cell<T>(name: string, fallback: T): Cell<T> {
    let found = this.cells.get(name);
    if (found) return found;
    const value = cell<T>(this.read(name, fallback), { name: `prefs.${name}` });
    this.cells.set(name, value);
    this.watch(name, value);
    return value;
  }

  /** A cell kept for this session only, never written to storage. */
  private memory<T>(name: string, fallback: T): Cell<T> {
    let found = this.cells.get(name);
    if (!found) this.cells.set(name, found = cell<T>(fallback, { name: `session.${name}` }));
    return found;
  }

  private watch<T>(name: string, value: Cell<T>) {
    runWithOwner(this.root, () => {
      let first = true;
      effect(() => {
        const next = value.get();
        if (first) { first = false; return; }
        untrack(() => { try { this.storage?.setItem(this.key(name), JSON.stringify(next)); } catch { /* storage full or blocked */ } });
      });
    });
  }

  get collapsed() { return this.cell('collapsed', false); }
  get density() { return this.cell<Density>('density', 'comfortable'); }
  /** This session's recent searches (memory only). */
  get recentSearches() { return this.memory<string[]>('recentQ', []); }
  /** This session's recent records (memory only). */
  get recentRecords() { return this.memory<Array<{ model: string; id: string; title: string }>>('recents', []); }
  /** Record info shows exact times instead of relative ones ("2 hours ago"). */
  get exactTimes() { return this.cell<boolean>('exactTimes', false); }
  get quickRows() { return this.cell<1 | 2>('qaRows', 1); }
  get quickIds() { return this.cell<string[] | null>('qaIds', null); }

  dispose() { this.stop(); }
}

function safeStorage(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}
