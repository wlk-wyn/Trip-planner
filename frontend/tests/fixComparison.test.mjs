import test from 'node:test'
import assert from 'node:assert/strict'
import { compareVisits, dayTiming, unverifiedRouteReasons } from '../src/utils/fixComparison.ts'

const visit = (id, minutes = 90) => ({ item_id: id, name: '同名景点', visit_duration: minutes })
const day = (attractions, visit_order = []) => ({ attractions, visit_order })
const timed = (segments, status = 'feasible') => ({ schedule: {
  status, minimum_minutes: 180, available_minutes: 200,
  items: [{ end_time: '10:30' }, { end_time: '12:00' }], segments,
} })

test('unknown traffic explains deduplicated reasons without hiding valid zero-duration legs', () => {
  const input = timed([
    {status:'unverified',duration_minutes:null,note:'查询超时'},
    {status:'unverified',duration_minutes:null,note:'查询超时'},
    {status:'unverified',duration_minutes:null},
    {status:'verified',duration_minutes:0,note:'有效零耗时'},
  ])
  assert.deepEqual(unverifiedRouteReasons(input), ['查询超时','交通查询未返回可用耗时'])
  assert.deepEqual(unverifiedRouteReasons({}), [])
})

test('same names do not hide a removed visit or a shortened visit', () => {
  const result = compareVisits(day([visit('a'), visit('b')]), day([visit('b', 60)]))
  assert.deepEqual(result.changes.map(row => [row.kind, row.before, row.after]), [['removed', 90, undefined], ['duration', 90, 60]])
  assert.equal(result.reordered, false)
})
test('compare explicit order among retained visits, ignoring meals and removed visits', () => {
  const result = compareVisits(day([visit('a'), visit('b'), visit('c')], ['a', 'meal', 'b', 'c']), day([visit('a'), visit('b')], ['b', 'meal', 'a']))
  assert.equal(result.reordered, true)
})
test('missing or duplicate identity does not invent additions or removals', () => {
  for (const items of [[visit(undefined)], [visit('a'), visit('a')]]) {
    const result = compareVisits(day(items), day([visit('b')]))
    assert.equal(result.identityComplete, false)
    assert.deepEqual(result.changes, [])
  }
})
test('added visits are identified without mutating source arrays', () => {
  const before = day([visit('a')]), after = day([visit('a'), visit('b')])
  const snapshot = JSON.stringify([before, after])
  assert.equal(compareVisits(before, after).changes[0].kind, 'added')
  assert.equal(JSON.stringify([before, after]), snapshot)
})
test('missing or unverified travel never appears as zero or a confirmed finish', () => {
  for (const segments of [[], [{ status: 'unverified', duration_minutes: null }], [{ status: 'verified', duration_minutes: null }]]) {
    const result = dayTiming(timed(segments))
    assert.equal(result.complete, false)
    assert.equal(result.finish, '暂无法确认')
    assert.notEqual(result.travel, '0 分钟')
    assert.equal(result.overrun, '暂无法确认')
  }
})
test('verified zero travel is valid; timing conflicts remain visible', () => {
  const value = timed([{ status: 'verified', duration_minutes: 0 }], 'conflict')
  value.schedule.minimum_minutes = 230
  assert.deepEqual(dayTiming(value), { complete: true, travel: '0 分钟', finish: '12:00', overrun: '超出 30 分钟' })
})
test('partial timing exposes only known lower bound for overrun', () => {
  const value = timed([{ status: 'unverified', duration_minutes: null }], 'conflict')
  value.schedule.minimum_minutes = 230
  assert.equal(dayTiming(value).overrun, '至少超出 30 分钟')
  assert.equal(dayTiming({}).travel, '尚未校验')
})
