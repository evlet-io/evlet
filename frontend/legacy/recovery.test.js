import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import App from './App.vue'
import RecordExtras from './RecordExtras.vue'
import { request } from './api.js'
import schema from './test-business-schema.json'

let wrapper
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
afterEach(() => { wrapper?.unmount(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('refreshes records after recovering from an unavailable initial event cursor', async () => {
  vi.useFakeTimers()
  let cursorRequests = 0, name = 'Original'
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path === '/api/schema') return response(schema)
    if (path.startsWith('/api/_events')) {
      if (path === '/api/_events' && ++cursorRequests === 1) return response({ error: 'Database temporarily unavailable' }, 503)
      return response({ items: [], cursor: 'a'.repeat(48), reset: false, more: false })
    }
    return response({ items: [{ id: 1, name, active: true }], total: 1 })
  }))
  wrapper = mount(App, { global: { plugins: [PrimeVue] } })
  await flushPromises()
  expect(wrapper.text()).toContain('Original')
  name = 'Updated during reconnect'
  await vi.advanceTimersByTimeAsync(10000); await flushPromises()
  expect(cursorRequests).toBe(2)
  expect(wrapper.text()).toContain('Updated during reconnect')
})

it('resumes job tracking after a temporary status failure', async () => {
  vi.useFakeTimers()
  let statusRequests = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: 7, state: 'queued' }, record: { id: 1 } }, 202)
    if (path === '/api/_jobs/7') {
      if (++statusRequests === 1) return response({ error: 'Database temporarily unavailable' }, 503)
      return response({ id: 7, state: 'succeeded' })
    }
    return response({ id: 1, name: 'Project', progress: 50 })
  }))
  wrapper = mount(RecordExtras, {
    props: { model: schema.models.find(model => model.name === 'project'), models: schema.models, record: { id: 1 } },
    global: { plugins: [PrimeVue] },
  })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'Recalculate progress').trigger('click')
  await flushPromises()
  await vi.advanceTimersByTimeAsync(60000); await flushPromises()
  expect(wrapper.text()).toContain('Background task: completed')
  expect(wrapper.emitted('updated').at(-1)[0].progress).toBe(50)
})

it.each(['network', 'unreadable 503', '429'])('retries a temporary %s response and clears the job error', async failure => {
  vi.useFakeTimers()
  let statusRequests = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: 7, state: 'queued' }, record: { id: 1 } }, 202)
    if (path === '/api/_jobs/7') {
      if (++statusRequests === 1) {
        if (failure === 'network') throw new TypeError('Network unavailable')
        if (failure === 'unreadable 503') return { status: 503, ok: false, json: async () => { throw new SyntaxError() } }
        return response({ error: 'Please slow down' }, 429)
      }
      return response({ id: 7, state: 'succeeded' })
    }
    return response({ id: 1, progress: 50 })
  }))
  wrapper = mount(RecordExtras, { props: { model: schema.models.find(model => model.name === 'project'), models: schema.models, record: { id: 1 } }, global: { plugins: [PrimeVue] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'Recalculate progress').trigger('click'); await flushPromises()
  await vi.advanceTimersByTimeAsync(500)
  expect(wrapper.text()).toContain('Checking again shortly.')
  expect(statusRequests).toBe(1)
  await vi.advanceTimersByTimeAsync(3000); await flushPromises()
  expect(wrapper.text()).toContain('Background task: completed')
  expect(wrapper.findAll('[role="alert"]')).toHaveLength(0)
  expect(wrapper.emitted('updated').at(-1)[0].progress).toBe(50)
  await vi.advanceTimersByTimeAsync(60000)
  expect(statusRequests).toBe(2)
})

it.each([401, 403, 404])('does not retry permanent job access failure %s', async status => {
  vi.useFakeTimers()
  let statusRequests = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: 7, state: 'queued' }, record: { id: 1 } }, 202)
    statusRequests++
    return response({ error: 'Job unavailable' }, status)
  }))
  wrapper = mount(RecordExtras, { props: { model: schema.models.find(model => model.name === 'project'), models: schema.models, record: { id: 1 } }, global: { plugins: [PrimeVue] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'Recalculate progress').trigger('click'); await flushPromises()
  await vi.advanceTimersByTimeAsync(60000)
  expect(statusRequests).toBe(1)
  expect(wrapper.text()).toContain('Job unavailable')
  expect(wrapper.text()).not.toContain('Checking again shortly.')
})

