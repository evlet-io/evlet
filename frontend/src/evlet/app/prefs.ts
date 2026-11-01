// Per-user preferences (docs/ui-design/README.md section 10): theme, sidebar, density, list or
// board, column widths, recent searches, quick access and recent records.
//
// The design keeps them in server-side user settings; this base has no such route yet, so they
// live in browser storage under `evlet.<username>.`. Every read and write is guarded: private
// windows and blocked storage fall back to the defaults.

import { cell, effect, getOwner, runWithOwner, scope, untrack, type Cell } from '../reactive';

export type Theme = 'system' | 'light' | 'dark';
export type Density = 'comfortable' | 'compact';

export class Prefs {
  private readonly cells = new Map<string, Cell<any>>();
  private readonly stop: () => void;
  private root: unknown = null;

  constructor(readonly user: string, private readonly storage: Storage | null = safeStorage()) {
    // Saving effects belong to the preferences, not to the page that first asked for a cell.
    this.stop = runWithOwner(null, () => scope(() => { this.root = getOwner(); }, { name: 'prefs' }));
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
    runWithOwner(this.root, () => {
      let first = true;
      effect(() => {
        const next = value.get();
        if (first) { first = false; return; }
        untrack(() => { try { this.storage?.setItem(this.key(name), JSON.stringify(next)); } catch { /* storage full or blocked */ } });
      });
    });
    return value;
  }

  get theme() { return this.cell<Theme>('theme', 'system'); }
  get collapsed() { return this.cell('collapsed', false); }
  get density() { return this.cell<Density>('density', 'comfortable'); }
  get recentSearches() { return this.cell<string[]>('recentQ', []); }
  get recentRecords() { return this.cell<Array<{ model: string; id: number; title: string }>>('recents', []); }
  get quickRows() { return this.cell<1 | 2>('qaRows', 1); }
  get quickIds() { return this.cell<string[] | null>('qaIds', null); }

  dispose() { this.stop(); }
}

function safeStorage(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

/** Apply a theme to the document: an explicit choice wins over the system in both directions. */
export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement) {
  if (theme === 'system') delete root.dataset.theme; else root.dataset.theme = theme;
}
