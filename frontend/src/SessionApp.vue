<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import Button from 'primevue/button'
import App from './App.vue'
import Accounts from './Accounts.vue'
import { request, setCsrf, authRequest } from './api.js'
const user = ref(null), setup = ref(false), loading = ref(true), busy = ref(false), error = ref(''), accounts = ref(false)
const username = ref(''), password = ref(''), tenant = ref(null)
let alive = true
function expired() { user.value = null; accounts.value = false; setCsrf(''); error.value = 'Your session ended. Sign in again.' }
async function session() {
  try {
    const data = await request('/api/auth/session')
    if (!alive) return
    user.value = data.user; setup.value = data.setup; tenant.value = data.tenant || null; setCsrf(data.csrf || '')
  } catch (failure) { if (alive) error.value = failure.message }
  finally { if (alive) loading.value = false }
}
async function login() {
  busy.value = true; error.value = ''
  try {
    if (setup.value) {
      await authRequest('setup', { username: username.value, password: password.value })
      setup.value = false
    }
    const data = await authRequest('login', { username: username.value, password: password.value })
    user.value = data.user; setCsrf(data.csrf); password.value = ''
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function logout() {
  busy.value = true
  try { await authRequest('logout', {}); user.value = null; accounts.value = false; setCsrf('') }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
onMounted(() => { window.addEventListener('evlet-session-ended', expired); session() })
onBeforeUnmount(() => { alive = false; window.removeEventListener('evlet-session-ended', expired) })
</script>
<template>
  <div v-if="loading" class="auth-card" role="status">Connecting…</div>
  <section v-else-if="!user" class="auth-card">
    <p v-if="tenant" class="tenant-name">{{ tenant.label }}</p>
    <h1>{{ setup ? 'Set up your Playground' : 'Sign in to Evlet' }}</h1>
    <p>{{ setup ? 'Create the first administrator account for this local database.' : 'Use your local username and password.' }}</p>
    <p v-if="error" role="alert" class="field-error">{{ error }}</p>
    <form @submit.prevent="login">
      <label for="username">Username</label><input id="username" v-model="username" autocomplete="username" required minlength="3" maxlength="64" :disabled="busy" />
      <label for="password">Password</label><input id="password" v-model="password" type="password" :autocomplete="setup ? 'new-password' : 'current-password'" required :minlength="setup ? 15 : undefined" maxlength="128" :disabled="busy" />
      <small v-if="setup">Use at least 15 characters.</small>
      <Button type="submit" :label="setup ? 'Create administrator' : 'Sign in'" :disabled="busy" />
    </form>
  </section>
  <template v-else>
    <header class="account-bar"><strong v-if="tenant" class="tenant-name">{{ tenant.label }}</strong><span>{{ user.username }} · {{ user.role }}</span><Button v-if="user.role === 'admin'" :label="accounts ? 'Back to records' : 'Accounts'" text @click="accounts = !accounts" /><Button label="Sign out" text :disabled="busy" @click="logout" /></header>
    <p v-if="error" role="alert" class="field-error">{{ error }}</p>
    <Accounts v-if="accounts" :current-user="user" /><App v-else :key="user.id" />
  </template>
</template>
