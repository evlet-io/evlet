import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import Select from 'primevue/select'
import App from './App.vue'

let wrapper
afterEach(() => {
  wrapper?.unmount()
  vi.unstubAllGlobals()
})

function response(data, status = 200) {
  return { ok: status < 400, status, json: async () => data }
}

function setup({ customers = [], invoices = [], payments = [], intercept } = {}) {
  const records = { customer: customers, invoice: invoices, payment: payments }
  const fetch = vi.fn(async (path, options = {}) => {
    const override = intercept?.(path, options)
    if (override) return override
    if (path === '/api/schema') return response({ version: 1, models: Object.keys(records).map(name => ({ name })) })
    const url = new URL(path, 'http://localhost')
    const model = url.pathname.split('/')[2]
    const id = Number(url.pathname.split('/')[3])
    if (options.method === 'PATCH') {
      const record = records[model].find(item => item.id === id)
      Object.assign(record, JSON.parse(options.body))
      return response(record)
    }
    if (options.method === 'DELETE') {
      records[model] = records[model].filter(item => item.id !== id)
      return response(null, 204)
    }
    if (options.method === 'POST') {
      const record = { id: records[model].length + 1, ...JSON.parse(options.body) }
      records[model].unshift(record)
      return response(record, 201)
    }
    const limit = Number(url.searchParams.get('limit'))
    const offset = Number(url.searchParams.get('offset'))
    const filters = JSON.parse(url.searchParams.get('filters') || '{}')
    let filtered = records[model].filter(item => Object.entries(filters).every(([key, value]) => String(item[key.split('__')[0]]).toLowerCase().includes(value.toLowerCase())))
    if (url.searchParams.get('order_by') === 'name') filtered = [...filtered].sort((a, b) => a.name.localeCompare(b.name))
    return response({ items: filtered.slice(offset, offset + limit), limit, offset })
  })
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(App, { global: { plugins: [PrimeVue] } })
  return { fetch, records }
}

const button = label => wrapper.findAll('button').find(item => item.text().trim() === label)

