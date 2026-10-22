import type { TripPlan } from '@/types'
import { amapNavigationUrl, distanceText, legText, routeTitle, modeLabel } from './route.ts'
import { waitingBefore } from './scheduleWait.ts'
import { applicableHotelQuote } from './hotelQuote.ts'
import { navigationTarget, visitGateLabel, routeGateLabel } from './routingPoints.ts'

type Day = TripPlan['days'][number]
type Place = { name: string; poi_id?: string; address?: string | null; location?: { longitude: number; latitude: number } | null; restaurant?: string | null; open_hours?: string | null }

const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] || c)
const kindLabel = { attraction: '游览', meal: '用餐', hotel: '住宿' } as Record<string, string>

/** Places of a day keyed by schedule item id (hotel start/end share one place). */
function placesById(day: Day): Record<string, Place> {
  const map: Record<string, Place> = {}
  for (const place of [...day.attractions, ...day.meals, ...(day.hotel ? [day.hotel] : [])]) {
    const id = place.item_id || ''
    map[id] = map[`${id}-start`] = map[`${id}-end`] = place as Place
  }
  return map
}

function stopsOf(day: Day) {
  const places = placesById(day)
  if (day.schedule?.items.length) return day.schedule.items.map(item => ({ item, place: places[item.id] }))
  return day.attractions.map(place => ({ item: { id: place.item_id || '', kind: 'attraction', name: place.name, start_time: null, end_time: null, duration_minutes: place.visit_duration, fixed: false }, place: place as Place }))
}

