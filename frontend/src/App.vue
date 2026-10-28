<script setup>
import { computed, onMounted, ref } from 'vue'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Message from 'primevue/message'
import RecordForm from './RecordForm.vue'
import { createRecord, getSchema, listRecords } from './api.js'

const sections = [
  { name: 'customer', title: 'Customers', mark: '01', description: 'Good work starts with good relationships.' },
  { name: 'invoice', title: 'Invoices', mark: '02', description: 'Keep each piece of work connected to a customer.' },
  { name: 'payment', title: 'Payments', mark: '03', description: 'Record payments and the invoices they belong to.' },
]
const active = ref('customer')
const section = computed(() => sections.find(item => item.name === active.value))
const rows = ref([])
const choices = ref([])
const offset = ref(0)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const success = ref('')
const errors = ref({})
const resetKey = ref(0)
const connected = ref(false)
let generation = 0

async function loadPage() {
  const current = ++generation
  const model = active.value
  loading.value = true
  error.value = ''
  try {
    const [records, links] = await Promise.all([
      listRecords(model, 20, offset.value),
      model === 'customer' ? Promise.resolve({ items: [] }) : listRecords(model === 'invoice' ? 'customer' : 'invoice', 100),
    ])
    if (current !== generation) return
    rows.value = records.items
    choices.value = links.items
  } catch (failure) {
    if (current === generation) {
      rows.value = []
      choices.value = []
      error.value = failure.message
    }
  } finally {
    if (current === generation) loading.value = false
  }
}
async function connect() {
  error.value = ''
  loading.value = true
  try {
    const schema = await getSchema()
    if (schema.version !== 1 || !sections.every(section => schema.models.some(model => model.name === section.name))) {
      throw new Error('This Playground requires the Customer, Invoice, and Payment models in schema version 1.')
    }
    connected.value = true
    await loadPage()
  } catch (failure) {
    connected.value = false
    loading.value = false
    error.value = failure.message
  }
}
async function select(model) {
  if (saving.value || active.value === model) return
  active.value = model
  offset.value = 0
  rows.value = []
  choices.value = []
  errors.value = {}
  success.value = ''
  if (connected.value) await loadPage()
}
async function page(direction) {
  offset.value = Math.max(0, offset.value + direction * 20)
  await loadPage()
}
async function save(data) {
  if (saving.value || loading.value || !connected.value) return
  saving.value = true
  errors.value = {}
  success.value = ''
  try {
    await createRecord(active.value, data)
    success.value = `${section.value.title.slice(0, -1)} created.`
    resetKey.value++
    offset.value = 0
    await loadPage()
  } catch (failure) {
    errors.value = { ...failure.fields, _form: failure.message }
  } finally {
    saving.value = false
  }
}
onMounted(connect)
</script>

<template>
  <div class="workspace">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="Evlet home"><span class="brand-mark">e</span>evlet<span class="brand-dot">.</span></a>
      <div class="workspace-label">PLAYGROUND <span>01</span></div>
      <nav aria-label="Records">
        <button v-for="item in sections" :key="item.name" type="button" :class="{ active: active === item.name }" :aria-current="active === item.name ? 'page' : undefined" :disabled="saving" @click="select(item.name)">
          <span class="nav-mark">{{ item.mark }}</span>{{ item.title }}<svg class="nav-arrow" aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 12 12 4M4 4h8v8" /></svg>
        </button>
      </nav>
      <div class="sidebar-note"><span class="status-dot"></span>A little less busywork.<br /><span class="sidebar-note-detail">A little more possibility.</span></div>
    </aside>
    <main>
      <header class="topbar"><span>Workspace <span class="breadcrumb">/ {{ section.title }}</span></span><span class="workspace-badge">Playground</span></header>
      <div class="page-content">
        <div class="page-heading"><div><div class="eyebrow">Your everyday essentials</div><h1>{{ section.title }}</h1><p class="muted">{{ section.description }}</p></div><span class="chapter-number" aria-hidden="true">{{ section.mark }}</span></div>
        <Message v-if="error" severity="error" :closable="false" role="alert">{{ error }} <Button label="Try again" text size="small" @click="connected ? loadPage() : connect()" /></Message>
        <Message v-if="success" severity="success" :closable="false" role="status">{{ success }}</Message>
        <div class="content-grid">
          <section class="records-card" :aria-label="section.title" :aria-busy="loading">
            <div class="card-heading"><h2>All {{ section.title.toLowerCase() }}</h2><Button label="Refresh" text size="small" :disabled="loading || saving || !connected" @click="loadPage" /></div>
            <p v-if="loading" class="loading-note" role="status">Loading records…</p>
            <DataTable :value="rows" data-key="id" striped-rows>
              <Column field="id" header="ID"><template #body="{ data }"><span class="record-id">#{{ data.id }}</span></template></Column>
              <template v-if="active === 'customer'">
                <Column field="name" header="Name" />
                <Column field="email" header="Email"><template #body="{ data }">{{ data.email || '—' }}</template></Column>
              </template>
              <template v-else-if="active === 'invoice'">
                <Column field="reference" header="Reference" />
                <Column field="customer" header="Customer"><template #body="{ data }">{{ choices.find(item => item.id === data.customer)?.name || `#${data.customer}` }}</template></Column>
                <Column field="total" header="Total" />
              </template>
              <template v-else>
                <Column field="reference" header="Reference" />
                <Column field="invoice" header="Invoice"><template #body="{ data }">{{ choices.find(item => item.id === data.invoice)?.reference || `#${data.invoice}` }}</template></Column>
                <Column field="amount" header="Amount" />
              </template>
              <template #empty><div v-if="!loading && !error" class="empty-state"><span class="empty-symbol" aria-hidden="true">+</span><h3>{{ offset ? 'You’re all caught up.' : `Your first ${active} starts here.` }}</h3><p>{{ offset ? 'Return to the previous page to see your records.' : `Add a ${active} using the form to get started.` }}</p></div></template>
            </DataTable>
            <footer class="pagination"><span>Page {{ offset / 20 + 1 }} · {{ rows.length }} records</span><div><Button label="Previous" text size="small" :disabled="offset === 0 || loading || saving" @click="page(-1)" /><Button label="Next" text size="small" :disabled="rows.length < 20 || loading || saving" @click="page(1)" /></div></footer>
          </section>
          <aside class="form-card"><RecordForm :model="active" :choices="choices" :errors="errors" :busy="saving" :disabled="loading || !connected" :reset-key="resetKey" @save="save" /></aside>
        </div>
        <footer class="page-footer"><span>Small steps. Everything connected.</span><span>EVLET · 0.1</span></footer>
      </div>
    </main>
  </div>
</template>
