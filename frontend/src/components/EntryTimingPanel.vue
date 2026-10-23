<template>
  <section class="entry-panel" aria-label="行程入场时间">
    <details :open="!!fixedTime || !!entryWindow">
      <summary><strong>行程入场时间</strong><span>{{ recordedLabel }}</span></summary>
      <p class="hint">已有门票或预约后，按凭证填写可入场时间，用于检查路线。这里不验证预订状态。</p>
      <p v-if="visitDate !== dayDate" class="notice">当前查票日期与行程不同。请切回 {{ dayDate }}，再设置这一天的入场时间。</p>
      <form @submit.prevent="check">
        <fieldset :disabled="working || disabled || visitDate !== dayDate">
          <label class="mode-label">时间类型<select v-model="mode"><option value="window">入场时间段</option><option value="fixed">固定场次</option><option value="none">清除时间约束</option></select></label>
          <div v-if="mode !== 'none'" class="times"><label>{{ mode === 'fixed' ? '开始时间' : '最早入场' }}<input v-model="start" type="time" required /></label><label v-if="mode === 'window'">最晚入场<input v-model="end" type="time" required /></label></div>
          <p v-if="mode === 'window'" class="hint">填写凭证上的具体时间；时段结束指最晚入场，不是游览结束。所有时间均为目的地当地时间。</p>
          <button type="submit" class="check-button" :disabled="!valid">{{ working ? '正在处理…' : '检查与预览' }}</button>
        </fieldset>
      </form>
      <p v-if="error" role="alert" class="notice">{{ error }}</p>
      <div v-if="preview" class="entry-preview" aria-live="polite">
        <div class="outcome"><strong>{{ statusLabel }}</strong><span v-if="arrival">预计入场 {{ arrival }}</span></div>
        <ul v-if="selected?.schedule?.conflicts.length"><li v-for="text in selected.schedule.conflicts" :key="text">{{ text }}</li></ul>
        <p v-if="selected?.schedule?.status === 'unverified'" class="notice">交通信息不足，暂时无法确认能否按时入场。保存后可通过“重新校验交通与时间”更新。</p>
        <label v-if="preview.earlier_day" class="earlier-choice"><input v-model="earlier" type="checkbox" />改为 {{ preview.earlier_day.start_time }} 出发，保留地点和停留时长</label>
        <details class="schedule-detail"><summary>查看当天时间安排</summary><ol><li v-for="item in selected?.schedule?.items || []" :key="item.id"><time>{{ item.start_time || '待核实' }}</time><span>{{ item.name }}</span><small>{{ item.duration_minutes ? `${item.duration_minutes} 分钟` : '' }}</small></li></ol></details>
        <p class="hint">复用已保存的交通耗时，未查询新路况、排队或余票。保存会新增一个可恢复版本。</p>
        <p v-if="mealWarnings.length" class="notice">{{ mealWarnings.join('；') }}</p>
        <button class="save-button" type="button" :disabled="working || disabled || visitDate !== dayDate" @click="save">{{ working ? '正在保存…' : selected?.schedule?.status === 'conflict' ? '仍保存时间，保留冲突提示' : '保存到行程' }}</button>
        <div v-if="preview.day.schedule?.status !== 'feasible'" class="other-options">
          <strong>还可以怎样安排？</strong>
          <p class="hint">尝试调整同日顺序；可选移动未预约的灵活景点。会查询变化后的交通，原行程先保留。</p>
          <label class="earlier-choice"><input v-model="allowCrossDay" type="checkbox" :disabled="working || disabled" />允许把未锁定、无预约的灵活景点移到相邻一天</label>
          <button type="button" class="check-button" :disabled="working || disabled" @click="findOptions">{{ working ? '正在核对路线…' : '寻找其他调整方案' }}</button>
          <p v-if="searched && !options.length" class="notice">本次有界搜索未找到可展示的方案；可手动调整出发时间、停留时长或地点后重新检查。</p>
          <article v-for="option in options" :key="option.id" class="option-card"><header><strong>{{ option.title }}</strong><span>{{ option.status === 'feasible' ? '按已知条件可安排' : option.status === 'unverified' ? '交通待核实' : '仍有冲突' }}</span></header><p class="hint">{{ option.detail }}</p><ul v-if="option.conflicts.length"><li v-for="conflict in option.conflicts" :key="conflict">{{ conflict }}</li></ul><p v-for="warning in option.warnings" :key="warning" class="notice">{{ warning }}</p><details class="schedule-detail"><summary>查看调整后的每天安排</summary><div v-for="day in option.days" :key="day.date"><h4>{{ day.date }} · {{ day.description }}</h4><ol><li v-for="item in day.schedule?.items || []" :key="item.id"><time>{{ item.start_time || '待核实' }}</time><span>{{ item.name }}</span><small>{{ item.duration_minutes ? `${item.duration_minutes} 分钟` : '' }}</small></li></ol></div></details><button type="button" class="save-button" :disabled="working || disabled || option.status !== 'feasible'" @click="adopt(option)">采用并保存这项调整</button></article>
        </div>
      </div>
    </details>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { previewEntry, saveEntryTiming, queryEntryOptions, applyEntryOption, type EntryOption, type EntryPreview, type EntryTimingInput } from '@/services/trips'
