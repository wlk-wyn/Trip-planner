// Synthetic component fixture; no API calls, storage changes or saved trip edits.
import { createApp, computed, h, ref } from 'vue'
import DayTimeline from '../src/components/DayTimeline.vue'
import '../src/styles/theme.css'
import type { DayPlan } from '../src/types'
import { buildHandbookHtml } from '../src/utils/handbook'

const original: DayPlan = {
  date: '2026-10-20', day_index: 0, description: '', transportation: '步行', accommodation: '',
  buffer_minutes: 15, start_time: '14:00', end_time: '20:00', attractions: [], meals: [],
  schedule: { status: 'feasible', window_start: '14:00', window_end: '20:00', minimum_minutes: 240, available_minutes: 360,
    conflicts: [], warnings: [], items: [
      { id: 'museum', name: '城市展馆', kind: 'attraction', start_time: '14:00', end_time: '15:00', duration_minutes: 60, fixed: false },
      { id: 'dinner', name: '附近餐厅', kind: 'meal', start_time: '17:00', end_time: '18:00', duration_minutes: 60, fixed: false }],
    segments: [{ from_id: 'museum', to_id: 'dinner', from_name: '城市展馆', to_name: '附近餐厅', mode: 'walking', status: 'verified', duration_minutes: 20 }] }
}
createApp({ setup() {
  const missing = ref(false)
  const day = computed(() => {
    const copy: DayPlan = structuredClone(original)
    if (missing.value) {
      copy.schedule!.segments[0].status = 'unverified'
      copy.schedule!.segments[0].duration_minutes = null
      copy.schedule!.items[1].start_time = null
      copy.schedule!.items[1].end_time = null
      copy.schedule!.status = 'unverified'
    }
    return copy
  })
  return () => h('main', [h('h1', '时间线空档 · 合成案例'),
    h('p', '已扣除20分钟交通与15分钟缓冲；未知交通时不显示等待推断。'),
    h('button', { onClick: () => { missing.value = !missing.value } }, missing.value ? '恢复已知交通' : '模拟交通缺失'),
    h('button', { onClick: () => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark' } }, '切换明暗'),
    h(DayTimeline, { day: day.value, editing: false }),
    h('h2', '离线手册预览'),
    h('iframe', { title: '离线手册预览', style: 'width:100%;height:600px;border:1px solid var(--color-border)',
      srcdoc: buildHandbookHtml({city:'合成城市',start_date:'2026-10-20',end_date:'2026-10-20',days:[day.value],weather_info:[],overall_suggestions:''}) })])
} }).mount('#app')
