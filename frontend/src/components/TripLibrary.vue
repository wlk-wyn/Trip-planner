<template>
  <section id="my-trips" v-reveal class="trip-library">
    <div class="library-heading"><h2>我的旅行</h2><button type="button" @click="load" :disabled="loading">{{ loading ? '读取中…' : '刷新' }}</button></div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else-if="!trips.length">生成后自动保存到本地服务，下次可以从这里继续。</p>
    <div class="trip-list"><button v-for="trip in trips" :key="trip.id" type="button" :disabled="loading" @click="open(trip.id)"><strong>{{ trip.city }}</strong><span>{{ trip.start_date }} — {{ trip.end_date }}</span><small>版本 {{ trip.version }} · 继续规划 ↗</small></button></div>
    <small>已保存的旅行，随时继续。</small>
  </section>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listTrips, readTrip, selectSavedTrip } from '@/services/trips'
import type { SavedTripSummary } from '@/types'
const router = useRouter()
const emit = defineEmits<{ loaded: [trips: SavedTripSummary[]] }>()
const trips = ref<SavedTripSummary[]>([])
const loading = ref(false)
const error = ref('')
async function load() { loading.value = true; error.value = ''; try { trips.value = await listTrips(); emit('loaded', trips.value) } catch(e: any) { error.value = e.message } finally { loading.value = false } }
async function open(id: string) { loading.value = true; error.value = ''; try { const trip = await readTrip(id); selectSavedTrip(trip); await router.push({ path: '/result', query: { trip: id } }) } catch(e: any) { error.value = e.message } finally { loading.value = false } }
onMounted(load)
defineExpose({ open })
</script>
<style scoped>
.trip-library { max-width:1100px; margin:24px auto 0; border:1px solid var(--color-border); background:var(--color-surface); border-radius:20px; padding:22px; position:relative; }
.library-heading { display:flex; justify-content:space-between; align-items:center; }
h2 { font-size:18px; margin:0; color:var(--color-text); }
p,small { color:var(--color-muted); font-size:12px; }
.trip-list { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin:16px 0; }
button { font:inherit; cursor:pointer; border:1px solid var(--color-border); border-radius:10px; background:var(--color-subtle); color:var(--color-text); padding:10px; }
.trip-list button { text-align:left; display:grid; gap:7px; }
span { font-size:12px; }
</style>