/** Self-contained HTML: opens offline on a phone, with an Amap navigation link per stop. */
export function buildHandbookHtml(plan: TripPlan): string {
  const days = plan.days.map(day => {
    const segments = day.schedule?.segments || []
    const stops = stopsOf(day).map(({ item, place }, i) => {
      const name = place?.restaurant || place?.name || item.name
      const target = navigationTarget(day, item.id, place ? { ...place, name } : undefined)
      const nav = target ? `<a class="nav" href="${escape(amapNavigationUrl(target.longitude, target.latitude, target.name, day.transportation === '步行' ? 'walking' : day.transportation === '自驾' ? 'driving' : 'transit'))}">${target.gate ? '导航到入口' : '定位点导航'}</a>` : ''
      const gates = visitGateLabel(day, item.id)
      const time = item.start_time ? `${item.start_time}${item.end_time && item.duration_minutes > 0 ? `–${item.end_time}` : ''}` : '—'
      const segment = segments[i]
      const verified = segment?.status === 'verified' && segment.duration_minutes != null && Number.isFinite(segment.duration_minutes) && segment.duration_minutes >= 0
      const transfer = segment ? `<div class="transfer"><strong>${escape(verified ? routeTitle(segment) : modeLabel(segment.mode))}</strong> · ${verified ? `${segment.duration_minutes} 分钟` : '耗时未获取'}${verified && segment.fare != null && Number.isFinite(segment.fare) && segment.fare >= 0 ? ` · ¥${segment.fare}` : ''}${verified && segment.distance_meters != null && Number.isFinite(segment.distance_meters) && segment.distance_meters >= 0 ? ` · ${distanceText(segment.distance_meters)}` : ''}${verified && segment.legs?.length && segment.legs.length > 1 ? `<ol>${segment.legs.map(leg => `<li>${escape(legText(leg))}</li>`).join('')}</ol>` : ''}</div>` : ''
      const direction = segment ? routeGateLabel(segment) : ''
      const routeNote = [direction, segment?.note].filter(Boolean).map(escape).join('；')
      const gap = waitingBefore(day, i)
      const wait = gap != null && gap >= 15 ? `<p class="wait muted">下一项安排前预留 ${gap} 分钟</p>` : ''
      return `<li>${wait}<div class="time">${escape(time)}</div><div class="stop"><span class="kind">${escape(kindLabel[item.kind] || '')}${item.fixed ? ' · 固定预约' : ''}</span><h3>${escape(name)} ${nav}</h3>${gates ? `<p class="muted">${escape(gates)} · 门址参考，临时开放请查官方公告</p>` : ''}${place?.poi_id === 'B000A8UIN8' && !target ? '<p class="muted">入口交通尚未确认，请重新校验后再导航。</p>' : ''}${place?.address ? `<p>${escape(place.address)}</p>` : ''}${place?.open_hours ? `<p class="muted">开放时间参考（未官方核实）：${escape(place.open_hours)}</p>` : ''}</div>${transfer}${routeNote ? `<p class="transfer">${routeNote}</p>` : ''}</li>`
    }).join('')
    const conflicts = day.schedule?.conflicts.length ? `<p class="warn">${day.schedule.conflicts.map(escape).join('；')}</p>` : ''
    const notes=(plan.adjustment_notes || []).filter(note=>note.startsWith(`第${day.day_index+1}天`))
    const adjustments=notes.length?`<aside><strong>路线调整说明</strong>${notes.map(note=>`<p class="muted">${escape(note)}</p>`).join('')}</aside>`:''
    const quote=day.hotel && day.date<plan.end_date?applicableHotelQuote(day.hotel,day.date):null
    const lodging=quote?`<p class="muted">用户提供的当晚房价参考：¥${escape(quote.price_per_room)} / 间夜 · ${escape(quote.room_type)} · ${quote.taxes_included===true?'用户注明含税':'税费待确认'}${quote.source_label?` · 用户来源备注：${escape(quote.source_label)}`:''}；未独立核实，不代表已预订或可订。</p>`:''
    return `<section><h2>第 ${day.day_index + 1} 天 · ${escape(day.date)}</h2><p class="muted">${escape(day.transportation)}${day.hotel ? ` · 住 ${escape(day.hotel.name)}` : ''}</p>${lodging}${conflicts}${adjustments}<ol class="timeline">${stops}</ol></section>`
  }).join('')
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(plan.city)}出行手册</title><style>
:root{--bg:#fff;--text:#1f1f1f;--muted:#6b7280;--line:#e5e7eb;--warn:#b45309}
@media(prefers-color-scheme:dark){:root{--bg:#141414;--text:#f3f4f6;--muted:#9ca3af;--line:#2f2f2f;--warn:#f59e0b}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px}h1{font-size:24px;margin:0}h2{font-size:18px;margin:28px 0 2px;padding-top:16px;border-top:1px solid var(--line)}
.muted,.kind{color:var(--muted);font-size:12px}.warn{color:var(--warn);font-size:13px}ol.timeline{list-style:none;padding:0;margin:12px 0}
ol.timeline>li{display:grid;grid-template-columns:96px 1fr;gap:4px 12px;padding:10px 0;border-bottom:1px dashed var(--line)}.time{font-variant-numeric:tabular-nums;font-weight:600}
.stop h3{font-size:16px;margin:2px 0}.stop p{margin:0;font-size:13px}.transfer{grid-column:2;font-size:13px;color:var(--muted);margin-top:4px}.transfer ol{margin:4px 0 0;padding-left:18px}
.wait{grid-column:2;margin:0 0 8px;padding-left:8px;border-left:2px solid var(--line)}
a.nav{font-size:12px;font-weight:400;margin-left:6px;color:#2563eb;text-decoration:none;border:1px solid currentColor;border-radius:999px;padding:0 8px}
footer{margin-top:32px;color:var(--muted);font-size:12px}@media print{a.nav{display:none}}
</style></head><body><main><h1>${escape(plan.city)}出行手册</h1><p class="muted">${escape(plan.start_date)} — ${escape(plan.end_date)} · ${plan.days.length} 天</p>${days}
<footer>时间为建议排程，交通来自已有查询记录，不预测当天路况与班次；开放时间、预约与票价请以官方为准。“导航”链接会打开高德地图。</footer></main></body></html>`
}

const icsText = (value: string) => value.replace(/\\/g, '\\\\').replace(/[,;]/g, m => `\\${m}`).replace(/\r?\n/g, '\\n')

/** Fold content lines at 75 octets as RFC 5545 requires (CJK characters are 3 bytes). */
function fold(line: string) {
  const encoder = new TextEncoder()
  const parts: string[] = []
  let current = ''
  for (const char of line) {
    const limit = parts.length ? 74 : 75
    if (encoder.encode(current + char).length > limit) { parts.push(current); current = char } else current += char
  }
  parts.push(current)
  return parts.join('\r\n ')
}

/** One event per timed stop, in Asia/Shanghai local time. */
export function buildIcs(plan: TripPlan): { ics: string; events: number } {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Trip Planner//CN', 'CALSCALE:GREGORIAN',
    'BEGIN:VTIMEZONE', 'TZID:Asia/Shanghai', 'BEGIN:STANDARD', 'DTSTART:19700101T000000', 'TZOFFSETFROM:+0800', 'TZOFFSETTO:+0800', 'TZNAME:CST', 'END:STANDARD', 'END:VTIMEZONE']
  let events = 0
  for (const day of plan.days) {
    const date = day.date.replace(/-/g, '')
    for (const { item, place } of stopsOf(day)) {
      if (!item.start_time || !item.end_time || item.duration_minutes <= 0) continue
      const name = place?.restaurant || place?.name || item.name
      const target = navigationTarget(day, item.id, place ? { ...place, name } : undefined)
      const gates = visitGateLabel(day, item.id)
      const description = [`${kindLabel[item.kind] || ''} · 第 ${day.day_index + 1} 天`, place?.open_hours ? `开放时间：${place.open_hours}` : '',
        gates ? `${gates} · 门址参考，临时开放请查官方公告` : '',
        target ? `${target.gate ? '导航到入口' : '定位点导航'}：${amapNavigationUrl(target.longitude, target.latitude, target.name, day.transportation === '步行' ? 'walking' : day.transportation === '自驾' ? 'driving' : 'transit')}` : place?.poi_id === 'B000A8UIN8' ? '入口交通尚未确认，请重新校验后再导航。' : ''].filter(Boolean).join('\n')
      lines.push('BEGIN:VEVENT', `UID:${item.id}-${date}@trip-planner`, `DTSTAMP:${stamp}`,
        `DTSTART;TZID=Asia/Shanghai:${date}T${item.start_time.replace(':', '')}00`, `DTEND;TZID=Asia/Shanghai:${date}T${item.end_time.replace(':', '')}00`,
        fold(`SUMMARY:${icsText(name)}`), ...(place?.address ? [fold(`LOCATION:${icsText(place.address)}`)] : []),
        // Provider coordinates are GCJ-02. Keep the provider-specific navigation URL;
        // do not let calendar clients interpret an unqualified GEO as a GPS point.
        fold(`DESCRIPTION:${icsText(description)}`), 'END:VEVENT')
      events++
    }
  }
  lines.push('END:VCALENDAR')
  return { ics: lines.join('\r\n') + '\r\n', events }
}

export function downloadFile(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
