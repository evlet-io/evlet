<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import Button from 'primevue/button'
import { recordRequest, request } from './api.js'
import { formatValue, visibleFields } from './schema.js'
const props = defineProps({ model: Object, models: Array, record: Object, disabled: Boolean })
const emit = defineEmits(['updated', 'busy'])
const files = ref([]), error = ref(''), jobError = ref(''), busy = ref(false), job = ref(null)
let jobTimer = null
let generation = 0, fileRequest = 0
const base = () => `/api/${props.model.name}/${props.record.id}`
async function loadFiles() {
  const current = generation, sequence = ++fileRequest
  if (!props.model.attachments) return
  try { const data = await request(`${base()}/attachments`); if (current === generation && sequence === fileRequest) files.value = data.items }
  catch (failure) { if (current === generation) error.value = failure.fields?._form || Object.values(failure.fields || {}).join('; ') || failure.message }
}
watch([() => props.model.name, () => props.record.id], () => {
  generation++; files.value = []; error.value = ''; jobError.value = ''; job.value = null; clearTimeout(jobTimer)
  loadFiles()
}, { immediate: true })
watch(() => props.record, (record, previous) => { if (record.id === previous?.id) loadFiles() })
onBeforeUnmount(() => { generation++; clearTimeout(jobTimer) })
function pending(value) { busy.value = value; emit('busy', value) }
async function action(name) {
  if (busy.value || props.disabled) return
  pending(true); error.value = ''
  const current = generation
  try {
    const result = await recordRequest(props.model.name, props.record.id, `actions/${name}`, {})
    if (current === generation) {
      clearTimeout(jobTimer); jobError.value = ''
      job.value = result.job || null
      emit('updated', result.job ? result.record : result)
      if (result.job) { jobTimer = setTimeout(() => checkJob(result.job.id, current), 500) }
    }
  }
  catch (failure) { if (current === generation) error.value = failure.fields?._form || Object.values(failure.fields || {}).join('; ') || failure.message }
  finally { pending(false) }
}
async function checkJob(id, current) {
  if (current !== generation || job.value?.id !== id) return
  try {
    const status = await request(`/api/_jobs/${id}`)
    if (current !== generation || job.value?.id !== id) return
    job.value = status
    jobError.value = ''
    if (status.state === 'queued' || status.state === 'running') jobTimer = setTimeout(() => checkJob(id, current), 1000)
    else if (status.state === 'succeeded') {
      const record = await request(base())
      if (current === generation && job.value?.id === id) emit('updated', record)
    }
  } catch (failure) {
    if (current !== generation || job.value?.id !== id) return
    const retry = failure.status === 0 || failure.status >= 500 || [408, 429].includes(failure.status)
    jobError.value = failure.message + (retry ? ' Checking again shortly.' : '')
    if (retry) jobTimer = setTimeout(() => checkJob(id, current), 3000)
  }
}
async function upload(event) {
  const file = event.target.files[0]
  if (!file || busy.value || props.disabled) return
  if (!file.size || file.size > 1024 * 1024) { error.value = 'Use a nonempty file up to 1 MiB.'; event.target.value = ''; return }
  pending(true); error.value = ''; fileRequest++
  const current = generation
  try {
    const bytes = new Uint8Array(await file.arrayBuffer())
    if (current !== generation) return
    let binary = ''; for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
    const saved = await recordRequest(props.model.name, props.record.id, 'attachments', { name: file.name, content: btoa(binary) })
    if (current === generation) files.value.push(saved)
  } catch (failure) { if (current === generation) error.value = failure.fields?._form || Object.values(failure.fields || {}).join('; ') || failure.message }
  finally { pending(false); event.target.value = '' }
}
async function remove(file) {
  if (busy.value || props.disabled) return
  pending(true); error.value = ''; fileRequest++
  const current = generation
  try { await request(`${base()}/attachments/${file.id}`, { method: 'DELETE' }); if (current === generation) files.value = files.value.filter(item => item.id !== file.id) }
  catch (failure) { if (current === generation) error.value = failure.fields?._form || Object.values(failure.fields || {}).join('; ') || failure.message }
  finally { pending(false) }
}
function childFields(child) { const model = props.models.find(item => item.name === child.model); return visibleFields(model).filter(field => field.name !== child.link) }
</script>
<template>
  <section class="record-extras">
    <p v-if="job" role="status">Background task: {{ job.state === 'succeeded' ? 'completed' : job.state }}<span v-if="job.state === 'failed'">. An administrator can inspect and retry it.</span></p>
    <p v-if="error" role="alert" class="field-error">{{ error }}</p>
    <p v-if="jobError" role="alert" class="field-error">{{ jobError }}</p>
    <section v-for="child in model.children || []" :key="child.name"><h3>{{ child.label }}</h3><p v-if="!record.children?.[child.name]?.length">No rows yet.</p><table v-else><thead><tr><th v-for="field in childFields(child)" :key="field.name">{{ field.label }}</th></tr></thead><tbody><tr v-for="row in record.children[child.name]" :key="row.id"><td v-for="field in childFields(child)" :key="field.name">{{ formatValue(field, row[field.name]) }}</td></tr></tbody></table></section>
    <div v-if="model.actions?.length" class="action-buttons"><Button v-for="item in model.actions" :key="item.name" :label="item.label" :disabled="busy || disabled" @click="action(item.name)" /></div>
    <section v-if="model.attachments"><h3>Attachments</h3><p class="muted">Private files, up to 1 MiB each and 10 per record.</p><ul><li v-for="file in files" :key="file.id"><a :href="`${base()}/attachments/${file.id}`">{{ file.name }}</a> <span>({{ file.size }} bytes)</span><Button v-if="model.permissions?.update" label="Remove" text :aria-label="`Remove ${file.name}`" :disabled="busy || disabled" @click="remove(file)" /></li></ul><label v-if="model.permissions?.update">Attach file<input type="file" aria-label="Attach file" :disabled="busy || disabled" @change="upload" /></label></section>
  </section>
</template>
