<template>
  <section class="schedule" :class="day.schedule?.status" aria-label="当天时间线">
    <div class="schedule-heading"><span class="schedule-status">{{ statusLabel }}</span><span>{{ day.schedule?.window_start || day.start_time || '09:00' }} — {{ day.schedule?.window_end || day.end_time || '20:00' }}</span></div>
    <div v-if="editing" class="time-controls">
      <label>出发时间<input type="time" :value="day.start_time || '09:00'" @input="day.start_time = ($event.target as HTMLInputElement).value" /></label>
      <label>结束时间<input type="time" :value="day.end_time || '20:00'" @input="day.end_time = ($event.target as HTMLInputElement).value" /></label>
      <label>每段缓冲（分钟）<a-input-number :value="day.buffer_minutes ?? undefined" :min="0" :max="60" @update:value="value => day.buffer_minutes = value == null ? null : Number(value)" /></label>
      <p>保存时重新校验；下方为上次保存的时间表。</p>
    </div>
    <template v-if="day.schedule">
      <details v-if="day.schedule.conflicts.length" class="conflict-details"><summary>{{ day.schedule.conflicts.length }} 项时间冲突 · 查看原因</summary><ul class="conflicts"><li v-for="text in day.schedule.conflicts" :key="text">{{ text }}</li></ul></details>
      <p v-if="missingSegments.length" class="time-explanation">{{ missingSegments.length }} 段交通耗时未获取，后续到达时间暂时无法计算。可点击页面上方“重新校验时间”重试。</p>
      <ol class="timeline"><li v-for="(item, i) in day.schedule.items" :key="item.id">
        <p v-if="(waitingBefore(day, i) ?? 0) >= 15" class="stop-wait">下一项安排前预留 {{ waitingBefore(day, i) }} 分钟</p>
        <div class="timeline-stop" :class="{ 'is-selected': selectedId === item.id }"><div class="stop-time" :class="{ 'time-unknown': !item.start_time }"><strong>{{ item.start_time || '时间待核实' }}</strong><span v-if="item.end_time && item.duration_minutes > 0">— {{ item.end_time }}</span></div><span class="stop-dot" :class="item.kind" aria-hidden="true"></span><div class="stop-content"><span class="stop-kind">{{ item.kind === 'hotel' && item.duration_minutes === 0 ? (i === 0 ? '从住宿出发' : '返回住宿') : kindLabel(item.kind) }}<span v-if="item.fixed"> · 固定预约</span></span><h4><button type="button" :aria-pressed="selectedId === item.id" :aria-label="`在地图查看${item.name}`" @click="emit('select', item.id)">{{ item.name }}<span aria-hidden="true"> ↗</span></button></h4><span v-if="item.duration_minutes > 0" class="stop-duration">建议{{ item.kind === 'meal' ? '用餐' : '停留' }} {{ item.duration_minutes }} 分钟</span><p v-if="visitGateLabel(day, item.id)" class="gate-reference">{{ visitGateLabel(day, item.id) }} · 门址参考</p></div></div>
        <div v-if="day.schedule.segments[i]" class="transfer">
          <div class="transfer-line">
            <span class="transfer-title">{{ modeLabel(day.schedule.segments[i].mode) }}</span>
            <span>{{ day.schedule.segments[i].status === 'verified' ? `${day.schedule.segments[i].duration_minutes} 分钟` : '耗时未获取' }}</span>
          </div>
          <details v-if="hasRide(day.schedule.segments[i]) || day.schedule.segments[i].note || day.schedule.segments[i].distance_meters != null" class="transfer-detail">
            <summary>路线详情</summary>
            <p v-if="routeGateLabel(day.schedule.segments[i])" class="gate-reference">{{ routeGateLabel(day.schedule.segments[i]) }}</p>
            <a v-if="officialGateUrl(day.schedule.segments[i])" :href="officialGateUrl(day.schedule.segments[i])!" target="_blank" rel="noopener noreferrer">官方参观方向 ↗</a>
            <p>{{ routeTitle(day.schedule.segments[i]) }}<span v-if="day.schedule.segments[i].distance_meters != null"> · {{ distanceText(day.schedule.segments[i].distance_meters!) }}</span><span v-if="day.schedule.segments[i].fare != null"> · ¥{{ day.schedule.segments[i].fare }}</span></p>
            <ol><li v-for="(leg, legIndex) in day.schedule.segments[i].legs" :key="legIndex" :class="leg.kind">{{ legText(leg) }}</li></ol>
            <p v-if="day.schedule.segments[i].taxi_cost != null">打车约 ¥{{ day.schedule.segments[i].taxi_cost }}（高德参考价，以实际计价为准）</p>
            <small v-if="day.schedule.segments[i].note" class="transfer-reason">{{ day.schedule.segments[i].note }}</small>
          </details>
        </div>
      </li></ol>
      <p v-if="!day.schedule.items.length" class="schedule-empty">当天暂无地点安排，可通过旅行助手补充。</p>
      <p v-if="day.schedule.solver_note" class="time-explanation">{{ day.schedule.solver_note }}</p><details class="schedule-basis"><summary>时间依据与注意事项</summary><p>已知耗时下限 {{ day.schedule.minimum_minutes }} 分钟 / 可用 {{ day.schedule.available_minutes }} 分钟</p><p v-for="text in day.schedule.warnings" :key="text">{{ text }}</p><p v-for="(segment, i) in day.schedule.segments.filter(s => s.checked_at)" :key="i">{{ segment.from_name }} → {{ segment.to_name }} · 查询于 {{ new Date(segment.checked_at!).toLocaleString() }}</p></details>
    </template>
    <p v-else class="schedule-empty">以下为建议顺序，尚无已校验的时间表。可点击“重新校验时间”。</p>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import type { DayPlan } from '@/types'
