<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import Select from 'primevue/select'
import Button from 'primevue/button'
import { getRecord, listRecords } from './api.js'
import { recordLabel } from './schema.js'

const props = defineProps({ field: Object, models: Array, modelValue: [Number, String], inputId: String, disabled: Boolean, invalid: Boolean, describedby: String })
const emit = defineEmits(['update:modelValue'])
const search = ref(''), options = ref([]), busy = ref(false), error = ref(''), total = ref(0), offset = ref(0)
let generation = 0
async function load(more = false) {
  const current = ++generation
  const target = props.models.find(model => model.name === props.field.target)
  busy.value = true
  error.value = ''
  try {
    const page = more ? offset.value + 20 : 0
    const result = await listRecords(target.name, 20, page, {}, '-id', search.value.trim())
    let items = result.items.map(record => ({ id: record.id, label: recordLabel(target, record) }))
    if (more) items = [...options.value, ...items]
    if (props.modelValue != null && !items.some(item => item.id === props.modelValue)) {
      const selected = await getRecord(target.name, props.modelValue)
      items.unshift({ id: selected.id, label: recordLabel(target, selected) })
    }
    if (current !== generation) return
    options.value = [...new Map(items.map(item => [item.id, item])).values()]
    total.value = result.total
    offset.value = page
  } catch (failure) {
    if (current === generation) error.value = failure.message
  } finally {
    if (current === generation) busy.value = false
  }
}
watch([() => props.field.target, () => props.modelValue], () => load(), { immediate: true })
onBeforeUnmount(() => generation++)
</script>

<template>
  <div class="link-input">
    <div class="link-search">
      <input v-model="search" type="search" maxlength="200" :aria-label="`Search ${field.label}`" :disabled="disabled || busy" @keydown.enter.prevent="load()" />
      <Button label="Find" text size="small" :aria-label="`Find ${field.label}`" :disabled="disabled || busy" @click="load()" />
    </div>
    <Select :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" :input-id="inputId" :options="options" option-label="label" option-value="id" :placeholder="`Choose ${field.label.toLowerCase()}`" :disabled="disabled || busy" :invalid="invalid" :show-clear="!field.required" :aria-labelledby="`${inputId}-label`" :pt="{ label: { 'aria-required': field.required, 'aria-invalid': invalid, 'aria-describedby': describedby } }" />
    <small v-if="busy" role="status">Loading choices…</small>
    <small v-if="error" class="field-error" role="alert">{{ error }} <button type="button" :disabled="disabled || busy" @click="load()">Retry choices</button></small>
    <Button v-if="offset + 20 < total" label="More choices" text size="small" :disabled="disabled || busy" @click="load(true)" />
  </div>
</template>
