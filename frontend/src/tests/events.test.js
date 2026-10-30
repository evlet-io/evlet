import { afterEach, expect, it, vi } from 'vitest'
import { subscribeEvents } from '../events.js'

let close
const response = data => ({ ok: true, status: 200, json: async () => data })
class Source {
  static instances = []
  constructor(url) { this.url = url; this.listeners = {}; this.readyState = 1; this.closed = false; Source.instances.push(this) }
  addEventListener(name, callback) { this.listeners[name] = callback }
  emit(name, data) { this.listeners[name]?.({ data: JSON.stringify(data) }) }
  close() { this.closed = true }
}
afterEach(() => { close?.(); close = null; vi.useRealTimers(); vi.unstubAllGlobals(); Source.instances = [] })

it('establishes a cursor, replays stream events, and falls back with that cursor', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source)
  const fetch = vi.fn(async path => response({ items: [], cursor: path.includes('cursor=second') ? 'third' : 'first', more: false }))
  vi.stubGlobal('fetch', fetch)
  const changed = vi.fn(); close = subscribeEvents(changed); await close.ready
  const source = Source.instances[0]
  expect(source.url).toBe('/api/_events/stream?cursor=first')
  source.emit('evlet', { items: [{ model: 'project', id: 1 }], cursor: 'second', reset: false })
  expect(changed).toHaveBeenCalledOnce()
  source.readyState = 2; source.onerror()
  await vi.advanceTimersByTimeAsync(1000)
  expect(source.closed).toBe(true)
  expect(fetch).toHaveBeenLastCalledWith('/api/_events?cursor=second', expect.anything())
  close(); await vi.advanceTimersByTimeAsync(10000)
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('clears the session when the stream reports revoked access', async () => {
  vi.stubGlobal('EventSource', Source)
  vi.stubGlobal('fetch', vi.fn(async () => response({ items: [], cursor: '0' })))
  const expired = vi.fn(); window.addEventListener('evlet-session-ended', expired)
  close = subscribeEvents(vi.fn()); await close.ready
  Source.instances[0].emit('unavailable', {})
  expect(expired).toHaveBeenCalledOnce(); expect(Source.instances[0].closed).toBe(true)
  window.removeEventListener('evlet-session-ended', expired)
})

it('does not reconnect or deliver a late initial request after unmount', async () => {
  vi.stubGlobal('EventSource', Source)
  let resolve; vi.stubGlobal('fetch', vi.fn(() => new Promise(done => { resolve = done })))
  const changed = vi.fn(); close = subscribeEvents(changed); close()
  resolve(response({ items: [], cursor: '0' })); await close.ready
  expect(Source.instances).toHaveLength(0); expect(changed).not.toHaveBeenCalled()
})

it('requests one refresh after cursor recovery and keeps the recovered cursor', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source)
  const fetch = vi.fn()
    .mockRejectedValueOnce(new TypeError('offline'))
    .mockRejectedValueOnce(new TypeError('offline'))
    .mockResolvedValue(response({ items: [], cursor: 'recovered', reset: false, more: false }))
  vi.stubGlobal('fetch', fetch)
  const changed = vi.fn(); close = subscribeEvents(changed); await close.ready
  await vi.advanceTimersByTimeAsync(6000)
  expect(changed).toHaveBeenCalledExactlyOnceWith({ items: [], cursor: 'recovered', reset: true, more: false })
  await vi.advanceTimersByTimeAsync(3000)
  expect(fetch).toHaveBeenLastCalledWith('/api/_events?cursor=recovered', expect.anything())
  expect(changed).toHaveBeenCalledOnce()
})
