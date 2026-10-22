import test from 'node:test'
import assert from 'node:assert/strict'
import { connectPlan, readPendingPlan, clearPendingPlan, PENDING_PLAN_KEY } from '../src/utils/pendingPlan.ts'
import { TripServiceError } from '../src/utils/serviceError.ts'

const id = '11111111-1111-4111-8111-111111111111'
const nextId = '22222222-2222-4222-8222-222222222222'
const original = () => ({ city:'西安', start_date:'2026-11-07', end_date:'2026-11-09', preferences:['历史文化'] })
function memory() {
  const data = new Map()
  return { getItem:key=>data.get(key)??null, setItem:(key,value)=>data.set(key,value), removeItem:key=>data.delete(key) }
}
const missing = () => new TripServiceError('不存在',404)
const noNewId = () => { throw new Error('must reuse the stored ID') }

test('lost acknowledgement after admission reconnects without another submission',async()=>{
  const store=memory(), accepted=new Map();let submissions=0
  const transport={
    submit:async(request,key)=>{submissions++;accepted.set(key,{id:key,status:'queued'});throw new TripServiceError('response lost',504)},
    read:async key=>accepted.get(key),
  }
  await assert.rejects(connectPlan(store,original(),transport,()=>id))
  assert.equal(readPendingPlan(store).accepted,false)
  assert.equal((await connectPlan(store,null,transport,noNewId)).id,id)
  assert.equal(submissions,1)
  assert.equal(readPendingPlan(store).accepted,true)
})

test('a request lost before admission replays the persisted snapshot, not an edited draft',async()=>{
  const store=memory(), draft=original(), calls=[]
  const transport={read:async()=>{throw missing()},submit:async(request,key)=>{
    calls.push({request:structuredClone(request),key})
    if(calls.length===1)throw new Error('connection lost before delivery')
    return {id:key,status:'queued'}
  }}
  await assert.rejects(connectPlan(store,draft,transport,()=>id))
  draft.city='北京';draft.preferences.push('亲子')
  const run=await connectPlan(store,draft,transport,noNewId)
  assert.equal(run.id,id)
  assert.deepEqual(calls[0],calls[1])
  assert.equal(calls[1].request.city,'西安')
  assert.deepEqual(calls[1].request.preferences,['历史文化'])
})

test('temporary read failure or explicit expiry never triggers another POST',async()=>{
  for(const status of [401,429,503,504,410]){
    const store=memory()
    await assert.rejects(connectPlan(store,original(),{submit:async()=>{throw new Error('network')}},()=>id))
    let posts=0
    await assert.rejects(connectPlan(store,null,{read:async()=>{throw new TripServiceError('read failed',status)},submit:async()=>{posts++}},noNewId))
    assert.equal(posts,0)
    assert.equal(readPendingPlan(store).id,id)
  }
})

test('an acknowledged missing job and legacy ID cannot silently restart generation',async()=>{
  for(const legacy of [false,true]){
    const store=memory()
    if(legacy)store.setItem(PENDING_PLAN_KEY,id)
    else await connectPlan(store,original(),{submit:async()=>({id,status:'queued'})},()=>id)
    await assert.rejects(connectPlan(store,null,{read:async()=>{throw missing()},submit:async()=>{throw new Error('unexpected resubmit')}},noNewId),{status:404})
    assert.equal(readPendingPlan(store).accepted,true)
  }
})

test('initial capacity rejection releases the record; an uncertain retry keeps the identity',async()=>{
  const store=memory(), busy=new TripServiceError('busy',429)
  await assert.rejects(connectPlan(store,original(),{submit:async()=>{throw busy}},()=>id))
  assert.equal(readPendingPlan(store),null)
  await assert.rejects(connectPlan(store,original(),{submit:async()=>{throw new Error('network')}},()=>id))
  await assert.rejects(connectPlan(store,null,{read:async()=>{throw missing()},submit:async()=>{throw busy}},noNewId))
  assert.equal(readPendingPlan(store).id,id)
})

test('storage failure prevents sending a request that cannot be recovered',async()=>{
  const store=memory();store.setItem=()=>{throw new Error('quota exceeded')}
  let posts=0
  await assert.rejects(connectPlan(store,original(),{submit:async()=>{posts++}},()=>id),/浏览器存储/)
  assert.equal(posts,0)
})

test('a completed old page cannot remove or overwrite a newer pending task',async()=>{
  const store=memory()
  const transport={submit:async()=>{
    store.setItem(PENDING_PLAN_KEY,JSON.stringify({schema:1,id:nextId,accepted:false,request:{...original(),city:'杭州'}}))
    return {id,status:'succeeded'}
  }}
  await connectPlan(store,original(),transport,()=>id)
  await clearPendingPlan(store,id)
  assert.equal(readPendingPlan(store).id,nextId)
  assert.equal(readPendingPlan(store).accepted,false)
})

test('no pending task requires no traffic; malformed records cannot be overwritten',async()=>{
  const store=memory()
  assert.equal(await connectPlan(store,null,{},noNewId),null)
  store.setItem(PENDING_PLAN_KEY,'{"schema":1}')
  await assert.rejects(connectPlan(store,original(),{},noNewId),/恢复记录无法读取/)
  assert.equal(store.getItem(PENDING_PLAN_KEY),'{"schema":1}')
})

test('browser lock serializes overlapping tab submissions around one stored task',async()=>{
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'navigator')
  let tail=Promise.resolve(),active=0,maximum=0,posts=0
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks:{request:(_name,operation)=>{
    const result=tail.then(operation);tail=result.catch(()=>{});return result
  }}}})
  const store=memory()
  const transport={
    submit:async(_request,key)=>{posts++;active++;maximum=Math.max(maximum,active);await new Promise(resolve=>setTimeout(resolve,20));active--;return {id:key,status:'queued'}},
    read:async key=>{active++;maximum=Math.max(maximum,active);await new Promise(resolve=>setTimeout(resolve,5));active--;return {id:key,status:'queued'}},
  }
  try{
    const runs=await Promise.all([connectPlan(store,original(),transport,()=>id),connectPlan(store,{...original(),city:'杭州'},transport,()=>nextId)])
    assert.deepEqual(runs.map(run=>run.id),[id,id])
    assert.equal(posts,1);assert.equal(maximum,1)
    assert.equal(readPendingPlan(store).request.city,'西安')
  }finally{
    if(descriptor)Object.defineProperty(globalThis,'navigator',descriptor)
    else delete globalThis.navigator
  }
})
