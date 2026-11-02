import { afterEach, describe, expect, it } from 'vitest';
import { DEFAULT_REGION, setRegion } from '../model/format';
import { clockText, dateOrder, fullYear, parseClock, parseDay, parseDayTime, parseLength } from '../ui/dateparse';
import { KINDS } from '../ui/kinds';
import { BaseField, localField } from '../ui/fields';
import { DateRangePanel } from '../ui/pickers';
import { change, cleanup, key, render } from './ui-helpers';

afterEach(() => { setRegion(DEFAULT_REGION); cleanup(); });

// Today is Monday 02-11-2026 in every table below.
const today = '2026-11-02';
const day = (text: string, order: 'DMY' | 'MDY' | 'YMD' = 'DMY') => parseDay(text, { today, order });

describe('typed dates, day first', () => {
  it.each([
    ['12062027', '2027-06-12'], ['120627', '2027-06-12'], ['1206', '2026-06-12'], ['12', '2026-11-12'], ['5', '2026-11-05'],
    ['12/06/27', '2027-06-12'], ['12/06/2027', '2027-06-12'], ['12-06-27', '2027-06-12'], ['12.06.2027', '2027-06-12'],
    ['12 06 2027', '2027-06-12'], ['1/6/27', '2027-06-01'], ['1-6-2027', '2027-06-01'], ['12/06', '2026-06-12'], ['12-6', '2026-06-12'],
    ['2027-06-12', '2027-06-12'], ['12 Jun 2027', '2027-06-12'], ['12 June 27', '2027-06-12'], ['Jun 12 2027', '2027-06-12'],
    ['12 jun', '2026-06-12'], ['29022028', '2028-02-29'], ['  12/06/27  ', '2027-06-12'],
    ['today', today], ['t', today], ['T', today], ['tomorrow', '2026-11-03'], ['yesterday', '2026-11-01'],
    ['+3', '2026-11-05'], ['-3', '2026-10-30'], ['+60', '2027-01-01'],
  ])('%s is %s', (text, expected) => expect(day(text)).toBe(expected));

  it.each([
    '31022027', '310227', '31/02/2027', '29/02/2027', '32/01/2027', '12/13/2027', '00/06/2027', '12/00/2027', '0', '32', '3106',
    '123', '12345', '1234567', '123456789', '12/06/202', '12/06/20271', '12//06', 'next week', '12 Foo 2027', '12 Junes 2027',
    '12/06/27/1', '', '   ', '+', '12a',
  ])('%s is not read', text => expect(day(text)).toBeNull());
});

describe('typed dates in other orders', () => {
  it.each([
    ['06122027', 'MDY', '2027-06-12'], ['061227', 'MDY', '2027-06-12'], ['0612', 'MDY', '2026-06-12'], ['6/12/27', 'MDY', '2027-06-12'],
    ['06/12', 'MDY', '2026-06-12'], ['20270612', 'YMD', '2027-06-12'], ['270612', 'YMD', '2027-06-12'], ['27/06/12', 'YMD', '2027-06-12'],
    ['2027-06-12', 'MDY', '2027-06-12'], ['13/06/2027', 'MDY', null],
  ] as const)('%s in %s is %s', (text, order, expected) => expect(day(text, order)).toBe(expected));

  it('follows the person\'s date format from Settings', () => {
    expect(dateOrder('dd-MM-yyyy')).toBe('DMY');
    expect(dateOrder('d MMM yyyy')).toBe('DMY');
    expect(dateOrder('MM/dd/yyyy')).toBe('MDY');
    expect(dateOrder('yyyy-MM-dd')).toBe('YMD');
    setRegion({ date: 'MM/dd/yyyy' });
    expect(parseDay('06/12/2027')).toBe('2027-06-12');
    setRegion({ date: 'dd-MM-yyyy' });
    expect(parseDay('06/12/2027')).toBe('2027-12-06');
  });
});

describe('two-digit years', () => {
  it.each([[27, 2026, 2027], [0, 2026, 2000], [76, 2026, 2076], [77, 2026, 1977], [99, 2026, 1999], [26, 2026, 2026], [10, 2095, 2110], [60, 2005, 1960], [55, 2005, 2055], [56, 2005, 1956]])(
    '%i in %i is %i', (short, year, expected) => expect(fullYear(short, year)).toBe(expected));
});

