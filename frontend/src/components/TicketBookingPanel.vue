<template>
  <section class="booking-panel" aria-label="我的门票记录">
    <details :open="bookings.length > 0">
      <summary><strong>我的门票记录</strong><span>{{ active ? `已录入 · ${money(active.paid_total)}` : '购买后录入 ＋' }}</span></summary>
      <p class="hint">在平台完成购买后，记录票种与实付总额。日期 {{ dayDate }}，关联当前景点及行程中的入场时间，不读取或核验平台订单。</p>
      <p v-if="timingChanged" class="notice">当前入场时间与这笔记录关联时不同，请先核对有效凭证。确认新的安排后，编辑并保存记录可更新关联时间。</p>
      <p v-if="visitDate !== dayDate" class="notice">当前在查询 {{ visitDate }} 的商品。下面记录始终属于行程 {{ dayDate }}，请按该日实际凭证填写。</p>
      <form @submit.prevent="check">
        <fieldset :disabled="working || disabled">
          <label>票种 / 套餐<input v-model="ticketName" maxlength="120" required placeholder="如：成人门票＋珍宝馆，2人" /></label>
          <div class="row"><label>覆盖人数<input v-model.number="participants" type="number" min="1" max="100" step="1" required /></label><label>实付总额（元）<input v-model.number="paid" type="number" min="0" max="1000000" step="0.01" required placeholder="整笔金额，含优惠" /></label></div>
          <label>购买渠道（选填）<input v-model="source" maxlength="80" placeholder="如：官方小程序 / 美团" /></label>
          <p class="hint">当前入场安排：{{ timingLabel }}。需要调整时，可先使用上方“行程入场时间”。</p>
          <label class="consent"><input v-model="confirmed" type="checkbox" />我已完成 {{ dayDate }} 的购买或预约，填写的是本次实付总额。</label>
          <button type="submit" :disabled="!valid">{{ working ? '处理中…' : '预览费用变化' }}</button>
        </fieldset>
      </form>
      <div v-if="preview" class="preview" aria-live="polite"><strong>已列小计 {{ money(preview.before_total) }} → {{ money(preview.after_total) }}</strong><p class="hint">本笔总额计一次，替换对应人数的门票参考费；未覆盖人数和其他未知费用仍单列。</p><p v-for="assumption in preview.cost_report.assumptions" :key="assumption" class="notice">{{ assumption }}</p><button class="primary" type="button" :disabled="working || disabled" @click="save">{{ active ? '保存记录修改' : '确认录入并更新预算' }}</button></div>
      <div v-if="bookings.length" class="records"><article v-for="booking in bookings" :key="booking.id"><div><strong>{{ booking.ticket_name }}</strong><small>{{ booking.status === 'recorded' ? '用户已录入' : '已记录取消' }} · {{ booking.participants }}人 · 实付 {{ money(booking.paid_total) }} · 已退 {{ money(booking.refunded_total) }}</small></div><button type="button" :disabled="working || disabled" @click="beginCancel(booking)">{{ booking.status === 'recorded' ? '取消 / 记录退款' : '更新已退金额' }}</button></article></div>
      <form v-if="cancelling" class="cancel-form" @submit.prevent="cancel">
        <p class="hint">只更新本地记录，请先在原平台办理退订。尚未到账的退款不要填入。</p>
        <fieldset :disabled="working || disabled"><label>累计已退金额（元）<input v-model.number="refunded" type="number" min="0" :max="cancelling.paid_total" step="0.01" required /></label><p class="notice">本笔仍计入 {{ money(Math.max(0, cancelling.paid_total - Number(refunded || 0))) }}。取消后，继续参观所需门票会恢复为参考费。</p><label v-if="cancelling.fixed_start_time || cancelling.entry_window" class="consent"><input v-model="clearTime" type="checkbox" />同时清除由这笔记录关联的入场时间</label><button type="submit">确认取消记录与已退金额</button><button type="button" @click="cancelling = null">返回</button></fieldset>
      </form>
      <p v-if="error" class="notice" role="alert">{{ error }}</p>
    </details>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import type { TicketBooking, SavedTrip } from '@/types'
