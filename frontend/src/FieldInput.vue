<script setup>
import { computed } from 'vue'
import InputText from 'primevue/inputtext'
import Checkbox from 'primevue/checkbox'
import LinkInput from './LinkInput.vue'

const props = defineProps({ field: Object, models: Array, modelValue: [String, Number, Boolean], inputId: String, disabled: Boolean, invalid: Boolean, describedby: String })
const emit = defineEmits(['update:modelValue'])
const unsafeInteger = computed(() => props.field.type === 'integer' && typeof props.modelValue === 'number' && !Number.isSafeInteger(props.modelValue))
</script>

<template>
  <p v-if="unsafeInteger" class="field-error">This value exceeds the browser integer range. Use the Python API to read or change it exactly.</p>
  <LinkInput v-else-if="field.type === 'link'" v-bind="props" @update:model-value="emit('update:modelValue', $event)" />
  <Checkbox v-else-if="field.type === 'boolean' && field.required" :model-value="modelValue" :input-id="inputId" binary :disabled="disabled" :invalid="invalid" :aria-describedby="describedby" @update:model-value="emit('update:modelValue', $event)" />
  <select v-else-if="field.type === 'select' || field.type === 'boolean'" :id="inputId" :value="modelValue == null ? '' : String(modelValue)" :required="field.required" :disabled="disabled" :aria-invalid="invalid" :aria-describedby="describedby" @change="emit('update:modelValue', $event.target.value === '' ? null : field.type === 'boolean' ? $event.target.value === 'true' : $event.target.value)">
    <option value="">{{ field.required ? 'Choose a value' : 'Not set' }}</option>
    <template v-if="field.type === 'boolean'"><option value="true">Yes</option><option value="false">No</option></template>
    <option v-for="choice in field.choices || []" :key="choice" :value="choice">{{ choice }}</option>
  </select>
  <textarea v-else-if="field.type === 'longtext'" :id="inputId" :value="modelValue" :required="field.required" :maxlength="field.max_length" :placeholder="field.placeholder" :disabled="disabled" :aria-invalid="invalid" :aria-describedby="describedby" rows="3" @input="emit('update:modelValue', $event.target.value)"></textarea>
  <InputText v-else :id="inputId" :model-value="modelValue == null ? '' : String(modelValue)" @update:model-value="emit('update:modelValue', $event)" :type="['date', 'email', 'url'].includes(field.type) ? field.type : 'text'" :inputmode="['currency', 'decimal'].includes(field.type) ? 'decimal' : field.type === 'integer' ? 'numeric' : undefined" :required="field.required" :maxlength="field.max_length" :placeholder="field.placeholder" :disabled="disabled" :invalid="invalid" :aria-invalid="invalid" :aria-describedby="describedby" />
</template>
