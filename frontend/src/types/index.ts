// 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface FactEvidence {
  source: 'amap' | 'model' | 'user' | 'official'
  recorded_at: string
  poi_id: string
  value_hash: string
  source_url?: string | null
  scope?: string | null
  applies_on?: string | null
  document_hash?: string | null
}

export interface Attraction {
  price_evidence?: FactEvidence | null
  hours_evidence?: FactEvidence | null
  item_id?: string
  place_id?: string
  locked?: boolean
  poi_id?: string
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number | null
  cost_status?: CostStatus
  cost_source?: string
  open_hours?: string | null
  fixed_start_time?: string | null
}

export interface Meal {
  category?: string | null
  price_evidence?: FactEvidence | null
  hours_evidence?: FactEvidence | null
  route_matched?: boolean
  item_id?: string
  place_id?: string
  locked?: boolean
  poi_id?: string
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  restaurant?: string
  address?: string
  location?: Location
  description?: string
  recommended_dish?: string
  image_url?: string
  estimated_cost?: number | null
  cost_status?: CostStatus
  cost_source?: string
  open_hours?: string | null
}

export interface HotelNightQuote {
  night_date: string
  hotel_place_id: string
  price_per_room: number
  room_type: string
  source_label: string
  taxes_included: boolean | null
  recorded_at: string
}
export interface HotelQuoteInput {
  night_date: string
  hotel_item_id: string
  price_per_room: number | null
  room_type: string
  source_label: string
  taxes_included: boolean | null
}
export interface Hotel {
  nightly_quote?: HotelNightQuote | null
  price_evidence?: FactEvidence | null
  hours_evidence?: FactEvidence | null
  item_id?: string
  place_id?: string
  locked?: boolean
  poi_id?: string
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number | null
  cost_status?: CostStatus
  cost_source?: string
  open_hours?: string | null
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  start_time?: string | null
  end_time?: string | null
  buffer_minutes?: number | null
  schedule?: DaySchedule | null
  visit_order?: string[]
  planned_start_times?: Record<string, string>
  route_notes?: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number | null
  night_temp: number | null
  wind_direction: string
  wind_power: string
  source?: 'amap' | 'model' | 'user' | null
  observed_at?: string | null
  report_time?: string | null
  adcode?: string | null
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  adjustment_notes?: string[]
  budget?: Budget
}

export type CostStatus = "unknown" | "estimated" | "confirmed"
export interface DayDiagnosis {
  opening_hours_checked?: number
  opening_hours_total?: number
  opening_hours_unknown?: number
  date: string
  day_index: number
  status: 'unchecked' | 'feasible' | 'conflict' | 'unverified'
  headline: string
  visit_minutes?: number
  meal_minutes?: number
  travel_minutes?: number
  buffer_minutes?: number
  waiting_minutes?: number
  minimum_minutes?: number
  available_minutes?: number
  overrun_minutes?: number
  remaining_minutes?: number | null
  verified_routes?: number
  total_routes?: number
  missing_routes?: number
  stale_routes?: number
  unknown_age_routes?: number
  oldest_checked_at?: string | null
  longest_leg?: { from_name: string; to_name: string; duration_minutes: number; mode: string } | null
}
export interface DaySchedule {
  status: "feasible" | "conflict" | "unverified"
  window_start: string
  window_end: string
  minimum_minutes: number
  available_minutes: number
  conflicts: string[]
  warnings: string[]
  solver_status?: string
  solver_note?: string
  route_queries?: number
  items: { id: string; name: string; kind: string; start_time: string | null; end_time: string | null; duration_minutes: number; fixed: boolean }[]
  segments: RouteSegment[]
}

export interface TripFormData {
  budget_mode?: 'active' | 'deferred'
  party_size?: number | null
  room_count?: number | null
  destination_budget?: number | null
  daily_start_time?: string
  daily_end_time?: string
  buffer_minutes?: number
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
  reference_type: "none" | "url" | "screenshot"
  reference_content: string
  reference_mode: "strict" | "hybrid" | "flexible"
  reference_sources?: ReferenceSource[]
  reference_candidates?: ReferenceCandidate[]
}

export interface TripCostContext {
  budget_mode?: 'active' | 'deferred'
  party_size: number | null
  room_count: number | null
  destination_budget: number | null
}
export interface CostReport {
  lodging_allowance?: { remaining_reference_budget: number; unpriced_room_nights: number; average_ceiling_per_room_night: number; already_over_reference: boolean; scope: string } | null
  unknown_hotel_nights?: number
  other_unknown_count?: number
  hotel_quote_taxes_pending?: number
  context: { party_size: number; room_count: number; lodging_nights: number | null }
  assumptions: string[]
  totals: Record<'attractions' | 'meals' | 'hotels' | 'transport', number>
  known_total: number
  unknown_count: number
  estimated_count: number
  lines: { date: string; kind: string; name: string; unit_price: number | null; quantity: number; unit: string; amount: number | null; status: string; source: string; evidence?: FactState; hotel_item_id?: string; quote?: HotelNightQuote }[]
  budget: { limit: number | null; status: 'deferred' | 'not_set' | 'needs_context' | 'needs_scope' | 'over_reference' | 'incomplete' | 'within_listed_reference' }
  exclusions: string[]
  notes: string[]
}

export interface FactState {
  source: 'amap' | 'model' | 'user' | 'official' | null
  recorded_at: string | null
  status: 'missing' | 'unknown_age' | 'recent_reference' | 'official_reference' | 'scope_mismatch' | 'stale_reference' | 'model_estimate' | 'user_provided'
  source_url?: string | null
  scope?: string | null
  applies_on?: string | null
}
export interface FactReport {
  items: { date: string; item_id: string; name: string; hours: FactState; price: FactState }[]
  stale: number
  unknown_age: number
  missing: number
  scope_mismatch?: number
}

