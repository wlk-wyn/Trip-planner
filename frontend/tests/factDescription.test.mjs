import test from 'node:test'
import assert from 'node:assert/strict'
import { factDescription, officialSourceUrl, officialPriceNeedsUpdate } from '../src/utils/factDescription.ts'

const official = {source:'official',status:'official_reference',recorded_at:'2026-10-16T01:00:00Z',
  source_url:'https://www.dpm.org.cn/singles_detail/257830.html',applies_on:'2026-11-06',scope:'成人大门票；展览另计'}

test('official policy stays a dated planning reference with explicit scope',()=>{
  const text=factDescription(official)
  assert.ok(text.includes('官网公示参考'))
  assert.ok(text.includes('2026-11-06'))
  assert.ok(text.includes('展览另计'))
  assert.ok(!text.includes('高德'))
  assert.equal(officialSourceUrl(official),official.source_url)
})

test('moved and expired price references explain why an update is needed',()=>{
  assert.ok(factDescription({...official,status:'scope_mismatch'}).includes('游览日期已变化'))
  assert.ok(factDescription({...official,status:'stale_reference'}).includes('建议更新'))
  assert.ok(factDescription({...official,status:'unknown_age'}).includes('记录时间不明'))
})

test('source links accept only supported official HTTPS pages',()=>{
  for(const url of ['javascript:alert(1)','http://www.dpm.org.cn/singles_detail/257830.html',
    'https://www.dpm.org.cn.evil.test/singles_detail/257830.html','https://www.dpm.org.cn/other',null]){
    assert.equal(officialSourceUrl({...official,source_url:url}),null)
  }
  assert.equal(officialSourceUrl({...official,source:'model'}),null)
  assert.equal(officialSourceUrl(),null)
})

test('place cards do not show a seasonal price for another visit date',()=>{
  assert.equal(officialPriceNeedsUpdate(official,'2026-11-07'),true)
  assert.equal(officialPriceNeedsUpdate(official,'2026-11-06'),false)
  assert.equal(officialPriceNeedsUpdate(official),true)
  assert.equal(officialPriceNeedsUpdate({...official,source:'model'},'2026-11-07'),false)
  assert.equal(officialPriceNeedsUpdate(null,'2026-11-07'),false)
})
