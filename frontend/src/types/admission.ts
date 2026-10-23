export interface OfficialAdmissionGuide {
  status: 'reviewed' | 'expired'
  reviewed_on: string
  valid_until: string
  visit_date: string
  booking_status: 'unverified'
  booking_opens_at: string | null
  source_name: string
  sources: { title: string; url: string }[]
  booking_url: string | null
  booking_label: string | null
  booking_channel: string
  notice: string
  summary: { label: string; text: string }[]
  details: { label: string; text: string }[]
}
export interface AdmissionGuideResult { version: number; item_id: string; visit_date: string; guide: OfficialAdmissionGuide | null }
