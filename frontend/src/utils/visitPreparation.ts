import type { VisitReminder, TripPrep } from '@/types'

export function stayPreparationKey(stay: TripPrep['stays'][number]): string {
  // Never reuse the old date-only key when a hotel or stay length changes.
  return `s|${stay.id || JSON.stringify([stay.check_in, stay.check_out, stay.nights, stay.name, stay.address])}`
}

export function bookingWindowLabel(item: VisitReminder, now = Date.now()): string | null {
  if (item.policy_status !== 'reviewed' || !item.booking_opens_at) return null
  const opens = Date.parse(item.booking_opens_at)
  if (!Number.isFinite(opens)) return null
  const formatted = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(opens)
  return opens > now ? `按规则预计 ${formatted} 开始预约（北京时间）` : '按规则已到预约开放时间，余票未查询'
}

export function checkedPreparation(value: string | null): string[] {
  try {
    const parsed: unknown = JSON.parse(value || '[]')
    return Array.isArray(parsed) ? parsed.filter((key): key is string => typeof key === 'string') : []
  } catch { return [] }
}
