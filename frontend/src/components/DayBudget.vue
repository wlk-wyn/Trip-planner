<template>
  <section class="day-budget" aria-label="当天时间预算">
    <header><strong>{{ availabilityIssue ? '开放时间存在冲突，请先调整参观安排' : report.headline }}</strong><span v-if="report.status !== 'unchecked'">可用 {{ duration(report.available_minutes || 0) }}</span></header>
    <p v-if="availabilityIssue" class="hours-coverage">{{ availabilityIssue }}</p>
    <p v-if="report.opening_hours_total" class="hours-coverage">营业时段已解析 {{ report.opening_hours_checked || 0 }}/{{ report.opening_hours_total }} 处<span v-if="report.opening_hours_unknown"> · {{ report.opening_hours_unknown }} 处待确认</span>；登记信息不代表官方开放与预约保证。</p>
    <template v-if="report.status !== 'unchecked'">
      <div class="budget-track" aria-hidden="true"><span v-for="part in parts" :key="part.label" :style="{ width: `${100 * part.minutes / scale}%`, background: part.color }"></span></div>
      <details><summary>时间分配与数据依据</summary>
      <ul class="budget-parts"><li v-for="part in parts" :key="part.label"><i :style="{ background: part.color }"></i>{{ part.label }} {{ duration(part.minutes) }}</li></ul>
      <p v-if="report.missing_routes">还有 {{ report.missing_routes }} 段交通未核实，当前仅为已知耗时下限，不能视为完整预算。</p>
      <p v-if="report.longest_leg && report.longest_leg.duration_minutes >= 60">最长路段：{{ report.longest_leg.from_name }} → {{ report.longest_leg.to_name }}，{{ duration(report.longest_leg.duration_minutes) }}。可优先考虑调整这一段附近的安排。</p>
      <footer>
        <small>{{ freshness }} · 停留为建议时长，开放时间和预约名额未全面核实。</small>
        <button v-if="availabilityIssue || report.status === 'conflict'" type="button" :disabled="busy" @click="emit('fix')">查看调整方案 →</button>
        <button v-else-if="report.missing_routes || report.stale_routes || report.unknown_age_routes" type="button" :disabled="busy" @click="emit('recheck')">更新交通数据 →</button>
      </footer>
      </details>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DayDiagnosis } from '@/types'
const props = defineProps<{ report: DayDiagnosis; busy: boolean; availabilityIssue?: string }>()
const emit = defineEmits<{ fix: []; recheck: [] }>()
const duration = (minutes: number) => minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时${minutes % 60 ? ` ${minutes % 60} 分钟` : ''}`
const parts = computed(() => [
  { label: '游览', minutes: props.report.visit_minutes || 0, color: 'var(--color-text)' },
  { label: '用餐', minutes: props.report.meal_minutes || 0, color: '#8c98a4' },
  { label: props.report.missing_routes ? '已知交通' : '交通', minutes: props.report.travel_minutes || 0, color: '#b3bec8' },
  { label: '缓冲', minutes: props.report.buffer_minutes || 0, color: '#d8dce0' },
  { label: '等待', minutes: props.report.waiting_minutes || 0, color: '#b4a9cb' },
].filter(part => part.minutes > 0))
const scale = computed(() => Math.max(1, props.report.available_minutes || 0, parts.value.reduce((sum, part) => sum + part.minutes, 0)))
const freshness = computed(() => {
  const report = props.report
  if (report.stale_routes) return `${report.stale_routes} 段交通查询已超过 24 小时，建议更新`
  if (report.unknown_age_routes) return `${report.unknown_age_routes} 段交通缺少查询时间，建议更新`
  if (!report.oldest_checked_at) return '暂无交通查询时间'
  const stamp = new Date(report.oldest_checked_at)
  return `交通查询于 ${stamp.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}（最早一段）`
})
</script>

<style scoped>
.day-budget { margin: 14px 0 18px; padding: 14px 16px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-subtle); font-size: 12px; }
header { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 6px 12px; }
header strong { font-size: 13px; font-weight: 600; }
header span, p, small { color: var(--color-muted); }
.budget-track { display: flex; height: 6px; gap: 2px; border-radius: 4px; overflow: hidden; background: var(--color-border); margin: 12px 0 10px; }
.budget-track span { flex-shrink: 1; }
.budget-parts { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 0; margin: 0; list-style: none; }
.budget-parts li { display: flex; align-items: center; gap: 5px; }
i { width: 6px; height: 6px; border-radius: 50%; flex: none; }
p { line-height: 1.7; margin: 10px 0 0; }
footer { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px; margin-top: 10px; }
small { font-size: 11px; line-height: 1.7; }
button { padding: 0; background: none; border: 0; color: var(--color-text); font: inherit; cursor: pointer; }
button:disabled { opacity: .5; cursor: wait; }
details>summary { cursor: pointer; color: var(--color-muted); font-size: 11px; }
.budget-parts { margin-top: 12px; }
.day-budget { padding: 12px 14px; background: transparent; }
.hours-coverage { font-size: 11px; margin-top: 8px; }
</style>
