import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import SessionApp from './SessionApp.vue'
import Accounts from './Accounts.vue'
import App from './App.vue'
import RecordForm from './RecordForm.vue'
import RecordExtras from './RecordExtras.vue'
import { setCsrf } from './api.js'
import schema from './test-business-schema.json'

let wrapper
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
const mountComponent = (component, props = {}) => mount(component, { props, global: { plugins: [PrimeVue] } })
const button = label => wrapper.findAll('button').find(item => item.text().trim() === label)
afterEach(() => { wrapper?.unmount(); setCsrf(''); vi.unstubAllGlobals() })

it('sets up a local account, signs in, sends request protection, and signs out', async () => {
  const fetch = vi.fn(async (path, options = {}) => {
    if (path === '/api/auth/session') return response({ user: null, setup: true })
    if (path === '/api/auth/setup') return response({ created: true }, 201)
    if (path === '/api/auth/login') return response({ user: { id: 1, username: 'owner', role: 'admin' }, csrf: 'test-token' })
    if (path === '/api/auth/logout') return response(null, 204)
    if (path === '/api/schema') return response(schema)
    return response({ items: [], total: 0 })
  })
  vi.stubGlobal('fetch', fetch)
  wrapper = mountComponent(SessionApp)
  await flushPromises()
  expect(wrapper.text()).toContain('Set up your Playground')
  await wrapper.find('#username').setValue('owner')
  await wrapper.find('#password').setValue('long account password')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(wrapper.text()).toContain('owner · admin')
  expect(fetch.mock.calls.find(([path]) => path === '/api/auth/setup')[1].headers['X-Evlet-Request']).toBe('1')
  await button('Sign out').trigger('click'); await flushPromises()
  expect(fetch.mock.calls.find(([path]) => path === '/api/auth/logout')[1].headers['X-CSRF-Token']).toBe('test-token')
  expect(wrapper.find('#password').element.value).toBe('')
  expect(wrapper.text()).toContain('Sign in to Evlet')
})

it('clears records and the form after a session expires', async () => {
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path === '/api/auth/session') return response({ user: { id: 1, username: 'viewer', role: 'viewer' }, csrf: 'token' })
    if (path === '/api/schema') return response(schema)
    return response({ error: 'Sign in to continue' }, 401)
  }))
  wrapper = mountComponent(SessionApp); await flushPromises()
  expect(wrapper.text()).toContain('Your session ended')
  expect(wrapper.findComponent(App).exists()).toBe(false)
})

it('hides mutation controls for viewers and child models from navigation', async () => {
  const readonly = structuredClone(schema)
  for (const model of readonly.models) { model.permissions = { create: false, update: false, delete: false }; model.actions = [] }
  vi.stubGlobal('fetch', vi.fn(async path => path === '/api/schema' ? response(readonly) : response({ items: [{ id: 1, name: 'Read only' }], total: 1 })))
  wrapper = mountComponent(App); await flushPromises()
  expect(wrapper.find('nav').text()).not.toContain('Project tasks')
  expect(wrapper.findAll('button').some(item => item.text() === 'Edit')).toBe(false)
  expect(wrapper.findAll('button').some(item => item.text() === 'Delete')).toBe(false)
  expect(wrapper.findComponent(RecordForm).exists()).toBe(false)
})

it('renders grouped forms and saves child rows with the parent', async () => {
  const project = schema.models.find(model => model.name === 'project')
  wrapper = mountComponent(RecordForm, { model: project, models: schema.models, resetKey: 0 })
  expect(wrapper.findAll('.field-group h3').map(item => item.text())).toEqual(['Project', 'Planning', 'Notes'])
  await wrapper.find('#name').setValue('Build')
  await button('Add tasks row').trigger('click'); await flushPromises()
  await wrapper.find('#tasks-0-title').setValue('Test task')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')[0][0].children).toEqual({ tasks: [{ title: 'Test task', done: false }] })
  expect(wrapper.emitted('save')[0][0]).not.toHaveProperty('status')
})

it('omits unchanged children on parent edits and includes explicit row removals', async () => {
  const project = schema.models.find(model => model.name === 'project')
  wrapper = mountComponent(RecordForm, { model: project, models: schema.models, record: { id: 4, name: 'Project', children: { tasks: [{ id: 9, title: 'Keep', done: false }] } } })
  await wrapper.find('#name').setValue('Rename')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')[0][0]).toEqual({ name: 'Rename' })
  await button('Remove row').trigger('click')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')[1][0].children).toEqual({ tasks: [] })
})

