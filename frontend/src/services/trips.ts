import axios from 'axios'
import { apiUrl } from './api'
import { httpFailure } from '@/utils/httpResponse'
import { runWatchRejected } from '@/utils/serviceError'
import type { TripFormData, TripPlan, ChatTurn, SavedTrip, SavedTripSummary, TripVersion, ReferenceSource, ReferenceCandidate, PlanningRun, WeatherAdvice, PlanIssue, TripPrep, FixProposal } from '@/types'

function ownerToken(): string {
  let token = localStorage.getItem('tripOwner')
  if (!token) { token = crypto.randomUUID(); localStorage.setItem('tripOwner', token) }
  return token
}
async function request<T>(method: string, path: string, data?: unknown, timeout?: number): Promise<T> {
  try {
    const response = await axios.request<T>({ method, url: apiUrl('/api/trips' + path), data,
      timeout: timeout ?? (method === 'GET' ? 10000 : 300000), headers: { 'X-Trip-Owner': ownerToken() } })
    return response.data
  } catch (error: any) {
    throw httpFailure(error.response?.status, error.response?.data?.detail || '无法连接保存服务，请确认后端已更新并运行', error.response?.headers?.['retry-after'])
  }
}
export const listTrips = () => request<SavedTripSummary[]>('GET', '')
export const readTrip = (id: string) => request<SavedTrip>('GET', '/' + encodeURIComponent(id))
export const createTrip = (plan: TripPlan, original: TripFormData, chat: ChatTurn[] = []) => request<SavedTrip>('POST', '', { plan, request: original, chat })
export const commitTrip = (id: string, version: number, plan: TripPlan, origin: string) => request<SavedTrip>('POST', `/${id}/versions`, { plan, base_version: version, origin })
export const tripVersions = (id: string) => request<TripVersion[]>('GET', `/${id}/versions`)
export const restoreTrip = (id: string, version: number, target: number) => request<SavedTrip>('POST', `/${id}/restore`, { base_version: version, target_version: target })
export const savedChat = (id: string, version: number, message: string) => request<SavedTrip & { reply: string; changed: boolean; actions: string[] }>('POST', `/${id}/chat`, { message, base_version: version })
export const analyzeReferences = (city: string, sources: ReferenceSource[]) => request<{ candidates: ReferenceCandidate[] }>('POST', '/references/analyze', { city, sources })
export function selectSavedTrip(trip: SavedTrip) {
  sessionStorage.setItem('tripPlan', JSON.stringify(trip.plan))
  sessionStorage.setItem('tripRequest', JSON.stringify(trip.request))
  sessionStorage.setItem('tripChat', JSON.stringify(trip.chat))
  localStorage.setItem('activeTripId', trip.id)
}

export const validateTrip = (id: string, version: number) => request<SavedTrip>("POST", `/${id}/validate`, { base_version: version })

// value: chat message, one date, or several dates (optimized in one run and saved as one version)
export const startRun = (id: string, version: number, kind: 'chat' | 'optimize', value: string | string[], requestId: string) => request<PlanningRun>('POST', `/${id}/runs`, { base_version: version, kind, request_id: requestId, ...(kind === 'chat' ? { message: value } : Array.isArray(value) ? { dates: value } : { date: value }) })
export const weatherCheck = (id: string) => request<WeatherAdvice>('GET', `/${id}/weather`)
export const planChecks = (id: string) => request<{ version: number; issues: PlanIssue[]; days: import('@/types').DayDiagnosis[]; cost_report: import('@/types').CostReport; fact_report: import('@/types').FactReport; visit_reminders: import('@/types').VisitReminder[] }>('GET', `/${id}/checks`)
export const previewCost = (id: string, version: number, context: import('@/types').TripCostContext) => request<{ version: number; cost_report: import('@/types').CostReport }>('POST', `/${id}/cost-preview`, { base_version: version, context })
export const saveHotelQuotes = (id: string, version: number, quotes: import('@/types').HotelQuoteInput[]) => request<SavedTrip>('POST',`/${id}/hotel-quotes`,{base_version:version,quotes})
export const tripPrep = (id: string) => request<TripPrep>('GET', `/${id}/prep`)
export const dayFixes = (id: string, version: number, date: string) => request<{ date: string; proposals: FixProposal[] }>('POST', `/${id}/days/${date}/fixes`, { base_version: version })
export const enrichTrip = (id: string, version: number) => request<SavedTrip & { enrichment: { looked_up: number; found: number; failed: number; tickets_estimated: number; official_updated?: number; official_failed?: number } }>('POST', `/${id}/enrich`, { base_version: version })
export const readRun = (id: string, runId: string) => request<PlanningRun>('GET', `/${id}/runs/${runId}`)
export const listRuns = (id: string) => request<PlanningRun[]>('GET', `/${id}/runs`)
export const cancelRun = (id: string, runId: string) => request<PlanningRun>('POST', `/${id}/runs/${runId}/cancel`)

export const startCreateRun = (original: TripFormData, requestId: string) => request<PlanningRun>('POST', '/plan-runs', { request: original, request_id: requestId }, 15000)
export const readCreateRun = (runId: string) => request<PlanningRun>('GET', `/plan-runs/${runId}`)
export const cancelCreateRun = (runId: string) => request<PlanningRun>('POST', `/plan-runs/${runId}/cancel`)

const TERMINAL = ['succeeded', 'failed', 'conflict', 'cancelled']

/**
 * Follow a run over SSE (fetch, so the owner header is sent) and fall back to
 * polling `poll` when the stream cannot be opened or drops. Resolves with the terminal run.
 */
export async function watchRun(runId: string, poll: () => Promise<PlanningRun>,
  onUpdate: (run: PlanningRun) => void, isDisposed: () => boolean = () => false): Promise<PlanningRun> {
  let latest: PlanningRun | null = null
  const controller = new AbortController()
  const stopWhenDisposed = setInterval(() => { if (isDisposed()) controller.abort() }, 500)
  try {
    const response = await fetch(apiUrl(`/api/trips/runs/${encodeURIComponent(runId)}/events`), {
      headers: { 'X-Trip-Owner': ownerToken(), Accept: 'text/event-stream' }, signal: controller.signal })
    if (response.ok && response.body) {
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done || isDisposed()) break
        buffer += decoder.decode(value, { stream: true })
        let boundary: number
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
          const data = block.split('\n').filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n')
          if (!data) continue
          latest = JSON.parse(data) as PlanningRun
          onUpdate(latest)
          if (TERMINAL.includes(latest.status)) return latest
        }
      }
    }
  } catch { /* stream unavailable or aborted: fall through to polling */ }
  finally { clearInterval(stopWhenDisposed); controller.abort() }
  while (!isDisposed()) {
    try { latest = await poll() }
    catch (error) {
      if (runWatchRejected(error)) throw error
      await new Promise(resolve => setTimeout(resolve, 1500))
      continue
    }
    if (isDisposed()) break
    onUpdate(latest)
    if (TERMINAL.includes(latest.status)) return latest
    await new Promise(resolve => setTimeout(resolve, 1500))
  }
  throw new Error('页面已关闭，任务继续执行')
}
