import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { resolve } from 'path'
import { bundleReport } from './scripts/bundle-report.mjs'
import { directAntComponents } from './scripts/ant-components.mjs'

const apiProxyTarget = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    bundleReport(),
    // Ant Design components are imported where templates use them, so each route ships only its own.
    Components({ dts: false, dirs: [], resolvers: [directAntComponents()] }),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  build: {
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        // Long-lived vendor chunks stay cached across app releases and load in parallel.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('/vue/') || id.includes('@vue') || id.includes('vue-router')) return 'vendor-vue'
          if (id.includes('@amap')) return 'vendor-amap'
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true
      },
      '/health': { target: apiProxyTarget, changeOrigin: true },
      '/ready': { target: apiProxyTarget, changeOrigin: true }
    }
  }
})

