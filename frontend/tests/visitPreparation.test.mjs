import test from 'node:test'
import assert from 'node:assert/strict'
import { bookingWindowLabel, checkedPreparation, stayPreparationKey } from '../src/utils/visitPreparation.ts'

const item = { policy_status: 'reviewed', booking_opens_at: '2026-10-20T20:00:00+08:00' }

test('booking opening is expressed in Beijing time, never as a deadline or availability', () => {
  assert.match(bookingWindowLabel(item, Date.parse('2026-10-18T10:00:00Z')), /10\/20.*20:00.*开始预约.*北京时间/)
  assert.match(bookingWindowLabel(item, Date.parse('2026-10-21T10:00:00Z')), /余票未查询/)
})
test('unknown, expired and malformed policies cannot display an opening time', () => {
  for (const change of [{ policy_status: 'unknown' }, { policy_status: 'expired' }, { booking_opens_at: null }, { booking_opens_at: 'bad' }]) {
    assert.equal(bookingWindowLabel({ ...item, ...change }), null)
  }
})
test('invalid local checklist storage cannot crash the preparation panel', () => {
  for (const value of [null, 'bad', '{}', 'true', 'null']) assert.deepEqual(checkedPreparation(value), [])
  assert.deepEqual(checkedPreparation('["r|one", 1, null, "p|two"]'), ['r|one', 'p|two'])
})

test('a hotel or stay change cannot inherit an old date-only checked box', () => {
  const stay = { name:'甲酒店', address:'湖滨路', check_in:'2026-10-03', check_out:'2026-10-04', nights:1 }
  const checked = new Set(['s|2026-10-03', stayPreparationKey(stay)])
  assert.equal(checked.has(stayPreparationKey({...stay})),true)
  for (const change of [{name:'乙酒店'}, {address:'另一条路'}, {check_out:'2026-10-05',nights:2}, {id:'new-server-identity'}]) {
    assert.equal(checked.has(stayPreparationKey({...stay,...change})),false)
  }
  assert.notEqual(stayPreparationKey({...stay,id:'old'}),stayPreparationKey({...stay,id:'new'}))
})
