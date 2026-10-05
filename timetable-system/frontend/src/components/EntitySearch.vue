<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { matchesSearch } from '../utils/search'

// Searchable picker: type part of a name/code, pick with mouse or ↑ ↓ Enter.
// Search ignores case, spaces, dashes and dots ("ис242" finds "ИС-242"),
// and also looks in the grey hint (course, department, room type).
const props = defineProps({
  options: { type: Array, required: true }, // [{ id, label, hint? }]
  modelValue: { type: [Number, String], default: null },
  placeholder: { type: String, default: 'Поиск…' },
  widthClass: { type: String, default: 'w-72' },
})
const emit = defineEmits(['update:modelValue'])

const query = ref('')
const open = ref(false)
const highlighted = ref(0)
const inputEl = ref(null)
const listEl = ref(null)

const selected = computed(() => props.options.find((o) => o.id === props.modelValue) ?? null)

const filtered = computed(() =>
  props.options.filter((o) => matchesSearch(query.value, o.label, o.hint)),
)

watch(filtered, () => {
  highlighted.value = 0
})

function openList() {
  open.value = true
  query.value = ''
  const index = props.options.findIndex((o) => o.id === props.modelValue)
  highlighted.value = Math.max(0, index)
  scrollToHighlighted()
}

function close() {
  open.value = false
  query.value = ''
}

function choose(option) {
  if (!option) return
  emit('update:modelValue', option.id)
  close()
  inputEl.value?.blur()
}

function scrollToHighlighted() {
  nextTick(() => listEl.value?.children[highlighted.value]?.scrollIntoView({ block: 'nearest' }))
}

function onInput(event) {
  query.value = event.target.value
  open.value = true
}

function onKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!open.value) return openList()
    const step = event.key === 'ArrowDown' ? 1 : -1
    const last = filtered.value.length - 1
    highlighted.value = Math.min(last, Math.max(0, highlighted.value + step))
    scrollToHighlighted()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    choose(filtered.value[highlighted.value])
  } else if (event.key === 'Escape') {
    close()
    inputEl.value?.blur()
  }
}
</script>

<template>
  <div class="relative" :class="widthClass">
    <input
      ref="inputEl"
      type="text"
      :value="open ? query : selected?.label ?? ''"
      :placeholder="open ? selected?.label || placeholder : placeholder"
      class="w-full border border-gray-300 rounded-md text-sm px-2 py-1 pr-7 focus:outline-none focus:ring-2 focus:ring-gray-300"
      @focus="openList"
      @blur="close"
      @input="onInput"
      @keydown="onKeydown"
    />
    <span class="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 text-xs">▾</span>

    <div
      v-if="open"
      class="absolute z-40 mt-1 w-full rounded-md border border-gray-200 bg-white shadow-lg"
    >
      <ul ref="listEl" class="max-h-80 overflow-y-auto py-1">
        <li
          v-for="(option, index) in filtered"
          :key="option.id"
          class="px-2 py-1 text-sm cursor-pointer flex items-baseline justify-between gap-2"
          :class="[
            index === highlighted ? 'bg-gray-100' : '',
            option.id === modelValue ? 'font-medium text-gray-900' : 'text-gray-700',
          ]"
          @mousedown.prevent="choose(option)"
          @mousemove="highlighted = index"
        >
          <span class="truncate">{{ option.label }}</span>
          <span v-if="option.hint" class="text-xs text-gray-400 shrink-0 truncate max-w-[50%]">
            {{ option.hint }}
          </span>
        </li>
      </ul>
      <p v-if="filtered.length === 0" class="px-2 py-2 text-sm text-gray-400">Ничего не найдено</p>
      <p v-else class="px-2 py-1 text-[11px] text-gray-400 border-t border-gray-100">
        {{ filtered.length }} из {{ options.length }}
      </p>
    </div>
  </div>
</template>
