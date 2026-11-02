export const fieldTypes = new Set(['text', 'longtext', 'email', 'url', 'integer', 'decimal', 'currency', 'boolean', 'date', 'datetime', 'select', 'link',
  'phone', 'percent', 'time', 'duration', 'tags', 'reference'])
const identifier = /^[A-Za-z][A-Za-z0-9_]*$/

export function validateSchema(schema) {
  if (schema.version !== 1 || schema.ui_version !== 1 || !Array.isArray(schema.models)) {
    throw new Error('This renderer requires schema version 1 and UI version 1.')
  }
  const names = new Set()
  for (const model of schema.models) {
    if (!identifier.test(model.name) || names.has(model.name) || !model.label || !model.plural || !Array.isArray(model.fields) || !Array.isArray(model.list_fields)) throw new Error('Invalid model metadata.')
    names.add(model.name)
    const fields = new Set(['id'])
    for (const field of model.fields) {
      if (!identifier.test(field.name) || fields.has(field.name) || !fieldTypes.has(field.type)) throw new Error(`Unsupported or invalid field: ${field.name}.`)
      fields.add(field.name)
    }
    if (model.list_fields.some(name => !fields.has(name))) throw new Error('Invalid list metadata.')
  }
  for (const model of schema.models) {
    if (model.fields.some(field => field.type === 'link' && !names.has(field.target))) throw new Error('Unknown relationship target.')
  }
  return schema.models
}

export const visibleFields = model => model.fields.filter(field => !field.hidden)
export const idField = { name: 'id', label: 'ID', type: 'integer', required: true }
export const fieldFor = (model, name) => name === 'id' ? idField : model.fields.find(field => field.name === name)
export const recordLabel = (model, record) => model.title_field === 'id' ? `#${record.id}` : String(record[model.title_field] || `#${record.id}`)
export function formatValue(field, value, labels = {}) {
  if (value === null || value === undefined || value === '') return 'Not set'
  if (field.type === 'integer' && typeof value === 'number' && !Number.isSafeInteger(value)) return 'Outside browser integer range'
  if (field.name === 'id') return `#${value}`
  if (field.type === 'boolean') return value ? 'Yes' : 'No'
  if (field.type === 'link') return labels[`${field.target}:${value}`] || `#${value}`
  return String(value)
}
export function inputValue(field, value) {
  if (value === '' || value === undefined || value === null) return null
  if (field.type === 'integer' || field.type === 'link') {
    if (!/^-?\d+$/.test(String(value)) || !Number.isSafeInteger(Number(value))) throw new Error('Use a whole number within the supported browser range.')
    return Number(value)
  }
  return value
}
