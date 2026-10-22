<template>
  <section class="result-status" :class="{ attention: items.length }" aria-label="行程状态">
    <p class="summary">
      <strong>{{ items.length ? `${items.length} 项需要处理` : '时间与路线已校验' }}</strong>
      <span>{{ summary }}</span>
    </p>
    <details v-if="items.length" class="status-details"><summary>查看需要处理的事项</summary><ul>
      <li v-for="item in items" :key="item.text">
        <span>{{ item.text }}</span>
        <span v-if="item.action === 'fix' && item.day != null" class="actions"><button type="button" :disabled="busy" @click="emit('fix', item.day)">给出调整方案</button><button type="button" @click="emit('goto', item.day)">去第 {{ item.day + 1 }} 天</button></span>
        <button v-else-if="item.action === 'day' && item.day != null" type="button" @click="emit('goto', item.day)">去第 {{ item.day + 1 }} 天</button>
        <button v-else-if="item.action === 'recheck'" type="button" :disabled="busy" @click="emit('recheck')">重新校验</button>
      </li>
    </ul></details>
    <details v-if="hints.length" class="hints"><summary>{{ hints.length }} 条小提示</summary><ul><li v-for="hint in hints" :key="hint">{{ hint }}</li></ul></details>
  </section>
</template>

<script setup lang="ts">
import type { StatusItem } from '@/types'
defineProps<{ items: StatusItem[]; hints: string[]; summary: string; busy: boolean }>()
const emit = defineEmits<{ goto: [day: number]; fix: [day: number]; recheck: [] }>()
</script>

<style scoped>
.result-status { margin: 0 0 16px; padding: 10px 14px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); font-size: 13px; }
.result-status.attention { border-color: var(--color-warning); background: var(--color-warning-bg); }
.summary { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; margin: 0; }
.summary strong { font-weight: 600; }
.attention .summary strong { color: var(--color-warning); }
.summary span { color: var(--color-muted); font-size: 12px; }
ul { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 6px; }
li { display: flex; justify-content: space-between; gap: 12px; align-items: center; line-height: 1.6; }
li button { flex: none; border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text); border-radius: 6px; padding: 2px 10px; font-size: 12px; cursor: pointer; }
li button:disabled { opacity: .5; cursor: not-allowed; }
.actions { display: flex; gap: 6px; flex: none; }
.hints { margin-top: 6px; color: var(--color-muted); font-size: 12px; }
.hints summary { cursor: pointer; }
.hints ul { margin-top: 4px; gap: 2px; }
.status-details { margin-top: 6px; }
.status-details>summary { color: var(--color-muted); font-size: 12px; cursor: pointer; }
@media(max-width:640px){li { align-items: flex-start; flex-direction: column; gap: 6px; }.actions { flex-wrap: wrap; }}
</style>
