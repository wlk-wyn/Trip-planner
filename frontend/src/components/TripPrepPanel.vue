<template>
  <section class="prep-panel" aria-label="出行准备">
    <header>
      <strong>出行准备</strong>
      <span class="progress">已勾选 {{ doneCount }} / {{ totalCount }}</span>
      <button type="button" class="close" aria-label="关闭出行准备" @click="emit('close')">×</button>
    </header>

    <div class="columns">
      <div>
        <h3>预约与入内</h3>
        <p v-if="!sortedReminders.length" class="muted">当前行程没有识别到常需预约的场馆；如有演出或特殊场馆请自行确认。</p>
        <div v-for="item in sortedReminders" :key="item.id" class="reminder">
          <label class="check">
            <input type="checkbox" :aria-label="`我已核对${item.name}的参观安排`" :checked="checked.has(key('r', item.id))" @change="toggle(key('r', item.id))" />
            <span><strong>{{ item.name }}</strong> · 第 {{ item.day_index + 1 }} 天<br />
              <small>{{ item.message }}</small>
              <em v-if="bookingWindowLabel(item)">{{ bookingWindowLabel(item) }}</em>
            </span>
          </label>
          <p v-if="item.policy_source_url" class="policy-source">
            <a :href="item.policy_source_url" target="_blank" rel="noopener noreferrer">官方参观须知 ↗</a>
            <span> · 规则复核 {{ item.policy_reviewed_on }}{{ item.policy_status === 'expired' ? ' · 参考已过期，请重新核对' : ' · 非实时余票' }}</span>
          </p>
        </div>

        <h3>住宿</h3>
        <p class="muted">{{ prep.lodging_note || '住宿日期和预订情况请核对以下安排。' }}</p>
        <label v-for="stay in prep.stays" :key="stayPreparationKey(stay)" class="check">
          <input type="checkbox" :aria-label="`我已核对${stay.name || '待选择酒店'}的入住和离店安排`" :disabled="!stay.name" :checked="checked.has(stayPreparationKey(stay))" @change="toggle(stayPreparationKey(stay))" />
          <span v-if="stay.name"><strong>{{ stay.name }}</strong> · {{ shortDate(stay.check_in) }} 入住，{{ shortDate(stay.check_out) }} 离店 · {{ stay.nights }} 晚<br /><small>{{ stay.address }}</small></span>
          <span v-else class="muted">{{ shortDate(stay.check_in) }} 起 {{ stay.nights }} 晚尚未安排住宿</span>
        </label>
      </div>
      <div>
        <h3>打包清单</h3>
        <label v-for="item in prep.packing" :key="`p-${item.item}`" class="check">
          <input type="checkbox" :checked="checked.has(key('p', item.item))" @change="toggle(key('p', item.item))" />
          <span>{{ item.item }} <small>· {{ item.reason }}</small></span>
        </label>
        <p class="muted">{{ prep.weather_note }}</p>
      </div>
    </div>
    <p class="muted">{{ prep.note }} 勾选仅为个人核对记录，只保存在当前浏览器，不代表平台已验证预订。</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TripPrep } from '@/types'
import { bookingWindowLabel, checkedPreparation, stayPreparationKey } from '@/utils/visitPreparation'

const props = defineProps<{ prep: TripPrep; tripId: string }>()
const emit = defineEmits<{ close: [] }>()
const storageKey = computed(() => `tripPrepChecked:v2:${props.tripId}`)
const checked = ref(new Set<string>(read()))

function read(): string[] {
  try { return checkedPreparation(localStorage.getItem(`tripPrepChecked:v2:${props.tripId}`)) } catch { return [] }
}
watch(storageKey, () => { checked.value = new Set(read()) })
const key = (...parts: string[]) => parts.join('|')
function toggle(id: string) {
  const next = new Set(checked.value)
  next.has(id) ? next.delete(id) : next.add(id)
  checked.value = next
  try { localStorage.setItem(storageKey.value, JSON.stringify([...next])) } catch { /* per-browser convenience only */ }
}
const sortedReminders = computed(() => [...props.prep.reminders].sort((a, b) => a.date.localeCompare(b.date)))
const ids = computed(() => [
  ...props.prep.reminders.map(r => key('r', r.id)),
  ...props.prep.stays.filter(s => s.name).map(stayPreparationKey),
  ...props.prep.packing.map(p => key('p', p.item)),
])
const totalCount = computed(() => ids.value.length)
const doneCount = computed(() => ids.value.filter(id => checked.value.has(id)).length)
const shortDate = (value: string) => { const [, m, d] = value.split('-'); return `${Number(m)}月${Number(d)}日` }
</script>

<style scoped>
.prep-panel { margin: 0 auto 18px; max-width: 1200px; padding: 16px 18px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-text); font-size: 13px; }
header { display: flex; align-items: center; gap: 12px; }
.progress { color: var(--color-muted); font-size: 12px; margin-right: auto; }
.close { border: 0; background: none; color: var(--color-muted); font-size: 20px; cursor: pointer; line-height: 1; }
.columns { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 8px 32px; }
h3 { font-size: 13px; font-weight: 600; margin: 16px 0 8px; }
.check { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; line-height: 1.6; cursor: pointer; }
.check input { margin-top: 4px; }
.policy-source { padding-left: 24px; margin: 0 0 10px; color: var(--color-muted); font-size: 12px; overflow-wrap: anywhere; }
.policy-source a { color: var(--color-primary, #0071e3); }
.check em { display: block; font-style: normal; color: var(--color-warning); font-size: 12px; margin-left: 4px; }
.check small, .muted { color: var(--color-muted); font-size: 12px; line-height: 1.7; }
@media (max-width: 720px) { .columns { grid-template-columns: minmax(0, 1fr); } }
</style>
