import test from 'node:test'
import assert from 'node:assert/strict'
import { staticClosure } from '../scripts/bundle-report.mjs'

test('route payload includes shared transitive dependencies once and excludes deferred features',()=>{
  const graph=[{file:'entry',imports:['shared'],dynamicImports:['home','result']},
    {file:'home',imports:['entry','shared'],dynamicImports:['settings']},
    {file:'shared',imports:['entry']},{file:'settings',imports:['shared']},{file:'result',imports:['shared']}]
  assert.deepEqual(staticClosure(graph,['entry','home']),['entry','home','shared'])
})
test('missing static chunks cannot silently make payload look smaller',()=>{
  assert.throws(()=>staticClosure([{file:'entry',imports:['missing']}],['entry']),/missing/)
})
