import type { FieldSchema, ModelSchema } from './types';
import { evaluate } from './StateStore';
import { DecimalValue, MISSING_INPUT } from './DecimalValue';

export function documentRead(model: ModelSchema, values: any, collections: any, models: ModelSchema[], parent?: {model: ModelSchema; values: any}) {
  const scalar = (definition: ModelSchema, record: any, name: string) => {
    const field = definition.fields.find(field => field.name === name);
    if (!field) throw new Error('Calculation input is unavailable');
    const value = record[name];
    return value == null || value === '' ? null : ['decimal', 'currency', 'integer'].includes(field.type) ? DecimalValue.from(value) : value;
  };
  return (path: string): any => {
    const parts = path.split('.');
    if (parts.length === 1) return model.children?.some(child => child.name === parts[0]) ? (collections[parts[0]] || []) : scalar(model, values, parts[0]);
    if (parts[0] === 'parent' && parent) return scalar(parent.model, parent.values, parts[1]);
    if (parts[0] === 'source' && model.source_fields) return scalar({...model, fields: model.source_fields}, values._source || parent?.values._source || {}, parts[1]);
    const source = model.sources?.[parts[0]]?.[parts[1]];
    if (parts.length === 2 && source) {
      const value = values._sources?.[parts[0]]?.[parts[1]];
      return value == null ? null : ['decimal', 'currency', 'integer'].includes(source.type) ? DecimalValue.from(value) : value;
    }
    const table = model.children?.find(child => child.name === parts[0]);
    const child = models.find(item => item.name === table?.model);
    if (!child || parts.length !== 2) throw new Error('Calculation input is unavailable');
    return (collections[parts[0]] || []).map((row: any) => scalar(child, row, parts[1]));
  };
}
export function behaviorValue(expression: any, read: (path: string) => any) {
  return evaluate(expression, read);
}
export function documentValue(field: FieldSchema, value: any) {
  // Literal numbers (for example `0 if x else qty * rate`) are rounded like the server.
  if (typeof value === 'number' && ['decimal', 'currency', 'integer'].includes(field.type)) value = DecimalValue.from(value);
  if (!(value instanceof DecimalValue)) return value;
  if (field.type === 'integer') {
    const integer = Number(value.fixed(0));
    if (!Number.isSafeInteger(integer) || value.compare(integer) !== 0) throw new Error('Integer calculations require a whole browser-safe value');
    return integer;
  }
  return value.fixed(field.scale || 0);
}
export function fieldFilters(model: ModelSchema, field: FieldSchema, values: any, collections: any, models: ModelSchema[], parent?: {model: ModelSchema; values: any}) {
  const behavior = model.behavior?.[field.name];
  if (behavior?.filters_blocked) return null;
  if (!behavior?.filters) return {};
  try {
    const read = documentRead(model, values, collections, models, parent);
    const result: any = {};
    for (const [key, expression] of Object.entries(behavior.filters)) {
      const value = evaluate(expression, read);
      if (value == null || value === '') return null;
      // Whole values (such as Link IDs) stay numbers; fractions keep their exact digits.
      result[key] = value instanceof DecimalValue ? (value.scale === 0 && Number.isSafeInteger(Number(value.coefficient)) ? Number(value.coefficient) : value.fixed(value.scale)) : value;
    }
    return result;
  } catch { return null; }
}
export function calculateDocument(model: ModelSchema, models: ModelSchema[], values: any, collections: any) {
  const next = {...values}, rows = Object.fromEntries(Object.entries(collections).map(([name, items]) => [name, (items as any[]).map(item => ({...item}))]));
  const errors: Record<string, string> = {};
  const compute = (definition: ModelSchema, row: any, name: string, prefix: string, parent?: {model: ModelSchema; values: any}) => {
    const field = definition.fields.find(field => field.name === name);
    const expression = definition.behavior?.[name]?.compute;
    if (!field || !expression) return;
    try {
      const value = evaluate(expression, documentRead(definition, row, rows, models, parent));
      row[name] = documentValue(field, value);
    } catch (error: any) { row[name] = null; if (error.message !== MISSING_INPUT) errors[prefix + name] = error.message; }
  };
  const order = model.order?.length ? model.order : model.fields.map(field => field.name);
  for (const key of order) {
    const parts = key.split('.');
    if (parts.length === 1) compute(model, next, key, '');
    else {
      const table = model.children?.find(child => child.name === parts[0]);
      const definition = models.find(item => item.name === table?.model);
      if (definition) for (const row of rows[parts[0]] || []) compute(definition, row, parts[1], parts[0] + '.' + (row._key || row.id) + '.', {model, values: next});
    }
  }
  return {values: next, children: rows, errors};
}
