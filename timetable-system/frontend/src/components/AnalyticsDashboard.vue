<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '../api'

const days = ref(30)
const report = ref(null)
const loading = ref(false)
const error = ref('')
let request = 0
const labels = { phone: 'Телефон', tablet: 'Планшет', desktop: 'Компьютер', unknown: 'Неизвестно', student: 'Студент', teacher: 'Преподаватель' }
const metrics = computed(() => {
  const t = report.value?.totals || {}
  return [
    ['visitors', 'Посетители', t.visitors, 'Уникальные браузеры за период'],
    ['visits', 'Визиты', t.visits, 'Новый визит после 30 минут без действий'],
    ['schedule_views', 'Просмотры расписания', t.schedule_views, 'Выбор группы, преподавателя или дня'],
    ['opens', 'Открытия приложения', t.opens, 'Включая перезагрузки страницы'],
    ['returning', 'Вернулись', t.returning, 'Браузеры с двумя и более визитами'],
    ['active', 'Активны за 5 минут', t.active, 'Посетители, совершившие действие'],
    ['errors', 'Ошибки загрузки', t.errors, 'Ошибки, о которых сообщил браузер'],
  ]
})
const peak = computed(() => Math.max(1, ...(report.value?.daily || []).map(d => d.visits)))
const sections = [
  ['groups', 'Популярные группы', 'Просмотры'], ['teachers', 'Популярные преподаватели', 'Просмотры'],
  ['modes', 'Режим просмотра', 'Визиты'], ['devices', 'Устройства', 'Визиты'],
  ['browsers', 'Браузеры', 'Визиты'], ['referrers', 'Источники переходов', 'Визиты'],
]
const number = value => new Intl.NumberFormat('ru-RU').format(value || 0)
const date = value => new Date(value).toLocaleString('ru-RU', { timeZone: 'Asia/Almaty' })
async function load() {
  const ticket = ++request
  loading.value = true
  error.value = ''
  report.value = null
  try {
    const { data } = await api.get('/api/admin/analytics', { params: { days: days.value } })
    if (ticket === request) report.value = data
  } catch (e) {
    if (ticket === request) error.value = e.response?.data?.detail || 'Не удалось загрузить статистику.'
  } finally { if (ticket === request) loading.value = false }
}
function download() {
  if (!report.value) return
  const rows = [['Статистика okak.asia', `${report.value.days} дней`, 'Asia/Almaty'], ['Показатель', 'Значение']]
  for (const [, title, value] of metrics.value) rows.push([title, value])
  rows.push([], ['Дата', 'Посетители', 'Визиты'])
  for (const d of report.value.daily) rows.push([d.day, d.visitors, d.visits])
  for (const [key, title, unit] of sections) {
    rows.push([], [title, unit])
    for (const row of report.value[key]) rows.push([labels[row.name] || row.name, row.count])
  }
  rows.push([], ['Час (Алматы)', 'Просмотры'])
  for (const row of report.value.hours) rows.push([row.hour, row.count])
  const cell = value => {
    let text = String(value ?? '')
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text
    return `"${text.replaceAll('"', '""')}"`
  }
  const url = URL.createObjectURL(new Blob(['\uFEFF' + rows.map(row => row.map(cell).join(';')).join('\r\n')], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `okak-statistics-${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
onMounted(load)
watch(days, load)
</script>

<template>
  <main class="analytics">
    <header class="analytics-heading">
      <div><p class="eyebrow">OKAK · АДМИНИСТРАТОР</p><h1>Использование расписания</h1>
        <p class="muted">Посещения общего сайта студентами и преподавателями</p></div>
      <div class="analytics-controls">
        <label>Период <select v-model="days"><option :value="7">7 дней</option><option :value="30">30 дней</option><option :value="90">90 дней</option><option :value="180">180 дней</option></select></label>
        <button @click="load" :disabled="loading">Обновить</button>
        <button @click="download" :disabled="!report">Скачать CSV</button>
      </div>
    </header>
    <p v-if="error" role="alert" class="analytics-error">{{ error }}</p>
    <p v-if="loading" role="status" class="muted">Загрузка статистики…</p>
    <template v-if="report">
      <p class="muted">Обновлено {{ date(report.generated_at) }} · Время Алматы</p>
      <div v-if="!report.first_event_at" class="analytics-empty">Сбор статистики включён. Данные появятся после первых посещений приложения. Посещения до включения сбора не учитываются.</div>
      <section class="metric-grid" aria-label="Основные показатели">
        <article v-for="[key, title, value, note] in metrics" :key="key" class="metric">
          <p>{{ title }}</p><strong>{{ number(value) }}</strong><small>{{ note }}</small>
        </article>
      </section>
      <section class="analytics-card">
        <h2>Визиты по дням</h2>
        <div class="daily-chart" aria-hidden="true">
          <div v-for="day in report.daily" :key="day.day" class="chart-column" :title="`${day.day}: ${day.visits} визитов, ${day.visitors} посетителей`">
            <div class="chart-bar" :style="{ height: `${day.visits / peak * 100}%` }"></div>
          </div>
        </div>
        <div class="chart-dates"><span>{{ report.daily[0]?.day }}</span><span>{{ report.daily.at(-1)?.day }}</span></div>
        <details><summary>Показать данные по дням</summary><div class="table-scroll"><table><thead><tr><th>Дата</th><th>Посетители</th><th>Визиты</th></tr></thead>
          <tbody><tr v-for="day in report.daily" :key="day.day"><td>{{ day.day }}</td><td>{{ number(day.visitors) }}</td><td>{{ number(day.visits) }}</td></tr></tbody></table></div></details>
      </section>
      <div class="analytics-grid">
        <section v-for="[key, title, unit] in sections" :key="key" class="analytics-card">
          <h2>{{ title }}</h2>
          <p v-if="!report[key].length" class="muted">Пока нет данных за этот период.</p>
          <table v-else><thead><tr><th>Название</th><th>{{ unit }}</th></tr></thead>
            <tbody><tr v-for="row in report[key]" :key="`${row.id || ''}:${row.name}`"><td>{{ labels[row.name] || row.name }}</td><td>{{ number(row.count) }}</td></tr></tbody>
          </table>
        </section>
        <section class="analytics-card"><h2>Просмотры по времени суток</h2>
          <p v-if="!report.hours.length" class="muted">Пока нет просмотров.</p>
          <table v-else><thead><tr><th>Час · Алматы</th><th>Просмотры</th></tr></thead><tbody>
            <tr v-for="row in report.hours" :key="row.hour"><td>{{ String(row.hour).padStart(2, '0') }}:00</td><td>{{ number(row.count) }}</td></tr>
          </tbody></table>
        </section>
      </div>
      <aside class="analytics-notes">
        <h2>Как читать эти данные</h2>
        <p>Посетитель — браузер со случайным идентификатором, а не конкретный человек. Другой браузер или очистка данных создают нового посетителя. Автообновление расписания не добавляет просмотры.</p>
        <p>Один визит может включать оба режима. В таблицах показаны до 15 популярных расписаний и до 20 источников. Если название группы изменилось, просмотры прежнего названия показаны отдельно.</p>
        <p>События хранятся {{ report.retention_days }} дней. Статистика приблизительная: блокировщики, Do Not Track, ошибки сети и автоматические запросы могут влиять на числа. IP-адреса, имена посетителей и полные адреса переходов не сохраняются в статистике.</p>
        <p v-if="report.first_event_at">Первое сохранённое событие: {{ date(report.first_event_at) }}.</p>
      </aside>
    </template>
  </main>
</template>

<style scoped>
.analytics { flex: 1; overflow: auto; background: #f4f6fa; padding: 32px clamp(16px, 4vw, 64px); color: #17233c; }
.analytics-heading { display: flex; justify-content: space-between; gap: 24px; flex-wrap: wrap; margin-bottom: 24px; }
h1 { font-size: clamp(24px, 3vw, 32px); font-weight: 700; margin: 5px 0; letter-spacing: -.6px; }
h2 { font-size: 17px; font-weight: 650; margin-bottom: 16px; }
.eyebrow { font-size: 11px; letter-spacing: 2px; font-weight: 700; color: #4367b1; }
.muted, small { color: #64748b; font-size: 13px; }
.analytics-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 13px; }
button, select { border: 1px solid #cdd6e4; padding: 9px 12px; border-radius: 8px; background: white; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
button:focus-visible, select:focus-visible, summary:focus-visible { outline: 2px solid #2563eb; outline-offset: 3px; }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 14px; margin: 20px 0; }
.metric, .analytics-card { padding: 22px; border: 1px solid #e0e6ef; border-radius: 14px; background: white; }
.metric p { font-size: 13px; }.metric strong { display: block; font-size: 36px; margin: 10px 0; font-weight: 700; }.metric small { display: block; line-height: 1.5; }
.analytics-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 18px; }
.daily-chart { display: flex; align-items: flex-end; height: 170px; gap: 3px; border-bottom: 1px solid #dde4ef; margin-top: 24px; }
.chart-column { height: 100%; flex: 1; display: flex; align-items: flex-end; min-width: 1px; }.chart-bar { width: 100%; background: #4978d4; border-radius: 3px 3px 0 0; }
.chart-dates { display: flex; justify-content: space-between; color: #64748b; font-size: 11px; margin: 8px 0 18px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }th { color: #64748b; font-weight: 500; }td, th { text-align: left; padding: 10px 5px; border-bottom: 1px solid #edf0f5; overflow-wrap: anywhere; }td:last-child, th:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.table-scroll { max-height: 360px; overflow: auto; }summary { cursor: pointer; font-size: 13px; }
.analytics-notes { font-size: 12px; line-height: 1.7; color: #64748b; margin: 28px 0; }.analytics-notes p { margin: 8px 0; }
.analytics-error { background: #fff0ef; color: #a33224; padding: 16px; border-radius: 8px; }.analytics-empty { padding: 16px; background: #e8f0ff; border-radius: 8px; margin-top: 16px; }
@media (max-width: 700px) { .analytics-grid { grid-template-columns: 1fr; } }
</style>
