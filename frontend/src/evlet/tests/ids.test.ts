import { describe, expect, it } from 'vitest';
import { isRecordId, legacyRecordId, recordId } from '../ids';

describe('record ids', () => {
  it('keeps 13-character ids as lowercase text and reads I, L and O as digits', () => {
    expect(recordId('01HC1SD9VC1YE')).toBe('01hc1sd9vc1ye');
    expect(recordId('0000000000O1I')).toBe('0000000000011');
    expect(recordId('000000000001L')).toBe('0000000000011');
    expect(isRecordId('7zzzzzzzzzzzz')).toBe(true);
  });

  it('refuses numbers, short or long text, u and ids above 63 bits', () => {
    for (const value of [42, '42', '000000000001', '00000000000001', '000000000001u', '8000000000000', null, undefined, 'new'])
      expect(recordId(value)).toBeNull();
  });

  it('turns a decimal bookmark id into the same record id the server shows', () => {
    expect(legacyRecordId('42')).toBe('000000000001a');
    expect(legacyRecordId('1')).toBe('0000000000001');
    // Decimal ids are shorter than 13 characters, so they never read as text ids.
    expect(legacyRecordId('1234567890123')).toBeNull();
    expect(legacyRecordId('999999999999')).toBe('00000x3aaa3zz');
    expect(legacyRecordId('042')).toBeNull();
    expect(legacyRecordId('01hc1sd9vc1ye')).toBeNull();
  });
});