it.each(['503', 'body download'])('retries the completed record read after a temporary %s failure', async failure => {
  vi.useFakeTimers()
  let recordRequests = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: 7, state: 'queued' }, record: { id: 1 } }, 202)
    if (path === '/api/_jobs/7') return response({ id: 7, state: 'succeeded' })
    if (++recordRequests === 1) {
      if (failure === 'body download') return new Response(new ReadableStream({
        start(controller) { controller.error(new TypeError('Connection terminated while reading body')) },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      return response({ error: 'Temporarily unavailable' }, 503)
    }
    return response({ id: 1, progress: 50 })
  }))
  wrapper = mount(RecordExtras, { props: { model: schema.models.find(model => model.name === 'project'), models: schema.models, record: { id: 1 } }, global: { plugins: [PrimeVue] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'Recalculate progress').trigger('click'); await flushPromises()
  await vi.advanceTimersByTimeAsync(500)
  expect(wrapper.emitted('updated')).toHaveLength(1)
  await vi.advanceTimersByTimeAsync(3000)
  expect(wrapper.emitted('updated').at(-1)[0].progress).toBe(50)
  expect(wrapper.findAll('[role="alert"]')).toHaveLength(0)
})

it.each(['record', 'job', 'unmount'])('cancels a scheduled retry when changing %s', async change => {
  vi.useFakeTimers()
  let actions = 0, oldStatusRequests = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: ++actions, state: 'queued' }, record: { id: 1 } }, 202)
    if (path === '/api/_jobs/1') { oldStatusRequests++; return response({ error: 'Temporarily unavailable' }, 503) }
    return response({ id: 2, state: 'queued' })
  }))
  wrapper = mount(RecordExtras, { props: { model: schema.models.find(model => model.name === 'project'), models: schema.models, record: { id: 1 } }, global: { plugins: [PrimeVue] } })
  await flushPromises()
  const action = wrapper.findAll('button').find(button => button.text() === 'Recalculate progress')
  await action.trigger('click'); await flushPromises(); await vi.advanceTimersByTimeAsync(500)
  expect(wrapper.text()).toContain('Checking again shortly.')
  if (change === 'record') await wrapper.setProps({ record: { id: 2 } })
  if (change === 'job') { await action.trigger('click'); await flushPromises() }
  if (change === 'unmount') { wrapper.unmount(); wrapper = null }
  await vi.advanceTimersByTimeAsync(10000)
  expect(oldStatusRequests).toBe(1)
  if (wrapper) expect(wrapper.text()).not.toContain('Checking again shortly.')
})

it('resumes job tracking when the connection fails while reading a successful response body', async () => {
  vi.useFakeTimers()
  let statusRequests = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/attachments')) return response({ items: [] })
    if (path.includes('/actions/')) return response({ job: { id: 7, state: 'queued' }, record: { id: 1 } }, 202)
    if (path === '/api/_jobs/7') {
      if (++statusRequests === 1) return new Response(new ReadableStream({
        start(controller) { controller.error(new TypeError('Connection terminated while reading body')) },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      return response({ id: 7, state: 'succeeded' })
    }
    return response({ id: 1, name: 'Project', progress: 50 })
  }))
  wrapper = mount(RecordExtras, {
    props: { model: schema.models.find(model => model.name === 'project'), models: schema.models, record: { id: 1 } },
    global: { plugins: [PrimeVue] },
  })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'Recalculate progress').trigger('click')
  await flushPromises()
  await vi.advanceTimersByTimeAsync(60000); await flushPromises()
  expect(wrapper.text()).toContain('Background task: completed')
  expect(statusRequests).toBe(2)
})

it.each([
  [200, 'TypeError', true],
  [200, 'AbortError', true],
  [200, 'SyntaxError', false],
  [403, 'TypeError', false],
  [503, 'SyntaxError', true],
])('classifies response-body failure %s/%s while retaining HTTP status', async (status, kind, retryable) => {
  vi.stubGlobal('fetch', vi.fn(async () => {
    if (kind === 'SyntaxError') return new Response('invalid JSON', { status })
    return new Response(new ReadableStream({
      start(controller) {
        controller.error(kind === 'TypeError' ? new TypeError('Body download failed') : new DOMException('Body download aborted', 'AbortError'))
      },
    }), { status })
  }))
  await expect(request('/api/_jobs/7')).rejects.toMatchObject({ status, retryable })
})
