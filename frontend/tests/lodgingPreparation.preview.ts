// Synthetic fixtures only; no API, user trip or supplier request.
import {createApp,h,ref} from 'vue'
import TripPrepPanel from '../src/components/TripPrepPanel.vue'
import data from './lodgingPreparation.preview.data.json'
import type {TripPrep} from '../src/types'
import '../src/styles/theme.css'
import '../src/styles/travel-design.css'
createApp({setup(){
  const active=ref(0),mobile=ref(false)
  const names=['连续住宿','更换酒店','一日游']
  return ()=>h('main',{style:`max-width:${mobile.value?'390px':'1100px'};margin:32px auto;padding:0 16px;color:var(--color-text)`},[
    h('h1',{style:'font-size:24px'},'住宿准备验收'),
    h('p','合成样例，与用户行程分开；勾选不代表已经预订。'),
    h('nav',{style:'display:flex;gap:10px;flex-wrap:wrap;margin:20px 0'},[
      ...names.map((name,index)=>h('button',{onClick:()=>active.value=index,'aria-pressed':active.value===index},name)),
      h('button',{onClick:()=>mobile.value=!mobile.value},'切换窄屏'),
      h('button',{onClick:()=>document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark'},'切换明暗')]),
    h(TripPrepPanel,{prep:data[active.value] as TripPrep,tripId:'synthetic-lodging-preparation-20261019'})])
}}).mount('#app')
