import { afterEach, describe, expect, it } from 'vitest';
import { buildChanges } from '../model/changes';
import { backend, cleanup } from './ui-helpers';
import { id } from './server';

afterEach(cleanup);

describe('Table.restore and rows of rows', () => {
  it('brings back a removed saved row with its id and nested rows, and nothing is left to save', async () => {
    const { store } = backend();
    await store.open(id(1));
    const lines = store.table('lines'), first = lines.rows.peek()[0];
    lines.remove(first);
    expect(buildChanges(store.root)).toEqual({ children: { lines: { delete: [id(1)] } } });
    expect(store.field('total').value.peek()).toBe('297.50');
    lines.restore(first, 0);
    expect(lines.rows.peek()[0]).toBe(first);
    expect(first.table('parts').rows.peek()).toHaveLength(2);
    expect(store.field('total').value.peek()).toBe('300.00');
    expect(buildChanges(store.root)).toEqual({});
    expect(store.dirty.peek()).toBe(false);
  });

  it('brings back a removed new row, and refuses rows it did not remove', async () => {
    const { store } = backend();
    await store.open(id(1));
    const lines = store.table('lines');
    const row = lines.add({ qty: '2', rate: '10.00' });
    expect(store.field('total').value.peek()).toBe('320.00');
    lines.remove(row);
    expect(store.field('total').value.peek()).toBe('300.00');
    lines.restore(row, 120);
    expect(lines.rows.peek()[120]).toBe(row);
    expect(store.field('total').value.peek()).toBe('320.00');
    row.field('qty').set('3');
    expect(store.field('total').value.peek()).toBe('330.00');
    expect(() => lines.restore(lines.rows.peek()[0], 0)).toThrow('Only a row removed');
  });

  it('adds a row with rows of rows as new rows that take part in calculations', async () => {
    const { store } = backend();
    await store.open(id(1));
    const lines = store.table('lines');
    const row = lines.add({ qty: '1', rate: '5.00', children: { parts: [{ hours: '1.5' }, { hours: '2' }] } });
    expect(row.table('parts').rows.peek().map(part => [part.isNew, part.field('hours').value.peek()])).toEqual([[true, '1.5'], [true, '2']]);
    expect(store.field('total').value.peek()).toBe('305.00');
    row.field('qty').set('2');
    expect(store.field('total').value.peek()).toBe('310.00');
    const created = (buildChanges(store.root) as any).children.lines.create[0];
    expect(created.children.parts).toHaveLength(2);
  });
});
