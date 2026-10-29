let csrf = ''
export const setCsrf = value => { csrf = value }

export class ApiError extends Error {
  constructor(message, fields = {}, status = 0) {
    super(message)
    this.fields = fields
    this.status = status
  }
}

export async function request(path, options = {}) {
  let response
  try {
    const headers = { ...options.headers }
    if (options.method && options.method !== 'GET') { headers['X-Evlet-Request'] = '1'; if (csrf) headers['X-CSRF-Token'] = csrf }
    response = await fetch(path, Object.keys(headers).length ? { ...options, headers } : options)
  } catch {
    throw new ApiError('Unable to reach the server. Check the connection and try again.')
  }
  if (response.status === 401 && !path.endsWith('/login')) window.dispatchEvent(new Event('evlet-session-ended'))
  if (response.status === 204 && response.ok) return null
  let data
  try {
    data = await response.json()
  } catch {
    throw new ApiError('The server returned an unreadable response.', {}, response.status)
  }
  if (!response.ok) throw new ApiError(data.error || 'The request failed.', data.fields, response.status)
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

export const authRequest = (path, data, method = "POST") => request(`/api/auth/${path}`, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
export const recordRequest = (model, id, path, data) => request(`${recordPath(model, id)}/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
