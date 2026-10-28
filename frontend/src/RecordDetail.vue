<script setup>
import Button from 'primevue/button'
import { formatValue, visibleFields } from './schema.js'
defineProps({ model: Object, record: Object, labels: Object, disabled: Boolean })
const emit = defineEmits(['edit', 'close', 'follow'])
</script>
<template>
  <section class="record-form" aria-label="Record details">
    <div class="eyebrow">Record #{{ record.id }}</div><h2>{{ model.label }} details</h2>
    <dl class="record-details"><template v-for="field in visibleFields(model)" :key="field.name"><dt>{{ field.label }}</dt><dd><button v-if="field.type === 'link' && record[field.name] != null" type="button" :disabled="disabled" @click="emit('follow', field.target, record[field.name])">{{ formatValue(field, record[field.name], labels) }}</button><span v-else>{{ formatValue(field, record[field.name], labels) }}</span></dd></template></dl>
    <Button label="Edit record" :disabled="disabled" @click="emit('edit', record)" /><Button label="Close details" text :disabled="disabled" @click="emit('close')" />
  </section>
</template>
