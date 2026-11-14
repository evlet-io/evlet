// A create receipt lives for 24 hours on the server. Keep a conservative local window,
// measured from before the first send, and never replace an uncertain request silently.
import { ApiError } from '../api';

export const CREATE_RETRY_MS = 23 * 60 * 60 * 1000;
export interface CreateAttempt {
  key: string;
  body: string;
  startedAt: number;
  principal: number | null;
  endpoint: string;
}

/** JSON's wire values, sorted by object key; arrays (including rows) retain their order. */
export function canonicalCreate(body: Record<string, unknown>): string {
  const sorted = (value: any): any => Array.isArray(value) ? value.map(sorted)
    : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
  return JSON.stringify(sorted(JSON.parse(JSON.stringify(body))));
}

const refused = (message: string): never => { throw new ApiError(message, 0, 'response'); };

export function prepareCreate(body: Record<string, unknown>, previous: CreateAttempt | null,
  principal: number | null, endpoint: string, now = Date.now()): CreateAttempt {
  const text = canonicalCreate(body);
  if (previous !== null) {
    if (!previous || typeof previous.key !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(previous.key) ||
      typeof previous.body !== 'string' || !Number.isFinite(previous.startedAt) || typeof previous.endpoint !== 'string' ||
      !(previous.principal === null || Number.isSafeInteger(previous.principal)))
      refused('This draft has an unreadable create receipt. Check the record list before starting a new record.');
    if (previous.principal !== principal || previous.endpoint !== endpoint)
      refused('This create belongs to another account or server. Return to that account and check its record list before continuing.');
    if (now < previous.startedAt || now - previous.startedAt >= CREATE_RETRY_MS)
      refused('The safe retry period for this create has ended. It may already exist. Check the record list before starting a new record.');
    if (previous.body !== text)
      refused('An earlier create may have succeeded with different values. Restore those values to retry, or check the record list before starting a new record.');
    return previous;
  }
  const random = new Uint8Array(16);
  crypto.getRandomValues(random);
  const key = [...random].map(value => value.toString(16).padStart(2, '0')).join('');
  return { key, body: text, startedAt: now, principal, endpoint };
}
