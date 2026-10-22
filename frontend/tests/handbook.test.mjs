import test from 'node:test'
import assert from 'node:assert/strict'
import { buildHandbookHtml } from '../src/utils/handbook.ts'

const fixture = () => ({ city: '测试城市', start_date: '2026-10-20', end_date: '2026-10-20', days: [{
  day_index: 0, date: '2026-10-20', transportation: '步行', buffer_minutes: 15, meals: [],
  attractions: [{item_id:'a',name:'展馆',location:{longitude:120,latitude:30}},{item_id:'b',name:'公园'}],
  schedule: { items: [{id:'a',kind:'attraction',name:'展馆',start_time:'09:00',end_time:'10:00',duration_minutes:60},
    {id:'b',kind:'attraction',name:'公园',start_time:'11:00',end_time:'12:00',duration_minutes:60}],
    window_start:'09:00', conflicts: [], segments:[{from_id:'a',to_id:'b',status:'verified',duration_minutes:20,mode:'walking'}] }
}] })

test('handbook includes known gaps beyond travel and buffer', () => {
  const plan=fixture(),before=JSON.stringify(plan),html=buildHandbookHtml(plan)
  assert.ok(html.includes('下一项安排前预留 25 分钟'))
  assert.equal(JSON.stringify(plan),before)
})

test('server adjustment explanations survive offline export and are escaped',()=>{
  const plan=fixture()
  plan.adjustment_notes=['第1天将远郊景点换为历史博物馆；原景点记为备选。<script>alert(1)</script>','第2天其他调整']
  const html=buildHandbookHtml(plan)
  assert.ok(html.includes('路线调整说明'))
  assert.ok(html.includes('原景点记为备选'))
  assert.ok(html.includes('&lt;script&gt;'))
  assert.ok(!html.includes('<script>'))
  assert.ok(!html.includes('第2天其他调整'))
})

test('offline hotel quote is scoped to a real lodging night and escaped',()=>{
  const plan=fixture();plan.end_date='2026-10-21'
  plan.days[0].hotel={name:'测试酒店',place_id:'hotel-a',nightly_quote:{night_date:'2026-10-20',hotel_place_id:'hotel-a',price_per_room:300,room_type:'双人间<script>',taxes_included:true,source_label:'合成测试'}}
  let html=buildHandbookHtml(plan)
  assert.ok(html.includes('¥300 / 间夜'));assert.ok(html.includes('双人间&lt;script&gt;'))
  plan.end_date=plan.start_date
  html=buildHandbookHtml(plan)
  assert.ok(!html.includes('¥300 / 间夜'))
})
test('unverified legacy values cannot masquerade as queried directions', () => {
  const plan=fixture(),edge=plan.days[0].schedule.segments[0]
  Object.assign(edge,{status:'unverified',duration_minutes:42,fare:99,distance_meters:999,
    legs:[{kind:'subway',line:'过期线路'},{kind:'subway',line:'过期换乘'}]})
  const html=buildHandbookHtml(plan)
  assert.ok(html.includes('耗时未获取'))
  for (const text of ['42 分钟','¥99','999 m','过期线路','过期换乘','下一项安排前预留']) assert.ok(!html.includes(text),text)
})
test('known zero travel is preserved while nonfinite duration is hidden', () => {
  const plan=fixture(),edge=plan.days[0].schedule.segments[0]
  edge.duration_minutes=0
  assert.ok(buildHandbookHtml(plan).includes('0 分钟'))
  edge.duration_minutes=NaN
  const html=buildHandbookHtml(plan)
  assert.ok(html.includes('耗时未获取'))
  assert.ok(!html.includes('NaN'))
})
test('place content is escaped and raw model overall advice is not exported', () => {
  const plan=fixture()
  plan.days[0].attractions[0].name='<script>danger</script>'
  plan.overall_suggestions='未经核实的原始整体建议'
  const html=buildHandbookHtml(plan)
  assert.ok(html.includes('&lt;script&gt;danger&lt;/script&gt;'))
  assert.ok(!html.includes('<script>danger'))
  assert.ok(!html.includes('未经核实的原始整体建议'))
})
