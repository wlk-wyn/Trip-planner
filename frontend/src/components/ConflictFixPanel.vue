<template>
  <section id="fix-comparison" class="fix-panel" tabindex="-1" aria-label="调整方案比较" :aria-busy="loading">
    <header>
      <div><small>调整预览 · 尚未保存</small><h2>让第 {{ dayIndex + 1 }} 天更从容</h2></div>
      <button type="button" class="close" aria-label="关闭调整方案" @click="emit('close')">×</button>
    </header>
    <p v-if="loading" role="status" class="muted">正在逐个校验候选路线，完成后即可比较…</p>
    <p v-else-if="stale" role="alert" class="stale">行程已更新，这批方案基于旧版本。请关闭后重新查看调整方案。</p>
    <p v-else-if="!proposals.length" class="muted">暂时没有可用的自动调整方案。可以手动调整当天时间、地点，或询问助手。</p>
    <div v-if="!loading && original" class="proposal-list">
      <article v-for="(row, index) in comparisons" :key="index" class="proposal">
        <div class="proposal-head"><h3>{{ row.proposal.title }}</h3><span class="badge" :class="{ feasible: row.proposal.feasible }">{{ row.proposal.feasible ? '时间可安排' : row.proposal.status === 'unverified' ? '交通待核实' : '仍有冲突' }}</span></div>
        <ul v-if="row.diff.identityComplete && row.diff.changes.length" class="changes">
          <li v-for="(change, n) in row.diff.changes" :key="n"><span class="change-type">{{ change.kind === 'removed' ? '移除' : change.kind === 'added' ? '新增' : '停留' }}</span><strong>{{ change.name }}</strong><span>{{ change.kind === 'duration' ? `${change.before} → ${change.after} 分钟` : `${change.before ?? change.after} 分钟` }}</span></li>
        </ul>
        <p v-else class="muted">{{ row.proposal.detail }}</p>
        <p v-if="!row.diff.identityComplete" class="muted">部分地点缺少唯一标识，请展开完整路线核对变化。</p>
        <p v-if="row.diff.reordered" class="muted">保留地点的游览顺序也有调整。</p>
        <div class="timing" role="group" aria-label="调整前后时间对比">
          <div class="metric-labels"><span>时间参考</span><span>交通耗时</span><span>预计结束</span><span>当天安排</span></div>
          <div><strong>当前行程</strong><span>{{ currentTiming.travel }}</span><span>{{ currentTiming.finish }}</span><span>{{ currentTiming.overrun }}</span></div>
          <div class="after"><strong>采用后</strong><span>{{ row.timing.travel }}</span><span>{{ row.timing.finish }}</span><span>{{ row.timing.overrun }}</span></div>
        </div>
        <p class="muted evidence">当前：{{ trafficEvidence(original) }}<br>候选：{{ trafficEvidence(row.proposal.day) }}</p>
        <p v-for="reason in unverifiedRouteReasons(row.proposal.day)" :key="reason" class="muted">交通未核实：{{ reason }}</p>
        <details><summary>查看完整路线对比</summary><div class="route-comparison"><div v-for="(day, side) in [original, row.proposal.day]" :key="side"><strong>{{ side === 0 ? '当前行程' : '采用后' }}</strong><ol><li v-for="(stop, n) in routeStops(day)" :key="n"><span>{{ stop.name }}</span><small>{{ stop.time }}</small></li></ol></div></div></details>
        <p v-if="row.proposal.conflicts.length" class="muted">{{ row.proposal.conflicts.join('；') }}</p>
        <footer><span>仅影响当天 · 采用后可撤销</span><button type="button" class="apply" :disabled="disabled || stale" @click="emit('apply', row.proposal)">采用此方案</button></footer>
      </article>
    </div>
    <p class="footnote">交通为查询时结果；停留为建议时长，开放时间与预约名额未全面核实。缺失耗时不会按 0 分钟计算，采用时会再次校验。</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DayPlan, FixProposal } from '@/types'
