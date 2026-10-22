import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from '@vue/compiler-sfc'

// Exercise the actual result-page loader with controlled network completion order.
const { descriptor } = parse(readFileSync(new URL('../src/views/Result.vue', import.meta.url), 'utf8'))
const source = ts.createSourceFile('result.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
const loader = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'loadChecks')
assert.ok(loader)
const code = ts.transpileModule(loader.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const ref = value => ({ value })
function harness() {
  const pending = []
  const state = { savedId: ref('trip-a'), savedVersion: ref(1), checksState: ref('idle'), checksGeneration: 0,
    costGeneration: 0, factsReport: ref(null), costReport: ref(null), dayReports: ref({}),
    visitReminders: ref([]), ruleIssues: ref([]),
    planChecks: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) }
  vm.createContext(state)
  vm.runInContext(code, state)
  return { state, pending, load: () => state.loadChecks() }
}
const result = (version, marker) => ({ version, issues: [marker], fact_report: marker, cost_report: marker,
  visit_reminders: [marker], days: [{ date: '2026-10-20', marker }] })

test('failed checks remove previous findings and permit a successful retry', async () => {
  const h = harness()
  h.state.ruleIssues.value = ['previous']
  const first = h.load()
  assert.equal(h.state.checksState.value, 'loading')
  assert.equal(h.state.ruleIssues.value.length, 0)
  h.pending[0].reject(new Error('offline'))
  await first
  assert.equal(h.state.checksState.value, 'error')
  const retry = h.load()
  h.pending[1].resolve(result(1, 'current'))
  await retry
  assert.equal(h.state.checksState.value, 'ready')
  assert.equal(h.state.ruleIssues.value[0], 'current')
})

test('older responses cannot overwrite newer checks even for the same version', async () => {
  const h = harness()
  const first = h.load(), second = h.load()
  h.pending[1].resolve(result(1, 'new'))
  await second
  h.pending[0].resolve(result(1, 'old'))
  await first
  assert.equal(h.state.factsReport.value, 'new')
  assert.equal(h.state.checksState.value, 'ready')
})

test('a previous version failure cannot mark current successful checks as failed', async () => {
  const h = harness()
  const first = h.load()
  h.state.savedVersion.value = 2
  const second = h.load()
  h.pending[1].resolve(result(2, 'version-2'))
  await second
  h.pending[0].reject(new Error('old failure'))
  await first
  assert.equal(h.state.checksState.value, 'ready')
  assert.equal(h.state.factsReport.value, 'version-2')
})

test('a mismatched server version never marks checks ready', async () => {
  const h = harness()
  const request = h.load()
  h.pending[0].resolve(result(2, 'wrong-version'))
  await request
  assert.equal(h.state.checksState.value, 'error')
  assert.equal(h.state.factsReport.value, null)
})

const assessment = source.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(decl => decl.name.getText(source) === 'currentAssessment'))
const assessmentCode = ts.transpileModule(assessment.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
function assess(state, issues = []) {
  const context = { computed: fn => ({ get value() { return fn() } }), savedId: ref('saved-trip'),
    checksState: ref(state), ruleIssues: ref(issues), venueTimingIssues: new Set(['inner_venue_closed', 'closed_day', 'opening_hours_conflict', 'opening_weekday_uncovered']),
    tripPlan: ref({ days: [{ day_index: 0, schedule: { conflicts: [], status: 'feasible', segments: [] } }] }) }
  vm.createContext(context)
  return vm.runInContext(assessmentCode + '\ncurrentAssessment.value', context)
}
test('overall advice cannot claim success while checks are loading or failed', () => {
  assert.match(assess('loading'), /正在检查/)
  assert.match(assess('error'), /重试检查/)
  assert.doesNotMatch(assess('error'), /通过校验/)
})
test('overall advice reflects venue timing conflicts despite a feasible travel schedule', () => {
  const advice = assess('ready', [{ code: 'inner_venue_closed', message: '园内场馆周一关闭。' }])
  assert.match(advice, /园内场馆周一关闭/)
  assert.doesNotMatch(advice, /通过校验/)
  for (const code of ['closed_day', 'opening_hours_conflict', 'opening_weekday_uncovered']) {
    assert.match(assess('ready', [{code, message: '登记闭馆或营业时段冲突'}]), /先调整参观安排/)
  }
  assert.match(assess('ready'), /通过校验/)
})
