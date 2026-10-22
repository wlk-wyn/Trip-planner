import test from 'node:test'
import assert from 'node:assert/strict'
import { isGatePoint, navigationTarget, visitGateLabel, dayGatePoints, officialGateUrl, endpointCoordinates } from '../src/utils/routingPoints.ts'
import { buildHandbookHtml, buildIcs } from '../src/utils/handbook.ts'

const gate = role => ({basis:'reviewed_gate',role,venue_poi_id:'B000A8UIN8',
  policy_source_url:'https://www.dpm.org.cn/Visit.html',policy_reviewed_on:'2026-10-18',
  name:role==='entrance'?'故宫博物院-午门':'故宫博物院-神武门',
  poi_id:role==='entrance'?'B000A84GDN':'B000A9PISW',
  location:role==='entrance'?{longitude:116.397228,latitude:39.913582}:{longitude:116.396786,latitude:39.922305}})
const fixture = () => ({city:'北京',start_date:'2026-10-20',end_date:'2026-10-20',days:[{
  day_index:0,date:'2026-10-20',transportation:'步行',meals:[],
  attractions:[{item_id:'a',poi_id:'B000A8UIN8',name:'故宫博物院',location:{longitude:116.397029,latitude:39.917839}}],
  schedule:{conflicts:[],items:[{id:'a',kind:'attraction',name:'故宫博物院',start_time:'10:00',end_time:'12:00',duration_minutes:120}],
    segments:[{from_id:'before',to_id:'a',status:'verified',mode:'walking',duration_minutes:15,to_endpoint:gate('entrance')},
      {from_id:'a',to_id:'after',status:'verified',mode:'walking',duration_minutes:15,from_endpoint:gate('exit')}]}
}]})

test('navigation and exports use the arrival gate, never the centre or exit',()=>{
  const plan=fixture(),before=JSON.stringify(plan),day=plan.days[0]
  const target=navigationTarget(day,'a',day.attractions[0])
  assert.deepEqual(target,{...gate('entrance').location,name:'故宫博物院-午门',gate:true})
  assert.equal(visitGateLabel(day,'a'),'午门进 · 神武门出')
  assert.equal(dayGatePoints(day).length,2)
  const html=buildHandbookHtml(plan),ics=buildIcs(plan).ics.replace(/\r\n /g,'')
  for(const output of [html,ics]) {
    assert.ok(output.includes('116.397228'))
    assert.ok(output.includes('39.913582'))
    assert.ok(output.includes('导航到入口'))
    assert.ok(output.includes('午门进 · 神武门出'))
    assert.ok(!output.includes('116.397029'))
  }
  assert.ok(!ics.includes('GEO:'))
  assert.equal(JSON.stringify(plan),before)
})

test('missing or unverified entry cannot turn into centre navigation',()=>{
  const plan=fixture(),day=plan.days[0]
  for(const edge of [{...day.schedule.segments[0],status:'unverified'},{...day.schedule.segments[0],to_endpoint:null}]) {
    day.schedule.segments=[edge]
    assert.equal(navigationTarget(day,'a',day.attractions[0]),null)
    assert.ok(!buildHandbookHtml(plan).includes('class="nav"'))
    assert.ok(buildHandbookHtml(plan).includes('入口交通尚未确认'))
    assert.ok(!buildIcs(plan).ics.includes('uri.amap.com'))
  }
})

test('wrong gate identity, area and arbitrary policy URLs do not become evidence',()=>{
  for(const change of [{name:'恶意门址<script>'},{poi_id:'unknown'},{policy_source_url:'https://example.com'},
    {location:{longitude:120,latitude:30}},{role:'exit'}]) {
    const point={...gate('entrance'),...change}
    assert.equal(isGatePoint(point),false)
    assert.equal(officialGateUrl({to_endpoint:point}),null)
    assert.equal(endpointCoordinates(point),null)
  }
})

test('gate markers deduplicate and ignore places absent from the day',()=>{
  const day=fixture().days[0]
  day.schedule.segments.push(day.schedule.segments[0])
  assert.equal(dayGatePoints(day).length,2)
  day.attractions=[]
  assert.deepEqual(dayGatePoints(day),[])
})

test('reversed roles and a gate attached to a different stop cannot become entrance navigation',()=>{
  const day=fixture().days[0]
  day.schedule.segments[0].to_endpoint=gate('exit')
  day.schedule.segments[1].from_endpoint=gate('entrance')
  assert.equal(navigationTarget(day,'a',day.attractions[0]),null)
  assert.equal(visitGateLabel(day,'a'),'')
  assert.deepEqual(dayGatePoints(day),[])
  day.schedule.segments[0].to_endpoint=gate('entrance')
  day.schedule.segments[0].to_id='other'
  assert.equal(navigationTarget(day,'other',{name:'其他景点'}),null)
})

test('ordinary places keep valid coordinate navigation and reject invalid coordinates',()=>{
  const day=fixture().days[0]
  assert.equal(navigationTarget(day,'other',{name:'普通展馆',location:{longitude:120,latitude:30}}).gate,false)
  for(const location of [{longitude:NaN,latitude:30},{longitude:200,latitude:30},{longitude:0,latitude:0}]) {
    assert.equal(navigationTarget(day,'other',{location}),null)
  }
})

test('route failure notes remain readable and escaped in offline handbook',()=>{
  const plan=fixture()
  plan.days[0].schedule.segments[0].note='停车及步行尚未校验<script>'
  const html=buildHandbookHtml(plan)
  assert.ok(html.includes('停车及步行尚未校验&lt;script&gt;'))
  assert.ok(!html.includes('<script>'))
})
