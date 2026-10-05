<script setup>
import { computed, reactive, ref } from 'vue'
import { useScheduleStore, LESSON_TYPE_LABELS } from '../stores/schedule'
import { matchesSearch } from '../utils/search'
import EntitySearch from './EntitySearch.vue'
import EntityMultiSearch from './EntityMultiSearch.vue'

// Academic loads get their own editor (not EntityTable) because of the
// group multi-select and the subject/teacher/type dropdowns.
const store = useScheduleStore()
const editingId = ref(null) // null = "creating new"
const saving = ref(false)
const formError = ref('')
const tableQuery = ref('')

const byLabel = (a, b) => a.label.localeCompare(b.label, 'ru', { numeric: true })
const subjectOptions = computed(() =>
  store.subjects.map((s) => ({ id: s.id, label: s.name })).sort(byLabel),
)
const teacherOptions = computed(() =>
  store.teachers
    .map((t) => ({ id: t.id, label: t.full_name, hint: t.department_name ?? '' }))
    .sort(byLabel),
)
const groupOptions = computed(() =>
  store.groups
    .map((g) => ({ id: g.id, label: g.code, hint: `${g.course} курс · ${g.students_count} студ.` }))
    .sort(byLabel),
)
const selectedStudents = computed(() =>
  form.group_ids.reduce((sum, id) => sum + (store.groupById(id)?.students_count ?? 0), 0),
)

const filteredLoads = computed(() =>
  store.loads.filter((l) =>
    matchesSearch(
      tableQuery.value,
      store.subjectById(l.subject_id)?.name,
      store.teacherById(l.teacher_id)?.full_name,
      store.groupCodes(l.group_ids),
      LESSON_TYPE_LABELS[l.lesson_type],
    ),
  ),
)

function emptyForm() {
  return {
    subject_id: null,
    teacher_id: null,
    lesson_type: 'lecture',
    total_hours_per_week: 2,
    group_ids: [],
    room_type: null, // null = по типу занятия
  }
}
const form = reactive(emptyForm())

function startEdit(loadItem) {
  editingId.value = loadItem.id
  form.subject_id = loadItem.subject_id
  form.teacher_id = loadItem.teacher_id
  form.lesson_type = loadItem.lesson_type
  form.total_hours_per_week = loadItem.total_hours_per_week
  form.group_ids = [...loadItem.group_ids]
  form.room_type = loadItem.room_type
  formError.value = ''
}
function cancelEdit() {
  editingId.value = null
  formError.value = ''
  Object.assign(form, emptyForm())
}

async function submit() {
  if (!form.subject_id || !form.teacher_id) {
    formError.value = 'Выберите предмет и преподавателя'
    return
  }
  formError.value = ''
  saving.value = true
  try {
    const payload = { ...form }
    if (editingId.value === null) {
      await store.createEntity('loads', payload)
    } else {
      await store.updateEntity('loads', editingId.value, payload)
    }
    cancelEdit()
  } catch {
    // store.error already holds the message; keep the form open so the
    // user can fix the values and retry.
  } finally {
    saving.value = false
  }
}

async function remove(loadItem) {
  if (!confirm('Удалить карточку нагрузки? Расставленные пары тоже будут удалены.')) return
  try {
    await store.deleteEntity('loads', loadItem.id)
    if (editingId.value === loadItem.id) cancelEdit()
  } catch {
    // store.error already set
  }
}
</script>

