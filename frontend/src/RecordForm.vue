<script setup>
import { reactive, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Message from 'primevue/message'
import Checkbox from 'primevue/checkbox'

const props = defineProps({
  model: { type: String, required: true },
  choices: { type: Array, default: () => [] },
  errors: { type: Object, default: () => ({}) },
  record: { type: Object, default: null },
  busy: Boolean,
  disabled: Boolean,
  resetKey: { type: Number, default: 0 },
})
const emit = defineEmits(['save', 'cancel'])
const defaults = () => ({ name: '', email: '', active: true, notes: '', customer: null, invoice: null,
  reference: '', total: '', amount: '', posting_date: '', status: 'draft', tax_rate: '0.0000', received_at: '' })
const form = reactive(defaults())
watch(() => [props.model, props.resetKey, props.record], () => {
  Object.assign(form, defaults(), props.record || {})
}, { immediate: true })
function save() {
  if (props.busy || props.disabled) return
  const data = props.model === 'customer'
    ? { name: form.name, email: form.email || null, active: form.active, notes: form.notes || null }
    : props.model === 'invoice'
      ? { customer: form.customer, reference: form.reference, total: form.total, posting_date: form.posting_date || null, status: form.status, tax_rate: form.tax_rate || null }
      : { invoice: form.invoice, reference: form.reference, amount: form.amount, received_at: form.received_at || null }
  emit('save', data)
}
</script>

<template>
  <form class="record-form" @submit.prevent="save">
    <div class="eyebrow">{{ record ? `Record #${record.id}` : 'Add a record' }}</div>
    <h2>{{ record ? 'Edit' : 'New' }} {{ model }}</h2>
    <p class="muted form-intro">A few details to keep your work moving.</p>
    <fieldset :disabled="busy || disabled">
      <template v-if="model === 'customer'">
        <label for="name">Name <span aria-hidden="true">*</span></label>
        <InputText id="name" v-model="form.name" required :maxlength="120" :invalid="!!errors.name" :aria-invalid="!!errors.name" aria-describedby="name-error" placeholder="e.g. Acme Studio" />
        <small v-if="errors.name" id="name-error" class="field-error">{{ errors.name }}</small>
        <label for="email">Email <span class="optional">Optional</span></label>
        <InputText id="email" v-model="form.email" type="email" :maxlength="254" :invalid="!!errors.email" aria-describedby="email-error" placeholder="hello@example.com" />
        <small v-if="errors.email" id="email-error" class="field-error">{{ errors.email }}</small>
        <label for="active">Active</label>
        <Checkbox v-model="form.active" input-id="active" binary />
        <small v-if="errors.active" class="field-error">{{ errors.active }}</small>
        <label for="notes">Notes</label>
        <textarea id="notes" v-model="form.notes" rows="3" maxlength="65535"></textarea>
        <small v-if="errors.notes" class="field-error">{{ errors.notes }}</small>
      </template>
      <template v-else>
        <template v-if="model === 'invoice'">
          <label id="customer-label" for="customer">Customer <span aria-hidden="true">*</span></label>
          <Select v-model="form.customer" input-id="customer" :options="choices" option-label="name" option-value="id" placeholder="Choose a customer" :invalid="!!errors.customer" :disabled="busy || disabled" aria-labelledby="customer-label" :pt="{ label: { 'aria-required': 'true', 'aria-describedby': errors.customer ? 'customer-error' : undefined } }" filter />
          <small v-if="errors.customer" id="customer-error" class="field-error">{{ errors.customer }}</small>
        </template>
        <template v-else>
          <label id="invoice-label" for="invoice">Invoice <span aria-hidden="true">*</span></label>
          <Select v-model="form.invoice" input-id="invoice" :options="choices" option-label="reference" option-value="id" placeholder="Choose an invoice" :invalid="!!errors.invoice" :disabled="busy || disabled" aria-labelledby="invoice-label" :pt="{ label: { 'aria-required': 'true', 'aria-describedby': errors.invoice ? 'invoice-error' : undefined } }" filter />
          <small v-if="errors.invoice" id="invoice-error" class="field-error">{{ errors.invoice }}</small>
        </template>
        <small v-if="!choices.length" class="muted">Create a {{ model === 'invoice' ? 'customer' : 'invoice' }} first.</small>
        <label for="reference">Reference <span aria-hidden="true">*</span></label>
        <InputText id="reference" v-model="form.reference" required :maxlength="40" :invalid="!!errors.reference" aria-describedby="reference-error" :placeholder="model === 'invoice' ? 'INV-001' : 'PAY-001'" />
        <small v-if="errors.reference" id="reference-error" class="field-error">{{ errors.reference }}</small>
        <template v-if="model === 'invoice'">
          <label for="total">Total <span aria-hidden="true">*</span></label>
          <InputText id="total" v-model="form.total" required inputmode="decimal" placeholder="0.00" :invalid="!!errors.total" aria-describedby="total-error" />
          <small v-if="errors.total" id="total-error" class="field-error">{{ errors.total }}</small>
          <label for="posting-date">Posting date</label>
          <InputText id="posting-date" v-model="form.posting_date" type="date" />
          <small v-if="errors.posting_date" class="field-error">{{ errors.posting_date }}</small>
          <label for="status">Status</label>
          <select id="status" v-model="form.status"><option value="draft">Draft</option><option value="sent">Sent</option></select>
          <small v-if="errors.status" class="field-error">{{ errors.status }}</small>
          <label for="tax-rate">Tax rate (%)</label>
          <InputText id="tax-rate" v-model="form.tax_rate" inputmode="decimal" />
          <small v-if="errors.tax_rate" class="field-error">{{ errors.tax_rate }}</small>
          <small class="muted">Recorded separately; this does not calculate the total.</small>
        </template>
        <template v-else>
          <label for="amount">Amount <span aria-hidden="true">*</span></label>
          <InputText id="amount" v-model="form.amount" required inputmode="decimal" placeholder="0.00" :invalid="!!errors.amount" aria-describedby="amount-error" />
          <small v-if="errors.amount" id="amount-error" class="field-error">{{ errors.amount }}</small>
          <label for="received-at">Received at (with timezone)</label>
          <InputText id="received-at" v-model="form.received_at" placeholder="2026-10-28T12:00:00+04:00" />
          <small v-if="errors.received_at" class="field-error">{{ errors.received_at }}</small>
        </template>
        <small class="muted">Use up to two decimal places. References must be unique.</small>
      </template>
      <Message v-if="errors._form" severity="error" :closable="false">{{ errors._form }}</Message>
      <Button type="submit" :label="busy ? 'Saving…' : record ? 'Save changes' : `Create ${model}`" :loading="busy" :disabled="busy || disabled" />
      <Button v-if="record" label="Cancel edit" text :disabled="busy" @click="emit('cancel')" />
    </fieldset>
  </form>
</template>
