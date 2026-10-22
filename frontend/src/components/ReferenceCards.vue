<template>
  <section v-if="sources.length" class="reference-cards" aria-label="攻略来源与地点">
    <div class="source-list">
      <article v-for="source in sources" :key="source.id" class="source-card">
        <div class="source-title"><strong>{{ source.title }}</strong><button type="button" :disabled="busy" @click="removeSource(source.id)">移除</button></div>
        <small>{{ source.type === 'screenshot' ? '截图识别' : source.type === 'url' ? '网页提取' : '粘贴正文' }} · {{ source.text.length }} 字</small>
        <details><summary>查看与修正原文</summary><textarea :value="source.text" :disabled="busy" rows="4" maxlength="15000" @change="editSource(source.id, ($event.target as HTMLTextAreaElement).value)" aria-label="攻略原文"></textarea></details>
      </article>
    </div>
    <div class="candidate-heading"><span>地点自动提取后默认可选，无需逐项填写。</span><button type="button" :disabled="busy || !city.trim()" @click="$emit('analyze')">{{ busy ? '正在提取并核实…' : '提取攻略地点' }}</button></div>
    <p v-if="!city.trim()" class="hint">先告诉我目的地城市，确认后会自动提取地点。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="candidate-list">
      <article v-for="candidate in candidates" :key="candidate.id" class="candidate-card">
        <strong>{{ candidate.place?.name || candidate.name }}</strong>
        <small>来源：{{ candidate.source_ids.map(id => sources.find(s => s.id === id)?.title || '攻略').join('、') }}</small>
        <p>{{ candidate.status === 'matched' ? '地图已匹配 · ' + candidate.place?.address : candidate.status === 'ambiguous' ? '存在候选地点，请确认分店或具体地点' : '暂无地图匹配，可保留为参考' }}</p>
        <select v-if="candidate.options.length" :value="candidate.place?.poi_id || ''" :disabled="busy" @change="selectPlace(candidate.id, ($event.target as HTMLSelectElement).value)" :aria-label="candidate.name + '地图匹配'">
          <option value="">选择具体地点</option><option v-for="place in candidate.options" :key="place.poi_id" :value="place.poi_id">{{ place.name }} · {{ place.address }}</option>
        </select>
        <div class="priorities"><button v-for="choice in choices" :key="choice.value" type="button" :disabled="busy" :aria-pressed="candidate.priority === choice.value" @click="priority(candidate.id, choice.value)">{{ choice.label }}</button></div>
      </article>
    </div>
    <p class="hint">地图匹配只核实地点；攻略中的价格、开放时间与预约规则仍需确认。</p>
  </section>
</template>
<script setup lang="ts">
import type { ReferenceSource, ReferenceCandidate } from '@/types'
const props = defineProps<{ sources: ReferenceSource[]; candidates: ReferenceCandidate[]; city: string; busy: boolean; error: string }>()
const emit = defineEmits<{ 'update:sources': [ReferenceSource[]]; 'update:candidates': [ReferenceCandidate[]]; analyze: [] }>()
const choices = [{ value: 'required' as const, label: '必去' }, { value: 'optional' as const, label: '可选' }, { value: 'ignored' as const, label: '忽略' }]
function removeSource(id: string) { emit('update:sources', props.sources.filter(s => s.id !== id)) }
function editSource(id: string, text: string) { if (text.trim()) emit('update:sources', props.sources.map(s => s.id === id ? { ...s, text } : s)) }
function priority(id: string, value: ReferenceCandidate['priority']) { emit('update:candidates', props.candidates.map(c => c.id === id ? { ...c, priority: value } : c)) }
function selectPlace(id: string, poiId: string) { emit('update:candidates', props.candidates.map(c => {
  if (c.id !== id) return c
  const place = c.options.find(p => p.poi_id === poiId) || null
  return { ...c, place, status: place ? 'matched' : 'ambiguous' }
})) }
</script>
<style scoped>
.reference-cards { margin-top: 18px; }
.source-list,.candidate-list { display:grid; grid-template-columns: repeat(auto-fit,minmax(230px,1fr)); gap:12px; }
.source-card,.candidate-card { border:1px solid var(--color-border); border-radius:12px; padding:14px; background:var(--color-surface); min-width:0; }
.source-title,.candidate-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.candidate-heading { margin:16px 0; flex-wrap:wrap; font-size:13px; }
small { display:block; color:var(--color-muted); margin:6px 0; }
p { font-size:12px; color:var(--color-muted); }
button,select,textarea { font:inherit; }
button { border:1px solid var(--color-border); border-radius:8px; background:var(--color-subtle); color:var(--color-text); padding:7px 10px; cursor:pointer; }
button:disabled { opacity:.5; cursor:default; }
.priorities { display:flex; gap:6px; margin-top:12px; }
.priorities button[aria-pressed=true] { color: var(--color-on-primary); background:var(--color-primary); }
select,textarea { width:100%; max-width:100%; padding:8px; border:1px solid var(--color-border); border-radius:6px; }
details { margin-top:10px; font-size:12px; }
summary { cursor:pointer; margin-bottom:8px; }
.error { color:var(--color-muted); }
</style>
