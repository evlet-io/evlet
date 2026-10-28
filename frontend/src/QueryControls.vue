<script setup>
import { computed, ref } from 'vue'
import Button from 'primevue/button'
import FieldInput from './FieldInput.vue'
import { fieldFor, idField, inputValue, visibleFields } from './schema.js'

const props = defineProps({ model: Object, models: Array, disabled: Boolean })
const emit = defineEmits(['apply'])
const search = ref(''), order = ref('-id'), filters = ref([]), error = ref('')
let sequence = 0
const fields = computed(() => [idField, ...visibleFields(props.model)])
const field = row => ({ ...fieldFor(props.model, row.name), required: false })
function operators(row) {
  const type = field(row).type
  return ['eq', 'ne', 'isnull', ...(['text', 'longtext', 'email', 'url'].includes(type) ? ['icontains', 'contains', 'startswith'] : ['integer', 'decimal', 'currency', 'date', 'datetime'].includes(type) ? ['gt', 'gte', 'lt', 'lte'] : [])]
}
const labels = { eq: 'Equals', ne: 'Does not equal', isnull: 'Is empty', icontains: 'Contains', contains: 'Contains (case sensitive)', startswith: 'Starts with', gt: 'Greater than', gte: 'At least', lt: 'Less than', lte: 'At most' }
function apply() {
  if (props.disabled) return
  const query = {}
  try {
    for (const row of filters.value) {
      const key = `${row.name}__${row.operator}`
      if (key in query) throw new Error('Use each field and comparison only once.')
      const value = row.operator === 'isnull' ? row.value === true : inputValue(field(row), row.value)
      if (value == null && !['eq', 'ne'].includes(row.operator)) throw new Error('Enter a filter value.')
      query[key] = value
    }
    error.value = ''
    emit('apply', { search: search.value.trim(), orderBy: order.value, filters: query })
  } catch (failure) { error.value = failure.message }
}
function clear() { search.value = ''; order.value = '-id'; filters.value = []; apply() }
</script>

<template>
  <div class="query-controls">
    <label for="record-search">Search</label><input id="record-search" v-model="search" type="search" maxlength="200" :disabled="disabled" @keydown.enter.prevent="apply" />
    <label for="record-order">Order</label><select id="record-order" v-model="order" :disabled="disabled"><option value="-id">Newest first</option><option value="id">Oldest first</option><template v-for="item in fields.filter(item => item.name !== 'id')" :key="item.name"><option :value="item.name">{{ item.label }}: ascending</option><option :value="`-${item.name}`">{{ item.label }}: descending</option></template></select>
    <div v-for="row in filters" :key="row.key" class="filter-row">
      <select v-model="row.name" :aria-label="`Filter field ${row.key}`" :disabled="disabled" @change="row.operator = 'eq'; row.value = null"><option v-for="item in fields" :key="item.name" :value="item.name">{{ item.label }}</option></select>
      <select v-model="row.operator" :aria-label="`Comparison ${row.key}`" :disabled="disabled" @change="row.value = row.operator === 'isnull' ? true : null"><option v-for="op in operators(row)" :key="op" :value="op">{{ labels[op] }}</option></select>
      <label :id="`filter-${row.key}-label`" :for="`filter-${row.key}`" class="sr-only">Filter value {{ row.key }}</label>
      <FieldInput :input-id="`filter-${row.key}`" :field="row.operator === 'isnull' ? { type: 'boolean', required: true } : field(row)" :models="models" v-model="row.value" :disabled="disabled" />
      <Button label="Remove" text size="small" :aria-label="`Remove filter ${row.key}`" :disabled="disabled" @click="filters = filters.filter(item => item.key !== row.key)" />
    </div>
    <Button label="Add filter" text size="small" :disabled="disabled || filters.length >= 20" @click="filters.push({ key: ++sequence, name: fields[0].name, operator: 'eq', value: null })" />
    <Button label="Apply" size="small" :disabled="disabled" @click="apply" /><Button label="Clear" text size="small" :disabled="disabled" @click="clear" />
    <small v-if="error" class="field-error" role="alert">{{ error }}</small>
  </div>
</template>
