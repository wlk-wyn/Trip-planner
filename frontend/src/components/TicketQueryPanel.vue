<template>
  <a-modal :open="open" :footer="null" :width="760" :closable="!panelWorking" :mask-closable="!panelWorking" :keyboard="!panelWorking" wrap-class-name="ticket-modal-wrap" :style="{ top: '32px' }" @cancel="emit('close')">
    <template #title><div class="ticket-heading"><span>门票与预约</span><h2>{{ name }}</h2></div></template>
    <div class="ticket-query">
      <form class="query-bar" @submit.prevent="search">
        <label for="ticket-visit-date"><span>参观日期</span><input id="ticket-visit-date" v-model="visitDate" type="date" :min="today" :disabled="loading || panelWorking" required /></label>
        <button class="query-button" type="submit" :disabled="loading || panelWorking || !visitDate || visitDate < today">{{ loading ? '正在查询价格…' : result ? '更新价格' : '查询票种与价格' }}<span v-if="!loading" aria-hidden="true">↻</span></button>
      </form>
      <p v-if="visitDate !== dayDate" class="date-note">仅查询此日期的商品，不更改原行程。</p>
      <div class="ticket-scroll">
        <OfficialAdmissionPanel :trip-id="tripId" :version="version" :day-date="dayDate" :item-id="itemId" :visit-date="visitDate" />
        <EntryTimingPanel :trip-id="tripId" :version="version" :day-date="dayDate" :item-id="itemId" :visit-date="visitDate" :fixed-time="fixedTime" :entry-window="entryWindow" :disabled="disabled || bookingWorking" @saved="trip => emit('saved', trip)" @working="value => entryWorking = value" />
        <TicketBookingPanel :trip-id="tripId" :version="version" :day-date="dayDate" :item-id="itemId" :visit-date="visitDate" :bookings="bookings || []" :fixed-time="fixedTime" :entry-window="entryWindow" :disabled="disabled || entryWorking" @saved="trip => emit('saved', trip)" @working="value => bookingWorking = value" />
        <div v-if="loading" class="query-loading" role="status">
          <div class="section-heading"><h3>正在读取票种与价格</h3><span>美团</span></div>
          <div v-for="i in 2" :key="i" class="offer-skeleton" aria-hidden="true"><span></span><span></span><span></span></div>
          <p>查询可能需要 1—2 分钟，可以关闭窗口继续查看行程。</p>
        </div>
        <div v-else-if="error" role="alert" class="query-error"><strong>这次没有查到结果</strong><p>{{ error }}</p><button type="button" @click="search">再试一次 ↗</button></div>
        <template v-else-if="result">
          <div class="section-heading"><h3>{{ offers.length ? '门票与套餐' : referencePrices.length ? '门票价格参考' : '平台入口' }}<span v-if="offers.length" class="offer-count">{{ offers.length }}</span></h3><span>美团 · {{ result.cached ? '最近查询' : '更新于' }} {{ queryTime }}</span></div>
          <div v-if="!offers.length && referencePrices.length" class="reference-prices" aria-label="平台票价参考">
            <p>平台提供的政策价格，按季节或票种分列，非实时可购报价。</p>
            <div v-for="(price, index) in referencePrices" :key="index" class="reference-price"><div><h4>{{ price.title }}</h4><small v-if="price.condition">{{ price.condition }}</small></div><strong>{{ price.price }}</strong></div>
          </div>
          <div v-if="offers.length" class="offer-list" aria-label="美团门票商品">
            <article v-for="(offer, index) in offers" :key="`${index}:${offer.url}`" class="ticket-offer">
              <div class="offer-main"><div class="offer-title"><span v-if="offer.excludesAdmission" class="service-tag">不含门票</span><h4>{{ offer.title }}</h4></div><div v-if="offer.titlePrice" class="offer-price"><small>商品标题标价</small><strong>{{ offer.titlePrice }}</strong></div></div>
              <ul v-if="offer.details.length" class="offer-details"><li v-for="(detail, detailIndex) in offer.details" :key="detailIndex"><ProviderText :content="detail" /></li></ul>
              <div class="offer-bottom"><div class="price-note"><ProviderText v-if="offer.priceNote" :content="offer.priceNote" /><span v-else>票种与适用条件请在商品页核对</span></div><a class="offer-link" :href="offer.url" target="_blank" rel="noopener noreferrer">去美团预订 <span aria-hidden="true">↗</span></a></div>
            </article>
          </div>
          <div v-else-if="moreLinks.length" class="platform-entries"><a v-for="link in moreLinks" :key="link.url" class="platform-entry" :href="link.url" target="_blank" rel="noopener noreferrer"><span><strong><ProviderText :content="link.text" /></strong><small>前往平台查看票种、商品及适用条件</small></span><span aria-hidden="true">↗</span></a></div>
          <p v-else class="no-offers-note">暂未返回可打开的商品入口，可查看平台说明及上方官方预约信息。</p>
          <div v-if="offers.length && moreLinks.length" class="more-links"><a v-for="link in moreLinks" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.text }} ↗</a></div>
          <details class="full-reply"><summary>查看票种、优惠与平台完整说明 <span aria-hidden="true">＋</span></summary><div><ProviderText :content="result.content" /></div></details>
        </template>
        <div v-else class="query-empty"><svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M9 13h30v8a4 4 0 0 0 0 8v8H9v-8a4 4 0 0 0 0-8v-8Z"/><path d="M29 14v5m0 5v4m0 5v3"/></svg><h3>为这一天，选好入场方式。</h3><p>选择参观日期，查看门票、套票与讲解服务。</p></div>
      </div>
      <footer class="ticket-footer"><span class="footer-dot" aria-hidden="true"></span>在美团确认票种、余票与最终价格；查询不代表预约成功。</footer>
    </div>
  </a-modal>
