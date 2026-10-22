import test from 'node:test'
import assert from 'node:assert/strict'
import { waitingBefore } from '../src/utils/scheduleWait.ts'

const fixture = () => ({ buffer_minutes: 15, schedule: { window_start: '09:00',
  items: [{ id: 'a', start_time: '09:00', end_time: '10:00' }, { id: 'b', start_time: '11:00', end_time: '12:00' }],
  segments: [{ from_id: 'a', to_id: 'b', status: 'verified', duration_minutes: 20 }] } })

test('only the gap beyond traffic and buffer is displayed', () => {
  const day = fixture(), snapshot = JSON.stringify(day)
  assert.equal(waitingBefore(day, 1), 25)
  assert.equal(JSON.stringify(day), snapshot)
})
test('known zero travel and zero buffer remain valid', () => {
  const day = fixture(); day.buffer_minutes = 0; day.schedule.segments[0].duration_minutes = 0
  assert.equal(waitingBefore(day, 1), 60)
})
test('unknown traffic, missing buffer and broken edge identity do not invent waiting', () => {
  for (const change of [d => d.schedule.segments[0].status = 'unverified', d => d.schedule.segments = [],
    d => d.schedule.segments[0].duration_minutes = null, d => d.buffer_minutes = null,
    d => d.schedule.segments[0].from_id = 'other', d => d.schedule.items[0].end_time = null]) {
    const day = fixture(); change(day); assert.equal(waitingBefore(day, 1), null)
  }
})
test('invalid clocks, negative and nonfinite traffic are rejected', () => {
  for (const change of [d => d.schedule.items[1].start_time = '25:00',
    d => d.schedule.items[0].end_time = '10:99', d => d.schedule.segments[0].duration_minutes = -1,
    d => d.schedule.segments[0].duration_minutes = NaN]) {
    const day = fixture(); change(day); assert.equal(waitingBefore(day, 1), null)
  }
})
test('the first stop can have a known opening or planned-start gap', () => {
  const day = fixture(); day.schedule.items[0].start_time = '10:00'
  assert.equal(waitingBefore(day, 0), 60)
})
test('no gap, no schedule and invalid indices stay empty', () => {
  const day = fixture(); day.schedule.items[1].start_time = '10:35'
  assert.equal(waitingBefore(day, 1), null)
  assert.equal(waitingBefore({}, 0), null)
  for (const index of [-1, .5, 2]) assert.equal(waitingBefore(day, index), null)
})
