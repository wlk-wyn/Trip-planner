import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import {parse} from '@vue/compiler-sfc'

const script=parse(readFileSync(new URL('../src/components/OfficialAdmissionPanel.vue',import.meta.url),'utf8')).descriptor.scriptSetup.content
const source=ts.createSourceFile('OfficialAdmissionPanel.ts',script,ts.ScriptTarget.Latest,true)
const loader=source.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='load')
const code=ts.transpileModule(loader.getText(source),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
function harness(read) {
  const context={props:{tripId:'trip',version:1,dayDate:'2026-10-27',itemId:'item',visitDate:'2026-10-27'},guide:{value:null},loading:{value:false},error:{value:''},generation:0,readAdmissionGuide:read}
  vm.createContext(context); vm.runInContext(code,context); return context
}
const response=(visitDate,guide={status:'reviewed',summary:[]})=>({version:1,item_id:'item',visit_date:visitDate,guide})

test('a slow old-date guide cannot overwrite the current-date result',async()=>{
  const waiting=[]
  const h=harness(()=>new Promise(resolve=>waiting.push(resolve)))
  const first=h.load()
  assert.equal(h.loading.value,true)
  h.props.visitDate='2026-10-28'
  const second=h.load()
  waiting[1](response('2026-10-28',{marker:'new'})); await second
  waiting[0](response('2026-10-27',{marker:'old'})); await first
  assert.equal(h.guide.value.marker,'new')
  assert.equal(h.loading.value,false)
  assert.equal(h.error.value,'')
})

test('unknown guides stay unknown and malformed input makes no request',async()=>{
  let calls=0
  const h=harness(async()=>{calls++;return response('2026-10-27',null)})
  await h.load(); assert.equal(h.guide.value,null);assert.equal(h.error.value,'')
  h.props.visitDate='broken';await h.load();assert.equal(calls,1);assert.equal(h.loading.value,false)
})

test('wrong identity or failed reads cannot display a stale official guide',async()=>{
  const h=harness(async()=>({...response('2026-10-27'),item_id:'other'}))
  h.guide.value={marker:'previous'}
  await h.load();assert.equal(h.guide.value,null);assert.match(h.error.value,/日期已变化/)
  h.readAdmissionGuide=async()=>{throw new Error('读取服务不可用')}
  await h.load();assert.equal(h.guide.value,null);assert.equal(h.loading.value,false);assert.equal(h.error.value,'读取服务不可用')
})
