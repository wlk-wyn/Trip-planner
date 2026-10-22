import type { PlanningRun, TripFormData } from '../types/index'
import { TripServiceError, submissionRejected } from './serviceError.ts'

export const PENDING_PLAN_KEY = 'pendingPlanRun'
export interface PendingPlan {
  id: string
  request: TripFormData | null
  accepted: boolean
}
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
type Transport = {
  read: (id: string) => Promise<PlanningRun>
  submit: (request: TripFormData, id: string) => Promise<PlanningRun>
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function readPendingPlan(store: Store): PendingPlan | null {
  const raw = store.getItem(PENDING_PLAN_KEY)
  if (!raw) return null
  // Earlier versions stored just the ID, without a request snapshot to replay.
  if (uuid.test(raw)) return { id: raw, request: null, accepted: true }
  try {
    const value = JSON.parse(raw)
    if (value?.schema !== 1 || !uuid.test(value.id) || typeof value.accepted !== 'boolean' ||
        !value.request || typeof value.request !== 'object' || Array.isArray(value.request)) throw new Error()
    return { id: value.id, request: value.request, accepted: value.accepted }
  } catch {
    throw new Error('上次规划的恢复记录无法读取，请保留此浏览器并联系维护者处理。')
  }
}

function writePendingPlan(store: Store, pending: PendingPlan) {
  try { store.setItem(PENDING_PLAN_KEY, JSON.stringify({ schema: 1, ...pending })) }
  catch { throw new Error('无法保存规划恢复记录，请允许本站使用浏览器存储后重试。尚未确认的任务不会被重复提交。') }
}

function clearStoredPlan(store: Store, id: string) {
  // An older page must not clear a newer task opened in another tab.
  if (readPendingPlan(store)?.id === id) store.removeItem(PENDING_PLAN_KEY)
}

async function withPlanLock<T>(operation: () => Promise<T> | T): Promise<T> {
  // Secure-context browsers coordinate mutation across tabs of the same origin.
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request('trip-plan-submission', operation)
  }
  return operation()
}

export async function clearPendingPlan(store: Store, id: string) {
  await withPlanLock(() => clearStoredPlan(store, id))
}

/** Snapshot before the POST; retry the same ID and body after an uncertain response. */
export async function connectPlan(
  store: Store, request: TripFormData | null, transport: Transport,
  newId: () => string,
): Promise<PlanningRun | null> {
  return withPlanLock(() => connectStoredPlan(store, request, transport, newId))
}

async function connectStoredPlan(store: Store, request: TripFormData | null, transport: Transport,
  newId: () => string): Promise<PlanningRun | null> {
  let pending = readPendingPlan(store)
  let created = false
  if (!pending) {
    if (!request) return null
    pending = { id: newId(), request: JSON.parse(JSON.stringify(request)), accepted: false }
    writePendingPlan(store, pending) // Failure here must prevent sending a non-recoverable request.
    created = true
  }
  let run: PlanningRun
  try {
    if (created) run = await transport.submit(pending.request!, pending.id)
    else {
      try { run = await transport.read(pending.id) }
      catch (error) {
        // A missing acknowledgement can precede admission. Replay only the saved body;
        // an acknowledged/deleted task must never silently become a new generation.
        if (!(error instanceof TripServiceError) || error.status !== 404 || pending.accepted || !pending.request) throw error
        run = await transport.submit(pending.request, pending.id)
      }
    }
  } catch (error) {
    // A rejected retry cannot prove that an earlier delayed POST will never arrive.
    if (created && submissionRejected(error)) clearStoredPlan(store, pending.id)
    throw error
  }
  if (readPendingPlan(store)?.id === pending.id && pending.request) {
    writePendingPlan(store, { ...pending, accepted: true })
  }
  return run
}
