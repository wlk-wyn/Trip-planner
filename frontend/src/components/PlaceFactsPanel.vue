<template>
  <section v-if="report" class="place-facts" aria-label="地点信息来源与时间">
    <h3>地点信息的来源与时间</h3>
    <p>{{ report.stale }} 项参考信息建议更新 · {{ report.unknown_age }} 项记录时间不明 · {{ report.missing }} 项暂无信息<span v-if="report.scope_mismatch"> · {{ report.scope_mismatch }} 项价格日期需更新</span></p>
    <details><summary>逐项查看开放时间与价格的记录</summary><article v-for="item in report.items" :key="`${item.date}:${item.item_id}`"><strong>{{ item.name }}</strong><small>{{ item.date }}</small><p>开放时间：{{ factDescription(item.hours) }}</p><p>价格：{{ factDescription(item.price) }} <a v-if="officialSourceUrl(item.price)" :href="officialSourceUrl(item.price)!" target="_blank" rel="noopener noreferrer">查看官方票价 ↗</a></p></article></details>
    <p class="note">查询时间表示取得参考信息的时间。官网票价按当前公示政策作规划参考，不代表预约余票或出行日最终价。地图营业信息达到7天、参考价格达到1天会提示更新。模型估算仍标为未核实，用户填写的价格不会被自动查询覆盖。</p>
    <button type="button" :disabled="disabled || loading" @click="$emit('refresh')">{{ loading ? '正在查询…' : '更新地图参考与补充价格' }}</button>
  </section>
</template>
<script setup lang="ts">
import type { FactReport } from '@/types'
import { factDescription, officialSourceUrl } from '@/utils/factDescription'
defineProps<{ report: FactReport | null; disabled: boolean; loading: boolean }>()
defineEmits<{ refresh: [] }>()
</script>
<style scoped>
.place-facts{padding-top:20px;margin-top:20px;border-top:1px solid var(--color-border)}h3{font-size:16px;font-weight:600;margin:0 0 12px}p{font-size:12px;line-height:1.8;color:var(--color-muted)}summary{cursor:pointer;font-size:13px;color:var(--color-text);padding:10px 0}article{padding:14px 0;border-bottom:1px solid var(--color-border)}article strong{font-size:13px;font-weight:500;display:block;overflow-wrap:anywhere}article small{color:var(--color-muted);font-size:11px}article p{margin:5px 0}button{border:1px solid var(--color-border);background:var(--color-surface);border-radius:20px;padding:8px 14px;color:var(--travel-accent);cursor:pointer;font-size:12px}button:disabled{cursor:default;opacity:.5}.note{max-width:700px}
</style>