it('shows action failures and refreshes the record after a successful action', async () => {
  const model = schema.models.find(model => model.name === 'project')
  const fetch = vi.fn(async path => {
    if (path.endsWith('/actions/complete')) return response({ error: 'Complete every task' }, 422)
    if (path.endsWith('/actions/start')) return response({ id: 1, name: 'Build', status: 'active' })
    return response({ items: [{ id: 3, name: 'notes.txt', size: 8 }] })
  })
  vi.stubGlobal('fetch', fetch)
  wrapper = mountComponent(RecordExtras, { model, models: schema.models, record: { id: 1, children: { tasks: [] } } }); await flushPromises()
  expect(wrapper.find('a').attributes('href')).toBe('/api/project/1/attachments/3')
  await button('Complete project').trigger('click'); await flushPromises()
  expect(wrapper.find('[role=alert]').text()).toContain('Complete every task')
  await button('Start project').trigger('click'); await flushPromises()
  expect(wrapper.emitted('updated')[0][0].status).toBe('active')
})

it('ignores old attachment responses after selecting a different record', async () => {
  let resolveFirst
  vi.stubGlobal('fetch', vi.fn(path => path.includes('/1/') ? new Promise(resolve => { resolveFirst = resolve }) : Promise.resolve(response({ items: [{ id: 2, name: 'Current.txt', size: 1 }] }))))
  const model = schema.models.find(model => model.name === 'project')
  wrapper = mountComponent(RecordExtras, { model, models: schema.models, record: { id: 1 } })
  await wrapper.setProps({ record: { id: 2 } }); await flushPromises()
  resolveFirst(response({ items: [{ id: 1, name: 'Old.txt', size: 1 }] })); await flushPromises()
  expect(wrapper.text()).toContain('Current.txt'); expect(wrapper.text()).not.toContain('Old.txt')
})

it('uploads encoded files and handles a failed removal without dropping the file', async () => {
  const fetch = vi.fn(async (path, options = {}) => {
    if (options.method === 'POST') return response({ id: 4, name: 'sample.txt', size: 2 }, 201)
    if (options.method === 'DELETE') return response({ error: 'Access changed' }, 403)
    return response({ items: [] })
  })
  vi.stubGlobal('fetch', fetch)
  const model = schema.models.find(model => model.name === 'project')
  wrapper = mountComponent(RecordExtras, { model, models: schema.models, record: { id: 1 } }); await flushPromises()
  const input = wrapper.find('input[type=file]')
  Object.defineProperty(input.element, 'files', { value: [{ name: 'sample.txt', size: 2, arrayBuffer: async () => new Uint8Array([65, 66]).buffer }], configurable: true })
  await input.trigger('change'); await flushPromises()
  expect(JSON.parse(fetch.mock.calls.find(([, opts]) => opts?.method === 'POST')[1].body)).toEqual({ name: 'sample.txt', content: 'QUI=' })
  await button('Remove').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('sample.txt'); expect(wrapper.text()).toContain('Access changed')
})


it('cancels an upload when its record changes while the file is being read', async () => {
  let resolveBytes
  const fetch = vi.fn(async () => response({ items: [] }))
  vi.stubGlobal('fetch', fetch)
  const model = schema.models.find(model => model.name === 'project')
  wrapper = mountComponent(RecordExtras, { model, models: schema.models, record: { id: 1 } }); await flushPromises()
  const input = wrapper.find('input[type=file]')
  Object.defineProperty(input.element, 'files', { value: [{ name: 'stale.txt', size: 1, arrayBuffer: () => new Promise(resolve => { resolveBytes = resolve }) }] })
  await input.trigger('change')
  await wrapper.setProps({ record: { id: 2 } }); await flushPromises()
  resolveBytes(new Uint8Array([65]).buffer); await flushPromises()
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
})


it('pages account management beyond the first hundred users', async () => {
  const fetch = vi.fn(async path => {
    const offset = Number(new URL(path, 'http://localhost').searchParams.get('offset'))
    const count = offset ? 1 : 100
    return response({ items: Array.from({ length: count }, (_, i) => ({ id: offset + i + 1, username: `user${offset + i + 1}`, role: 'viewer', active: true })), total: 101 })
  })
  vi.stubGlobal('fetch', fetch)
  wrapper = mountComponent(Accounts, { currentUser: { id: 1, role: 'admin' } }); await flushPromises()
  expect(wrapper.text()).toContain('101 accounts')
  await button('Next accounts').trigger('click'); await flushPromises()
  expect(fetch).toHaveBeenCalledWith('/api/auth/users?offset=100', {})
  expect(wrapper.find('tbody').text()).toContain('user101')
  expect(button('Next accounts').attributes('disabled')).toBeDefined()
  await button('Previous accounts').trigger('click'); await flushPromises()
  expect(wrapper.find('tbody tr').text()).toContain('user1')
})
