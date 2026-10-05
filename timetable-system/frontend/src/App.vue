<script setup>
import { onMounted, ref } from 'vue'
import api from './api'
import TimetableApp from './TimetableApp.vue'
import AnalyticsDashboard from './components/AnalyticsDashboard.vue'
const adminPage = window.location.pathname.replace(/\/$/, '') === '/admin'

const user = ref(null)
const checking = ref(true)
const busy = ref(false)
const username = ref('')
const password = ref('')
const error = ref('')
const checkFailed = ref(false)

async function checkSession() {
  checking.value = true
  checkFailed.value = false
  error.value = ''
  try {
    const { data } = await api.get('/api/auth/me')
    user.value = data
  } catch (e) {
    if (e.response?.status !== 401) {
      checkFailed.value = true
      error.value = 'Не удалось подключиться к серверу. Попробуйте ещё раз.'
    }
  } finally {
    checking.value = false
  }
}
onMounted(checkSession)

async function login() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const { data } = await api.post('/api/auth/login', {
      username: username.value.trim(), password: password.value,
    })
    password.value = ''
    // Confirm that the browser actually accepted and returned the session cookie.
    // Otherwise mounting the timetable would trigger a reload loop on its 401.
    try {
      await api.get('/api/auth/me')
    } catch {
      error.value = 'Не удалось сохранить сеанс. Проверьте, разрешены ли cookie, или обратитесь к администратору.'
      return
    }
    user.value = data
  } catch (e) {
    error.value = typeof e.response?.data?.detail === 'string'
      ? e.response.data.detail : 'Не удалось войти. Проверьте соединение с сервером.'
    password.value = ''
  } finally {
    busy.value = false
  }
}

async function logout() {
  busy.value = true
  error.value = ''
  try {
    await api.post('/api/auth/logout')
    window.location.reload()
  } catch (e) {
    if (e.response?.status === 401) window.location.reload()
    else error.value = 'Не удалось выйти. Попробуйте ещё раз.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="checking" class="min-h-screen grid place-items-center bg-gray-50 text-gray-600" role="status">
    Проверка входа…
  </div>
  <div v-else-if="user" class="h-screen flex flex-col">
    <div class="flex items-center justify-end gap-4 border-b border-gray-200 px-4 py-2 text-sm bg-gray-50">
      <span v-if="error" role="alert" class="text-red-700">{{ error }}</span>
      <a v-if="adminPage" href="/" class="text-blue-700">Расписание сотрудников</a>
      <a v-else-if="user.is_admin" href="/admin" class="text-blue-700">Статистика</a>
      <span class="text-gray-600">{{ user.username }}</span>
      <button class="rounded px-3 py-1 text-gray-900 hover:bg-gray-200 disabled:opacity-50" :disabled="busy" @click="logout">
        {{ busy ? 'Выход…' : 'Выйти' }}
      </button>
    </div>
    <AnalyticsDashboard v-if="adminPage && user.is_admin" />
    <main v-else-if="adminPage" class="p-8" role="alert">Статистика доступна только администратору.</main>
    <TimetableApp v-else class="flex-1 min-h-0" />
  </div>
  <main v-else class="min-h-screen flex items-center justify-center bg-gray-50 px-4 py-8">
    <section class="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-8 shadow-sm" aria-labelledby="login-title">
      <h1 id="login-title" class="text-2xl font-semibold text-gray-900">Вход в систему</h1>
      <p class="mt-2 text-sm text-gray-500">{{ adminPage ? 'Панель статистики · Только для администратора' : 'Система составления расписания' }}</p>
      <p v-if="error" role="alert" class="mt-5 rounded-md bg-red-50 p-3 text-sm text-red-700">{{ error }}</p>
      <button v-if="checkFailed" class="mt-6 w-full rounded-md bg-gray-900 px-4 py-2.5 text-white" @click="checkSession">
        Повторить подключение
      </button>
      <form v-else class="mt-6 space-y-5" @submit.prevent="login">
        <div>
          <label for="username" class="block text-sm font-medium text-gray-700">Логин</label>
          <input id="username" v-model="username" name="username" type="text" autocomplete="username" required maxlength="100"
            :disabled="busy" autofocus class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
        </div>
        <div>
          <label for="password" class="block text-sm font-medium text-gray-700">Пароль</label>
          <input id="password" v-model="password" name="password" type="password" autocomplete="current-password" required maxlength="1024"
            :disabled="busy" class="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
        </div>
        <button type="submit" :disabled="busy" class="w-full rounded-md bg-gray-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50">
          {{ busy ? 'Вход…' : 'Войти' }}
        </button>
      </form>
    </section>
  </main>
</template>
