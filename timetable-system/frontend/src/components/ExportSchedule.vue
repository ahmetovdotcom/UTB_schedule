<script setup>
import { computed, ref, watch } from 'vue'
import api from '../api'
import { useScheduleStore } from '../stores/schedule'

const store = useScheduleStore()
const courses = computed(() => [...new Set(store.groups.map(g => g.course).filter(c => c > 0))].sort((a, b) => a - b))
const course = ref(null)
const busy = ref(false)
const error = ref('')
watch(courses, values => {
  if (!values.includes(course.value)) course.value = values[0] ?? null
}, { immediate: true })
const withoutDepartment = computed(() => store.groups.filter(g => g.course === course.value && !g.department_id).length)

async function download() {
  if (!course.value || busy.value) return
  const selected = course.value
  busy.value = true
  error.value = ''
  try {
    const { data } = await api.get('/api/schedule/export.xlsx', { params: { course: selected }, responseType: 'blob' })
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = `Расписание-${selected}-курс.xlsx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) {
    error.value = 'Не удалось выгрузить расписание. Попробуйте ещё раз.'
    try {
      const body = JSON.parse(await e.response.data.text())
      if (typeof body.detail === 'string') error.value = body.detail
    } catch { /* Keep the connection-error message. */ }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-3 px-3 py-2 border-b border-gray-200 text-sm">
    <label for="export-course" class="text-gray-600">Выгрузить расписание</label>
    <select id="export-course" v-model="course" :disabled="busy || !courses.length"
      class="border border-gray-300 rounded px-2 py-1.5">
      <option v-if="!courses.length" :value="null">Нет групп</option>
      <option v-for="value in courses" :key="value" :value="value">{{ value }} курс</option>
    </select>
    <button @click="download" :disabled="busy || !course || store.loading"
      class="rounded-md bg-green-700 text-white px-3 py-1.5 hover:bg-green-800 disabled:opacity-50">
      {{ busy ? 'Формирую Excel…' : 'Скачать Excel' }}
    </button>
    <span class="text-gray-500">Текущее расписание · отдельный лист для каждой кафедры</span>
    <span v-if="withoutDepartment" class="text-amber-700">
      Групп без кафедры: {{ withoutDepartment }} — попадут на лист «Без кафедры».
    </span>
    <span v-if="error" role="alert" class="text-red-700">{{ error }}</span>
  </div>
</template>