describe('Playground with real PrimeVue components', () => {
  it('loads metadata and renders server records', async () => {
    const { fetch } = setup({ customers: [{ id: 1, name: 'Acme', email: 'hello@example.com' }] })
    await flushPromises()
    expect(fetch).toHaveBeenCalledWith('/api/schema', {})
    expect(wrapper.find('h1').text()).toBe('Customers')
    expect(wrapper.find('table').text()).toContain('Acme')
    expect(wrapper.find('table').text()).toContain('hello@example.com')
    expect(wrapper.find('input#name').attributes('required')).toBeDefined()
  })

  it('shows an empty state, creates a customer, refreshes records, and clears the form', async () => {
    const { records, fetch } = setup()
    await flushPromises()
    expect(wrapper.text()).toContain('Your first customer starts here.')
    await wrapper.find('#name').setValue('A new studio')
    await wrapper.find('#email').setValue('studio@example.com')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(records.customer[0].name).toBe('A new studio')
    expect(wrapper.find('table').text()).toContain('A new studio')
    expect(wrapper.find('#name').element.value).toBe('')
    expect(wrapper.text()).toContain('Customer created.')
    const call = fetch.mock.calls.find(([, options]) => options.method === 'POST')
    expect(call[1].headers['Content-Type']).toBe('application/json')
  })

  it('creates linked invoices with exact decimal strings', async () => {
    const { records } = setup({ customers: [{ id: 1, name: 'Acme' }] })
    await flushPromises()
    await wrapper.findAll('nav button')[1].trigger('click')
    await flushPromises()
    const select = wrapper.findComponent(Select)
    expect(select.props('options')).toEqual([{ id: 1, name: 'Acme' }])
    expect(wrapper.find('[role="combobox"]').attributes('aria-labelledby')).toBe('customer-label')
    expect(wrapper.find('#customer-label').text()).toContain('Customer')
    select.vm.$emit('update:modelValue', 1)
    await wrapper.find('#reference').setValue('INV-001')
    await wrapper.find('#total').setValue('123.45')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(records.invoice[0]).toMatchObject({ customer: 1, reference: 'INV-001', total: '123.45' })
    expect(wrapper.find('table').text()).toContain('Acme')
    expect(wrapper.find('table').text()).toContain('123.45')
  })

  it('creates a payment linked to an existing invoice', async () => {
    const { records } = setup({ invoices: [{ id: 7, reference: 'INV-007', total: '10.00', customer: 1 }] })
    await flushPromises()
    await wrapper.findAll('nav button')[2].trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="combobox"]').attributes('aria-labelledby')).toBe('invoice-label')
    wrapper.findComponent(Select).vm.$emit('update:modelValue', 7)
    await wrapper.find('#reference').setValue('PAY-001')
    await wrapper.find('#amount').setValue('10.00')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(records.payment[0]).toMatchObject({ invoice: 7, reference: 'PAY-001', amount: '10.00' })
    expect(wrapper.find('table').text()).toContain('INV-007')
  })

  it('retains entered values and displays backend field validation', async () => {
    setup({ intercept: (path, options) => options.method === 'POST'
      ? response({ error: 'Validation failed', fields: { name: 'This field must not be blank' } }, 422)
      : null })
    await flushPromises()
    await wrapper.find('#name').setValue('  ')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('#name-error').text()).toBe('This field must not be blank')
    expect(wrapper.find('#name').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#name').element.value).toBe('  ')
    expect(wrapper.text()).not.toContain('Customer created.')
  })

  it('recovers from a failed connection using Try again', async () => {
    let fail = true
    setup({ intercept: () => fail ? Promise.reject(new TypeError('offline')) : null })
    await flushPromises()
    expect(wrapper.text()).toContain('Unable to reach the server')
    expect(button('Create customer').attributes('disabled')).toBeDefined()
    fail = false
    await button('Try again').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('Unable to reach the server')
    expect(button('Create customer').attributes('disabled')).toBeUndefined()
  })

  it('requests bounded server pagination', async () => {
    const { fetch } = setup({ customers: Array.from({ length: 21 }, (_, i) => ({ id: i+1, name: `Customer ${i+1}` })) })
    await flushPromises()
    expect(wrapper.text()).toContain('Page 1 · 20 records')
    await button('Next').trigger('click')
    await flushPromises()
    expect(fetch).toHaveBeenCalledWith('/api/customer?limit=20&offset=20', {})
    expect(wrapper.find('table').text()).toContain('Customer 21')
    expect(wrapper.text()).toContain('Page 2 · 1 records')
    expect(button('Next').attributes('disabled')).toBeDefined()
    await button('Previous').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Page 1 · 20 records')
  })

  it('prevents duplicate writes and navigation while saving', async () => {
    let finish
    const pending = new Promise(resolve => { finish = resolve })
    const { fetch } = setup({ intercept: (path, options) => options.method === 'POST' ? pending : null })
    await flushPromises()
    await wrapper.find('#name').setValue('Acme')
    await wrapper.find('form').trigger('submit')
    await wrapper.find('form').trigger('submit')
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
    expect(wrapper.find('nav button').attributes('disabled')).toBeDefined()
    finish(response({ id: 1, name: 'Acme' }, 201))
    await flushPromises()
    expect(wrapper.find('nav button').attributes('disabled')).toBeUndefined()
  })

  it('rejects incompatible metadata before enabling writes', async () => {
    setup({ intercept: path => path === '/api/schema' ? response({ version: 9, models: [] }) : null })
    await flushPromises()
    expect(wrapper.text()).toContain('schema version 1')
    expect(wrapper.find('fieldset').attributes('disabled')).toBeDefined()
  })
})