import { distanceText, hasRide, legText, routeTitle, modeLabel } from '@/utils/route'
import { waitingBefore } from '@/utils/scheduleWait'
import { visitGateLabel, routeGateLabel, officialGateUrl } from '@/utils/routingPoints'
const props = defineProps<{ day: DayPlan; editing: boolean; selectedId?: string; availabilityIssue?: string }>()
const emit = defineEmits<{ select: [id: string] }>()
const missingSegments = computed(() => props.day.schedule?.segments.filter(segment => segment.status !== 'verified') || [])
const statusLabel = computed(() => props.availabilityIssue ? '开放时间待调整' : props.day.schedule ? { feasible: '时间可安排', conflict: '存在时间冲突', unverified: '交通耗时缺失' }[props.day.schedule.status] : '时间待校验')
const kindLabel = (kind: string) => ({ attraction: '游览', meal: '用餐', hotel: '住宿' }[kind] || '安排')
</script>
<style scoped>
.gate-reference { margin: 5px 0 0; font-size: 11px; line-height: 1.6; color: var(--color-muted); }
.schedule{margin:22px 0;color:var(--color-text);font-size:13px}.schedule-heading{display:flex;justify-content:space-between;gap:12px;color:var(--color-muted);font-size:12px}.schedule-status{padding:4px 9px;background:var(--color-subtle);border-radius:6px}.conflict .schedule-status,.unverified .schedule-status{color:var(--color-warning);background:var(--color-warning-bg)}.time-controls{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}.time-controls label{display:flex;flex-direction:column;gap:5px}.time-controls input{max-width:140px;border:1px solid var(--color-border);border-radius:6px;padding:6px;background:var(--color-surface);color:var(--color-text)}.time-controls p{width:100%;margin:0;color:var(--color-muted)}.timeline{padding:0;list-style:none;margin:24px 0}.timeline li{position:relative}.timeline-stop{display:grid;grid-template-columns:88px 16px minmax(0,1fr);gap:16px;padding:8px 0 16px;position:relative}.stop-time strong{display:block;font-size:16px;font-weight:500;font-variant-numeric:tabular-nums}.stop-time>span{font-size:10px;color:var(--color-muted);display:block;margin-top:5px}.stop-dot{height:9px;width:9px;margin:6px auto 0;background:var(--color-text);border:2px solid var(--color-surface);box-shadow:0 0 0 1px var(--color-border);border-radius:50%;z-index:1}.timeline li:not(:last-child)::before{content:'';position:absolute;left:111px;top:16px;bottom:-16px;width:1px;background:var(--color-border)}.stop-content h4{font-size:17px;font-weight:500;margin:4px 0 7px;line-height:1.5;overflow-wrap:anywhere}.stop-kind,.stop-duration{font-size:11px;color:var(--color-muted)}.transfer{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px 136px;color:var(--color-muted);font-size:11px;min-height:20px;padding:5px 0}.transfer span+span::before{content:'·';margin-right:10px}.schedule-basis{border-top:1px solid var(--color-border);padding-top:14px;color:var(--color-muted);font-size:11px}.schedule-basis summary{cursor:pointer}.schedule-basis p{line-height:1.8;overflow-wrap:anywhere}.conflicts{background:var(--color-warning-bg);color:var(--color-warning);padding:12px 12px 12px 28px;border-radius:8px;line-height:1.8}.schedule-empty{font-size:12px;color:var(--color-muted);line-height:1.8}@media(max-width:640px){.timeline-stop{grid-template-columns:66px 12px minmax(0,1fr);gap:12px}.timeline li:not(:last-child)::before{left:84px}.transfer{margin-left:102px}.stop-content h4{font-size:15px}.stop-time strong{font-size:14px}}
.time-explanation{color:var(--color-muted);background:var(--color-subtle);padding:12px 14px;border-radius:8px;font-size:12px;line-height:1.8;margin:14px 0}.time-unknown strong{font-size:11px;font-weight:400;color:var(--color-muted);line-height:1.8}.transfer-reason{width:100%;font-size:10px;line-height:1.7}
.transfer{flex-direction:column;align-items:flex-start;gap:4px}.transfer-line{display:flex;gap:10px;flex-wrap:wrap}.transfer-title{color:var(--color-text);font-weight:500}
.transfer-detail{width:100%}.transfer-detail summary{cursor:pointer;color:var(--color-text);font-size:11px}.transfer-detail ol{margin:6px 0 4px;padding-left:18px;line-height:1.9;color:var(--color-text)}.transfer-detail li.walk,.transfer-detail li.drive{color:var(--color-muted)}.transfer-detail p{margin:0;font-size:11px}
.stop-content h4 button{font:inherit;text-align:left;padding:0;border:0;background:none;color:inherit;cursor:pointer;line-height:1.5}.stop-content h4 button span{font-size:12px;color:var(--color-muted);opacity:0;transition:opacity .2s}.stop-content h4 button:hover span,.stop-content h4 button:focus-visible span{opacity:1}.timeline-stop.is-selected .stop-content{background:var(--color-subtle);border-radius:10px;outline:1px solid var(--color-border)}.stop-content{padding:6px 10px;margin:-6px -10px}.stop-dot.meal,.stop-dot.hotel{background:var(--color-surface);box-shadow:0 0 0 1px var(--color-muted)}.transfer{border:1px solid var(--color-border);border-radius:8px;padding:6px 10px;gap:3px;max-width:400px}.transfer-line{gap:8px;font-size:11px}.transfer-detail>summary{font-size:10px;color:var(--color-muted)}.transfer-detail[open]>summary{margin-bottom:8px}.transfer-reason{display:block;margin-top:6px}.conflict-details{margin:10px 0;color:var(--color-warning);font-size:11px}.conflict-details>summary{cursor:pointer}.timeline{margin:20px 0}.timeline-stop{padding-bottom:18px}.stop-content h4{font-size:16px;margin:4px 0}.stop-content h4 button:focus-visible{outline:2px solid var(--color-text);outline-offset:3px;border-radius:3px}
</style>
<style scoped>
.stop-wait{margin:0 0 14px 136px;padding:8px 10px;border-left:2px solid var(--color-border);color:var(--color-muted);font-size:11px;line-height:1.6;background:var(--color-subtle);border-radius:0 7px 7px 0}
@media(max-width:640px){.stop-wait{margin-left:102px}}
</style>