import type { SavedTrip } from '@/types'
const props = defineProps<{ tripId: string; version: number; dayDate: string; itemId: string; visitDate: string; fixedTime?: string | null; entryWindow?: { start: string; end: string } | null; disabled?: boolean }>()
const emit = defineEmits<{ saved: [trip: SavedTrip]; working: [value: boolean] }>()
const mode = ref('window'), start = ref(''), end = ref('')
const preview = ref<EntryPreview | null>(null), earlier = ref(false), error = ref(''), working = ref(false)
const allowCrossDay = ref(false), options = ref<EntryOption[]>([]), searched = ref(false)
let generation = 0
const valid = computed(() => mode.value === 'none' || /^([01]\d|2[0-3]):[0-5]\d$/.test(start.value) && (mode.value === 'fixed' || /^([01]\d|2[0-3]):[0-5]\d$/.test(end.value) && start.value < end.value))
const recordedLabel = computed(() => props.entryWindow ? `${props.entryWindow.start}—${props.entryWindow.end}` : props.fixedTime ? `固定 ${props.fixedTime}` : '录入与检查 ＋')
const selected = computed(() => earlier.value && preview.value?.earlier_day ? preview.value.earlier_day : preview.value?.day)
const statusLabel = computed(() => selected.value?.schedule?.status === 'feasible' ? '按已知时间可以安排' : selected.value?.schedule?.status === 'conflict' ? '存在时间冲突' : '到达时间待核实')
const arrival = computed(() => selected.value?.schedule?.items.find(item => item.id === props.itemId)?.start_time)
const mealWarnings = computed(() => (selected.value?.schedule?.warnings || []).filter(text => /早餐|午餐|晚餐/.test(text) && !text.startsWith('游览时长')))
function invalidate() { generation++; preview.value = null; earlier.value = false; error.value = ''; options.value = []; searched.value = false }
watch(() => [props.tripId, props.version, props.dayDate, props.itemId, props.fixedTime, props.entryWindow?.start, props.entryWindow?.end], () => {
  invalidate(); mode.value = props.entryWindow ? 'window' : props.fixedTime ? 'fixed' : 'window'
  start.value = props.entryWindow?.start || props.fixedTime || ''; end.value = props.entryWindow?.end || ''
}, { immediate: true })
watch(() => [mode.value, start.value, end.value, props.visitDate, props.disabled], invalidate, { flush: 'sync' })
watch(allowCrossDay, () => { generation++; options.value = []; searched.value = false; error.value = '' }, { flush: 'sync' })
onUnmounted(() => { generation++; emit('working', false) })
function input(): EntryTimingInput {
  return { base_version: props.version, day_date: props.dayDate, item_id: props.itemId,
    fixed_start_time: mode.value === 'fixed' ? start.value : null,
    entry_window: mode.value === 'window' ? { start: start.value, end: end.value } : null }
}
async function check() {
  if (working.value || props.disabled || !valid.value || props.visitDate !== props.dayDate) return
  invalidate(); const request = ++generation; const body = input()
  working.value = true; emit('working', true)
  try {
    const result = await previewEntry(props.tripId, body)
    if (request !== generation) return
    if (result.version !== props.version || result.item_id !== props.itemId || result.day.date !== props.dayDate) throw new Error('行程已变化，请重新预览')
    preview.value = result
  } catch (e: any) { if (request === generation) error.value = e.message || '预览失败，请重试' }
  finally { working.value = false; emit('working', false) }
}
async function save() {
  if (!preview.value || working.value || props.disabled || props.visitDate !== props.dayDate || preview.value.version !== props.version) return
  const request = ++generation; const body = { ...input(), choice: earlier.value ? 'earlier' as const : 'keep' as const }
  working.value = true; emit('working', true); error.value = ''
  try {
    const saved = await saveEntryTiming(props.tripId, body)
    if (request === generation) emit('saved', saved)
  } catch (e: any) { if (request === generation) error.value = e.message || '保存失败，请重新预览' }
  finally { working.value = false; emit('working', false) }
}
async function findOptions() {
  if (!preview.value || working.value || props.disabled || props.visitDate !== props.dayDate) return
  const request = ++generation; working.value = true; emit('working', true); error.value = ''; options.value = []; searched.value = false
  try {
    const result = await queryEntryOptions(props.tripId, input(), allowCrossDay.value)
    if (request !== generation) return
    if (result.version !== props.version || result.item_id !== props.itemId || result.day_date !== props.dayDate) throw new Error('行程已变化，请重新检查')
    options.value = result.options; searched.value = true
  } catch (e: any) { if (request === generation) error.value = e.message || '方案查询失败，原行程已保留' }
  finally { working.value = false; emit('working', false) }
}
async function adopt(option: EntryOption) {
  if (working.value || props.disabled || !options.value.includes(option) || option.status !== 'feasible' || props.visitDate !== props.dayDate) return
  const request = ++generation; working.value = true; emit('working', true); error.value = ''
  try { const saved = await applyEntryOption(props.tripId, input(), allowCrossDay.value, option.id); if (request === generation) emit('saved', saved) }
  catch (e: any) { if (request === generation) error.value = e.message || '调整未保存，请重新查询' }
  finally { working.value = false; emit('working', false) }
}
</script>
<style scoped>
.entry-panel { margin-bottom: 25px; padding: 17px 19px; border: 1px solid var(--color-border); border-radius: 16px; }
summary { display: flex; justify-content: space-between; gap: 12px; cursor: pointer; list-style: none; font-size: 12px; }
summary::-webkit-details-marker { display: none; } summary span { color: var(--color-muted); font-size: 11px; }
fieldset { border: 0; padding: 0; margin: 15px 0 0; min-width: 0; }
label { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.mode-label { justify-content: space-between; }
input[type=time], select { border: 1px solid var(--color-border); border-radius: 8px; padding: 7px 9px; color: var(--color-text); background: var(--color-surface); font: inherit; }
.times { display: flex; gap: 20px; flex-wrap: wrap; margin-top: 14px; }
.hint, .notice { font-size: 11px; line-height: 1.8; color: var(--color-muted); margin: 11px 0; }
.notice { background: var(--color-subtle); padding: 9px 11px; border-radius: 8px; }
button { border-radius: 999px; padding: 8px 14px; border: 0; cursor: pointer; font: inherit; font-size: 12px; } button:disabled { cursor: default; opacity: .5; }
.check-button { color: var(--color-text); background: var(--color-subtle); } .save-button { color: #fff; background: var(--travel-accent-fill, #0071e3); }
.entry-preview { border-top: 1px solid var(--color-border); padding-top: 14px; margin-top: 16px; }
.outcome { display: flex; gap: 12px; justify-content: space-between; font-size: 12px; }.outcome span { color: var(--color-muted); }
ul { padding-left: 17px; font-size: 12px; line-height: 1.8; }
.earlier-choice { margin: 14px 0; line-height: 1.7; align-items: flex-start; }.earlier-choice input { margin-top: 4px; }
.schedule-detail { margin: 14px 0; } .schedule-detail summary { color: var(--color-muted); font-size: 11px; }
.other-options { border-top:1px solid var(--color-border);margin-top:20px;padding-top:18px;font-size:12px }.option-card{border:1px solid var(--color-border);padding:14px;border-radius:12px;margin-top:14px}.option-card header{display:flex;gap:12px;justify-content:space-between}.option-card header span{font-size:10px;color:var(--color-muted)}.option-card h4{font-size:11px;font-weight:500;line-height:1.8}
ol { list-style: none; padding: 0; margin: 12px 0; font-size: 11px; } li { margin: 7px 0; } ol li { display: flex; gap: 12px; } time { min-width: 45px; } small { color: var(--color-muted); margin-left: auto; white-space: nowrap; }
@media(max-width:560px) { .entry-panel { padding: 15px; }.times { gap: 12px; } .times label { flex-wrap: wrap; } }
</style>
