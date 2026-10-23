<template>
  <section class="trip-cost-panel" aria-label="旅行费用测算">
    <div class="cost-context">
      <label>同行人数<a-input-number :value="context.party_size ?? undefined" @update:value="value => setValue('party_size', value)" aria-label="测算同行人数" :min="1" :max="50" :precision="0" placeholder="待确定" @change="invalidate" /></label>
      <label>每晚房间数<a-input-number :value="context.room_count ?? undefined" @update:value="value => setValue('room_count', value)" aria-label="测算每晚房间数" :min="1" :max="50" :precision="0" placeholder="待确定" @change="invalidate" /></label>
      <label class="cost-limit">目的地内全体预算（元）<a-input-number :value="context.destination_budget ?? undefined" @update:value="value => setValue('destination_budget', value)" aria-label="测算目的地内全体预算" :disabled="context.budget_mode === 'deferred'" :min="0" :max="10000000" :precision="2" placeholder="不含往返大交通" @change="invalidate" /></label>
      <a-button :loading="loading" :disabled="disabled || !tripId" @click="calculate">重新测算</a-button>
    </div>
    <label class="cost-note"><input type="checkbox" :checked="context.budget_mode === 'deferred'" @change="setDeferred(($event.target as HTMLInputElement).checked)" /> 预算稍后再定（仍可查看费用参考）</label>
    <p v-if="error" role="alert" class="cost-error">{{ error }}</p>
    <p v-if="dirty" role="status" class="cost-note">条件已修改，请重新测算。</p>
    <template v-if="report && !dirty">
      <div class="cost-heading"><div><span>已列项目参考小计</span><strong>{{ report.known_total === 0 && report.unknown_count ? '待补充' : money(report.known_total) }}</strong></div><p>{{ report.context.party_size }} 人 · 每晚 {{ report.context.room_count }} 间 · {{ report.context.lodging_nights == null ? '晚数待核实' : `${report.context.lodging_nights} 晚` }}</p></div>
      <p v-for="assumption in report.assumptions" :key="assumption" class="cost-note">{{ assumption }}</p>
      <div class="cost-categories"><div v-for="(label, key) in categories" :key="key"><span>{{ label }}</span><strong>{{ categoryAmount(key) }}</strong></div></div>
      <p class="cost-note">{{ report.unknown_count }} 项费用未知，{{ report.estimated_count }} 项含参考估算；未知金额未计入小计。</p>
      <p class="cost-target" :class="{ over: report.budget.status === 'over_reference' }" role="status">{{ targetMessage }}</p>
      <div v-if="report.lodging_allowance" class="lodging-allowance">
        <strong>{{ report.lodging_allowance.already_over_reference ? '已列参考费用已超目标，需要先调整预算或项目' : `待报价住宿的参考预留 ${money(report.lodging_allowance.remaining_reference_budget)}` }}</strong>
        <p v-if="!report.lodging_allowance.already_over_reference">待补 {{ report.lodging_allowance.unpriced_room_nights }} 间夜，平均参考上限 {{ money(report.lodging_allowance.average_ceiling_per_room_night) }} / 间夜。</p>
        <small>{{ report.lodging_allowance.scope }}</small>
      </div>
      <p v-else-if="report.unknown_hotel_nights && report.other_unknown_count" class="cost-note">住宿及其他 {{ report.other_unknown_count }} 项费用仍未知，暂不能判断住宿可用额度。</p>
      <p v-if="report.hotel_quote_taxes_pending" class="cost-note">{{ report.hotel_quote_taxes_pending }} 晚所填报价的税费尚未确认，不能据此判断预算满足。</p>
      <details><summary>查看每项计算与来源</summary><div class="cost-lines"><article v-for="(line, index) in report.lines" :key="index"><div><small>{{ line.date }} · {{ categories[line.kind as keyof typeof categories] }}</small><strong>{{ line.name }}</strong><p>{{ line.unit_price == null ? '单价待补充' : `${money(line.unit_price)} × ${line.quantity} ${line.unit}` }}<span v-if="line.source"> · {{ line.source }}</span></p><p v-if="line.booking_id">覆盖 {{ line.booking_participants }} 人 · 用户录入实付 {{ money(line.paid_total || 0) }} · 已退 {{ money(line.refunded_total || 0) }} · {{ line.booking_status === 'cancelled' ? '已取消，未退金额仍计入' : '尚未独立核验订单' }}</p><p v-if="line.quote">{{ line.quote.room_type }} · {{ line.quote.taxes_included===true?'用户已注明含税':'税费待确认' }}<span v-if="line.quote.source_label"> · 用户来源备注：{{ line.quote.source_label }}</span></p><p v-if="line.evidence">{{ factDescription(line.evidence) }} <a v-if="officialSourceUrl(line.evidence)" :href="officialSourceUrl(line.evidence)!" target="_blank" rel="noopener noreferrer">查看官方票价 ↗</a></p></div><span>{{ line.amount == null ? '未知' : money(line.amount) }}</span></article></div></details>
      <details><summary>计算范围与未包含的费用</summary><p v-for="note in report.notes" :key="note" class="cost-note">{{ note }}</p><p class="cost-note">未包含：{{ report.exclusions.join('、') }}。</p></details>
    </template>
    <p v-else-if="!dirty && !loading" class="cost-note">尚无测算结果，可填写条件后重新测算。</p>
    <HotelQuoteEditor :trip-id="tripId" :version="version" :plan="plan" :disabled="disabled || !tripId" @saved="quotesSaved" />
    <button type="button" class="text-action" :disabled="disabled || enriching" @click="$emit('enrich')">{{ enriching ? '正在补充…' : '补充价格与开放时间' }}</button>
  </section>