import { previewBooking, saveBooking, cancelBooking, type BookingInput, type BookingPreview } from '@/services/trips'
const props = defineProps<{ tripId: string; version: number; itemId: string; dayDate: string; visitDate: string; bookings: TicketBooking[]; fixedTime?: string | null; entryWindow?: { start: string; end: string } | null; disabled?: boolean }>()
const emit = defineEmits<{ saved: [trip: SavedTrip]; working: [value: boolean] }>()
const active = computed(() => props.bookings.find(b => b.status === 'recorded'))
const ticketName = ref(''), participants = ref<number | string>(1), paid = ref<number | string>(''), source = ref(''), confirmed = ref(false)
const preview = ref<BookingPreview | null>(null), error = ref(''), working = ref(false)
const cancelling = ref<TicketBooking | null>(null), refunded = ref<number | string>(0), clearTime = ref(false)
let generation = 0
const valid = computed(() => confirmed.value && ticketName.value.trim() && Number.isInteger(participants.value) && Number(participants.value) >= 1 && Number(participants.value) <= 100 && typeof paid.value === 'number' && Number.isFinite(paid.value) && paid.value >= 0 && paid.value <= 1000000)
const timingLabel = computed(() => props.entryWindow ? `${props.entryWindow.start}—${props.entryWindow.end}` : props.fixedTime || '尚未设置，录入后仍需补充')
const timingChanged = computed(() => !!active.value && ((active.value.fixed_start_time || '') !== (props.fixedTime || '') || (active.value.entry_window?.start || '') !== (props.entryWindow?.start || '') || (active.value.entry_window?.end || '') !== (props.entryWindow?.end || '')))
const money = (value: number) => `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
function invalidate() { generation++; preview.value = null; error.value = '' }
watch(() => [props.tripId, props.version, props.itemId, props.dayDate], () => {
  invalidate(); ticketName.value = active.value?.ticket_name || ''; participants.value = active.value?.participants || 1
  paid.value = active.value?.paid_total ?? ''; source.value = active.value?.source_label || ''; confirmed.value = false; cancelling.value = null
}, { immediate: true })
watch(() => [ticketName.value, participants.value, paid.value, source.value, confirmed.value, props.visitDate, props.disabled], invalidate, { flush: 'sync' })
onUnmounted(() => { generation++; emit('working', false) })
function input(): BookingInput { return { base_version: props.version, ...(active.value ? { booking_id: active.value.id } : {}), day_date: props.dayDate, item_id: props.itemId, ticket_name: ticketName.value.trim(), participants: Number(participants.value), paid_total: Number(paid.value), source_label: source.value.trim() } }
async function check() {
  if (!valid.value || working.value || props.disabled) return
  invalidate(); const request = ++generation; working.value = true; emit('working', true)
  try { const result = await previewBooking(props.tripId, input()); if (request !== generation) return
    if (result.version !== props.version || result.item_id !== props.itemId || result.day_date !== props.dayDate) throw new Error('行程已变化，请重新预览')
    preview.value = result
  } catch (e: any) { if (request === generation) error.value = e.message || '费用预览失败' }
  finally { working.value = false; emit('working', false) }
}
async function save() {
  if (!preview.value || !valid.value || working.value || props.disabled) return
  const request = ++generation; working.value = true; emit('working', true)
  try { const saved = await saveBooking(props.tripId, input()); if (request === generation) emit('saved', saved) }
  catch (e: any) { if (request === generation) error.value = e.message || '记录未保存，请重试' }
  finally { working.value = false; emit('working', false) }
}
function beginCancel(booking: TicketBooking) { invalidate(); cancelling.value = booking; refunded.value = booking.refunded_total; clearTime.value = false }
async function cancel() {
  if (!cancelling.value || working.value || props.disabled || typeof refunded.value !== 'number' || !Number.isFinite(refunded.value) || refunded.value < 0 || refunded.value > cancelling.value.paid_total) return
  const request = ++generation; working.value = true; emit('working', true); error.value = ''
  try { const saved = await cancelBooking(props.tripId, props.version, cancelling.value.id, refunded.value, clearTime.value); if (request === generation) emit('saved', saved) }
  catch (e: any) { if (request === generation) error.value = e.message || '取消记录未保存' }
  finally { working.value = false; emit('working', false) }
}
</script>
<style scoped>
.booking-panel{border:1px solid var(--color-border);border-radius:16px;padding:17px 19px;margin-bottom:25px}summary{display:flex;justify-content:space-between;gap:12px;cursor:pointer;list-style:none;font-size:12px}summary::-webkit-details-marker{display:none}summary span,.hint,small{color:var(--color-muted)}.hint,.notice{font-size:11px;line-height:1.8;margin:12px 0}.notice{padding:10px;background:var(--color-subtle);border-radius:9px}fieldset{border:0;padding:0;margin:0;min-width:0}label{display:grid;gap:7px;font-size:12px;margin:12px 0}input:not([type=checkbox]){width:100%;box-sizing:border-box;padding:9px;border:1px solid var(--color-border);border-radius:9px;background:var(--color-surface);color:var(--color-text);font:inherit}.row{display:grid;grid-template-columns:1fr 1fr;gap:15px}.consent{display:flex;align-items:flex-start;gap:7px;font-size:11px;line-height:1.8}.consent input{margin-top:4px}button{border:0;border-radius:999px;background:var(--color-subtle);color:var(--color-text);padding:8px 13px;font:inherit;font-size:11px;cursor:pointer}button:disabled{opacity:.5;cursor:default}button.primary{background:var(--travel-accent-fill,#0071e3);color:#fff}.preview,.records,.cancel-form{border-top:1px solid var(--color-border);margin-top:16px;padding-top:15px}.preview strong{font-size:13px}.records article{display:flex;align-items:center;gap:14px;justify-content:space-between;margin:12px 0}.records strong{font-size:12px;font-weight:500}.records small{display:block;font-size:10px;line-height:1.8;margin-top:4px}.records button{flex-shrink:0}.cancel-form button+button{margin-left:9px}@media(max-width:560px){.booking-panel{padding:15px}.records article{align-items:flex-start;flex-direction:column}.row{gap:10px}}
</style>
