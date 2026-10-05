<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { matchesSearch } from '../utils/search'

// Multi-select with search: chosen items are chips, type to find more,
// click or Enter toggles, Backspace in an empty field removes the last chip.
const props = defineProps({
  options: { type: Array, required: true }, // [{ id, label, hint? }]
  modelValue: { type: Array, default: () => [] }, // selected ids
  placeholder: { type: String, default: 'Поиск…' },
  widthClass: { type: String, default: 'w-96' },
})
const emit = defineEmits(['update:modelValue'])

const query = ref('')
const open = ref(false)
const highlighted = ref(0)
const inputEl = ref(null)
const listEl = ref(null)

const selectedSet = computed(() => new Set(props.modelValue))
const optionById = computed(() => new Map(props.options.map((o) => [o.id, o])))
const selectedOptions = computed(() =>
  props.modelValue.map((id) => optionById.value.get(id)).filter(Boolean),
)

const filtered = computed(() =>
  props.options.filter((o) => matchesSearch(query.value, o.label, o.hint)),
)

watch(filtered, () => {
  highlighted.value = 0
})

function toggle(option) {
  if (!option) return
  const next = selectedSet.value.has(option.id)
    ? props.modelValue.filter((id) => id !== option.id)
    : [...props.modelValue, option.id]
  emit('update:modelValue', next)
}

function remove(id) {
  emit('update:modelValue', props.modelValue.filter((x) => x !== id))
}

function scrollToHighlighted() {
  nextTick(() => listEl.value?.children[highlighted.value]?.scrollIntoView({ block: 'nearest' }))
}

function onKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    open.value = true
    const step = event.key === 'ArrowDown' ? 1 : -1
    highlighted.value = Math.min(filtered.value.length - 1, Math.max(0, highlighted.value + step))
    scrollToHighlighted()
  } else if (event.key === 'Enter') {
    event.preventDefault() // don't submit the surrounding form
    if (open.value) toggle(filtered.value[highlighted.value])
  } else if (event.key === 'Escape') {
    open.value = false
    query.value = ''
  } else if (event.key === 'Backspace' && !query.value && props.modelValue.length) {
    remove(props.modelValue[props.modelValue.length - 1])
  }
}

function onBlur() {
  open.value = false
  query.value = ''
}
</script>

<template>
  <div class="relative" :class="widthClass">
    <div
      class="flex flex-wrap items-center gap-1 min-h-[2rem] w-full border border-gray-300 rounded-md bg-white px-1.5 py-1 cursor-text focus-within:ring-2 focus-within:ring-gray-300"
      @mousedown.self.prevent="inputEl?.focus()"
    >
      <span
        v-for="option in selectedOptions"
        :key="option.id"
        class="inline-flex items-center gap-1 rounded bg-gray-100 border border-gray-200 px-1.5 text-xs text-gray-800"
      >
        {{ option.label }}
        <button
          type="button"
          class="text-gray-400 hover:text-red-600 leading-none"
          :title="`Убрать ${option.label}`"
          @mousedown.prevent="remove(option.id)"
        >
          ×
        </button>
      </span>
      <input
        ref="inputEl"
        v-model="query"
        type="text"
        :placeholder="selectedOptions.length ? '' : placeholder"
        class="flex-1 min-w-[6rem] text-sm px-1 py-0.5 outline-none bg-transparent"
        @focus="open = true"
        @input="open = true"
        @blur="onBlur"
        @keydown="onKeydown"
      />
    </div>

    <div
      v-if="open"
      class="absolute z-40 mt-1 w-full rounded-md border border-gray-200 bg-white shadow-lg"
    >
      <ul ref="listEl" class="max-h-72 overflow-y-auto py-1">
        <li
          v-for="(option, index) in filtered"
          :key="option.id"
          class="px-2 py-1 text-sm cursor-pointer flex items-baseline gap-2"
          :class="index === highlighted ? 'bg-gray-100' : ''"
          @mousedown.prevent="toggle(option)"
          @mousemove="highlighted = index"
        >
          <span class="w-4 shrink-0 text-gray-700">{{ selectedSet.has(option.id) ? '✓' : '' }}</span>
          <span class="truncate" :class="selectedSet.has(option.id) ? 'font-medium text-gray-900' : 'text-gray-700'">
            {{ option.label }}
          </span>
          <span v-if="option.hint" class="ml-auto text-xs text-gray-400 shrink-0 truncate max-w-[50%]">
            {{ option.hint }}
          </span>
        </li>
      </ul>
      <p v-if="filtered.length === 0" class="px-2 py-2 text-sm text-gray-400">Ничего не найдено</p>
      <p v-else class="px-2 py-1 text-[11px] text-gray-400 border-t border-gray-100">
        {{ filtered.length }} из {{ options.length }} · выбрано {{ modelValue.length }} · Enter — отметить, Esc — закрыть
      </p>
    </div>
  </div>
</template>
