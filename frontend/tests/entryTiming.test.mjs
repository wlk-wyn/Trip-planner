import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from '@vue/compiler-sfc'
import { reactive, ref, computed, watch } from 'vue'

const script = parse(readFileSync(new URL('../src/components/EntryTimingPanel.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
// Run the component's actual reactive setup, replacing only Vue compile macros/imports.
const source = ts.createSourceFile('EntryTimingPanel.ts', script, ts.ScriptTarget.Latest, true)
const setup = source.statements.filter(node => !ts.isImportDeclaration(node) && !node.getText(source).startsWith('const props =') && !node.getText(source).startsWith('const emit =')).map(node => node.getText(source)).join('\n')
const code = ts.transpileModule(setup + '\n;globalThis.h = {check,save,preview,mode,start,end,working,error,earlier,valid,selected,findOptions,adopt,options,allowCrossDay};', {compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
const result = (version=1) => ({version,item_id:'stop',day:{date:'2026-10-27',schedule:{status:'feasible',items:[],warnings:[],conflicts:[]}},earlier_day:null})
function harness(api={}) {
  const events=[]
  const props=reactive({tripId:'trip',version:1,dayDate:'2026-10-27',itemId:'stop',visitDate:'2026-10-27',disabled:false})
  const c={props,ref,computed,watch,onUnmounted:()=>{},emit:(...event)=>events.push(event),previewEntry:api.preview||(async()=>result()),saveEntryTiming:api.save||(async()=>({id:'trip',version:2})),queryEntryOptions:api.options,applyEntryOption:api.adopt}
  vm.createContext(c);vm.runInContext(code,c)
  c.h.start.value='09:00';c.h.end.value='12:00'
  return {...c.h,props,events}
}

test('editing input or changing version invalidates an in-flight preview',async()=>{
  let resolve
  const h=harness({preview:()=>new Promise(done=>resolve=done)})
  const first=h.check();h.end.value='11:00';resolve(result());await first
  assert.equal(h.preview.value,null)
  const second=h.check();h.props.version=2;resolve(result());await second
  assert.equal(h.preview.value,null);assert.equal(h.working.value,false)
})

test('different ticket date and invalid window cannot preview or save',async()=>{
  let calls=0
  const h=harness({preview:async()=>{calls++;return result()},save:async()=>{calls++}})
  h.end.value='08:00';await h.check();assert.equal(calls,0)
  h.end.value='12:00';h.props.visitDate='2026-10-28';await h.check();await h.save();assert.equal(calls,0)
})

test('saving requires a current preview and submits only explicit timing fields',async()=>{
  const bodies=[]
  const h=harness({save:async(id,body)=>{bodies.push(body);return {id,version:2}}})
  await h.save();assert.equal(bodies.length,0)
  await h.check();h.earlier.value=true;await h.save()
  assert.deepEqual(JSON.parse(JSON.stringify(bodies[0])),{base_version:1,day_date:'2026-10-27',item_id:'stop',fixed_start_time:null,entry_window:{start:'09:00',end:'12:00'},choice:'earlier'})
  assert.equal(h.events.filter(event=>event[0]==='saved').length,1)
})

test('identity mismatch and failed save keep the trip unchanged and show a retryable error',async()=>{
  const bad=harness({preview:async()=>({...result(),item_id:'wrong'})})
  await bad.check();assert.equal(bad.preview.value,null);assert.match(bad.error.value,/重新预览/)
  const h=harness({save:async()=>{throw new Error('行程已更新，请重新预览')}})
  await h.check();await h.save()
  assert.match(h.error.value,/行程已更新/);assert.equal(h.events.some(event=>event[0]==='saved'),false)
  assert.equal(h.working.value,false)
})

test('route repair adoption requires a current feasible option and sends no client timeline',async()=>{
  const feasible={id:'reorder-1',status:'feasible'},unknown={id:'move-flex',status:'unverified'}
  const calls=[]
  const h=harness({options:async()=>({version:1,item_id:'stop',day_date:'2026-10-27',options:[feasible,unknown]}),adopt:async(...args)=>{calls.push(args);return {id:'trip',version:2}}})
  await h.check();await h.findOptions();await h.adopt(unknown);assert.equal(calls.length,0)
  await h.adopt(feasible);assert.equal(calls.length,1);assert.equal(calls[0][3],'reorder-1')
  assert.equal(Object.hasOwn(calls[0][1],'days'),false)
  h.allowCrossDay.value=true;assert.equal(h.options.value.length,0)
  await h.adopt(feasible);assert.equal(calls.length,1)
})
