<template>
  <section class="arrange" aria-label="拖拽调整当天景点">
    <header>
      <strong>调整顺序</strong>
      <span>拖动排序；拖到上方日期标签可移到那一天。锁定的景点保持位置，优化路线时也不移动。</span>
    </header>
    <ol>
      <li v-for="(place, index) in day.attractions" :key="place.item_id || index"
          :class="{ locked: place.locked || !!place.fixed_start_time || !!place.entry_window, dragging: dragIndex === index, over: overIndex === index && dragIndex !== index }"
          :draggable="!isFixed(place)"
          @dragstart="onDragStart($event, index)" @dragend="reset"
          @dragover.prevent="overIndex = index" @dragleave="overIndex === index && (overIndex = -1)"
          @drop.prevent="onDrop(index)">
        <span class="handle" aria-hidden="true">{{ isFixed(place) ? '📌' : '⋮⋮' }}</span>
        <span class="order">{{ index + 1 }}</span>
        <span class="name">{{ place.name }}<small v-if="place.entry_window"> · 入场 {{ place.entry_window.start }}—{{ place.entry_window.end }}</small><small v-else-if="place.fixed_start_time"> · 预约 {{ place.fixed_start_time }}</small></span>
        <span class="actions">
          <button type="button" :disabled="index === 0 || isFixed(place) || isFixed(day.attractions[index - 1])" :aria-label="`上移${place.name}`" @click="emit('reorder', index, index - 1)">↑</button>
          <button type="button" :disabled="index === day.attractions.length - 1 || isFixed(place) || isFixed(day.attractions[index + 1])" :aria-label="`下移${place.name}`" @click="emit('reorder', index, index + 1)">↓</button>
          <select v-if="dayCount > 1" :disabled="isFixed(place) || day.attractions.length < 2" :aria-label="`把${place.name}移到其他日期`" value="" @change="onMoveSelect($event, index)">
            <option value="" disabled>移到…</option>
            <option v-for="target in dayCount" :key="target" :value="target - 1" :disabled="target - 1 === dayIndex">第 {{ target }} 天</option>
          </select>
          <button type="button" class="lock" :aria-pressed="!!place.locked" :disabled="!!place.fixed_start_time || !!place.entry_window" @click="emit('toggle-lock', index)">{{ place.locked ? '已锁定' : '锁定' }}</button>
        </span>
      </li>
    </ol>
    <label class="auto-optimize"><input type="checkbox" :checked="autoOptimize" @change="emit('update:autoOptimize', ($event.target as HTMLInputElement).checked)" /> 保存后自动优化变动日期的路线（锁定项不动）</label>
  </section>
</template>

<script lang="ts">
/** Drag payload type shared with the day tabs, which accept cross-day drops. */
export const VISIT_MIME = 'application/x-trip-visit'
</script>

<script setup lang="ts">
import { ref } from 'vue'
import type { DayPlan, Attraction } from '@/types'

const props = defineProps<{ day: DayPlan; dayIndex: number; dayCount: number; autoOptimize: boolean }>()
const emit = defineEmits<{
  reorder: [from: number, to: number]
  move: [index: number, targetDay: number]
  'toggle-lock': [index: number]
  'update:autoOptimize': [value: boolean]
}>()
const dragIndex = ref(-1)
const overIndex = ref(-1)
const isFixed = (place?: Attraction) => !!place && (!!place.locked || !!place.fixed_start_time || !!place.entry_window)

function onDragStart(event: DragEvent, index: number) {
  dragIndex.value = index
  event.dataTransfer?.setData(VISIT_MIME, JSON.stringify({ day: props.dayIndex, index }))
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
function onDrop(target: number) {
  const from = dragIndex.value
  reset()
  if (from < 0 || from === target || isFixed(props.day.attractions[target])) return
  emit('reorder', from, target)
}
function onMoveSelect(event: Event, index: number) {
  const select = event.target as HTMLSelectElement
  const target = Number(select.value)
  select.value = ''
  if (!Number.isNaN(target)) emit('move', index, target)
}
function reset() { dragIndex.value = -1; overIndex.value = -1 }
</script>

<style scoped>
.arrange { margin: 18px 0; padding: 14px 16px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); }
header { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: baseline; font-size: 13px; }
header span { color: var(--color-muted); font-size: 12px; }
ol { list-style: none; padding: 0; margin: 12px 0 8px; display: grid; gap: 6px; }
li { display: grid; grid-template-columns: 22px 22px minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-subtle); cursor: grab; transition: border-color .15s, opacity .15s; }
li.locked { cursor: default; }
li.dragging { opacity: .45; }
li.over { border-color: var(--color-text); border-style: dashed; }
.handle { color: var(--color-muted); text-align: center; user-select: none; }
.order { font-variant-numeric: tabular-nums; color: var(--color-muted); font-size: 12px; text-align: center; }
.name { overflow-wrap: anywhere; font-size: 14px; }
.name small { color: var(--color-muted); }
.actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.actions button, .actions select { border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text); border-radius: 6px; padding: 3px 8px; font-size: 12px; cursor: pointer; }
.actions button:disabled, .actions select:disabled { opacity: .4; cursor: not-allowed; }
.actions .lock[aria-pressed="true"] { background: var(--color-text); color: var(--color-surface); }
.auto-optimize { display: flex; gap: 8px; align-items: center; color: var(--color-muted); font-size: 12px; }
@media (max-width: 640px) { li { grid-template-columns: minmax(0, 1fr); } .handle, .order { display: none; } .actions { justify-content: flex-start; } }
</style>
