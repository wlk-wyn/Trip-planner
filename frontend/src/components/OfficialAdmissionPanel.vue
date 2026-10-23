<template>
  <section class="official-guide" aria-label="官方预约与入场须知">
    <header><strong>官方预约与入场</strong><span v-if="guide">{{ guide.status === 'reviewed' ? '已整理' : '待复核' }} · {{ guide.reviewed_on }}</span></header>
    <p v-if="loading" role="status" class="guide-note">正在读取已核实的官方须知…</p>
    <div v-else-if="error" role="alert"><p class="guide-note">{{ error }}</p><button class="text-button" type="button" @click="load">重试读取</button></div>
    <template v-else-if="guide">
      <template v-if="guide.status === 'reviewed'">
        <dl><div v-for="row in guide.summary" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.text }}</dd></div></dl>
        <p v-if="windowLabel" class="guide-window">{{ windowLabel }}</p>
        <p v-if="guide.notice" class="guide-notice">{{ guide.notice }}</p>
        <details v-if="guide.details.length" class="guide-details"><summary>入场、票种与退改细则 <span aria-hidden="true">＋</span></summary><dl><div v-for="row in guide.details" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.text }}</dd></div></dl></details>
        <p class="guide-note">{{ guide.booking_channel }}</p>
      </template>
      <p v-else class="guide-notice">{{ guide.notice }} 当前不展示过期的具体规则。</p>
      <div class="guide-actions"><small>{{ guide.source_name }} · 常规规则参考</small><a v-if="bookingUrl" :href="bookingUrl" target="_blank" rel="noopener noreferrer">{{ guide.booking_label || '去官方预约' }} ↗</a></div>
      <details class="guide-sources"><summary>资料来源与复核范围</summary><p>复核于 {{ guide.reviewed_on }}，参考期限至 {{ guide.valid_until }}。未查询余票、临时公告或预约成功状态。</p><a v-for="source in sources" :key="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }} ↗</a></details>
    </template>
    <p v-else class="guide-note">该景点的官方预约须知尚未收录，平台回复不能当作官方规则。</p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import type { OfficialAdmissionGuide } from '@/types/admission'
import { readAdmissionGuide } from '@/services/trips'
import { officialVisitUrl } from '@/utils/providerContent'
const props = defineProps<{ tripId: string; version: number; dayDate: string; itemId: string; visitDate: string }>()
const guide = ref<OfficialAdmissionGuide | null>(null)
const loading = ref(false)
const error = ref('')
let generation = 0
onUnmounted(() => { generation++ })
const bookingUrl = computed(() => officialVisitUrl(guide.value?.booking_url || undefined))
const sources = computed(() => (guide.value?.sources || []).flatMap(source => {
  const url = officialVisitUrl(source.url)
  return url ? [{ title: source.title, url }] : []
}))
const windowLabel = computed(() => {
  if (guide.value?.status !== 'reviewed' || !guide.value.booking_opens_at) return ''
  const opens = Date.parse(guide.value.booking_opens_at)
  if (!Number.isFinite(opens)) return ''
  const when = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(opens)
  return opens > Date.now() ? `按常规规则推算：${when} 开始预约（北京时间），不代表有余票。` : '按常规规则已到预约开放时间，余票与预约结果尚未查询。'
})
async function load() {
  const request = ++generation
  guide.value = null; error.value = ''
  if (!props.tripId || !props.itemId || !/^\d{4}-\d{2}-\d{2}$/.test(props.visitDate)) { loading.value = false; return }
  loading.value = true
  try {
    const result = await readAdmissionGuide(props.tripId, props.version, props.dayDate, props.itemId, props.visitDate)
    if (request !== generation) return
    if (result.version !== props.version || result.item_id !== props.itemId || result.visit_date !== props.visitDate) throw new Error('行程或日期已变化，请重新读取官方须知')
    guide.value = result.guide
  } catch (e: any) { if (request === generation) error.value = e.message || '官方须知暂时无法读取' }
  finally { if (request === generation) loading.value = false }
}
watch(() => [props.tripId, props.version, props.dayDate, props.itemId, props.visitDate], load, { immediate: true })
</script>
<style scoped>
.official-guide { border: 1px solid var(--color-border); border-radius: 16px; padding: 17px 19px; margin-bottom: 25px; color: var(--color-text); }
header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 13px; }
header strong { font-size: 13px; font-weight: 600; }
header span { font-size: 10px; color: var(--color-muted); white-space: nowrap; }
dl { margin: 0; }
dl > div { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 13px; margin-top: 9px; font-size: 12px; line-height: 1.8; }
dt { color: var(--color-muted); }
dd { margin: 0; }
.guide-note, .guide-window { font-size: 11px; line-height: 1.8; color: var(--color-muted); margin: 12px 0 0; }
.guide-notice { font-size: 11px; line-height: 1.8; padding: 10px 12px; border-radius: 9px; background: var(--color-subtle); margin: 12px 0; }
.guide-details { margin-top: 12px; }
summary { display: flex; align-items: center; justify-content: space-between; cursor: pointer; list-style: none; font-size: 11px; color: var(--color-muted); }
summary::-webkit-details-marker { display: none; }
.guide-details[open] summary span { transform: rotate(45deg); }
.guide-actions { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 15px; padding-top: 13px; border-top: 1px solid var(--color-border); }
.guide-actions small { font-size: 10px; color: var(--color-muted); }
.guide-actions a { color: var(--travel-accent, #0071e3); font-size: 12px; font-weight: 500; text-decoration: none; }
.guide-sources { margin-top: 12px; }
.guide-sources summary, .guide-sources p, .guide-sources a { font-size: 10px; color: var(--color-muted); line-height: 1.8; }
.guide-sources a { display: inline-block; margin: 4px 12px 0 0; }
.text-button { background: none; border: 0; padding: 8px 0 0; color: var(--travel-accent, #0071e3); font-size: 11px; cursor: pointer; }
@media(max-width:560px) { .official-guide { padding: 15px; } header { flex-wrap: wrap; gap: 5px; } dl > div { grid-template-columns: 50px minmax(0,1fr); gap: 8px; font-size: 11px; } }
</style>
