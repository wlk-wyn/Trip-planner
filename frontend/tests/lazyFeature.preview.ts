// Synthetic loader failure, no API or browser storage changes.
import { createApp, defineComponent, h, ref } from 'vue'
import { lazyFeature } from '../src/utils/lazyFeature'
let calls=0
const feature=lazyFeature(async()=>{
  if (++calls===1) throw new Error('合成加载失败')
  return defineComponent({setup:()=>()=>h('p','详细设置已恢复')})
},'详细设置')
createApp({setup(){
  const note=ref('想要轻松一点的旅行')
  return()=>h('main',[h('h1','按需功能失败与重试'),h('label',['未提交的想法',h('input',{'aria-label':'未提交的想法',value:note.value,onInput:(e:Event)=>note.value=(e.target as HTMLInputElement).value})]),h(feature.value!)])
}}).mount('#app')
