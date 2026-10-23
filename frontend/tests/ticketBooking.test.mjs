import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from '@vue/compiler-sfc'
import { reactive, ref, computed, watch, nextTick } from 'vue'
const script=parse(readFileSync(new URL('../src/components/TicketBookingPanel.vue',import.meta.url),'utf8')).descriptor.scriptSetup.content
const source=ts.createSourceFile('TicketBookingPanel.ts',script,ts.ScriptTarget.Latest,true)
const setup=source.statements.filter(node=>!ts.isImportDeclaration(node)&&!node.getText(source).startsWith('const props =')&&!node.getText(source).startsWith('const emit =')).map(node=>node.getText(source)).join('\n')
const code=ts.transpileModule(setup+'\n;globalThis.h={check,save,cancel,beginCancel,ticketName,participants,paid,confirmed,preview,error,working,refunded};',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
function harness(api={}) {
  const events=[],bodies=[]
  const props=reactive({tripId:'trip',version:1,itemId:'stop',dayDate:'2026-10-27',visitDate:'2026-10-27',bookings:[],disabled:false})
  const c={props,ref,computed,watch,onUnmounted:()=>{},emit:(...event)=>events.push(event),
    previewBooking:api.preview||(async(id,body)=>{bodies.push(body);return {version:1,item_id:'stop',day_date:'2026-10-27',before_total:180,after_total:210}}),
    saveBooking:api.save||(async()=>({id:'trip',version:2})),cancelBooking:api.cancel||(async()=>({id:'trip',version:2}))}
  vm.createContext(c);vm.runInContext(code,c)
  c.h.ticketName.value='成人票';c.h.participants.value=2;c.h.paid.value=150
  return {...c.h,props,events,bodies}
}
test('user confirmation is required and the aggregate paid amount is sent once',async()=>{
  const h=harness();await h.check();assert.equal(h.bodies.length,0)
  h.confirmed.value=true;await h.check()
  assert.equal(h.bodies[0].paid_total,150);assert.equal(h.bodies[0].participants,2)
  assert.equal(h.preview.value.after_total,210)
  await h.save();assert.equal(h.events.filter(event=>event[0]==='saved').length,1)
})
test('changing amount or version invalidates old previews and prevents stale saving',async()=>{
  let resolve
  const h=harness({preview:()=>new Promise(done=>resolve=done)})
  h.confirmed.value=true
  const pending=h.check();h.paid.value=120
  resolve({version:1,item_id:'stop',day_date:'2026-10-27'});await pending
  assert.equal(h.preview.value,null);await h.save();assert.equal(h.events.some(event=>event[0]==='saved'),false)
  h.props.version=2;await nextTick();assert.equal(h.confirmed.value,false)
})
test('record cancellation uses only the user supplied cumulative refund',async()=>{
  const calls=[]
  const h=harness({cancel:async(...args)=>{calls.push(args);return {id:'trip',version:2}}})
  h.beginCancel({id:'booking',paid_total:150,refunded_total:0,status:'recorded'})
  h.refunded.value=50;await h.cancel()
  assert.deepEqual(calls[0],['trip',1,'booking',50,false])
  assert.equal(h.events.filter(event=>event[0]==='saved').length,1)
})
test('pricing another date does not relabel the trip receipt, and excessive refunds or failures do not save',async()=>{
  const h=harness({save:async()=>{throw new Error('版本冲突')}})
  h.confirmed.value=true;h.props.visitDate='2026-10-28';await h.check();assert.equal(h.bodies[0].day_date,'2026-10-27')
  h.props.visitDate='2026-10-27';await h.check();await h.save();assert.equal(h.error.value,'版本冲突')
  h.beginCancel({id:'booking',paid_total:150,refunded_total:0,status:'recorded'});h.refunded.value=151;await h.cancel()
  assert.equal(h.events.some(event=>event[0]==='saved'),false)
})