describe('typed times', () => {
  it.each([
    ['1235', '12:35:00'], ['120627', '12:06:27'], ['935', '09:35:00'], ['9', '09:00:00'], ['0', '00:00:00'], ['23', '23:00:00'],
    ['12:35', '12:35:00'], ['12.35', '12:35:00'], ['14:05:30', '14:05:30'], ['9:05', '09:05:00'], ['0000', '00:00:00'],
    ['12.35pm', '12:35:00'], ['12:35 pm', '12:35:00'], ['9am', '09:00:00'], ['9 AM', '09:00:00'], ['2:05 p.m.', '14:05:00'],
    ['12am', '00:00:00'], ['12pm', '12:00:00'], ['12:30am', '00:30:00'], ['1pm', '13:00:00'], ['1159p', '23:59:00'],
  ])('%s is %s', (text, expected) => expect(parseClock(text) && clockText(parseClock(text)!)).toBe(expected));

  it.each(['9.5', '24', '2400', '1260', '12345', '1234567', '13pm', '0am', '25:00', '12:60', '12:30:60', '12:3', 'noon', '', '12:35 xm'])(
    '%s is not read', text => expect(parseClock(text)).toBeNull());
});

describe('typed date-times', () => {
  it.each([
    ['12062027 1235', { day: '2027-06-12', hours: 12, minutes: 35 }], ['12/06/27 9am', { day: '2027-06-12', hours: 9, minutes: 0 }],
    ['12 06 2027 12:35 pm', { day: '2027-06-12', hours: 12, minutes: 35 }], ['2027-06-12T09:30', { day: '2027-06-12', hours: 9, minutes: 30 }],
    ['today 14:30', { day: today, hours: 14, minutes: 30 }], ['12-06-2027, 935', { day: '2027-06-12', hours: 9, minutes: 35 }],
    ['12 06 2027', { day: '2027-06-12', hours: 0, minutes: 0 }], ['120627', { day: '2027-06-12', hours: 0, minutes: 0 }],
    ['12/06 1235', { day: '2026-06-12', hours: 12, minutes: 35 }],
  ])('%s', (text, expected) => expect(parseDayTime(text, { today, order: 'DMY' })).toMatchObject(expected));

  it.each(['31/02/2027 10:00', '12/06/2027 25:00', '12/06/2027 9.5', 'soon'])('%s is not read', text => expect(parseDayTime(text, { today, order: 'DMY' })).toBeNull());
});

describe('typed lengths of time', () => {
  it.each([
    // A whole number without a unit is minutes.
    ['90', 5400], ['0', 0], ['5', 300],
    // h:mm and h:mm:ss, with : or a dot followed by exactly two digits from 00 to 59.
    ['1:30', 5400], ['1.30', 5400], ['1.05', 3900], ['0.45', 2700], ['10:00', 36000], ['1:30:20', 5420], ['1.30.20', 5420],
    // Units, with or without spaces, short or in words.
    ['1h 30m', 5400], ['1h30m', 5400], ['30m', 1800], ['20s', 20], ['2d 4h', 187200], ['1 h 30 min', 5400], ['1hr 30mins', 5400],
    ['2 hours', 7200], ['45 sec', 45], ['1 hour 5 minutes 10 seconds', 3910], ['3 days', 259200],
    // A fraction of a unit: an h number is always a decimal fraction of an hour.
    ['1.5h', 5400], ['2.25h', 8100], ['1.30h', 4680], ['1.62h', 5832], ['1.5m', 90], ['0.5 hours', 1800], ['1.5h 10m', 6000],
  ])('%s is %i seconds', (text, seconds) => expect(parseLength(text)).toBe(seconds));

  it.each([
    // A dot without a unit is only hours.minutes with two minute digits.
    '1.5', '1.62', '1.300', '1.5.', '.30', '1.', '1:75', '1:5', '1.30.2', '1:30.20',
    // A fraction that is not whole seconds, a unit twice or an unknown word.
    '1.33333h', '0.001m', '1.5s', '1h 2h', '5 weeks', 'h', 'soon', '-5', '', '1h-30m',
  ])('%s is not read', text => expect(parseLength(text)).toBeNull());

  it('says how to type a duration', () => {
    expect(() => KINDS.duration.parse!('1.62', { name: 'd', type: 'duration', label: 'D', required: false, read_only: false }))
      .toThrow('Type a length of time such as 1h 30m, 1:30, 1.30 or 90m');
  });
});

