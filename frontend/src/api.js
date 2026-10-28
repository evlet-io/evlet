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
export const listRecords = (model, limit = 20, offset = 0) =>
  request(`/api/${model}?limit=${limit}&offset=${offset}`)
export const createRecord = (model, data) => request(`/api/${model}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
})
