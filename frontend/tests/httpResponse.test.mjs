import test from 'node:test'
import assert from 'node:assert/strict'
import { httpFailure, readApiJson } from '../src/utils/httpResponse.ts'
import { TripServiceError } from '../src/utils/serviceError.ts'

test('busy responses keep the server explanation and minimum retry delay',async()=>{
  const response=new Response(JSON.stringify({detail:'当前操作较多'}),{status:429,headers:{'Retry-After':'15'}})
  await assert.rejects(readApiJson(response),error=>{
    assert.ok(error instanceof TripServiceError);assert.equal(error.status,429)
    assert.equal(error.retryAfter,15);assert.match(error.message,/当前操作较多.*15 秒/);return true
  })
})
test('HTTP errors are not interpreted as empty recommendations or a network failure',async()=>{
  await assert.rejects(readApiJson(new Response(JSON.stringify({detail:'协调服务不可用'}),{status:503})),{status:503,message:'协调服务不可用'})
  await assert.rejects(readApiJson(new Response('<html>gateway</html>',{status:502})),{status:502})
  assert.equal(httpFailure(422,[{msg:'invalid'}],null).message,'服务暂时无法完成此操作，请稍后重试')
})
test('invalid retry headers do not invent a delay and successful payloads remain intact',async()=>{
  for(const header of ['bad','NaN','-1','0','1e5','Infinity','99999999999999999999'])assert.equal(httpFailure(429,'繁忙',header).retryAfter,undefined)
  assert.deepEqual(await readApiJson(new Response(JSON.stringify({success:true,data:{results:[]}}))),{success:true,data:{results:[]}})
})
