<script setup>
import { ref, computed, watch } from 'vue'
import { useScheduleStore, DAYS } from '../stores/schedule'

const props = defineProps({
  // { loadId, itemId, day, slotId } | null
  request: { type: Object, default: null },
})
const emit = defineEmits(['close'])

const store = useScheduleStore()
const roomResults = ref([]) // [{ room, status: 'ok' | 'warn' | 'error', messages: [] }]
const loadingRooms = ref(false)
const roomsError = ref('')
const saving = ref(false)

const load = computed(() => (props.request ? store.loadById(props.request.loadId) : null))
const dayLabel = computed(() => DAYS.find((d) => d.value === props.request?.day)?.label ?? '')
const slot = computed(() => (props.request ? store.timeSlotById(props.request.slotId) : null))

watch(
  () => props.request,
  async (req, _previous, onCleanup) => {
    let stale = false
    onCleanup(() => { stale = true })
    roomResults.value = []
    roomsError.value = ''
    loadingRooms.value = !!req
    if (!req) return
    try {
      const validations = await store.roomOptions(req.loadId, req.itemId, req.day, req.slotId)
      if (stale) return
      const roomsById = new Map(store.rooms.map((room) => [room.id, room]))
      const results = validations.filter((data) => roomsById.has(data.room_id)).map((data) => ({
        room: roomsById.get(data.room_id),
        streamItemId: data.stream_item_id,
        streamGroupIds: data.stream_group_ids,
        studentsTotal: data.students_total,
        status: !data.is_valid ? 'error' : data.warnings.length ? 'warn' : 'ok',
        messages: [...data.errors, ...data.warnings],
      }))
      const studentsTotal = store.loadById(req.loadId)?.students_total ?? 0
      const rank = { ok: 0, warn: 1, error: 2 }
      results.sort((a, b) => {
        if (rank[a.status] !== rank[b.status]) return rank[a.status] - rank[b.status]
        if (a.status === 'ok') return a.room.capacity - studentsTotal - (b.room.capacity - studentsTotal)
        if (a.status === 'warn') return b.room.capacity - a.room.capacity
        return 0
      })
      roomResults.value = results
    } catch {
      if (!stale) roomsError.value = 'Не удалось проверить аудитории. Закройте окно и попробуйте снова.'
    } finally {
      if (!stale) loadingRooms.value = false
    }
  },
  { immediate: true },
)

function close() {
  emit('close')
}

async function pickRoom(result) {
  if (result.status === 'error' || !props.request || saving.value) return
  saving.value = true
  roomsError.value = ''
  try {
    await store.assign({
      loadId: props.request.loadId,
      itemId: props.request.itemId,
      day: props.request.day,
      timeSlotId: props.request.slotId,
      roomId: result.room.id,
      streamItemId: result.streamItemId,
    })
    close()
  } catch {
    roomsError.value = store.error
  } finally {
    saving.value = false
  }
}

function onKeydown(event) {
  if (event.key === 'Escape' && props.request) close()
}
watch(
  () => !!props.request,
  (isOpen) => {
    if (isOpen) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)
</script>

<template>
  <div
    v-if="request"
    class="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
    @click.self="close"
  >
    <div class="bg-white rounded-lg shadow-xl w-[28rem] max-h-[80vh] flex flex-col">
      <div class="p-4 border-b border-gray-200">
        <h3 class="font-semibold text-gray-900">{{ store.subjectById(load?.subject_id)?.name }}</h3>
        <p class="text-sm text-gray-500">
          {{ dayLabel }}, {{ slot?.slot_number }} пара ({{ slot?.start_time }}–{{ slot?.end_time }}) ·
          {{ load?.students_total }} студ.
        </p>
        <p v-if="load?.group_ids.length > 1" class="text-xs text-gray-600 mt-1">
          Поток: {{ store.groupCodes(load.group_ids) }}
        </p>
        <p class="text-xs text-gray-500 mt-1">
          Если мест не хватает, появится предупреждение. Выбрать аудиторию всё равно можно.
        </p>
        <p v-if="load?.room_type" class="text-xs text-gray-500">
          Нужно помещение: {{ store.roomTypeLabel(load.room_type) }}
        </p>
      </div>
      <div class="overflow-y-auto p-2 space-y-1">
        <p v-if="roomsError" role="alert" class="text-sm text-red-600 p-2">{{ roomsError }}</p>
        <p v-if="loadingRooms" class="text-sm text-gray-400 p-2">Проверяем аудитории…</p>
        <button
          v-for="result in roomResults"
          :key="result.room.id"
          :disabled="saving || result.status === 'error'"
          class="w-full text-left rounded-md p-2 border text-sm flex flex-col gap-0.5 transition-colors"
          :class="{
            'border-green-300 bg-green-50 hover:bg-green-100 cursor-pointer': result.status === 'ok',
            'border-amber-300 bg-amber-50 hover:bg-amber-100 cursor-pointer': result.status === 'warn',
            'border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed': result.status === 'error',
          }"
          @click="pickRoom(result)"
        >
          <div class="flex items-center justify-between">
            <span class="font-medium">
              <span class="text-gray-500 font-normal">{{ result.room.building_name }} ·</span>
              {{ result.room.number }}
              <span class="text-[10px] ml-1 px-1 rounded bg-gray-200 text-gray-700 font-normal">
                {{ store.roomTypeLabel(result.room.room_type) }}
              </span>
            </span>
            <span class="shrink-0">{{ result.room.capacity }} мест · {{ result.room.floor }} этаж</span>
          </div>
          <div v-if="result.streamItemId" class="text-xs font-medium">
            Объединить в поток: {{ store.groupCodes(result.streamGroupIds) }}
            · {{ result.studentsTotal }} студ.
            <span class="block font-normal">Объединится только эта пара. Остальные часы останутся отдельно.</span>
          </div>
          <div v-if="result.room.department_name" class="text-[11px] text-gray-500">
            {{ result.room.department_name }}
          </div>
          <div v-if="result.messages.length > 0" class="text-xs">
            {{ result.messages.join('; ') }}
          </div>
        </button>
      </div>
      <div class="p-3 border-t border-gray-200 flex justify-end">
        <button class="text-sm text-gray-500 hover:text-gray-800 px-3 py-1" @click="close">
          Отмена
        </button>
      </div>
    </div>
  </div>
</template>
