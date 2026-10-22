import type { DayPlan } from '../types/index.ts'

export interface VisitChange { kind: 'removed' | 'added' | 'duration'; name: string; before?: number; after?: number }

export function unverifiedRouteReasons(day: DayPlan): string[] {
  return [...new Set((day.schedule?.segments || [])
    .filter(segment => segment.status !== 'verified' || segment.duration_minutes == null)
    .map(segment => segment.note?.trim() || '交通查询未返回可用耗时'))]
}

/** Only stable, unique visit IDs establish identity; names and positions do not. */
export function compareVisits(before: DayPlan, after: DayPlan) {
  const index = (day: DayPlan) => {
    const ids = day.attractions.map(item => item.item_id)
    return new Map(day.attractions.filter(item => item.item_id && ids.filter(id => id === item.item_id).length === 1).map(item => [item.item_id!, item]))
  }
  const old = index(before), next = index(after)
  const identityComplete = old.size === before.attractions.length && next.size === after.attractions.length
  if (!identityComplete) return { changes: [] as VisitChange[], reordered: false, identityComplete }
  const changes: VisitChange[] = []
  for (const [id, item] of old) {
    const replacement = next.get(id)
    if (!replacement) changes.push({ kind: 'removed', name: item.name, before: item.visit_duration })
    else if (item.visit_duration !== replacement.visit_duration) changes.push({ kind: 'duration', name: replacement.name, before: item.visit_duration, after: replacement.visit_duration })
  }
  for (const [id, item] of next) if (!old.has(id)) changes.push({ kind: 'added', name: item.name, after: item.visit_duration })
  const order = (day: DayPlan) => {
    const shared = day.attractions.map(item => item.item_id!).filter(id => old.has(id) && next.has(id))
    const explicit = (day.visit_order || []).filter(id => shared.includes(id))
    return explicit.length === shared.length && new Set(explicit).size === shared.length ? explicit : shared
  }
  return { changes, reordered: JSON.stringify(order(before)) !== JSON.stringify(order(after)), identityComplete }
}

export function dayTiming(day: DayPlan) {
  const schedule = day.schedule
  if (!schedule) return { travel: '尚未校验', finish: '尚未校验', overrun: '尚未校验', complete: false }
  const expected = Math.max(0, schedule.items.length - 1)
  const valid = schedule.segments.filter(segment => segment.status === 'verified' && segment.duration_minutes != null && Number.isFinite(segment.duration_minutes) && segment.duration_minutes >= 0)
  const complete = schedule.segments.length === expected && valid.length === expected && schedule.status !== 'unverified'
  const missing = Math.max(0, expected - valid.length)
  const total = valid.reduce((sum, segment) => sum + segment.duration_minutes!, 0)
  const last = schedule.items[schedule.items.length - 1]
  const overrun = Math.max(0, schedule.minimum_minutes - schedule.available_minutes)
  return {
    complete,
    travel: complete ? `${Math.round(total)} 分钟` : missing ? `还有 ${missing} 段未核实` : '交通数据不完整',
    finish: complete && last?.end_time ? last.end_time : '暂无法确认',
    overrun: complete ? overrun ? `超出 ${overrun} 分钟` : '未超出时间窗口' : overrun ? `至少超出 ${overrun} 分钟` : '暂无法确认',
  }
}
