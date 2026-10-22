<template>
  <details v-if="rows.length" class="quote-editor">
    <summary>补充入住日期报价 · {{ rows.length }} 晚</summary>
    <p class="quote-note">填写你查到的每间每晚价格，并注明房型。保存会生成一个可撤销版本；这是用户提供的参考，不代表已预订或实时可订。</p>
    <form @submit.prevent="save">
      <fieldset v-for="row in rows" :key="row.night_date" :disabled="disabled || saving">
        <legend>{{ row.night_date }} 入住 · {{ row.name }}</legend>
        <div class="quote-fields">
          <label>每间每晚（元）<input v-model="row.price" :aria-label="`${row.night_date} 每间每晚报价`" type="number" min="0" max="1000000" step="0.01" placeholder="留空为未知" @input="row.changed=true" /></label>
          <label>房型<input v-model="row.room_type" :aria-label="`${row.night_date} 报价房型`" maxlength="80" placeholder="例如：标准双人间" @input="row.changed=true" /></label>
          <label class="quote-source">报价来源备注<input v-model="row.source_label" :aria-label="`${row.night_date} 报价来源备注`" maxlength="120" placeholder="可选，例如平台名称或酒店电话" @input="row.changed=true" /></label>
        </div>
        <label class="quote-tax"><input type="checkbox" :checked="row.taxes_included===true" @change="row.taxes_included=($event.target as HTMLInputElement).checked?true:null;row.changed=true" />已确认该房价含税费</label>
        <small>未勾选时税费仍待确认；清空原报价并保存可撤销这晚的报价。</small>
      </fieldset>
      <p v-if="error" role="alert" class="quote-error">{{ error }}</p>
      <button type="submit" :disabled="disabled || saving || !rows.some(row=>row.changed)">{{ saving?'正在保存…':'保存报价并重新测算' }}</button>
    </form>
  </details>
</template>
<script setup lang="ts">
import {ref,watch,onBeforeUnmount} from 'vue'
import type {TripPlan,HotelQuoteInput,SavedTrip} from '@/types'
import {saveHotelQuotes} from '@/services/trips'
import {applicableHotelQuote} from '@/utils/hotelQuote'
const props=defineProps<{tripId:string;version:number;plan:TripPlan;disabled:boolean}>()
const emit=defineEmits<{saved:[trip:SavedTrip]}>()
type Row={night_date:string;hotel_item_id:string;name:string;price:string;room_type:string;source_label:string;taxes_included:boolean|null;changed:boolean}
const rows=ref<Row[]>([]),saving=ref(false),error=ref('')
let generation=0
watch(()=>[props.tripId,props.version],()=>{
  generation++;saving.value=false;error.value=''
  rows.value=props.plan.days.filter(day=>day.date<props.plan.end_date && day.hotel?.item_id).map(day=>{
    const hotel=day.hotel!,q=applicableHotelQuote(hotel,day.date)
    return {night_date:day.date,hotel_item_id:hotel.item_id!,name:hotel.name,price:q?String(q.price_per_room):'',room_type:q?.room_type||'',source_label:q?.source_label||'',taxes_included:q?.taxes_included??null,changed:false}
  })
},{immediate:true})
onBeforeUnmount(()=>generation++)
async function save(){
  if(saving.value||props.disabled)return
  const changed=rows.value.filter(row=>row.changed)
  if(!changed.length)return
  const quotes:HotelQuoteInput[]=[]
  for(const row of changed){
    const price=row.price===''?null:Number(row.price)
    if(price!=null && (!Number.isFinite(price)||price<0||price>1000000)){error.value='请填写有效的每间每晚价格';return}
    if(price!=null && !row.room_type.trim()){error.value=`请填写 ${row.night_date} 的报价房型`;return}
    quotes.push({night_date:row.night_date,hotel_item_id:row.hotel_item_id,price_per_room:price,room_type:row.room_type.trim(),source_label:row.source_label.trim(),taxes_included:row.taxes_included})
  }
  const current=++generation,id=props.tripId,version=props.version
  saving.value=true;error.value=''
  try{
    const saved=await saveHotelQuotes(id,version,quotes)
    if(current!==generation||props.tripId!==id||props.version!==version)return
    emit('saved',saved)
  }catch(e:any){if(current===generation)error.value=e.message||'报价保存失败，请稍后再试'}
  finally{if(current===generation)saving.value=false}
}
</script>
<style scoped>
.quote-editor{border-top:1px solid var(--color-border);padding:16px 0;color:var(--color-text)}summary{font-size:13px;cursor:pointer}.quote-note,small{font-size:12px;line-height:1.8;color:var(--color-muted)}fieldset{border:1px solid var(--color-border);border-radius:12px;margin:18px 0;padding:16px}legend{font-size:12px;max-width:100%;white-space:normal;overflow-wrap:anywhere}.quote-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}.quote-source{grid-column:1/-1}label{display:flex;flex-direction:column;gap:7px;font-size:12px}input:not([type=checkbox]){box-sizing:border-box;width:100%;min-width:0;border:1px solid var(--color-border);border-radius:8px;padding:9px 10px;background:var(--color-surface);color:var(--color-text);font:inherit}.quote-tax{display:flex;flex-direction:row;align-items:center;margin:14px 0 6px}small{display:block}button{border:1px solid var(--color-border);border-radius:20px;padding:9px 16px;background:var(--color-subtle);color:var(--color-text);font:inherit;font-size:12px;cursor:pointer}button:disabled{opacity:.5;cursor:default}.quote-error{font-size:12px;color:var(--color-warning)}input:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--travel-accent);outline-offset:3px}@media(max-width:640px){.quote-fields{grid-template-columns:1fr}fieldset{padding:12px}}
</style>
