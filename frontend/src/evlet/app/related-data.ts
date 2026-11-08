// A record's related records, one page of five per relation, loaded only when something shows them:
// the side panel's Related block or a Related tab section, once it scrolls into view. One page per
// relation serves both, so its count costs no request of its own, and pages stay loaded while the
// record is open, so switching tabs does not ask again. A save of the record reloads the pages
// already shown, since an action or a move may add or remove related records.

import type { RecordData } from '../api';
import { batch, cell, effect, onCleanup, untrack, type Cell, type Readable } from '../reactive';
import type { RelationSchema } from './meta';

/** Rows a relation shows at a time. */
export const RELATED_PAGE = 5;

export interface RelatedPage {
  rows: Cell<RecordData[]>;
  /** Null until the first page has loaded. */
  total: Cell<number | null>;
  offset: Cell<number>;
  problem: Cell<string>;
  /** Loads the first page unless a page was asked for already. */
  ensure(): void;
  /** Loads the page that starts at `offset`. */
  load(offset: number): void;
}

type Fetch = (relation: string, page: { limit: number; offset: number }) => Promise<{ items: RecordData[]; total: number }>;

/** The pages of one record's relations; `version` changes after each save (the record's etag). */
export function relatedPages(relations: RelationSchema[], fetch: Fetch, version: Readable<unknown>, problemText: (error: unknown) => string): (name: string) => RelatedPage {
  const pages = new Map<string, RelatedPage & { started: boolean }>();
  for (const relation of relations) {
    const rows = cell<RecordData[]>([]), total = cell<number | null>(null), offset = cell(0), problem = cell('');
    let asked = 0;
    const page = {
      rows, total, offset, problem, started: false,
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
    untrack(() => { for (const page of pages.values()) if (page.started) page.load(page.offset.peek()); });
  });
  return name => pages.get(name)!;
}

/** Runs `show` once `element` first scrolls into view; at once where IntersectionObserver is missing. */
export function whenVisible(element: Element, show: () => void): void {
  if (typeof IntersectionObserver === 'undefined') { queueMicrotask(show); return; }
  const observer = new IntersectionObserver(entries => {
    if (!entries.some(entry => entry.isIntersecting)) return;
    observer.disconnect();
    show();
  });
  observer.observe(element);
  onCleanup(() => observer.disconnect());
}

/** "1–5 of 12". */
export const rangeText = (offset: number, shown: number, total: number) => `${offset + 1}–${offset + shown} of ${total}`;
