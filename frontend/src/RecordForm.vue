<script setup>
import { reactive, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Message from 'primevue/message'

const props = defineProps({
  model: { type: String, required: true },
  choices: { type: Array, default: () => [] },
  errors: { type: Object, default: () => ({}) },
  busy: Boolean,
  disabled: Boolean,
  resetKey: { type: Number, default: 0 },
})
const emit = defineEmits(['save'])
const form = reactive({ name: '', email: '', customer: null, invoice: null, reference: '', total: '', amount: '' })
watch(() => [props.model, props.resetKey], () => {
  Object.assign(form, { name: '', email: '', customer: null, invoice: null, reference: '', total: '', amount: '' })
})
function save() {
  if (props.busy || props.disabled) return
  const data = props.model === 'customer'
    ? { name: form.name, email: form.email || null }
    : props.model === 'invoice'
      ? { customer: form.customer, reference: form.reference, total: form.total }
      : { invoice: form.invoice, reference: form.reference, amount: form.amount }
  emit('save', data)
}
</script>

<template>
  <form class="record-form" @submit.prevent="save">
    <div class="eyebrow">Add a record</div>
    <h2>New {{ model }}</h2>
    <p class="muted form-intro">A few details to keep your work moving.</p>
    <fieldset :disabled="busy || disabled">
      <template v-if="model === 'customer'">
        <label for="name">Name <span aria-hidden="true">*</span></label>
        <InputText id="name" v-model="form.name" required :maxlength="120" :invalid="!!errors.name" :aria-invalid="!!errors.name" aria-describedby="name-error" placeholder="e.g. Acme Studio" />
        <small v-if="errors.name" id="name-error" class="field-error">{{ errors.name }}</small>
        <label for="email">Email <span class="optional">Optional</span></label>
        <InputText id="email" v-model="form.email" type="email" :maxlength="254" :invalid="!!errors.email" aria-describedby="email-error" placeholder="hello@example.com" />
        <small v-if="errors.email" id="email-error" class="field-error">{{ errors.email }}</small>
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
        </template>
        <template v-else>
          <label for="amount">Amount <span aria-hidden="true">*</span></label>
          <InputText id="amount" v-model="form.amount" required inputmode="decimal" placeholder="0.00" :invalid="!!errors.amount" aria-describedby="amount-error" />
          <small v-if="errors.amount" id="amount-error" class="field-error">{{ errors.amount }}</small>
        </template>
        <small class="muted">Use up to two decimal places. References must be unique.</small>
      </template>
      <Message v-if="errors._form" severity="error" :closable="false">{{ errors._form }}</Message>
      <Button type="submit" :label="busy ? 'Saving…' : `Create ${model}`" :loading="busy" :disabled="busy || disabled" />
    </fieldset>
  </form>
</template>
