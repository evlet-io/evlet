import { afterEach, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import RecordForm from './RecordForm.vue'
import schema from './test-business-schema.json'

let wrapper
afterEach(() => wrapper?.unmount())

it('lets a user with child create permission enter a new child without update permission', async () => {
  const models = structuredClone(schema.models)
  const child = models.find(model => model.name === 'projecttask')
  child.permissions = { create: true, update: false, delete: false }
  wrapper = mount(RecordForm, {
    props: { model: models.find(model => model.name === 'project'), models },
    global: { plugins: [PrimeVue] },
  })
  await wrapper.findAll('button').find(button => button.text() === 'Add tasks row').trigger('click')
  const title = wrapper.find('#tasks-0-title')
  expect(title.exists()).toBe(true)
  expect(title.element.matches(':disabled')).toBe(false)
})

it('lets a user with child delete permission remove a row without update permission', () => {
  const models = structuredClone(schema.models)
  const child = models.find(model => model.name === 'projecttask')
  child.permissions = { create: false, update: false, delete: true }
  wrapper = mount(RecordForm, {
    props: {
      model: models.find(model => model.name === 'project'), models,
      record: { id: 1, name: 'Can delete', children: { tasks: [{ id: 1, title: 'Remove me', done: false }] } },
    },
    global: { plugins: [PrimeVue] },
  })
  const remove = wrapper.findAll('button').find(button => button.text() === 'Remove row')
  expect(remove.element.matches(':disabled')).toBe(false)
})

function formWithPermissions(permissions, rows = [{ id: 7, title: 'Keep', done: false }]) {
  const models = structuredClone(schema.models)
  models.find(model => model.name === 'projecttask').permissions = permissions
  wrapper = mount(RecordForm, {
    props: {
      model: models.find(model => model.name === 'project'), models,
      record: { id: 1, name: 'Parent', children: { tasks: rows } },
    },
    global: { plugins: [PrimeVue] },
  })
}
const button = label => wrapper.findAll('button').find(item => item.text() === label)

it('appends a new child while retaining rows the user cannot update', async () => {
  formWithPermissions({ create: true, update: false, delete: false })
  expect(wrapper.find('#tasks-0-title').element.matches(':disabled')).toBe(true)
  expect(button('Remove row')).toBeUndefined()
  await button('Add tasks row').trigger('click')
  expect(wrapper.find('#tasks-1-title').element.matches(':disabled')).toBe(false)
  await wrapper.find('#tasks-1-title').setValue('New child')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')[0][0]).toEqual({ children: { tasks: [{ id: 7 }, { title: 'New child', done: false }] } })
})

it('discards an unsaved child without requiring persisted-row delete permission', async () => {
  formWithPermissions({ create: true, update: false, delete: false })
  await button('Add tasks row').trigger('click')
  await button('Remove row').trigger('click')
  expect(wrapper.find('#tasks-1-title').exists()).toBe(false)
  expect(wrapper.find('#tasks-0-title').element.value).toBe('Keep')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')[0][0]).toEqual({ children: { tasks: [{ id: 7 }] } })
})

it('deletes one child while retaining another without update permission', async () => {
  formWithPermissions({ create: false, update: false, delete: true }, [
    { id: 7, title: 'Remove', done: false }, { id: 8, title: 'Keep', done: false },
  ])
  expect(wrapper.find('#tasks-0-title').element.matches(':disabled')).toBe(true)
  expect(button('Remove row').element.matches(':disabled')).toBe(false)
  await button('Remove row').trigger('click')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')[0][0]).toEqual({ children: { tasks: [{ id: 8 }] } })
})

it('allows existing-row updates without granting creation or deletion controls', async () => {
  formWithPermissions({ create: false, update: true, delete: false })
  expect(button('Add tasks row')).toBeUndefined()
  expect(button('Remove row')).toBeUndefined()
  expect(wrapper.find('#tasks-0-title').element.matches(':disabled')).toBe(false)
  await wrapper.find('#tasks-0-title').setValue('Updated')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')[0][0]).toEqual({ children: { tasks: [{ id: 7, title: 'Updated', done: false }] } })
})

it.each(['busy', 'disabled'])('keeps all child controls disabled while the parent form is %s', async state => {
  formWithPermissions({ create: true, update: true, delete: true })
  await button('Add tasks row').trigger('click')
  await wrapper.setProps({ [state]: true })
  for (const field of wrapper.findAll('.child-editor input, .child-editor button')) {
    expect(field.element.matches(':disabled')).toBe(true)
  }
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('save')).toBeUndefined()
})