import { compareVisits, dayTiming, unverifiedRouteReasons } from '@/utils/fixComparison'
const props = defineProps<{ dayIndex: number; original: DayPlan | null; proposals: FixProposal[]; loading: boolean; disabled: boolean; stale: boolean }>()
const emit = defineEmits<{ apply: [proposal: FixProposal]; close: [] }>()
const currentTiming = computed(() => props.original ? dayTiming(props.original) : { travel: '尚未校验', finish: '尚未校验', overrun: '尚未校验' })
const comparisons = computed(() => props.original ? props.proposals.map(proposal => ({ proposal, diff: compareVisits(props.original!, proposal.day), timing: dayTiming(proposal.day) })) : [])
function routeStops(day: DayPlan) {
  if (day.schedule?.items.length) return day.schedule.items.map(item => ({ name: item.name, time: `${item.start_time || '待核实'} — ${item.end_time || '待核实'}` }))
  return day.attractions.map(item => ({ name: item.name, time: `停留 ${item.visit_duration} 分钟` }))
}
function trafficEvidence(day: DayPlan) {
  const segments = day.schedule?.segments || []
  if (!segments.length) return '无交通路段或尚未校验'
  const stamps = segments.map(segment => segment.checked_at ? Date.parse(segment.checked_at) : NaN)
  const valid = stamps.filter(Number.isFinite)
  if (!valid.length) return '缺少交通查询时间'
  const oldest = Math.min(...valid)
  const time = new Date(oldest).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  return `交通最早查询于 ${time}${Date.now() - oldest > 86400000 ? '，已超过 24 小时' : ''}${valid.length < stamps.length ? '，部分路段缺少查询时间' : ''}`
}
</script>

<style scoped>
.fix-panel { margin: 0 0 22px; padding: 22px; border: 1px solid var(--color-border); border-radius: 18px; background: var(--color-surface); color: var(--color-text); font-size: 13px; }
header, .proposal-head, footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
header { align-items: flex-start; margin-bottom: 18px; }
header small, .muted, .footnote, footer>span { color: var(--color-muted); }
h2 { margin: 6px 0 0; font-size: 20px; letter-spacing: -.4px; }
h3 { margin: 0; font-size: 15px; }
button { font: inherit; cursor: pointer; }
.close { background: transparent; border: 0; color: var(--color-muted); font-size: 24px; }
.proposal-list { display: grid; gap: 14px; }
.proposal { padding: 18px; border: 1px solid var(--color-border); border-radius: 14px; min-width: 0; }
.badge { flex-shrink: 0; font-size: 11px; border-radius: 20px; padding: 4px 9px; color: var(--color-warning); background: var(--color-warning-bg); }
.badge.feasible { color: var(--color-text); background: var(--color-subtle); }
.changes { list-style: none; padding: 0; margin: 16px 0; display: grid; gap: 10px; }
.changes li { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: baseline; }
.changes strong { font-weight: 500; }
.changes li>span:last-child { color: var(--color-muted); }
.change-type { color: var(--color-muted); font-size: 11px; }
.timing { display: grid; grid-template-columns: .8fr 1fr 1fr; border: 1px solid var(--color-border); border-radius: 10px; overflow: hidden; margin: 16px 0; }
.timing>div { display: grid; grid-template-rows: repeat(4, minmax(35px, auto)); align-items: center; padding: 8px 12px; gap: 4px; }
.timing>div+div { border-left: 1px solid var(--color-border); }
.metric-labels { color: var(--color-muted); font-size: 12px; }
.timing strong { font-size: 12px; }
.after { background: var(--color-subtle); }
details { margin-top: 12px; }
summary { cursor: pointer; color: var(--color-muted); width: fit-content; }
.route-comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 18px; }
.route-comparison strong { font-size: 12px; }
ol { margin: 12px 0; padding-left: 20px; }
ol li { padding: 0 0 12px 3px; }
ol small { display: block; color: var(--color-muted); margin-top: 4px; font-size: 11px; }
footer { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--color-border); }
footer>span { font-size: 11px; }
.apply { border: 0; border-radius: 20px; padding: 9px 16px; color: var(--color-on-primary); background: var(--color-primary); white-space: nowrap; }
button:disabled { opacity: .45; cursor: not-allowed; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 4px; }
.muted, .footnote, .stale { line-height: 1.7; }
.footnote { font-size: 11px; margin: 16px 0 0; }
.evidence { font-size: 11px; margin: -5px 0 14px; }
.stale { color: var(--color-warning); background: var(--color-warning-bg); padding: 10px 12px; border-radius: 8px; }
@media(max-width: 600px) { .fix-panel { padding: 16px 12px; } .proposal { padding: 14px 10px; } h2 { font-size: 18px; } .proposal-head { align-items: flex-start; } .timing { font-size: 12px; } .timing>div { padding: 8px 6px; grid-template-rows: repeat(4, minmax(42px, auto)); } .route-comparison { grid-template-columns: 1fr; gap: 8px; } }
</style>
