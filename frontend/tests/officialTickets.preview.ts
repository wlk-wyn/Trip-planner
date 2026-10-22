// Public, price-only replay output. No API calls, job creation or browser storage.
import { createApp, h, ref } from 'vue'
import TripCostPanel from '../src/components/TripCostPanel.vue'
import PlaceFactsPanel from '../src/components/PlaceFactsPanel.vue'
import data from './officialTickets.preview.data.json'
import '../src/styles/theme.css'
import '../src/styles/travel-design.css'
import '../src/styles/motion.css'
import type { CostReport, FactReport, TripFormData } from '../src/types'

document.documentElement.dataset.motion='off'
createApp({setup(){
  const active=ref(0)
  const names=['北京 · 官方淡季门票','西安 · 官方成人门票','游览日期变化 · 价格待更新']
  return ()=>{
    const row=data[active.value]
    return h('main',{class:'travel-redesign',style:'max-width:1000px;margin:32px auto;padding:0 24px;color:var(--color-text)'},[
      h('h1','官方票价参考'),h('p','公开评测快照验收：没有查询余票、创建任务或修改用户旅行。'),
      h('nav',{'aria-label':'验收案例',style:'display:flex;flex-wrap:wrap;gap:10px;margin:20px 0'},[
        ...names.map((name,index)=>h('button',{onClick:()=>active.value=index,'aria-pressed':active.value===index},name)),
        h('button',{onClick:()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark'}},'切换明暗')]),
      h(TripCostPanel,{key:row.id,tripId:'',version:1,request:row.request as TripFormData,
        report:row.cost_report as CostReport,disabled:true,enriching:false}),
      h(PlaceFactsPanel,{report:row.fact_report as FactReport,disabled:true,loading:false})])
  }
}}).mount('#app')
