<script setup>
import { onMounted, ref } from 'vue'
import Button from 'primevue/button'
import { authRequest, request } from './api.js'
defineProps({ currentUser: Object })
const users = ref([]), username = ref(''), password = ref(''), role = ref('viewer'), error = ref(''), busy = ref(false), success = ref('')
async function load() { users.value = (await request('/api/auth/users')).items }
async function create() {
  busy.value = true; error.value = ''; success.value = ''
  try { await authRequest('users', { username: username.value, password: password.value, role: role.value }); username.value = ''; password.value = ''; success.value = 'Account created.'; await load() }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function update(user, values) {
  busy.value = true; error.value = ''; success.value = ''
  try { await authRequest(`users/${user.id}`, values, 'PATCH'); await load() }
  catch (failure) { error.value = failure.message; await load().catch(() => {}) }
  finally { busy.value = false }
}
onMounted(() => load().catch(failure => { error.value = failure.message }))
</script>
<template>
  <section class="accounts-card"><h1>Accounts</h1><p>Account changes end that user's active sessions. Keep one active administrator.</p>
    <p v-if="error" role="alert" class="field-error">{{ error }}</p><p v-if="success" role="status">{{ success }}</p>
    <table><thead><tr><th>Username</th><th>Role</th><th>Status</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td>{{ user.username }}</td><td><select :aria-label="`Role for ${user.username}`" :value="user.role" :disabled="busy" @change="update(user, { role: $event.target.value })"><option>admin</option><option>editor</option><option>viewer</option></select></td><td><Button :label="user.active ? 'Disable' : 'Enable'" :aria-label="`${user.active ? 'Disable' : 'Enable'} ${user.username}`" :disabled="busy" @click="update(user, { active: !user.active })" /></td></tr></tbody></table>
    <h2>Add account</h2><form @submit.prevent="create"><label for="account-name">Username</label><input id="account-name" v-model="username" autocomplete="off" required minlength="3" maxlength="64" /><label for="account-password">Initial password</label><input id="account-password" v-model="password" type="password" autocomplete="new-password" required minlength="15" maxlength="128" /><label for="account-role">Role</label><select id="account-role" v-model="role"><option>viewer</option><option>editor</option><option>admin</option></select><Button type="submit" label="Create account" :disabled="busy" /></form>
  </section>
</template>
