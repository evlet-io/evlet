// A record's related records. The Related section's chips show how many records each relation holds: one read
// counts them all (GET /api/{model}/{id}/related), once the section first scrolls into view. A relation's rows come a
// page of five at a time, only when its list shows (the section is open, or a Related tab section is in view); a
// page's total then replaces the count. Pages stay loaded while the record is open, so switching tabs does not ask
// again. A save of the record reloads the counts and the pages already shown, since an action or a move may add or
// remove related records.

import type { RecordData } from '../api';
import { batch, cell, derive, effect, onCleanup, untrack, type Cell, type Readable } from '../reactive';
import type { RelationSchema } from './meta';

/** Rows a relation shows at a time. */
export const RELATED_PAGE = 5;

export interface RelatedPage {
  rows: Cell<RecordData[]>;
  /** Null until the first page has loaded. */
  total: Cell<number | null>;
  offset: Cell<number>;
  problem: Cell<string>;
  /** How many records the relation holds: the page's total once a page loaded, else the counted number; null while
   * neither has come. */
  count: Readable<number | null>;
  /** Loads the first page unless a page was asked for already. */
  ensure(): void;
  /** Loads the page that starts at `offset`. */
  load(offset: number): void;
}

type Fetch = (relation: string, page: { limit: number; offset: number }) => Promise<{ items: RecordData[]; total: number }>;
type FetchCounts = () => Promise<Record<string, number>>;

export interface RelatedPages {
  (name: string): RelatedPage;
  /** Counts every relation in one read, unless they were asked for already. */
  ensureCounts(): void;
  /** Whether the counts could not be read. */
  countsFailed: Readable<boolean>;
}

/** The pages of one record's relations; `version` changes after each save (the record's etag). */
export function relatedPages(relations: RelationSchema[], fetch: Fetch, version: Readable<unknown>, problemText: (error: unknown) => string, fetchCounts?: FetchCounts): RelatedPages {
  const pages = new Map<string, RelatedPage & { started: boolean }>();
  const counted = cell<Record<string, number> | null>(null), countsFailed = cell(false);
  let countsStarted = false, countTicket = 0;
  const loadCounts = () => {
    if (!fetchCounts) return;
    countsStarted = true;
    const ticket = ++countTicket;
    void fetchCounts().then(counts => { if (ticket === countTicket) batch(() => { counted.set(counts); countsFailed.set(false); }); },
      () => { if (ticket === countTicket) countsFailed.set(true); });
  };
  for (const relation of relations) {
    const rows = cell<RecordData[]>([]), total = cell<number | null>(null), offset = cell(0), problem = cell('');
    const count = derive(() => total.get() ?? counted.get()?.[relation.name] ?? null);
    let asked = 0;
    const page = {
      rows, total, offset, problem, count, started: false,
      ensure() { if (!page.started) page.load(0); },
      load(start: number) {
        page.started = true;
        const ticket = ++asked;
        offset.set(start);
        void fetch(relation.name, { limit: RELATED_PAGE, offset: start }).then(result => {
          if (ticket !== asked) return;
          // Records went away under the page shown: step back to the last page that has rows.
          if (!result.items.length && start > 0 && result.total > 0) { page.load(Math.floor((result.total - 1) / RELATED_PAGE) * RELATED_PAGE); return; }
          batch(() => { rows.set(result.items); total.set(result.total); problem.set(''); });
        }, error => { if (ticket === asked) problem.set(problemText(error)); });
      },
    };
    pages.set(relation.name, page);
  }
  let first = true;
  effect(() => {
    version.get();
    if (first) { first = false; return; }
    untrack(() => {
      if (countsStarted) loadCounts();
      for (const page of pages.values()) if (page.started) page.load(page.offset.peek());
    });
  });
  return Object.assign((name: string) => pages.get(name)!, { ensureCounts() { if (!countsStarted) loadCounts(); }, countsFailed });
}

/** Runs `show` once `element` first scrolls into view; at once where IntersectionObserver is missing. */
export function whenVisible(element: Element, show: () => void): void {
  // A browser can retain a disconnected observer's delegate. Release the page callback as soon
  // as it is consumed or its scope closes, and ignore notifications already queued at disposal.
  let pending: (() => void) | null = show;
  let observer: IntersectionObserver | null = null;
  const cancel = () => { pending = null; observer?.disconnect(); };
  const visible = () => { const callback = pending; cancel(); callback?.(); };
  onCleanup(cancel);
  if (!pending) return;
  if (typeof IntersectionObserver === 'undefined') { queueMicrotask(visible); return; }
  observer = new IntersectionObserver(entries => {
    if (!entries.some(entry => entry.isIntersecting)) return;
    visible();
  });
  observer.observe(element);
}

/** "1–5 of 12". */
export const rangeText = (offset: number, shown: number, total: number) => `${offset + 1}–${offset + shown} of ${total}`;
