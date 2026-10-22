import { defineAsyncComponent, defineComponent, h, shallowRef, type Component } from 'vue'

/** Feature loading can fail independently of the form; retry preserves its data. */
export function lazyFeature(loader: () => Promise<Component>, label: string) {
  const feature = shallowRef<Component>()
  function create(): Component {
    return defineAsyncComponent({
      loader, delay: 150,
      loadingComponent: defineComponent({inheritAttrs:false, setup: () => () => h('p', {role:'status'}, `正在打开${label}…`)}),
      errorComponent: defineComponent({inheritAttrs:false, setup: () => () => h('div', {role:'alert',class:'feature-load-error'}, [
        h('p', `${label}暂时无法打开，已填写内容仍保留。`),
        h('button', {type:'button',onClick:() => { feature.value=create() }}, '重试'),
      ])}),
    })
  }
  feature.value=create()
  return feature
}
