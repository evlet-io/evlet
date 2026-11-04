// Record ids: 13 lowercase Crockford base32 characters, such as '01hc1sd9vc1ye'.
//
// The server sends ids only in this text form, never as JSON numbers, because a 63-bit id
// does not fit a JavaScript number. The browser compares and routes them as strings.
// See docs/internal/record-identity.md.

const ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz';
const TEXT = /^[0-7ilo][0-9a-hjkmnp-tv-z]{12}$/;
const LEGACY = /^[1-9][0-9]{0,11}$/;  // shorter than a text id, as in evlet/identity.py

/** The id in its stored form: lowercase, with i and l read as 1 and o as 0. Null when it is not an id. */
export function recordId(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const text = value.toLowerCase().replace(/[il]/g, '1').replace(/o/g, '0');
  return TEXT.test(text) ? text : null;
}

/** True for a 13-character record id in any case. */
export function isRecordId(value: unknown): value is string {
  return recordId(value) !== null;
}

/** The text id of a decimal id from an older bookmark (/app/invoice/42), or null. */
export function legacyRecordId(segment: string): string | null {
  if (!LEGACY.test(segment)) return null;
  let value = BigInt(segment), text = '';
  for (let index = 0; index < 13; index += 1) {
    text = ALPHABET[Number(value % 32n)] + text;
    value /= 32n;
  }
  return text;
}
