<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Message from 'primevue/message'
import RecordForm from './RecordForm.vue'
import RecordDetail from './RecordDetail.vue'
import QueryControls from './QueryControls.vue'
import { createRecord, deleteRecord, getRecord, getSchema, listRecords, updateRecord } from './api.js'
import { fieldFor, formatValue, recordLabel, validateSchema, visibleFields } from './schema.js'

const sections = ref([]), active = ref(null)
const section = computed(() => sections.value.find(item => item.name === active.value))
const editing = ref(null), detail = ref(null), deleting = ref(null)
const query = ref({ search: '', filters: {}, orderBy: '-id' })
const rows = ref([]), labels = ref({}), offset = ref(0), total = ref(0)
const loading = ref(true), saving = ref(false), reading = ref(false), connected = ref(false)
const error = ref(''), success = ref(''), errors = ref({}), resetKey = ref(0)
const busy = computed(() => loading.value || saving.value || reading.value)
let generation = 0, recordGeneration = 0

async function resolveLabels(model, records) {
  const targets = new Map()
  for (const field of visibleFields(model).filter(field => field.type === 'link')) {
    if (!targets.has(field.target)) targets.set(field.target, new Set())
    for (const record of records) if (record[field.name] != null) targets.get(field.target).add(record[field.name])
  }
  const found = {}
  await Promise.all([...targets].map(async ([name, ids]) => {
    if (!ids.size) return
    const target = sections.value.find(item => item.name === name)
    const values = [...ids]
    for (let i = 0; i < values.length; i += 100) {
      const result = await listRecords(name, 100, 0, { id__in: values.slice(i, i + 100) })
      for (const record of result.items) found[`${name}:${record.id}`] = recordLabel(target, record)
    }
  }))
  return found
}
async function loadPage() {
  if (!section.value) { loading.value = false; return }
  const current = ++generation, model = section.value
  loading.value = true
  error.value = ''
  try {
    const { search, filters, orderBy } = query.value
    let records = await listRecords(model.name, 20, offset.value, filters, orderBy, search)
    if (current !== generation) return
    if (!records.items.length && offset.value && records.total) {
      offset.value = Math.floor((records.total - 1) / 20) * 20
      records = await listRecords(model.name, 20, offset.value, filters, orderBy, search)
    } else if (!records.total) offset.value = 0
    const found = await resolveLabels(model, records.items)
    if (current !== generation) return
    rows.value = records.items
    total.value = records.total
    labels.value = { ...labels.value, ...found }
  } catch (failure) {
    if (current === generation) { rows.value = []; total.value = 0; error.value = failure.message }
  } finally { if (current === generation) loading.value = false }
}
async function connect() {
  error.value = ''; loading.value = true
  try {
    sections.value = validateSchema(await getSchema())
    active.value = sections.value[0]?.name || null
    connected.value = true
    await loadPage()
  } catch (failure) { connected.value = false; loading.value = false; error.value = failure.message }
}
async function select(model) {
  if (saving.value || active.value === model) return
  generation++; recordGeneration++
  active.value = model
  editing.value = null; detail.value = null; deleting.value = null; reading.value = false
  query.value = { search: '', filters: {}, orderBy: '-id' }
  offset.value = 0; rows.value = []; errors.value = {}; success.value = ''; labels.value = {}
  if (connected.value) await loadPage()
}
async function page(direction) { if (busy.value) return; offset.value = Math.max(0, offset.value + direction * 20); await loadPage() }
async function save(data) {
  if (busy.value || !connected.value) return
  saving.value = true; errors.value = {}; success.value = ''
  try {
    const wasEditing = !!editing.value
    if (wasEditing) await updateRecord(active.value, editing.value.id, data)
    else await createRecord(active.value, data)
    success.value = `${section.value.label} ${wasEditing ? 'updated' : 'created'}.`
    editing.value = null; resetKey.value++; offset.value = 0
    await loadPage()
  } catch (failure) { errors.value = { ...failure.fields, _form: failure.message } }
  finally { saving.value = false }
}
async function openRecord(id, mode = 'view') {
  if (busy.value) return
  const current = ++recordGeneration, model = section.value
  reading.value = true; error.value = ''; success.value = ''; errors.value = {}; deleting.value = null
  try {
    const record = await getRecord(model.name, id)
    const found = await resolveLabels(model, [record])
    if (current !== recordGeneration) return
    labels.value = { ...labels.value, ...found }
    detail.value = mode === 'view' ? record : null
    editing.value = mode === 'edit' ? record : null
  } catch (failure) { if (current === recordGeneration) error.value = failure.message }
  finally { if (current === recordGeneration) reading.value = false }
}
function cancelEdit() { if (saving.value) return; editing.value = null; errors.value = {} }
async function follow(model, id) {
  const selection = select(model)
  const current = recordGeneration
  await selection
  if (current !== recordGeneration || active.value !== model) return
  await openRecord(id)
}
async function remove() {
  if (!deleting.value || busy.value) return
  saving.value = true; error.value = ''; success.value = ''
  try {
    await deleteRecord(active.value, deleting.value.id)
    if (editing.value?.id === deleting.value.id) { editing.value = null; errors.value = {} }
    if (detail.value?.id === deleting.value.id) detail.value = null
    deleting.value = null; success.value = `${section.value.label} deleted.`
    if (rows.value.length === 1 && offset.value) offset.value -= 20
    await loadPage()
  } catch (failure) { error.value = failure.message }
  finally { saving.value = false }
}
async function applyQuery(value) { if (busy.value) return; query.value = value; offset.value = 0; await loadPage() }
onMounted(connect)
onBeforeUnmount(() => { generation++; recordGeneration++ })
</script>

