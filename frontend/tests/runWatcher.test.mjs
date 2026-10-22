import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { TripServiceError, runWatchRejected, runMissing } from '../src/utils/serviceError.ts'
import { parse } from '@vue/compiler-sfc'

// Execute the production watcher with controlled transport and timers.
const source = ts.createSourceFile('trips.ts', readFileSync(new URL('../src/services/trips.ts', import.meta.url),'utf8'), ts.ScriptTarget.Latest,true)
const watcher = source.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'watchRun')
const code = ts.transpileModule(watcher.getText(source).replace('export ',''),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
function harness(fetcher) {
  const signals=[]
  const context={AbortController,TextDecoder,runWatchRejected,TERMINAL:['succeeded','failed','conflict','cancelled'],
    apiUrl:path=>path,ownerToken:()=> 'synthetic-owner',
    setInterval:()=>1,clearInterval:()=>{},setTimeout:callback=>callback(),
    fetch:async (url,options)=>{signals.push(options.signal);return fetcher(url,options)} }
  vm.createContext(context);vm.runInContext(code,context)
  return {watch:context.watchRun,signals}
}
test('permanent polling rejections stop instead of leaving an endless spinner',async()=>{
  for(const status of [401,403,404,410]) {
    const h=harness(async()=>({ok:false,status:404}))
    const error=new TripServiceError('任务不可访问',status)
    let polls=0
    await assert.rejects(h.watch('run',async()=>{polls++;throw error},()=>{}),e=>e===error)
    assert.equal(polls,1)
    assert.equal(h.signals[0].aborted,true)
  }
})
test('transient failures recover through polling even when SSE is unavailable',async()=>{
  const h=harness(async()=>({ok:false,status:404}));let polls=0;const seen=[]
  const final=await h.watch('run',async()=>{if(++polls===1)throw new TripServiceError('稍后重试',503);return {status:'succeeded'}},r=>seen.push(r.status))
  assert.equal(final.status,'succeeded');assert.equal(polls,2)
  assert.deepEqual(seen,['succeeded'])
})
test('terminal stream results close the connection and do not start polling',async()=>{
  const bytes=new TextEncoder().encode('data: {"status":"succeeded"}\n\n')
  const h=harness(async()=>({ok:true,body:{getReader:()=>({read:async()=>({done:false,value:bytes})})}}))
  let polls=0
  const final=await h.watch('run',async()=>{polls++;throw new Error('unexpected poll')},()=>{})
  assert.equal(final.status,'succeeded');assert.equal(polls,0)
  assert.equal(h.signals[0].aborted,true)
})
test('a disposed page receives no late stream update or polling result',async()=>{
  let disposed=false,updates=0,polls=0
  const bytes=new TextEncoder().encode('data: {"status":"succeeded"}\n\n')
  const h=harness(async()=>({ok:true,body:{getReader:()=>({read:async()=>{disposed=true;return {done:false,value:bytes}}})}}))
  await assert.rejects(h.watch('run',async()=>{polls++;return {status:'succeeded'}},()=>updates++,()=>disposed),/页面已关闭/)
  assert.equal(updates,0);assert.equal(polls,0)
  assert.equal(h.signals[0].aborted,true)
})

const {descriptor}=parse(readFileSync(new URL('../src/views/Result.vue',import.meta.url),'utf8'))
const resultSource=ts.createSourceFile('result.ts',descriptor.scriptSetup.content,ts.ScriptTarget.Latest,true)
const awaitRun=resultSource.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='awaitRun')
const resultCode=ts.transpileModule(awaitRun.getText(resultSource),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
test('result page releases its busy state and retains only recoverable task identities',async()=>{
  for(const status of [401,403,404,410]) {
    const h=harness(async()=>({ok:false}))
    const storage=new Map([['pendingRun:trip','run']])
    const error=new TripServiceError('任务不可访问',status)
    const context={activeRun:{value:null},savedId:{value:'trip'},runConnectionIssue:{value:''},runDisposed:false,
      pendingRunKey:()=> 'pendingRun:trip',runMissing,runWatchRejected,watchRun:h.watch,
      readRun:async()=>{throw error},localStorage:{getItem:key=>storage.get(key),removeItem:key=>storage.delete(key)}}
    vm.createContext(context);vm.runInContext(resultCode,context)
    await assert.rejects(context.awaitRun({id:'run',status:'running'}),e=>e===error)
    assert.equal(context.activeRun.value,null)
    assert.equal(context.runConnectionIssue.value,'')
    assert.equal(storage.has('pendingRun:trip'),[401,403].includes(status))
  }
})
