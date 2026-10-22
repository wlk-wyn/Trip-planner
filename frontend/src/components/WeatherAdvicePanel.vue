<template>
  <section class="weather-panel" aria-live="polite">
    <header>
      <strong>天气与雨天备选</strong>
      <button type="button" class="close" aria-label="关闭天气建议" @click="emit('close')">×</button>
    </header>
    <p class="note">{{ advice.note }}</p>
    <p v-if="sourceEvidence" class="note">高德短期预报 · 发布 {{ sourceEvidence.reportTime }} · 查询 {{ sourceEvidence.queryTime }}（北京时间）</p>
    <ul class="forecast">
      <li v-for="day in advice.days" :key="day.date" :class="{ wet: day.wet }">
        <strong>第 {{ day.day_index + 1 }} 天</strong>
        <span v-if="forecastDisplay(day.weather)">{{ forecastDisplay(day.weather)?.text }}</span>
        <span v-else-if="day.weather" class="muted">预报更新时间不明或已过期，请刷新核实</span>
        <span v-else class="muted">暂无预报</span>
      </li>
    </ul>
    <p v-if="advice.forecast_available && !suggestionCount" class="muted">预报期内没有需要调整的户外安排。</p>
    <template v-for="day in advice.days" :key="`s-${day.date}`">
      <article v-for="item in day.suggestions" :key="item.item_id" class="suggestion">
        <p><strong>第 {{ day.day_index + 1 }} 天 · {{ item.name }}</strong><span>{{ item.reason }}。可换成附近的室内地点：</span></p>
        <p v-if="item.error" class="muted">{{ item.error }}</p>
        <p v-else-if="!item.candidates.length" class="muted">附近 5 公里内没有找到合适的室内地点，可保留原安排或携带雨具。</p>
        <div v-else class="candidates">
          <button v-for="candidate in item.candidates" :key="candidate.poi_id || candidate.name" type="button" :disabled="disabled || applied.has(item.item_id)" @click="apply(day.day_index, item.item_id, candidate)">
            <strong>{{ candidate.name }}</strong><small>{{ candidate.reason }}</small>
          </button>
        </div>
        <small v-if="applied.has(item.item_id)" class="muted">已放入编辑草稿，保存后生效。</small>
      </article>
    </template>
  </section>
</template>

<script setup lang="ts">
import { forecastDisplay, forecastEvidence } from '@/utils/forecast'
import { computed, ref } from 'vue'
import type { WeatherAdvice, WeatherCandidate } from '@/types'

const props = defineProps<{ advice: WeatherAdvice; disabled: boolean }>()
const emit = defineEmits<{ apply: [dayIndex: number, itemId: string, candidate: WeatherCandidate]; close: [] }>()
const applied = ref(new Set<string>())
const suggestionCount = computed(() => props.advice.days.reduce((total, day) => total + day.suggestions.length, 0))
const sourceEvidence=computed(()=>{
  for(const day of props.advice.days){const evidence=forecastEvidence(day.weather);if(evidence)return evidence}
  return null
})

function apply(dayIndex: number, itemId: string, candidate: WeatherCandidate) {
  applied.value = new Set([...applied.value, itemId])
  emit('apply', dayIndex, itemId, candidate)
}
</script>

<style scoped>
.weather-panel { margin: 0 auto 18px; max-width: 1200px; padding: 16px 18px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-text); font-size: 13px; }
header { display: flex; justify-content: space-between; align-items: center; }
.close { border: 0; background: none; color: var(--color-muted); font-size: 20px; cursor: pointer; line-height: 1; }
.note, .muted { color: var(--color-muted); font-size: 12px; line-height: 1.7; }
.forecast { list-style: none; padding: 0; margin: 10px 0; display: flex; flex-wrap: wrap; gap: 8px; }
.forecast li { display: flex; gap: 8px; padding: 6px 10px; border-radius: 8px; background: var(--color-subtle); }
.forecast li.wet { background: var(--color-warning-bg); color: var(--color-warning); }
.suggestion { border-top: 1px solid var(--color-border); padding-top: 12px; margin-top: 12px; }
.suggestion p { margin: 0 0 8px; display: flex; flex-wrap: wrap; gap: 4px 10px; }
.suggestion p span { color: var(--color-muted); }
.candidates { display: flex; flex-wrap: wrap; gap: 8px; }
.candidates button { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; max-width: 280px; padding: 8px 12px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-subtle); color: var(--color-text); text-align: left; cursor: pointer; }
.candidates button:hover:not(:disabled) { border-color: var(--color-text); }
.candidates button:disabled { opacity: .5; cursor: not-allowed; }
.candidates small { color: var(--color-muted); font-size: 11px; }
</style>