<template>
  <div class="p-4">
    <h2 class="font-semibold text-gray-900 mb-3">Нагрузка</h2>

    <form
      class="flex flex-wrap items-start gap-3 mb-4 p-3 bg-gray-50 rounded-md border border-gray-200"
      @submit.prevent="submit"
    >
      <div class="flex flex-col">
        <label class="text-xs text-gray-500 mb-0.5">Предмет</label>
        <EntitySearch
          v-model="form.subject_id"
          :options="subjectOptions"
          placeholder="Поиск предмета…"
          width-class="w-72"
        />
      </div>
      <div class="flex flex-col">
        <label class="text-xs text-gray-500 mb-0.5">Преподаватель</label>
        <EntitySearch
          v-model="form.teacher_id"
          :options="teacherOptions"
          placeholder="Поиск преподавателя…"
          width-class="w-64"
        />
      </div>
      <div class="flex flex-col">
        <label class="text-xs text-gray-500 mb-0.5">Тип</label>
        <select v-model="form.lesson_type" class="border border-gray-300 rounded px-2 py-1 text-sm">
          <option v-for="(label, key) in LESSON_TYPE_LABELS" :key="key" :value="key">
            {{ label }}
          </option>
        </select>
      </div>
      <div class="flex flex-col">
        <label class="text-xs text-gray-500 mb-0.5">Нужное помещение</label>
        <select v-model="form.room_type" class="border border-gray-300 rounded px-2 py-1 text-sm">
          <option :value="null">По типу занятия</option>
          <option
            v-for="t in store.roomTypes.filter((t) => t.value !== 'other')"
            :key="t.value"
            :value="t.value"
          >
            {{ t.label }}
          </option>
        </select>
      </div>
      <div class="flex flex-col">
        <label class="text-xs text-gray-500 mb-0.5">Часов в неделю</label>
        <input
          v-model.number="form.total_hours_per_week"
          type="number"
          min="1"
          class="border border-gray-300 rounded px-2 py-1 text-sm w-24"
          required
        />
      </div>
      <div class="flex flex-col">
        <label class="text-xs text-gray-500 mb-0.5">
          Группы
          <span v-if="form.group_ids.length" class="text-gray-400">
            · {{ form.group_ids.length }} гр., {{ selectedStudents }} студ.
          </span>
        </label>
        <EntityMultiSearch
          v-model="form.group_ids"
          :options="groupOptions"
          placeholder="Поиск группы…"
          width-class="w-96"
        />
      </div>
      <div class="flex gap-2 pt-4">
        <button
          type="submit"
          :disabled="saving"
          class="px-3 py-1.5 text-sm rounded bg-gray-900 text-white hover:bg-gray-700 disabled:opacity-50"
        >
          {{ editingId === null ? 'Добавить' : 'Сохранить' }}
        </button>
        <button
          v-if="editingId !== null"
          type="button"
          class="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-800"
          @click="cancelEdit"
        >
          Отмена
        </button>
      </div>
      <p v-if="formError" class="basis-full text-sm text-red-600">{{ formError }}</p>
    </form>

    <div class="flex items-center gap-2 mb-2">
      <input
        v-model="tableQuery"
        type="search"
        placeholder="Фильтр: предмет, преподаватель, группа, тип…"
        class="w-96 border border-gray-300 rounded-md text-sm px-2 py-1"
      />
      <span v-if="tableQuery" class="text-xs text-gray-400">
        {{ filteredLoads.length }} из {{ store.loads.length }}
      </span>
    </div>

    <table class="w-full text-sm border-collapse">
      <thead>
        <tr class="text-left text-xs text-gray-500 uppercase">
          <th class="p-2 border-b border-gray-200">Предмет</th>
          <th class="p-2 border-b border-gray-200">Тип</th>
          <th class="p-2 border-b border-gray-200">Помещение</th>
          <th class="p-2 border-b border-gray-200">Преподаватель</th>
          <th class="p-2 border-b border-gray-200">Группы</th>
          <th class="p-2 border-b border-gray-200">Часов</th>
          <th class="p-2 border-b border-gray-200 w-24"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="loadItem in filteredLoads" :key="loadItem.id" class="hover:bg-gray-50">
          <td class="p-2 border-b border-gray-100">{{ store.subjectById(loadItem.subject_id)?.name }}</td>
          <td class="p-2 border-b border-gray-100">{{ LESSON_TYPE_LABELS[loadItem.lesson_type] }}</td>
          <td class="p-2 border-b border-gray-100 text-gray-500">
            {{ loadItem.room_type ? store.roomTypeLabel(loadItem.room_type) : 'по типу' }}
          </td>
          <td class="p-2 border-b border-gray-100">
            {{ store.teacherById(loadItem.teacher_id)?.full_name }}
          </td>
          <td class="p-2 border-b border-gray-100">{{ store.groupCodes(loadItem.group_ids) }}</td>
          <td class="p-2 border-b border-gray-100">
            {{ store.remaining(loadItem) }} / {{ loadItem.total_hours_per_week }} ост.
          </td>
          <td class="p-2 border-b border-gray-100 text-right whitespace-nowrap">
            <button class="text-gray-400 hover:text-gray-800 text-xs mr-2" @click="startEdit(loadItem)">
              Изм.
            </button>
            <button class="text-gray-400 hover:text-red-600 text-xs" @click="remove(loadItem)">
              Удал.
            </button>
          </td>
        </tr>
        <tr v-if="filteredLoads.length === 0">
          <td colspan="7" class="p-3 text-center text-gray-400 text-sm">
            {{ store.loads.length === 0 ? 'Пусто' : 'Ничего не найдено' }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
