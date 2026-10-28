export class ApiError extends Error {
  constructor(message, fields = {}) {
    super(message)
    this.fields = fields
  }
}

export async function request(path, options = {}) {
  let response
  try {
    response = await fetch(path, options)
  } catch {
    throw new ApiError('Unable to reach the server. Check the connection and try again.')
  }
  if (response.status === 204 && response.ok) return null
  let data
  try {
    data = await response.json()
  } catch {
    throw new ApiError('The server returned an unreadable response.')
  }
  if (!response.ok) throw new ApiError(data.error || 'The request failed.', data.fields)
  return data
}

export const getSchema = () => request('/api/schema')
export const listRecords = (model, limit = 20, offset = 0, filters = {}, orderBy = '-id', search = '') => {
  const query = new URLSearchParams({ limit, offset })
  if (Object.keys(filters).length) query.set('filters', JSON.stringify(filters))
  if (search) query.set('q', search)
  if (orderBy !== '-id') query.set('order_by', orderBy)
  return request(`/api/${model}?${query}`)
}
export const createRecord = (model, data) => request(`/api/${model}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
})

function recordPath(model, id) {
  if (!Number.isSafeInteger(id) || id < 1) throw new ApiError('This record ID is outside the supported browser range. Use the Python API.')
  return `/api/${model}/${id}`
}

export const updateRecord = (model, id, data) => request(recordPath(model, id), {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
})
export const deleteRecord = (model, id) => request(recordPath(model, id), { method: 'DELETE' })

export const getRecord = (model, id) => request(recordPath(model, id))