<template>
  <div class="workspace">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="Evlet home"><span class="brand-mark">e</span>evlet<span class="brand-dot">.</span></a>
      <div class="workspace-label">PLAYGROUND <span>01</span></div>
      <nav aria-label="Records">
        <button v-for="item in sections" :key="item.name" type="button" :class="{ active: active === item.name }" :aria-current="active === item.name ? 'page' : undefined" :disabled="saving" @click="select(item.name)">
          <span class="nav-mark">{{ String(sections.indexOf(item) + 1).padStart(2, '0') }}</span>{{ item.plural }}<svg class="nav-arrow" aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 12 12 4M4 4h8v8" /></svg>
        </button>
      </nav>
      <div class="sidebar-note"><span class="status-dot"></span>A little less busywork.<br /><span class="sidebar-note-detail">A little more possibility.</span></div>
    </aside>
    <main>
      <header class="topbar"><span>Workspace <span class="breadcrumb">/ {{ section?.plural || 'Records' }}</span></span><span class="workspace-badge">Playground</span></header>
      <div class="page-content">
        <div class="page-heading"><div><div class="eyebrow">Your everyday essentials</div><h1>{{ section?.plural || 'Records' }}</h1><p class="muted">{{ section?.description }}</p></div><span class="chapter-number" aria-hidden="true">{{ String(sections.indexOf(section) + 1).padStart(2, '0') }}</span></div>
        <Message v-if="error" severity="error" :closable="false" role="alert">{{ error }} <Button label="Try again" text size="small" :disabled="saving || loading" @click="connected ? loadPage() : connect()" /></Message>
        <Message v-if="success" severity="success" :closable="false" role="status">{{ success }}</Message>
        <p v-if="loading && !connected" role="status">Connecting…</p>
        <p v-else-if="connected && !section">No models are registered.</p>
        <div v-if="section && connected" class="content-grid">
          <section class="records-card" :aria-label="section.plural" :aria-busy="loading">
            <div class="card-heading"><h2>All {{ section.plural.toLowerCase() }}</h2><Button label="Refresh" text size="small" :disabled="busy" @click="loadPage" /></div>
            <QueryControls :key="active" :model="section" :models="sections" :disabled="busy" @apply="applyQuery" />
            <Message v-if="deleting" severity="warn" :closable="false">
              Delete {{ section.label.toLowerCase() }} #{{ deleting.id }}? Linked records must be removed first.
              <Button label="Delete record" severity="danger" size="small" :disabled="busy" @click="remove" />
              <Button label="Cancel deletion" text size="small" :disabled="busy" @click="deleting = null" />
            </Message>
            <p v-if="loading || reading" class="loading-note" role="status">Loading records…</p>
            <DataTable :value="rows" data-key="id" striped-rows>
              <Column v-for="name in section.list_fields" :key="name" :field="name" :header="fieldFor(section, name).label"><template #body="{ data }">{{ formatValue(fieldFor(section, name), data[name], labels) }}</template></Column>
              <Column header="Actions"><template #body="{ data }">
                <Button label="View" text size="small" :aria-label="`View ${active} ${data.id}`" :disabled="busy" @click="openRecord(data.id)" />
                <Button label="Edit" text size="small" :aria-label="`Edit ${active} ${data.id}`" :disabled="busy" @click="openRecord(data.id, 'edit')" />
                <Button label="Delete" text severity="danger" size="small" :aria-label="`Delete ${active} ${data.id}`" :disabled="busy" @click="deleting = data" />
              </template></Column>
              <template #empty><div v-if="!loading && !error" class="empty-state"><span class="empty-symbol" aria-hidden="true">+</span><h3>{{ query.search || Object.keys(query.filters).length ? 'No matching records.' : `Your first ${section.label.toLowerCase()} starts here.` }}</h3><p>Add a record using the form, or change your search and filters.</p></div></template>
            </DataTable>
            <footer class="pagination"><span>Page {{ offset / 20 + 1 }} · {{ rows.length }} records · {{ total }} total</span><div><Button label="Previous" text size="small" :disabled="offset === 0 || busy" @click="page(-1)" /><Button label="Next" text size="small" :disabled="offset + rows.length >= total || busy" @click="page(1)" /></div></footer>
          </section>
          <aside class="form-card">
            <RecordDetail v-if="detail" :model="section" :record="detail" :labels="labels" :disabled="busy" @edit="openRecord($event.id, 'edit')" @close="detail = null" @follow="follow" />
            <RecordForm v-else :key="active" :model="section" :models="sections" :errors="errors" :busy="saving" :disabled="loading || reading || !connected" :reset-key="resetKey" :record="editing" @save="save" @cancel="cancelEdit" />
          </aside>
        </div>
        <footer class="page-footer"><span>Small steps. Everything connected.</span><span>EVLET · 0.1</span></footer>
      </div>
    </main>
  </div>
</template>
