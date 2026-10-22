<template>
  <a-config-provider :theme="theme" :locale="zhCN">
    <a-layout class="app-shell">
      <a-layout-header class="app-header">
        <div class="header-inner">
          <router-link to="/" class="brand" aria-label="旅途有方 · 返回首页">
            <svg class="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5"/><path d="m15.5 8.5-2 5-5 2 2-5 5-2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
            <span>旅途有方</span><span class="brand-caption">旅行助手</span>
          </router-link>
          <nav class="header-tools" aria-label="全局导航">
            <router-link to="/" class="new-trip">新旅行</router-link>
            <button type="button" class="motion-toggle" :aria-pressed="motionEnabled" :aria-label="motionEnabled ? '关闭页面动效' : '开启页面动效'" @click="toggleMotion">动效<span aria-hidden="true"> {{ motionEnabled ? '开' : '关' }}</span></button>
            <button type="button" class="theme-toggle" :aria-label="dark ? '切换浅色模式' : '切换暗色模式'" :aria-pressed="dark" @click="toggleTheme">
              <svg v-if="!dark" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M20.5 14A8.7 8.7 0 0 1 10 3.5 8.7 8.7 0 1 0 20.5 14Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.5"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
              <span>{{ dark ? '浅色' : '暗色' }}</span>
            </button>
          </nav>
        </div>
      </a-layout-header>
      <a-layout-content class="app-content">
        <router-view v-slot="{ Component, route }">
          <Transition name="page-change" :mode="motionEnabled ? 'out-in' : undefined" :css="motionEnabled" :duration="{ enter: motionEnabled ? 280 : 0, leave: motionEnabled ? 140 : 0 }">
            <component :is="Component" :key="route.path" />
          </Transition>
        </router-view>
      </a-layout-content>
      <a-layout-footer class="app-footer">旅途有方 · 从一个想法开始</a-layout-footer>
    </a-layout>
  </a-config-provider>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watchEffect } from 'vue'
import antTheme from 'ant-design-vue/es/theme'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import './styles/theme.css'
import './styles/travel-design.css'
import './styles/motion.css'
import { useMotionPreference, syncMotionPreference } from './utils/motionPreference'
const { enabled: motionEnabled, toggle: toggleMotion } = useMotionPreference()
syncMotionPreference()
const dark = ref(document.documentElement.dataset.theme === 'dark')
const theme = computed(() => ({
  algorithm: dark.value ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
  token: {
    colorPrimary: dark.value ? '#ededed' : '#202020',
    colorInfo: dark.value ? '#ededed' : '#202020',
    colorText: dark.value ? '#ededed' : '#202020',
    colorTextSecondary: dark.value ? '#a1a1a6' : '#727277',
    colorBgBase: dark.value ? '#171717' : '#ffffff',
    colorBgContainer: dark.value ? '#202020' : '#ffffff',
    colorBorder: dark.value ? '#383838' : '#e5e5e7',
    borderRadius: 10,
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif"
  }
}))
watchEffect(() => {
  document.documentElement.dataset.motion = motionEnabled.value ? 'on' : 'off'
  document.documentElement.dataset.theme = dark.value ? 'dark' : 'light'
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark.value ? '#171717' : '#ffffff')
})
function toggleTheme() { dark.value = !dark.value; try { localStorage.setItem('tripTheme', dark.value ? 'dark' : 'light') } catch { /* The current tab still switches when storage is unavailable. */ } }
function syncTheme(event: StorageEvent) { if (event.key === 'tripTheme') dark.value = event.newValue === 'dark' }
window.addEventListener('storage', syncTheme)
onBeforeUnmount(() => window.removeEventListener('storage', syncTheme))
</script>
