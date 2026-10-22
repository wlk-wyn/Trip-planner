import type { DayPlan } from '../types/index'

const clock = (value?: string | null) => typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)
  ? Number(value.slice(0, 2)) * 60 + Number(value.slice(3)) : null

/** Gap beyond queried travel and the day's explicit buffer; unknown stays null. */
export function waitingBefore(day: DayPlan, index: number): number | null {
  const schedule = day.schedule
  if (!schedule || !Number.isInteger(index) || index < 0 || index >= schedule.items.length) return null
  const start = clock(schedule.items[index].start_time)
  if (start == null) return null
  let ready = clock(schedule.window_start)
  if (index > 0) {
    const edges = schedule.segments.slice(0, index)
    if (edges.length !== index || edges.some((edge, i) => edge.status !== 'verified' ||
      !Number.isFinite(edge.duration_minutes) || edge.duration_minutes! < 0 ||
      edge.from_id !== schedule.items[i].id || edge.to_id !== schedule.items[i + 1].id)) return null
    const previousEnd = clock(schedule.items[index - 1].end_time)
    const buffer = day.buffer_minutes
    if (previousEnd == null || buffer == null || !Number.isFinite(buffer) || buffer < 0) return null
    ready = previousEnd + Math.ceil(edges[index - 1].duration_minutes!) + buffer
  }
  return ready != null && start > ready ? start - ready : null
}
