import test from 'node:test'
import assert from 'node:assert/strict'
import {TripServiceError,submissionRejected,runMissing} from '../src/utils/serviceError.ts'

test('busy admission clears an unaccepted submission but not an existing pending run',()=>{
  const busy=new TripServiceError('服务繁忙',429)
  assert.equal(submissionRejected(busy),true)
  assert.equal(submissionRejected(busy,true),false)
  assert.equal(runMissing(busy),false)
})
test('a dropped response or server failure must retain the recoverable run identity',()=>{
  for(const error of [new Error('network'),new TripServiceError('timeout'),new TripServiceError('unavailable',503),new TripServiceError('gateway timeout',504)]){
    assert.equal(submissionRejected(error),false)
    assert.equal(runMissing(error),false)
  }
})
test('only an authoritative missing response clears a run during reattachment',()=>{
  assert.equal(runMissing(new TripServiceError('missing',404)),true)
  assert.equal(runMissing(new TripServiceError('expired',410)),true)
  assert.equal(runMissing(new TripServiceError('unauthorized',401)),false)
})
