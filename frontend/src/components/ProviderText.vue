<template>
  <span class="provider-text"><template v-for="(token, index) in tokens" :key="index"><a v-if="token.kind === 'link'" :href="token.url" target="_blank" rel="noopener noreferrer">{{ token.text.replace(/\*\*(.*?)\*\*/g, '$1') }} ↗</a><img v-else-if="token.kind === 'image'" :src="token.url" :alt="token.text || '美团商品图片'" loading="lazy" referrerpolicy="no-referrer" /><strong v-else-if="token.kind === 'strong'">{{ token.text }}</strong><template v-else>{{ token.text }}</template></template></span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { providerTokens } from '@/utils/providerContent'
const props = defineProps<{ content: string }>()
const tokens = computed(() => providerTokens(props.content))
</script>
<style scoped>
.provider-text { white-space: pre-wrap; overflow-wrap: anywhere; }
a { color: var(--travel-accent, #0071e3); text-decoration: underline; text-underline-offset: 3px; }
img { display: block; max-width: 100%; max-height: 280px; border-radius: 12px; margin: 12px 0; object-fit: contain; }
strong { font-weight: 600; }
</style>
