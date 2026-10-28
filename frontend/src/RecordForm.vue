<script setup>
import { computed, reactive, ref, watch } from 'vue'
import Button from 'primevue/button'
import Message from 'primevue/message'
import FieldInput from './FieldInput.vue'
import { inputValue, visibleFields } from './schema.js'

const props = defineProps({ model: Object, models: Array, errors: { type: Object, default: () => ({}) }, record: { type: Object, default: null }, busy: Boolean, disabled: Boolean, resetKey: Number })
const emit = defineEmits(['save', 'cancel'])
const form = reactive({}), dirty = new Set(), localErrors = ref({})
const fields = computed(() => visibleFields(props.model))
const messages = computed(() => ({ ...props.errors, ...localErrors.value }))
watch(() => [props.model, props.resetKey, props.record], () => {
  for (const name of Object.keys(form)) delete form[name]
  dirty.clear()
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
      <template v-for="field in fields" :key="field.name">
        <label :id="`${field.name}-label`" :for="field.name">{{ field.label }} <span v-if="field.required && field.default_kind === 'none' && !field.read_only" aria-hidden="true">*</span><span v-if="field.read_only" class="optional">Read only</span></label>
        <FieldInput :field="{ ...field, required: field.required && field.default_kind === 'none' && !field.read_only || field.type === 'boolean' && field.required }" :models="models" :input-id="field.name" :model-value="form[field.name]" @update:model-value="changed(field, $event)" :disabled="busy || disabled || field.read_only" :invalid="!!messages[field.name]" :describedby="`${field.name}-help ${field.name}-error`" />
        <small :id="`${field.name}-help`" class="muted">
          {{ field.help_text }}<template v-if="field.default_kind === 'callable' && !record"> Leave unchanged to use the server default.</template>
          <template v-if="['currency', 'decimal'].includes(field.type)"> Up to {{ field.scale }} decimal places.</template>
          <template v-if="field.type === 'datetime'"> Include a timezone, for example 2026-10-28T12:00:00+04:00.</template>
        </small>
        <small v-if="messages[field.name]" :id="`${field.name}-error`" class="field-error">{{ messages[field.name] }}</small>
      </template>
      <Message v-if="messages._form" severity="error" :closable="false" role="alert">{{ messages._form }}</Message>
      <Button type="submit" :label="busy ? 'Saving…' : record ? 'Save changes' : `Create ${model.label.toLowerCase()}`" :loading="busy" :disabled="busy || disabled" />
      <Button v-if="record" label="Cancel edit" text :disabled="busy" @click="emit('cancel')" />
    </fieldset>
  </form>
</template>
