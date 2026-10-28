<script setup>
import { computed, reactive, ref, watch } from 'vue'
import Button from 'primevue/button'
import Message from 'primevue/message'
import FieldInput from './FieldInput.vue'
import { inputValue, visibleFields } from './schema.js'

const props = defineProps({ model: Object, models: Array, errors: { type: Object, default: () => ({}) }, record: { type: Object, default: null }, busy: Boolean, disabled: Boolean, resetKey: Number })
const emit = defineEmits(['save', 'cancel'])
const form = reactive({}), dirty = new Set(), localErrors = ref({})
const collections = reactive({}), childDirty = new Set()
const groups = computed(() => props.model.layout?.length ? props.model.layout : [{ label: 'Details', fields: fields.value.map(field => field.name) }])
function childModel(child) { return props.models.find(model => model.name === child.model) }
function childFields(child) { return visibleFields(childModel(child)).filter(field => field.name !== child.link) }
function canEditChild(child, row) { return childModel(child).permissions?.[row.id ? 'update' : 'create'] !== false }
function canRemoveChild(child, row) { return !row.id || childModel(child).permissions?.delete !== false }
function addChild(child) { if (props.busy || props.disabled || childModel(child).permissions?.create === false) return; collections[child.name].push(Object.fromEntries(childFields(child).map(field => [field.name, field.default_kind === 'static' ? field.default : null]))); childDirty.add(child.name) }
function removeChild(child, index) { if (props.busy || props.disabled || !canRemoveChild(child, collections[child.name][index])) return; collections[child.name].splice(index, 1); childDirty.add(child.name) }
const fields = computed(() => visibleFields(props.model))
const messages = computed(() => ({ ...props.errors, ...localErrors.value }))
watch(() => [props.model, props.resetKey, props.record], () => {
  for (const name of Object.keys(form)) delete form[name]
  dirty.clear(); childDirty.clear()
  for (const name of Object.keys(collections)) delete collections[name]
  for (const child of props.model.children || []) collections[child.name] = (props.record?.children?.[child.name] || []).map(row => ({ ...row }))
  localErrors.value = {}
  for (const field of props.model.fields) form[field.name] = props.record ? props.record[field.name] : field.default_kind === 'static' ? field.default : field.type === 'boolean' && field.required ? false : null
}, { immediate: true })
function changed(field, value) { form[field.name] = value; dirty.add(field.name); delete localErrors.value[field.name] }
function save() {
  if (props.busy || props.disabled) return
  const data = {}, errors = {}
  for (const field of fields.value) {
    if (field.read_only || (!dirty.has(field.name) && (props.record || field.default_kind !== 'none'))) continue
    try { data[field.name] = inputValue(field, form[field.name]) }
    catch (failure) { errors[field.name] = failure.message }
  }
  const children = {}
  for (const child of props.model.children || []) {
    if (!childDirty.has(child.name)) continue
    children[child.name] = collections[child.name].map(row => {
      const values = row.id ? { id: row.id } : {}
      if (row.id && !canEditChild(child, row)) return values
      for (const field of childFields(child).filter(field => !field.read_only)) {
        try { if (row[field.name] != null || field.default_kind === 'none') values[field.name] = inputValue(field, row[field.name]) }
        catch (failure) { errors.children = failure.message }
      }
      return values
    })
  }
  if (Object.keys(children).length) data.children = children
  localErrors.value = errors
  if (!Object.keys(errors).length) emit('save', data)
}
</script>

<template>
  <form class="record-form" @submit.prevent="save">
    <div class="eyebrow">{{ record ? `Record #${record.id}` : 'Add a record' }}</div>
    <h2>{{ record ? 'Edit' : 'New' }} {{ model.label.toLowerCase() }}</h2>
    <p class="muted form-intro">A few details to keep your work moving.</p>
    <fieldset :disabled="busy || disabled">
      <section v-for="group in groups" :key="group.label" class="field-group"><h3>{{ group.label }}</h3>
      <template v-for="field in fields.filter(item => group.fields.includes(item.name))" :key="field.name">
        <label :id="`${field.name}-label`" :for="field.name">{{ field.label }} <span v-if="field.required && field.default_kind === 'none' && !field.read_only" aria-hidden="true">*</span><span v-if="field.read_only" class="optional">Read only</span></label>
        <FieldInput :field="{ ...field, required: field.required && field.default_kind === 'none' && !field.read_only || field.type === 'boolean' && field.required }" :models="models" :input-id="field.name" :model-value="form[field.name]" @update:model-value="changed(field, $event)" :disabled="busy || disabled || field.read_only" :invalid="!!messages[field.name]" :describedby="`${field.name}-help ${field.name}-error`" />
        <small :id="`${field.name}-help`" class="muted">
          {{ field.help_text }}<template v-if="field.default_kind === 'callable' && !record"> Leave unchanged to use the server default.</template>
          <template v-if="['currency', 'decimal'].includes(field.type)"> Up to {{ field.scale }} decimal places.</template>
          <template v-if="field.type === 'datetime'"> Include a timezone, for example 2026-10-28T12:00:00+04:00.</template>
        </small>
        <small v-if="messages[field.name]" :id="`${field.name}-error`" class="field-error">{{ messages[field.name] }}</small>
      </template>
      </section>
      <section v-for="child in model.children || []" :key="child.name" class="child-editor"><h3>{{ child.label }}</h3>
        <fieldset v-for="(row, index) in collections[child.name]" :key="index" class="child-row"><legend>{{ child.label }} {{ index + 1 }}</legend>
          <template v-for="field in childFields(child)" :key="field.name"><label :for="`${child.name}-${index}-${field.name}`">{{ field.label }}</label><FieldInput :field="field" :models="models" :input-id="`${child.name}-${index}-${field.name}`" :model-value="row[field.name]" :disabled="busy || disabled || field.read_only || !canEditChild(child, row)" @update:model-value="row[field.name] = $event; childDirty.add(child.name)" /></template>
          <Button v-if="canRemoveChild(child, row)" label="Remove row" text :disabled="busy || disabled" @click="removeChild(child, index)" />
        </fieldset>
        <Button v-if="childModel(child).permissions?.create !== false" :label="`Add ${child.label.toLowerCase()} row`" text :disabled="busy || disabled || collections[child.name]?.length >= 100" @click="addChild(child)" />
      </section>
      <p v-if="messages.children" role="alert" class="field-error">{{ messages.children }}</p>
      <Message v-if="messages._form" severity="error" :closable="false" role="alert">{{ messages._form }}</Message>
      <Button type="submit" :label="busy ? 'Saving…' : record ? 'Save changes' : `Create ${model.label.toLowerCase()}`" :loading="busy" :disabled="busy || disabled" />
      <Button v-if="record" label="Cancel edit" text :disabled="busy" @click="emit('cancel')" />
    </fieldset>
  </form>
</template>