describe('Model engine controls', () => {
  it('edits a record, retains its ID, and cancels without saving', async () => {
    const { records, fetch } = setup({ customers: [{ id: 1, name: 'Before', email: null, active: true }] })
    await flushPromises()
    await wrapper.find('[aria-label="Edit customer 1"]').trigger('click')
    expect(wrapper.find('#name').element.value).toBe('Before')
    await wrapper.find('#name').setValue('After')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(records.customer).toHaveLength(1)
    expect(records.customer[0]).toMatchObject({ id: 1, name: 'After', active: true })
    expect(fetch.mock.calls.find(([, options]) => options.method === 'PATCH')[0]).toBe('/api/customer/1')
    expect(wrapper.text()).toContain('Customer updated.')
    await wrapper.find('[aria-label="Edit customer 1"]').trigger('click')
    await wrapper.find('#name').setValue('Discard')
    await button('Cancel edit').trigger('click')
    expect(wrapper.find('#name').element.value).toBe('')
    expect(records.customer[0].name).toBe('After')
  })

  it('requires deletion confirmation and handles an empty 204 response', async () => {
    const { fetch, records } = setup({ customers: [{ id: 1, name: 'Delete me' }] })
    await flushPromises()
    await wrapper.find('[aria-label="Delete customer 1"]').trigger('click')
    expect(fetch.mock.calls.some(([, options]) => options.method === 'DELETE')).toBe(false)
    await button('Cancel deletion').trigger('click')
    expect(records.customer).toHaveLength(1)
    await wrapper.find('[aria-label="Delete customer 1"]').trigger('click')
    await button('Delete record').trigger('click')
    await flushPromises()
    expect(records.customer).toHaveLength(0)
    expect(wrapper.text()).toContain('Customer deleted.')
  })

  it('shows linked-record conflicts and keeps the record', async () => {
    setup({ customers: [{ id: 1, name: 'Linked' }], intercept: (path, options) => options.method === 'DELETE'
      ? response({ error: 'A unique value or linked record constraint was violated' }, 409) : null })
    await flushPromises()
    await wrapper.find('[aria-label="Delete customer 1"]').trigger('click')
    await button('Delete record').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('linked record constraint')
    expect(wrapper.find('table').text()).toContain('Linked')
  })

  it('sends search and ordering to the server and resets pagination', async () => {
    const { fetch } = setup({ customers: [{ id: 2, name: 'Zulu' }, { id: 1, name: 'Alpha' }] })
    await flushPromises()
    await wrapper.find('#record-search').setValue('alpha')
    await wrapper.find('#record-order').setValue('name')
    await button('Apply').trigger('click')
    await flushPromises()
    expect(wrapper.find('table').text()).toContain('Alpha')
    expect(wrapper.find('table').text()).not.toContain('Zulu')
    const url = new URL(fetch.mock.calls.at(-1)[0], 'http://localhost')
    expect(JSON.parse(url.searchParams.get('filters'))).toEqual({ name__icontains: 'alpha' })
    expect(url.searchParams.get('order_by')).toBe('name')
    expect(url.searchParams.get('offset')).toBe('0')
  })

  it('sends date, choice, and exact decimal values without number conversion', async () => {
    const { records } = setup({ customers: [{ id: 1, name: 'Acme' }] })
    await flushPromises()
    await wrapper.findAll('nav button')[1].trigger('click')
    await flushPromises()
    wrapper.findComponent(Select).vm.$emit('update:modelValue', 1)
    await wrapper.find('#reference').setValue('FIELDS')
    await wrapper.find('#total').setValue('12.34')
    await wrapper.find('#posting-date').setValue('2026-10-28')
    await wrapper.find('#status').setValue('sent')
    await wrapper.find('#tax-rate').setValue('5.1234')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(records.invoice[0]).toMatchObject({ posting_date: '2026-10-28', status: 'sent', tax_rate: '5.1234' })
  })

  it('keeps edit values and errors after a rejected partial update', async () => {
    setup({ customers: [{ id: 1, name: 'Keep' }], intercept: (path, options) => options.method === 'PATCH'
      ? response({ error: 'Validation failed', fields: { name: 'Required' } }, 422) : null })
    await flushPromises()
    await wrapper.find('[aria-label="Edit customer 1"]').trigger('click')
    await wrapper.find('#name').setValue(' ')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('#name').element.value).toBe(' ')
    expect(wrapper.find('#name-error').text()).toBe('Required')
    expect(button('Save changes')).toBeDefined()
  })
})
