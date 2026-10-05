<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'

const busy = ref(false)
const state = ref(null)
const message = ref('')
const failed = ref(false)

onMounted(async () => {
  try { state.value = (await api.get('/api/publication')).data }
  catch { message.value = 'Не удалось загрузить статус публикации'; failed.value = true }
})

async function publish() {
  if (!confirm('Опубликовать текущее расписание и полный список групп? У студентов будут заменены все пары, добавлены новые и удалены отсутствующие группы. Нерасставленные пары опубликованы не будут.')) return
  busy.value = true
  failed.value = false
  message.value = ''
  try {
    const { data } = await api.post('/api/publication')
    state.value = { configured: true, ...data }
    message.value = `Опубликовано. Групп: ${data.groups_count}. Пар по группам: ${data.lessons_count}.`
  } catch (error) {
    failed.value = true
    message.value = typeof error.response?.data?.detail === 'string'
      ? error.response.data.detail : 'Не удалось подтвердить публикацию. Проверьте соединение и повторите.'
  } finally { busy.value = false }
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-3 px-3 py-2 border-b border-gray-200 bg-gray-50 text-sm">
    <button :disabled="busy || state?.configured === false" @click="publish"
      class="rounded-md bg-blue-600 text-white px-3 py-1.5 hover:bg-blue-700 disabled:opacity-50">
      {{ busy ? 'Публикую…' : 'Опубликовать для студентов' }}
    </button>
    <span class="text-gray-600" v-if="state?.published_at">
      Последняя подтверждённая публикация: {{ new Date(state.published_at).toLocaleString('ru-RU') }}
    </span>
    <span class="text-gray-600" v-else-if="state?.configured === false">Публикация ещё не подключена. Обратитесь к администратору.</span>
    <span class="text-gray-500" v-else>Изменения появятся у студентов после публикации.</span>
    <span v-if="message" :role="failed ? 'alert' : 'status'" :class="failed ? 'text-red-700' : 'text-green-700'">{{ message }}</span>
  </div>
</template>
