// Undo and redo for the rich text editor. Each step keeps the state before a change. Changes of the
// same group (typing, deleting) within a second of each other are one step, so undo takes back a burst
// of typing rather than one letter; a new word, a format change, a paste or a caret move starts a step.

import type { State } from './ops';

export const LIMIT = 200;
export const BURST_MS = 1000;

export class History {
  private done: State[] = [];
  private undone: State[] = [];
  private group: string | null = null;
  private last = 0;

  /** Record the state before a change. A change in the same group as the last, soon after it, joins its step. */
  record(before: State, group: string | null = null, now = Date.now()) {
    this.undone = [];
    if (group !== null && group === this.group && now - this.last < BURST_MS && this.done.length) { this.last = now; return; }
    this.done.push(before);
    if (this.done.length > LIMIT) this.done.shift();
    this.group = group;
    this.last = now;
  }

  /** The next change starts a new step (the caret moved, a word ended). */
  close() { this.group = null; }

  undo(current: State): State | null {
    const previous = this.done.pop();
    if (!previous) return null;
    this.undone.push(current);
    this.group = null;
    return previous;
  }

  redo(current: State): State | null {
    const next = this.undone.pop();
    if (!next) return null;
    this.done.push(current);
    this.group = null;
    return next;
  }

  get canUndo() { return this.done.length > 0; }
  get canRedo() { return this.undone.length > 0; }

  clear() { this.done = []; this.undone = []; this.group = null; }
}
