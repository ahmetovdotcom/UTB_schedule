<script setup>
import { computed, ref } from 'vue'
import { useScheduleStore } from '../stores/schedule'

// Explains the last auto-generate run: which cards stayed in the pool and why,
// and which pairs were placed into an unsuitable room.
const store = useScheduleStore()
const expanded = ref(true)
const showWarnings = ref(false)

const report = computed(() => store.autoReport)
const unplaced = computed(() => report.value?.unplaced_loads ?? [])
const warnings = computed(() => report.value?.warnings ?? [])
const unplacedHours = computed(() => unplaced.value.reduce((sum, row) => sum + row.hours, 0))

function title(row) {
  const groups = row.groups.length ? row.groups.join(', ') : 'без групп'
  return `${row.subject} · ${row.lesson_type} · ${groups}`
}
</script>

<template>
  <div v-if="report" class="border-b border-gray-200 bg-white text-sm shrink-0 max-h-[40vh] flex flex-col">
    <div class="flex items-center gap-3 px-3 py-2 bg-gray-50 border-b border-gray-100">
      <button class="font-medium text-gray-800 hover:text-gray-950" @click="expanded = !expanded">
        {{ expanded ? '▾' : '▸' }} Отчёт автогенерации
      </button>
      <span v-if="unplaced.length" class="text-red-700">
        не поставлено: {{ unplacedHours }} ч. в {{ unplaced.length }} карточках
      </span>
      <span v-if="warnings.length" class="text-amber-700">
        в неподходящем помещении: {{ warnings.length }}
      </span>
      <button
        class="ml-auto text-gray-400 hover:text-gray-800 text-lg leading-none"
        title="Скрыть отчёт"
        @click="store.autoReport = null"
      >
        ×
      </button>
    </div>

    <div v-if="expanded" class="overflow-y-auto px-3 py-2 space-y-2">
      <div
        v-for="row in unplaced"
        :key="`u-${row.academic_load_id}`"
        class="rounded-md border border-red-200 bg-red-50 p-2"
      >
        <div class="flex items-start gap-2">
          <div class="flex-1 min-w-0">
            <div class="font-medium text-gray-900">{{ title(row) }}</div>
            <div class="text-xs text-gray-600">
              {{ row.teacher }} · {{ row.students }} студ. · не поставлено {{ row.hours }} из
              {{ row.total_hours }} ч.
            </div>
          </div>
          <button
            class="shrink-0 text-xs px-2 py-0.5 rounded border border-gray-300 bg-white hover:bg-gray-100"
            title="Открыть расписание группы — карточка будет в пуле слева"
            @click="store.focusLoad(row)"
          >
            Показать
          </button>
        </div>
        <div class="mt-1 text-red-800">{{ row.reason }}</div>
        <ul v-if="row.details.length" class="mt-0.5 text-xs text-gray-700 list-disc pl-5">
          <li v-for="(detail, i) in row.details" :key="i">{{ detail }}</li>
        </ul>
      </div>

      <div v-if="warnings.length">
        <button class="text-amber-800 hover:text-amber-950 text-sm" @click="showWarnings = !showWarnings">
          {{ showWarnings ? '▾' : '▸' }} Поставлены в неподходящее помещение ({{ warnings.length }})
        </button>
        <div v-if="showWarnings" class="mt-1 space-y-1">
          <div
            v-for="(row, i) in warnings"
            :key="`w-${i}`"
            class="rounded-md border border-amber-200 bg-amber-50 p-2 flex items-start gap-2"
          >
            <div class="flex-1 min-w-0">
              <div class="font-medium text-gray-900">{{ title(row) }}</div>
              <div class="text-xs text-gray-600">{{ row.when }} · {{ row.room }} · {{ row.teacher }}</div>
              <div class="text-xs text-amber-900">{{ row.messages.join('; ') }}</div>
            </div>
            <button
              class="shrink-0 text-xs px-2 py-0.5 rounded border border-gray-300 bg-white hover:bg-gray-100"
              @click="store.focusLoad(row)"
            >
              Показать
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