</template>
<script setup lang="ts">
import { computed, reactive, ref, watch, nextTick } from 'vue'
import type { CostReport, TripCostContext, TripFormData, TripPlan, SavedTrip } from '@/types'
import HotelQuoteEditor from './HotelQuoteEditor.vue'
import { previewCost } from '@/services/trips'
import { factDescription, officialSourceUrl } from '@/utils/factDescription'
const props = defineProps<{ tripId: string; version: number; plan: TripPlan; request?: TripFormData | null; report: CostReport | null; disabled: boolean; enriching: boolean }>()
const emit = defineEmits<{ report: [CostReport | null]; enrich: []; saved: [SavedTrip] }>()
const context = reactive<TripCostContext>({ party_size: null, room_count: null, destination_budget: null, budget_mode: 'active' })
const categories = { attractions: '门票', meals: '餐饮', hotels: '住宿', transport: '当地交通' }
const money = (value: number) => `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
function categoryAmount(key: keyof typeof categories) {
  const report = props.report
  if (!report) return '待补充'
  const lines = report.lines.filter(line => line.kind === key)
  if (!lines.length) return key === 'hotels' && report.context.lodging_nights === 0 ? '0 晚' : '未列项目'
  const unknown = lines.filter(line => line.amount == null).length
  if (unknown === lines.length) return '待补充'
  return money(report.totals[key]) + (unknown ? ' + 待补充' : '')
}
const loading = ref(false), dirty = ref(false), error = ref('')
let generation = 0
watch(() => [props.tripId, props.version], () => {
  generation++
  Object.assign(context, { party_size: props.request?.party_size ?? null, room_count: props.request?.room_count ?? null, destination_budget: props.request?.destination_budget ?? null, budget_mode: props.request?.budget_mode || 'active' })
  dirty.value = false; error.value = ''; loading.value = false
}, { immediate: true })
function setValue(key: 'party_size' | 'room_count' | 'destination_budget', value: number | string | null | undefined) { context[key] = value == null || value === '' ? null : Number(value) }
function setDeferred(value: boolean) { context.budget_mode = value ? 'deferred' : 'active'; invalidate() }
function invalidate() { generation++; dirty.value = true; error.value = ''; emit('report', null); loading.value = false }
async function calculate() {
  const current = ++generation, id = props.tripId, version = props.version
  loading.value = true; error.value = ''
  try {
    const result = await previewCost(id, version, { ...context })
    if (current !== generation || id !== props.tripId || version !== props.version) return
    if (result.version !== version) throw new Error('行程已更新，请重新打开后测算')
    dirty.value = false; emit('report', result.cost_report)
  } catch (e: any) { if (current === generation) error.value = e.message || '测算暂时失败，请重试' }
  finally { if (current === generation) loading.value = false }
}
async function quotesSaved(saved: SavedTrip) {
  const trial={...context}
  emit('saved',saved)
  await nextTick()
  if(props.tripId!==saved.id || props.version!==saved.version)return
  Object.assign(context,trial)
  invalidate()
  await calculate()
}
const targetMessage = computed(() => {
  const budget = props.report?.budget
  if (!budget || budget.status === 'not_set') return '可填写目的地内全体预算，对照已列项目参考费用。'
  if (budget.status === 'deferred') return '预算稍后再定：当前仅展示费用参考，不判断预算是否满足。'
  if (budget.status === 'needs_scope') return `原文提到预算 ${money(budget.limit!)}；请确认它是否为全体在目的地内的预算。`
  if (budget.status === 'needs_context') return '人数或房间数尚未确认，暂不判断预算是否满足。'
  if (budget.status === 'over_reference') return `已列参考小计超过目标 ${money(budget.limit!)}，可考虑调整住宿、餐厅或可选项目。`
  if (budget.status === 'incomplete') return props.report?.hotel_quote_taxes_pending ? `报价税费仍待确认，尚不能判断是否满足目标 ${money(budget.limit!)}。` : `仍有未知费用，尚不能判断是否满足目标 ${money(budget.limit!)}。`
  return `已列参考项目在目标 ${money(budget.limit!)} 内；未列项目与实际预订价格仍需另计。`
})
</script>
<style scoped>
.trip-cost-panel{color:var(--color-text)}.cost-context{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;margin-bottom:28px}.cost-context label{display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--color-muted);flex:1;min-width:100px}.cost-context .ant-input-number{width:100%}.cost-context .cost-limit{flex:2;min-width:200px}.cost-context>.ant-btn{border-radius:20px}.cost-heading{display:flex;justify-content:space-between;gap:16px;align-items:end}.cost-heading span{display:block;font-size:12px;color:var(--color-muted)}.cost-heading strong{font-size:32px;font-weight:600;letter-spacing:-.04em}.cost-heading p,.cost-note{font-size:12px;color:var(--color-muted);line-height:1.8}.cost-categories{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}.cost-categories>div{padding:14px;background:var(--color-subtle);border-radius:14px}.cost-categories span{display:block;font-size:12px;color:var(--color-muted);margin-bottom:6px}.cost-categories strong{font-size:18px;font-weight:500;overflow-wrap:anywhere}.cost-target{font-size:13px;line-height:1.8}.cost-target.over,.cost-error{color:var(--color-warning)}details{border-top:1px solid var(--color-border);padding:16px 0;font-size:12px}summary{cursor:pointer;color:var(--color-muted)}.cost-lines article{display:flex;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid var(--color-border)}.cost-lines article>div{min-width:0}.cost-lines small{color:var(--color-muted)}.cost-lines strong{display:block;font-weight:500;overflow-wrap:anywhere;margin-top:4px}.cost-lines p{color:var(--color-muted);line-height:1.8;margin:4px 0 0}.cost-lines article>span{white-space:nowrap}.text-action{margin-top:14px;border:0;background:transparent;color:var(--travel-accent);cursor:pointer}.text-action:disabled{opacity:.5;cursor:default}@media(max-width:640px){.cost-heading{align-items:start;flex-direction:column;gap:0}.cost-heading p{margin-top:6px}.cost-categories{grid-template-columns:repeat(2,minmax(0,1fr))}.cost-context>.ant-btn{width:100%}}
</style>
<style scoped>
.lodging-allowance{padding:16px;border:1px solid var(--color-border);border-radius:12px;margin:18px 0;font-size:13px}.lodging-allowance p,.lodging-allowance small{font-size:12px;line-height:1.8;color:var(--color-muted)}
</style>