describe('date fields read typed text when they commit', () => {
  it('normalises on blur and Enter', () => {
    const field = localField({ name: 'due', type: 'date', label: 'Due' }, null);
    const host = render(() => BaseField({ field }));
    const input = host.querySelector('input')!;
    change(input, '120627');
    expect(field.value.peek()).toBe('2027-06-12');
    expect(input.value).toBe('12-06-2027');
    input.value = '1/6/27';
    key(input, 'Enter');
    expect(input.value).toBe('01-06-2027');
  });

  it.each([
    ['date', '31-02-2027', '2027-06-01', '01-06-2027', "Couldn't read 31-02-2027 as a date"],
    ['date', 'abc', '2027-06-01', '01-06-2027', "Couldn't read abc as a date"],
    ['datetime', '12/06/2027 25:00', '2027-06-12T05:00:00Z', '12-06-2027 09:00', "Couldn't read 12/06/2027 25:00 as a date and time"],
    ['time', '9.5', '09:00:00', '09:00', "Couldn't read 9.5 as a time"],
    ['duration', '1.62', 'PT1H', '1h', "Couldn't read 1.62 as a duration"],
    ['date', 'abc', null, '', "Couldn't read abc as a date"],
  ])('keeps a %s it cannot read (%s) as it was, says why, and clears the message on the next edit', (type, typed, before, shown, message) => {
    const field = localField({ name: 'value', type, label: 'Value' }, before);
    const host = render(() => BaseField({ field }));
    const input = host.querySelector('input')!;
    input.value = typed;
    key(input, 'Enter');
    expect([input.value, field.value.peek(), field.error.peek()]).toEqual([shown, before, message]);
    expect(host.querySelector('.err')!.textContent).toBe(message);
    change(input, typed);
    expect([input.value, field.value.peek(), field.error.peek()]).toEqual([shown, before, message]);
    input.value = '1';
    input.dispatchEvent(new Event('input'));
    expect(field.error.peek()).toBeNull();
  });

  it('keeps a required field\'s value too, and the required rule still applies when it is emptied', () => {
    const field = localField({ name: 'due', type: 'date', label: 'Due', required: true }, '2027-06-01');
    const input = render(() => BaseField({ field })).querySelector('input')!;
    change(input, 'abc');
    expect([input.value, field.value.peek(), field.error.peek()]).toEqual(['01-06-2027', '2027-06-01', "Couldn't read abc as a date"]);
    change(input, '');
    expect(field.error.peek()).toBe('Due is required');
  });

  it('reads times and date-times the same way', () => {
    const time = localField({ name: 'at', type: 'time', label: 'At' }, null), when = localField({ name: 'when', type: 'datetime', label: 'When' }, null);
    const host = render(() => [BaseField({ field: time }), BaseField({ field: when })]);
    change(host.querySelector<HTMLInputElement>('[data-field="at"] input')!, '935');
    expect(time.value.peek()).toBe('09:35:00');
    expect(host.querySelector<HTMLInputElement>('[data-field="at"] input')!.value).toBe('09:35');
    change(host.querySelector<HTMLInputElement>('[data-field="when"] input')!, '12062027 1235');
    // 12:35 in GST is 08:35 UTC.
    expect(when.value.peek()).toBe('2027-06-12T08:35:00Z');
    expect(host.querySelector<HTMLInputElement>('[data-field="when"] input')!.value).toBe('12-06-2027 12:35');
    expect(KINDS.duration.parse!('90m', { name: 'd', type: 'duration', label: 'D', required: false, read_only: false })).toBe('PT1H30M');
  });
});

describe('typed range ends', () => {
  it('reads both ends, swaps them when needed and keeps an end it cannot read', () => {
    const applied: unknown[] = [];
    render(() => DateRangePanel({ value: () => null, onApply: range => applied.push(range) }));
    const [first, last] = document.querySelectorAll<HTMLInputElement>('.range-typed input');
    change(first, '1/6/27');
    change(last, '120627');
    expect([first.value, last.value]).toEqual(['01-06-2027', '12-06-2027']);
    change(last, '31/02/2027');
    expect(last.value).toBe('12-06-2027');
    expect(document.querySelector('.range-note')!.textContent).toBe("Couldn't read 31/02/2027 as a date");
    change(last, '20/05/27');
    expect([first.value, last.value]).toEqual(['20-05-2027', '01-06-2027']);
    [...document.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === 'Apply')!.click();
    expect(applied).toEqual([['2027-05-20', '2027-06-01']]);
  });
});
