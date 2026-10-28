import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, deleteRecord, getRecord, request, updateRecord } from './api.js'

afterEach(() => vi.unstubAllGlobals())

it('preserves backend field errors', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ error: 'Validation failed', fields: { total: 'Use two decimals' } }) }))
  await expect(request('/api/invoice')).rejects.toMatchObject({ message: 'Validation failed', fields: { total: 'Use two decimals' } })
})

it('reports a readable error for a non-JSON server response', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => { throw new SyntaxError('HTML error') } }))
  await expect(request('/api/schema')).rejects.toThrow('unreadable response')
})

it('reports network failures without exposing transport internals', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('fetch failed')))
  await expect(request('/api/schema')).rejects.toBeInstanceOf(ApiError)
  await expect(request('/api/schema')).rejects.toThrow('Unable to reach the server')
})


it('refuses unsafe record IDs before sending a request to a rounded ID', () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  for (const operation of [getRecord, updateRecord, deleteRecord]) {
    expect(() => operation('customer', 9007199254740992, {})).toThrow('record ID')
  }
  expect(fetch).not.toHaveBeenCalled()
})
