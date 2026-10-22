// Isolated, synthetic UI fixture. No API calls and no saved trip changes.
import { createApp, h, ref } from 'vue'
import ConflictFixPanel from '../src/components/ConflictFixPanel.vue'
import '../src/styles/theme.css'
import type { DayPlan, FixProposal } from '../src/types'

const place = (id: string, name: string, visit_duration: number) => ({ item_id: id, name, visit_duration, address: '', location: { longitude: 116.4, latitude: 39.9 }, description: '' })
const original: DayPlan = {
  date: '2026-10-20', day_index: 0, description: '', transportation: '步行', accommodation: '', meals: [],
  attractions: [place('a', '城市博物馆', 150), place('b', '河边公园', 90)], visit_order: ['a', 'b'],
  schedule: { status: 'conflict', window_start: '09:00', window_end: '13:00', minimum_minutes: 270, available_minutes: 240, conflicts: ['当天超出 30 分钟'], warnings: [],
    items: [{ id: 'a', name: '城市博物馆', kind: 'attraction', start_time: '09:00', end_time: '11:30', duration_minutes: 150, fixed: false }, { id: 'b', name: '河边公园', kind: 'attraction', start_time: '12:00', end_time: '13:30', duration_minutes: 90, fixed: false }],
    segments: [{ from_name: '城市博物馆', to_name: '河边公园', status: 'verified', duration_minutes: 30, distance_meters: 2000, mode: 'walking', note: '', checked_at: '2026-10-16T01:00:00Z' }] },
}
const candidate = structuredClone(original)
candidate.attractions[0].visit_duration = 100
candidate.schedule!.status = 'feasible'; candidate.schedule!.minimum_minutes = 220; candidate.schedule!.conflicts = []
candidate.schedule!.items[0].end_time = '10:40'; candidate.schedule!.items[1].start_time = '11:10'; candidate.schedule!.items[1].end_time = '12:40'
const proposal: FixProposal = { kind: 'shorten', title: '缩短博物馆停留', detail: '减少 50 分钟停留', feasible: true, status: 'feasible', overrun_minutes: 0, conflicts: [], day: candidate }

createApp({ setup() {
  const stale = ref(false), missing = ref(false), adopted = ref(0), closed = ref(false)
  const toggleTheme = () => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark' }
  return () => {
    const current = structuredClone(original), next = structuredClone(proposal)
    if (missing.value) {
      next.feasible = false; next.status = 'unverified'; next.day.schedule!.status = 'unverified'
      next.day.schedule!.segments[0].status = 'unverified'; next.day.schedule!.segments[0].duration_minutes = null
      next.day.schedule!.items[1].end_time = null
    }
    return h('main', { style: 'max-width:900px;margin:32px auto;padding:16px' }, [
      h('p', '本地合成测试数据，不会保存旅行'),
      h('nav', { style: 'display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px' }, [
        h('button', { onClick: () => stale.value = !stale.value }, '切换旧版本状态'),
        h('button', { onClick: () => missing.value = !missing.value }, '切换缺失交通'),
        h('button', { onClick: toggleTheme }, '切换测试主题'),
      ]),
      closed.value ? h('p', '预览已关闭') : h(ConflictFixPanel, { dayIndex: 0, original: current, proposals: [next], loading: false, disabled: false, stale: stale.value, onApply: () => adopted.value++, onClose: () => closed.value = true }),
      h('p', { role: 'status' }, `采用事件次数：${adopted.value}`),
    ])
  }
} }).mount('#app')