</template>
<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { queryTickets, type TicketQueryResult } from '@/services/trips'
import { providerTokens, ticketOffers, ticketPriceReferences } from '@/utils/providerContent'
import ProviderText from './ProviderText.vue'
import OfficialAdmissionPanel from './OfficialAdmissionPanel.vue'
import EntryTimingPanel from './EntryTimingPanel.vue'
import TicketBookingPanel from './TicketBookingPanel.vue'
import type { SavedTrip, TicketBooking } from '@/types'
const props = defineProps<{ open: boolean; tripId: string; version: number; dayDate: string; itemId: string; name: string; fixedTime?: string | null; entryWindow?: { start: string; end: string } | null; bookings?: TicketBooking[]; disabled?: boolean }>()
const emit = defineEmits<{ close: []; saved: [trip: SavedTrip] }>()
const entryWorking = ref(false)
const bookingWorking = ref(false)
const panelWorking = computed(() => entryWorking.value || bookingWorking.value)
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const visitDate = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<TicketQueryResult | null>(null)
const offers = computed(() => ticketOffers(result.value?.content || ''))
const referencePrices = computed(() => ticketPriceReferences(result.value?.content || ''))
const moreLinks = computed(() => {
  const offerUrls = new Set(offers.value.map(offer => offer.url))
  const seen = new Set<string>()
  return providerTokens(result.value?.content || '').filter(token => {
    if (token.kind !== 'link' || !token.url || offerUrls.has(token.url) || seen.has(token.url)) return false
    seen.add(token.url); return true
  })
})
const queryTime = computed(() => result.value ? new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(result.value.queried_at)) : '')
let generation = 0
onUnmounted(() => { generation++ })
watch(() => [props.open, props.tripId, props.version, props.dayDate, props.itemId], () => {
  generation++; loading.value = false; error.value = ''; result.value = null
  visitDate.value = props.dayDate < today ? today : props.dayDate
  if (props.open) void search()
}, { immediate: true })
watch(visitDate, () => { result.value = null; error.value = '' })
async function search() {
  if (loading.value || panelWorking.value || !props.open || !props.tripId || !props.itemId || !visitDate.value || visitDate.value < today) return
  const request = ++generation
  loading.value = true; error.value = ''; result.value = null
  try {
    const response = await queryTickets(props.tripId, props.version, props.dayDate, props.itemId, visitDate.value)
    if (request !== generation) return
    if (response.version !== props.version || response.item_id !== props.itemId || response.visit_date !== visitDate.value) throw new Error('行程或查询日期已变化，请重新查询')
    result.value = response
  } catch (e: any) { if (request === generation) error.value = e.message || '查询失败，请稍后重试' }
  finally { if (request === generation) loading.value = false }
}
</script>
<style scoped>
.ticket-query { color: var(--color-text); }
.ticket-heading > span { display: block; color: var(--color-muted); font-size: 11px; font-weight: 400; letter-spacing: .08em; margin-bottom: 7px; }
.ticket-heading h2 { margin: 0; font-size: 26px; font-weight: 600; letter-spacing: -.04em; line-height: 1.3; overflow-wrap: anywhere; }
.query-bar { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin: 0; padding: 20px 30px; }
.query-bar label { display: flex; align-items: center; gap: 14px; min-width: 0; }
.query-bar label > span { font-size: 12px; color: var(--color-muted); white-space: nowrap; }
input { min-width: 0; width: 150px; border: 1px solid var(--color-border); background: var(--color-subtle); color: var(--color-text); border-radius: 10px; padding: 8px 10px; font: inherit; font-size: 13px; }
.query-button { display: flex; align-items: center; justify-content: center; gap: 16px; flex-shrink: 0; background: var(--travel-accent-fill, #0071e3); color: #fff; border: 0; border-radius: 999px; padding: 10px 17px; font: inherit; font-size: 12px; cursor: pointer; transition: background .2s, transform .2s; }
.query-button:hover:not(:disabled) { background: #0077ed; }
.query-button:disabled { opacity: .5; cursor: default; }
.date-note { margin: -7px 30px 14px; color: var(--color-muted); font-size: 11px; }
.ticket-scroll { max-height: min(610px, calc(100dvh - 277px)); min-height: 180px; overflow-y: auto; overscroll-behavior: contain; padding: 0 30px 24px; scrollbar-width: thin; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 0 14px; }
.section-heading h3 { margin: 0; display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; }
.section-heading > span { font-size: 10px; color: var(--color-muted); text-align: right; }
.offer-count { font-size: 10px; font-weight: 400; padding: 1px 6px; border-radius: 6px; background: var(--color-subtle); color: var(--color-muted); }
.offer-list { display: grid; gap: 12px; }
.reference-prices { margin-bottom: 18px; border: 1px solid var(--color-border); border-radius: 16px; padding: 0 18px; }
.reference-prices > p { color: var(--color-muted); font-size: 11px; line-height: 1.7; margin: 14px 0 4px; }
.reference-price { display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 15px 0; }
.reference-price + .reference-price { border-top: 1px solid var(--color-border); }
.reference-price h4 { margin: 0; font-size: 13px; font-weight: 500; line-height: 1.6; }
.reference-price small { display: block; font-size: 11px; color: var(--color-muted); margin-top: 4px; line-height: 1.7; }
.reference-price > strong { flex-shrink: 0; font-size: 18px; font-weight: 550; white-space: nowrap; }
.offer-details { padding: 0; margin: 12px 0 0; list-style: none; font-size: 11px; color: var(--color-muted); line-height: 1.8; }
.offer-details li + li { margin-top: 4px; }
.ticket-offer { border: 1px solid var(--color-border); border-radius: 16px; padding: 19px 20px 16px; background: var(--color-surface); }
.offer-main { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.offer-title { min-width: 0; }
.offer-title h4 { font-size: 14px; font-weight: 500; line-height: 1.7; margin: 0; overflow-wrap: anywhere; }
.offer-price { flex-shrink: 0; text-align: right; }
.offer-price small { display: block; font-size: 9px; color: var(--color-muted); margin-bottom: 4px; }
.offer-price strong { display: block; font-size: 23px; line-height: 1.2; font-weight: 550; letter-spacing: -.035em; white-space: nowrap; }
.service-tag { display: inline-block; border-radius: 5px; padding: 1px 6px; margin-bottom: 6px; font-size: 10px; background: var(--color-subtle); color: var(--color-muted); }
.offer-bottom { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--color-border); padding-top: 12px; margin-top: 15px; }
.price-note { font-size: 11px; color: var(--color-muted); line-height: 1.7; }
.offer-link { flex-shrink: 0; display: flex; align-items: center; gap: 13px; border-radius: 999px; padding: 7px 11px; background: var(--color-subtle); color: var(--color-text); font-size: 11px; text-decoration: none; transition: background .2s; }
.offer-link:hover { background: var(--color-border); color: var(--color-text); }
.more-links { display: flex; flex-wrap: wrap; gap: 12px; margin: 17px 0 0; }
.more-links a { font-size: 11px; color: var(--color-muted); text-decoration: none; }
.more-links a:hover { color: var(--travel-accent, #0071e3); }
.full-reply { margin-top: 23px; border-top: 1px solid var(--color-border); }
.full-reply summary { padding: 16px 0 0; display: flex; align-items: center; justify-content: space-between; cursor: pointer; font-size: 12px; color: var(--color-muted); list-style: none; }
.full-reply summary::-webkit-details-marker { display: none; }
.full-reply[open] summary > span { transform: rotate(45deg); }
.full-reply > div { margin-top: 18px; padding: 16px; border-radius: 12px; background: var(--color-subtle); font-size: 12px; line-height: 1.9; }
.no-offers-note { font-size: 13px; line-height: 1.9; color: var(--color-muted); }
.platform-entries { display: grid; gap: 12px; }
.platform-entry { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-radius: 16px; padding: 22px 20px; border: 1px solid var(--color-border); color: var(--color-text); text-decoration: none; }
.platform-entry:hover { background: var(--color-subtle); color: var(--color-text); }
.platform-entry strong { display: block; font-size: 15px; font-weight: 500; }
.platform-entry small { display: block; font-size: 11px; margin-top: 7px; color: var(--color-muted); }
.platform-entry > span:last-child { color: var(--travel-accent, #0071e3); font-size: 18px; }
.ticket-footer { display: flex; align-items: center; justify-content: center; gap: 7px; border-top: 1px solid var(--color-border); padding: 13px 20px; font-size: 10px; color: var(--color-muted); line-height: 1.7; background: var(--color-subtle); }
.footer-dot { width: 4px; height: 4px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.query-empty { text-align: center; padding: 22px 0 28px; }
.query-empty svg { width: 48px; height: 48px; stroke: var(--color-muted); stroke-width: 1.3; margin-bottom: 15px; opacity: .65; }
.query-empty h3 { font-size: 17px; font-weight: 500; letter-spacing: -.025em; margin: 0 0 9px; }
.query-empty p, .query-loading > p { color: var(--color-muted); font-size: 12px; line-height: 1.8; margin: 0; }
.query-loading > p { text-align: center; margin-top: 15px; font-size: 11px; }
.offer-skeleton { padding: 20px; border: 1px solid var(--color-border); border-radius: 16px; margin-top: 12px; }
.offer-skeleton span { display: block; height: 9px; width: 73%; background: var(--color-subtle); border-radius: 3px; margin-bottom: 13px; }
.offer-skeleton span:nth-child(2) { width: 48%; }
.offer-skeleton span:nth-child(3) { width: 90%; margin-bottom: 0; }
.query-error { padding: 24px 20px; border: 1px solid var(--color-border); border-radius: 14px; }
.query-error strong { font-size: 14px; font-weight: 500; }
.query-error p { font-size: 12px; color: var(--color-muted); line-height: 1.8; }
.query-error button { border: 0; padding: 0; background: none; color: var(--travel-accent, #0071e3); font-size: 12px; cursor: pointer; }
@media(max-width:560px) {
  .query-bar { padding: 16px 20px; gap: 10px; }
  .query-bar label { display: grid; gap: 5px; }
  .query-button { margin-top: 18px; padding: 10px 13px; gap: 8px; }
  input { width: 148px; }
  .ticket-scroll { padding: 0 20px 20px; max-height: calc(100dvh - 285px); }
  .date-note { margin-left: 20px; margin-right: 20px; }
  .ticket-offer { padding: 15px; }
  .offer-main { gap: 15px; }
  .offer-title h4 { font-size: 13px; }
  .offer-price strong { font-size: 21px; }
  .offer-bottom { flex-wrap: wrap; }
  .offer-link { margin-left: auto; }
  .section-heading > span { max-width: 135px; font-size: 9px; }
}
html[data-motion=on] .ticket-offer { animation: ticket-appear .25s ease-out both; }
@keyframes ticket-appear { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
@media(prefers-reduced-motion:reduce) { .ticket-offer { animation: none !important; } .query-button, .offer-link { transition: none; } }
</style>
<style>
.ticket-modal-wrap .ant-modal-content { padding: 0; border-radius: 22px; overflow: hidden; background: var(--color-surface); }
.ticket-modal-wrap .ant-modal-header { margin: 0; padding: 26px 60px 22px 30px; background: var(--color-surface); border-bottom: 1px solid var(--color-border); }
.ticket-modal-wrap .ant-modal-body { padding: 0; }
.ticket-modal-wrap .ant-modal-close { top: 25px; right: 23px; border-radius: 50%; color: var(--color-muted); }
html[data-theme=dark] .ticket-modal-wrap input[type=date] { color-scheme: dark; }
@media(max-width:560px) { .ticket-modal-wrap .ant-modal { top: 12px !important; max-width: calc(100vw - 20px); padding-bottom: 12px; }.ticket-modal-wrap .ant-modal-header { padding: 22px 50px 18px 20px; }.ticket-modal-wrap .ant-modal-close { top: 20px; right: 15px; } }
</style>
