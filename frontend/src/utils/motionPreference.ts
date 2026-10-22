import { computed, onBeforeUnmount, ref } from 'vue'

const read = () => { try { return localStorage.getItem('tripDayMotion') } catch { return null } }
const preference = ref(read())
const media = window.matchMedia('(prefers-reduced-motion: reduce)')
const reduced = ref(media.matches)
const enabled = computed(() => preference.value === 'on' || preference.value === 'true' || (!preference.value && !reduced.value))

/** Shared by the shell and day switcher; explicit user choice wins over OS default. */
export function useMotionPreference() {
  return { enabled, toggle: () => {
    preference.value = enabled.value ? 'off' : 'on'
    try { localStorage.setItem('tripDayMotion', preference.value) } catch { /* Keep this session usable. */ }
  } }
}

/** The application shell owns the listeners, rather than every page. */
export function syncMotionPreference() {
  const onMedia = (event: MediaQueryListEvent) => { reduced.value = event.matches }
  const onStorage = (event: StorageEvent) => { if (event.key === 'tripDayMotion' || event.key === null) preference.value = read() }
  media.addEventListener('change', onMedia)
  window.addEventListener('storage', onStorage)
  onBeforeUnmount(() => {
    media.removeEventListener('change', onMedia)
    window.removeEventListener('storage', onStorage)
  })
}
