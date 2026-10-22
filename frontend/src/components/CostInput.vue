<template><label class="cost-input">{{ label }}（元，留空为未知）<a-input-number :value="amount" :min="0" @change="update" /><small>{{ place.cost_source || '填写后标为用户提供；请注明票价适用条件。' }}</small></label></template>
<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ place: any; field: string; label: string }>()
const amount = computed(() => props.place[props.field])
function update(value: number | string | null) { props.place[props.field] = value == null || value === '' ? null : Number(value); props.place.cost_status = props.place[props.field] == null ? 'unknown' : 'confirmed'; props.place.cost_source = props.place[props.field] == null ? '' : '用户填写，未独立核实' }
</script>
<style scoped>.cost-input{display:flex;flex-direction:column;gap:6px;margin:12px 0;font-size:13px}.cost-input small{color:#65756b}</style>
