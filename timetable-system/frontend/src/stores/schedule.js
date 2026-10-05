import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from '../api'

export const DAYS = [
  { value: 1, label: 'Пн' },
  { value: 2, label: 'Вт' },
  { value: 3, label: 'Ср' },
  { value: 4, label: 'Чт' },
  { value: 5, label: 'Пт' },
  { value: 6, label: 'Сб' },
]

export const LESSON_TYPE_LABELS = {
  lecture: 'Лекция',
  practice: 'Практика',
  lab: 'Лаб',
}

export const useScheduleStore = defineStore('schedule', () => {
  const roomTypes = ref([]) // [{ value, label }] from the backend (room_rules.py)
  const departments = ref([])
  const buildings = ref([])
  const rooms = ref([])
  const teachers = ref([])
  const groups = ref([])
  const subjects = ref([])
  const timeSlots = ref([])
  const loads = ref([])
  const items = ref([])

  const viewMode = ref('group') // 'group' | 'room' | 'teacher'
  const selectedId = ref(null)

  const dragging = ref(null) // { loadId, itemId: number | null } | null
  const cellCache = ref(new Map()) // "day-slot" -> { is_valid, errors, warnings }

  const loading = ref(false)
  const error = ref('')
  const info = ref('') // non-error status banner, e.g. auto-generate results
  // Last auto-generate result with explanations: { unplaced_loads[], warnings[] } | null
  const autoReport = ref(null)

  // --- lookups -------------------------------------------------------

  function loadById(id) {
    return loads.value.find((l) => l.id === id) ?? null
  }
  function subjectById(id) {
    return subjects.value.find((s) => s.id === id) ?? null
  }
  function teacherById(id) {
    return teachers.value.find((t) => t.id === id) ?? null
  }
  function groupById(id) {
    return groups.value.find((g) => g.id === id) ?? null
  }
  function departmentById(id) {
    return departments.value.find((d) => d.id === id) ?? null
  }
  function roomTypeLabel(value) {
    return roomTypes.value.find((t) => t.value === value)?.label ?? value
  }
  function roomById(id) {
    return rooms.value.find((r) => r.id === id) ?? null
  }
  // Room numbers repeat between blocks (300 in 1 and 2 блок), so show the block.
  function roomLabel(room) {
    if (!room) return ''
    return room.building_name ? `${room.building_name} · ${room.number}` : room.number
  }
  function timeSlotById(id) {
    return timeSlots.value.find((t) => t.id === id) ?? null
  }
  function groupCodes(groupIds) {
    return groupIds.map((id) => groupById(id)?.code ?? '?').join(', ')
  }

  function remaining(load) {
    const placed = items.value.filter((i) => i.academic_load_id === load.id).length
    return load.total_hours_per_week - placed
  }

  // [{ id, label, hint }] for the entity picker; `hint` is shown in grey and is
  // searchable too (course, department, room type).
  const byLabel = (a, b) => a.label.localeCompare(b.label, 'ru', { numeric: true })
  const entityOptions = computed(() => {
    if (viewMode.value === 'group') {
      return groups.value
        .map((g) => ({ id: g.id, label: g.code, hint: `${g.course} курс · ${g.students_count} студ.` }))
        .sort(byLabel)
    }
    if (viewMode.value === 'teacher') {
      return teachers.value
        .map((t) => ({ id: t.id, label: t.full_name, hint: t.department_name ?? '' }))
        .sort(byLabel)
    }
    return rooms.value
      .map((r) => ({
        id: r.id,
        label: roomLabel(r),
        hint: `${roomTypeLabel(r.room_type)} · ${r.capacity} мест`,
      }))
      .sort(byLabel)
  })

  const poolLoads = computed(() => {
    const unfinished = loads.value.filter((l) => remaining(l) > 0)
    if (viewMode.value === 'group') {
      return unfinished.filter((l) => l.group_ids.includes(selectedId.value))
    }
    if (viewMode.value === 'teacher') {
      return unfinished.filter((l) => l.teacher_id === selectedId.value)
    }
    return unfinished
  })

  function itemAt(day, timeSlotId) {
    const candidates = items.value.filter(
      (i) => i.day_of_week === day && i.time_slot_id === timeSlotId,
    )
    if (viewMode.value === 'room') {
      return candidates.find((i) => i.room_id === selectedId.value) ?? null
    }
    return (
      candidates.find((i) => {
        const load = loadById(i.academic_load_id)
        if (!load) return false
        if (viewMode.value === 'teacher') return load.teacher_id === selectedId.value
        return load.group_ids.includes(selectedId.value)
      }) ?? null
    )
  }

  // --- data loading ----------------------------------------------------

  async function load() {
    loading.value = true
    try {
      const { data } = await axios.get('/api/initial-data')
      roomTypes.value = data.room_types
      departments.value = data.departments
      buildings.value = data.buildings
      rooms.value = data.rooms
      teachers.value = data.teachers
      groups.value = data.groups
      subjects.value = data.subjects
      timeSlots.value = data.time_slots
      loads.value = data.loads
      items.value = data.items
      error.value = ''
      if (selectedId.value === null && entityOptions.value.length > 0) {
        selectedId.value = entityOptions.value[0].id
      }
    } catch (e) {
      error.value = 'Бэкенд недоступен — запустите uvicorn (см. README)'
      throw e
    } finally {
      loading.value = false
    }
  }

  function setViewMode(mode) {
    viewMode.value = mode
    selectedId.value = entityOptions.value.length > 0 ? entityOptions.value[0].id : null
  }

  // --- validation --------------------------------------------------------

  // loadId/itemId are passed explicitly (not read from `dragging`) because
  // `dragging` is cleared by the `dragend` event, which can fire before the
  // room-picker modal's roomOptions call resolves.
  async function validateCell(loadId, itemId, day, timeSlotId, signal) {
    const cache = cellCache.value
    const key = `${day}-${timeSlotId}`
    if (cellCache.value.has(key)) return cellCache.value.get(key)
    const { data } = await axios.post('/api/schedule/validate', {
      academic_load_id: loadId,
      day_of_week: day,
      time_slot_id: timeSlotId,
      current_item_id: itemId,
    }, { signal })
    if (cellCache.value === cache && !signal?.aborted) cache.set(key, data)
    return data
  }

  async function roomOptions(loadId, itemId, day, timeSlotId) {
    const { data } = await axios.post('/api/schedule/room-options', {
      academic_load_id: loadId,
      day_of_week: day,
      time_slot_id: timeSlotId,
      current_item_id: itemId,
    })
    return data
  }

  // --- mutations -----------------------------------------------------

  async function assign({ loadId, itemId, day, timeSlotId, roomId, streamItemId }) {
    try {
      const { data } = await axios.post(streamItemId ? '/api/schedule/join-stream' : '/api/schedule/assign', {
        ...(streamItemId ? { target_item_id: streamItemId } : {}),
        academic_load_id: loadId,
        day_of_week: day,
        time_slot_id: timeSlotId,
        room_id: roomId,
        current_item_id: itemId,
      })
      error.value = ''
      info.value = data.warnings?.length ? `Занятие добавлено. ${data.warnings.join('; ')}` : ''
      await load()
    } catch (e) {
      const detail = e.response?.data?.detail
      error.value = (typeof detail === 'string' ? detail : detail?.errors?.join('; ')) || 'Не удалось поставить пару'
      throw e
    }
  }

  async function unassign(itemId) {
    try {
      await axios.delete(`/api/schedule/unassign/${itemId}`)
      error.value = ''
      info.value = ''
      await load()
    } catch (e) {
      error.value = 'Не удалось убрать пару'
      throw e
    }
  }

  async function autoGenerate() {
    try {
      const { data } = await axios.post('/api/schedule/auto-generate')
      error.value = ''
      const parts = [
        data.unplaced === 0
          ? `Расставлено ${data.placed} из ${data.placed} часов — всё поместилось.`
          : `Расставлено ${data.placed} часов, ${data.unplaced} осталось в пуле — причины ниже.`,
      ]
      if (data.with_warnings > 0) {
        parts.push(
          `${data.with_warnings} пар стоят в неподходящем помещении (тип или чужая кафедра) — нужных не хватило.`,
        )
      }
      info.value = parts.join(' ')
      autoReport.value =
        data.unplaced_loads?.length || data.warnings?.length
          ? { unplaced_loads: data.unplaced_loads ?? [], warnings: data.warnings ?? [] }
          : null
      await load()
    } catch (e) {
      error.value = 'Не удалось составить расписание автоматически'
      throw e
    }
  }

  // Open the schedule of the group (or teacher) a report row is about, so its
  // card is visible in the pool.
  function focusLoad(row) {
    if (row.group_ids?.length) {
      viewMode.value = 'group'
      selectedId.value = row.group_ids[0]
    } else {
      viewMode.value = 'teacher'
      selectedId.value = row.teacher_id
    }
  }

  function startDrag(loadId, itemId = null) {
    cellCache.value = new Map()
    dragging.value = { loadId, itemId }
  }
  function endDrag() {
    dragging.value = null
    cellCache.value = new Map()
  }

  // --- reference entity CRUD ------------------------------------------

  const ENTITY_PATHS = {
    departments: '/api/departments',
    buildings: '/api/buildings',
    rooms: '/api/rooms',
    teachers: '/api/teachers',
    groups: '/api/groups',
    subjects: '/api/subjects',
    timeSlots: '/api/time-slots',
    loads: '/api/loads',
  }

  function extractErrorMessage(e, fallback) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail?.errors) return detail.errors.join('; ')
    return fallback
  }

  async function createEntity(entity, data) {
    try {
      await axios.post(ENTITY_PATHS[entity], data)
      error.value = ''
      info.value = ''
      await load()
    } catch (e) {
      error.value = extractErrorMessage(e, 'Не удалось создать запись')
      throw e
    }
  }

  async function updateEntity(entity, id, data) {
    try {
      await axios.put(`${ENTITY_PATHS[entity]}/${id}`, data)
      error.value = ''
      info.value = ''
      await load()
    } catch (e) {
      error.value = extractErrorMessage(e, 'Не удалось сохранить изменения')
      throw e
    }
  }

  async function deleteEntity(entity, id) {
    try {
      await axios.delete(`${ENTITY_PATHS[entity]}/${id}`)
      error.value = ''
      info.value = ''
      await load()
    } catch (e) {
      error.value = extractErrorMessage(e, 'Не удалось удалить запись')
      throw e
    }
  }

  return {
    roomTypes,
    departments,
    buildings,
    rooms,
    teachers,
    groups,
    subjects,
    timeSlots,
    loads,
    items,
    viewMode,
    selectedId,
    dragging,
    cellCache,
    loading,
    error,
    info,
    autoReport,
    focusLoad,
    entityOptions,
    poolLoads,
    loadById,
    subjectById,
    teacherById,
    groupById,
    roomById,
    roomLabel,
    departmentById,
    roomTypeLabel,
    timeSlotById,
    groupCodes,
    remaining,
    itemAt,
    load,
    setViewMode,
    validateCell,
    roomOptions,
    assign,
    unassign,
    startDrag,
    endDrag,
    autoGenerate,
    createEntity,
    updateEntity,
    deleteEntity,
  }
})
