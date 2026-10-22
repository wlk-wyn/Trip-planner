import type { DayPlan, RouteEndpoint, RouteSegment } from '@/types'

const POLICY = 'https://www.dpm.org.cn/Visit.html'

export function isGatePoint(point: RouteEndpoint | null | undefined): point is RouteEndpoint {
  return !!point && point.basis === 'reviewed_gate' && ['entrance', 'exit'].includes(point.role)
    && point.name === (point.role === 'entrance' ? '故宫博物院-午门' : '故宫博物院-神武门')
    && point.venue_poi_id === 'B000A8UIN8' && point.policy_source_url === POLICY
    && (point.role === 'entrance' ? point.poi_id === 'B000A84GDN' : point.poi_id === 'B000A9PISW')
    && Number.isFinite(point.location?.longitude) && Number.isFinite(point.location?.latitude)
    && (point.role === 'entrance'
      ? point.location.longitude >= 116.3965 && point.location.longitude <= 116.3980 && point.location.latitude >= 39.9130 && point.location.latitude <= 39.9142
      : point.location.longitude >= 116.3960 && point.location.longitude <= 116.3976 && point.location.latitude >= 39.9218 && point.location.latitude <= 39.9229)
}

const shortName = (point: RouteEndpoint) => point.name.replace(/^故宫博物院[-－—]/, '')

export function routeGateLabel(segment: RouteSegment): string {
  return [isGatePoint(segment.from_endpoint) && segment.from_endpoint.role === 'exit' ? `从${shortName(segment.from_endpoint)}（出口参考）` : '',
    isGatePoint(segment.to_endpoint) && segment.to_endpoint.role === 'entrance' ? `到${shortName(segment.to_endpoint)}（入口参考）` : ''].filter(Boolean).join(' → ')
}

function matchesStop(day: DayPlan, id: string | undefined, point: RouteEndpoint | null | undefined, role: 'entrance' | 'exit'): point is RouteEndpoint {
  return !!id && isGatePoint(point) && point.role === role && day.attractions.some(a => a.item_id === id && a.poi_id === point.venue_poi_id)
}

export function visitGateLabel(day: DayPlan, id: string): string {
  const entry = day.schedule?.segments.find(s => s.to_id === id && matchesStop(day, id, s.to_endpoint, 'entrance'))?.to_endpoint
  const exit = day.schedule?.segments.find(s => s.from_id === id && matchesStop(day, id, s.from_endpoint, 'exit'))?.from_endpoint
  return [entry ? `${shortName(entry)}进` : '', exit ? `${shortName(exit)}出` : ''].filter(Boolean).join(' · ')
}

export function officialGateUrl(segment: RouteSegment): string | null {
  return routeGateLabel(segment) ? POLICY : null
}

export function dayGatePoints(day: DayPlan): RouteEndpoint[] {
  const points = (day.schedule?.segments || []).flatMap(s => [
    matchesStop(day, s.from_id, s.from_endpoint, 'exit') ? s.from_endpoint : null,
    matchesStop(day, s.to_id, s.to_endpoint, 'entrance') ? s.to_endpoint : null,
  ]).filter(isGatePoint)
  return [...new Map(points.map(point => [point.poi_id, point])).values()]
}

export function endpointCoordinates(point: RouteEndpoint | null | undefined): [number, number] | null {
  if (!point || (point.basis !== 'place_coordinate' && !isGatePoint(point))) return null
  const { longitude, latitude } = point.location || {}
  return Number.isFinite(longitude) && Number.isFinite(latitude) && Math.abs(longitude) <= 180 && Math.abs(latitude) <= 90
    ? [longitude, latitude] : null
}

type NavPlace = { poi_id?: string; name?: string; location?: { longitude: number; latitude: number } | null }
export function navigationTarget(day: DayPlan, id: string, place?: NavPlace) {
  const incoming = day.schedule?.segments.find(s => s.to_id === id && matchesStop(day, id, s.to_endpoint, 'entrance'))
  if (incoming?.status === 'verified' && incoming.to_endpoint) return { ...incoming.to_endpoint.location, name: incoming.to_endpoint.name, gate: true }
  // A missing gate cannot become a navigation link to the centre, including old exports.
  if (place?.poi_id === 'B000A8UIN8') return null
  const loc = place?.location
  return loc && Number.isFinite(loc.longitude) && Number.isFinite(loc.latitude)
    && Math.abs(loc.longitude) <= 180 && Math.abs(loc.latitude) <= 90 && (loc.longitude !== 0 || loc.latitude !== 0)
    ? { ...loc, name: place?.name || '', gate: false } : null
}
