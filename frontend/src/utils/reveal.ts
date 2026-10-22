import type { ObjectDirective } from 'vue'

const observers = new WeakMap<HTMLElement, IntersectionObserver>()

/** Content stays visible if observers or motion are unavailable. Reveal only once. */
export const reveal: ObjectDirective<HTMLElement> = {
  mounted(element) {
    if (typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      if (document.documentElement.dataset.motion === 'on') {
        element.classList.add('travel-reveal')
      }
      observer.disconnect()
      observers.delete(element)
    }, { threshold: 0, rootMargin: '0px 0px -32px 0px' })
    observers.set(element, observer)
    observer.observe(element)
  },
  unmounted(element) {
    observers.get(element)?.disconnect()
    observers.delete(element)
  }
}
