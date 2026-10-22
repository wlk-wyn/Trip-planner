import type { RouteLeg, RouteSegment } from '@/types'

const RIDES = ['subway', 'bus', 'rail']

export const modeLabel = (mode: string) => ({ walking: '步行', driving: '自驾', transit: '公共交通' }[mode] || mode)
export const hasRide = (segment: RouteSegment) => !!segment.legs?.some(leg => RIDES.includes(leg.kind))
export const distanceText = (meters: number) => meters < 1000 ? `${meters} m` : `${(meters / 1000).toFixed(1)} km`

/** "地铁8号线 → 2号线内环" for transit plans; otherwise the travel mode. */
export function routeTitle(segment: RouteSegment) {
  const rides = (segment.legs || []).filter(leg => RIDES.includes(leg.kind)).map(leg => leg.line)
  if (!rides.length) return modeLabel(segment.mode)
  return rides.map((line, i) => (i > 0 ? line.replace(/^地铁/, '') : line)).join(' → ')
}

/** One readable instruction per leg, e.g. "天桥站 C东南口进站 乘 地铁8号线（往朱辛庄），2 站到 前门站". */
export function legText(leg: RouteLeg) {
  const minutes = leg.duration_minutes != null ? `，约 ${leg.duration_minutes} 分钟` : ''
  if (leg.kind === 'walk') return `步行 ${distanceText(leg.distance_meters || 0)}${minutes}`
  if (leg.kind === 'drive') return `驾车 ${distanceText(leg.distance_meters || 0)}${minutes}`
  const station = leg.kind === 'subway' ? '站' : ''
  const board = `${leg.from_stop}${station}${leg.entrance ? ` ${leg.entrance}进站` : ''}`
  const ride = `乘 ${leg.line}${leg.direction ? `（往${leg.direction}）` : ''}`
  const alight = `${leg.stops ? `${leg.stops} 站` : ''}到 ${leg.to_stop}${station}${leg.exit ? `，${leg.exit}出站` : ''}`
  const alternatives = leg.alternatives?.length ? `；也可乘 ${leg.alternatives.join('、')}` : ''
  return `${board} ${ride}，${alight}${minutes}${alternatives}`
}

/** Amap URI that opens navigation (app when installed, web otherwise) to a place. */
export function amapNavigationUrl(longitude: number, latitude: number, name: string, mode = 'transit') {
  const amapMode = { walking: 'walk', driving: 'car', transit: 'bus' }[mode] || 'bus'
  return `https://uri.amap.com/navigation?to=${longitude},${latitude},${encodeURIComponent(name)}&mode=${amapMode}&callnative=1&src=trip-planner`
}
