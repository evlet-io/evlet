import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { subscribeEvents } from './events.js'
import RecordExtras from './RecordExtras.vue'
import schema from './test-business-schema.json'

let close, wrapper
const response = data => ({ ok: true, status: 200, json: async () => data })
class Source {
  static instances = []
  constructor(url) { this.url = url; this.listeners = {}; this.readyState = 1; this.closed = false; Source.instances.push(this) }
  addEventListener(name, callback) { this.listeners[name] = callback }
  emit(name, data) { this.listeners[name]?.({ data: JSON.stringify(data) }) }
  close() { this.closed = true }
}
afterEach(() => { close?.(); wrapper?.unmount(); close = null; wrapper = null; vi.useRealTimers(); vi.unstubAllGlobals(); Source.instances = [] })

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

it('keeps job tracking across same-record updates and refreshes the completed result', async () => {
  vi.useFakeTimers()
  const project = schema.models.find(model => model.name === 'project')
  const fetch = vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.endsWith('/actions/refresh_progress')) return response({ job: { id: 7, state: 'queued' }, record: { id: 1, name: 'Project', progress: 0 } })
    if (path === '/api/_jobs/7') return response({ id: 7, state: 'succeeded' })
    return response({ id: 1, name: 'Project', progress: 50 })
  })
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(RecordExtras, { props: { model: project, models: schema.models, record: { id: 1, name: 'Project', progress: 0 } }, global: { plugins: [PrimeVue] } })
  await flushPromises()
  await wrapper.findAll('button').find(item => item.text() === 'Recalculate progress').trigger('click'); await flushPromises()
  await wrapper.setProps({ record: { id: 1, name: 'Project', progress: 0 } }); await flushPromises()
  expect(wrapper.text()).toContain('Background task: queued')
  await vi.advanceTimersByTimeAsync(500); await flushPromises()
  expect(wrapper.text()).toContain('Background task: completed')
  expect(wrapper.emitted('updated').at(-1)[0].progress).toBe(50)
})

it('drops a late job result after switching records', async () => {
  vi.useFakeTimers(); let resolve
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: 7, state: 'queued' }, record: { id: 1 } })
    if (path === '/api/_jobs/7') return new Promise(done => { resolve = done })
    return response({ id: 1, progress: 100 })
  }))
  const project = schema.models.find(model => model.name === 'project')
  wrapper = mount(RecordExtras, { props: { model: project, models: schema.models, record: { id: 1 } }, global: { plugins: [PrimeVue] } })
  await flushPromises(); await wrapper.findAll('button').find(item => item.text() === 'Recalculate progress').trigger('click'); await flushPromises()
  await vi.advanceTimersByTimeAsync(500)
  await wrapper.setProps({ record: { id: 2 } }); await flushPromises()
  resolve(response({ id: 7, state: 'succeeded' })); await flushPromises()
  expect(wrapper.emitted('updated')).toHaveLength(1)
  expect(wrapper.text()).not.toContain('Background task:')
})

it('refreshes changed rows without replacing unsaved form input', async () => {
  const { default: App } = await import('./App.vue')
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source)
  let changed = false
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path === '/api/schema') return response(schema)
    if (path === '/api/_events') return response({ items: [], cursor: '0' })
    if (path === '/api/customer/1') return response({ id: 1, name: 'Original', active: true })
    return response({ items: [{ id: 1, name: changed ? 'Changed elsewhere' : 'Original', active: true }], total: 1 })
  }))
  wrapper = mount(App, { global: { plugins: [PrimeVue] } }); await flushPromises()
  await wrapper.find('button[aria-label="Edit customer 1"]').trigger('click'); await flushPromises()
  await wrapper.find('#name').setValue('Unsaved work')
  changed = true
  Source.instances[0].emit('evlet', { items: [{ model: 'customer', id: 1 }], cursor: 'next', reset: false })
  await vi.advanceTimersByTimeAsync(100); await flushPromises()
  expect(wrapper.find('#name').element.value).toBe('Unsaved work')
  expect(wrapper.text()).toContain('Changed elsewhere')
})


it('does not replace a newer job with a late result from the previous job', async () => {
  vi.useFakeTimers(); let resolve, count = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: ++count, state: 'queued' }, record: { id: 1 } })
    if (path === '/api/_jobs/1') return new Promise(done => { resolve = done })
    return response({ id: 1, progress: 100 })
  }))
  const project = schema.models.find(model => model.name === 'project')
  wrapper = mount(RecordExtras, { props: { model: project, models: schema.models, record: { id: 1 } }, global: { plugins: [PrimeVue] } })
  await flushPromises()
  const action = wrapper.findAll('button').find(item => item.text() === 'Recalculate progress')
  await action.trigger('click'); await flushPromises(); await vi.advanceTimersByTimeAsync(500)
  await action.trigger('click'); await flushPromises()
  resolve(response({ id: 1, state: 'succeeded' })); await flushPromises()
  expect(wrapper.text()).toContain('Background task: queued')
  expect(wrapper.emitted('updated')).toHaveLength(2)
})

it('does not start event tracking when schema arrives after sign-out', async () => {
  const { default: App } = await import('./App.vue')
  vi.stubGlobal('EventSource', Source)
  let resolve; const fetch = vi.fn(() => new Promise(done => { resolve = done }))
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(App, { global: { plugins: [PrimeVue] } })
  wrapper.unmount(); wrapper = null
  resolve(response(schema)); await flushPromises()
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(Source.instances).toHaveLength(0)
})
