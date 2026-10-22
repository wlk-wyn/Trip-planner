import test from 'node:test'
import assert from 'node:assert/strict'
import {forecastDisplay,forecastEvidence} from '../src/utils/forecast.ts'
const now=Date.parse('2026-10-18T03:00:00Z')
const forecast={day:'小雨',night:'阴',high:0,low:null,source:'amap',observed_at:'2026-10-18T02:00:00Z',report_time:'2026-10-18 08:00:00'}
test('actual zero temperature is displayed while an unknown temperature is not invented',()=>{
  assert.match(forecastDisplay(forecast,now).text,/最高 0°C/)
  assert.equal(forecastDisplay({...forecast,high:null,low:''},now).text,'小雨 · 短期预报')
  assert.match(forecastDisplay({...forecast,low:'-3'},now).text,/-3–0°C/)
})
test('legacy or model weather without actual provider evidence is not presented as a forecast',()=>{
  for(const changes of [{source:undefined},{source:'model'},{observed_at:null},{report_time:null}])assert.equal(forecastDisplay({...forecast,...changes},now),null)
})
test('stale, malformed and future report/query times are hidden',()=>{
  for(const changes of [{report_time:'2026-10-16 08:00:00'},{observed_at:'2026-10-16T02:00:00Z'},{report_time:'bad'},{report_time:'2026-10-19 08:00:00'},{observed_at:'2026-10-19T02:00:00Z'}])assert.equal(forecastDisplay({...forecast,...changes},now),null)
})
test('missing temperatures stay unknown and weather descriptions still identify wet conditions',()=>{
  for(const value of ['',false,'NaN',Infinity,'1000'])assert.equal(forecastDisplay({...forecast,high:value},now).text,'小雨 · 短期预报')
  assert.equal(forecastDisplay({...forecast,day:'多云',night:'雨'},now).wet,true)
})
test('publication and query labels use Beijing time and never relabel stale data as fresh',()=>{
  const evidence=forecastEvidence(forecast,now)
  assert.match(evidence.reportTime,/08:00/)
  assert.match(evidence.queryTime,/10:00/)
  assert.equal(forecastEvidence({...forecast,report_time:null},now),null)
})
