import test from 'node:test'
import assert from 'node:assert/strict'
import { applyIntakeCosts, intakeCostConditions, needsBudgetClarification } from '../src/utils/intakeCosts.ts'

test('intake carries confirmed quantities and total into the planning form', () => {
  const form = {}
  applyIntakeCosts(form, { party_size: 2, room_count: 1, destination_budget: 3000, budget_mode: 'active' })
  assert.deepEqual(form, { party_size: 2, room_count: 1, destination_budget: 3000, budget_mode: 'active' })
  assert.deepEqual(intakeCostConditions(form), ['同行总人数：2人', '每晚房间数：1间', '目的地内全体总预算3000元，不含往返大交通'])
})

test('explicit null clears stale counts and total; missing old-server fields preserve manual values', () => {
  const form = { party_size: 2, room_count: 1, destination_budget: 1500 }
  applyIntakeCosts(form, {})
  assert.equal(form.destination_budget, 1500)
  applyIntakeCosts(form, { party_size: null, room_count: null, destination_budget: null })
  assert.deepEqual(form, { party_size: null, room_count: null, destination_budget: null })
})

test('deferring a budget never resends the previous amount as an active condition', () => {
  const form = { party_size: 2, room_count: 1, destination_budget: 1500 }
  applyIntakeCosts(form, { budget_mode: 'deferred', destination_budget: 1500 })
  assert.equal(form.destination_budget, null)
  assert.equal(form.budget_mode, 'deferred')
  assert.ok(intakeCostConditions(form).includes('预算稍后再定，先规划行程'))
  assert.ok(!intakeCostConditions(form).join('').includes('1500'))
  applyIntakeCosts(form, { budget_mode: 'active', destination_budget: 2000 })
  assert.ok(intakeCostConditions(form).join('').includes('2000'))
})

test('zero is an explicit budget and absent party sizes are never invented', () => {
  assert.deepEqual(intakeCostConditions({ destination_budget: 0 }), ['目的地内全体总预算0元，不含往返大交通'])
  assert.deepEqual(intakeCostConditions({}), [])
})

test('pending questions cannot be bypassed but detailed fields or defer resolve them', () => {
  assert.equal(needsBudgetClarification({}, 3, true), true)
  assert.equal(needsBudgetClarification({ party_size: 2, destination_budget: 1500 }, 3, true), true)
  assert.equal(needsBudgetClarification({ party_size: 2, room_count: 1, destination_budget: 1500 }, 3, true), false)
  assert.equal(needsBudgetClarification({ party_size: 1, destination_budget: 0 }, 1, true), false)
  assert.equal(needsBudgetClarification({ budget_mode: 'deferred' }, 3, true), false)
  assert.equal(needsBudgetClarification({}, 3, false), false)
})