export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface TripIntakeData extends Partial<TripCostContext> {
  budget_amount?: number | null
  budget_basis?: 'party' | 'person' | null
  budget_coverage?: 'destination' | 'whole_trip' | null
  budget_clarification?: boolean
  ready: boolean
  reply: string
  city: string | null
  start_date: string | null
  end_date: string | null
  travel_days: number | null
  transportation: string | null
  accommodation: string | null
  preferences: string[]
  free_text_input: string
}

export interface TripIntakeResponse {
  success: boolean
  message: string
  data?: TripIntakeData
}

export interface TripChatResponse {
  success: boolean
  message: string
  data?: {
    reply: string
    plan: TripPlan
    changed: boolean
    actions: string[]
  }
}

// DIY备选项
export interface Alternative {
  poi_id?: string
  name: string
  address: string
  location: Location
  category?: string
  rating?: number | null
  reason: string
  estimated_cost?: number | null
  cost_status?: CostStatus
  cost_source?: string
  open_hours?: string | null
  visit_duration?: number
  recommended_dish?: string
}

export interface AlternativesResponse {
  success: boolean
  message: string
  data: {
    alternatives: Alternative[]
  }
}


export interface ReferenceSource {
  id: string
  type: 'text' | 'url' | 'screenshot'
  title: string
  url: string
  text: string
}
export interface ReferencePlace {
  poi_id: string
  name: string
  address: string
  longitude: number
  latitude: number
}
export interface ReferenceCandidate {
  id: string
  name: string
  source_ids: string[]
  priority: 'required' | 'optional' | 'ignored'
  status: 'matched' | 'ambiguous' | 'unmatched'
  place: ReferencePlace | null
  options: ReferencePlace[]
}
export interface SavedTrip {
  id: string
  request: TripFormData
  plan: TripPlan
  chat: (ChatTurn & { actions?: string[]; changed?: boolean })[]
  version: number
  change_summary?: string[]
}
export interface TripVersion {
  version: number
  summary: string[]
  origin: string
  created_at: string
}
export interface SavedTripSummary {
  id: string
  city: string
  start_date: string
  end_date: string
  version: number
  updated_at: string
}


export interface PlanningRun {
  id: string
  trip_id: string
  kind: 'chat' | 'optimize' | 'create'
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'conflict' | 'cancelled'
  stage: string
  queries: number
  elapsed_seconds: number
  error: string
  created_at: string
  updated_at: string
  result: (SavedTrip & { reply?: string; changed?: boolean; actions?: string[]; optimization?: { status: string; note: string; queries: number } }) | null
}

export interface WeatherCandidate {
  poi_id: string
  name: string
  address: string
  location: Location
  category: string
  reason: string
  visit_duration: number
}

export interface WeatherAdvice {
  forecast_available: boolean
  note: string
  days: {
    date: string
    day_index: number
    wet: boolean
    weather: { day: string; night: string; high: string | number | null; low: string | number | null; source?:string | null; observed_at?:string | null; report_time?:string | null; adcode?:string | null } | null
    suggestions: { item_id: string; name: string; reason: string; candidates: WeatherCandidate[]; error?: string }[]
  }[]
}

export interface PlanIssue {
  item_id?: string
  code: string
  severity: 'error' | 'warning' | 'info'
  message: string
  day_index: number | null
}

export interface VisitReminder {
  id: string
  date: string
  day_index: number
  name: string
  kind: 'appointment' | 'likely_booking' | 'official_reference'
  confirm_by: null
  message: string
  booking_status: 'unverified'
  booking_opens_at: string | null
  policy_source_url: string | null
  policy_reviewed_on: string | null
  policy_status: 'unknown' | 'reviewed' | 'expired'
  fixed_start_time: string | null
}

export interface TripPrep {
  version: number
  reminders: VisitReminder[]
  stays: { id?: string; name: string | null; address: string; check_in: string; check_out: string; nights: number }[]
  lodging_note?: string
  packing: { item: string; reason: string }[]
  weather_note: string
  note: string
}

/** One actionable line in the result page status strip. */
export interface StatusItem { text: string; day?: number; action: 'day' | 'fix' | 'recheck' | 'none' }

/** A validated repair option for a conflicting day; `day` is the full replacement DayPlan. */
export interface FixProposal {
  kind: 'remove' | 'shorten'
  title: string
  detail: string
  feasible: boolean
  status: string
  overrun_minutes: number
  conflicts: string[]
  day: DayPlan
}

/** One step of a queried route (Amap plan at query time). */
export interface RouteLeg {
  kind: 'walk' | 'subway' | 'bus' | 'rail' | 'drive'
  line: string
  direction: string
  from_stop: string
  to_stop: string
  stops: number | null
  entrance: string
  exit: string
  distance_meters: number | null
  duration_minutes: number | null
  alternatives: string[]
}

export interface RouteEndpoint {
  name: string
  poi_id: string
  location: { longitude: number; latitude: number }
  basis: 'place_coordinate' | 'reviewed_gate'
  role: 'location' | 'entrance' | 'exit'
  venue_poi_id: string
  observed_at: string | null
  policy_source_url: string | null
  policy_reviewed_on: string | null
}

export interface RouteSegment {
  from_id?: string
  to_id?: string
  from_name: string
  to_name: string
  status: string
  duration_minutes: number | null
  distance_meters: number | null
  mode: string
  note: string
  checked_at: string | null
  fare?: number | null
  taxi_cost?: number | null
  legs?: RouteLeg[]
  polyline?: string
  from_endpoint?: RouteEndpoint | null
  to_endpoint?: RouteEndpoint | null
}
