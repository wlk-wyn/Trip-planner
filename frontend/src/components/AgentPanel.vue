<template>
  <section id="agent" class="agent-panel" :class="{ open }" aria-label="旅行助手">
    <header>
      <div><strong>旅行助手</strong><span>替换地点、调整节奏，或问问行程</span></div>
      <button type="button" class="close" aria-label="收起旅行助手" @click="emit('close')">×</button>
    </header>
    <div ref="scroller" class="messages" aria-live="polite">
      <p v-if="!messages.length" class="empty">告诉我想怎么改。我只会加入地图上核实过的地点，改动保存为可撤销的版本。</p>
      <div v-for="(item, index) in messages" :key="index" class="message" :class="item.role">
        <div class="bubble">{{ item.content }}</div>
        <small v-if="item.role === 'assistant' && item.actions?.length">已查询：{{ formatActions(item.actions) }}</small>
        <small v-if="item.role === 'assistant' && item.changed" class="updated">行程已更新</small>
      </div>
      <p v-if="loading" class="thinking"><span></span>正在判断需求并核实地点…</p>
    </div>
    <div v-if="!messages.length" class="prompts">
      <button v-for="prompt in prompts" :key="prompt" type="button" :disabled="disabled" @click="emit('send', prompt)">{{ prompt }}</button>
    </div>
    <form class="compose" @submit.prevent="emit('send')">
      <label class="sr-only" for="agent-input">对旅行助手说</label>
      <textarea id="agent-input" :value="modelValue" rows="2" maxlength="1500" :disabled="disabled"
        placeholder="例如：第二天少安排一个景点" @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
        @keydown.enter.exact.prevent="emit('send')"></textarea>
      <button type="submit" :disabled="disabled || !modelValue.trim()">{{ loading ? '调整中' : '发送' }}</button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatTurn } from '@/types'

type AgentMessage = ChatTurn & { actions?: string[]; changed?: boolean }
const props = defineProps<{
  messages: AgentMessage[]; loading: boolean; disabled: boolean; prompts: string[]
  formatActions: (actions: string[]) => string; modelValue: string; open: boolean
}>()
const emit = defineEmits<{ send: [preset?: string]; close: []; 'update:modelValue': [value: string] }>()
const scroller = ref<HTMLElement | null>(null)

async function scrollToEnd() {
  await nextTick()
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight, behavior: 'smooth' })
}
watch(() => [props.messages.length, props.loading], scrollToEnd)
defineExpose({ scrollToEnd })
</script>

<style scoped>
.agent-panel { flex: 1; min-height: 360px; display: flex; flex-direction: column; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-text); overflow: hidden; }
header { display: flex; justify-content: space-between; align-items: flex-start; padding: 14px 16px 10px; border-bottom: 1px solid var(--color-border); }
header div { display: flex; flex-direction: column; gap: 2px; }
header strong { font-size: 15px; font-weight: 600; }
header span { font-size: 12px; color: var(--color-muted); }
.close { display: block; border: 0; background: none; font-size: 22px; line-height: 1; color: var(--color-muted); cursor: pointer; }
.messages { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; min-height: 120px; }
.empty { color: var(--color-muted); font-size: 12px; line-height: 1.7; margin: 0; }
.message { display: flex; flex-direction: column; gap: 4px; max-width: 92%; }
.message.user { align-self: flex-end; align-items: flex-end; }
.bubble { padding: 9px 12px; border-radius: 12px; background: var(--color-subtle); font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.message.user .bubble { background: var(--color-primary); color: var(--color-on-primary); }
.message small { font-size: 11px; color: var(--color-muted); }
.message .updated { color: var(--color-success, var(--color-text)); }
.thinking { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--color-muted); margin: 0; }
.thinking span { width: 8px; height: 8px; border-radius: 50%; background: var(--color-text); animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .25; } }
.prompts { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 16px 10px; }
.prompts button { border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text); border-radius: 999px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.prompts button:disabled { opacity: .5; cursor: not-allowed; }
.compose { display: flex; gap: 8px; padding: 10px 12px 12px; border-top: 1px solid var(--color-border); }
.compose textarea { flex: 1; resize: none; border: 1px solid var(--color-border); border-radius: 10px; padding: 8px 10px; font: inherit; font-size: 13px; background: var(--color-surface); color: var(--color-text); }
.compose button { align-self: flex-end; border: 0; border-radius: 10px; padding: 8px 14px; background: var(--color-primary); color: var(--color-on-primary); cursor: pointer; }
.compose button:disabled { background: var(--color-subtle); color: var(--color-muted); cursor: not-allowed; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 1023px) {
  .agent-panel { position: fixed; left: 0; right: 0; bottom: 0; z-index: 40; height: min(78vh, 640px); border-radius: 16px 16px 0 0; box-shadow: 0 -8px 30px rgba(0, 0, 0, .18); transform: translateY(105%); transition: transform .28s ease; }
  .agent-panel.open { transform: none; }
  .close { display: block; }
}
@media (prefers-reduced-motion: reduce) { .agent-panel { transition: none; } }
</style>
